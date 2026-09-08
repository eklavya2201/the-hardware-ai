"""Shared 2D geometry helpers for component rotation (placement + routing).

A `PlacedComponent` carries a `rotation` (0/90/180/270° CCW). Historically the
in-house placer/router ignored it (every auto-placed part was rotation 0), while
the frontend PCB view, the 3D view, and the KiCad export all honoured it. When the
placer started rotating edge connectors so their mating face points off-board
(Week-4 6.1), the router had to honour rotation too — otherwise a rotated
connector's traces would target the un-rotated pad positions.

`rotate_xy` matches the frontend's `transform="translate(cx,cy) rotate(deg)"`
convention exactly (and the KiCad export), so pads land where every other view
draws them. It is identity at 0°, so rotation-0 boards are byte-for-byte unchanged.
"""

from __future__ import annotations

from app.models import FootprintPad, PlacedComponent


def rotate_xy(x: float, y: float, deg: float) -> tuple[float, float]:
    """Rotate a local (x, y) offset by a 0/90/180/270° CCW component rotation.
    Exact at the four legal orientations (no float error); any other value snaps
    to the nearest of the four."""
    d = int(round(deg / 90.0)) * 90 % 360
    if d == 90:
        return (-y, x)
    if d == 180:
        return (-x, -y)
    if d == 270:
        return (y, -x)
    return (x, y)


def pad_world(pc: PlacedComponent, pad: FootprintPad) -> tuple[float, float]:
    """World (board-frame) position of a pad, honouring the component rotation."""
    rx, ry = rotate_xy(pad.x, pad.y, pc.rotation)
    return (pc.x + rx, pc.y + ry)


def body_dims(pc: PlacedComponent) -> tuple[float, float]:
    """Footprint (width, height) as seen in the board frame — swapped for 90/270°."""
    w, h = pc.footprint.width, pc.footprint.height
    return (h, w) if int(pc.rotation) % 180 else (w, h)


def solid_rects(pc: PlacedComponent) -> list[tuple[float, float, float, float]]:
    """The rectangles this part actually OCCUPIES, in board coordinates.

    Normally one box — the footprint's width x height centred on its pad-field
    centroid, the same model the placer solves in. A footprint that declares
    `bodyRects` occupies those instead: an Arduino shield's header pair is two
    thin strips along opposite board edges with the entire middle free, and
    treating it as one bounding box reported an unavoidable overlap against
    every part a shield is supposed to carry.

    THE single authority for "is this space taken" — both the overlap rule and
    the placement legalizer read it, so they can never disagree.
    """
    fp = pc.footprint
    rot = int(pc.rotation)
    if fp.bodyRects:
        out: list[tuple[float, float, float, float]] = []
        for x0, y0, x1, y1 in fp.bodyRects:
            corners = [rotate_xy(x, y, rot) for x, y in
                       ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            xs = [pc.x + cx for cx, _ in corners]
            ys = [pc.y + cy for _, cy in corners]
            out.append((min(xs), min(ys), max(xs), max(ys)))
        return out

    if fp.pads:
        xs = [p.x for p in fp.pads]
        ys = [p.y for p in fp.pads]
        ox, oy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    else:
        ox = oy = 0.0
    sx, sy = rotate_xy(ox, oy, rot)
    w, h = body_dims(pc)
    cx, cy = pc.x + sx, pc.y + sy
    return [(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)]
