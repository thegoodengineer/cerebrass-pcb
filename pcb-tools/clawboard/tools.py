"""The four tools exposed to the agent (OpenClaw skill / MCP server / CLI all call these)."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path

from .audit import run_audit
from .patch import PatchError, apply_patch
from .pcb import clearance_violations, ratsnest
from .project import Project, replace_file
from .render import render_pcb_svg, render_sch_svg

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "tools.schema.json"


def tool_specs() -> list[dict]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["tools"]


def _atomic_write(path: Path, text: str, atomic: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not atomic:
        for attempt in range(40):
            try:
                path.write_text(text, encoding="utf-8", newline="\n")
                return
            except PermissionError:  # Windows: the dashboard may be reading the file
                time.sleep(0.01 + 0.005 * attempt)
        return
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    replace_file(tmp, path)


def _svg_targets(proj: Project) -> tuple["Path | None", "Path | None"]:
    cfg = proj.read_state("config.json", {}) or {}
    pcb = os.environ.get("CLAWBOARD_SVG_OUT") or cfg.get("svg_out")
    sch = os.environ.get("CLAWBOARD_SCH_SVG_OUT") or cfg.get("sch_svg_out")
    public = REPO_ROOT / "web" / "public"
    if not pcb and public.is_dir():
        pcb = public / "board_preview.svg"
    if not sch and public.is_dir():
        sch = public / "schematic_preview.svg"
    return (Path(pcb) if pcb else None, Path(sch) if sch else None)


def _render(proj: Project, sch: bool = True, publish: bool = True) -> dict:
    """Render the previews.  ``publish=False`` (used after each patch step) only refreshes the
    project's own ``.clawboard`` copy, which is what the live dashboard streams."""
    audit = proj.read_state("audit.json", None)
    changes = proj.read_state("changes.json", None)
    out: dict = {}
    pcb_target, sch_target = _svg_targets(proj)
    if proj.board is not None:
        svg = render_pcb_svg(proj.board, audit, changes, title=f"{proj.name} - PCB")
        _atomic_write(proj.state_dir / "board_preview.svg", svg, atomic=False)
        if pcb_target and publish:
            _atomic_write(pcb_target, svg)
        out["board_svg"] = str(pcb_target or proj.state_dir / "board_preview.svg")
        out["bytes"] = len(svg)
    if sch and proj.sch is not None:
        svg = render_sch_svg(proj.sch, audit, changes, title=f"{proj.name} - schematic")
        _atomic_write(proj.state_dir / "schematic_preview.svg", svg, atomic=False)
        if sch_target and publish:
            _atomic_write(sch_target, svg)
        out["schematic_svg"] = str(sch_target or proj.state_dir / "schematic_preview.svg")
    return out


def _logged(name: str):
    def deco(fn):
        def wrapper(project_dir, *args, **kwargs):
            t0 = time.perf_counter()
            proj = None
            try:
                proj = Project(project_dir)
                proj.log_event({"tool": name, "phase": "start"})
            except Exception:
                pass
            try:
                res = fn(project_dir, *args, **kwargs)
                ok = True
            except (PatchError, FileNotFoundError, ValueError, KeyError) as e:
                res = {"error": str(e)}
                ok = False
            ms = round((time.perf_counter() - t0) * 1000)
            if isinstance(res, dict):
                res["tool_ms"] = ms
            if proj is not None:
                proj.log_event({"tool": name, "phase": "end", "ok": ok, "ms": ms, "brief": _brief(name, res)})
            return res
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def _brief(name: str, res: dict) -> str:
    if "error" in res:
        return f"error: {res['error']}"
    if name == "inspect_kicad_project":
        b = res.get("board", {})
        return f"{b.get('footprints', 0)} footprints, {len(res.get('nets', {}))} nets, {len(res.get('unrouted', []))} unrouted"
    if name == "run_dfm_and_erc_audit":
        s = res["summary"]
        return ("PASS" if res["pass"] else "FAIL") + f": {s['errors']} errors, {s['warnings']} warnings"
    if name == "apply_pcb_patch":
        return f"{res['applied']} applied, {res['failed']} failed -> {res['audit']['errors']} errors left"
    return "rendered"


@_logged("inspect_kicad_project")
def inspect_kicad_project(project_dir: str) -> dict:
    """Compact JSON summary of the schematic + board."""
    proj = Project(project_dir)
    out: dict = {"project": proj.name, "units": "mm, absolute KiCad coordinates",
                 "files": {"pcb": proj.pcb_path.name if proj.pcb_path else None, "sch": proj.sch_path.name if proj.sch_path else None}}
    b = proj.board
    pin_names: dict[tuple[str, str], str] = {}
    if proj.sch is not None:
        nl = proj.sch.netlist()
        for s in proj.sch.symbols:
            for p in s.pins:
                pin_names[(s.ref, p.number)] = p.name
        out["schematic"] = {
            "symbols": sum(1 for s in proj.sch.symbols if not s.virtual),
            "nets": len(nl["nets"]),
            "no_connect_pins": len(nl["nc"]),
            "floating_pins": [f"{r}.{k}" for r, k, _ in nl["floating"]],
        }
    if b is not None:
        x1, y1, x2, y2 = b.outline
        widths = Counter(f"{t.width:g}" for t in b.tracks)
        vias = Counter(f"{v.size:g}/{v.drill:g}" for v in b.vias)
        drills = Counter(f"{p.drill:g}" for f in b.footprints for p in f.pads if p.drill)
        tight = clearance_violations(b, 0.2)
        out["board"] = {
            "outline_mm": [x1, y1, x2, y2],
            "size_mm": list(b.size()),
            "copper_layers": ["F.Cu", "B.Cu"],
            "footprints": len(b.footprints),
            "tracks": len(b.tracks),
            "vias": len(b.vias),
            "zones": sorted({f"{z.net} pour on {L}" for z in b.zones for L in z.layers}),
            "track_widths_mm": dict(sorted(widths.items(), key=lambda kv: float(kv[0]))),
            "via_pad/drill_mm": dict(vias),
            "pad_drills_mm": dict(drills),
            "tightest_clearance_mm": min((v["gap_mm"] for v in tight), default=">=0.2"),
        }
        out["components"] = [
            f"{f.ref} {f.value} [{f.lib.split(':')[-1]}] @({f.x:g},{f.y:g}) rot{f.rot:g}"
            for f in sorted(b.footprints, key=lambda f: (f.ref.rstrip('0123456789'), int(''.join(ch for ch in f.ref if ch.isdigit()) or 0)))
        ]
        rn = ratsnest(b)
        nets: dict[str, dict] = {}
        for f in b.footprints:
            for p in f.pads:
                if not p.net:
                    continue
                n = nets.setdefault(p.net, {"pins": [], "w": set(), "len": 0.0})
                nm = pin_names.get((f.ref, p.num), "")
                label = f"{f.ref}.{p.num}" + (f"({nm})" if nm and not nm.startswith("Pin_") and nm != "~" else "")
                if label not in n["pins"]:
                    n["pins"].append(label)
        for t in b.tracks:
            if t.net in nets:
                nets[t.net]["w"].add(t.width)
                nets[t.net]["len"] += t.length
        out["nets"] = {}
        for name in sorted(nets):
            n = nets[name]
            entry = {"pins": " ".join(n["pins"])}
            if n["w"]:
                entry["track_mm"] = sorted(n["w"]) if len(n["w"]) > 1 else next(iter(n["w"]))
                entry["length_mm"] = round(n["len"], 1)
            if b.zone_layers(name):
                entry["pour"] = True
            if name in rn:
                entry["unrouted"] = len(rn[name]["missing"])
            out["nets"][name] = entry
        out["unrouted"] = [f"{net}: {m['from']} -> {m['to']} ({m['dist_mm']} mm)" for net, info in sorted(rn.items()) for m in info["missing"]]
    return out


@_logged("run_dfm_and_erc_audit")
def run_dfm_and_erc_audit(project_dir: str, fab_house: str = "jlcpcb") -> dict:
    """ERC + DFM checks.  Also refreshes the SVG previews so error markers stay current."""
    proj = Project(project_dir)
    report = run_audit(proj, fab_house or "jlcpcb")
    _render(proj)
    return report


@_logged("apply_pcb_patch")
def apply_pcb_patch(project_dir: str, operations) -> dict:
    """Apply edit operations, then re-audit quietly so the preview's error markers are up to date."""
    if isinstance(operations, str):
        operations = json.loads(operations)
    done: list = []
    res = apply_patch(project_dir, operations,
                      on_progress=lambda proj, sch_changed: _render(proj, sch=sch_changed, publish=False),
                      on_done=done.append)
    _render(done[0] if done else Project(project_dir))
    return res


@_logged("render_board_svg")
def render_board_svg(project_dir: str) -> dict:
    """Write board_preview.svg / schematic_preview.svg."""
    return _render(Project(project_dir))


TOOLS = {
    "inspect_kicad_project": inspect_kicad_project,
    "run_dfm_and_erc_audit": run_dfm_and_erc_audit,
    "apply_pcb_patch": apply_pcb_patch,
    "render_board_svg": render_board_svg,
}


def call_tool(name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool '{name}'. Available: {', '.join(TOOLS)}"}
    args = dict(args or {})
    if "project_dir" not in args:
        default = os.environ.get("CLAWBOARD_PROJECT_DIR")
        if not default:
            return {"error": "project_dir is required"}
        args["project_dir"] = default
    allowed = {"inspect_kicad_project": {"project_dir"}, "run_dfm_and_erc_audit": {"project_dir", "fab_house"},
               "apply_pcb_patch": {"project_dir", "operations"}, "render_board_svg": {"project_dir"}}[name]
    if name == "apply_pcb_patch" and "operations" not in args:
        return {"error": "operations is required (a list of {op: ...} objects)"}
    try:
        return TOOLS[name](**{k: v for k, v in args.items() if k in allowed})
    except Exception as e:  # never crash the agent loop on a tool bug
        return {"error": f"{type(e).__name__}: {e}"}
