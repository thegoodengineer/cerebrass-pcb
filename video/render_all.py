#!/usr/bin/env python3
"""Render every HTML scene for exactly as long as its narration needs.

    python video/render_all.py        # all
    python video/render_all.py 6      # single scenes

seconds = narration + lead + tail + cross-fade + 0.6 (the first 0.6 s of a recording is page load and is trimmed).
"""
from __future__ import annotations

import sys

from film import ASSETS, LEAD, RENDERS, REPO_URL, VO, XFADE, facts, scenes
from narrate import duration
from render_scene import render


def params(scene_id: str) -> dict:
    f = facts()
    if scene_id == "cold":
        return {"errors": f.get("errors_before") or 0}
    if scene_id == "numbers":
        return {"tps": f["tps"], "calls": f["calls"], "loop": f["loop_s"], "cache": f["cache_pct"], "eb": f["errors_before"], "ops": f["ops"],
                "drc": f["drc_errors"], "unc": f["unconnected"], "erc": f["erc_errors"]}
    if scene_id == "close":
        return {"url": REPO_URL}
    return {}


def main() -> None:
    want = {int(a) for a in sys.argv[1:]}
    for sc in scenes():
        if sc["kind"] != "html" or (want and sc["n"] not in want):
            continue
        secs = duration(VO / f"{sc['n']:02d}.wav") + LEAD + sc["tail"] + XFADE + 0.6
        out = RENDERS / f"s{sc['n']:02d}.webm"
        render(sc["id"], secs, out, params(sc["id"]), shot=ASSETS / f"frame_{sc['id']}.png")
        print(f"  s{sc['n']:02d} {sc['id']:<8} {secs:5.2f}s -> {out.name}")


if __name__ == "__main__":
    main()
