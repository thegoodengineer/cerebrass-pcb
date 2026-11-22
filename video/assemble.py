#!/usr/bin/env python3
"""Assemble the film with ffmpeg only.

    python video/assemble.py [out.mp4]

Each clip = narration + 0.4 s lead + tail, 1080p30 H.264 crf 18. Cuts are 0.7 s cross-fades
(video xfade + audio acrossfade). Capture scenes are cut from assets/demo.webm at the
milestones logged in assets/demo.webm.json; the last frame is held if narration runs longer.
Nothing is sped up. Music is the procedural bed, side-chain ducked under the narration.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

from film import ASSETS, FPS, HERE, LEAD, LIMIT_S, RENDERS, VO, XFADE, clip_len, scenes
from narrate import duration

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
CLIPS = HERE / "clips"


def run(args: list[str]) -> None:
    p = subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"ffmpeg failed:\n{p.stderr[-1500:]}")


def cut_points() -> dict[str, float]:
    marks = json.loads((ASSETS / "demo.webm.json").read_text(encoding="utf-8"))["milestones"]
    first = {}
    for m in marks:
        first.setdefault(m["event"], m["t"])
    compare = {}
    cmp_file = ASSETS / "compare.webm.json"
    if cmp_file.exists():
        cm = {}
        for m in json.loads(cmp_file.read_text(encoding="utf-8"))["milestones"]:
            cm.setdefault(m["event"], m["t"])
        # a few seconds before the slower lane finishes, so the verdict banner arrives on screen
        last_done = max(v for k, v in cm.items() if k.endswith(":lane_done") or k.endswith(":lane_error") or k.endswith(":lane_stopped"))
        compare = {"compare": max(0.0, last_done - 4.0)}
    return {
        **compare,
        "idle": first["board_visible"] + 0.5,
        # a few seconds before the patch lands: the model is writing the patch, then the operations apply
        "patch": max(first["click_run"], first["tool_apply_pcb_patch"] - 3.6),
    }


def build_clip(sc: dict, cuts: dict) -> tuple[Path, float]:
    vo = VO / f"{sc['n']:02d}.wav"
    d = clip_len(sc, duration(vo))
    out = CLIPS / f"c{sc['n']:02d}.mp4"
    if sc["kind"] == "html":
        src, ss = RENDERS / f"s{sc['n']:02d}.webm", 0.6
    elif sc["cut"] == "compare":
        src, ss = ASSETS / "compare.webm", cuts["compare"]
    elif sc["cut"] == "oracle":
        # finished state of the same run, recorded once KiCad's check had completed (capture_final.py)
        src, ss = ASSETS / "demo_final.webm", 2.2
    else:
        src, ss = ASSETS / "demo.webm", cuts[sc["cut"]]
    # product recordings are laid out at 1536x864; crop away any padding, then scale to the 1080p frame
    crop = "crop=min(iw\\,1536):min(ih\\,864):0:0," if sc["kind"] == "capture" else ""
    vf = (f"[0:v]{crop}fps={FPS},scale=1920:1080:flags=lanczos,format=yuv420p,tpad=stop_mode=clone:stop_duration=8,"
          f"trim=duration={d},setpts=PTS-STARTPTS[v]")
    af = (f"[1:a]aresample=48000,adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},apad,atrim=duration={d},asetpts=PTS-STARTPTS,"
          "aformat=sample_fmts=fltp:channel_layouts=stereo[a]")
    run(["-ss", f"{ss:.2f}", "-i", str(src), "-i", str(vo), "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)])
    return out, d


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "clawboard_demo.mp4"
    CLIPS.mkdir(exist_ok=True)
    cuts = cut_points()
    clips = []
    for sc in scenes():
        path, d = build_clip(sc, cuts)
        clips.append((path, d))
        src = "html" if sc["kind"] == "html" else ("demo_final.webm" if sc["cut"] == "oracle" else
                                                     f"{'compare' if sc['cut'] == 'compare' else 'demo'}.webm @ {cuts[sc['cut']]:.1f}s")
        print(f"  {sc['n']:02d} {sc['id']:<16} {d:5.2f}s  ({src})")
    total = sum(d for _, d in clips) - XFADE * (len(clips) - 1)
    print(f"total {total:.2f}s (limit {LIMIT_S:.0f}s)")
    if total > LIMIT_S:
        raise SystemExit("over the limit: cut words in film.py and re-run narrate.py (the film is never sped up)")

    inputs: list[str] = []
    for path, _ in clips:
        inputs += ["-i", str(path)]
    inputs += ["-stream_loop", "-1", "-i", str(ASSETS / "music.wav")]
    fc = []
    acc = clips[0][1]
    v_prev, a_prev = "[0:v]", "[0:a]"
    for i in range(1, len(clips)):
        fc.append(f"{v_prev}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={acc - XFADE:.3f}[v{i}]")
        fc.append(f"{a_prev}[{i}:a]acrossfade=d={XFADE}:c1=tri:c2=tri[a{i}]")
        v_prev, a_prev = f"[v{i}]", f"[a{i}]"
        acc += clips[i][1] - XFADE
    m = len(clips)
    fc.append(f"{a_prev}asplit=2[vo1][vo2]")
    fc.append(f"[{m}:a]atrim=duration={total:.3f},volume=0.30[bed]")
    fc.append("[bed][vo1]sidechaincompress=threshold=0.02:ratio=9:attack=15:release=500[duck]")
    fc.append(f"[vo2][duck]amix=inputs=2:normalize=0:duration=first,afade=t=in:d=0.5,afade=t=out:st={total - 1.3:.3f}:d=1.3,"
              "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aout]")
    fc.append(f"{v_prev}fade=t=in:d=0.4,fade=t=out:st={total - 0.6:.3f}:d=0.6,format=yuv420p[vout]")
    run([*inputs, "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out)])
    print(f"wrote {out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
