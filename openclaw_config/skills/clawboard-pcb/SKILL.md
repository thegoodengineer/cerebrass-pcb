---
name: clawboard-pcb
description: Audit and repair KiCad PCB projects - inspect schematic and board, run DFM/ERC checks against JLCPCB rules, patch parts/nets/tracks, render a live SVG preview. Use for any request about a .kicad_sch / .kicad_pcb project, PCB design review, DRC, DFM, decoupling, pull-ups, trace width, clearance or routing.
metadata:
  openclaw:
    requires:
      bins: ["python"]
---

# ClawBoard PCB tools

Four tools, served by the `clawboard-pcb` MCP server (they may appear with a
`clawboard-pcb__` prefix):

- `inspect_kicad_project(project_dir)` - compact JSON summary of parts, nets, tracks, vias, airwires
- `run_dfm_and_erc_audit(project_dir, fab_house="jlcpcb")` - the rule check
- `apply_pcb_patch(project_dir, operations)` - edits schematic + board together
- `render_board_svg(project_dir)` - refresh the live preview (automatic after audit/patch)

## How to work

1. Inspect once, audit once.
2. Send **one** `apply_pcb_patch` containing a fix for every issue. You name the part and the
   pad it belongs next to; the tool handles placement, routing and clearance.
3. Read the `audit` object in the patch result. Zero errors means done; otherwise patch again.
4. Sign off in a few lines. Only claim what a tool result confirmed.

The operation reference, fix recipes, JLCPCB limits and datasheet rules are in the workspace
`AGENTS.md`, which is already part of your instructions.

## If the MCP tools are not available

The same tools have a command line. Run them with the exec tool (this needs `exec` in
`tools.allow`; the shipped config allows only the `clawboard-pcb__*` tools):

```bash
python {baseDir}/../../../pcb-tools/cli.py inspect <project_dir>
python {baseDir}/../../../pcb-tools/cli.py audit   <project_dir> --fab jlcpcb
python {baseDir}/../../../pcb-tools/cli.py patch   <project_dir> --ops '[{"op":"route_net","net":"UART_TX"}]'
python {baseDir}/../../../pcb-tools/cli.py render  <project_dir>
```

Each prints one JSON object. Never open the `.kicad_*` files yourself: they are large and the
tools already return everything you need in compact form.
