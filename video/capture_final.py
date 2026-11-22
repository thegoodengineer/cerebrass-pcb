#!/usr/bin/env python3
"""Record the finished state of the run captured by capture_demo.py (same run, same session).

KiCad's DRC/ERC takes 15-40 s after the agent signs off. If the main recording ended before
it finished, this records the dashboard once all three verification rungs have a result, and
refreshes assets/report.json so it includes the oracle.

    python video/capture_final.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture_demo import BASE, ZOOM, api
from film import ASSETS


def main() -> int:
    prev = json.loads((ASSETS / "report.json").read_text(encoding="utf-8"))
    for _ in range(40):
        lane = api("/api/state")["lanes"].get("cerebras") or {}
        o = lane.get("oracle") or {}
        if o and not o.get("pending"):
            break
        time.sleep(3)
    report = api("/api/report/cerebras")
    if report.get("run_id") != prev.get("run_id"):
        print("the dashboard is showing a different run than assets/demo.webm - not recording")
        return 1
    (ASSETS / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="clawboard_fin_"))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
        # lay the page out as a 1536x864 window; assemble.py scales the recording to 1920x1080, so text reads on a phone
        ctx = browser.new_context(viewport={"width": round(1920 / ZOOM), "height": round(1080 / ZOOM)}, device_scale_factor=ZOOM,
                                  record_video_dir=str(tmp), record_video_size={"width": round(1920 / ZOOM), "height": round(1080 / ZOOM)}, color_scheme="dark")
        page = ctx.new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector(".rung.ok", timeout=30000)
        page.wait_for_timeout(11000)
        video = page.video
        ctx.close()
        browser.close()
        shutil.move(video.path(), ASSETS / "demo_final.webm")
    shutil.rmtree(tmp, ignore_errors=True)
    o = report["verification"]["rung3_kicad_oracle"]
    print("oracle:", {k: o.get(k) for k in ("version",)}, "drc", o.get("drc", {}).get("errors"), "unconnected", o.get("drc", {}).get("unconnected_items"), "erc", o.get("erc", {}).get("errors"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
