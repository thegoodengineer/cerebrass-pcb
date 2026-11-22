"""Copper-pour connectivity: find ground-plane islands and re-attach them.

Every new track cuts the F.Cu / B.Cu pours a little more.  A pad that ends up on a
pour island with no path to the main plane is *unconnected* as far as KiCad's DRC is
concerned, so the tools model the fill conservatively (0.25 mm grid, 4-connected,
clearance + half the minimum fill width) and repair it with stitching vias or, where a
via cannot fit (fine-pitch IC ground pins), a short ground track out to the plane.
"""
from __future__ import annotations

import math
from collections import deque

from .geom import shape_dist
from .pcb import CU_LAYERS, TOUCH, Board, Cu
from .router import RouteGrid, _astar, _path_to_primitives, _verify

FILL_RADIUS = 0.35      # zone clearance 0.2 + margin, so every neck we count is >= 0.2 mm wide
VIA_RADIUS = 0.5        # 0.6 mm via + 0.2 mm clearance
EDGE = 0.55
LINK_WIDTH = 0.25


def _components(mask: bytearray, W: int, H: int) -> tuple[list[int], list[int]]:
    n_cells = W * H
    comp = [-1] * n_cells
    sizes: list[int] = []
    for start in range(n_cells):
        if not mask[start] or comp[start] != -1:
            continue
        cid = len(sizes)
        comp[start] = cid
        n = 0
        q = deque([start])
        while q:
            i = q.popleft()
            n += 1
            x = i % W
            for j in (i - W, i + W, i - 1 if x else -1, i + 1 if x < W - 1 else -1):
                if 0 <= j < n_cells and mask[j] and comp[j] == -1:
                    comp[j] = cid
                    q.append(j)
        sizes.append(n)
    return comp, sizes


class PourModel:
    def __init__(self, board: Board, net: str = "GND", grid: "RouteGrid | None" = None):
        self.board, self.net = board, net
        self.layers = [L for L in CU_LAYERS if L in board.zone_layers(net)]
        self.grid = grid or RouteGrid(board)
        g = self.grid
        self.comp: dict[str, list[int]] = {}
        self.sizes: dict[str, list[int]] = {}
        for L in self.layers:
            mask = g.free_mask(net, L, FILL_RADIUS, EDGE)
            self.comp[L], self.sizes[L] = _components(mask, g.W, g.H)
        self.parent: dict = {}
        self.items = [c for c in board.copper() if c.net == net and c.kind in ("pad", "via", "track")]
        self.touch: dict[int, set] = {}
        for c in self.items:
            me = ("item", id(c))
            self._find(me)
            nodes = self._touching(c)
            self.touch[id(c)] = nodes
            for n in nodes:
                self._union(me, n)
        order = sorted(self.items, key=lambda c: c.shape.bbox[0])
        for a in range(len(order)):
            A = order[a]
            for b in range(a + 1, len(order)):
                B = order[b]
                if B.shape.bbox[0] > A.shape.bbox[2] + TOUCH:
                    break
                if B.shape.bbox[1] > A.shape.bbox[3] + TOUCH or B.shape.bbox[3] < A.shape.bbox[1] - TOUCH:
                    continue
                if set(A.layers) & set(B.layers) and shape_dist(A.shape, B.shape) <= TOUCH:
                    self._union(("item", id(A)), ("item", id(B)))

    def _find(self, a):
        p = self.parent
        p.setdefault(a, a)
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def _union(self, a, b):
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self.parent[rb] = ra

    def _touching(self, c: Cu) -> set:
        """Pour components (layer, id) that reach this pad / via / track."""
        g = self.grid
        out = set()
        ix0, ix1, iy0, iy1 = g._range(c.shape.bbox, 0.3)
        for L in c.layers:
            if L not in self.comp:
                continue
            comp = self.comp[L]
            for iy in range(iy0, iy1 + 1):
                cy = g.oy + iy * g.g
                for ix in range(ix0, ix1 + 1):
                    k = comp[iy * g.W + ix]
                    if k >= 0 and (L, k) not in out and c.shape.dist_point(g.ox + ix * g.g, cy) <= 0.3:
                        out.add((L, k))
        return out

    def main_root(self):
        weight: dict = {}
        for L in self.layers:
            for k, n in enumerate(self.sizes[L]):
                r = self._find((L, k))
                weight[r] = weight.get(r, 0) + n
        return max(weight, key=weight.get) if weight else None

    def stranded(self) -> list[list[Cu]]:
        """Groups of pads of the pour net that are not on the main plane."""
        main = self.main_root()
        groups: dict = {}
        for c in self.items:
            if c.kind != "pad":
                continue
            r = self._find(("item", id(c)))
            if r != main:
                groups.setdefault(r, []).append(c)
        return list(groups.values())


def pour_report(board: Board, net: str = "GND") -> list[dict]:
    if len(board.zone_layers(net)) < 1:
        return []
    out = []
    for grp in PourModel(board, net).stranded():
        out.append({"pads": sorted(c.label for c in grp), "x": grp[0].obj.x, "y": grp[0].obj.y})
    return out


def _via_spot(model: PourModel, grp, vf, smd_pads, via_size):
    """Nearest cell where one via ties this island to the main plane."""
    g = model.grid
    main = model.main_root()
    root = model._find(("item", id(grp[0])))
    px, py = grp[0].obj.x, grp[0].obj.y
    cf, cb = model.comp["F.Cu"], model.comp["B.Cu"]
    root_f = [model._find(("F.Cu", k)) for k in range(len(model.sizes["F.Cu"]))]
    root_b = [model._find(("B.Cu", k)) for k in range(len(model.sizes["B.Cu"]))]
    best = None
    v0, v1 = vf
    for idx in range(g.W * g.H):
        if not (v0[idx] and v1[idx]):
            continue
        kf, kb = cf[idx], cb[idx]
        if kf < 0 or kb < 0:
            continue
        rf, rb = root_f[kf], root_b[kb]
        if not ((rf == root and rb == main) or (rf == main and rb == root)):
            continue
        x, y = g.cell_xy(idx)
        d = math.hypot(x - px, y - py)
        if best is not None and d >= best[0]:
            continue
        if any(abs(p.obj.x - x) < 3 and abs(p.obj.y - y) < 3 and p.shape.dist_point(x, y) < via_size / 2 + 0.15 for p in smd_pads):
            continue
        best = (d, x, y)
    return best


def _track_link(board: Board, model: PourModel, grp, net: str):
    """Short track from a stranded pad out to the main plane (for pads too cramped for a via)."""
    g = model.grid
    main = model.main_root()
    src: dict = {}
    for c in grp:
        src.update(g.cells_in(c))
    free = [g.free_mask(net, L, LINK_WIDTH / 2 + 0.2, 0.3 + LINK_WIDTH) if L in model.comp else bytearray(g.W * g.H) for L in CU_LAYERS]
    dst: dict = {}
    for li, L in enumerate(CU_LAYERS):
        if L not in model.comp:
            continue
        comp = model.comp[L]
        roots: dict[int, bool] = {}
        for idx in range(g.W * g.H):
            k = comp[idx]
            if k < 0:
                continue
            if k not in roots:
                roots[k] = model._find((L, k)) == main
            if roots[k]:
                dst[(li, idx)] = None
    src = {k: v for k, v in src.items() if free[k[0]][k[1]]}
    if not src or not dst:
        return None
    viafree = None
    if len(model.comp) == 2:
        va = g.free_mask(net, "F.Cu", VIA_RADIUS, EDGE + 0.2)
        vb = g.free_mask(net, "B.Cu", VIA_RADIUS, EDGE + 0.2)
        viafree = bytearray(a & b for a, b in zip(va, vb))
        for c in board.copper():
            if c.kind == "pad" and c.obj.kind == "smd":
                for (_, idx) in g.cells_in(c):
                    viafree[idx] = 0
    for _ in range(4):
        path = _astar(g, src, dst, free, viafree, [1.0, 1.0], 3.0, max_pops=40_000)
        if path is None:
            return None
        segs, vias = _path_to_primitives(g, path, src, dst)
        bad = _verify(board, net, segs, vias, LINK_WIDTH, 0.6, 0.13)
        if not bad:
            made = [board.add_track(net, CU_LAYERS[L], x1, y1, x2, y2, LINK_WIDTH) for (L, x1, y1, x2, y2) in segs]
            made += [board.add_via(net, x, y, 0.6, 0.3) for (x, y) in vias]
            return made
        for (L, bx, by) in bad:
            ix, iy = int(round((bx - g.ox) / g.g)), int(round((by - g.oy) / g.g))
            for yy in range(max(0, iy - 1), min(g.H, iy + 2)):
                for xx in range(max(0, ix - 1), min(g.W, ix + 2)):
                    free[L][yy * g.W + xx] = 0
    return None


def stitch_pour(board: Board, net: str = "GND", via_size: float = 0.6, via_drill: float = 0.3, max_fixes: int = 40) -> dict:
    """Re-attach every stranded pad of the pour net to the main plane (via first, short track otherwise)."""
    vias: list[tuple[float, float]] = []
    new_ids: list[str] = []
    links = 0
    if len(board.zone_layers(net)) < 2:
        return {"vias": vias, "links": 0, "stranded": [], "new": new_ids}
    gave_up: set[str] = set()
    for _ in range(max_fixes):
        model = PourModel(board, net)
        all_groups = model.stranded()
        groups = [grp for grp in all_groups if sorted(c.label for c in grp)[0] not in gave_up]
        if not groups:
            left = [{"pads": sorted(c.label for c in grp), "x": grp[0].obj.x, "y": grp[0].obj.y} for grp in all_groups]
            return {"vias": vias, "links": links, "stranded": left, "new": new_ids}
        g = model.grid
        vf = [g.free_mask(net, L, VIA_RADIUS, EDGE + 0.2) for L in CU_LAYERS]
        smd = [c for c in board.copper() if c.kind == "pad" and c.obj.kind == "smd"]
        grp = groups[0]
        spot = _via_spot(model, grp, vf, smd, via_size)
        if spot is not None:
            v = board.add_via(net, spot[1], spot[2], via_size, via_drill)
            vias.append((round(spot[1], 3), round(spot[2], 3)))
            new_ids.append(v.uuid)
            continue
        tracks = _track_link(board, model, grp, net)
        if tracks:
            links += 1
            new_ids += [t.uuid for t in tracks]
            continue
        gave_up.add(sorted(c.label for c in grp)[0])
    left = [{"pads": sorted(c.label for c in grp), "x": grp[0].obj.x, "y": grp[0].obj.y} for grp in PourModel(board, net).stranded()]
    return {"vias": vias, "links": links, "stranded": left, "new": new_ids}
