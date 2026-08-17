#!/usr/bin/env python3
"""Measure the line framing of an EP6 capture.

TLB.dll frames lines by scanning for a hardware LINE-SYNC MARKER: the firmware
sets bit0 of the first uint16 sample of every line (psix/pakon/decode.py, and
visible in TLB.dll itself at 0x1001d2b0 as `testb $1,(%ebp)` stepping by -6108).
When that scan finds nothing it walks off the buffer and faults -- which is the
crash we are chasing.  So the question this answers is simply: are the markers
in the data we hand over, and at what period and phase?

    ./.venv/bin/python framing.py /tmp/ep6_dump.bin
"""
import sys

import numpy as np


def report(path):
    raw = np.fromfile(path, dtype="<u2")
    print(f"{path}: {raw.nbytes} bytes = {raw.size} uint16 samples")
    print(f"  value range {raw.min()}..{raw.max()}  mean {raw.mean():.0f}")

    mk = np.flatnonzero(raw & 1)
    print(f"  samples with bit0 set: {len(mk)} ({100.0 * len(mk) / raw.size:.3f}%)")
    if len(mk) < 10:
        print("  -> NO line-sync markers.  TLB.dll's scan cannot frame this; "
              "that is the crash.")
        return

    d = np.diff(mk)
    vals, counts = np.unique(d, return_counts=True)
    order = counts.argsort()[::-1][:6]
    print("  most common marker spacings (samples):")
    for i in order:
        print(f"    {vals[i]:7d}  x{counts[i]}")

    P = int(vals[counts.argmax()])
    hist = np.bincount(mk % P, minlength=P)
    phase = int(hist.argmax())
    onphase = int(hist[phase])
    print(f"  line period P = {P} samples ({P * 2} bytes)")
    print(f"  phase = {phase} samples -> line 0 starts at byte {phase * 2}")
    print(f"  {onphase}/{len(mk)} markers on that phase "
          f"({100.0 * onphase / len(mk):.1f}%), ~{raw.size // P} lines")
    # channels x width = period.  Report every plausible split rather than
    # assuming this scanner's calibration geometry.
    splits = [(c, P // c) for c in (3, 4) if P % c == 0]
    if splits:
        print("  -> " + ", or ".join("%d channels x %d px" % (c, w) for c, w in splits))
        if P == 8000:
            print("     (8000 = RGB+IR: 6000 visible + 2000 infrared)")
    if phase:
        print(f"  -> the stream starts MID-LINE; the OEM must be given the "
              f"buffer from byte {phase * 2} on.")
    else:
        print("  -> already line-aligned at byte 0.")


def selftest():
    """Synthesise a stream with a known period and phase and recover them."""
    P, phase, lines = 6108, 137, 40
    rng = np.random.default_rng(7)
    raw = (rng.integers(0, 30000, P * (lines + 1)).astype("<u2") & 0xFFFE)
    for i in range(lines):
        raw[phase + i * P] |= 1
    mk = np.flatnonzero(raw & 1)
    d = np.unique(np.diff(mk), return_counts=True)
    assert int(d[0][d[1].argmax()]) == P
    assert int(np.bincount(mk % P, minlength=P).argmax()) == phase
    print("selftest OK")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        report(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ep6_dump.bin")
