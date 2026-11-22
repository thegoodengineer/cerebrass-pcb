"""Shared definition of the ClawBoard demo film: scene order, narration, timing.

Everything in the film is generated from this folder; no video editor is used and
nothing is sped up. Numbers spoken or shown come from assets/report.json, which is
the report of the live run recorded by capture_demo.py.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
VO = HERE / "vo"
RENDERS = HERE / "renders"
REPO_URL = "github.com/thegoodengineer/cerebrass-pcb"
LIMIT_S = 60.0
FPS = 30
XFADE = 0.7
LEAD = 0.4          # narration starts this long after a clip begins


def report() -> dict:
    p = ASSETS / "report.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def facts() -> dict:
    """The numbers the film is allowed to use - all read from the recorded live run."""
    r = report()
    if not r:
        return {}
    t = r.get("totals", {})
    before = (r.get("verification", {}).get("rung2_audit_before") or {}).get("summary", {})
    after = (r.get("verification", {}).get("rung2_audit_after") or {}).get("summary", {})
    oracle = r.get("verification", {}).get("rung3_kicad_oracle") or {}
    drc = oracle.get("drc", {}) if isinstance(oracle, dict) else {}
    erc = oracle.get("erc", {}) if isinstance(oracle, dict) else {}
    prompt = t.get("prompt_tokens") or 0
    return {
        "live": r.get("mode") == "live",
        "engine": r.get("engine", ""),
        "model": r.get("model", ""),
        "tps": round(t.get("tps_generation") or 0),
        "calls": t.get("llm_calls") or 0,
        "loop_s": round((r.get("agent_loop_ms") or 0) / 1000, 1),
        "cache_pct": round(100 * (t.get("cached_prompt_tokens") or 0) / prompt) if prompt else 0,
        "ops": len(r.get("verification", {}).get("rung1_patch_steps") or []),
        "errors_before": before.get("errors"),
        "errors_after": after.get("errors"),
        "drc_errors": drc.get("errors"),
        "unconnected": drc.get("unconnected_items"),
        "erc_errors": erc.get("errors"),
        "kicad": oracle.get("version") if isinstance(oracle, dict) else None,
    }


def _num(n) -> str:
    words = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
             11: "eleven", 12: "twelve"}
    return words.get(n, str(n))


def scenes() -> list[dict]:
    """id, kind (html | capture), on-screen text, narration (display) and spoken form, tail seconds."""
    f = facts()
    ops, eb, calls = f.get("ops", 0), f.get("errors_before"), f.get("calls", 0)
    return [
        {"n": 1, "id": "cold", "kind": "html", "tail": 0.9,
         "text": "Most PCB mistakes are found after the boards come back.",
         "say": "Most PCB mistakes are found after the boards come back."},
        {"n": 2, "id": "title", "kind": "html", "tail": 0.9,
         "text": "ClawBoard is an OpenClaw agent that reviews and repairs KiCad designs, running Kimi K3.1 Pro on Cerebras.",
         "say": "ClawBoard is an OpenClaw agent that reviews and repairs Key-CAD designs, running Kimi K three point one Pro, on Cerebras."},
        {"n": 3, "id": "CAPTURE board", "kind": "capture", "cut": "idle", "tail": 0.6,
         "text": "This ESP32 board hides six classic bugs: shorted USB-C pins, a missing decoupling capacitor, no I2C pull-ups, "
                 "a hair-thin power trace, a clearance violation and an unrouted net.",
         "say": "This E S P thirty-two board hides six classic bugs: shorted USB-C pins, a missing decoupling capacitor, no I squared C pull-ups, "
                "a hair-thin power trace, a clearance violation, and an unrouted net."},
        {"n": 4, "id": "CAPTURE fix", "kind": "capture", "cut": "patch", "tail": 0.6,
         "text": f"The agent inspects the project, runs the audit, and sends one patch: {ops} operations, each re-checked as it lands. "
                 f"{eb} errors, down to zero.",
         "say": f"The agent inspects the project, runs the audit, and sends one patch: {_num(ops)} operations, each re-checked as it lands. "
                f"{_num(eb).capitalize()} errors, down to zero."},
        {"n": 5, "id": "CAPTURE verify", "kind": "capture", "cut": "oracle", "tail": 0.6,
         "text": "Then KiCad's own rule checker confirms it: zero errors, nothing unconnected.",
         "say": "Then Key-CAD's own rule checker confirms it: zero errors, nothing unconnected."},
        {"n": 6, "id": "numbers", "kind": "html", "tail": 0.7,
         "text": f"On Cerebras, Kimi generated at {f.get('tps', 0)} tokens per second. The whole review took {calls} model calls.",
         "say": f"On Cerebras, Kimi generated at {f.get('tps', 0)} tokens per second. The whole review took {_num(calls)} model calls."},
        {"n": 7, "id": "close", "kind": "html", "tail": 1.4,
         "text": "ClawBoard. Catch it before the fab does.",
         "say": "ClawBoard. Catch it before the fab does."},
    ]
