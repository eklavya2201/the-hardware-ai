"""Parametric enclosure derivation — Week-4 ECAD task 6.1.

Turns a *placed board* into real mechanical geometry: connector cutouts located
from where the connectors actually sit, and enclosure clearances sized from the
real part heights. Pure functions (Project/Board -> geometry) with no IR
mutation — `mechanical_tools.derive_from_board` composes these and lands every
change through the strict tool layer so the anti-hallucination contract holds.

Coordinates are mm in the IR frame (Y-up, origin bottom-left), same as the Board.

**Height data dependency.** The library has no reliable per-part body Z-height
today (`Footprint.height` is an in-plane bbox dimension, not solder-to-top). So
`body_height_mm` reads the optional curated `LibraryPart.bodyHeightMm` when
present and otherwise falls back to a package-family table — the same
"verified value with a parametric fallback" shape as the footprint layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.models import ConnectorCutout, MountingHole, PlacedComponent, Project
from app.services import geometry, library

# --------------------------------------------------------------------------- #
# Body-height fallback table (mm, solder-plane to top of part).
# --------------------------------------------------------------------------- #
# Keyed by an UPPER-CASE package-name prefix; longest match wins so "SOT-223"
# beats "SOT-23". These are typical bodies for the family — approximate, but a
# sane clearance beats none until `bodyHeightMm` is curated per part.
_PACKAGE_HEIGHTS: dict[str, float] = {
    # chip passives (imperial)
    "0201": 0.3, "0402": 0.5, "0603": 0.55, "0805": 0.65,
    "1206": 0.9, "1210": 1.2, "1812": 1.9, "2010": 1.2, "2512": 1.2,
    # SOT / small transistor+regulator
    "SOT-23": 1.1, "SOT-223": 1.6, "SOT-89": 1.5, "SOT-353": 0.6, "SOT-363": 0.6,
    "TO-92": 5.2, "TO-220": 9.5, "TO-252": 2.3, "TO-263": 2.5, "DPAK": 2.3,
    # SO / small-outline ICs
    "SOIC": 1.75, "SOP": 1.75, "SSOP": 1.75, "TSSOP": 1.2, "MSOP": 1.1,
    "VSSOP": 1.0, "QSOP": 1.75,
    # leadless / grid
    "QFN": 0.9, "DFN": 0.9, "LGA": 1.0, "BGA": 1.2, "WLCSP": 0.6,
    # quad flat-pack
    "LQFP": 1.6, "TQFP": 1.2, "QFP": 2.7, "PLCC": 4.5,
    # diodes / small
    "SOD-123": 1.1, "SOD-323": 0.8, "SOD-523": 0.6, "SMA": 2.3, "SMB": 2.3,
    "SMC": 2.6, "DO-214": 2.4,
    # discrete / footprint families the library uses
    "SMD-3225": 0.9,
    # A plug-in MODULE is not a 3.2 mm chip. It is a small PCB that sits on a
    # header — 8.5 mm of female header before its own components even start —
    # and the ones we have measured run 3.3 to 27 mm thick. This fallback is a
    # deliberate OVER-estimate for modules we have no manufacturer thickness
    # for: over-estimating clearance only makes an enclosure roomier, while
    # under-estimating closes a lid onto a part. Curated heights
    # (`scripts/curate_module_heights.py`) always win over this.
    "MODULE": 12.0,
}

# Last-resort fallback by part category when the package is unknown/parametric
# (e.g. "SMD", "THT", "Custom" carry no size signal).
_CATEGORY_HEIGHTS: dict[str, float] = {
    "passive": 1.0, "resistor": 0.6, "capacitor": 1.2, "diode": 1.5,
    "transistor": 1.5, "led": 1.0, "crystal": 3.5, "logic": 1.5,
    "regulator": 1.8, "sensor": 1.5, "memory": 1.2, "protection": 1.0,
    "mcu": 2.0, "audio": 3.0, "charger": 1.5,
    "display": 4.0, "actuator": 10.0, "connector": 8.5, "mechanical": 6.0,
    "interface": 2.0, "switch": 5.0,
}

_DEFAULT_HEIGHT = 2.0  # generic body when neither package nor category is known

# Connectors that are physical panel/edge ports worth cutting a wall opening for.
# We key on category=="connector" (the reliable library signal) but exclude the
# ones that are never a wall opening (they live inside the enclosure).
_CUTOUT_EXCLUDE_TOKENS = (
    "batteryholder", "coin", "cr2032", "cr2016", "cr1220",  # cell holders
    "pinheader", "pin_header", "swd", "swo", "idc",          # internal headers
    "testpoint", "test_point", "fiducial",
)

# Clearance radius that must stay free of part courtyards around a mounting hole,
# for the screw boss / standoff (mm — a standard Ø5 mm M3 boss ≈ 2.5 mm radius).
# Shared with mechanical_verifier MECH_R04 so hole placement and the collision
# check agree (a hole the deriver accepts is a hole the DRC passes).
MOUNT_CLEAR_R = 2.5

# Mounting-hole placement: the per-axis insets (mm) tried when searching for a
# clear corner spot. The hole stays near its corner (min inset keeps edge
# clearance for the annular/wall); the search tries these on each axis
# independently and picks the clear combination closest to the corner.
_MOUNT_INSET = 3.5
_MOUNT_INSET_STEPS = (3.0, 4.0, 5.5, 7.0, 9.0, 11.5, 14.0)

# How far a connector's body may sit from a board edge and still be considered
# edge-mounted: within its own half-extent plus this slack (mm).
_EDGE_SLACK_MM = 6.0

# Fit tolerances added to the connector body when sizing the opening (mm).
_CUTOUT_SPAN_TOL = 1.5   # total, along the wall
_CUTOUT_HEIGHT_TOL = 1.0  # total, vertical


def _package_height(package: str | None) -> float | None:
    if not package:
        return None
    pk = package.upper()
    best: tuple[int, float] | None = None
    for prefix, h in _PACKAGE_HEIGHTS.items():
        if pk.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), h)
    return best[1] if best else None


def body_height_mm(mpn: str) -> float:
    """Resolve a part's body height (mm above the PCB): curated field →
    package-family table → category default → generic fallback. Never raises."""
    part = library.get_part(mpn)
    if part is None:
        return _DEFAULT_HEIGHT
    if part.bodyHeightMm is not None and part.bodyHeightMm > 0:
        return float(part.bodyHeightMm)
    ph = _package_height(part.package)
    if ph is not None:
        return ph
    return _CATEGORY_HEIGHTS.get((part.category or "").lower(), _DEFAULT_HEIGHT)


# A HAT does not sit on a table — a whole Raspberry Pi sits under it. The
# official HAT specification (raspberrypi/hats) requires the GPIO connector to
# space the HAT "at least 8mm from the Pi (i.e. uses spacers 8mm or larger)".
# Nothing is ever placed on a board's BOTTOM side today, so the derived floor
# gap came out at its 3 mm minimum and an enclosure for a HAT would close on
# the computer underneath it.
HOST_BOARD_CLEARANCE_MM: dict[str, float] = {
    "RPi_HAT_Header_40P": 8.0,   # HAT spec, "spacers 8mm or larger"
}


def host_board_clearance(project: Project) -> tuple[float, str] | None:
    """`(mm, why)` when this board stacks ON another board that needs room
    underneath it, else None. Keyed on the standard host header, which is the
    thing that makes it a HAT/shield in the first place."""
    board = project.board
    if board is None:
        return None
    for pc in board.components:
        need = HOST_BOARD_CLEARANCE_MM.get(pc.mpn)
        if need is not None:
            return (need, f"{pc.refdes} is a Raspberry Pi HAT header — the Pi itself "
                          f"sits under this board, and the HAT specification requires "
                          f"spacers of {need:g} mm or larger")
    return None


def derived_clearances(project: Project) -> tuple[float, float]:
    """`(clearance_top, clearance_bottom)` sized from the tallest real part on each
    board side, with sane floors + a small assembly gap so the lid closes over the
    tallest component. Shared by `derive_from_board` and the enclosure templates.

    A board that STACKS on a host (a Pi HAT) also has to leave room for the host
    underneath — see `host_board_clearance`."""
    top_h, _ = tallest_on_side(project, "top")
    bot_h, _ = tallest_on_side(project, "bottom")
    bottom = max(3.0, bot_h + 1.5)
    host = host_board_clearance(project)
    if host is not None:
        bottom = max(bottom, host[0])
    return (round(max(5.0, top_h + 2.0), 1), round(bottom, 1))


def tallest_on_side(project: Project, layer: str) -> tuple[float, str | None]:
    """Tallest placed part on the given board `layer` ("top"/"bottom"):
    returns `(height_mm, refdes)` — `(0.0, None)` if nothing is on that side."""
    board = project.board
    if board is None:
        return (0.0, None)
    best_h, best_ref = 0.0, None
    for pc in board.components:
        if pc.layer != layer:
            continue
        part = library.get_part(pc.mpn)
        if part is not None and getattr(part, "offBoard", False):
            # The board carries this part's connector, not the part. A servo
            # does not have to fit under the lid, and letting one set the
            # clearance inflated every enclosure that drove a motor.
            continue
        h = body_height_mm(pc.mpn)
        if h > best_h:
            best_h, best_ref = h, pc.refdes
    return (best_h, best_ref)


def _footprint_extent(pc: PlacedComponent) -> tuple[float, float]:
    """Board-frame (x-extent, y-extent) of a placed part's footprint bbox,
    accounting for 90/270° rotation swapping the axes."""
    fw = pc.footprint.courtyard_w or pc.footprint.width
    fh = pc.footprint.courtyard_h or pc.footprint.height
    if pc.rotation in (90, 270):
        fw, fh = fh, fw
    return (fw, fh)


def _body_bbox(pc: PlacedComponent) -> tuple[float, float, float, float]:
    """Bbox of the part's PHYSICAL BODY (never the courtyard). A module's
    courtyard can be far larger than the part: the ESP32-WROOM's is 48x41 mm
    because it contains the antenna KEEP-OUT, a no-copper region that is meant
    to overhang the board edge. Asking "does this part fit on the board" has to
    measure the 18x25.5 mm can, not the keep-out."""
    from app.services import geometry

    fw, fh = pc.footprint.width, pc.footprint.height
    if pc.rotation in (90, 270):
        fw, fh = fh, fw
    cx, cy = pc.x, pc.y
    if pc.footprint.pads:
        pts = [geometry.pad_world(pc, pad) for pad in pc.footprint.pads]
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        fw = max(fw, (max(xs) - min(xs)) + 1.0)
        fh = max(fh, (max(ys) - min(ys)) + 1.0)
    return (cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2)


def _bbox(pc: PlacedComponent) -> tuple[float, float, float, float]:
    """Courtyard bbox of a placed part: (x0, y0, x1, y1) in board coords.

    Pads are stored origin-relative, and on most KiCad footprints the origin is
    the body centroid — but pin-header-class footprints anchor at *pin 1*, so a
    centred bbox lands half a body away from the copper (a corner-origin part
    tripped MECH_R01 at a legal spec position). When the part has real pads,
    centre the courtyard on the measured pad field instead of the origin; the
    origin-centred estimate stays as the fallback for pad-less parts."""
    from app.services import geometry  # local import avoids a cycle

    ex, ey = _footprint_extent(pc)
    cx, cy = pc.x, pc.y
    if pc.footprint.pads:
        pts = [geometry.pad_world(pc, pad) for pad in pc.footprint.pads]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        # The courtyard is at least the pad field itself plus a hair of margin.
        ex = max(ex, (max(xs) - min(xs)) + 1.0)
        ey = max(ey, (max(ys) - min(ys)) + 1.0)
    return (cx - ex / 2, cy - ey / 2, cx + ex / 2, cy + ey / 2)


def _hole_clear(x: float, y: float, boxes: list[tuple[float, float, float, float]]) -> bool:
    """True if a mounting hole at (x, y) keeps its `MOUNT_CLEAR_R` boss clear of
    every part BODY (nearest-point distance from the hole centre to each bbox
    ≥ the boss radius). Bodies, not courtyards: a screw boss collides with the
    can and the pads, not with the ESP32's antenna keep-out — which spans the
    whole width of a compact board and left no legal hole spot at all."""
    for x0, y0, x1, y1 in boxes:
        dx = max(x0 - x, 0.0, x - x1)
        dy = max(y0 - y, 0.0, y - y1)
        if (dx * dx + dy * dy) ** 0.5 < MOUNT_CLEAR_R:
            return False
    return True


def _round_mounting_holes(project: Project, d: float) -> list[MountingHole]:
    """Mounting holes for a circular board: four holes on a bolt circle at the
    45° diagonals (the rim positions farthest from the horizontal/vertical
    component flow), each slid inboard along its radial until clear of part
    courtyards. Wearable-scale boards (< 60 mm) get M2 hardware — an M3 boss
    ring eats a 42 mm watch face. Boss clearance stays MOUNT_CLEAR_R so
    MECH_R04 and the deriver keep agreeing."""
    import math as _math

    board = project.board
    boxes = [_body_bbox(pc) for pc in board.components] if board else []
    r_board = d / 2.0
    cx = cy = r_board
    small = d < 60.0
    hole_d, standoff = (2.2, "M2") if small else (3.2, "M3")
    # Keep the full annular + a hair of wall between hole rim and board edge.
    rim_inset = hole_d / 2 + 1.5
    holes: list[MountingHole] = []
    for deg in (45.0, 135.0, 225.0, 315.0):
        a = _math.radians(deg)
        ux, uy = _math.cos(a), _math.sin(a)
        chosen: tuple[float, float] | None = None
        for extra in (0.0, 1.5, 3.0, 5.0, 7.5, 10.0):
            rr = r_board - rim_inset - extra
            if rr <= MOUNT_CLEAR_R:
                break
            x, y = cx + ux * rr, cy + uy * rr
            if _hole_clear(x, y, boxes):
                chosen = (x, y)
                break
        if chosen is None:
            rr = r_board - rim_inset
            chosen = (cx + ux * rr, cy + uy * rr)
        holes.append(MountingHole(
            id=f"mh-{uuid4().hex[:8]}", x=round(chosen[0], 2), y=round(chosen[1], 2),
            diameter=hole_d, standoff=standoff,
        ))
    return holes


def _polygon_mounting_holes(project, board) -> list[MountingHole]:
    """Holes for a polygon board: one inset from each VERTEX, pulled inboard
    until it is clear of the milled edge and of every part.

    A polygon has corners, but they are not the rect's corners — a triangle's
    are 60 degrees and a hole placed at the bbox corner is off the board
    entirely. Each hole starts on the bisector from its vertex toward the
    centroid, which is the direction that gains material fastest, and steps
    inboard until it fits. Vertices too close together (the short notch edges
    of an X) collapse to one hole rather than drilling two overlapping ones.
    """
    from app.services import board_shape

    ring = board_shape.ring_for_board(board)
    if ring is None:
        return []
    cx, cy = board_shape.centroid(ring)
    boxes = [_body_bbox(pc) for pc in board.components]
    need = MOUNT_CLEAR_R          # the boss must clear the edge by its radius
    holes: list[MountingHole] = []
    placed: list[tuple[float, float]] = []
    dia = 2.75 if max(board.width, board.height) < 60 else 3.2
    for vx, vy in ring:
        dx, dy = cx - vx, cy - vy
        d = (dx * dx + dy * dy) ** 0.5
        if d < 1e-6:
            continue
        ux, uy = dx / d, dy / d
        spot = None
        # Walk in from the vertex; stop at the first place with room for the
        # boss AND no part under it.
        steps = int(d / 0.5)
        for i in range(1, max(2, steps)):
            px, py = vx + ux * i * 0.5, vy + uy * i * 0.5
            if board_shape.clearance(ring, px, py) < need:
                continue
            if not _hole_clear(px, py, boxes):
                continue
            spot = (round(px, 2), round(py, 2))
            break
        if spot is None:
            continue
        if any((spot[0] - qx) ** 2 + (spot[1] - qy) ** 2 < (3 * MOUNT_CLEAR_R) ** 2
               for qx, qy in placed):
            continue          # a near-duplicate from an adjacent short edge
        placed.append(spot)
        holes.append(MountingHole(
            id=f"H{len(holes) + 1}", x=spot[0], y=spot[1], diameter=dia,
            standoff="M3" if dia > 3.0 else "M2.5",
        ))
    return holes


def place_mounting_holes(project: Project, w: float, h: float) -> list[MountingHole]:
    """Four corner M3 mounting holes, each nudged diagonally inboard from its
    corner to the nearest spot clear of part courtyards (so the screw doesn't
    land on a part — MECH_R04). Falls back to the ideal corner inset if the whole
    search window is occupied (a genuinely too-dense corner, which MECH_R04 then
    honestly flags rather than the deriver hiding it). Round boards route to the
    bolt-circle variant instead — a circle has no corners."""
    board = project.board
    if board is not None and board.shape == "round":
        return _round_mounting_holes(project, board.width)
    if board is not None and board.shape == "polygon":
        return _polygon_mounting_holes(project, board)
    boxes = [_body_bbox(pc) for pc in board.components] if board else []
    holes: list[MountingHole] = []
    # (corner x, corner y, x-direction inboard, y-direction inboard)
    corners = (
        (0.0, 0.0, +1, +1), (w, 0.0, -1, +1),
        (0.0, h, +1, -1), (w, h, -1, -1),
    )
    for cx, cy, sx, sy in corners:
        # Search a small 2D neighbourhood of insets (each axis independent) and
        # take the clear spot closest to the corner — a corner blocked by a large
        # part (e.g. an MCU module hugging one side) is escaped by sliding along an
        # edge, which the pure-diagonal search can't do.
        chosen: tuple[float, float] | None = None
        best_cost = float("inf")
        for dx in _MOUNT_INSET_STEPS:
            for dy in _MOUNT_INSET_STEPS:
                x, y = cx + sx * dx, cy + sy * dy
                if _hole_clear(x, y, boxes):
                    cost = dx * dx + dy * dy  # keep it near the corner
                    if cost < best_cost:
                        best_cost, chosen = cost, (x, y)
        if chosen is None:
            chosen = (cx + sx * _MOUNT_INSET, cy + sy * _MOUNT_INSET)
        holes.append(MountingHole(
            id=f"mh-{uuid4().hex[:8]}", x=round(chosen[0], 2), y=round(chosen[1], 2),
            diameter=3.2, standoff="M3",
        ))
    return holes


_BODY_CENTERS: dict[str, tuple[float, float]] | None = None


def _body_centers() -> dict[str, tuple[float, float]]:
    """MPN -> measured 3D-model body-bbox centre (IR-local mm), generated from
    the actual GLBs by `scripts/gen_model_body_centers.py` and committed. The
    models are the ground truth for where the visible body sits — measured
    examples: the GCT USB-C's body centres on its pad field (+2.98) while the
    barrel jack's centres on the origin with pads 2.35 mm off-axis, so neither
    the origin nor the pad centre is a reliable anchor on its own."""
    global _BODY_CENTERS
    if _BODY_CENTERS is None:
        path = Path(__file__).resolve().parent.parent / "library" / "model_body_centers.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _BODY_CENTERS = {k: (v["cx"], v["cy"]) for k, v in raw.items()}
        except Exception:  # noqa: BLE001 — missing/corrupt table ⇒ fallback anchors
            _BODY_CENTERS = {}
    return _BODY_CENTERS


def _connector_anchor_world(pc: PlacedComponent) -> tuple[float, float]:
    """World centre of the connector's VISIBLE BODY — the cutout anchor.
    Measured model body centre (rotated into board frame) when available;
    else the pad-field bbox centre; else the placement origin."""
    center = _body_centers().get(pc.mpn)
    if center is not None:
        dx, dy = geometry.rotate_xy(center[0], center[1], pc.rotation)
        return (pc.x + dx, pc.y + dy)
    xs: list[float] = []
    ys: list[float] = []
    for pad in pc.footprint.pads:
        x, y = geometry.pad_world(pc, pad)
        xs.append(x)
        ys.append(y)
    if not xs:
        return (pc.x, pc.y)
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _nearest_edge(x: float, y: float, w: float, h: float) -> tuple[str, float]:
    """Nearest board edge to a point and its distance."""
    dists = {"left": x, "right": w - x, "bottom": y, "top": h - y}
    edge = min(dists, key=dists.get)  # type: ignore[arg-type]
    return (edge, dists[edge])


def _is_cutout_connector(mpn: str) -> bool:
    part = library.get_part(mpn)
    if part is None or (part.category or "").lower() != "connector":
        return False
    tok = (mpn + " " + (part.package or "") + " " + (part.description or "")).lower()
    return not any(bad in tok for bad in _CUTOUT_EXCLUDE_TOKENS)


def derive_cutouts(project: Project) -> list[ConnectorCutout]:
    """Derive an enclosure-wall opening for every edge-mounted connector on the
    placed board. A connector qualifies when it is a physical port (library
    category "connector", minus internal-only types) AND its body sits within
    reach of a board edge. The opening is sized to the connector body + a fit
    tolerance and tagged with the real `refdes` + `edge`."""
    board = project.board
    if board is None or not board.components:
        return []
    w, h = board.width, board.height
    out: list[ConnectorCutout] = []
    seq = 0
    for pc in board.components:
        if pc.layer != "top" or not _is_cutout_connector(pc.mpn):
            continue
        edge, dist = _nearest_edge(pc.x, pc.y, w, h)
        ex, ey = _footprint_extent(pc)
        # normal extent = half the body dimension perpendicular to the edge.
        normal_half = (ex if edge in ("left", "right") else ey) / 2.0
        if dist > normal_half + _EDGE_SLACK_MM:
            continue  # sits too far inside to poke through a wall
        along = (ey if edge in ("left", "right") else ex)  # body span along the wall
        span = round(along + _CUTOUT_SPAN_TOL, 2)
        height = round(body_height_mm(pc.mpn) + _CUTOUT_HEIGHT_TOL, 2)
        # Centre the opening on the connector's VISIBLE BODY (measured from the
        # actual 3D model — see _connector_anchor_world). Neither the placement
        # origin nor the pad centre reliably tracks the body across models.
        acx, acy = _connector_anchor_world(pc)
        if edge == "bottom":
            x0, y0 = acx - span / 2.0, 0.0
        elif edge == "top":
            x0, y0 = acx - span / 2.0, h
        elif edge == "left":
            x0, y0 = 0.0, acy - span / 2.0
        else:  # right
            x0, y0 = w, acy - span / 2.0
        seq += 1
        out.append(ConnectorCutout(
            id=f"cut-{pc.refdes}-{seq}",
            refdes=pc.refdes,
            x=round(x0, 2), y=round(y0, 2),
            width=span, height=height, edge=edge,  # type: ignore[arg-type]
        ))
    return out
