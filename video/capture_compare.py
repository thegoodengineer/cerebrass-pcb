#!/usr/bin/env python3
"""Record one LIVE side-by-side run: Cerebras lane vs. the configured baseline lane.

    python video/capture_compare.py        # dashboard running, CEREBRAS_* and BASELINE_* set in .env

Writes assets/compare.webm, assets/compare.webm.json (milestones per lane) and
assets/report_cerebras.json / assets/report_baseline.json (per-request reports of this run).
Refuses to record a simulated run.
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

LANES = ("cerebras", "baseline")


def main() -> int:
    cfg = api("/api/config")
    for lane in LANES:
        if not cfg["lanes"][lane]["ready"]:
            print(f"{lane} endpoint is not configured in .env - a simulated lane is not recorded for the film.")
            return 1
    engine = "openclaw" if cfg["defaultEngine"] == "openclaw" else "direct"
    if engine == "openclaw" and cfg["openclaw"]["gateway"]["status"] != "ready":
        print("OpenClaw gateway is not ready yet; wait for it and run again.")
        return 1
    api("/api/reset", {})
    ASSETS.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="clawboard_cmp_"))
    marks: list[dict] = []
    size = {"width": round(1920 / ZOOM), "height": round(1080 / ZOOM)}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
        ctx = browser.new_context(viewport=size, device_scale_factor=ZOOM, record_video_dir=str(tmp), record_video_size=size, color_scheme="dark")
        page = ctx.new_page()
        t0 = time.time()

        def mark(name, **extra):
            marks.append({"t": round(time.time() - t0, 2), "event": name, **extra})
            print(f"  {marks[-1]['t']:6.2f}s  {name} {extra if extra else ''}")

        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector(".canvas svg", timeout=30000)
        page.wait_for_timeout(2500)
        sim = page.locator(".toggle input")
        if sim.is_checked():
            sim.uncheck()
        page.select_option("select", engine)
        mark("click_compare", engine=engine)
        page.locator("button.hero").click()
        seen: set[str] = set()
        steps = {lane: 0 for lane in LANES}
        deadline = time.time() + 600
        all_done_at = None
        while time.time() < deadline:
            page.wait_for_timeout(500)
            lanes = api("/api/state")["lanes"]
            for lane_id in LANES:
                lane = lanes.get(lane_id)
                if not lane:
                    continue

                def once(key, **extra):
                    if f"{lane_id}:{key}" not in seen:
                        seen.add(f"{lane_id}:{key}")
                        mark(f"{lane_id}:{key}", **extra)

                if lane.get("loopStartedAt"):
                    once("first_model_request")
                for e in lane.get("trace", []):
                    if e.get("kind") == "tool":
                        once(f"tool_{e['name']}")
                    if e.get("kind") == "llm":
                        once(f"llm_{e['n']}_done", tps=e.get("tps"), ms=e.get("ms"))
                if len(lane.get("steps", [])) != steps[lane_id]:
                    steps[lane_id] = len(lane["steps"])
                    mark(f"{lane_id}:patch_step", n=steps[lane_id], errors_after=lane["steps"][-1]["after"])
                if (lane.get("audit") or {}).get("pass"):
                    once("audit_pass")
                if lane["status"] in ("done", "error", "stopped"):
                    once(f"lane_{lane['status']}", error=lane.get("error"))
            if all(lanes.get(x, {}).get("status") in ("done", "error", "stopped") for x in LANES):
                all_done_at = all_done_at or time.time()
                if time.time() - all_done_at > 7:      # hold on the verdict banner
                    break
        mark("end")
        state = api("/api/state")
        video = page.video
        ctx.close()
        browser.close()
        shutil.move(video.path(), ASSETS / "compare.webm")
    shutil.rmtree(tmp, ignore_errors=True)
    (ASSETS / "compare.webm.json").write_text(json.dumps({"zoom": ZOOM, "engine": engine, "milestones": marks}, indent=1), encoding="utf-8")
    ok = True
    for lane_id in LANES:
        lane = state["lanes"].get(lane_id, {})
        report = api(f"/api/report/{lane_id}")
        (ASSETS / f"report_{lane_id}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        good = lane.get("status") == "done" and report.get("mode") == "live"
        ok = ok and good
        m = lane.get("metrics", {})
        print(f"[{lane_id}] status={lane.get('status')} audit_pass={(lane.get('audit') or {}).get('pass')} mode={report.get('mode')} "
              f"llm_ms={m.get('llmMs')} tool_ms={m.get('toolMs')} tps={round(m.get('tps') or 0)} ttft={m.get('ttftMs')} error={lane.get('error')}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
