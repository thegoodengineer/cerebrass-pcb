#!/usr/bin/env python3
"""Narration: one file per script row -> vo/NN.wav plus vo/NN.txt.

    python video/narrate.py          # every line
    python video/narrate.py 4 6      # regenerate single lines

Voice engines, tried in this order (force one with CLAWBOARD_TTS=edge|piper|sapi):
  1. edge-tts, en-US-AndrewNeural (online, Microsoft Edge neural voice)
  2. Piper, en_US-ryan-high (offline neural voice, open source; model under assets/voices/)
  3. Windows System.Speech (offline, robotic - last resort)
Words that TTS tends to mangle are respelled in the spoken text only (see film.py):
KiCad -> "Key-CAD", ESP32 -> "E S P thirty-two", I2C -> "I squared C", K3.1 -> "K three point one".
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import wave

import imageio_ffmpeg

from film import ASSETS, VO, scenes

ENGINE = os.environ.get("CLAWBOARD_TTS", "auto")
EDGE_VOICE = "en-US-AndrewNeural"
EDGE_RATE = "+6%"
PIPER_MODEL = ASSETS / "voices" / "en_US-ryan-high.onnx"
PIPER_LENGTH_SCALE = 0.93            # < 1 speaks a little faster
SAPI_VOICE = os.environ.get("CLAWBOARD_SAPI_VOICE", "Microsoft David Desktop")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
_piper = None


def duration(path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


async def edge_say(text: str, out) -> None:
    import edge_tts
    await edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE).save(str(out))


def piper_say(text: str, out) -> None:
    global _piper
    from piper import PiperVoice
    if _piper is None:
        _piper = PiperVoice.load(str(PIPER_MODEL))
    with wave.open(str(out), "wb") as wf:
        try:
            from piper import SynthesisConfig
            _piper.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=PIPER_LENGTH_SCALE))
        except (ImportError, AttributeError, TypeError):  # older piper-tts API
            _piper.synthesize(text, wf)


def sapi_say(text: str, out) -> None:
    txt = out.with_suffix(".say.txt")
    txt.write_text(text, encoding="utf-8")
    ps = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name }; "
        f"$pick = @('{SAPI_VOICE}', 'Microsoft David Desktop', 'Microsoft Mark', 'Microsoft Zira Desktop') | Where-Object {{ $v -contains $_ }} | Select-Object -First 1; "
        "if ($pick) { $s.SelectVoice($pick) }; "
        f"$s.Rate = 1; $s.SetOutputToWaveFile('{out}'); "
        f"$s.Speak([IO.File]::ReadAllText('{txt}', [Text.Encoding]::UTF8)); $s.Dispose()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], check=True)
    txt.unlink()


async def speak(sc: dict) -> tuple[float, str]:
    VO.mkdir(exist_ok=True)
    stem = VO / f"{sc['n']:02d}"
    stem.with_suffix(".txt").write_text(sc["text"] + "\n", encoding="utf-8")
    raw, used = None, None
    if ENGINE in ("auto", "edge"):
        try:
            raw = stem.with_suffix(".raw.mp3")
            await edge_say(sc["say"], raw)
            used = "edge-tts"
        except Exception:
            raw.unlink(missing_ok=True)
    if used is None and ENGINE in ("auto", "piper") and PIPER_MODEL.exists():
        try:
            raw = stem.with_suffix(".raw.wav")
            piper_say(sc["say"], raw)
            used = "piper"
        except Exception as e:
            print(f"     piper failed: {type(e).__name__}: {str(e)[:120]}")
            raw.unlink(missing_ok=True)
    if used is None:
        raw = stem.with_suffix(".raw.wav")
        sapi_say(sc["say"], raw)
        used = "windows-sapi"
    trim = "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(raw), "-af", trim, "-ar", "48000", "-ac", "2", str(stem.with_suffix(".wav"))], check=True)
    raw.unlink()
    return duration(stem.with_suffix(".wav")), used


async def main() -> None:
    want = {int(a) for a in sys.argv[1:]}
    total = 0.0
    for sc in scenes():
        wav = VO / f"{sc['n']:02d}.wav"
        if want and sc["n"] not in want and wav.exists():
            d, used = duration(wav), "kept"
        else:
            d, used = await speak(sc)
        total += d
        print(f"  {sc['n']:02d} {sc['id']:<16} {d:5.2f}s  [{used}]  {sc['text'][:70]}")
    print(f"narration total {total:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
