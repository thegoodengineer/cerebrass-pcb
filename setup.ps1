# ClawBoard setup for Windows PowerShell.
#
#   ./setup.ps1                     check tools, install dashboard deps, build the sample board, write OpenClaw config
#   ./setup.ps1 -InstallOpenClaw    also run `npm install -g openclaw@latest` (needs Node >= 24.16)
param([switch]$InstallOpenClaw)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$HomeDir = (Get-Location).Path

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host " !! $m" -ForegroundColor Yellow }

# ---- python ------------------------------------------------------------------------------
$py = $env:CLAWBOARD_PYTHON
if (-not $py) { foreach ($c in 'python', 'python3', 'py') { if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break } } }
if (-not $py) { throw 'Python 3.10+ not found. Install it, or set CLAWBOARD_PYTHON.' }
& $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 or newer is required.' }
Say "python: $(& $py --version)  - the PCB tools use only the standard library"

# ---- node --------------------------------------------------------------------------------
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js not found (18+ for the dashboard, 24.16+ for OpenClaw).' }
$nodeVer = (node -p 'process.versions.node')
$nodeMajor = [int]($nodeVer.Split('.')[0])
Say "node: v$nodeVer"
if ($nodeMajor -lt 18) { throw 'Node 18 or newer is required.' }

# ---- openclaw ----------------------------------------------------------------------------
if (Get-Command openclaw -ErrorAction SilentlyContinue) {
  Say "openclaw: $(openclaw --version | Select-Object -First 1)"
} elseif ($InstallOpenClaw) {
  if ($nodeMajor -lt 24) { throw "OpenClaw needs Node >= 24.16 (found v$nodeVer). Upgrade Node first." }
  Say 'installing openclaw globally'
  npm install -g openclaw@latest
} else {
  Warn 'openclaw CLI not found. The dashboard falls back to its built-in agent loop.'
  if ($nodeMajor -ge 24) { Warn 'install it with:  ./setup.ps1 -InstallOpenClaw' }
  else { Warn "OpenClaw needs Node >= 24.16 (found v$nodeVer): upgrade Node, then ./setup.ps1 -InstallOpenClaw" }
}

# ---- kicad (optional oracle) -------------------------------------------------------------
$kicad = Get-Command kicad-cli -ErrorAction SilentlyContinue
if (-not $kicad) { $kicad = Get-ChildItem "$env:ProgramFiles\KiCad\*\bin\kicad-cli.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1 }
if ($kicad) { Say 'kicad-cli found (KiCad oracle enabled)' } else { Warn 'kicad-cli not found: the independent KiCad DRC/ERC rung will be skipped.' }

# ---- dashboard ---------------------------------------------------------------------------
Say 'installing dashboard dependencies'
Push-Location web; npm install --no-audit --no-fund; Pop-Location

# ---- sample board ------------------------------------------------------------------------
if (-not (Test-Path sample_board/sample_board.kicad_pcb)) {
  Say 'generating sample_board (takes ~20 s)'
  & $py pcb-tools/make_sample_board.py
}
Say 'self-test'
& $py -m unittest discover -s pcb-tools/tests -q

# ---- env + openclaw config ---------------------------------------------------------------
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Say 'created .env - add your CEREBRAS_API_KEY' }
$pyAbs = (Get-Command $py).Source -replace '\\', '/'
$homeFwd = $HomeDir -replace '\\', '/'
$cfg = (Get-Content openclaw_config/openclaw.json -Raw).Replace('${CLAWBOARD_HOME}', $homeFwd).Replace('${CLAWBOARD_PYTHON}', $pyAbs)
Set-Content -Path openclaw_config/openclaw.resolved.json -Value $cfg -Encoding utf8
Say 'wrote openclaw_config/openclaw.resolved.json'

Write-Host @"

Next steps
  1. Put your key in .env            CEREBRAS_API_KEY=...   (and BASELINE_* for the benchmark)
  2. Start the dashboard             npm run dev            -> http://localhost:3000
  3. Use the agent from a terminal   `$env:OPENCLAW_CONFIG_PATH = "$homeFwd/openclaw_config/openclaw.resolved.json"
                                     `$env:CEREBRAS_API_KEY = "..."
                                     openclaw config validate
                                     openclaw mcp doctor clawboard-pcb --probe
                                     openclaw agent exec --config "$homeFwd/openclaw_config/openclaw.resolved.json" --cwd "$homeFwd/openclaw_config/workspace" --json "Audit and repair the KiCad project in project_dir: $homeFwd/sample_board"
"@
