"""Regenerate the organ samples and check the pitch actually stays put.

Verifying by ear is what let the bug through. A stable tone has roughly the
same zero-crossing rate at the end of the sample as at the beginning; the
broken version drifted upward by a large factor, so the ratio is the test.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import gen_instruments as GI


def zcr(frames: list[int]) -> float:
    crossings = sum(
        1 for a, b in zip(frames, frames[1:]) if (a >= 0) != (b >= 0)
    )
    return crossings / max(1, len(frames))


def analyse(path: Path, window: float = 0.4) -> tuple[float, float]:
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    data = list(struct.unpack("<%dh" % n, raw))
    k = int(GI.SR * window)
    return zcr(data[:k]), zcr(data[-k:])


if __name__ == "__main__":
    print("%-14s %10s %10s %8s  %s" % ("file", "zcr start", "zcr end", "ratio", "verdict"))
    worst = 1.0
    for base, _, _ in GI.ZONES["organ"]:
        name = GI.sample_name("organ", base)
        GI.write_wav(name, GI.organ(base))
        a, b = analyse(GI.OUT / name)
        ratio = (b / a) if a else 0.0
        worst = max(worst, ratio, (1 / ratio) if ratio else 99)
        print("%-14s %10.4f %10.4f %8.2f  %s"
              % (name, a, b, ratio, "stable" if 0.85 < ratio < 1.18 else "*** DRIFTING ***"))
    print()
    print("worst deviation: %.2fx" % worst)
