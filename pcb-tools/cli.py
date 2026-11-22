#!/usr/bin/env python3
"""ClawBoard PCB tools - command line front end.

  python cli.py inspect <project_dir>
  python cli.py audit   <project_dir> [--fab jlcpcb]
  python cli.py patch   <project_dir> --ops '<json list>' | --ops-file ops.json | (stdin)
  python cli.py render  <project_dir>
  python cli.py plan    <project_dir>      # deterministic fix plan (used by the simulator + tests)
  python cli.py oracle  <project_dir>      # independent check with KiCad's own DRC/ERC (needs kicad-cli)
  python cli.py call    <tool_name>        # JSON arguments on stdin, JSON result on stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clawboard import tools  # noqa: E402


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="clawboard", description="Headless KiCad inspect / audit / patch / render tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("inspect", "audit", "patch", "render", "plan", "oracle"):
        sp = sub.add_parser(name)
        sp.add_argument("project_dir")
        sp.add_argument("--pretty", action="store_true", help="indent the JSON output")
        if name == "audit":
            sp.add_argument("--fab", default="jlcpcb")
        if name == "patch":
            sp.add_argument("--ops", help="JSON list of operations")
            sp.add_argument("--ops-file", help="file containing the JSON list ('-' = stdin)")
    sp = sub.add_parser("call")
    sp.add_argument("tool")
    sp.add_argument("--pretty", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "inspect":
        res = tools.inspect_kicad_project(a.project_dir)
    elif a.cmd == "audit":
        res = tools.run_dfm_and_erc_audit(a.project_dir, a.fab)
    elif a.cmd == "render":
        res = tools.render_board_svg(a.project_dir)
    elif a.cmd == "oracle":
        from clawboard.oracle import run_kicad_oracle
        res = run_kicad_oracle(a.project_dir)
    elif a.cmd == "plan":
        from clawboard.audit import run_audit
        from clawboard.autofix import plan_fixes
        from clawboard.project import Project
        proj = Project(a.project_dir)
        res = {"operations": plan_fixes(proj.read_state("audit.json", None) or run_audit(proj))}
    elif a.cmd == "patch":
        if a.ops:
            raw = a.ops
        elif a.ops_file and a.ops_file != "-":
            raw = Path(a.ops_file).read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()
        ops = json.loads(raw)
        if isinstance(ops, dict) and "operations" in ops:
            ops = ops["operations"]
        res = tools.apply_pcb_patch(a.project_dir, ops)
    else:
        raw = sys.stdin.read().strip()
        res = tools.call_tool(a.tool, json.loads(raw) if raw else {})
    if a.pretty:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(res, separators=(",", ":"), ensure_ascii=False))
    return 1 if isinstance(res, dict) and "error" in res else 0


if __name__ == "__main__":
    raise SystemExit(main())
