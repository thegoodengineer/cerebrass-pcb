#!/usr/bin/env python3
"""Procedural ambient bed (numpy only): slow minor pad, sparse arpeggio, sub, simple reverb.

    python video/music.py [seconds]   -> assets/music.wav   (original, no licence needed)
"""
from __future__ import annotations

import sys
import wave

import numpy as np

from film import ASSETS

SR = 48000


def make(seconds: float = 66.0, swells=((2.0, 9.0), (40.0, 52.0))) -> np.ndarray:
    n = int(SR * seconds)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    left = np.zeros(n)
    right = np.zeros(n)
    # pad: A minor add9, each partial breathing on its own slow LFO, slightly detuned between channels
    for i, f in enumerate((110.0, 164.81, 220.0, 261.63, 329.63, 493.88)):
        lfo = 0.55 + 0.45 * np.sin(2 * np.pi * (0.035 + 0.011 * i) * t + i * 1.3)
        amp = 0.16 / (1 + i * 0.35)
        left += amp * lfo * (np.sin(2 * np.pi * f * 0.9985 * t) + 0.25 * np.sin(2 * np.pi * f * 2 * t + i))
        right += amp * lfo * (np.sin(2 * np.pi * f * 1.0015 * t + 0.4) + 0.25 * np.sin(2 * np.pi * f * 2 * t + i + 0.7))
    sub = 0.10 * np.sin(2 * np.pi * 55.0 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.05 * t))
    left += sub
    right += sub
    # sparse arpeggio: soft plucks on a fixed, seeded pattern
    notes = (440.0, 523.25, 659.25, 493.88, 783.99, 659.25, 587.33, 880.0)
    step = 0.5
    for k in range(int(seconds / step)):
        if rng.random() > 0.42:
            continue
        f = notes[(k * 3) % len(notes)]
        start = int(k * step * SR)
        length = min(int(1.6 * SR), n - start)
        if length <= 0:
            break
        tt = np.arange(length) / SR
        pluck = 0.06 * np.exp(-tt * 3.2) * np.sin(2 * np.pi * f * tt) * (1 - np.exp(-tt * 220))
        pan = 0.3 + 0.4 * rng.random()
        left[start:start + length] += pluck * (1 - pan)
        right[start:start + length] += pluck * pan
    # simple reverb: three feedback combs per channel
    def reverb(x, delays):
        out = x.copy()
        for d_ms, g in delays:
            d = int(SR * d_ms / 1000)
            buf = x.copy()
            for _ in range(4):
                shifted = np.zeros_like(buf)
                shifted[d:] = buf[:-d] * g
                out += shifted * 0.5
                buf = shifted
        return out
    left = reverb(left, ((113, 0.55), (171, 0.5), (263, 0.42)))
    right = reverb(right, ((127, 0.55), (187, 0.5), (241, 0.42)))
    # swells at the title and at the payoff scene
    env = np.full(n, 0.72)
    for a, b in swells:
        env += 0.28 * np.clip(np.minimum((t - a) / 1.5, (b - t) / 2.5), 0, 1)
    stereo = np.stack([left, right], axis=1) * env[:, None]
    stereo /= np.max(np.abs(stereo)) / 0.7
    return stereo


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 66.0
    data = (make(seconds) * 32767).astype(np.int16)
    ASSETS.mkdir(exist_ok=True)
    with wave.open(str(ASSETS / "music.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    print(f"wrote assets/music.wav ({seconds:.0f}s)")


if __name__ == "__main__":
    main()
