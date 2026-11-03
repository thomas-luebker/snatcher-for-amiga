#!/usr/bin/env python3
"""The FM music and effects for the native engine: work/music/NN.wav (tools/fmrender.py) -> one file.

  mkmusic.py [--rate 22050] [--dir work/music] [--out DIR/SNATCHER.MUS]

Driver codes $01-$3E are effects, $3F-$7A the 60 songs. Each render is mixed to mono, halved to
22050 Hz (pairs averaged), scaled by ONE factor for all of them (the loudest peak -> 127, so songs
keep their levels against each other) and stored as signed 8-bit -- what Paula plays.

SNATCHER.MUS, big-endian:
  "SMUS"  rate.l  count.l (= $7B, indexed by code)
  count x [offset.l  length.l  loop.l]   offset from the file start, length in samples,
                                         loop = sample to go back to at the end, $FFFFFFFF = none
  the samples
"""
import argparse, json, os, struct, wave
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument("--rate", type=int, default=22050)
ap.add_argument("--dir", default=os.path.join(ROOT, "work/music"), help="fmrender.py's WAVs and loops.json")
ap.add_argument("--out")
a = ap.parse_args()
D = a.dir
a.out = a.out or os.path.join(D, "SNATCHER.MUS")
loops = json.load(open(os.path.join(D, "loops.json")))
N = 0x7B
clips = {}
for code in range(1, N):
    key = "%02X" % code
    if key not in loops: key = "%02x" % code
    if key not in loops: continue
    w = wave.open(os.path.join(D, loops[key]["file"]))
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
    if w.getnchannels() == 2: x = x.reshape(-1, 2).mean(axis=1)
    step = w.getframerate() // a.rate
    x = x[: len(x) // step * step].reshape(-1, step).mean(axis=1) if step > 1 else x
    ls = loops[key].get("loop_start")
    clips[code] = (x, None if ls is None else ls // step)
peak = max((np.abs(x).max() for x, _ in clips.values() if len(x)), default=1)
scale = 127.0 / peak
head = 12 + 12 * N
table, body = [], bytearray()
for code in range(N):
    if code not in clips or not len(clips[code][0]):
        table.append((0, 0, 0xFFFFFFFF)); continue
    x, ls = clips[code]
    s = np.clip(np.round(x * scale), -128, 127).astype(np.int8).tobytes()
    table.append((head + len(body), len(s), 0xFFFFFFFF if ls is None or ls >= len(s) else ls))
    body += s
    if len(body) & 1: body += b"\0"
with open(a.out, "wb") as f:
    f.write(b"SMUS" + struct.pack(">LL", a.rate, N))
    for t in table: f.write(struct.pack(">LLL", *t))
    f.write(body)
print("%s: %d clips, %.1f MB, scale %.5f" % (a.out, len(clips), os.path.getsize(a.out) / 1e6, scale))
