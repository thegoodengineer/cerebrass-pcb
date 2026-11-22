#!/usr/bin/env python3
"""Render one motion-graphics scene to video with headless Chrome (Playwright record_video).

    python video/render_scene.py <scene> <seconds> <out.webm> [key=value ...]
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from film import HERE

ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--hide-scrollbars"]


def render(scene: str, seconds: float, out: Path, params: "dict | None" = None, shot: "Path | None" = None) -> None:
    url = (HERE / "scenes" / "scenes.html").as_uri() + "?" + urlencode({"scene": scene, **(params or {})})
    tmp = Path(tempfile.mkdtemp(prefix="clawboard_scene_"))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=ARGS)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(tmp),
                                  record_video_size={"width": 1920, "height": 1080}, color_scheme="dark")
        page = ctx.new_page()
        page.goto(url)
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(int(seconds * 1000))
        if shot:
            page.screenshot(path=str(shot))
        video = page.video
        ctx.close()
        browser.close()
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(video.path(), out)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    kv = dict(a.split("=", 1) for a in sys.argv[4:])
    render(sys.argv[1], float(sys.argv[2]), Path(sys.argv[3]), kv)
    print("wrote", sys.argv[3])
