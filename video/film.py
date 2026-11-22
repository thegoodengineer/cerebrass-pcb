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
        **_speed(r),
    }


MODEL_NAMES = {"moonshotai-kimi-k3.1-pro-trial": "Kimi K3.1 Pro", "nvidia/nemotron-3-ultra-550b-a55b": "Nemotron-3 Ultra"}
COMPARE_START = 0.8     # seconds into the comparison clip at which the real-time race starts


def cfacts() -> dict:
    """Side-by-side facts from two live runs of the same task through the same OpenClaw harness:
    assets/report.json (Cerebras) and assets/report_baseline.json (GPU provider). Recorded separately."""
    files = {"cerebras": ASSETS / "report.json", "baseline": ASSETS / "report_baseline.json"}
    if not all(f.exists() for f in files.values()):
        return {}
    out = {}
    for lane, f in files.items():
        r = json.loads(f.read_text(encoding="utf-8"))
        t = r.get("totals", {})
        after = r.get("verification", {}).get("rung2_audit_after") or {}
        sp = _speed(r)
        out[lane] = {
            "model": MODEL_NAMES.get(r.get("model"), r.get("model")), "host": r.get("endpoint_host"), "live": r.get("mode") == "live",
            "openclaw": "OpenClaw" in (r.get("engine") or ""), "status": r.get("status"),
            "llm_s": round((r.get("llm_ms") or 0) / 1000, 1), "loop_s": round((r.get("agent_loop_ms") or 0) / 1000), "tps": round(t.get("tps_generation") or 0),
            "calls": t.get("llm_calls") or 0, "retries": t.get("busy_retries") or 0, "audit_pass": bool(after.get("pass")),
            "ops": len(r.get("verification", {}).get("rung1_patch_steps") or []),
            "patch_s": sp.get("patch_req_s"), "patch_tokens": sp.get("patch_tokens"), "patch_tps": sp.get("patch_tps"),
        }
    c, b = out["cerebras"], out["baseline"]
    out["patch_ratio"] = round(b["patch_s"] / c["patch_s"], 1) if c.get("patch_s") and b.get("patch_s") else None
    out["tps_ratio"] = round(c["tps"] / b["tps"], 1) if b["tps"] else None
    out["start_s"] = COMPARE_START
    return out


def clip_len(sc: dict, vo_seconds: float) -> float:
    """Length of a scene's clip: narration + lead + tail, or longer if the scene needs the time (real-time race)."""
    return round(max(vo_seconds + LEAD + sc["tail"], sc.get("min_len", 0.0)), 3)


def _speed(r: dict) -> dict:
    """Speed evidence, all provider-reported (time_info) for the recorded run."""
    reqs = r.get("requests", [])
    patch = next((q for q in reqs if any("apply_pcb_patch" in t for t in q.get("tool_calls", []))), None)
    gen_total = sum((q.get("time_info") or {}).get("completion_time", 0) for q in reqs)
    out = {"gen_total_s": round(gen_total, 1), "queue_s": round((r.get("totals", {}).get("provider_queue_ms") or 0) / 1000, 1)}
    if patch:
        ti = patch.get("time_info") or {}
        out.update({
            "patch_n": patch["n"], "patch_tokens": patch["completion_tokens"], "patch_tps": round(patch["tps_generation"]),
            "patch_gen_s": round(ti.get("completion_time", patch["generation_ms"] / 1000), 2),
            "patch_req_s": round(patch["duration_ms"] / 1000, 2),
        })
    return out


def ops() -> list[str]:
    """The patch operations of the recorded run, as logged (for the real-time replay)."""
    steps = report().get("verification", {}).get("rung1_patch_steps") or []
    return [f"{st['op']}  {st['summary']}"[:74] for st in steps]


def _num(n) -> str:
    words = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
             11: "eleven", 12: "twelve"}
    return words.get(n, str(n))


def _under(seconds, spoken: bool = False) -> str:
    """'under a second' / 'under 3 seconds' - only ever rounds UP, so the claim stays true."""
    import math
    if not seconds:
        return "an unmeasured time"
    n = max(1, math.ceil(seconds))
    if n == 1:
        return "under a second"
    return f"under {_num(n) if spoken else n} seconds"


def _compare_scene() -> list[dict]:
    cf = cfacts()
    if not cf or not cf.get("patch_ratio"):
        return []
    c, b = cf["cerebras"], cf["baseline"]
    if not (c["live"] and b["live"] and c["audit_pass"] and b["audit_pass"]):
        return []
    b_s = f"{b['patch_s']:.1f}"
    times = round(cf["tps_ratio"])
    return [{
        "n": 7, "id": "compare", "kind": "html", "tail": 0.8,
        # long enough for the slower lane's real-time replay to finish on screen
        "min_len": COMPARE_START + b["patch_s"] + 1.0,
        "text": f"Side by side: the same agent on NVIDIA's GPU cloud. Writing the patch took Cerebras {_under(c['patch_s'])}. "
                f"NVIDIA's flagship model needed {b_s} seconds. Across the run, Cerebras generated about {times} times faster.",
        "say": f"Side by side: the same agent on NVIDIA's GPU cloud. Writing the patch took Cerebras {_under(c['patch_s'], True)}. "
               f"NVIDIA's flagship model needed {b_s.replace('.', ' point ')} seconds. Across the run, Cerebras generated about {_num(times)} times faster.",
    }]


def scenes() -> list[dict]:
    """id, kind (html | capture), on-screen text, narration (display) and spoken form, tail seconds."""
    f = facts()
    ops, eb, calls = f.get("ops", 0), f.get("errors_before"), f.get("calls", 0)
    return [
        {"n": 1, "id": "cold", "kind": "html", "tail": 0.6,
         "text": "Most PCB mistakes are found after the boards come back.",
         "say": "Most PCB mistakes are found after the boards come back."},
        {"n": 2, "id": "title", "kind": "html", "tail": 0.6,
         "text": "ClawBoard is an OpenClaw agent that reviews and repairs KiCad designs, running Kimi K3.1 Pro on Cerebras.",
         "say": "ClawBoard is an OpenClaw agent that reviews and repairs Key-CAD designs, running Kimi K three point one Pro, on Cerebras."},
        {"n": 3, "id": "CAPTURE board", "kind": "capture", "cut": "idle", "tail": 0.5,
         "text": "This ESP32 board hides six classic bugs, from shorted USB-C pins to an unrouted net.",
         "say": "This E S P thirty-two board hides six classic bugs, from shorted USB-C pins to an unrouted net."},
        {"n": 4, "id": "CAPTURE fix", "kind": "capture", "cut": "patch", "tail": 0.6,
         "text": f"The agent inspects the project, runs the audit, and sends one patch: {ops} operations, each re-checked as it lands. "
                 f"{eb} errors, down to zero.",
         "say": f"The agent inspects the project, runs the audit, and sends one patch: {_num(ops)} operations, each re-checked as it lands. "
                f"{_num(eb).capitalize()} errors, down to zero."},
        {"n": 5, "id": "CAPTURE verify", "kind": "capture", "cut": "oracle", "tail": 0.6,
         "text": "Then KiCad's own rule checker confirms it: zero errors, nothing unconnected.",
         "say": "Then Key-CAD's own rule checker confirms it: zero errors, nothing unconnected."},
        {"n": 6, "id": "numbers", "kind": "html", "tail": 0.7,
         "text": f"Speed is what makes this feel live. On Cerebras, Kimi wrote the whole {ops}-operation patch in {_under(f.get('patch_req_s'))}, "
                 f"at {f.get('patch_tps', 0):,} tokens per second.",
         "say": f"Speed is what makes this feel live. On Cerebras, Kimi wrote the whole {_num(ops)}-operation patch in {_under(f.get('patch_req_s'), True)}, "
                f"at {f.get('patch_tps', 0)} tokens per second."},
        *_compare_scene(),
        {"n": 8, "id": "close", "kind": "html", "tail": 1.1,
         "text": "ClawBoard. Catch it before the fab does.",
         "say": "ClawBoard. Catch it before the fab does."},
    ]
