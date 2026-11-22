"""High-contrast SVG renderers for the board (top + bottom copper) and the schematic.

The SVGs are self-contained: CSS animations for freshly patched items (green/cyan glow,
draw-on effect) and for DFM error markers (pulsing red) live inside the file, so they
animate in the dashboard *and* when opened directly in a browser.
"""
from __future__ import annotations

import math
from html import escape

from .geom import rot
from .pcb import Board, ratsnest
from .sch import Schematic, _xform
from .sexpr import find, find_all, num, val

PCB_CSS = """
.bg{fill:#06090d}
.substrate{fill:#0b1711;stroke:none}
.griddot{fill:#1d2b25}
.zone-f{fill:#e5484d;opacity:.13}
.zone-b{fill:#3e8bff;opacity:.11}
.halo{stroke:#0b1711;fill:#0b1711;stroke-linecap:round;stroke-linejoin:round}
.trk{fill:none;stroke-linecap:round}
.fcu .trk{stroke:#f0555a}
.bcu .trk{stroke:#4c93ff}
.pad{fill:#f2c14e}
.pad.th{fill:#f2c14e}
.pad.np{fill:#06090d;stroke:#59636e;stroke-width:.12}
.hole{fill:#06090d}
.via{fill:#c9d1d9}
.silk{stroke:#e6edf3;fill:none;stroke-linecap:round;opacity:.82}
.fab{stroke:#51606e;fill:none;opacity:.55}
.ref{fill:#e6edf3;font:600 1.05px 'JetBrains Mono','SFMono-Regular',Consolas,monospace;text-anchor:middle;dominant-baseline:central;opacity:.9;paint-order:stroke;stroke:#06090d;stroke-width:.22px}
.ref.small{font-size:.8px}
.edge{fill:none;stroke:#ffd33d;stroke-width:.18;stroke-linejoin:round}
.rats{stroke:#8be9fd;stroke-width:.12;stroke-dasharray:.5 .35;opacity:.95}
.rats-dot{fill:#8be9fd}
[data-net]{cursor:pointer}
.chg{fill:none;stroke:#2dffb3;stroke-linecap:round;animation:chgpulse 2.4s ease-in-out infinite}
.chg.fill{fill:#2dffb3;stroke:none;animation:chgfill 2.4s ease-in-out infinite}
.chg.box{stroke-width:.16;stroke-dasharray:.6 .3;fill:rgba(45,255,179,.07)}
.chg.fresh{animation:drawon .9s ease-out both, chgpulse 2.4s ease-in-out .9s infinite}
.chg.box.fresh{animation:popin .55s cubic-bezier(.2,1.6,.4,1) both, chgpulse 2.4s ease-in-out .6s infinite}
.chg.fill.fresh{animation:popin .55s cubic-bezier(.2,1.6,.4,1) both, chgfill 2.4s ease-in-out .6s infinite}
.err .ring{fill:none;stroke:#ff3b5c;stroke-width:.2;transform-box:fill-box;transform-origin:center;animation:errpulse 1.1s ease-out infinite}
.err .core{fill:#ff3b5c}
.err .halo2{fill:#ff3b5c;opacity:.28}
.err .tag{fill:#ff8fa3;font:700 1.15px 'JetBrains Mono',Consolas,monospace;paint-order:stroke;stroke:#06090d;stroke-width:.3px}
.err.warn .ring{stroke:#ffb020}.err.warn .core{fill:#ffb020}.err.warn .tag{fill:#ffd27a}
.errtrk{fill:none;stroke:#ff3b5c;stroke-linecap:round;animation:errblink 1s ease-in-out infinite}
@keyframes chgpulse{0%,100%{stroke:#2dffb3;opacity:1}50%{stroke:#34d5ff;opacity:.78}}
@keyframes chgfill{0%,100%{fill:#2dffb3}50%{fill:#34d5ff}}
@keyframes drawon{from{stroke-dashoffset:var(--len,60);stroke-dasharray:var(--len,60)}to{stroke-dashoffset:0;stroke-dasharray:var(--len,60)}}
@keyframes popin{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}
@keyframes errpulse{0%{transform:scale(.45);opacity:1}100%{transform:scale(1.9);opacity:0}}
@keyframes errblink{0%,100%{opacity:1}50%{opacity:.35}}
svg[data-view=top] #layer-B\\.Cu{opacity:.7}
svg[data-view=bottom] #layer-F\\.Cu,svg[data-view=bottom] #silk,svg[data-view=bottom] .pad.smd{opacity:.14}
svg[data-view=bottom] #world{transform:scale(-1,1);transform-origin:center;transform-box:fill-box}
.chg.box,.chg.fill{transform-box:fill-box;transform-origin:center}
"""

def _defs(x: float, y: float, w: float, h: float) -> str:
    region = f'filterUnits="userSpaceOnUse" x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}"'
    return (
        "<defs>"
        f'<filter id="glow" {region}><feGaussianBlur stdDeviation=".3" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        f'<filter id="rglow" {region}><feGaussianBlur stdDeviation=".35" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        '<pattern id="grid" width="2" height="2" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".05" class="griddot"/></pattern>'
        "</defs>"
    )


def _f(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pad_svg(p, cls: str, extra: str = "", grow: float = 0.0) -> str:
    w, h = p.w + 2 * grow, p.h + 2 * grow
    if p.shape_name == "circle":
        return f'<circle cx="{_f(p.x)}" cy="{_f(p.y)}" r="{_f(w / 2)}" class="{cls}"{extra}/>'
    rx = min(w, h) / 2 if p.shape_name == "oval" else (min(w, h) * 0.25 if p.shape_name == "roundrect" else 0)
    rx += grow if p.shape_name not in ("oval",) and grow else 0
    tr = f' transform="rotate({_f(-p.angle)} {_f(p.x)} {_f(p.y)})"' if p.angle % 180 else ""
    return (f'<rect x="{_f(p.x - w / 2)}" y="{_f(p.y - h / 2)}" width="{_f(w)}" height="{_f(h)}" rx="{_f(rx)}" class="{cls}"{tr}{extra}/>')


def _fp_graphics(f, layer_key: str, cls: str, min_w: float = 0.1) -> list[str]:
    out = []
    for g in f.node:
        if not isinstance(g, list) or not g or g[0] not in ("fp_line", "fp_rect", "fp_circle"):
            continue
        if layer_key not in str(val(g, "layer", "")):
            continue
        st = find(g, "stroke")
        w = max(num(val(st, "width", 0.12)) if st else 0.12, min_w)

        def T(c):
            rx, ry = rot(num(c[1]), num(c[2]), f.rot)
            return f.x + rx, f.y + ry

        if g[0] == "fp_line":
            (x1, y1), (x2, y2) = T(find(g, "start")), T(find(g, "end"))
            out.append(f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}" stroke-width="{_f(w)}" class="{cls}"/>')
        elif g[0] == "fp_rect":
            s, e = find(g, "start"), find(g, "end")
            corners = [(num(s[1]), num(s[2])), (num(e[1]), num(s[2])), (num(e[1]), num(e[2])), (num(s[1]), num(e[2]))]
            pts = " ".join(f"{_f(f.x + rot(cx, cy, f.rot)[0])},{_f(f.y + rot(cx, cy, f.rot)[1])}" for cx, cy in corners)
            out.append(f'<polygon points="{pts}" stroke-width="{_f(w)}" class="{cls}"/>')
        else:
            c, e = find(g, "center"), find(g, "end")
            r = math.hypot(num(e[1]) - num(c[1]), num(e[2]) - num(c[2]))
            cx, cy = T(c)
            out.append(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(r)}" stroke-width="{_f(w)}" class="{cls}"/>')
    return out


def render_pcb_svg(board: Board, audit: "dict | None" = None, changes: "dict | None" = None, title: str = "") -> str:
    x1, y1, x2, y2 = board.outline
    m = 2.5
    vb = (x1 - m, y1 - m, (x2 - x1) + 2 * m, (y2 - y1) + 2 * m)
    items = (changes or {}).get("items", {})
    seq = (changes or {}).get("seq", 0)

    def chg(uid):
        c = items.get(uid)
        if not c:
            return None
        return "chg fresh" if c[1] == seq else "chg"

    o = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_f(vb[0])} {_f(vb[1])} {_f(vb[2])} {_f(vb[3])}" '
        f'data-view="top" data-board-w="{_f(x2 - x1)}" data-board-h="{_f(y2 - y1)}" data-seq="{seq}" class="clawboard-pcb">',
        f"<title>{escape(title or 'ClawBoard PCB preview')}</title>", _defs(*vb), f"<style>{PCB_CSS}</style>",
        f'<rect class="bg" x="{_f(vb[0])}" y="{_f(vb[1])}" width="{_f(vb[2])}" height="{_f(vb[3])}"/>',
        '<g id="world">',
        f'<rect class="substrate" x="{_f(x1)}" y="{_f(y1)}" width="{_f(x2 - x1)}" height="{_f(y2 - y1)}" rx="1.2"/>',
        f'<rect fill="url(#grid)" x="{_f(x1)}" y="{_f(y1)}" width="{_f(x2 - x1)}" height="{_f(y2 - y1)}"/>',
    ]
    pads = [p for f in board.footprints for p in f.pads]
    for layer, cls, zcls in (("B.Cu", "bcu", "zone-b"), ("F.Cu", "fcu", "zone-f")):
        o.append(f'<g id="layer-{layer}" class="layer {cls}">')
        znets = {z.net for z in board.zones if layer in z.layers}
        if znets:
            o.append(f'<rect class="{zcls}" x="{_f(x1 + .4)}" y="{_f(y1 + .4)}" width="{_f(x2 - x1 - .8)}" height="{_f(y2 - y1 - .8)}" rx=".9"/>')
            # knock the pour back around foreign copper so it reads like a real filled zone
            for t in board.tracks:
                if t.layer == layer and t.net not in znets:
                    o.append(f'<line x1="{_f(t.x1)}" y1="{_f(t.y1)}" x2="{_f(t.x2)}" y2="{_f(t.y2)}" stroke-width="{_f(t.width + .5)}" class="halo"/>')
            for p in pads:
                if layer in p.layers and p.net not in znets:
                    o.append(_pad_svg(p, "halo", ' stroke-width=".02"', grow=0.25))
            for v in board.vias:
                if v.net not in znets:
                    o.append(f'<circle cx="{_f(v.x)}" cy="{_f(v.y)}" r="{_f(v.size / 2 + .25)}" class="halo"/>')
        for t in board.tracks:
            if t.layer == layer:
                o.append(f'<line x1="{_f(t.x1)}" y1="{_f(t.y1)}" x2="{_f(t.x2)}" y2="{_f(t.y2)}" stroke-width="{_f(t.width)}" class="trk" data-net="{escape(t.net)}"><title>{escape(t.net)} {t.width:g} mm {layer}</title></line>')
        o.append("</g>")
    o.append('<g id="pads">')
    for p in pads:
        if p.kind == "np_thru_hole":
            o.append(_pad_svg(p, "pad np"))
            continue
        cls = "pad th" if p.kind == "thru_hole" else "pad smd"
        tip = f"<title>{escape(p.name)} {escape(p.net or 'no net')}</title>"
        s = _pad_svg(p, cls, f' data-net="{escape(p.net)}" data-pad="{escape(p.name)}"')
        o.append(s[:-2] + ">" + tip + s[s.index("<") + 1:s.index(" ")].join(["</", ">"]))
        if p.kind == "thru_hole" and p.drill:
            o.append(f'<circle cx="{_f(p.x)}" cy="{_f(p.y)}" r="{_f(min(p.drill, min(p.w, p.h) * .8) / 2)}" class="hole"/>')
    o.append("</g>")
    o.append('<g id="vias">')
    for v in board.vias:
        o.append(f'<circle cx="{_f(v.x)}" cy="{_f(v.y)}" r="{_f(v.size / 2)}" class="via" data-net="{escape(v.net)}"><title>via {escape(v.net)} {v.size:g}/{v.drill:g}</title></circle>')
        o.append(f'<circle cx="{_f(v.x)}" cy="{_f(v.y)}" r="{_f(v.drill / 2)}" class="hole"/>')
    o.append("</g>")
    o.append('<g id="silk">')
    for f in board.footprints:
        o += _fp_graphics(f, "F.Fab", "fab", 0.06)
        o += _fp_graphics(f, "F.SilkS", "silk", 0.12)
    for f in board.footprints:
        if not f.pads or f.pads[0].kind == "np_thru_hole":
            continue
        cx1, cy1, cx2, cy2 = f.court
        big = (cx2 - cx1) * (cy2 - cy1) > 30
        if big:
            tx, ty = f.x, f.y
        else:
            # label small parts just outside the courtyard, on the side with more room
            ox, oy = rot(0, cy1 - 0.55, f.rot)
            tx, ty = f.x + ox, f.y + oy
        o.append(f'<text x="{_f(tx)}" y="{_f(ty)}" class="ref{"" if big else " small"}">{escape(f.ref)}</text>')
    o.append("</g>")
    o.append(f'<rect class="edge" x="{_f(x1)}" y="{_f(y1)}" width="{_f(x2 - x1)}" height="{_f(y2 - y1)}" rx="1.2"/>')
    # ------------------------------------------------------------------ ratsnest
    o.append('<g id="ratsnest">')
    for net, info in ratsnest(board).items():
        for mseg in info["missing"]:
            (ax, ay), (bx, by) = mseg["a"], mseg["b"]
            o.append(f'<line x1="{_f(ax)}" y1="{_f(ay)}" x2="{_f(bx)}" y2="{_f(by)}" class="rats"><title>unrouted {escape(net)}</title></line>')
            o.append(f'<circle cx="{_f(ax)}" cy="{_f(ay)}" r=".3" class="rats-dot"/><circle cx="{_f(bx)}" cy="{_f(by)}" r=".3" class="rats-dot"/>')
    o.append("</g>")
    # ------------------------------------------------------------------ patched items
    o.append('<g id="changes" filter="url(#glow)">')
    for key, c in items.items():
        if key.startswith("pad:"):
            pd = board.find_pad(key[4:])
            if pd is not None:
                o.append(_pad_svg(pd, ("chg fresh" if c[1] == seq else "chg") + " fill"))
    for t in board.tracks:
        c = chg(t.uuid)
        if c:
            L = max(t.length, 0.01)
            o.append(f'<line x1="{_f(t.x1)}" y1="{_f(t.y1)}" x2="{_f(t.x2)}" y2="{_f(t.y2)}" stroke-width="{_f(t.width)}" class="{c}" style="--len:{_f(L + t.width)}" data-net="{escape(t.net)}"/>')
    for v in board.vias:
        c = chg(v.uuid)
        if c:
            o.append(f'<circle cx="{_f(v.x)}" cy="{_f(v.y)}" r="{_f(v.size / 2)}" class="{c} fill"/>')
            o.append(f'<circle cx="{_f(v.x)}" cy="{_f(v.y)}" r="{_f(v.drill / 2)}" class="hole"/>')
    for f in board.footprints:
        c = chg(f.uuid)
        if c:
            sh = f.court_shape(0.15)
            pts = " ".join(f"{_f(px)},{_f(py)}" for px, py in sh.pts)
            o.append(f'<polygon points="{pts}" class="{c} box"/>')
            for p in f.pads:
                o.append(_pad_svg(p, f"{c} fill"))
            o.append(f'<text x="{_f(f.x)}" y="{_f(f.y + (f.court[3] - f.court[1]) / 2 + 0.9 if abs(f.rot % 180) < 1 else f.y + (f.court[2] - f.court[0]) / 2 + 0.9)}" class="ref small" style="fill:#2dffb3">{escape(f.ref)} {escape(f.value)}</text>')
    o.append("</g>")
    # ------------------------------------------------------------------ DFM markers
    o.append('<g id="errtracks" filter="url(#rglow)">')
    if audit:
        thin_nets = {n for it in audit.get("issues", []) if it["rule"] in ("power_track_width", "min_track_width") for n in it.get("nets", [])}
        for t in board.tracks:
            if t.net in thin_nets:
                o.append(f'<line x1="{_f(t.x1)}" y1="{_f(t.y1)}" x2="{_f(t.x2)}" y2="{_f(t.y2)}" stroke-width="{_f(max(t.width, .18))}" class="errtrk"/>')
    o.append("</g>")
    o.append('<g id="markers">')
    if audit:
        placed: list[tuple[float, float]] = []
        for it in audit.get("issues", []):
            if "at" not in it or it["severity"] == "info":
                continue
            ax, ay = it["at"]
            # nudge overlapping labels apart
            ly = ay
            while any(abs(ax - px) < 9 and abs(ly - py) < 1.5 for px, py in placed):
                ly += 1.5
            placed.append((ax, ly))
            cls = "err" if it["severity"] == "error" else "err warn"
            right = ax < (x1 + x2) / 2 + 12
            tx = ax + 1.7 if right else ax - 1.7
            anchor = "start" if right else "end"
            o.append(
                f'<g class="{cls}" data-issue="{escape(it["id"])}"><title>{escape(it["title"])}</title>'
                f'<circle cx="{_f(ax)}" cy="{_f(ay)}" r="1.25" class="ring"/><circle cx="{_f(ax)}" cy="{_f(ay)}" r="1.25" class="ring" style="animation-delay:.55s"/>'
                f'<circle cx="{_f(ax)}" cy="{_f(ay)}" r=".8" class="halo2"/><circle cx="{_f(ax)}" cy="{_f(ay)}" r=".42" class="core"/>'
                f'<text x="{_f(tx)}" y="{_f(ly + .4)}" text-anchor="{anchor}" class="tag">{escape(it["id"])}</text></g>'
            )
    o.append("</g></g></svg>")
    return "\n".join(o)


# ============================================================================= schematic
SCH_CSS = """
.bg{fill:#06090d}
.sheet{fill:#0a0e14;stroke:#1f2937;stroke-width:.3}
.body{fill:#151b26;stroke:#ff7b72;stroke-width:.3;stroke-linejoin:round}
.gfx{fill:none;stroke:#ff7b72;stroke-width:.3;stroke-linecap:round;stroke-linejoin:round}
.pin{stroke:#ff7b72;stroke-width:.2;stroke-linecap:round}
.wire{stroke:#3fb950;stroke-width:.22;stroke-linecap:round}
.pname{fill:#7ee0e6;font:1.15px 'JetBrains Mono',Consolas,monospace;dominant-baseline:central}
.pnum{fill:#6e7681;font:.9px 'JetBrains Mono',Consolas,monospace}
.label{fill:#58a6ff;font:600 1.3px 'JetBrains Mono',Consolas,monospace}
.label.pwr{fill:#ffa657}.label.gnd{fill:#8b949e}
.refd{fill:#e6edf3;font:700 1.6px 'JetBrains Mono',Consolas,monospace}
.vald{fill:#a5b4c4;font:1.3px 'JetBrains Mono',Consolas,monospace}
.nc{stroke:#566170;stroke-width:.2}
.note{fill:#566170;font:700 2.6px 'JetBrains Mono',Consolas,monospace;letter-spacing:.2px}
.frame{fill:none;stroke:#1f2937;stroke-width:.25;stroke-dasharray:1.2 .8}
.chgbox{fill:rgba(45,255,179,.08);stroke:#2dffb3;stroke-width:.3;stroke-dasharray:1 .5;animation:chgpulse 2.4s ease-in-out infinite;transform-box:fill-box;transform-origin:center}
.chgbox.fresh{animation:popin .55s cubic-bezier(.2,1.6,.4,1) both, chgpulse 2.4s ease-in-out .6s infinite}
.errbox{fill:rgba(255,59,92,.07);stroke:#ff3b5c;stroke-width:.3;animation:errblink 1s ease-in-out infinite}
.errtag{fill:#ff8fa3;font:700 1.5px 'JetBrains Mono',Consolas,monospace}
@keyframes chgpulse{0%,100%{stroke:#2dffb3}50%{stroke:#34d5ff}}
@keyframes popin{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}
@keyframes errblink{0%,100%{opacity:1}50%{opacity:.35}}
"""


def render_sch_svg(sch: Schematic, audit: "dict | None" = None, changes: "dict | None" = None, title: str = "") -> str:
    items = (changes or {}).get("items", {})
    seq = (changes or {}).get("seq", 0)
    libs = {str(ls[1]): ls for ls in find_all(find(sch.tree, "lib_symbols") or [], "symbol")}
    o: list[str] = []
    xs: list[float] = []
    ys: list[float] = []
    boxes: dict[str, tuple[float, float, float, float]] = {}

    def track(x, y):
        xs.append(x)
        ys.append(y)

    def label_cls(name):
        u = name.upper()
        if u.startswith("GND") or u.startswith("VSS"):
            return "label gnd"
        if u.startswith("+") or "VBUS" in u or "VCC" in u:
            return "label pwr"
        return "label"

    for a, b, _ in sch.wires:
        o.append(f'<line x1="{_f(a[0])}" y1="{_f(a[1])}" x2="{_f(b[0])}" y2="{_f(b[1])}" class="wire"/>')
        track(*a)
        track(*b)
    for s in sch.symbols:
        lib = libs.get(s.lib_id)
        if lib is None:
            continue
        bx: list[float] = []
        by: list[float] = []

        def T(px, py, s=s):
            ox, oy = _xform(px, py, s.rot, s.mirror)
            return s.x + ox, s.y + oy

        def walk(node, s=s, T=T, bx=bx, by=by):
            for c in node:
                if not isinstance(c, list) or not c:
                    continue
                if c[0] == "symbol":
                    walk(c)
                elif c[0] == "rectangle":
                    (ax, ay), (cx, cy) = T(num(find(c, "start")[1]), num(find(c, "start")[2])), T(num(find(c, "end")[1]), num(find(c, "end")[2]))
                    o.append(f'<rect x="{_f(min(ax, cx))}" y="{_f(min(ay, cy))}" width="{_f(abs(cx - ax))}" height="{_f(abs(cy - ay))}" rx=".4" class="body"/>')
                    bx.extend([ax, cx])
                    by.extend([ay, cy])
                elif c[0] == "polyline":
                    pts = [T(num(p[1]), num(p[2])) for p in find_all(find(c, "pts"), "xy")]
                    o.append('<polyline points="' + " ".join(f"{_f(px)},{_f(py)}" for px, py in pts) + '" class="gfx"/>')
                    bx.extend(p[0] for p in pts)
                    by.extend(p[1] for p in pts)

        walk(lib)
        hide_names = any(str(x) == "hide" for x in (find(lib, "pin_names") or []))
        hide_nums = find(lib, "pin_numbers") is not None
        lp_by_num = {lp["number"]: lp for lp in sch.lib_pins.get(s.lib_id, [])}
        for p in s.pins:
            lp = lp_by_num.get(p.number, {})
            ln = lp.get("length", 2.54)
            ex, ey = p.x - p.dx * ln, p.y - p.dy * ln
            o.append(f'<line x1="{_f(p.x)}" y1="{_f(p.y)}" x2="{_f(ex)}" y2="{_f(ey)}" class="pin"/>')
            bx.extend([p.x, ex])
            by.extend([p.y, ey])
            if not hide_names and p.name and p.name != "~":
                if p.dx:
                    anchor = "start" if p.dx < 0 else "end"
                    o.append(f'<text x="{_f(ex - p.dx * .7)}" y="{_f(ey)}" text-anchor="{anchor}" class="pname">{escape(p.name)}</text>')
                else:
                    anchor = "end" if p.dy < 0 else "start"
                    o.append(f'<text x="{_f(ex)}" y="{_f(ey - p.dy * .7)}" text-anchor="{anchor}" class="pname" transform="rotate(-90 {_f(ex)} {_f(ey - p.dy * .7)})">{escape(p.name)}</text>')
            if not hide_nums and p.number and p.dx:
                o.append(f'<text x="{_f(p.x - p.dx * 1.27)}" y="{_f(p.y - .35)}" text-anchor="middle" class="pnum">{escape(p.number)}</text>')
        if bx:
            boxes[s.ref] = (min(bx), min(by), max(bx), max(by))
        for prop in find_all(s.node, "property"):
            if prop[1] not in ("Reference", "Value"):
                continue
            eff = find(prop, "effects")
            if eff and (find(eff, "hide") is not None or any(str(x) == "hide" for x in eff)):
                continue
            if find(prop, "hide") is not None:
                continue
            at = find(prop, "at")
            o.append(f'<text x="{_f(num(at[1]))}" y="{_f(num(at[2]))}" class="{"refd" if prop[1] == "Reference" else "vald"}">{escape(str(prop[2]))}</text>')
            track(num(at[1]), num(at[2]))
        xs.extend(bx)
        ys.extend(by)
    for lab in sch.labels:
        at = find(lab["node"], "at")
        ang = num(at[3]) if len(at) > 3 else 0
        x, y = lab["x"], lab["y"]
        n = len(lab["name"]) * 0.8 + 1
        if ang == 0:
            o.append(f'<text x="{_f(x + .4)}" y="{_f(y - .35)}" class="{label_cls(lab["name"])}">{escape(lab["name"])}</text>')
            track(x + n, y)
        elif ang == 180:
            o.append(f'<text x="{_f(x - .4)}" y="{_f(y - .35)}" text-anchor="end" class="{label_cls(lab["name"])}">{escape(lab["name"])}</text>')
            track(x - n, y)
        elif ang == 90:
            o.append(f'<text x="{_f(x)}" y="{_f(y)}" class="{label_cls(lab["name"])}" transform="rotate(-90 {_f(x)} {_f(y)}) translate(.4 -.35)">{escape(lab["name"])}</text>')
            track(x, y - n)
        else:
            o.append(f'<text x="{_f(x)}" y="{_f(y)}" text-anchor="end" class="{label_cls(lab["name"])}" transform="rotate(-90 {_f(x)} {_f(y)}) translate(-.4 -.35)">{escape(lab["name"])}</text>')
            track(x, y + n)
    for (kx, ky) in sch.no_connects:
        x, y = kx / 100, ky / 100
        o.append(f'<path d="M{_f(x - .6)} {_f(y - .6)}L{_f(x + .6)} {_f(y + .6)}M{_f(x - .6)} {_f(y + .6)}L{_f(x + .6)} {_f(y - .6)}" class="nc"/>')
    for node in find_all(sch.tree, "text"):
        at = find(node, "at")
        o.append(f'<text x="{_f(num(at[1]))}" y="{_f(num(at[2]))}" class="note">{escape(str(node[1]))}</text>')
        track(num(at[1]), num(at[2]))
        track(num(at[1]) + len(str(node[1])) * 1.8, num(at[2]) - 3)
    for node in find_all(sch.tree, "rectangle"):
        s_, e_ = find(node, "start"), find(node, "end")
        o.append(f'<rect x="{_f(num(s_[1]))}" y="{_f(num(s_[2]))}" width="{_f(num(e_[1]) - num(s_[1]))}" height="{_f(num(e_[2]) - num(s_[2]))}" rx="1.5" class="frame"/>')
        track(num(s_[1]), num(s_[2]))
        track(num(e_[1]), num(e_[2]))
    # overlays
    o.append('<g id="sch-changes" filter="url(#glow)">')
    for ref, (ax, ay, bx_, by_) in boxes.items():
        c = items.get(f"sch:{ref}")
        if c:
            cls = "chgbox fresh" if c[1] == seq else "chgbox"
            o.append(f'<rect x="{_f(ax - 2.2)}" y="{_f(ay - 2.2)}" width="{_f(bx_ - ax + 4.4)}" height="{_f(by_ - ay + 4.4)}" rx="1.2" class="{cls}"/>')
    o.append("</g>")
    if audit:
        seen: set[str] = set()
        for it in audit.get("issues", []):
            if it["severity"] != "error":
                continue
            ref = next((r for r in it.get("refs", []) if r in boxes), None)
            if ref is None or ref in seen:
                continue
            seen.add(ref)
            ax, ay, bx_, by_ = boxes[ref]
            o.append(f'<rect x="{_f(ax - 1.4)}" y="{_f(ay - 1.4)}" width="{_f(bx_ - ax + 2.8)}" height="{_f(by_ - ay + 2.8)}" rx="1" class="errbox"><title>{escape(it["title"])}</title></rect>')
            o.append(f'<text x="{_f(ax - 1.4)}" y="{_f(by_ + 4)}" class="errtag">{escape(it["id"])}</text>')
    if not xs:
        xs, ys = [0, 100], [0, 100]
    mx = 8
    vx, vy = min(xs) - mx, min(ys) - mx
    vw, vh = max(xs) - min(xs) + 2 * mx, max(ys) - min(ys) + 2 * mx
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_f(vx)} {_f(vy)} {_f(vw)} {_f(vh)}" data-seq="{seq}" class="clawboard-sch">',
        f"<title>{escape(title or 'ClawBoard schematic preview')}</title>", _defs(vx, vy, vw, vh), f"<style>{SCH_CSS}</style>",
        f'<rect class="bg" x="{_f(vx)}" y="{_f(vy)}" width="{_f(vw)}" height="{_f(vh)}"/>',
        f'<rect class="sheet" x="{_f(vx + 2)}" y="{_f(vy + 2)}" width="{_f(vw - 4)}" height="{_f(vh - 4)}" rx="2"/>',
    ]
    return "\n".join(head + o + ["</svg>"])
