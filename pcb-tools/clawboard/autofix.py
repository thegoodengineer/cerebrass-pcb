"""Deterministic fix planner: turns audit ``fix`` hints into ``apply_pcb_patch`` operations.

This is NOT what the LLM agent uses - the agent reasons from the audit report itself.
It exists so the test-suite has an oracle and so the dashboard's clearly-labelled
SIMULATED mode can run without an API key.
"""
from __future__ import annotations

import re

_ORDER = ["set_pin_net", "add_component", "move_footprint", "set_track_width", "move_via", "reroute_net", "route_net", "stitch_pour"]


def _parse(hint: str) -> list[dict]:
    ops: list[dict] = []
    for part in re.split(r",\s*then\s+", hint or ""):
        part = part.strip()
        m = re.match(r"set_pin_net (\S+)\.(\S+) -> (\S+)", part)
        if m:
            ops.append({"op": "set_pin_net", "ref": m.group(1), "pin": m.group(2), "net": m.group(3)})
            continue
        m = re.match(r"add_component (resistor|capacitor) (\S+) \((\S+) to (\S+)\) near (\S+)", part)
        if m:
            ops.append({"op": "add_component", "kind": m.group(1), "value": m.group(2),
                        "pins": {"1": m.group(3), "2": m.group(4)}, "near": m.group(5)})
            continue
        m = re.match(r"move_footprint (\S+) near (\S+)", part)
        if m:
            ops.append({"op": "move_footprint", "ref": m.group(1), "near": m.group(2)})
            continue
        m = re.match(r"move_footprint (\S+)", part)
        if m:
            ops.append({"op": "move_footprint", "ref": m.group(1)})
            continue
        m = re.match(r"set_track_width (\S+) ([\d.]+)", part)
        if m:
            ops.append({"op": "set_track_width", "net": m.group(1), "width": float(m.group(2))})
            continue
        m = re.match(r"move_via at \[([-\d.]+), ([-\d.]+)\]", part)
        if m:
            ops.append({"op": "move_via", "at": [float(m.group(1)), float(m.group(2))]})
            continue
        m = re.match(r"stitch_pour (\S+)", part)
        if m:
            ops.append({"op": "stitch_pour", "net": m.group(1)})
            continue
        m = re.match(r"(route_net|reroute_net) (\S+)", part)
        if m:
            ops.append({"op": m.group(1), "net": m.group(2)})
    return ops


def plan_fixes(audit: dict) -> list[dict]:
    ops: list[dict] = []
    for it in audit.get("issues", []):
        if it["severity"] == "info":
            continue
        for op in _parse(it.get("fix", "")):
            if op["op"] == "move_footprint":
                prev = next((o for o in ops if o["op"] == "move_footprint" and o["ref"] == op["ref"]), None)
                if prev is not None:
                    if "near" in op and "near" not in prev:
                        prev["near"] = op["near"]
                    continue
            if op not in ops:
                ops.append(op)
    ops.sort(key=lambda o: _ORDER.index(o["op"]) if o["op"] in _ORDER else len(_ORDER))
    return ops
