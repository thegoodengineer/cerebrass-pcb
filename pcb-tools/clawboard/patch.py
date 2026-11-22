"""``apply_pcb_patch`` - programmatic edits that keep schematic and board in sync.

The agent states intent ("add 100nF between +3V3 and GND near U1.2"); geometry
(placement on a clean grid, clearance-safe Manhattan/45 degree routing) is solved here.
"""
from __future__ import annotations

import math
import re
import time

from . import library
from .audit import load_rules, run_audit
from .geom import Shape, rot, shape_dist, snap
from .pcb import CU_LAYERS, TOUCH, Board, Cu, net_groups, ratsnest
from .pour import stitch_pour
from .project import Project
from .router import RouteGrid, find_free_spot, route_net
from .sexpr import S, find

SAFE_CLEARANCE = 0.15


class PatchError(Exception):
    pass


# ----------------------------------------------------------------------------- helpers
def _rules():
    return load_rules("jlcpcb", 2)


def default_width(net: str, rules: dict) -> float:
    pw = rules["power"]
    if re.search(rules["erc"]["ground_regex"], net, re.I):
        return 0.4
    if re.search(pw["power_net_regex"], net, re.I):
        return pw["high_current_min_width_mm"] if re.search(pw["high_current_regex"], net, re.I) else pw["min_width_mm"]
    return 0.25


def _xy(v, what="point"):
    if not (isinstance(v, (list, tuple)) and len(v) >= 2):
        raise PatchError(f"{what} must be [x, y] in mm")
    return float(v[0]), float(v[1])


def _clears(board: Board, shape: Shape, layers, net: str, clearance: float) -> "tuple[bool, str]":
    b = shape.bbox
    for c in board.copper():
        if c.net == net and c.net and c.kind != "hole":
            continue
        cb = c.shape.bbox
        if cb[0] > b[2] + clearance or cb[2] < b[0] - clearance or cb[1] > b[3] + clearance or cb[3] < b[1] - clearance:
            continue
        if not set(layers) & set(c.layers):
            continue
        d = shape_dist(shape, c.shape)
        if d < clearance - 1e-4:
            return False, f"{d:.3f} mm from {c.label or c.kind}"
    return True, ""


def cleanup_dangling(board: Board, net: str) -> int:
    """Remove track stubs (and orphan vias) of ``net`` that no longer lead anywhere."""
    removed = 0
    has_zone = bool(board.zone_layers(net))
    while True:
        items = [c for c in board.copper() if c.net == net and c.kind != "hole"]
        victim = None
        for c in items:
            if c.kind != "track":
                continue
            t = c.obj
            for ex, ey in ((t.x1, t.y1), (t.x2, t.y2)):
                if not any(o is not c and t.layer in o.layers and o.shape.dist_point(ex, ey) <= TOUCH for o in items):
                    victim = t
                    break
            if victim:
                break
        if victim is not None:
            board.remove_track(victim)
            removed += 1
            continue
        if not has_zone:
            for c in items:
                if c.kind == "via" and not any(o is not c and o.kind in ("track", "pad") and shape_dist(o.shape, c.shape) <= TOUCH for o in items):
                    board.remove_via(c.obj)
                    removed += 1
                    victim = c
                    break
            if victim is not None:
                continue
        return removed


def ripup_pad(board: Board, pad) -> int:
    if not pad.net:
        return 0
    net = pad.net
    hit = [t for t in board.tracks if t.net == net and t.layer in pad.layers and shape_dist(t.shape, pad.shape) <= TOUCH]
    for t in hit:
        board.remove_track(t)
    return len(hit) + cleanup_dangling(board, net)


def widen_tracks(board: Board, tracks, width: float, clearance: float = SAFE_CLEARANCE) -> dict:
    """Set each segment to ``width`` where it stays clearance-clean, else the widest legal fallback."""
    done, necked = 0, 0
    steps = [w for w in (width, 0.4, 0.3, 0.25, 0.2, 0.15) if w <= width + 1e-9]
    for t in tracks:
        chosen = None
        for w in steps:
            if w <= t.width + 1e-9:
                break
            if _clears(board, Shape.capsule(t.x1, t.y1, t.x2, t.y2, w), (t.layer,), t.net, clearance)[0]:
                chosen = w
                break
        if chosen is not None:
            board.set_track_width(t, chosen)
        if chosen == width:
            done += 1
        else:
            necked += 1
    return {"widened": done, "necked": necked}


def _route(board: Board, net: str, width: float, grid: "RouteGrid | None", only_pads=None, layer=None) -> dict:
    """Route at full width; if fine-pitch pads make that impossible, route thin and widen where legal."""
    layers = CU_LAYERS if layer is None else (layer,)
    prefer = layer or "F.Cu"
    res = route_net(board, net, width=width, grid=grid, only_pads=only_pads, layers=layers, prefer=prefer)
    if res["failed"] and width > 0.25:
        res2 = route_net(board, net, width=0.2, clearance=0.15, only_pads=only_pads, layers=layers, prefer=prefer)
        ids = set(res2["new"])
        w = widen_tracks(board, [t for t in board.tracks if t.uuid in ids], width)
        res["routed"] += res2["routed"]
        res["vias"] += res2["vias"]
        res["length_mm"] = round(res["length_mm"] + res2["length_mm"], 2)
        res["new"] += res2["new"]
        res["failed"] = res2["failed"]
        if w["necked"]:
            res["notes"] = [f"{w['necked']} short segment(s) necked down at fine-pitch pads"]
    return res


def _is_pour_net(board: Board, net: str) -> bool:
    return "F.Cu" in board.zone_layers(net)


def _place(board: Board, fp_name: str, near_xy, anchor_pad_local=None, rotations=(0, 90, 180, 270), ignore_ref=None, grid_mm=0.5, pad_nets=None):
    """Best (x, y, rot) for a footprint: closest free grid spot, oriented so the anchor pad faces ``near_xy``."""
    spec = library.FOOTPRINTS[fp_name]
    x1, y1, x2, y2 = spec["courtyard"]
    pad_nets = pad_nets or {}
    saved = None
    if ignore_ref and ignore_ref in board.fp_by_ref:
        saved = board.footprints
        board.footprints = [f for f in saved if f.ref != ignore_ref]
        board._cu = None
    try:
        best = None
        for r in rotations:
            def court(x, y, r=r):
                cx, cy = rot((x1 + x2) / 2, (y1 + y2) / 2, r)
                return Shape.rect(x + cx, y + cy, x2 - x1, y2 - y1, r)
            def pads_at(x, y, r=r):
                out = []
                for pnum, _kind, shape, px, py, pw, ph, _drill in spec["pads"]:
                    ox, oy = rot(px, py, r)
                    sh = Shape.circle(x + ox, y + oy, pw / 2) if shape == "circle" else Shape.rect(x + ox, y + oy, pw, ph, r)
                    out.append((sh, pad_nets.get(pnum, "")))
                return out

            spot = find_free_spot(board, court, near_xy, grid_mm=grid_mm, pads_at=pads_at)
            if spot is None:
                continue
            if anchor_pad_local is not None:
                ax, ay = rot(anchor_pad_local[0], anchor_pad_local[1], r)
                score = math.hypot(spot[0] + ax - near_xy[0], spot[1] + ay - near_xy[1])
            else:
                score = math.hypot(spot[0] - near_xy[0], spot[1] - near_xy[1])
            if best is None or score < best[0] - 1e-6:
                best = (score, spot[0], spot[1], r)
        return best
    finally:
        if saved is not None:
            board.footprints = saved
            board._cu = None


# ----------------------------------------------------------------------------- operations
class Patcher:
    def __init__(self, project: Project):
        self.p = project
        self.board: Board = project.board
        self.sch = project.sch
        self.rules = _rules()
        self.changes: dict[str, str] = {}
        self.sch_dirty = False
        self._grid: "RouteGrid | None" = None
        if self.board is None:
            raise PatchError("project has no .kicad_pcb file")

    def grid(self) -> RouteGrid:
        if self._grid is None:
            self._grid = RouteGrid(self.board)
        return self._grid

    def dirty(self) -> None:
        """Copper was removed or moved: the routing grid must be rebuilt."""
        self._grid = None

    def grew(self, *objs) -> None:
        """Copper was only added (new pads / tracks / vias): update the routing grid in place."""
        if self._grid is None:
            return
        for o in objs:
            if hasattr(o, "pads"):
                for p in o.pads:
                    self._grid.add(Cu(p.shape, p.layers, p.net, "hole" if p.kind == "np_thru_hole" else "pad", p.name, p))
            elif hasattr(o, "drill"):
                self._grid.add(Cu(o.shape, o.layers, o.net, "via", "", o))
            else:
                self._grid.add(Cu(o.shape, (o.layer,), o.net, "track", "", o))

    def _mark(self, uuids, kind="added"):
        for u in uuids:
            if u:
                self.changes[u] = kind

    def _pad(self, spec: str):
        pad = self.board.find_pad(str(spec))
        if pad is None:
            raise PatchError(f"unknown pad '{spec}' (use REF.PIN, e.g. U1.2)")
        return pad

    def _do_route(self, net, width=None, only_pads=None, layer=None) -> dict:
        if _is_pour_net(self.board, net):
            return {"net": net, "routed": 0, "failed": [], "note": "connected by the copper pour"}
        w = float(width) if width else default_width(net, self.rules)
        res = _route(self.board, net, w, self.grid(), only_pads=only_pads, layer=layer)
        self._mark(res.pop("new", []))
        if res.get("notes"):
            self.dirty()  # the neck-down fallback re-shaped tracks behind the grid's back
        return res

    # .......................................................................
    def op_set_pin_net(self, ref, pin, net, **_):
        fp = self.board.fp_by_ref.get(ref)
        pads = [p for p in fp.pads if p.num == str(pin)] if fp else []
        if not pads:
            raise PatchError(f"unknown pad {ref}.{pin}")
        old = pads[0].net
        ripped = 0
        for p in pads:
            ripped += ripup_pad(self.board, p)
            self.board.set_pad_net(p, net)
        if self.sch is not None:
            self.sch.set_pin_net(ref, str(pin), net)
            self.sch_dirty = True
        self.changes[f"pad:{ref}.{pin}"] = "modified"
        self.dirty()
        note = ""
        if old and old in ratsnest(self.board):
            res = self._do_route(old)
            note = f"; re-routed {old}" + (" (FAILED)" if res.get("failed") else f" {res.get('length_mm', 0)} mm")
        return {"summary": f"{ref}.{pin}: {old or 'no net'} -> {net} ({ripped} stale segment(s) ripped up{note})", "old_net": old}

    def op_add_component(self, kind="capacitor", value="", pins=None, nets=None, near=None, at=None, package="0603",
                         ref=None, rotation=None, route=True, **_):
        kind = str(kind).lower()
        kind = {"r": "resistor", "c": "capacitor", "res": "resistor", "cap": "capacitor"}.get(kind, kind)
        if kind not in library.PASSIVES:
            raise PatchError(f"kind must be one of {sorted(library.PASSIVES)}")
        part = library.PASSIVES[kind]
        pkg = str(package).upper().replace("C_", "").replace("R_", "")
        fp_name = part["footprints"].get(pkg) or part["footprints"]["0603"]
        if pins is None:
            if not (isinstance(nets, (list, tuple)) and len(nets) == 2):
                raise PatchError("give pins {\"1\": net, \"2\": net} (or nets [net1, net2])")
            pins = {"1": nets[0], "2": nets[1]}
        pins = {str(k): str(v) for k, v in pins.items()}
        if set(pins) != {"1", "2"}:
            raise PatchError("two-terminal parts need nets for pins 1 and 2")
        taken = set(self.board.fp_by_ref)
        if ref and ref in taken:
            raise PatchError(f"reference {ref} already exists")
        if not ref:
            ref = self.sch.next_ref(part["prefix"], taken) if self.sch is not None else next(f"{part['prefix']}{i}" for i in range(1, 999) if f"{part['prefix']}{i}" not in taken)
        spec = library.FOOTPRINTS[fp_name]
        pad_local = {p[0]: (p[3], p[4]) for p in spec["pads"]}
        rots = (0, 90, 180, 270) if rotation is None else (float(rotation) % 360,)
        if at is not None:
            x, y = _xy(at, "at")
            r = rots[0] if rotation is not None else 0
            near_pad = None
        else:
            if not near:
                raise PatchError("give 'near' (REF.PIN) or 'at' [x, y]")
            near_pad = self._pad(near)
            anchor_pin = next((k for k, n in pins.items() if n == near_pad.net), None)
            best = _place(self.board, fp_name, (near_pad.x, near_pad.y), pad_local.get(anchor_pin) if anchor_pin else None, rots, pad_nets=pins)
            if best is None:
                raise PatchError(f"no free spot within 12 mm of {near}")
            _, x, y, r = best
        sym_uuid = None
        if self.sch is not None:
            sx, sy = self.sch.free_slot()
            sym = self.sch.add_symbol(part["symbol"], ref, value, f"{library.LIB}:{fp_name}", sx, sy, pins, project=self.p.name)
            sym_uuid = sym.uuid
            self.sch_dirty = True
            self.changes[f"sch:{ref}"] = "added"
        node = library.footprint_node(fp_name, ref, value, x, y, r, pins, self.board.net_token, sym_uuid, pin_types={"1": "passive", "2": "passive"})
        fp = self.board.add_footprint_node(node)
        self._mark([fp.uuid])
        self.grew(fp)
        out = {"ref": ref, "at": [x, y], "rotation": r, "footprint": fp_name}
        routed = []
        if route:
            for k, n in pins.items():
                res = self._do_route(n, only_pads={f"{ref}.{k}"})
                if res.get("failed"):
                    routed.append(f"{n}: FAILED")
                elif res.get("routed"):
                    routed.append(f"{n}: {res['length_mm']} mm")
                else:
                    routed.append(f"{n}: {'pour' if res.get('note') else 'already touching'}")
        dist = f", {math.hypot(x - near_pad.x, y - near_pad.y):.1f} mm from {near}" if near_pad is not None else ""
        out["summary"] = f"added {ref} {value} ({fp_name}) at ({x:g}, {y:g}){dist}" + (f"; routed {', '.join(routed)}" if routed else "")
        return out

    def op_route_net(self, net, width=None, layer=None, **_):
        if net not in self.board.net_numbers and net not in self.board.net_names():
            raise PatchError(f"unknown net '{net}'")
        res = self._do_route(net, width, layer=layer)
        if res.get("failed"):
            res["summary"] = f"{net}: routed {res['routed']}, FAILED to reach {', '.join(res['failed'])}"
        elif res.get("note"):
            res["summary"] = f"{net}: {res['note']}"
        else:
            res["summary"] = f"{net}: routed {res['routed']} connection(s), {res['length_mm']} mm, {res['vias']} via(s), width {res['width_mm']} mm"
        return res

    def op_ripup_net(self, net, **_):
        tracks = [t for t in self.board.tracks if t.net == net]
        vias = [v for v in self.board.vias if v.net == net]
        for t in tracks:
            self.board.remove_track(t)
        if not self.board.zone_layers(net):
            for v in vias:
                self.board.remove_via(v)
        self.dirty()
        return {"summary": f"{net}: removed {len(tracks)} segment(s)"}

    def op_reroute_net(self, net, width=None, **_):
        self.op_ripup_net(net)
        return self.op_route_net(net, width)

    def op_set_track_width(self, net, width, **_):
        width = float(width)
        prof = self.rules["profile"]
        if width < prof["min_track_mm"]:
            raise PatchError(f"{width} mm is below the fab minimum {prof['min_track_mm']} mm")
        tracks = [t for t in self.board.tracks if t.net == net]
        if not tracks:
            raise PatchError(f"net '{net}' has no tracks")
        before = min(t.width for t in tracks)
        narrow = [t for t in tracks if t.width < width - 1e-9]
        res = widen_tracks(self.board, narrow, width)
        self._mark([t.uuid for t in narrow], "modified")
        self.grew(*narrow)
        note = f"; {res['necked']} pad-entry segment(s) kept narrower to hold clearance" if res["necked"] else ""
        return {"summary": f"{net}: {res['widened']} of {len(narrow)} segment(s) widened {before:g} -> {width:g} mm{note}", **res}

    def _find_via(self, at, net=None):
        x, y = _xy(at, "at")
        cands = [v for v in self.board.vias if (net is None or v.net == net)]
        if not cands:
            raise PatchError("board has no matching via")
        v = min(cands, key=lambda q: math.hypot(q.x - x, q.y - y))
        if math.hypot(v.x - x, v.y - y) > 1.0:
            raise PatchError(f"no via within 1 mm of ({x:g}, {y:g})")
        return v

    def op_delete_via(self, at, net=None, **_):
        v = self._find_via(at, net)
        self.board.remove_via(v)
        self.dirty()
        return {"summary": f"deleted {v.net} via at ({v.x:g}, {v.y:g})"}

    def op_move_via(self, at, to=None, net=None, **_):
        v = self._find_via(at, net)
        old = (v.x, v.y)
        attached = [t for t in self.board.tracks if t.net == v.net and shape_dist(t.shape, v.shape) <= TOUCH]
        need = max(SAFE_CLEARANCE, self.rules["profile"]["min_clearance_mm"]) + 0.05
        edge = self.rules["profile"]["copper_to_edge_mm"]
        ox1, oy1, ox2, oy2 = self.board.outline
        self.board.remove_via(v)

        def legal(x, y):
            if x - v.size / 2 < ox1 + edge or y - v.size / 2 < oy1 + edge or x + v.size / 2 > ox2 - edge or y + v.size / 2 > oy2 - edge:
                return False
            return _clears(self.board, Shape.circle(x, y, v.size / 2), CU_LAYERS, v.net, need)[0]

        if to is not None:
            nx, ny = _xy(to, "to")
            if not legal(nx, ny):
                self.board.add_via(v.net, old[0], old[1], v.size, v.drill)
                raise PatchError(f"target ({nx:g}, {ny:g}) violates clearance; omit 'to' to auto-place")
        else:
            g = 0.25
            cx, cy = snap(old[0], g), snap(old[1], g)
            cands = sorted(
                (math.hypot(cx + i * g - old[0], cy + j * g - old[1]), cx + i * g, cy + j * g)
                for i in range(-12, 13) for j in range(-12, 13)
            )
            spot = next(((x, y) for _, x, y in cands if legal(x, y)), None)
            if spot is None:
                self.board.add_via(v.net, old[0], old[1], v.size, v.drill)
                raise PatchError("no legal spot within 3 mm; delete_via instead")
            nx, ny = spot
        nv = self.board.add_via(v.net, nx, ny, v.size, v.drill)
        self._mark([nv.uuid], "moved")
        for layer in sorted({t.layer for t in attached}):
            w = max(t.width for t in attached if t.layer == layer)
            t = self.board.add_track(v.net, layer, old[0], old[1], nx, ny, w)
            self._mark([t.uuid])
        self.dirty()
        return {"summary": f"moved {v.net} via ({old[0]:g}, {old[1]:g}) -> ({nx:g}, {ny:g}), now >= {need:.2f} mm from other nets", "to": [nx, ny]}

    def op_add_via(self, net, at, size=0.6, drill=0.3, **_):
        x, y = _xy(at, "at")
        ok, why = _clears(self.board, Shape.circle(x, y, float(size) / 2), CU_LAYERS, net, self.rules["profile"]["min_clearance_mm"])
        if not ok:
            raise PatchError(f"via would violate clearance ({why})")
        v = self.board.add_via(net, x, y, float(size), float(drill))
        self._mark([v.uuid])
        self.grew(v)
        return {"summary": f"added {net} via at ({x:g}, {y:g})"}

    def op_add_track(self, net, points, layer="F.Cu", width=None, **_):
        if layer not in CU_LAYERS:
            raise PatchError("layer must be F.Cu or B.Cu")
        pts = [_xy(p) for p in points]
        if len(pts) < 2:
            raise PatchError("need at least two points")
        w = float(width) if width else default_width(net, self.rules)
        clr = self.rules["profile"]["min_clearance_mm"]
        for a, b in zip(pts, pts[1:]):
            ok, why = _clears(self.board, Shape.capsule(*a, *b, w), (layer,), net, clr)
            if not ok:
                raise PatchError(f"segment ({a[0]:g},{a[1]:g})-({b[0]:g},{b[1]:g}) violates clearance ({why}); use route_net instead")
        total = 0.0
        for a, b in zip(pts, pts[1:]):
            t = self.board.add_track(net, layer, *a, *b, w)
            total += t.length
            self._mark([t.uuid])
            self.grew(t)
        return {"summary": f"{net}: added {len(pts) - 1} segment(s), {total:.2f} mm on {layer}"}

    def op_move_footprint(self, ref, near=None, to=None, rotation=None, reroute=True, **_):
        f = self.board.fp_by_ref.get(ref)
        if f is None:
            raise PatchError(f"unknown footprint '{ref}'")
        fp_name = f.lib.split(":")[-1]
        old = (f.x, f.y)
        nets = sorted({p.net for p in f.pads if p.net})
        ripped = sum(ripup_pad(self.board, p) for p in f.pads)
        if to is not None:
            x, y = _xy(to, "to")
            x, y = snap(x, 0.25), snap(y, 0.25)
            r = f.rot if rotation is None else float(rotation)
        else:
            if not near:
                x, y, r = snap(f.x, 0.25), snap(f.y, 0.25), f.rot if rotation is None else float(rotation)
            else:
                if fp_name not in library.FOOTPRINTS:
                    raise PatchError(f"{ref}: only ClawBoard library footprints can be auto-placed; give 'to' [x, y]")
                npad = self._pad(near)
                spec = library.FOOTPRINTS[fp_name]
                anchor = next(((p[3], p[4]) for p in spec["pads"] if (f.pad(p[0]) and f.pad(p[0]).net == npad.net)), None)
                rots = (0, 90, 180, 270) if rotation is None else (float(rotation) % 360,)
                best = _place(self.board, fp_name, (npad.x, npad.y), anchor, rots, ignore_ref=ref, pad_nets={p.num: p.net for p in f.pads})
                if best is None:
                    raise PatchError(f"no free spot within 12 mm of {near}")
                _, x, y, r = best
        nf = self.board.move_footprint(f, x, y, r)
        self._mark([nf.uuid], "moved")
        self.dirty()
        notes = []
        if reroute:
            for n in nets:
                res = self._do_route(n)
                if res.get("failed"):
                    notes.append(f"{n}: FAILED")
                elif res.get("routed"):
                    notes.append(f"{n}: {res['length_mm']} mm")
        return {"summary": f"moved {ref} ({old[0]:g}, {old[1]:g}) -> ({x:g}, {y:g}) rot {r:g}; ripped {ripped} segment(s)"
                           + (f"; rerouted {', '.join(notes)}" if notes else ""), "at": [x, y]}

    def op_stitch_pour(self, net="GND", **_):
        res = stitch_pour(self.board, net)
        self._mark(res["new"])
        self.dirty()
        left = f"; still isolated: {', '.join(g['pads'][0] for g in res['stranded'])}" if res["stranded"] else ""
        return {"summary": f"{net} pour: added {len(res['vias'])} stitching via(s), {res['links']} ground link(s){left}"}

    def op_rename_net(self, old, new, **_):
        b = self.board
        if old not in b.net_numbers:
            raise PatchError(f"unknown net '{old}'")
        if new in b.net_numbers:
            raise PatchError(f"net '{new}' already exists")
        k = b.net_numbers[old]
        for node in b.tree:
            if isinstance(node, list) and node and node[0] == "net" and len(node) >= 3 and int(node[1]) == k:
                node[2] = new
            elif isinstance(node, list) and node and node[0] == "zone":
                nn = find(node, "net_name")
                if nn and nn[1] == old:
                    nn[1] = new
            elif isinstance(node, list) and node and node[0] == "footprint":
                for c in node:
                    if isinstance(c, list) and c and c[0] == "pad":
                        n = find(c, "net")
                        if n and len(n) >= 3 and n[2] == old:
                            n[2] = new
        b.index()
        n = self.sch.rename_net(old, new) if self.sch is not None else 0
        self.sch_dirty = True
        self.dirty()
        return {"summary": f"renamed net {old} -> {new} ({n} schematic label(s))"}

    def op_set_value(self, ref, value, **_):
        f = self.board.fp_by_ref.get(ref)
        if f is None:
            raise PatchError(f"unknown footprint '{ref}'")
        old = f.value
        for p in f.node:
            if isinstance(p, list) and p and p[0] == "property" and p[1] == "Value":
                p[2] = str(value)
        f.value = str(value)
        if self.sch is not None and ref in self.sch.by_ref:
            for p in self.sch.by_ref[ref].node:
                if isinstance(p, list) and p and p[0] == "property" and p[1] == "Value":
                    p[2] = str(value)
            self.sch.index()
            self.sch_dirty = True
        self._mark([f.uuid], "modified")
        return {"summary": f"{ref}: value {old} -> {value}"}


OPS = {name[3:]: name for name in dir(Patcher) if name.startswith("op_")}


def apply_patch(project_dir, operations, on_progress=None, on_done=None) -> dict:
    if isinstance(operations, dict):
        operations = operations.get("operations") or [operations]
    if not isinstance(operations, list) or not operations:
        raise PatchError("operations must be a non-empty list of {op: ...} objects")
    proj = Project(project_dir)
    patcher = Patcher(proj)
    results = []
    applied = 0
    last = run_audit(proj, quick=True)
    steps = proj.read_state("steps.json", None) or []
    sch_touched = False
    for op in operations:
        name = str(op.get("op") or op.get("type") or op.get("action") or "") if isinstance(op, dict) else ""
        entry = {"op": name or "?"}
        try:
            if name not in OPS:
                raise PatchError(f"unknown op '{name}'. Valid: {', '.join(sorted(OPS))}")
            args = {k: v for k, v in op.items() if k not in ("op", "type", "action")}
            res = getattr(patcher, OPS[name])(**args)
            entry["ok"] = True
            entry["result"] = res.get("summary", "done")
            applied += 1
            # Commit after every operation and measure its effect: the live preview updates
            # step by step, and every step is logged with the error count before and after.
            # (the .kicad files themselves are written once, atomically, when the whole patch is done)
            proj.mark_changed(patcher.changes)
            sch_touched = sch_touched or patcher.sch_dirty
            now = run_audit(proj, quick=True)
            before_ids = {i["id"] for i in last["issues"]}
            after_ids = {i["id"] for i in now["issues"]}
            entry["errors_after"] = now["summary"]["errors"]
            if before_ids - after_ids:
                entry["resolved"] = sorted(before_ids - after_ids)
            if after_ids - before_ids:
                entry["introduced"] = sorted(after_ids - before_ids)
            steps.append({"step": len(steps) + 1, "op": name, "summary": entry["result"],
                          "errors_before": last["summary"]["errors"], "errors_after": now["summary"]["errors"],
                          "warnings_after": now["summary"]["warnings"], "resolved": entry.get("resolved", []),
                          "introduced": entry.get("introduced", []), "ts": round(time.time() * 1000)})
            proj.write_state("steps.json", steps)
            last = now
            proj.log_event({"tool": "apply_pcb_patch", "phase": "op", "ok": True, "brief": f"{name}: {entry['result']}",
                            "errors": now["summary"]["errors"], "resolved": entry.get("resolved", [])})
            if on_progress:
                on_progress(proj, patcher.sch_dirty)
            patcher.changes = {}
            patcher.sch_dirty = False
        except PatchError as e:
            entry["ok"] = False
            entry["error"] = str(e)
        except TypeError as e:
            entry["ok"] = False
            entry["error"] = f"bad arguments for {name}: {e}"
        except (KeyError, ValueError) as e:
            entry["ok"] = False
            entry["error"] = str(e)
        if not entry["ok"]:
            proj.log_event({"tool": "apply_pcb_patch", "phase": "op", "ok": False, "brief": f"{name}: {entry['error']}"})
        results.append(entry)
    board = proj.board
    pour_note = None
    if applied:
        # tracks added above may have cut the ground pour: re-attach any stranded pads, then run the full audit
        has_pour = "GND" in {z.net for z in board.zones}
        st = stitch_pour(board, "GND") if has_pour else {"new": [], "vias": [], "links": 0, "stranded": None}
        if st["new"]:
            proj.mark_changed({u: "added" for u in st["new"]})
            pour_note = f"kept the GND pour connected: {len(st['vias'])} stitching via(s), {st['links']} ground link(s)"
            proj.log_event({"tool": "apply_pcb_patch", "phase": "op", "ok": True, "brief": pour_note})
        proj.save(sch=sch_touched)
        last = run_audit(proj, pour={"GND": st["stranded"]} if has_pour else None)
    rn = ratsnest(board)
    if on_done:
        on_done(proj)
    out_extra = {"pour": pour_note} if pour_note else {}
    return {
        **out_extra,
        "applied": applied,
        "failed": len(results) - applied,
        "results": results,
        "unrouted_connections": sum(len(v["missing"]) for v in rn.values()),
        "board": {"footprints": len(board.footprints), "tracks": len(board.tracks), "vias": len(board.vias)},
        "audit": {"pass": last["pass"], "errors": last["summary"]["errors"], "warnings": last["summary"]["warnings"],
                  "remaining": [f"{i['id']}: {i['title']}" for i in last["issues"] if i["severity"] != "info"]},
    }
