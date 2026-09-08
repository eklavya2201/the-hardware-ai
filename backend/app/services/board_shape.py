"""The board's OUTLINE as geometry — the single authority on what is on the board.

Rect and circle were each hard-coded into every consumer that needs to know
where the copper ends: the placer's edge force, its clamp, the edge row-packer,
the router's cell mask, PCB_R02, the mounting-hole deriver, the Gerber outline.
That was tolerable for two shapes. A drone board is a triangle, a hexagon or an
X, and repeating polygon maths eight times would guarantee the eight disagree —
which is exactly how a part ends up placed where the router thinks there is no
copper.

So every shape resolves to ONE representation here (a closed point ring) and
every question is asked of these functions:

    contains(ring, x, y)              is this point on the board?
    clearance(ring, x, y)             how far is it from the milled edge?
    fits(ring, x, y, hw, hh, margin)  does a body with these half-extents fit?
    clamp(ring, x, y, r, margin)      nearest legal position for that body

Rect keeps its fast path (a bbox test is cheaper and exact), so nothing about
the existing two shapes changes cost or behaviour.

Frame: IR millimetres, origin bottom-left, Y up — the same frame as Board and
the mechanical IR. Rings are counter-clockwise, not repeated at the end.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]
Ring = list[Point]

# A body's corners leave an outline before its centre does, so every "does it
# fit" question uses the half-DIAGONAL. Named because three call sites used to
# spell it out and one of them used the half-extent by mistake.
def half_diagonal(hw: float, hh: float) -> float:
    return math.hypot(hw, hh)


def regular_polygon(sides: int, diameter: float, *, rotation_deg: float = 0.0,
                    center: Point | None = None) -> Ring:
    """A regular n-gon inscribed in `diameter` (circumscribed circle).

    Rotation is counter-clockwise from the +X axis. The default puts a vertex
    at the top for odd-sided shapes (a triangle points "up", which is what
    someone asking for a triangular board pictures) and a flat edge at the
    bottom for even ones.
    """
    if sides < 3:
        raise ValueError("a polygon needs at least 3 sides")
    r = diameter / 2.0
    cx, cy = center if center is not None else (r, r)
    # Odd n: start at 90 deg so a vertex points up. Even n: offset half a step
    # so the shape sits flat rather than balancing on a point.
    base = 90.0 if sides % 2 else 90.0 + 180.0 / sides
    ring: Ring = []
    for i in range(sides):
        a = math.radians(base + rotation_deg + i * 360.0 / sides)
        ring.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return ring


def circle_ring(diameter: float, *, segments: int = 72,
                center: Point | None = None) -> Ring:
    """A circle as a ring — for the consumers that can only take polygons
    (Gerber outline, KiCad Edge.Cuts on old versions). Placement and routing
    keep exact circle maths; this is a rendering/export convenience."""
    r = diameter / 2.0
    cx, cy = center if center is not None else (r, r)
    return [
        (cx + r * math.cos(2 * math.pi * i / segments),
         cy + r * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def bbox(ring: Sequence[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def centroid(ring: Sequence[Point]) -> Point:
    """Area centroid (not the vertex average — a vertex average drifts toward
    whichever edge carries more points, which matters for the X shapes a drone
    frame wants)."""
    a2 = 0.0
    cx = 0.0
    cy = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a2) < 1e-9:  # degenerate: fall back to the vertex average
        return (sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n)
    return (cx / (3.0 * a2), cy / (3.0 * a2))


def area(ring: Sequence[Point]) -> float:
    a2 = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        a2 += x0 * y1 - x1 * y0
    return abs(a2) / 2.0


def contains(ring: Sequence[Point], x: float, y: float) -> bool:
    """Ray casting. A point exactly on an edge may read either way — callers
    that care use `clearance`, which is signed and unambiguous."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            xc = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xc:
                inside = not inside
        j = i
    return inside


def _dist_point_segment(px: float, py: float, ax: float, ay: float,
                        bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def clearance(ring: Sequence[Point], x: float, y: float) -> float:
    """Signed distance to the milled edge: positive inside, negative outside.

    This is the honest form of the question. A point 0.2 mm inside a triangle's
    acute corner is 'contained' but has no room for a part, and a rule that
    only asks `contains` reports such a board as clean."""
    n = len(ring)
    d = min(
        _dist_point_segment(x, y, ring[i][0], ring[i][1],
                            ring[(i + 1) % n][0], ring[(i + 1) % n][1])
        for i in range(n)
    )
    return d if contains(ring, x, y) else -d


def fits(ring: Sequence[Point], x: float, y: float, hw: float, hh: float,
         margin: float = 0.0) -> bool:
    """Does a body of half-extents (hw, hh) centred at (x, y) sit inside?

    Uses the half-diagonal, so the answer holds at any rotation — the same
    conservative test PCB_R02 applies to a circular board."""
    return clearance(ring, x, y) >= half_diagonal(hw, hh) + margin


def clamp(ring: Sequence[Point], x: float, y: float, need: float,
          *, toward: Point | None = None) -> Point:
    """Nearest position that leaves `need` mm of board around the point.

    Pulls along the line to the centroid (or `toward`) and bisects, because a
    polygon has no closed-form 'push it back inside' the way a circle does.
    Returns the target itself when even that has no room — the caller's DRC
    then reports an honest off-board part rather than a silently wrong one."""
    if clearance(ring, x, y) >= need:
        return (x, y)
    tx, ty = toward if toward is not None else centroid(ring)
    if clearance(ring, tx, ty) < need:
        return (tx, ty)  # the board cannot hold this body anywhere
    lo, hi = 0.0, 1.0  # 0 = the original point, 1 = the safe interior point
    for _ in range(40):
        mid = (lo + hi) / 2.0
        mx = x + (tx - x) * mid
        my = y + (ty - y) * mid
        if clearance(ring, mx, my) >= need:
            hi = mid
        else:
            lo = mid
    return (x + (tx - x) * hi, y + (ty - y) * hi)


def ring_for_board(board) -> Ring | None:
    """The outline of a placed Board, or None for a plain rectangle.

    None is the signal to take the existing fast path: every consumer already
    handles rect exactly and more cheaply than polygon maths would."""
    shape = getattr(board, "shape", "rect")
    pts = list(getattr(board, "outline_points", None) or [])
    if shape == "polygon" and len(pts) >= 3:
        return [(p.x, p.y) if hasattr(p, "x") else (p[0], p[1]) for p in pts]
    if shape == "round":
        return circle_ring(board.width)
    return None


def rect_ring(width: float, height: float) -> Ring:
    return [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]


def normalize(points: Iterable[Point]) -> Ring:
    """Drop repeated/closing vertices and force counter-clockwise winding, so
    downstream code never has to care how the ring was authored."""
    ring: Ring = []
    for p in points:
        pt = (float(p[0]), float(p[1]))
        if not ring or math.hypot(pt[0] - ring[-1][0], pt[1] - ring[-1][1]) > 1e-6:
            ring.append(pt)
    if len(ring) > 1 and math.hypot(ring[0][0] - ring[-1][0],
                                    ring[0][1] - ring[-1][1]) <= 1e-6:
        ring.pop()
    signed = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        signed += x0 * y1 - x1 * y0
    if signed < 0:
        ring.reverse()
    return ring


def translate_to_origin(ring: Ring) -> Ring:
    """Move the ring so its bbox starts at (0, 0) — the Board frame's
    convention, and what keeps width/height meaning what they always meant."""
    x0, y0, _, _ = bbox(ring)
    return [(x - x0, y - y0) for x, y in ring]
