#!/usr/bin/env python3
"""CD audio for the Amiga runtime: redump audio tracks -> one Paula-ready blob.

  mkcdda.py --tracks 2,3 [--rate 22030] [--out work/rt/Snatcher.cdda]
  mkcdda.py --tracks all ...          every audio track (~62 MB: streamed from the IDE drive)

Input: the redump track files (raw 16-bit LE stereo 44100) named in the cue under disc/scd-usa.
Output blob (all big-endian; loaded whole into fast RAM by the loader, or streamed from the IDE drive
when it sits on the A1200's internal drive -- tools/mkdata.py):

  'SCDA'  ULONG rate  ULONG n   n x (ULONG track, ULONG offset, ULONG bytes)   data

Data is 8-bit signed mono at `rate`, one byte per sample -- what Paula plays directly.
The rate is 22030 Hz, not 22050: Paula plays at its clock divided by a whole number, and on a PAL
Amiga 3546895 / 161 = 22030.4 Hz. Data at 22050 Hz was played 0.54% fast by engines up to 1.0 (they
took period 160), which put the music 1.1 s ahead of the picture late in a long track; at 22030 Hz
every engine version plays it at its own speed.
The game data never enters the repo: the blob is built from the user's own disc at install time.
"""
import argparse
import glob
import os
import re
import struct
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def convert(path, rate, offset=0, count=-1):
    """count 16-bit samples (-1 = to the end) from byte offset: one track out of a single-file image"""
    raw = np.fromfile(path, dtype="<i2", count=count, offset=offset)
    st = raw.reshape(-1, 2).mean(axis=1)                    # L+R -> mono, float
    step = 44100 / rate
    n = int(len(st) / step)
    idx = (np.arange(n) * step).astype(np.int64)
    mono = st[idx]
    # keep headroom: the mix of two full-scale channels still fits 16 bits here
    return np.clip(mono / 256.0, -128, 127).astype(np.int8).tobytes()


def write(out, rate, entries, data):
    """entries: (track, offset into data, bytes); returns the file's size"""
    head = struct.pack(">4sII", b"SCDA", rate, len(entries))
    base = len(head) + 12 * len(entries)
    head += b"".join(struct.pack(">III", t, base + off, ln) for t, off, ln in entries)
    with open(out, "wb") as f:
        f.write(head)
        f.write(data)
    return base + len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", default="2,3")
    ap.add_argument("--rate", type=int, default=22030)
    ap.add_argument("--disc", default=os.path.join(ROOT, "disc/scd-usa"))
    ap.add_argument("--out", default=os.path.join(ROOT, "work/rt/Snatcher.cdda"))
    a = ap.parse_args()
    if a.tracks == "all":
        tracks = sorted(int(m.group(1)) for f in glob.glob(os.path.join(a.disc, "*Track *.bin"))
                        for m in [re.search(r"Track (\d+)", f)] if int(m.group(1)) > 1)
    else:
        tracks = [int(t) for t in a.tracks.split(",")]
    entries, data = [], b""
    for t in tracks:
        hits = glob.glob(os.path.join(a.disc, f"*Track {t:02d}*.bin"))
        if not hits:
            sys.exit(f"track {t}: no file in {a.disc}")
        pcm = convert(hits[0], a.rate)
        entries.append((t, len(data), len(pcm)))
        data += pcm
        print(f"track {t}: {os.path.getsize(hits[0])} raw -> {len(pcm)} bytes "
              f"({len(pcm) / a.rate:.1f} s at {a.rate} Hz)")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    print(f"{a.out}: {write(a.out, a.rate, entries, data)} bytes")


if __name__ == "__main__":
    main()
