"""Headless 2-layer maze router (A* on a 0.25 mm grid, Manhattan + 45 degree moves).

The LLM decides *what* to route; this module decides *where* the copper goes so the
result is always clearance-clean.  Every candidate path is re-verified with exact
geometry before it is committed to the board.
"""
from __future__ import annotations

import math
from heapq import heappop, heappush

from .geom import Shape, nearest_on_seg, pt_seg_dist, shape_dist
from .pcb import CU_LAYERS, TOUCH, Board, Cu, net_groups

BIG = 1e9
_DX = (1, 1, 0, -1, -1, -1, 0, 1)
_DY = (0, 1, 1, 1, 0, -1, -1, -1)
_TURN = (0.0, 0.12, 0.6)  # extra cost for 0 / 45 / 90 degree bends (sharper is forbidden)


class RouteGrid:
    """Per layer, per cell: distance to the nearest copper and to the nearest copper of another net."""

    def __init__(self, board: Board, pitch: float = 0.25, reach: float = 0.8):
        self.board, self.g, self.R = board, pitch, reach
        x1, y1, x2, y2 = board.outline
        self.ox, self.oy = x1, y1
        self.W = int(round((x2 - x1) / pitch)) + 1
        self.H = int(round((y2 - y1) / pitch)) + 1
        n = self.W * self.H
        self.d1 = {L: [BIG] * n for L in CU_LAYERS}
        self.n1 = {L: [None] * n for L in CU_LAYERS}
        self.d2 = {L: [BIG] * n for L in CU_LAYERS}
        for c in board.copper():
            self.add(c)

    def cell_xy(self, idx: int) -> tuple[float, float]:
        return self.ox + (idx % self.W) * self.g, self.oy + (idx // self.W) * self.g

    def _range(self, bbox, reach):
        g, W, H = self.g, self.W, self.H
        ix0 = max(0, int(math.ceil((bbox[0] - reach - self.ox) / g - 1e-9)))
        ix1 = min(W - 1, int(math.floor((bbox[2] + reach - self.ox) / g + 1e-9)))
        iy0 = max(0, int(math.ceil((bbox[1] - reach - self.oy) / g - 1e-9)))
        iy1 = min(H - 1, int(math.floor((bbox[3] + reach - self.oy) / g + 1e-9)))
        return ix0, ix1, iy0, iy1

    def add(self, c: Cu) -> None:
        net = c.net if (c.net and c.kind != "hole") else None
        sh = c.shape
        R, g, W, ox, oy = self.R, self.g, self.W, self.ox, self.oy
        ix0, ix1, iy0, iy1 = self._range(sh.bbox, R)
        pts, r, n = sh.pts, sh.r, len(sh.pts)
        ab = sh.aabb
        layers = [L for L in c.layers if L in self.d1]
        hyp = math.hypot
        for iy in range(iy0, iy1 + 1):
            cy = oy + iy * g
            base = iy * W
            for ix in range(ix0, ix1 + 1):
                cx = ox + ix * g
                if ab is not None:
                    dx = abs(cx - ab[0]) - ab[2]
                    dy = abs(cy - ab[1]) - ab[3]
                    if dx <= 0.0:
                        d = (dy if dy > 0.0 else 0.0) - r
                    elif dy <= 0.0:
                        d = dx - r
                    else:
                        d = hyp(dx, dy) - r
                elif n == 1:
                    d = hyp(cx - pts[0][0], cy - pts[0][1]) - r
                elif n == 2:
                    d = pt_seg_dist(cx, cy, pts[0][0], pts[0][1], pts[1][0], pts[1][1]) - r
                else:
                    d = sh.core_dist_point(cx, cy) - r
                if d >= R:
                    continue
                i = base + ix
                for L in layers:
                    d1, n1, d2 = self.d1[L], self.n1[L], self.d2[L]
                    if n1[i] == net and net is not None:
                        if d < d1[i]:
                            d1[i] = d
                    elif d < d1[i]:
                        d2[i] = d1[i]
                        d1[i] = d
                        n1[i] = net
                    elif d < d2[i]:
                        d2[i] = d

    def free_mask(self, net: str, layer: str, radius: float, edge: float) -> bytearray:
        """1 where a copper disc of ``radius`` centred on the cell clears every other net."""
        d1, n1, d2 = self.d1[layer], self.n1[layer], self.d2[layer]
        m = bytearray(1 if (a if k != net else b) >= radius else 0 for a, k, b in zip(d1, n1, d2))
        W, H = self.W, self.H
        k = max(1, int(math.ceil(edge / self.g - 1e-9)))
        zero_row = bytes(W)
        for iy in range(H):
            if iy < k or iy >= H - k:
                m[iy * W:(iy + 1) * W] = zero_row
            else:
                m[iy * W:iy * W + k] = bytes(k)
                m[(iy + 1) * W - k:(iy + 1) * W] = bytes(k)
        return m

    def cells_in(self, c: Cu) -> dict[tuple[int, int], tuple[float, float]]:
        """Grid cells that sit on this copper item -> anchor point the track must end on."""
        out: dict[tuple[int, int], tuple[float, float]] = {}
        sh = c.shape
        tol = 0.0
        if c.kind == "track":
            tol = max(0.0, 0.18 - sh.r)
        ix0, ix1, iy0, iy1 = self._range(sh.bbox, tol)
        for iy in range(iy0, iy1 + 1):
            cy = self.oy + iy * self.g
            for ix in range(ix0, ix1 + 1):
                cx = self.ox + ix * self.g
                if sh.dist_point(cx, cy) > tol + 1e-6:
                    continue
                if c.kind == "track":
                    anchor = nearest_on_seg(cx, cy, *sh.pts[0], *sh.pts[1])
                else:
                    anchor = (c.obj.x, c.obj.y)
                for L in c.layers:
                    if L in self.d1:
                        out[(CU_LAYERS.index(L), iy * self.W + ix)] = anchor
        return out


def _astar(grid: RouteGrid, src, dst, free, viafree, layer_cost, via_cost, max_pops=120_000, weight=1.6):
    W, H = grid.W, grid.H
    WH = W * H
    g = grid.g
    gw = g * weight  # weight > 1: trades a few percent of track length for a much smaller search
    step = (g, g * 1.41421356)
    dst_cells = {L * WH + idx for (L, idx) in dst}
    if not dst_cells:
        return None
    txs = [(c % WH) % W for c in dst_cells]
    tys = [(c % WH) // W for c in dst_cells]
    tx0, tx1, ty0, ty1 = min(txs), max(txs), min(tys), max(tys)
    INF = float("inf")
    best: dict[int, float] = {}
    parent: dict[int, int] = {}
    heap: list = []
    for (L, idx) in src:
        if not free[L][idx]:
            continue
        s = (L * WH + idx) * 9 + 8
        best[s] = 0.0
        ix, iy = idx % W, idx // W
        dx = tx0 - ix if ix < tx0 else (ix - tx1 if ix > tx1 else 0)
        dy = ty0 - iy if iy < ty0 else (iy - ty1 if iy > ty1 else 0)
        h = (dx + 0.41421356 * dy if dx > dy else dy + 0.41421356 * dx) * gw
        heappush(heap, (h, 0.0, s))
    pops = 0
    goal = None
    two_layers = viafree is not None
    while heap:
        _, gc, s = heappop(heap)
        if best.get(s, INF) < gc:
            continue
        cell = s // 9
        d = s - cell * 9
        if cell in dst_cells:
            goal = s
            break
        pops += 1
        if pops > max_pops:
            break
        L = cell // WH
        idx = cell - L * WH
        ix, iy = idx % W, idx // W
        fl = free[L]
        lc = layer_cost[L]
        for nd in range(8):
            if d != 8:
                turn = nd - d
                if turn < 0:
                    turn = -turn
                if turn > 4:
                    turn = 8 - turn
                if turn > 2:
                    continue
                tc = _TURN[turn]
            else:
                tc = 0.0
            nx, ny = ix + _DX[nd], iy + _DY[nd]
            nidx = ny * W + nx
            if not fl[nidx]:
                continue
            if nd & 1 and not (fl[iy * W + nx] and fl[ny * W + ix]):
                continue
            ng = gc + step[nd & 1] * lc + tc
            ns = (L * WH + nidx) * 9 + nd
            if ng < best.get(ns, INF):
                best[ns] = ng
                parent[ns] = s
                dx = tx0 - nx if nx < tx0 else (nx - tx1 if nx > tx1 else 0)
                dy = ty0 - ny if ny < ty0 else (ny - ty1 if ny > ty1 else 0)
                h = (dx + 0.41421356 * dy if dx > dy else dy + 0.41421356 * dx) * gw
                heappush(heap, (ng + h, ng, ns))
        if two_layers and viafree[idx]:
            oL = 1 - L
            if free[oL][idx]:
                ng = gc + via_cost
                ns = (oL * WH + idx) * 9 + 8
                if ng < best.get(ns, INF):
                    best[ns] = ng
                    parent[ns] = s
                    dx = tx0 - ix if ix < tx0 else (ix - tx1 if ix > tx1 else 0)
                    dy = ty0 - iy if iy < ty0 else (iy - ty1 if iy > ty1 else 0)
                    h = (dx + 0.41421356 * dy if dx > dy else dy + 0.41421356 * dx) * gw
                    heappush(heap, (ng + h, ng, ns))
    if goal is None:
        return None
    path = []
    s = goal
    while True:
        cell = s // 9
        L = cell // WH
        idx = cell - L * WH
        if not path or path[-1] != (L, idx):
            path.append((L, idx))
        if s not in parent:
            break
        s = parent[s]
    path.reverse()
    return path


def _path_to_primitives(grid: RouteGrid, path, src, dst):
    """Grid path -> ([(layer, x1, y1, x2, y2)], [(x, y) vias])."""
    segs, vias = [], []
    pts = [(L, *grid.cell_xy(idx)) for (L, idx) in path]
    a = src.get(path[0])
    if a and math.hypot(a[0] - pts[0][1], a[1] - pts[0][2]) > 1e-6:
        segs.append((pts[0][0], a[0], a[1], pts[0][1], pts[0][2]))
    run_start = 0
    for i in range(1, len(pts) + 1):
        if i == len(pts) or pts[i][0] != pts[run_start][0]:
            run = pts[run_start:i]
            L = run[0][0]
            j = 0
            while j < len(run) - 1:
                k = j + 1
                ddx, ddy = run[k][1] - run[j][1], run[k][2] - run[j][2]
                while k + 1 < len(run):
                    ex, ey = run[k + 1][1] - run[k][1], run[k + 1][2] - run[k][2]
                    if abs(ex - ddx) > 1e-9 or abs(ey - ddy) > 1e-9:
                        break
                    k += 1
                segs.append((L, run[j][1], run[j][2], run[k][1], run[k][2]))
                j = k
            if i < len(pts):
                vias.append((pts[i][1], pts[i][2]))
            run_start = i
    b = dst.get(path[-1])
    if b and math.hypot(b[0] - pts[-1][1], b[1] - pts[-1][2]) > 1e-6:
        segs.append((pts[-1][0], pts[-1][1], pts[-1][2], b[0], b[1]))
    return segs, vias


def _verify(board: Board, net, segs, vias, width, via_size, hard_clearance):
    """Exact-geometry check of a candidate path.  Returns a list of (layer, x, y) trouble spots."""
    bad = []
    others = [c for c in board.copper() if c.net != net or c.kind == "hole" or not c.net]
    shapes = [((CU_LAYERS[L],), Shape.capsule(x1, y1, x2, y2, width)) for (L, x1, y1, x2, y2) in segs]
    shapes += [(CU_LAYERS, Shape.circle(x, y, via_size / 2)) for (x, y) in vias]
    for layers, sh in shapes:
        b = sh.bbox
        for o in others:
            ob = o.shape.bbox
            if ob[0] > b[2] + hard_clearance or ob[2] < b[0] - hard_clearance or ob[1] > b[3] + hard_clearance or ob[3] < b[1] - hard_clearance:
                continue
            if not set(layers) & set(o.layers):
                continue
            if shape_dist(sh, o.shape) < hard_clearance - 1e-4:
                cx, cy = sh.center()
                ox, oy = o.shape.center()
                # closest point of the new copper to the offender
                if len(sh.pts) == 2:
                    cx, cy = nearest_on_seg(ox, oy, *sh.pts[0], *sh.pts[1])
                for L in layers:
                    bad.append((CU_LAYERS.index(L), cx, cy))
    return bad


def route_net(
    board: Board,
    net: str,
    width: float = 0.25,
    clearance: float = 0.2,
    via_size: float = 0.6,
    via_drill: float = 0.3,
    layers: "tuple[str, ...]" = CU_LAYERS,
    prefer: str = "F.Cu",
    hard_clearance: float = 0.13,
    edge_clearance: float = 0.3,
    grid: "RouteGrid | None" = None,
    only_pads: "set[str] | None" = None,
    limit: "int | None" = None,
    effort: int = 120_000,
    weight: float = 1.6,
) -> dict:
    """Route every missing connection of ``net``.  Returns a summary + the new item uuids."""
    result = {"net": net, "routed": 0, "failed": [], "length_mm": 0.0, "vias": 0, "width_mm": width, "new": []}
    if grid is None:
        grid = RouteGrid(board)
    skipped: set[int] = set()
    attempts = [(width, clearance)]
    if clearance > 0.15:
        attempts.append((width, 0.15))
    if width > 0.2:
        attempts.append((0.2, 0.15))
    guard = 0
    while guard < 64:
        guard += 1
        if limit is not None and result["routed"] >= limit:
            break
        groups = [g for g in net_groups(board, net) if any(c.kind == "pad" for c in g)]
        if len(groups) <= 1:
            break
        keyed = []
        for g in groups:
            key = min(id(c.obj) for c in g)
            if key in skipped:
                continue
            if only_pads is not None and not any(c.kind == "pad" and c.label in only_pads for c in g):
                continue
            keyed.append((key, g))
        if not keyed:
            break
        # the island that is cheapest to reach from any other island goes first
        best = None
        for key, g in keyed:
            pads = [c for c in g if c.kind == "pad"]
            dmin = BIG
            for other in groups:
                if other is g:
                    continue
                for a in pads:
                    for b in other:
                        if b.kind == "track":
                            d = pt_seg_dist(a.obj.x, a.obj.y, b.obj.x1, b.obj.y1, b.obj.x2, b.obj.y2)
                        else:
                            d = math.hypot(a.obj.x - b.obj.x, a.obj.y - b.obj.y)
                        if d < dmin:
                            dmin = d
            if best is None or dmin < best[0]:
                best = (dmin, key, g)
        _, key, target_group = best
        dst: dict = {}
        for c in target_group:
            dst.update(grid.cells_in(c))
        src: dict = {}
        for g in groups:
            if g is target_group:
                continue
            for c in g:
                src.update(grid.cells_in(c))
        own_smd = set()
        for c in board.copper():
            if c.net == net and c.kind == "pad" and c.obj.kind == "smd":
                own_smd.update(idx for (_, idx) in grid.cells_in(c))
        done = False
        for (w, clr) in attempts:
            rt = w / 2 + clr
            rv = via_size / 2 + clr
            free = [grid.free_mask(net, L, rt, edge_clearance + w / 2) if L in layers else bytearray(grid.W * grid.H) for L in CU_LAYERS]
            viafree = None
            if len(layers) == 2:
                va = grid.free_mask(net, "F.Cu", rv, edge_clearance + via_size / 2)
                vb = grid.free_mask(net, "B.Cu", rv, edge_clearance + via_size / 2)
                viafree = bytearray(a & b for a, b in zip(va, vb))
                for idx in own_smd:
                    viafree[idx] = 0
            layer_cost = [1.0 if L == prefer else 1.3 for L in CU_LAYERS]
            for _retry in range(6):
                s_ok = {k: v for k, v in src.items() if free[k[0]][k[1]]}
                d_ok = {k: v for k, v in dst.items() if free[k[0]][k[1]]}
                if not s_ok or not d_ok:
                    break
                path = _astar(grid, s_ok, d_ok, free, viafree, layer_cost, via_cost=5.0, max_pops=effort, weight=weight)
                if path is None:
                    break
                segs, vias = _path_to_primitives(grid, path, s_ok, d_ok)
                bad = _verify(board, net, segs, vias, w, via_size, hard_clearance)
                if bad:
                    for (L, bx, by) in bad:
                        ix = int(round((bx - grid.ox) / grid.g))
                        iy = int(round((by - grid.oy) / grid.g))
                        for yy in range(max(0, iy - 1), min(grid.H, iy + 2)):
                            for xx in range(max(0, ix - 1), min(grid.W, ix + 2)):
                                free[L][yy * grid.W + xx] = 0
                                if viafree is not None:
                                    viafree[yy * grid.W + xx] = 0
                    continue
                for (L, x1, y1, x2, y2) in segs:
                    t = board.add_track(net, CU_LAYERS[L], x1, y1, x2, y2, w)
                    grid.add(Cu(t.shape, (t.layer,), net, "track", "", t))
                    result["length_mm"] += t.length
                    result["new"].append(t.uuid)
                for (x, y) in vias:
                    v = board.add_via(net, x, y, via_size, via_drill)
                    grid.add(Cu(v.shape, v.layers, net, "via", "", v))
                    result["vias"] += 1
                    result["new"].append(v.uuid)
                result["routed"] += 1
                if w != width:
                    result.setdefault("notes", []).append(f"necked down to {w} mm near fine-pitch pads")
                done = True
                break
            if done:
                break
        if not done:
            skipped.add(key)
            pads = sorted(c.label for c in target_group if c.kind == "pad")
            result["failed"].append(pads[0] if pads else "?")
    result["length_mm"] = round(result["length_mm"], 2)
    return result


def find_free_spot(board: Board, shape_at, near: tuple[float, float], grid_mm: float = 0.5, max_radius: float = 12.0, margin: float = 0.25, pads_at=None):
    """Spiral search for the grid point closest to ``near`` where the part fits.

    ``shape_at(x, y)`` is the candidate courtyard: it must clear other courtyards, drill
    holes and the board edge.  ``pads_at(x, y)`` -> [(Shape, net)] are the candidate pads:
    each must clear all copper of *other* nets, so a decoupling capacitor may sit right on
    top of the rail it decouples but can never short two nets.
    """
    x1, y1, x2, y2 = board.outline
    courts = [f.court_shape() for f in board.footprints]
    copper = [c for c in board.copper() if c.kind in ("track", "via", "hole")]
    holes = [c for c in copper if c.kind == "hole"]
    cx, cy = round(near[0] / grid_mm) * grid_mm, round(near[1] / grid_mm) * grid_mm
    n = int(max_radius / grid_mm)
    cands = []
    for dy in range(-n, n + 1):
        for dx in range(-n, n + 1):
            x, y = cx + dx * grid_mm, cy + dy * grid_mm
            cands.append((math.hypot(x - near[0], y - near[1]), x, y))
    cands.sort()
    for d, x, y in cands:
        if d > max_radius:
            break
        sh = shape_at(x, y)
        b = sh.bbox
        if b[0] < x1 + 0.5 or b[1] < y1 + 0.5 or b[2] > x2 - 0.5 or b[3] > y2 - 0.5:
            continue
        ok = True
        for c in courts:
            cb = c.bbox
            if cb[0] > b[2] + margin or cb[2] < b[0] - margin or cb[1] > b[3] + margin or cb[3] < b[1] - margin:
                continue
            if shape_dist(sh, c) < margin:
                ok = False
                break
        if not ok:
            continue
        for c in (holes if pads_at is not None else copper):
            cb = c.shape.bbox
            if cb[0] > b[2] + 0.2 or cb[2] < b[0] - 0.2 or cb[1] > b[3] + 0.2 or cb[3] < b[1] - 0.2:
                continue
            if shape_dist(sh, c.shape) < 0.15:
                ok = False
                break
        if ok and pads_at is not None:
            for psh, pnet in pads_at(x, y):
                pb = psh.bbox
                for c in copper:
                    if c.net == pnet and c.net and c.kind != "hole":
                        continue
                    cb = c.shape.bbox
                    if cb[0] > pb[2] + 0.25 or cb[2] < pb[0] - 0.25 or cb[1] > pb[3] + 0.25 or cb[3] < pb[1] - 0.25:
                        continue
                    if shape_dist(psh, c.shape) < 0.2:
                        ok = False
                        break
                if not ok:
                    break
        if ok:
            return round(x, 4), round(y, 4)
    return None
