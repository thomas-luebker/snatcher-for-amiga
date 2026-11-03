#!/usr/bin/env python3
"""Pre-render Snatcher's FM music and FM effects (main-CPU YM2612 + PSG driver) to WAV files.

  fmrender.py [CODE ...] [--out work/music] [--max-seconds 300] [--base-frame 44000]

CODE is a hex driver code; default: all of $01-$7A. Writes OUT/NN.wav (16-bit stereo at the core's
44100 Hz) and OUT/loops.json.

The driver's table at $FF6142 (8 bytes per code: [flags][kind][top channel][priority][index.l]) has
two banks: $01-$3E are FM sound effects (resident, data from FMWR_1+$1E00 at $FF9000; flags 0 =
they play over the music; the script's command $1C uses them, e.g. $1D 886 times in a trace) and
$3F-$7A are the 60 songs (flags 1 = stop the music first; data copied on demand from FMWR_1+$2846
in word RAM to $FF8000; the script's command $07 uses them, e.g. $4F).

How it works (docs/ENGINE.md "Sound"; addresses are main-CPU work RAM):
- The game is played to --base-frame with the boot schedule + random walk of tools/snapshot.py
  (cached in OUT/base*.state), in the instrumented core with CD-DA and PCM volume set to 0 through
  the libretro core options, so only the YM2612 and PSG are heard.
- The receiver of the sub CPU's sound codes ($FF4F96, called when gate $A12025 changes) is
  replaced by an RTS, so the running scene can no longer start or stop music. The master volume
  the receiver normally copies from gate $A12022 ($FF7F5E) is set to $FFFF (no attenuation).
- A code is played the way the receiver queues it: byte at $FF7F00 + [$FF7F20], index + 1 (mod
  $20); the driver ($FF5106) takes it on its next tick. $F2 is the driver's stop ($F1 is a full
  reset that also clears the volume). After a stop and 2 s of silence the state is saved, and
  every code starts from that same state.
- Loop points come from the driver, not the waveform: after each frame the driver's state (ten
  $80-byte channel records at $FF7A00 plus its variables up to $FF7F7F) is kept, and each channel's
  period of its sequence position (active flag + pointers into the song data) is found on its own
  (channel_period, find_loop). The whole state rarely repeats exactly: every channel's tempo
  accumulator (+$05) runs on its own and a loop is usually a fractional number of frames, so the
  seam can be off by a frame or two (17-33 ms). Effects use the first exact repeat of the whole state.
  loop_start/loop_end are the sample counts at the ends of those two frames. Checked against
  the waveform: the 5 s after each point, as normalized sample correlation ("corr"; low when the
  YM2612's free-running phases differ) and as correlation of their log band energies ("spec_corr",
  also used to reject a candidate period whose audio does not repeat).
  A song that ends (no channel active) is a one-shot: the file is cut after the release tail.
  Files of looping songs end at loop_end (intro + exactly one loop).
"""
import argparse
import ctypes as C
import hashlib
import json
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retro  # noqa: E402
from survey import play  # noqa: E402

ROOT = os.path.dirname(HERE)
OPTS = {b"genesis_plus_gx_cdda_volume": b"0", b"genesis_plus_gx_pcm_volume": b"0"}
ENV_GET_VARIABLE = 15
RECEIVER, QUEUE, QWRITE, MVOL = 0xFF4F96, 0xFF7F00, 0xFF7F20, 0xFF7F5E
DRIVER_LO, DRIVER_HI = 0xFF7A00, 0xFF7F80          # channel records + driver variables
STOP = 0xF2


class Var(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class AV(C.Structure):
    _fields_ = [("bw", C.c_uint), ("bh", C.c_uint), ("mw", C.c_uint), ("mh", C.c_uint), ("ar", C.c_float),
                ("fps", C.c_double), ("rate", C.c_double)]


class Emu(retro.Emu):
    """retro.Emu with core options (CD-DA and PCM muted) and main work RAM access."""
    def _env(self, cmd, data):
        if (cmd & 0xFFFF) == ENV_GET_VARIABLE:
            v = C.cast(data, C.POINTER(Var))[0]
            if v.key in OPTS:
                v.value = OPTS[v.key]
                return True
            return False
        return super()._env(cmd, data)

    def setup(self):
        # 64 KB work RAM, 16-bit words byte-swapped: the stock core's RETRO_MEMORY_SYSTEM_RAM (the
        # trace core's scd_mem(5) is the same pointer), so any Genesis Plus GX build will do
        self.lib.retro_get_memory_data.restype = C.c_void_p
        self.wr = self.lib.retro_get_memory_data(2)
        av = AV()
        self.lib.retro_get_system_av_info(C.byref(av))
        self.rate = int(round(av.rate))
        self.fps = av.fps

    def wb(self, a, v):
        C.c_ubyte.from_address(self.wr + ((a & 0xFFFF) ^ 1)).value = v

    def rb(self, a):
        return C.c_ubyte.from_address(self.wr + ((a & 0xFFFF) ^ 1)).value

    def block(self, lo, hi):
        b = bytearray(C.string_at(self.wr + (lo & 0xFFFF), hi - lo))
        b[0::2], b[1::2] = b[1::2], b[0::2]
        return bytes(b)

    def push(self, code):
        i = self.rb(QWRITE) & 0x1F
        self.wb(QUEUE + i, code)
        self.wb(QWRITE, (i + 1) & 0x1F)


def active(st):
    return any(st[0x80 * c] for c in range(10))


def channel_ids(S):
    """Per active channel, an id per frame of its sequence position: its active byte and the longs
    in its record ($40-$7C) that hold pointers into the song data ($FF8000-$FFFFFF) -- the read
    pointer and its loop/call stack. Durations, tempo accumulators and modulators are left out."""
    out = []
    for c in range(10):
        r = S[:, 0x80 * c:0x80 * c + 0x80].astype(np.uint32)
        if not r[:, 0].any():
            continue
        cols = [r[:, 0]]
        for o in range(0x40, 0x80, 4):
            v = (r[:, o] << 24) | (r[:, o + 1] << 16) | (r[:, o + 2] << 8) | r[:, o + 3]
            if len(np.unique(v)) > 1 and np.all((v == 0) | ((v >= 0xFF8000) & (v <= 0xFFFFFF))):
                cols.append(v)
        _, ids = np.unique(np.stack(cols, axis=1), axis=0, return_inverse=True)
        out.append(ids.ravel())
    return out


def pointer_ids(S):
    """one id per frame for the whole driver's sequence position"""
    ch = channel_ids(S)
    if not ch:
        return np.zeros(len(S), dtype=np.int64)
    _, ids = np.unique(np.stack(ch, axis=1), axis=0, return_inverse=True)
    return ids.ravel()


def channel_period(ids, T, min_verify):
    """(start frame, period in frames, fractional) of one channel's position, ('done', frame) when it stopped
    moving for good, or None. Works on events, not frames: the run of frames in which the position
    stays the same is one event, so it does not matter that the tempo accumulator puts the ticks on
    the frame grid (a fractional-frame loop shifts events by a frame here and there). P is the
    smallest period of the event sequence, e0 the first event from which it holds; a whole period
    and at least min_verify frames must be verified after the first one (repeat counters are not
    pointers: a phrase played four times looks periodic until the song moves on)."""
    first = np.r_[0, np.nonzero(np.diff(ids))[0] + 1]
    ev = ids[first]
    E = len(ev)
    if T - first[-1] >= min_verify:
        return "done", int(first[-1])
    for P in range(1, E // 2):
        bad = np.nonzero(ev[:E - P] != ev[P:])[0]
        e0 = int(bad[-1]) + 1 if len(bad) else 0
        if E - P - e0 < P + 1:
            continue
        g, h = int(first[e0]), int(first[e0 + P])
        if T - h >= max(h - g, min_verify):
            n = (E - 1 - e0) // P                 # average over every whole period seen
            return g, (first[e0 + n * P] - g) / n
    return None


def find_loop(S, min_verify, accept=None, max_mult=16, partial=False):
    """The song's loop as (start frame, end frame, how), or None. Each channel's period is found on
    its own (channel_period: the channels have their own tempo accumulators, so events of different
    channels do not interleave the same way on every pass); the song loops after a period that the
    channels' periods divide (to 1%), from the frame where the last channel became periodic.
    Candidates are tried by how many channels agree (a silent channel can run a different length);
    accept(g, L) checks that the audio repeats -- pitch registers are not pointers, a phrase can
    climb on every pass -- and is required when not every channel agrees. Then multiples are tried.
    partial: ignore channels that never showed a period, and candidates the recording is too short
    to verify (used once the recording is over)."""
    T = len(S)
    act = S[:, [0x80 * c for c in range(10)]].any(axis=1)
    if not act.any():
        return None
    per = [channel_period(ids, T, min_verify) for ids in channel_ids(S)]
    if any(p is None for p in per) and not partial:
        return None                               # a channel has not shown its period yet
    unresolved = sum(p is None for p in per)
    per = [p for p in per if p is not None]
    loops = [p for p in per if p[0] != "done"]
    if not loops:
        return None                               # nothing moves any more: held notes
    g_done = max([p[1] for p in per if p[0] == "done"], default=0)

    def divides(l, L):
        k = max(1, round(L / l))
        return abs(L - k * l) <= 0.01 * L + 2
    cands = []
    for _, Lc in loops:
        agree = [p for p in loops if divides(p[1], Lc)]
        cands.append((-len(agree), Lc, max([p[0] for p in agree] + [g_done])))
    seen = set()
    for neg, Lf, g in sorted(cands):
        if round(Lf) in seen:
            continue
        seen.add(round(Lf))
        unanimous = -neg == len(loops) and not unresolved
        how = "sequence" if unanimous else f"sequence, {-neg} of {len(loops) + unresolved} channels"
        if T - g - round(Lf) < max(round(Lf), min_verify):
            if not partial:
                return None                       # wait for more: do not settle for a lesser loop
            continue
        if accept is None:
            if unanimous:
                return g, g + int(round(Lf)), how
            continue
        # multiples: a phrase transposed on every pass repeats only after several (0.4 s of
        # pointers, 3.2 s of music); take the first multiple that sounds as good as the best one
        scores = []
        for m in range(1, max_mult + 1):
            L = int(round(m * Lf))
            if (m > 1 and L > 60 * 60) or T - g - L < max(L, min_verify):
                break
            scores.append((m, L, accept(g, L)))
        best = max(sc for _, _, sc in scores)
        m, L, sc = next(x for x in scores if x[2] >= best - 0.01)
        # every channel agreeing is strong evidence: only reject audio that clearly differs
        # (noise and LFO keep even a true loop from matching closely)
        if sc >= (0.75 if unanimous else 0.93):
            return g, g + L, how if m == 1 else f"{how}, x{m}"
    return None


def sounds_alike(emu, bounds, T):
    """accept(g, L) for find_loop: how well the log band energies of everything recorded from
    frame g on match those one period later (spec_corr)."""
    pcm = np.frombuffer(bytes(emu.audio), dtype=np.int16).reshape(-1, 2)

    def accept(g, L):
        a, b, d = bounds[g], bounds[T - L], bounds[g + L] - bounds[g]
        return spec_corr(pcm[a:b], pcm[a + d:b + d])
    return accept


def record(emu, code, song, max_frames, verify_frames, tail_frames, check_every=1800):
    """Play code from the current state. Returns (stereo int16 array, frame bounds, loop, exact, held):
    loop = the sequence-position period (songs only), exact = the first exact repeat of the whole
    driver state; each (start frame, end frame) or None; held = the frame from which a song's
    sequence stood still for 30 s with notes still sounding (or None)."""
    emu.audio = bytearray()
    emu.push(code)
    bounds, states, idle, loop, seen, exact, held = [0], [], 0, None, {}, None, None
    held_frames = min_verify = int(30 * emu.fps)
    f = 0
    while f < max_frames:
        emu.run()
        f += 1
        bounds.append(len(emu.audio) // 4)
        st = emu.block(DRIVER_LO, DRIVER_HI)
        states.append(np.frombuffer(st, dtype=np.uint8))
        on = active(st)
        idle = 0 if on else idle + 1
        if f > 5 and idle >= tail_frames:
            break
        if on and exact is None:
            h = hashlib.blake2b(st, digest_size=16).digest()
            if h in seen:
                exact = (seen[h], f)
            seen.setdefault(h, f)
        if exact and f >= exact[1] + verify_frames and not song:
            break
        if song and f % check_every == 0:
            S = np.array(states)
            ids = pointer_ids(S)
            if on and f > held_frames and (ids[-held_frames:] == ids[-1]).all():
                held = int(np.nonzero(ids != ids[-1])[0][-1]) + 1 if (ids != ids[-1]).any() else 1
                break                             # the sequence stopped on held notes
            loop = find_loop(S, min_verify, sounds_alike(emu, bounds, f))
            # stop once the loop is verified and multiples up to 60 s could be compared
            if loop and f >= loop[1] + max(loop[1] - loop[0], min_verify) + verify_frames and \
                    f >= loop[0] + 2 * min(16 * (loop[1] - loop[0]), int(60 * emu.fps)) + verify_frames:
                break
    S = np.array(states)
    if idle >= tail_frames:
        loop = exact = None                       # it ended: a one-shot
    elif held:
        loop = None
    elif song and (loop is None or f % check_every):
        loop = find_loop(S, min_verify, sounds_alike(emu, bounds, f))
        if loop is None:                          # some channel never repeated: let the audio decide
            loop = find_loop(S, min_verify, sounds_alike(emu, bounds, f), partial=True)
    if exact and f < exact[1] + verify_frames:
        exact = None                              # found too late to check
    pcm = np.frombuffer(bytes(emu.audio), dtype=np.int16).reshape(-1, 2).copy()
    return pcm, bounds, loop, exact, held


def corr(a, b):
    a, b = a.astype(np.float64).ravel(), b.astype(np.float64).ravel()
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d else 0.0


def spec_corr(a, b, n=2048):
    """Correlation of the log energies in 48 log-spaced bands, per 2048-sample frame: what the ear
    compares; insensitive to the YM2612's free-running oscillator and LFO phases."""
    edges = np.unique(np.geomspace(4, n // 2, 49).astype(int))

    def bands(x):
        x = x.astype(np.float64).mean(axis=1)
        k = len(x) // n
        p = np.abs(np.fft.rfft(x[:k * n].reshape(k, n) * np.hanning(n), axis=1)) ** 2
        return np.log10(np.add.reduceat(p, edges, axis=1)[:, :-1] + 1e3).ravel()
    m = min(len(a), len(b))
    if m < n:
        return 0.0
    x, y = bands(a[:m]), bands(b[:m])
    return float(np.corrcoef(x, y)[0, 1]) if x.std() and y.std() else 0.0


def trim_tail(pcm, rate, thresh=8):
    """cut trailing near-silence, keeping 50 ms after the last audible sample"""
    loud = np.nonzero(np.abs(pcm).max(axis=1) > thresh)[0]
    return pcm[:0] if not len(loud) else pcm[:min(len(pcm), loud[-1] + 1 + rate // 20)]


def write_wav(path, pcm, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype("<i2").tobytes())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="*", help="hex codes (default $01-$7A)")
    ap.add_argument("--out", default=os.path.join(ROOT, "work/music"))
    ap.add_argument("--cue", default=os.path.join(ROOT, "disc/scd-usa/Snatcher (USA).cue"))
    ap.add_argument("--max-seconds", type=float, default=300, help="songs: give up looking for a loop")
    ap.add_argument("--effect-seconds", type=float, default=60)
    ap.add_argument("--base-frame", type=int, default=44000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--core", default=os.path.join(ROOT, "emu", "genesis_plus_gx_trace.dylib"))
    ap.add_argument("--sysdir", default=os.path.join(ROOT, "emu", "sysgpgx"), help="holds bios_CD_U.bin")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.codes:
        codes = [int(c, 16) for c in a.codes]
    else:
        codes = list(range(0x01, 0x7B))

    emu = Emu(os.path.abspath(a.core), os.path.abspath(a.sysdir))
    emu.load(a.cue)
    emu.setup()
    base = os.path.join(a.out, f"base{a.base_frame}-{a.seed}.state")
    if not os.path.exists(base):
        play(emu, a.base_frame, a.seed)
        emu.wb(RECEIVER, 0x4E)                 # rts: the scene can no longer queue sound codes
        emu.wb(RECEIVER + 1, 0x75)
        emu.wb(MVOL, 0xFF)
        emu.wb(MVOL + 1, 0xFF)
        emu.push(STOP)
        for _ in range(120):
            emu.run()
        emu.save_state(base)
    emu.run()
    if not emu.load_state(base):
        raise SystemExit("could not load " + base)

    jpath = os.path.join(a.out, "loops.json")
    meta = json.load(open(jpath)) if os.path.exists(jpath) else {}
    fps = emu.fps
    for code in codes:
        emu.load_state(base)
        song = code >= 0x3F
        secs = a.max_seconds if song else a.effect_seconds
        maxf = int(secs * fps)
        pcm, bounds, loop, exact, held = record(emu, code, song, maxf, int(5 * fps), int(1.0 * fps))
        rate = emu.rate
        e = {"kind": "song" if song else "effect", "rate": rate, "channels": 2}
        def check(lp):
            ls, le = bounds[lp[0]], bounds[lp[1]]
            n = min(5 * rate, len(pcm) - le)
            return ls, le, corr(pcm[ls:ls + n], pcm[le:le + n]), spec_corr(pcm[ls:ls + n], pcm[le:le + n])
        # songs: the musical (sequence-position) loop, unless it does not sound like a repeat and the
        # exact state loop does; effects: the exact state loop
        pick, how = None, None
        if loop:
            pick, how = loop[:2], loop[2]
        # the exact repeat of the whole state is ground truth: the musical loop must divide it
        if exact and (not loop or abs((exact[1] - exact[0]) - (loop[1] - loop[0]) * round(
                (exact[1] - exact[0]) / (loop[1] - loop[0]))) > 3 or check(loop)[3] < 0.93 <= check(exact)[3]):
            pick, how = exact, "exact state"
        if held and not pick:
            # the sequence stopped with notes held: keep 5 s after the last event and loop its
            # last second (a sustain, not part of the composition)
            end = min(len(bounds) - 1, held + int(5 * fps))
            pick, how = (end - int(fps), end), "held notes"
        if pick:
            ls, le, c1, c2 = check(pick)
            e.update(loop_start=ls, loop_end=le, loop_frames=list(pick), loop_by=how,
                     loop_seconds=round((le - ls) / rate, 3), corr=round(c1, 4), spec_corr=round(c2, 4))
            if exact:
                e["exact_frames"] = list(exact)
            if c2 < 0.93:
                e["note"] = "check the loop: the spectra after the two points differ"
            out = pcm[:le]
        else:
            e.update(loop_start=None, loop_end=None)
            out = trim_tail(pcm, rate)
            if len(bounds) - 1 >= maxf:
                e["note"] = f"no loop and no end within {secs:.0f} s"
        e["samples"] = len(out)
        e["seconds"] = round(len(out) / rate, 3)
        e["peak"] = int(np.abs(out).max()) if len(out) else 0
        e["rms"] = round(float(np.sqrt((out.astype(np.float64) ** 2).mean())), 1) if len(out) else 0.0
        name = f"{code:02X}.wav"
        write_wav(os.path.join(a.out, name), out, rate)
        e["file"] = name
        meta[f"{code:02X}"] = e
        json.dump(meta, open(jpath, "w"), indent=1, sort_keys=True)
        print(f"{code:02X} {e['kind']:6s} {e['seconds']:8.2f}s peak {e['peak']:5d} loop {e.get('loop_seconds')} "
              f"corr {e.get('corr')} spec {e.get('spec_corr')} {e.get('note', '')}", flush=True)


if __name__ == "__main__":
    main()
