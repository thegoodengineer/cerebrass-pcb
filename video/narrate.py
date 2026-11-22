#!/usr/bin/env python3
"""Narration: one file per script row -> vo/NN.wav plus vo/NN.txt.

    python video/narrate.py          # every line
    python video/narrate.py 4 6      # regenerate single lines

Voice: Microsoft Edge neural TTS (edge-tts, en-US-AndrewNeural). No GPU needed.
Words that TTS tends to mangle are respelled in the spoken text only (see film.py):
KiCad -> "Key-CAD", ESP32 -> "E S P thirty-two", I2C -> "I squared C", K3.1 -> "K three point one".
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import wave

import edge_tts
import imageio_ffmpeg

from film import VO, scenes

import os

VOICE = "en-US-AndrewNeural"
RATE = "+6%"
USE_SAPI = os.environ.get("CLAWBOARD_TTS") == "sapi"
SAPI_VOICE = os.environ.get("CLAWBOARD_SAPI_VOICE", "Microsoft David Desktop")
SAPI_RATE = 1
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def duration(path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


async def speak(sc: dict) -> float:
    VO.mkdir(exist_ok=True)
    stem = VO / f"{sc['n']:02d}"
    stem.with_suffix(".txt").write_text(sc["text"] + "\n", encoding="utf-8")
    raw = stem.with_suffix(".mp3")
    try:
        if USE_SAPI:
            raise RuntimeError("offline voice requested")
        await edge_tts.Communicate(sc["say"], VOICE, rate=RATE).save(str(raw))
    except Exception as e:  # the online voice is unreachable: fall back to the offline Windows voice
        raw = stem.with_suffix(".raw.wav")
        sapi(sc["say"], raw)
        if not USE_SAPI:
            print(f"     (edge-tts unavailable: {type(e).__name__}; used the offline Windows voice)")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(raw), "-af", "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                    "silenceremove=start_periods=1:start_threshold=-50dB,areverse", "-ar", "48000", "-ac", "2", str(stem.with_suffix(".wav"))], check=True)
    raw.unlink()
    return duration(stem.with_suffix(".wav"))


def sapi(text: str, out) -> None:
    """Offline fallback: Windows System.Speech (no network)."""
    txt = out.with_suffix(".say.txt")
    txt.write_text(text, encoding="utf-8")
    ps = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name }; "
        f"$pick = @('{SAPI_VOICE}', 'Microsoft David Desktop', 'Microsoft Mark', 'Microsoft Zira Desktop') | Where-Object {{ $v -contains $_ }} | Select-Object -First 1; "
        "if ($pick) { $s.SelectVoice($pick) }; "
        f"$s.Rate = {SAPI_RATE}; $s.SetOutputToWaveFile('{out}'); "
        f"$s.Speak([IO.File]::ReadAllText('{txt}', [Text.Encoding]::UTF8)); $s.Dispose()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], check=True)
    txt.unlink()


async def main() -> None:
    want = {int(a) for a in sys.argv[1:]}
    total = 0.0
    for sc in scenes():
        wav = VO / f"{sc['n']:02d}.wav"
        if want and sc["n"] not in want and wav.exists():
            d = duration(wav)
        else:
            d = await speak(sc)
        total += d
        print(f"  {sc['n']:02d} {sc['id']:<16} {d:5.2f}s  {sc['text'][:86]}")
    print(f"narration total {total:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
