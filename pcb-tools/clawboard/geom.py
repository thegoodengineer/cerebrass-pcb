"""2-D geometry used by connectivity, clearance checks and the router.

Every copper item is modelled as a *convex polygon inflated by a radius*:
  circle  -> 1 point  + r          track -> 2 points + width/2
  oval    -> 2 points + r          rect  -> 4 points + 0 (roundrect: 4 points + corner r)
which gives one uniform, exact distance routine for all pairs.
"""
from __future__ import annotations

import math

EPS = 1e-6


def rot(x: float, y: float, deg: float) -> tuple[float, float]:
    """Rotate using KiCad's convention (Y axis down, positive angle = CCW on screen)."""
    if not deg:
        return x, y
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return x * c + y * s, -x * s + y * c


def pt_seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def nearest_on_seg(px, py, ax, ay, bx, by) -> tuple[float, float]:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return ax, ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return ax + t * dx, ay + t * dy


def _orient(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def segs_intersect(a, b, c, d) -> bool:
    o1 = _orient(*a, *b, *c)
    o2 = _orient(*a, *b, *d)
    o3 = _orient(*c, *d, *a)
    o4 = _orient(*c, *d, *b)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def seg_seg_dist(a, b, c, d) -> float:
    if segs_intersect(a, b, c, d):
        return 0.0
    return min(
        pt_seg_dist(*a, *c, *d),
        pt_seg_dist(*b, *c, *d),
        pt_seg_dist(*c, *a, *b),
        pt_seg_dist(*d, *a, *b),
    )


def point_in_poly(px, py, pts) -> bool:
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Shape:
    """Convex polygon ``pts`` inflated by ``r``."""

    __slots__ = ("pts", "r", "bbox", "aabb")

    def __init__(self, pts, r: float = 0.0):
        self.pts = [(float(x), float(y)) for x, y in pts]
        self.r = float(r)
        self.aabb = None  # (cx, cy, half_w, half_h) when the core polygon is an axis-aligned rectangle
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        self.bbox = (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)

    # -- constructors -------------------------------------------------------
    @staticmethod
    def circle(x, y, r) -> "Shape":
        return Shape([(x, y)], r)

    @staticmethod
    def capsule(x1, y1, x2, y2, width) -> "Shape":
        return Shape([(x1, y1), (x2, y2)], width / 2.0)

    @staticmethod
    def rect(cx, cy, w, h, angle=0.0, corner_r=0.0) -> "Shape":
        hw, hh = w / 2.0 - corner_r, h / 2.0 - corner_r
        hw, hh = max(hw, 0.0), max(hh, 0.0)
        corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
        pts = []
        for x, y in corners:
            rx, ry = rot(x, y, angle)
            pts.append((cx + rx, cy + ry))
        sh = Shape(pts, corner_r)
        a = angle % 180
        if abs(a) < 1e-6:
            sh.aabb = (cx, cy, hw, hh)
        elif abs(a - 90) < 1e-6:
            sh.aabb = (cx, cy, hh, hw)
        return sh

    @staticmethod
    def oval(cx, cy, w, h, angle=0.0) -> "Shape":
        if abs(w - h) < 1e-9:
            return Shape.circle(cx, cy, w / 2.0)
        if w > h:
            a, r = (w - h) / 2.0, h / 2.0
            ends = [(-a, 0.0), (a, 0.0)]
        else:
            a, r = (h - w) / 2.0, w / 2.0
            ends = [(0.0, -a), (0.0, a)]
        pts = []
        for x, y in ends:
            rx, ry = rot(x, y, angle)
            pts.append((cx + rx, cy + ry))
        return Shape(pts, r)

    # -- queries ------------------------------------------------------------
    def edges(self):
        p = self.pts
        n = len(p)
        if n == 1:
            return [(p[0], p[0])]
        if n == 2:
            return [(p[0], p[1])]
        return [(p[i], p[(i + 1) % n]) for i in range(n)]

    def core_dist_point(self, px, py) -> float:
        ab = self.aabb
        if ab is not None:
            dx = abs(px - ab[0]) - ab[2]
            dy = abs(py - ab[1]) - ab[3]
            if dx <= 0.0:
                return dy if dy > 0.0 else 0.0
            return dx if dy <= 0.0 else math.hypot(dx, dy)
        p = self.pts
        n = len(p)
        if n == 1:
            return math.hypot(px - p[0][0], py - p[0][1])
        if n == 2:
            return pt_seg_dist(px, py, *p[0], *p[1])
        if point_in_poly(px, py, p):
            return 0.0
        return min(pt_seg_dist(px, py, *p[i], *p[(i + 1) % n]) for i in range(n))

    def dist_point(self, px, py) -> float:
        """Signed-ish distance from a point to the copper edge (<=0 means inside)."""
        return self.core_dist_point(px, py) - self.r

    def contains(self, px, py, tol: float = 1e-4) -> bool:
        return self.dist_point(px, py) <= tol

    def center(self) -> tuple[float, float]:
        n = len(self.pts)
        return sum(p[0] for p in self.pts) / n, sum(p[1] for p in self.pts) / n


def bbox_gap(a, b) -> float:
    """Lower bound on the distance between two bounding boxes."""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def shape_dist(a: Shape, b: Shape) -> float:
    """Copper-edge to copper-edge distance (0 or negative when touching/overlapping)."""
    pa, pb = a.pts, b.pts
    if len(pa) >= 3 and point_in_poly(pb[0][0], pb[0][1], pa):
        core = 0.0
    elif len(pb) >= 3 and point_in_poly(pa[0][0], pa[0][1], pb):
        core = 0.0
    else:
        core = min(seg_seg_dist(e1[0], e1[1], e2[0], e2[1]) for e1 in a.edges() for e2 in b.edges())
    return core - a.r - b.r


def closest_points(a: Shape, b: Shape) -> tuple[float, float]:
    """Approximate midpoint of the gap between two shapes (for error markers)."""
    best = None
    for e1 in a.edges():
        for e2 in b.edges():
            for p in e1:
                q = nearest_on_seg(*p, *e2[0], *e2[1])
                d = math.hypot(p[0] - q[0], p[1] - q[1])
                if best is None or d < best[0]:
                    best = (d, p, q)
            for p in e2:
                q = nearest_on_seg(*p, *e1[0], *e1[1])
                d = math.hypot(p[0] - q[0], p[1] - q[1])
                if d < best[0]:
                    best = (d, q, p)
    _, p, q = best
    return (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0


def snap(v: float, grid: float) -> float:
    return round(round(v / grid) * grid, 6)
