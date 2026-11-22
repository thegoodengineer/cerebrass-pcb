#!/usr/bin/env bash
# ClawBoard setup (macOS / Linux / Git Bash).  Windows PowerShell users: run ./setup.ps1
#
#   ./setup.sh                    check tools, install dashboard deps, build the sample board, write OpenClaw config
#   ./setup.sh --install-openclaw also run `npm install -g openclaw@latest` (needs Node >= 24.16)
set -euo pipefail
cd "$(dirname "$0")"
HOME_DIR="$(pwd)"
INSTALL_OPENCLAW=0
[[ "${1:-}" == "--install-openclaw" ]] && INSTALL_OPENCLAW=1

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m xx\033[0m %s\n' "$*"; exit 1; }

# ---- python ------------------------------------------------------------------------------
PY="${CLAWBOARD_PYTHON:-}"
if [[ -z "$PY" ]]; then
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
  done
fi
[[ -n "$PY" ]] || die "Python 3.10+ not found. Install it, or set CLAWBOARD_PYTHON."
say "python: $("$PY" --version 2>&1) ($PY)  - the PCB tools use only the standard library"

# ---- node --------------------------------------------------------------------------------
command -v node >/dev/null 2>&1 || die "Node.js not found (18+ for the dashboard, 24.16+ for OpenClaw)."
NODE_VER="$(node -p 'process.versions.node')"
NODE_MAJOR="${NODE_VER%%.*}"
say "node: v$NODE_VER"
(( NODE_MAJOR >= 18 )) || die "Node 18 or newer is required."

# ---- openclaw ----------------------------------------------------------------------------
if command -v openclaw >/dev/null 2>&1; then
  say "openclaw: $(openclaw --version 2>/dev/null | head -n1)"
elif (( INSTALL_OPENCLAW )); then
  (( NODE_MAJOR >= 24 )) || die "OpenClaw needs Node >= 24.16 (found v$NODE_VER). Upgrade Node first."
  say "installing openclaw globally"
  npm install -g openclaw@latest
else
  warn "openclaw CLI not found. The dashboard falls back to its built-in agent loop."
  (( NODE_MAJOR >= 24 )) && warn "install it with:  ./setup.sh --install-openclaw" \
                         || warn "OpenClaw needs Node >= 24.16 (found v$NODE_VER): upgrade Node, then ./setup.sh --install-openclaw"
fi

# ---- kicad (optional oracle) -------------------------------------------------------------
if command -v kicad-cli >/dev/null 2>&1; then say "kicad-cli: $(kicad-cli version 2>/dev/null | head -n1) (KiCad oracle enabled)"
else warn "kicad-cli not on PATH: the independent KiCad DRC/ERC rung will be skipped (set KICAD_CLI to enable)."; fi

# ---- dashboard ---------------------------------------------------------------------------
say "installing dashboard dependencies"
(cd web && npm install --no-audit --no-fund)

# ---- sample board ------------------------------------------------------------------------
if [[ ! -f sample_board/sample_board.kicad_pcb ]]; then
  say "generating sample_board (takes ~20 s)"
  "$PY" pcb-tools/make_sample_board.py
fi
say "self-test"
"$PY" -m unittest discover -s pcb-tools/tests -q

# ---- env + openclaw config ---------------------------------------------------------------
[[ -f .env ]] || { cp .env.example .env; say "created .env - add your CEREBRAS_API_KEY"; }
PY_ABS="$(command -v "$PY")"
"$PY" - "$HOME_DIR" "$PY_ABS" <<'PYEOF'
import pathlib, sys
home, py = pathlib.Path(sys.argv[1]).as_posix(), pathlib.Path(sys.argv[2]).as_posix()
src = pathlib.Path("openclaw_config/openclaw.json").read_text(encoding="utf-8")
out = src.replace("${CLAWBOARD_HOME}", home).replace("${CLAWBOARD_PYTHON}", py)
pathlib.Path("openclaw_config/openclaw.resolved.json").write_text(out, encoding="utf-8")
PYEOF
say "wrote openclaw_config/openclaw.resolved.json"

cat <<EOF

Next steps
  1. Put your key in .env            CEREBRAS_API_KEY=...   (and BASELINE_* for the benchmark)
  2. Start the dashboard             npm run dev            -> http://localhost:3000
  3. Use the agent from a terminal   export OPENCLAW_CONFIG_PATH="$HOME_DIR/openclaw_config/openclaw.resolved.json"
                                     export CEREBRAS_API_KEY=...
                                     openclaw config validate
                                     openclaw mcp doctor clawboard-pcb --probe
                                     openclaw agent exec --config "$HOME_DIR/openclaw_config/openclaw.resolved.json" --cwd "$HOME_DIR/openclaw_config/workspace" --json "Audit and repair the KiCad project in project_dir: $HOME_DIR/sample_board"
EOF
