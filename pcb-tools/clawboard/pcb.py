"""In-memory model of a ``.kicad_pcb`` file (KiCad 7/8/9/10 S-expression format).

The S-expression tree stays the source of truth: mutators edit the tree and the
lightweight index (footprints / pads / tracks / vias / zones) is rebuilt from it.
"""
from __future__ import annotations

import math
from pathlib import Path

from .geom import Shape, bbox_gap, closest_points, rot, shape_dist
from .sexpr import S, Sym, dumps, find, find_all, new_uuid, num, parse, val

CU_LAYERS = ("F.Cu", "B.Cu")
TOUCH = 2e-3  # mm - shapes closer than this are electrically connected


def _uuid_of(node) -> str:
    u = find(node, "uuid") or find(node, "tstamp")
    return str(u[1]) if u and len(u) > 1 else ""


class Pad:
    __slots__ = ("fp", "num", "net", "x", "y", "w", "h", "angle", "kind", "shape_name", "drill", "layers", "shape", "node", "pintype")

    @property
    def name(self) -> str:
        return f"{self.fp.ref}.{self.num}"


class Footprint:
    __slots__ = ("ref", "value", "lib", "x", "y", "rot", "layer", "pads", "node", "uuid", "court", "attrs")

    def pad(self, num_) -> "Pad | None":
        for p in self.pads:
            if p.num == str(num_):
                return p
        return None

    def court_shape(self, margin: float = 0.0) -> Shape:
        x1, y1, x2, y2 = self.court
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        rx, ry = rot(cx, cy, self.rot)
        return Shape.rect(self.x + rx, self.y + ry, (x2 - x1) + 2 * margin, (y2 - y1) + 2 * margin, self.rot)


class Track:
    __slots__ = ("x1", "y1", "x2", "y2", "width", "layer", "net", "node", "uuid", "shape")

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


class Via:
    __slots__ = ("x", "y", "size", "drill", "net", "layers", "node", "uuid", "shape")


class Zone:
    __slots__ = ("net", "layers", "pts", "node", "uuid")


class Cu:
    """One copper (or drilled) primitive used for connectivity + clearance."""

    __slots__ = ("shape", "layers", "net", "kind", "label", "obj")

    def __init__(self, shape, layers, net, kind, label, obj):
        self.shape, self.layers, self.net, self.kind, self.label, self.obj = shape, layers, net, kind, label, obj


class Board:
    def __init__(self, path):
        self.path = Path(path)
        self.tree = parse(self.path.read_text(encoding="utf-8"))
        if not self.tree or self.tree[0] != "kicad_pcb":
            raise ValueError(f"{path} is not a KiCad PCB file")
        self.index()

    # ------------------------------------------------------------------ indexing
    def index(self) -> None:
        t = self.tree
        self.nets: dict[int, str] = {}
        for n in find_all(t, "net"):
            if len(n) >= 3:
                self.nets[int(n[1])] = str(n[2])
        self.net_numbers = {name: k for k, name in self.nets.items()}
        self.footprints: list[Footprint] = []
        self.tracks: list[Track] = []
        self.vias: list[Via] = []
        self.zones: list[Zone] = []
        edge_pts: list[tuple[float, float]] = []
        for node in t:
            if not isinstance(node, list) or not node:
                continue
            head = node[0]
            if head in ("footprint", "module"):
                self.footprints.append(self._read_fp(node))
            elif head == "segment":
                self.tracks.append(self._read_track(node))
            elif head == "via":
                self.vias.append(self._read_via(node))
            elif head == "zone":
                z = self._read_zone(node)
                if z:
                    self.zones.append(z)
            elif head in ("gr_line", "gr_rect", "gr_arc", "gr_poly", "gr_circle") and str(val(node, "layer", "")) == "Edge.Cuts":
                for key in ("start", "end", "mid"):
                    c = find(node, key)
                    if c:
                        edge_pts.append((num(c[1]), num(c[2])))
                if head == "gr_circle":
                    c, e = find(node, "center"), find(node, "end")
                    if c and e:
                        r = math.hypot(num(e[1]) - num(c[1]), num(e[2]) - num(c[2]))
                        edge_pts += [(num(c[1]) - r, num(c[2]) - r), (num(c[1]) + r, num(c[2]) + r)]
                pts = find(node, "pts")
                if pts:
                    edge_pts += [(num(p[1]), num(p[2])) for p in find_all(pts, "xy")]
        self.fp_by_ref = {f.ref: f for f in self.footprints}
        if edge_pts:
            xs, ys = [p[0] for p in edge_pts], [p[1] for p in edge_pts]
            self.outline = (min(xs), min(ys), max(xs), max(ys))
        else:
            xs = [p.x for f in self.footprints for p in f.pads] or [0.0, 50.0]
            ys = [p.y for f in self.footprints for p in f.pads] or [0.0, 50.0]
            self.outline = (min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2)
        self._cu = None

    def _net_name(self, node) -> str:
        c = find(node, "net")
        if c is None or len(c) < 2:
            return ""
        if len(c) >= 3:
            return str(c[2])
        if isinstance(c[1], int) or (isinstance(c[1], Sym) and str(c[1]).lstrip("-").isdigit()):
            return self.nets.get(int(c[1]), "")
        return str(c[1])

    def _read_fp(self, node) -> Footprint:
        f = Footprint()
        f.node, f.uuid = node, _uuid_of(node)
        f.lib = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) else ""
        at = find(node, "at") or [S("at"), 0, 0]
        f.x, f.y = num(at[1]), num(at[2])
        f.rot = num(at[3]) if len(at) > 3 else 0.0
        f.layer = str(val(node, "layer", "F.Cu"))
        f.ref, f.value = "?", ""
        for p in find_all(node, "property"):
            if p[1] == "Reference":
                f.ref = str(p[2])
            elif p[1] == "Value":
                f.value = str(p[2])
        for p in find_all(node, "fp_text"):  # KiCad <= 7
            if p[1] == "reference":
                f.ref = str(p[2])
            elif p[1] == "value":
                f.value = str(p[2])
        a = find(node, "attr")
        f.attrs = [str(x) for x in a[1:]] if a else []
        # courtyard (local frame)
        cx: list[float] = []
        cy: list[float] = []
        for g in node:
            if isinstance(g, list) and g and g[0] in ("fp_line", "fp_rect") and "CrtYd" in str(val(g, "layer", "")):
                for key in ("start", "end"):
                    c = find(g, key)
                    if c:
                        cx.append(num(c[1]))
                        cy.append(num(c[2]))
        f.pads = []
        for pn in find_all(node, "pad"):
            f.pads.append(self._read_pad(f, pn))
        if cx:
            f.court = (min(cx), min(cy), max(cx), max(cy))
        elif f.pads:
            lx, ly = [], []
            for p in f.pads:
                dx, dy = rot(p.x - f.x, p.y - f.y, -f.rot)
                m = max(p.w, p.h) / 2 + 0.25
                lx += [dx - m, dx + m]
                ly += [dy - m, dy + m]
            f.court = (min(lx), min(ly), max(lx), max(ly))
        else:
            f.court = (-0.5, -0.5, 0.5, 0.5)
        return f

    def _read_pad(self, f: Footprint, pn) -> Pad:
        p = Pad()
        p.fp, p.node = f, pn
        p.num = str(pn[1])
        p.kind = str(pn[2])
        p.shape_name = str(pn[3])
        at = find(pn, "at") or [S("at"), 0, 0]
        lx, ly = num(at[1]), num(at[2])
        p.angle = num(at[3]) if len(at) > 3 else 0.0
        rx, ry = rot(lx, ly, f.rot)
        p.x, p.y = round(f.x + rx, 6), round(f.y + ry, 6)
        size = find(pn, "size") or [S("size"), 1, 1]
        p.w, p.h = num(size[1]), num(size[2])
        d = find(pn, "drill")
        p.drill = 0.0
        if d:
            vals = [num(x) for x in d[1:] if not isinstance(x, list) and str(x) != "oval"]
            p.drill = max(vals) if vals else 0.0
        layers = [str(x) for x in (find(pn, "layers") or [None])[1:]]
        cu = set()
        for L in layers:
            if L in ("*.Cu", "F&B.Cu"):
                cu.update(CU_LAYERS)
            elif L in CU_LAYERS:
                cu.add(L)
        if p.kind in ("thru_hole", "np_thru_hole"):
            cu.update(CU_LAYERS)
        p.layers = tuple(sorted(cu, reverse=True))
        p.net = "" if p.kind == "np_thru_hole" else self._net_name(pn)
        p.pintype = str(val(pn, "pintype", ""))
        if p.shape_name == "circle":
            p.shape = Shape.circle(p.x, p.y, p.w / 2)
        elif p.shape_name == "oval":
            p.shape = Shape.oval(p.x, p.y, p.w, p.h, p.angle)
        elif p.shape_name == "roundrect":
            rr = num(val(pn, "roundrect_rratio", 0.25)) * min(p.w, p.h)
            p.shape = Shape.rect(p.x, p.y, p.w, p.h, p.angle, rr)
        else:
            p.shape = Shape.rect(p.x, p.y, p.w, p.h, p.angle)
        return p

    def _read_track(self, node) -> Track:
        t = Track()
        t.node, t.uuid = node, _uuid_of(node)
        s, e = find(node, "start"), find(node, "end")
        t.x1, t.y1, t.x2, t.y2 = num(s[1]), num(s[2]), num(e[1]), num(e[2])
        t.width = num(val(node, "width", 0.25))
        t.layer = str(val(node, "layer", "F.Cu"))
        t.net = self._net_name(node)
        t.shape = Shape.capsule(t.x1, t.y1, t.x2, t.y2, t.width)
        return t

    def _read_via(self, node) -> Via:
        v = Via()
        v.node, v.uuid = node, _uuid_of(node)
        at = find(node, "at")
        v.x, v.y = num(at[1]), num(at[2])
        v.size = num(val(node, "size", 0.6))
        v.drill = num(val(node, "drill", 0.3))
        v.net = self._net_name(node)
        v.layers = CU_LAYERS
        v.shape = Shape.circle(v.x, v.y, v.size / 2)
        return v

    def _read_zone(self, node) -> "Zone | None":
        z = Zone()
        z.node, z.uuid = node, _uuid_of(node)
        z.net = str(val(node, "net_name", "")) or self._net_name(node)
        layers = find(node, "layers")
        z.layers = tuple(str(x) for x in layers[1:]) if layers else (str(val(node, "layer", "F.Cu")),)
        poly = find(node, "polygon")
        pts = find(poly, "pts") if poly else None
        z.pts = [(num(p[1]), num(p[2])) for p in find_all(pts, "xy")] if pts else []
        return z if z.net else None

    # ------------------------------------------------------------------ nets
    def ensure_net(self, name: str) -> int:
        if not self.net_numbers and not find(self.tree, "net"):
            return 0  # name-only file format
        if name in self.net_numbers:
            return self.net_numbers[name]
        k = max(self.nets) + 1 if self.nets else 1
        self.nets[k] = name
        self.net_numbers[name] = k
        last = max((i for i, c in enumerate(self.tree) if isinstance(c, list) and c and c[0] == "net"), default=len(self.tree) - 1)
        self.tree.insert(last + 1, [S("net"), k, name])
        return k

    def net_token(self, name: str, with_name: bool = True) -> list:
        k = self.ensure_net(name)
        if not self.net_numbers:
            return [S("net"), name]
        return [S("net"), k, name] if with_name else [S("net"), k]

    def net_names(self) -> list[str]:
        names = {p.net for f in self.footprints for p in f.pads if p.net}
        names.update(t.net for t in self.tracks if t.net)
        return sorted(names)

    # ------------------------------------------------------------------ mutation
    def _remove_node(self, node) -> None:
        for i, c in enumerate(self.tree):
            if c is node:
                del self.tree[i]
                return

    def add_track(self, net, layer, x1, y1, x2, y2, width) -> Track:
        node = [
            S("segment"), [S("start"), round(x1, 4), round(y1, 4)], [S("end"), round(x2, 4), round(y2, 4)],
            [S("width"), width], [S("layer"), layer], self.net_token(net, False), [S("uuid"), new_uuid()],
        ]
        self.tree.append(node)
        t = self._read_track(node)
        self.tracks.append(t)
        self._cu = None
        return t

    def add_via(self, net, x, y, size=0.6, drill=0.3) -> Via:
        node = [
            S("via"), [S("at"), round(x, 4), round(y, 4)], [S("size"), size], [S("drill"), drill],
            [S("layers"), "F.Cu", "B.Cu"], self.net_token(net, False), [S("uuid"), new_uuid()],
        ]
        self.tree.append(node)
        v = self._read_via(node)
        self.vias.append(v)
        self._cu = None
        return v

    def remove_track(self, t: Track) -> None:
        self._remove_node(t.node)
        self.tracks = [x for x in self.tracks if x is not t]
        self._cu = None

    def remove_via(self, v: Via) -> None:
        self._remove_node(v.node)
        self.vias = [x for x in self.vias if x is not v]
        self._cu = None

    def add_footprint_node(self, node) -> Footprint:
        # keep footprints before tracks for a tidy file
        idx = max((i for i, c in enumerate(self.tree) if isinstance(c, list) and c and c[0] in ("footprint", "net", "setup", "layers")), default=len(self.tree) - 1)
        self.tree.insert(idx + 1, node)
        f = self._read_fp(node)
        self.footprints.append(f)
        self.fp_by_ref[f.ref] = f
        self._cu = None
        return f

    def set_pad_net(self, pad: Pad, net: str) -> None:
        node = pad.node
        for i, c in enumerate(node):
            if isinstance(c, list) and c and c[0] == "net":
                del node[i]
                break
        if net:
            at = next((i for i, c in enumerate(node) if isinstance(c, list) and c and c[0] in ("uuid", "tstamp", "pintype")), len(node))
            node.insert(at, self.net_token(net))
        pad.net = net
        self._cu = None

    def move_footprint(self, f: Footprint, x: float, y: float, rotation: "float | None" = None) -> Footprint:
        node = f.node
        new_rot = f.rot if rotation is None else float(rotation) % 360
        delta = new_rot - f.rot
        at = find(node, "at")
        at[1:] = [round(x, 4), round(y, 4), new_rot]
        if abs(delta) > 1e-9:
            for c in node:
                if isinstance(c, list) and c and c[0] in ("pad", "property", "fp_text"):
                    a = find(c, "at")
                    if a is not None:
                        cur = num(a[3]) if len(a) > 3 else 0.0
                        a[3:] = [(cur + delta) % 360]
        nf = self._read_fp(node)
        self.footprints = [nf if x_ is f else x_ for x_ in self.footprints]
        self.fp_by_ref[nf.ref] = nf
        self._cu = None
        return nf

    def set_track_width(self, t: Track, width: float) -> None:
        w = find(t.node, "width")
        w[1:] = [width]
        t.width = width
        t.shape = Shape.capsule(t.x1, t.y1, t.x2, t.y2, width)
        self._cu = None

    def save(self, path=None) -> None:
        Path(path or self.path).write_text(dumps(self.tree), encoding="utf-8", newline="\n")

    # ------------------------------------------------------------------ copper view
    def copper(self) -> list[Cu]:
        if self._cu is not None:
            return self._cu
        items: list[Cu] = []
        for f in self.footprints:
            for p in f.pads:
                kind = "hole" if p.kind == "np_thru_hole" else "pad"
                items.append(Cu(p.shape, p.layers, p.net, kind, p.name if p.num else f.ref, p))
        for t in self.tracks:
            items.append(Cu(t.shape, (t.layer,), t.net, "track", f"track[{t.net}]", t))
        for v in self.vias:
            items.append(Cu(v.shape, v.layers, v.net, "via", f"via[{v.net}]", v))
        self._cu = items
        return items

    def zone_layers(self, net: str) -> set[str]:
        out: set[str] = set()
        for z in self.zones:
            if z.net == net:
                out.update(L for L in z.layers if L in CU_LAYERS)
        return out

    def find_pad(self, spec: str) -> "Pad | None":
        """``"U1.2"`` -> Pad."""
        if "." not in spec:
            return None
        ref, pin = spec.rsplit(".", 1)
        f = self.fp_by_ref.get(ref)
        return f.pad(pin) if f else None

    def size(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.outline
        return round(x2 - x1, 3), round(y2 - y1, 3)


# =========================================================================== connectivity
class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def net_groups(board: Board, net: str, items: "list[Cu] | None" = None) -> list[list[Cu]]:
    """Connected copper islands of one net (zones are treated as solid pours)."""
    cu = [c for c in (items if items is not None else board.copper()) if c.net == net and c.kind != "hole"]
    n = len(cu)
    dsu = _DSU(n)
    order = sorted(range(n), key=lambda i: cu[i].shape.bbox[0])
    for a in range(n):
        i = order[a]
        bi = cu[i].shape.bbox
        for b in range(a + 1, n):
            j = order[b]
            bj = cu[j].shape.bbox
            if bj[0] > bi[2] + TOUCH:
                break
            if bj[1] > bi[3] + TOUCH or bj[3] < bi[1] - TOUCH:
                continue
            if dsu.find(i) == dsu.find(j):
                continue
            if not set(cu[i].layers) & set(cu[j].layers):
                continue
            if shape_dist(cu[i].shape, cu[j].shape) <= TOUCH:
                dsu.union(i, j)
    zl = board.zone_layers(net)
    if zl:
        first = None
        for i in range(n):
            if set(cu[i].layers) & zl:
                if first is None:
                    first = i
                else:
                    dsu.union(first, i)
    groups: dict[int, list[Cu]] = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(cu[i])
    return list(groups.values())


def ratsnest(board: Board) -> dict[str, dict]:
    """Per net: number of missing connections + the closest pad pairs that still need copper."""
    out: dict[str, dict] = {}
    cu = board.copper()
    by_net: dict[str, list[Cu]] = {}
    for c in cu:
        if c.net and c.kind != "hole":
            by_net.setdefault(c.net, []).append(c)
    for net, items in by_net.items():
        pads = [c for c in items if c.kind == "pad"]
        if len(pads) < 2:
            continue
        groups = [g for g in net_groups(board, net, items) if any(c.kind == "pad" for c in g)]
        if len(groups) <= 1:
            continue
        groups.sort(key=lambda g: -sum(1 for c in g if c.kind == "pad"))
        connected = [c for c in groups[0] if c.kind == "pad"]
        missing = []
        rest = [[c for c in g if c.kind == "pad"] for g in groups[1:]]
        while rest:
            best = None
            for gi, g in enumerate(rest):
                for a in g:
                    for b in connected:
                        d = math.hypot(a.obj.x - b.obj.x, a.obj.y - b.obj.y)
                        if best is None or d < best[0]:
                            best = (d, gi, a, b)
            d, gi, a, b = best
            missing.append({"from": b.label, "to": a.label, "dist_mm": round(d, 2), "a": (b.obj.x, b.obj.y), "b": (a.obj.x, a.obj.y)})
            connected += rest.pop(gi)
        out[net] = {"missing": missing, "islands": len(groups)}
    return out


# =========================================================================== clearance
def clearance_violations(board: Board, min_clearance: float, hole_clearance: float = 0.2) -> list[dict]:
    cu = board.copper()
    order = sorted(range(len(cu)), key=lambda i: cu[i].shape.bbox[0])
    reach = max(min_clearance, hole_clearance)
    out = []
    for a in range(len(order)):
        A = cu[order[a]]
        ba = A.shape.bbox
        for b in range(a + 1, len(order)):
            B = cu[order[b]]
            bb = B.shape.bbox
            if bb[0] > ba[2] + reach:
                break
            if bb[1] > ba[3] + reach or bb[3] < ba[1] - reach:
                continue
            if A.net == B.net and A.net:
                continue
            if A.kind == "pad" and B.kind == "pad" and A.obj.fp is B.obj.fp:
                continue
            if A.kind == "hole" and B.kind == "hole":
                continue
            if A.kind in ("pad", "hole") and B.kind in ("pad", "hole") and A.obj.fp is B.obj.fp:
                continue
            shared = set(A.layers) & set(B.layers)
            if not shared:
                continue
            need = hole_clearance if "hole" in (A.kind, B.kind) else min_clearance
            if bbox_gap(ba, bb) >= need:
                continue
            d = shape_dist(A.shape, B.shape)
            if d < need - 1e-4:
                x, y = closest_points(A.shape, B.shape)
                out.append({
                    "a": A, "b": B, "gap_mm": round(max(d, 0.0), 4), "required_mm": need,
                    "layer": sorted(shared, reverse=True)[0], "x": round(x, 3), "y": round(y, 3), "short": d <= TOUCH,
                })
    return out


def edge_violations(board: Board, min_edge: float) -> list[dict]:
    x1, y1, x2, y2 = board.outline
    out = []
    for c in board.copper():
        if c.kind not in ("track", "via"):
            continue
        b = c.shape.bbox
        d = min(b[0] - x1, b[1] - y1, x2 - b[2], y2 - b[3])
        if d < min_edge - 1e-4:
            cx, cy = c.shape.center()
            out.append({"item": c, "gap_mm": round(d, 3), "x": round(cx, 3), "y": round(cy, 3)})
    return out
