"""Synthesise a small instrument library as WAV files, stdlib only.

This machine has no third-party VSTi and ReaSynth has no filter, so gospel
voicings on it sound like a toy. Generating the tones here gives a Rhodes-ish
electric piano, a drawbar organ, a choir pad, an electric bass and a gospel
drum kit - all deterministic and reproducible on any REAPER install.

Pitched instruments are multisampled a few notes apart so ReaSamplOmatic5000
never has to shift a sample more than about an octave.
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

SR = 48000
OUT = Path(__file__).parent / "demo" / "samples"

TWO_PI = 2 * math.pi


def hz(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def write_wav(name: str, buf: list[float], peak_target: float = 0.89) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    peak = max(abs(s) for s in buf) or 1.0
    norm = peak_target / peak
    path = OUT / name
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(
            b"".join(
                struct.pack("<h", max(-32767, min(32767, int(s * norm * 32767))))
                for s in buf
            )
        )
    return path


def fade(buf: list[float], ms: float = 8.0) -> None:
    n = min(len(buf), int(SR * ms / 1000))
    for i in range(n):
        buf[len(buf) - n + i] *= 1.0 - i / n


class LP:
    """One-pole low pass, cascadable."""

    def __init__(self, fc: float):
        self.a = 1 - math.exp(-TWO_PI * fc / SR)
        self.y = 0.0

    def __call__(self, x: float) -> float:
        self.y += self.a * (x - self.y)
        return self.y


class HP:
    def __init__(self, fc: float):
        self.a = math.exp(-TWO_PI * fc / SR)
        self.px = self.py = 0.0

    def __call__(self, x: float) -> float:
        y = self.a * (self.py + x - self.px)
        self.px, self.py = x, y
        return y


# --------------------------------------------------------------- instruments


def rhodes(note: int, dur: float = 3.4) -> list[float]:
    """Sine body plus a fast-decaying inharmonic tine - the Rhodes signature."""
    f = hz(note)
    n = int(SR * dur)
    out = []
    bright = max(0.35, 1.0 - (note - 40) / 90)  # lower notes keep more tine
    for i in range(n):
        t = i / SR
        body = math.sin(TWO_PI * f * t) * math.exp(-t * 1.35)
        second = math.sin(TWO_PI * f * 2 * t) * math.exp(-t * 2.6) * 0.22
        tine = math.sin(TWO_PI * f * 6.18 * t) * math.exp(-t * 11.0) * 0.40 * bright
        click = math.exp(-t * 420) * 0.12 * math.sin(TWO_PI * f * 11 * t)
        out.append(body + second + tine + click)
    fade(out, 20)
    return out


def organ(note: int, dur: float = 5.0) -> list[float]:
    """Additive drawbars with a slow Leslie-ish wobble; sustains cleanly.

    The vibrato MUST go through a phase accumulator. Writing it as
    sin(2*pi * f*wob(t) * t) looks equivalent but is not: the instantaneous
    frequency becomes f*(wob + t*dwob/dt), so the deviation grows with time
    and the pitch drifts by a factor of three by the fifth second. That bug
    made the first version of this organ audibly broken.
    """
    f = hz(note)
    # 16' 8' 5 1/3' 4' 2 2/3' 2' 1 3/5' 1 1/3' 1'
    bars = [(0.5, 0.55), (1.0, 1.00), (1.5, 0.50), (2.0, 0.62),
            (3.0, 0.34), (4.0, 0.26), (5.0, 0.16), (6.0, 0.14), (8.0, 0.20)]
    bars = [(m, a) for m, a in bars if f * m < SR / 2.2]
    n = int(SR * dur)
    phases = [0.0] * len(bars)
    out = []
    for i in range(n):
        t = i / SR
        wob = 1.0 + 0.004 * math.sin(TWO_PI * 5.6 * t)
        env = min(1.0, t / 0.012) * (0.94 + 0.06 * math.sin(TWO_PI * 5.6 * t + 1.2))
        s = 0.0
        for k, (mult, amp) in enumerate(bars):
            phases[k] += f * mult * wob / SR
            s += math.sin(TWO_PI * phases[k]) * amp
        out.append(s * env * 0.30)
    fade(out, 25)
    return out


def choir(note: int, dur: float = 5.0) -> list[float]:
    """Detuned saw stack, low-passed, slow swell, light vibrato and breath."""
    f = hz(note)
    n = int(SR * dur)
    rnd = random.Random(note * 13 + 5)
    detunes = [-0.16, -0.07, 0.0, 0.06, 0.15]
    phases = [rnd.random() for _ in detunes]
    lp1, lp2 = LP(f * 4.5 + 700), LP(f * 5.5 + 1100)
    out = []
    breath = HP(2200)
    for i in range(n):
        t = i / SR
        vib = 1.0 + 0.004 * math.sin(TWO_PI * 4.7 * t + 0.6)
        s = 0.0
        for k, d in enumerate(detunes):
            ff = f * (1 + d / 100) * vib
            phases[k] += ff / SR
            s += (2 * (phases[k] % 1.0) - 1) * 0.2
        s = lp2(lp1(s))
        air = breath(rnd.uniform(-1, 1)) * 0.035
        swell = min(1.0, t / 0.42) * (1.0 - 0.12 * math.exp(-t * 0.8))
        out.append((s + air) * swell)
    fade(out, 40)
    return out


def bass(note: int, dur: float = 2.6) -> list[float]:
    """Round electric bass: fundamental, a little grit, short pluck."""
    f = hz(note)
    n = int(SR * dur)
    out = []
    lp = LP(f * 6 + 220)
    for i in range(n):
        t = i / SR
        env = math.exp(-t * 1.5)
        s = math.sin(TWO_PI * f * t) * env
        s += math.sin(TWO_PI * f * 2 * t) * env * 0.30
        s += math.sin(TWO_PI * f * 3 * t) * math.exp(-t * 4.5) * 0.14
        pluck = math.exp(-t * 55) * 0.5 * (2 * ((f * 2.5 * t) % 1.0) - 1)
        out.append(lp(s + pluck))
    fade(out, 15)
    return out


# ------------------------------------------------------------------- the kit


def kick(dur: float = 0.52) -> list[float]:
    n = int(SR * dur)
    out, phase = [], 0.0
    for i in range(n):
        t = i / SR
        f = 41 + (104 - 41) * math.exp(-t * 26)
        phase += TWO_PI * f / SR
        out.append(math.sin(phase) * math.exp(-t * 5.4) * 0.97
                   + math.exp(-t * 340) * 0.28 * math.sin(TWO_PI * 1250 * t))
    fade(out)
    return out


def snare(dur: float = 0.26) -> list[float]:
    n = int(SR * dur)
    rnd = random.Random(17)
    hp = HP(900)
    out, p1, p2 = [], 0.0, 0.0
    for i in range(n):
        t = i / SR
        noise = hp(rnd.uniform(-1, 1)) * math.exp(-t * 22)
        p1 += TWO_PI * 192 / SR
        p2 += TWO_PI * 291 / SR
        body = (math.sin(p1) * 0.66 + math.sin(p2) * 0.34) * math.exp(-t * 30)
        out.append(noise * 0.82 + body * 0.52)
    fade(out)
    return out


def hat(dur: float, decay: float) -> list[float]:
    n = int(SR * dur)
    rnd = random.Random(23)
    h1, h2 = HP(6500), HP(8200)
    out = []
    for i in range(n):
        t = i / SR
        out.append(h2(h1(rnd.uniform(-1, 1))) * math.exp(-t * decay))
    fade(out, 4)
    return out


def clap(dur: float = 0.30) -> list[float]:
    """Four offset noise bursts, the way a real handclap smears."""
    n = int(SR * dur)
    rnd = random.Random(31)
    hp = HP(1100)
    bursts = [0.000, 0.009, 0.019, 0.031]
    out = []
    for i in range(n):
        t = i / SR
        s = 0.0
        for k, off in enumerate(bursts):
            if t >= off:
                s += math.exp(-(t - off) * (190 if k < 3 else 24)) * (0.85 if k < 3 else 1.0)
        out.append(hp(rnd.uniform(-1, 1)) * s * 0.5)
    fade(out)
    return out


def tambourine(dur: float = 0.34) -> list[float]:
    """Noise plus a cluster of inharmonic jingles."""
    n = int(SR * dur)
    rnd = random.Random(41)
    hp = HP(4000)
    jingles = [2870, 3410, 4120, 5230, 6180, 7640, 9120]
    out = []
    for i in range(n):
        t = i / SR
        s = hp(rnd.uniform(-1, 1)) * math.exp(-t * 34) * 0.6
        for f in jingles:
            s += math.sin(TWO_PI * f * t) * math.exp(-t * 13) * 0.085
        out.append(s)
    fade(out, 6)
    return out


def ride(dur: float = 1.5) -> list[float]:
    n = int(SR * dur)
    rnd = random.Random(47)
    hp = HP(3200)
    partials = [1210, 1670, 2130, 2790, 3510, 4430, 5610, 7020]
    out = []
    for i in range(n):
        t = i / SR
        s = hp(rnd.uniform(-1, 1)) * math.exp(-t * 9) * 0.32
        for k, f in enumerate(partials):
            s += math.sin(TWO_PI * f * t) * math.exp(-t * (1.6 + k * 0.35)) * 0.10
        out.append(s)
    fade(out, 20)
    return out


# --------------------------------------------------- multisample definitions
# Each pitched instrument: list of (base_note, lo, hi) zones.

ZONES = {
    "ep":     [(48, 33, 53), (60, 54, 65), (72, 66, 88)],
    "organ":  [(48, 33, 53), (60, 54, 65), (72, 66, 88)],
    "choir":  [(55, 45, 60), (67, 61, 72), (76, 73, 88)],
    "bass":   [(33, 21, 38), (45, 39, 57)],
}

GENERATORS = {"ep": rhodes, "organ": organ, "choir": choir, "bass": bass}

DRUMS = {
    "g_kick.wav": kick,
    "g_snare.wav": snare,
    "g_hat_closed.wav": lambda: hat(0.070, 105),
    "g_hat_open.wav": lambda: hat(0.38, 11),
    "g_clap.wav": clap,
    "g_tamb.wav": tambourine,
    "g_ride.wav": ride,
}


def sample_name(inst: str, base: int) -> str:
    return "%s_%d.wav" % (inst, base)


def generate_all() -> list[str]:
    made = []
    for inst, zones in ZONES.items():
        gen = GENERATORS[inst]
        for base, _, _ in zones:
            name = sample_name(inst, base)
            buf = gen(base)
            p = write_wav(name, buf)
            made.append(name)
            print("%-14s base=%-3d %6.0f ms  %7d bytes" % (name, base, len(buf) / SR * 1000, p.stat().st_size))
    for name, fn in DRUMS.items():
        buf = fn()
        p = write_wav(name, buf)
        made.append(name)
        print("%-14s %16.0f ms  %7d bytes" % (name, len(buf) / SR * 1000, p.stat().st_size))
    return made


if __name__ == "__main__":
    names = generate_all()
    print()
    print("%d samples in %s" % (len(names), OUT))
