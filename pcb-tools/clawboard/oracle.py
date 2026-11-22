"""Independent oracle: KiCad's own DRC / ERC via ``kicad-cli`` (optional).

Third rung of the verification ladder:
  1. patch post-conditions (exact-geometry clearance check on every new primitive)
  2. ClawBoard's DFM/ERC audit
  3. KiCad's rule checker - a different implementation, so it cannot share our bugs

The rungs are reported separately and never substituted for one another.  KiCad's
DRC is slow (seconds), so it runs once per finished run, not inside the agent loop.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from .project import Project


def find_kicad_cli() -> "str | None":
    env = os.environ.get("KICAD_CLI")
    if env and Path(env).exists():
        return env
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KiCad", Path("/Applications/KiCad/KiCad.app/Contents/MacOS"), Path("/usr/bin")]
    for root in roots:
        if not root.exists():
            continue
        for cand in sorted(root.glob("*/bin/kicad-cli.exe"), reverse=True):
            return str(cand)
        for name in ("kicad-cli", "kicad-cli.exe"):
            if (root / name).exists():
                return str(root / name)
    return None


def _run(cli: str, args: list[str], timeout: int = 120) -> tuple[int, str]:
    p = subprocess.run([cli] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_kicad_oracle(project_dir, refill_zones: bool = True) -> dict:
    """Run ``kicad-cli pcb drc`` + ``sch erc`` on a *copy* of the project and summarise the violations."""
    t0 = time.perf_counter()
    proj = Project(project_dir)
    cli = find_kicad_cli()
    if not cli:
        return {"available": False, "reason": "kicad-cli not found (install KiCad 8+ or set KICAD_CLI)"}
    out: dict = {"available": True, "tool": "kicad-cli"}
    rc, ver = _run(cli, ["version"], 30)
    out["version"] = ver.strip().splitlines()[0] if ver.strip() else "?"
    with tempfile.TemporaryDirectory(prefix="clawboard_oracle_") as tmp:
        work = Path(tmp) / "p"
        shutil.copytree(proj.dir, work, ignore=shutil.ignore_patterns(".clawboard", "*.tmp", "*-backups", "*.lck"))
        if proj.pcb_path is not None:
            rep = Path(tmp) / "drc.json"
            args = ["pcb", "drc", "--format", "json", "--severity-all", "--output", str(rep)]
            if refill_zones:
                args.append("--refill-zones")
            rc, log = _run(cli, args + [str(work / proj.pcb_path.name)])
            if rep.exists():
                data = json.loads(rep.read_text(encoding="utf-8"))
                viol = data.get("violations", [])
                unc = data.get("unconnected_items", [])
                by_type = Counter(f"{v.get('severity', '?')}:{v.get('type', '?')}" for v in viol)
                out["drc"] = {
                    "loaded": True,
                    "errors": sum(1 for v in viol if v.get("severity") == "error"),
                    "warnings": sum(1 for v in viol if v.get("severity") == "warning"),
                    "unconnected_items": len(unc),
                    "by_type": dict(sorted(by_type.items())),
                    "examples": [f"{v.get('type')}: {v.get('description')}" for v in viol if v.get("severity") == "error"][:6],
                    "unconnected_examples": [" <-> ".join(i.get("description", "?") for i in u.get("items", [])) for u in unc][:6],
                }
            else:
                out["drc"] = {"loaded": False, "log": log[-600:]}
        if proj.sch_path is not None:
            rep = Path(tmp) / "erc.json"
            rc, log = _run(cli, ["sch", "erc", "--format", "json", "--severity-all", "--output", str(rep), str(work / proj.sch_path.name)])
            if rep.exists():
                data = json.loads(rep.read_text(encoding="utf-8"))
                viol = [v for sheet in data.get("sheets", []) for v in sheet.get("violations", [])]
                by_type = Counter(f"{v.get('severity', '?')}:{v.get('type', '?')}" for v in viol)
                out["erc"] = {
                    "loaded": True,
                    "errors": sum(1 for v in viol if v.get("severity") == "error"),
                    "warnings": sum(1 for v in viol if v.get("severity") == "warning"),
                    "by_type": dict(sorted(by_type.items())),
                    "examples": [f"{v.get('type')}: {v.get('description')}" for v in viol if v.get("severity") == "error"][:6],
                }
            else:
                out["erc"] = {"loaded": False, "log": log[-600:]}
    out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
    proj.write_state("oracle.json", out)
    return out
