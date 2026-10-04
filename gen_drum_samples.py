"""Synthesise drum one-shots as WAV files, stdlib only.

ReaSynDr's note mapping could not be determined by measurement, and it exposes
no parameters to configure. Generating the samples here instead makes the whole
drum kit deterministic: the mapping is whatever we load them at, the timbres are
reproducible, and nothing depends on an undocumented plugin.

48 kHz / 16-bit mono, to match the project's audio device.
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

SR = 48000
OUT = Path(__file__).parent / "demo" / "samples"


def write_wav(name: str, samples: list[float]) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    peak = max(abs(s) for s in samples) or 1.0
    norm = 0.89 / peak
    path = OUT / name
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = b"".join(
            struct.pack("<h", max(-32767, min(32767, int(s * norm * 32767))))
            for s in samples
        )
        w.writeframes(frames)
    return path


def fade_tail(buf: list[float], ms: float = 6.0) -> None:
    n = min(len(buf), int(SR * ms / 1000))
    for i in range(n):
        buf[len(buf) - n + i] *= 1.0 - i / n


def kick(dur: float = 0.42) -> list[float]:
    n = int(SR * dur)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / SR
        # pitch sweep 112 Hz -> 44 Hz, fast at first
        f = 44 + (112 - 44) * math.exp(-t * 28)
        phase += 2 * math.pi * f / SR
        amp = math.exp(-t * 7.0)
        body = math.sin(phase) * amp
        click = math.exp(-t * 320) * 0.35 * math.sin(2 * math.pi * 1400 * t)
        out.append(body * 0.95 + click)
    fade_tail(out)
    return out


def snare(dur: float = 0.22) -> list[float]:
    n = int(SR * dur)
    rnd = random.Random(7)
    out = []
    hp_prev_in = hp_prev_out = 0.0
    p1 = p2 = 0.0
    for i in range(n):
        t = i / SR
        noise = rnd.uniform(-1.0, 1.0)
        # one-pole high pass so the noise sits above the body
        hp = 0.72 * (hp_prev_out + noise - hp_prev_in)
        hp_prev_in, hp_prev_out = noise, hp
        noise_env = math.exp(-t * 26)
        p1 += 2 * math.pi * 186 / SR
        p2 += 2 * math.pi * 284 / SR
        body = (math.sin(p1) * 0.7 + math.sin(p2) * 0.3) * math.exp(-t * 34)
        out.append(hp * noise_env * 0.8 + body * 0.55)
    fade_tail(out)
    return out


def hat(dur: float, decay: float) -> list[float]:
    n = int(SR * dur)
    rnd = random.Random(11)
    out = []
    prev_in = prev_out = 0.0
    for i in range(n):
        t = i / SR
        noise = rnd.uniform(-1.0, 1.0)
        # steeper high pass, applied twice, for a metallic band
        hp = 0.90 * (prev_out + noise - prev_in)
        prev_in, prev_out = noise, hp
        out.append(hp * math.exp(-t * decay))
    # second pass to push the band higher
    prev_in = prev_out = 0.0
    for i, v in enumerate(out):
        hp = 0.90 * (prev_out + v - prev_in)
        prev_in, prev_out = v, hp
        out[i] = hp
    fade_tail(out, 4.0)
    return out


KIT = {
    "kick.wav": kick(),
    "snare.wav": snare(),
    "hat_closed.wav": hat(0.075, 95),
    "hat_open.wav": hat(0.34, 13),
}

if __name__ == "__main__":
    for name, buf in KIT.items():
        p = write_wav(name, buf)
        print("%-16s %6.0f ms  %8d bytes  %s" % (name, len(buf) / SR * 1000, p.stat().st_size, p))
