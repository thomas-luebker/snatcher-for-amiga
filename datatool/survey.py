#!/usr/bin/env python3
"""Play the Sega CD game headlessly and measure what a stock A1200 has to hold and show.

  survey.py [--frames 60000] [--sample 120] [--out work/survey] [--state-in f]

After the boot menus, walks the command menus at random (mostly C, some up/down, a little B;
--seed picks the walk), and every --sample
frames records:
  - memory: every byte of main work RAM, PRG-RAM and word RAM that has ever been non-zero, and every
    byte that ever changed after it was first seen -- the high-water mark of what the game uses;
  - colours: distinct colours on screen. The VDP can show at most 61 at once (4 palettes x 15 + the
    backdrop); more than that in a frame means mid-frame palette writes or shadow/highlight.
Writes work/survey/report.json, a screenshot per --shot-every frames and the final state.

Genesis Plus GX states keep 68000 memory word-swapped; the offsets below are for core 1.7.6 and
were found by locating IP, SP and SUBCODE.BIN in a state (docs/FINDINGS.md).
"""
import argparse
import ctypes as C
import json
import os
import struct
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retro  # noqa: E402

REGIONS = {"work": (0x10, 0x10000), "prg": (0x380EE, 0x80000), "word": (0xB80EE, 0x40000)}
C_BUTTON = 8          # libretro A -> Mega Drive C in Genesis Plus GX
START = 3
B_BUTTON, UP, DOWN = 0, 4, 5      # libretro B -> Mega Drive B (cancel)
# backup RAM select, title, options, out of options: the schedule that reached the intro (work/boot)
BOOT = [(3100, 3110, C_BUTTON), (4500, 4510, START), (6000, 6010, START), (7500, 7510, START)]


def play(emu, frames, seed):
    """The boot schedule, then a random walk (same seed = same game)."""
    rng, act = random.Random(seed), C_BUTTON
    for f in range(1, frames + 1):
        press = []
        if f > 9500 and f % 35 < 4:
            if f % 35 == 0:
                act = rng.choices([C_BUTTON, DOWN, UP, B_BUTTON], [60, 25, 10, 5])[0]
            press = [act]
        for lo, hi, b in BOOT:
            if lo <= f <= hi:
                press = [b]
        emu.run(press)


def swap(b):
    a = bytearray(b)
    a[0::2], a[1::2] = b[1::2], b[0::2]
    return bytes(a)


def serialize(emu):
    n = emu.lib.retro_serialize_size()
    buf = C.create_string_buffer(n)
    emu.lib.retro_serialize(buf, n)
    return buf.raw


def colours(emu):
    buf, w, h, pitch = emu.frame
    seen = set()
    if emu.fmt == 1:
        for y in range(h):
            row = buf[y * pitch:y * pitch + w * 4]
            seen.update(struct.unpack(f"<{w}I", row))
        return {v & 0xFFFFFF for v in seen}, w, h
    for y in range(h):
        seen.update(struct.unpack(f"<{w}H", buf[y * pitch:y * pitch + w * 2]))
    return seen, w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cue", default="disc/scd-usa/Snatcher (USA).cue")
    ap.add_argument("--frames", type=int, default=60000)
    ap.add_argument("--sample", type=int, default=120)
    ap.add_argument("--shot-every", type=int, default=1500)
    ap.add_argument("--out", default="work/survey")
    ap.add_argument("--state-in")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--states", action="store_true", help="save a state with every screenshot")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    here = os.path.dirname(os.path.abspath(__file__))
    emu = retro.Emu(os.path.join(here, "..", "emu", "genesis_plus_gx_libretro.dylib"),
                    os.path.join(here, "..", "emu", "sysgpgx"))
    emu.load(os.path.abspath(a.cue))
    if a.state_in:
        emu.run()
        emu.load_state(a.state_in)

    rng, act = random.Random(a.seed), C_BUTTON
    nonzero = {k: np.zeros(n, bool) for k, (_, n) in REGIONS.items()}
    changed = {k: np.zeros(n, bool) for k, (_, n) in REGIONS.items()}
    first = {}
    frames_log = []
    for f in range(1, a.frames + 1):
        press = []
        if f > 9500 and f % 35 < 4:
            if f % 35 == 0:
                act = rng.choices([C_BUTTON, DOWN, UP, B_BUTTON], [60, 25, 10, 5])[0]
            press = [act]
        for lo, hi, b in BOOT:
            if lo <= f <= hi:
                press = [b]
        emu.run(press)
        if f % a.sample == 0:
            s = serialize(emu)
            for k, (off, n) in REGIONS.items():
                m = np.frombuffer(swap(s[off:off + n]), np.uint8)
                first.setdefault(k, m)
                nonzero[k] |= m != 0
                changed[k] |= m != first[k]
            cols, w, h = colours(emu)
            frames_log.append({"frame": f, "colours": len(cols), "w": w, "h": h})
        if a.shot_every and f % a.shot_every == 0:
            emu.screenshot(os.path.join(a.out, f"f{f:06d}.png"))
            if a.states:
                emu.save_state(os.path.join(a.out, f"f{f:06d}.state"))
            print(f, frames_log[-1] if frames_log else "", flush=True)
    emu.save_state(os.path.join(a.out, "end.state"))

    def blocks(bits, size):
        return "".join("#" if bits[i:i + size].any() else "." for i in range(0, len(bits), size))

    report = {"frames": a.frames, "samples": frames_log, "memory": {}}
    for k, (_, n) in REGIONS.items():
        report["memory"][k] = {
            "size": n, "ever_nonzero": int(nonzero[k].sum()), "ever_changed": int(changed[k].sum()),
            "map_nonzero_4k": blocks(nonzero[k], 0x1000), "map_changed_4k": blocks(changed[k], 0x1000)}
        open(os.path.join(a.out, f"{k}.used"), "wb").write(nonzero[k].astype(np.uint8).tobytes())
    json.dump(report, open(os.path.join(a.out, "report.json"), "w"), indent=1)
    cs = [x["colours"] for x in frames_log]
    print("colours per frame: max", max(cs), "frames over 61:", sum(c > 61 for c in cs), "of", len(cs))
    for k, v in report["memory"].items():
        print(f"{k:5} {v['size'] // 1024:4} KB  ever non-zero {v['ever_nonzero'] / 1024:7.1f} KB  "
              f"ever changed {v['ever_changed'] / 1024:7.1f} KB")


if __name__ == "__main__":
    main()
