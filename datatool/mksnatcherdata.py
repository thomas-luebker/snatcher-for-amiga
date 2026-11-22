#!/usr/bin/env python3
"""Snatcher's data files for the Amiga engine, made from your own Sega CD disc (on a Mac/PC).

  mksnatcherdata.py "Snatcher (USA).cue" --bios bios_CD_U.bin --core genesis_plus_gx_libretro.dylib
  mksnatcherdata.py "Snatcher (USA).cue" --no-music      (everything but SNATCHER.MUS: no BIOS needed)

Input: a bin/cue image of the US disc (Konami 1994, T-95035) with its audio tracks -- one file per
track (redump) or one file for all. A bare .iso lacks the audio tracks: the CD audio of the intro,
title and ending.

Writes the data files of the engine package into the Snatcher drawer beside this tool's drawer (as the
package unpacks), which is then the complete game: the same files as the tested package. --out DIR
writes them elsewhere.
  SUBCODE.BIN SP*.BIN DATA_*.BIN PCMLT_01.BIN PCMLD_01.BIN PCMDRMDT.BIN   copied off the data track
  Snatcher.cdda    the audio tracks for Paula (8-bit mono 22030 Hz, tools/mkcdda.py)
  SNATCHER.MUS     the FM music and effects, rendered by playing every sound code in Genesis Plus GX
                   (tools/fmrender.py + tools/mkmusic.py): needs the Sega CD BIOS to boot the disc and
                   the Genesis Plus GX libretro core; takes a while
Every file is checked against tools/snatcherdata.sha1 (the files of the tested package); a mismatch is
reported. Needs Python 3 with numpy. Nothing here contains game data.
"""
import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mkcdda  # noqa: E402

RATE = 22030                    # what a PAL Paula really plays (clock / 161): see mkcdda.py
RAW, USER = 2352, 2048
SPEC = re.compile(r"^(SUBCODE|SP\d\d|DATA_\w+|PCMLT_01|PCMLD_01|PCMDRMDT)\.BIN$")


def msf(s):
    m, sec, f = (int(x) for x in s.split(":"))
    return (m * 60 + sec) * 75 + f


def parse_cue(cue):
    """-> [{no, mode, path, start, end}] with start/end in bytes inside path (end None = file end);
    a track runs from its first INDEX (00 if there is one: the pregap is part of it, as in a redump
    file) to the next track's first INDEX in the same file"""
    folder, tracks, path = os.path.dirname(os.path.abspath(cue)), [], None
    for line in open(cue, encoding="latin1"):
        w = line.split()
        if not w:
            continue
        if w[0] == "FILE":
            path = os.path.join(folder, re.search(r'FILE\s+"?(.+?)"?\s+\w+\s*$', line.strip()).group(1))
        elif w[0] == "TRACK":
            tracks.append({"no": int(w[1]), "mode": w[2], "path": path, "index": None})
        elif w[0] == "INDEX" and tracks[-1]["index"] is None:
            tracks[-1]["index"] = msf(w[2])
    for i, t in enumerate(tracks):
        size = RAW if t["mode"] in ("AUDIO", "MODE1/2352", "MODE2/2352") else USER
        t["start"] = t["index"] * size
        nxt = tracks[i + 1] if i + 1 < len(tracks) else None
        t["end"] = nxt["index"] * size if nxt and nxt["path"] == t["path"] else None
        if not os.path.exists(t["path"]):
            sys.exit(f"{cue}: track {t['no']}: {t['path']} not found")
    return tracks


class DataTrack:
    """2048-byte user data of the MODE1 track, by logical sector"""
    def __init__(self, t):
        if t["mode"] not in ("MODE1/2352", "MODE1/2048"):
            sys.exit(f"track 1 is {t['mode']}, expected MODE1 (is this the Sega CD disc?)")
        self.f, self.raw, self.base = open(t["path"], "rb"), t["mode"] == "MODE1/2352", t["start"]

    def read(self, lba, n):
        if not self.raw:
            self.f.seek(self.base + lba * USER)
            return self.f.read(n)
        out = bytearray()
        while len(out) < n:
            self.f.seek(self.base + lba * RAW)
            s = self.f.read(RAW)
            out += s[16:16 + USER]
            lba += 1
        return bytes(out[:n])

    def files(self):
        """the root directory (the disc has no subdirectories): {name: (lba, size)}"""
        pvd = self.read(16, USER)
        if pvd[1:6] != b"CD001":
            sys.exit("track 1 has no ISO 9660 file system")
        lba, size = struct.unpack_from("<I", pvd, 156 + 2)[0], struct.unpack_from("<I", pvd, 156 + 10)[0]
        d, i, out = self.read(lba, size), 0, {}
        while i < len(d):
            n = d[i]
            if n == 0:
                i = (i // USER + 1) * USER
                continue
            name = d[i + 33:i + 33 + d[i + 32]].decode("latin1").split(";")[0]
            if not d[i + 25] & 2:
                out[name] = struct.unpack_from("<I", d, i + 2)[0], struct.unpack_from("<I", d, i + 10)[0]
            i += n
        return out


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cue")
    ap.add_argument("--out", help="default: the Snatcher drawer beside this tool")
    ap.add_argument("--bios", help="the US Sega CD BIOS (bios_CD_U.bin) -- for SNATCHER.MUS")
    ap.add_argument("--core", help="Genesis Plus GX libretro core (.dylib/.so/.dll) -- for SNATCHER.MUS")
    ap.add_argument("--no-music", action="store_true", help="skip SNATCHER.MUS")
    ap.add_argument("--work", help="keep fmrender's WAVs here (default: a temporary folder)")
    a = ap.parse_args()
    if not a.no_music and not (a.bios and a.core):
        sys.exit("SNATCHER.MUS needs --bios and --core (or --no-music)")
    ref = dict(reversed(line.split()) for line in open(os.path.join(HERE, "snatcherdata.sha1")))
    if not a.out:
        a.out = os.path.join(os.path.dirname(HERE), "Snatcher")
        if not os.path.exists(os.path.join(a.out, "snatch-aga")):
            sys.exit(f"no Snatcher drawer at {a.out}: unpack the whole package, or give --out")
    os.makedirs(a.out, exist_ok=True)
    tracks = parse_cue(a.cue)
    bad = []

    def check(name):
        p = os.path.join(a.out, name)
        if sha1(p) != ref.get(name):
            bad.append(name)
            print(f"  {name}: differs from the tested package")

    # the data track's files
    dt = DataTrack(tracks[0])
    found = dt.files()
    files = {n: found[n] for n in ref if SPEC.match(n) and n in found}
    missing = sorted(n for n in ref if SPEC.match(n) and n not in found)
    if missing:
        sys.exit(f"not on this disc: {' '.join(missing)} (is it the US Snatcher?)")
    print(f"data track: {len(files)} files")
    for n, (lba, size) in sorted(files.items()):
        with open(os.path.join(a.out, n), "wb") as f:
            f.write(dt.read(lba, size))
        check(n)

    # the audio tracks -> Snatcher.cdda
    audio = [t for t in tracks if t["mode"] == "AUDIO"]
    if not audio:
        sys.exit(f"{a.cue}: no audio tracks -- the intro, title and ending need them")
    entries, data = [], bytearray()
    for t in audio:
        count = -1 if t["end"] is None else (t["end"] - t["start"]) // 2
        pcm = mkcdda.convert(t["path"], RATE, t["start"], count)
        entries.append((t["no"], len(data), len(pcm)))
        data += pcm
    print(f"audio: {len(audio)} tracks, {mkcdda.write(os.path.join(a.out, 'Snatcher.cdda'), RATE, entries, data)} bytes")
    check("Snatcher.cdda")

    # the FM music: fmrender boots the disc in Genesis Plus GX and records every sound code
    if not a.no_music:
        work = a.work or tempfile.mkdtemp(prefix="snatcher-fm-")
        os.makedirs(work, exist_ok=True)
        sysdir = os.path.join(work, "system")
        os.makedirs(sysdir, exist_ok=True)
        shutil.copyfile(a.bios, os.path.join(sysdir, "bios_CD_U.bin"))
        print("FM music: rendering 122 sound codes (this takes a while) ...", flush=True)
        py = [sys.executable, "-u"]
        subprocess.run(py + [os.path.join(HERE, "fmrender.py"), "--out", work, "--cue", os.path.abspath(a.cue),
                             "--core", os.path.abspath(a.core), "--sysdir", sysdir], check=True)
        subprocess.run(py + [os.path.join(HERE, "mkmusic.py"), "--dir", work,
                             "--out", os.path.join(a.out, "SNATCHER.MUS")], check=True)
        check("SNATCHER.MUS")
        if not a.work:
            shutil.rmtree(work)

    print(f"\n{a.out}: done -- copy this drawer (with Snatcher.info beside it) to the Amiga.")
    if bad:
        print(f"{len(bad)} file(s) differ from the tested package: {' '.join(bad)}\n"
              "The engine may still work; if it does not, say which disc image you used.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
