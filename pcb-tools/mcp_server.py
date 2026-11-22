#!/usr/bin/env python3
"""ClawBoard PCB tools as a Model Context Protocol server (stdio, newline-delimited JSON-RPC 2.0).

Zero dependencies.  Register with OpenClaw:
    openclaw mcp add clawboard-pcb --command python --arg /abs/path/pcb-tools/mcp_server.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clawboard import tools  # noqa: E402

SERVER_INFO = {"name": "clawboard-pcb", "version": "1.0.0"}
DEFAULT_PROTOCOL = "2025-06-18"


def _send(msg: dict) -> None:
    sys.stdout.buffer.write((json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _handle(req: dict) -> "dict | None":
    method = req.get("method", "")
    rid = req.get("id")
    params = req.get("params") or {}
    if rid is None:  # notification - no reply
        return None
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "KiCad PCB tools: inspect -> run_dfm_and_erc_audit -> apply_pcb_patch (all fixes in one call) -> audit again.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools.tool_specs()}
    elif method == "tools/call":
        res = tools.call_tool(params.get("name", ""), params.get("arguments") or {})
        result = {
            "content": [{"type": "text", "text": json.dumps(res, separators=(",", ":"), ensure_ascii=False)}],
            "isError": "error" in res,
        }
    elif method in ("resources/list", "prompts/list"):
        result = {method.split("/")[0]: []}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> int:
    # stdout carries the protocol: nothing else may print to it
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        for r in req if isinstance(req, list) else [req]:
            try:
                resp = _handle(r)
            except Exception as e:  # pragma: no cover - defensive
                resp = {"jsonrpc": "2.0", "id": r.get("id"), "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
            if resp is not None:
                _send(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
