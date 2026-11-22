#!/usr/bin/env python3
"""Record one LIVE run of the product (real model, real tools) with Playwright.

    python video/capture_demo.py            # dashboard must be running: npm run dev

Writes assets/demo.webm, assets/demo.webm.json (DOM/state milestones with timestamps, used
as cut points) and assets/report.json (the run's per-request report - the source of every
number in the film). Refuses to record a simulated run.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

from film import ASSETS

BASE = "http://127.0.0.1:3000"
ZOOM = 1.25          # recorded frame is 1920x1080; the page is laid out 1.25x smaller so text reads on a phone
IDLE_HOLD = 14.0     # seconds of the untouched, broken board before the run starts


def api(path: str, body: "dict | None" = None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    cfg = api("/api/config")
    if not cfg["lanes"]["cerebras"]["ready"]:
        print("CEREBRAS_API_KEY is not configured - a simulated run is not recorded for the film.")
        return 1
    engine = "openclaw" if cfg["defaultEngine"] == "openclaw" else "direct"
    if engine == "openclaw" and cfg["openclaw"]["gateway"]["status"] != "ready":
        print("OpenClaw gateway is not ready yet; wait for it and run again.")
        return 1
    api("/api/reset", {})
    ASSETS.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="clawboard_cap_"))
    marks: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
        # lay the page out as a 1536x864 window; assemble.py scales the recording to 1920x1080, so text reads on a phone
        ctx = browser.new_context(viewport={"width": round(1920 / ZOOM), "height": round(1080 / ZOOM)}, device_scale_factor=ZOOM,
                                  record_video_dir=str(tmp), record_video_size={"width": round(1920 / ZOOM), "height": round(1080 / ZOOM)}, color_scheme="dark")
        page = ctx.new_page()
        t0 = time.time()

        def mark(name, **extra):
            marks.append({"t": round(time.time() - t0, 2), "event": name, **extra})
            print(f"  {marks[-1]['t']:6.2f}s  {name} {extra if extra else ''}")

        page.goto(BASE, wait_until="domcontentloaded")  # the live event stream never lets the network go idle
        page.wait_for_selector(".canvas svg", timeout=30000)
        mark("board_visible")
        page.wait_for_timeout(int(IDLE_HOLD * 1000))
        sim = page.locator(".toggle input")
        if sim.is_checked():
            sim.uncheck()
        page.select_option("select", engine)
        mark("click_run", engine=engine)
        page.get_by_role("button", name="Run audit & fix").click()
        seen: set[str] = set()
        steps = 0
        deadline = time.time() + 480
        done_at = None
        while time.time() < deadline:
            page.wait_for_timeout(500)
            lane = api("/api/state")["lanes"].get("cerebras")
            if not lane:
                continue

            def once(key, **extra):
                if key not in seen:
                    seen.add(key)
                    mark(key, **extra)

            if lane.get("loopStartedAt"):
                once("first_model_request")
            for e in lane.get("trace", []):
                if e.get("kind") == "tool":
                    once(f"tool_{e['name']}")
                if e.get("kind") == "llm":
                    once(f"llm_{e['n']}_done", tps=e.get("tps"))
            if len(lane.get("steps", [])) != steps:
                steps = len(lane["steps"])
                mark("patch_step", n=steps, errors_after=lane["steps"][-1]["after"])
            if lane.get("audit", {}) and lane["audit"].get("pass"):
                once("audit_pass")
            if lane["status"] in ("done", "error", "stopped"):
                once(f"lane_{lane['status']}", error=lane.get("error"))
                done_at = done_at or time.time()
                o = lane.get("oracle") or {}
                if lane["status"] != "done" or (o and not o.get("pending")):
                    once("oracle_done", drc=(o.get("drc") or {}).get("errors"), unconnected=(o.get("drc") or {}).get("unconnected_items"))
                    page.wait_for_timeout(6000)
                    break
                if time.time() - done_at > 150:
                    break
        mark("end")
        state = api("/api/state")
        video = page.video
        ctx.close()
        browser.close()
        shutil.move(video.path(), ASSETS / "demo.webm")
    shutil.rmtree(tmp, ignore_errors=True)
    lane = state["lanes"].get("cerebras", {})
    (ASSETS / "demo.webm.json").write_text(json.dumps({"zoom": ZOOM, "engine": engine, "status": lane.get("status"), "milestones": marks}, indent=1), encoding="utf-8")
    report = api("/api/report/cerebras")
    (ASSETS / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    ok = lane.get("status") == "done" and (lane.get("audit") or {}).get("pass") and report.get("mode") == "live"
    print(f"status={lane.get('status')} audit_pass={(lane.get('audit') or {}).get('pass')} mode={report.get('mode')} -> {'OK' if ok else 'NOT USABLE'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
