"""Auto-placement.

Two strategies:
  - **Force-directed** (default for ≥10 components): a spring simulator that
    attracts net-connected components together and repels everyone from
    everyone, with a board-edge constraint. Minimizes total wire length and
    produces compact, square-ish layouts. Used for any non-trivial design.
  - **Zone-based fallback**: simple left-to-right column packing. Kept as a
    deterministic fallback for tiny designs and tests.

Smartwatch detection: when the schematic contains a round display
(GC9A01-1.28-Round) AND a typical wearable peripheral (MAX30102, AS5600), we
emit a round board outline and pull the display to the geometric centre as
an anchor. Other components arrange around it via force-directed.

Output: the same `Board` shape as before, plus `shape="rect"|"round"` and
`has_ground_pour=True`. Rats-nest lines are still emitted for the auto-router
to consume.
"""

from __future__ import annotations

import math
import random

from app.models import (
    Board,
    FormFactorSpec,
    PlacedComponent,
    Point,
    Project,
    RatsLine,
)
from app.services import board_shape, constraints, footprints, geometry, library

# Defaults
BOARD_MARGIN = 2.5          # mm

# Axis-direction → CCW angle (deg from +X). Used to rotate an edge connector so
# its mating face points off-board (Week-4 6.1).
_DIR_ANGLE = {"+X": 0, "+Y": 90, "-X": 180, "-Y": 270}
DEFAULT_BOARD_AR = 1.4      # width/height target for rect boards
SMARTWATCH_DIAMETER = 42.0  # mm — typical wearable

# Force-directed knobs. Tuned so repulsion ≈ attraction at the ideal-length
# distance, and edge constraint dominates when a component escapes the board.
ITERATIONS = 300
INITIAL_TEMP_FRAC = 0.10    # initial temperature as fraction of board span
COOL_FACTOR = 0.972         # slower cool over more iterations → better settling
ATTRACT_K = 0.04            # spring strength toward connected components
REPEL_K = 1.8               # inverse-square repulsion (smaller = less explosive)
EDGE_K = 8.0                # board-edge restoring force (large — no escapes)
DECOUPLER_BIAS_K = 0.10     # extra pull for caps to their associated IC
# Body-clearance separation. These boards are mostly empty, so we optimise
# placement for spacing (routability) over compactness: keep a real gap between
# bodies and push hard once they would overlap, so the legalizer is a rare
# cleanup rather than a second stage fighting force-directed's output.
SEPARATION_FACTOR = 1.9     # target centre-spacing as a multiple of summed radii
#                             (enough to avoid most overlaps without flinging
#                             parts apart — over-spreading lengthens every net
#                             and slows the router; the legalizer guarantees the
#                             rest regardless)
OVERLAP_REPEL_BOOST = 4.0   # extra repulsion once two bodies are inside keepout


# ---- Helpers --------------------------------------------------------------


def _is_smartwatch(project: Project) -> bool:
    mpns = {c.mpn for c in project.ir.components}
    has_round_display = "GC9A01-1.28-Round" in mpns
    has_wearable_sensor = bool(
        mpns & {"MAX30102", "AS5600", "BMI270 IMU"}
    )
    return has_round_display and has_wearable_sensor


# The outline of the board currently being solved, or None for rect/round.
# Threading it through the force loop, the clamp, the edge packer and the
# legalizer would touch every signature and every caller; the solve is
# synchronous and single-threaded, so it is set once per _assemble_board and
# read by the helpers that need it. Always restored in a finally — a leaked
# ring would silently constrain the NEXT board.
_RING: list[tuple[float, float]] | None = None


def polygon_ring(form: FormFactorSpec | None, fp_map: dict | None = None
                 ) -> list[tuple[float, float]] | None:
    """The outline a polygon form factor asks for, sized so the largest part
    physically fits (the same courtesy `_round_diameter` does for a disc — a
    board whose biggest part cannot fit makes every downstream result
    meaningless, and R57 reports the growth honestly)."""
    if form is None or form.shape != "polygon":
        return None
    if form.points:
        ring = board_shape.normalize([(p.x, p.y) for p in form.points])
    elif form.sides:
        ring = board_shape.regular_polygon(form.sides, form.diameter_mm or 60.0)
    else:
        return None
    ring = board_shape.translate_to_origin(ring)
    if fp_map:
        # Grow about the centroid until the largest body fits inside.
        need = 0.0
        for fp in fp_map.values():
            need = max(need, board_shape.half_diagonal(fp.width / 2.0, fp.height / 2.0))
        need += BOARD_MARGIN
        cx, cy = board_shape.centroid(ring)
        for _ in range(24):
            if board_shape.clearance(ring, cx, cy) >= need:
                break
            ring = [(cx + (x - cx) * 1.12, cy + (y - cy) * 1.12) for x, y in ring]
        ring = board_shape.translate_to_origin(ring)
    return [(round(x, 3), round(y, 3)) for x, y in ring]


def requested_form(project: Project) -> FormFactorSpec | None:
    """The effective board-form ask: the user's recorded `set_form_factor`
    spec wins; with none recorded, the legacy smartwatch heuristic (round
    display + wearable sensor) implies a round board so old projects keep
    their behaviour. None = no stated ask, use the rect heuristics."""
    spec = getattr(project.ir, "form_factor", None)
    if spec is not None:
        return spec
    if _is_smartwatch(project):
        return FormFactorSpec(shape="round", note="wearable (implied)")
    return None


def _display_anchor_ref(project: Project, fp_map: dict) -> str | None:
    """On a round board the display is the product's face — anchor the largest
    display-category part dead centre (generalized from the GC9A01-only
    smartwatch hack: ANY display gets centred on ANY round board)."""
    best: tuple[float, str] | None = None
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part is None or part.category != "display":
            continue
        fp = fp_map.get(c.refdes)
        area = (fp.width * fp.height) if fp is not None else 0.0
        if best is None or area > best[0]:
            best = (area, c.refdes)
    return best[1] if best else None


def _display_center_anchor(
    ref: str, fp_map: dict, cx: float, cy: float
) -> tuple[float, float]:
    """Anchor coordinates that put the display's BODY (pad-field centre) at
    (cx, cy). Anchors are footprint-anchor coordinates, and a pin-1-anchored
    or asymmetric footprint puts the body up to half a part away from its
    anchor — anchoring the raw centre left the watch face 2 mm off-axis."""
    ox, oy = _pad_centroid(fp_map[ref]) if ref in fp_map else (0.0, 0.0)
    return (cx - ox, cy - oy)


def _round_diameter(fp_map: dict, requested: float | None) -> float:
    """Board diameter for a round build: the requested diameter (or the 42 mm
    wearable default), grown only when the parts physically demand it — the
    sum of footprint areas must stay under ~30 % of the disc, and the single
    largest part's diagonal must fit inside the outline. R57 reports the
    overrun when a requested diameter had to grow, so it is never silent."""
    total_area = sum(f.width * f.height for f in fp_map.values())
    # A disc has no corners to park parts in and its rim is dead for
    # routing; ease the fill from 30 % as the part count grows (35 parts on
    # a Ø60 watch left 6 preview rats at 30 %).
    n = len(fp_map)
    fill = 0.30 if n <= 20 else max(0.20, 0.30 * (20 / n) ** 0.5)
    need = math.sqrt(total_area / fill / math.pi) * 2 if total_area > 0 else 0.0
    max_w, max_h = _max_extent(fp_map, list(fp_map))
    need = max(need, math.hypot(max_w, max_h) + 2 * BOARD_MARGIN)
    return max(requested or SMARTWATCH_DIAMETER, need)


def _is_passive(part) -> bool:
    return part is not None and part.category in {
        "passive", "led", "diode", "transistor", "crystal", "protection",
        "mechanical", "switch",
    }


def _net_pairs(project: Project) -> list[tuple[str, str, float]]:
    """Return (refdes_a, refdes_b, weight) edges between connected components.
    Power/GND nets get lower weight than signal nets so signal partners stay
    clustered without GND dragging everything to one place."""
    out: list[tuple[str, str, float]] = []
    for net in project.ir.nets:
        upper = net.name.upper()
        if upper in {"GND", "VSS"} or upper.startswith(("VCC", "VDD", "VBUS", "VBAT", "+")):
            weight = 0.25
        else:
            weight = 1.0
        refdeses = sorted({r for r, _ in net.pins})
        for i in range(len(refdeses)):
            for j in range(i + 1, len(refdeses)):
                out.append((refdeses[i], refdeses[j], weight))
    return out


def _decoupling_pairs(project: Project) -> list[tuple[str, str]]:
    """Identify (cap, host_IC) pairs so we can pull bypass caps right next to
    their IC. A cap is associated with the host IC if both share a power_in
    pin's net and the cap is the smaller of the two on that net."""
    out: list[tuple[str, str]] = []
    cap_refdeses = {
        c.refdes for c in project.ir.components
        if (p := library.get_part(c.mpn)) and p.symbol.lower() in {"device:c"}
    }
    for net in project.ir.nets:
        upper = net.name.upper()
        if not (upper.startswith(("VCC", "VDD", "+3V", "+5V", "3V3", "5V"))):
            continue
        # Sorted so `ics[0]` is stable across runs — a bare set iteration order
        # depends on PYTHONHASHSEED and would make placement nondeterministic
        # (the force-directed solve is otherwise seeded for reproducibility).
        refdeses = sorted({r for r, _ in net.pins})
        ics = [r for r in refdeses if r not in cap_refdeses]
        caps = [r for r in refdeses if r in cap_refdeses]
        if not ics or not caps:
            continue
        # Pair every cap to the FIRST IC on the net — close enough for placement.
        for cap in caps:
            out.append((cap, ics[0]))

    # Companion 2-pin passives on a SIGNAL net: a USB-C CC pull-down, a strap
    # resistor, an I2C pull-up. Their whole job is to sit at the pin they serve,
    # and with GND as their only other net the solver had nothing pulling them
    # in — a weather-station CC resistor landed in the far corner from its
    # receptacle and the net could not be routed at all (review #14 run).
    two_pin = {
        c.refdes for c in project.ir.components
        if len(c.pins) == 2 and (lp := library.get_part(c.mpn)) and lp.category == "passive"
    }
    for net in project.ir.nets:
        if _classify_signal_net(net.name) is False:
            continue
        refdeses = sorted({r for r, _ in net.pins})
        passives = [r for r in refdeses if r in two_pin]
        hosts = [r for r in refdeses if r not in two_pin]
        if not passives or not hosts or len(refdeses) > 4:
            continue
        for r in passives:
            out.append((r, hosts[0]))
    return out


def _classify_signal_net(name: str) -> bool:
    """True when the net is a signal (not a power rail or ground)."""
    upper = name.upper()
    if upper in {"GND", "VSS", "AGND", "DGND"}:
        return False
    return not upper.startswith(("VCC", "VDD", "VBUS", "VBAT", "VIN", "+3V", "+5V", "3V3", "5V", "VM"))


# ---- Force-directed -------------------------------------------------------


def _force_directed(
    project: Project,
    *,
    shape: str,
    board_w: float,
    board_h: float,
    anchors: dict[str, tuple[float, float]] | None = None,
    region: tuple[float, float, float, float] | None = None,
    subset: set[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Lay out component centres using a spring simulator. Returns a
    {refdes -> (cx, cy)} map in board coordinates (mm).

    `region` = (x0, y0, x1, y1) confines the solve to a sub-rectangle of the
    board (the control strip beside a pinned part grid) and `subset` limits
    which refdeses are solved; anything not in the subset is left out of the
    physics entirely (grid members are placed deterministically elsewhere).
    Anchored refs inside the region still act as fixed obstacles."""
    rng = random.Random(0xC0FFEE)  # deterministic
    refdeses = [c.refdes for c in project.ir.components]
    if subset is not None:
        refdeses = [r for r in refdeses if r in subset]
    if not refdeses:
        return {}

    if region is not None:
        rx0, ry0, rx1, ry1 = region
        # Solve in region-local coordinates on a virtual board of the region's
        # size, then translate back. Anchors are translated in the same way.
        local_anchors = (
            {r: (ax - rx0, ay - ry0) for r, (ax, ay) in anchors.items() if r in refdeses}
            if anchors else None
        )
        local = _force_directed(
            project, shape="rect", board_w=rx1 - rx0, board_h=ry1 - ry0,
            anchors=local_anchors, subset=set(refdeses),
        )
        return {r: (x + rx0, y + ry0) for r, (x, y) in local.items()}

    # Pre-compute footprints for repulsion radii.
    footprint_map = {}
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        fp = footprints.for_part(part) if part else footprints._generic(c.mpn)  # noqa: SLF001
        footprint_map[c.refdes] = fp

    cx0 = board_w / 2
    cy0 = board_h / 2

    # Initial random positions (or anchored).
    pos: dict[str, list[float]] = {}
    for r in refdeses:
        if anchors and r in anchors:
            ax, ay = anchors[r]
            pos[r] = [ax, ay]
            continue
        pos[r] = [
            cx0 + rng.uniform(-board_w * 0.25, board_w * 0.25),
            cy0 + rng.uniform(-board_h * 0.25, board_h * 0.25),
        ]

    edges = _net_pairs(project)
    decoupler_edges = _decoupling_pairs(project)

    # Adjacency map for attraction (ideal length ≈ a fraction of the board span).
    span = max(board_w, board_h)
    ideal_len = span / max(8.0, math.sqrt(len(refdeses)))
    temperature = span * INITIAL_TEMP_FRAC

    radii = {r: max(footprint_map[r].width, footprint_map[r].height) / 2 for r in refdeses}
    # Round-board edge maths needs the half-DIAGONAL: a rect body seated at the
    # rim by its max half-extent still pokes its corners past the milled edge
    # (PCB_R02 checks the corners). Rect boards keep per-axis margins.
    edge_radii = {
        r: math.hypot(footprint_map[r].width, footprint_map[r].height) / 2
        for r in refdeses
    }

    for _ in range(ITERATIONS):
        disp: dict[str, list[float]] = {r: [0.0, 0.0] for r in refdeses}

        # Repulsion (every pair).
        for i in range(len(refdeses)):
            for j in range(i + 1, len(refdeses)):
                a = refdeses[i]
                b = refdeses[j]
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                dist2 = dx * dx + dy * dy + 0.001
                # Penalty grows sharply once bodies are inside the keepout, so
                # they settle apart in the (mostly empty) board instead of
                # overlapping — the legalizer then rarely has anything to do.
                min_sep = (radii[a] + radii[b]) * SEPARATION_FACTOR
                if dist2 < min_sep * min_sep:
                    factor = REPEL_K * OVERLAP_REPEL_BOOST * (min_sep * min_sep) / dist2
                else:
                    factor = REPEL_K * (ideal_len * ideal_len) / dist2
                disp[a][0] += dx * factor
                disp[a][1] += dy * factor
                disp[b][0] -= dx * factor
                disp[b][1] -= dy * factor

        # Attraction along net edges.
        for a, b, weight in edges:
            if a not in pos or b not in pos:
                continue
            dx = pos[a][0] - pos[b][0]
            dy = pos[a][1] - pos[b][1]
            dist = math.sqrt(dx * dx + dy * dy) + 0.001
            factor = ATTRACT_K * weight * (dist - ideal_len)
            ux = dx / dist
            uy = dy / dist
            disp[a][0] -= ux * factor
            disp[a][1] -= uy * factor
            disp[b][0] += ux * factor
            disp[b][1] += uy * factor

        # Extra pull for decoupling caps toward their IC.
        for cap, ic in decoupler_edges:
            if cap not in pos or ic not in pos:
                continue
            dx = pos[cap][0] - pos[ic][0]
            dy = pos[cap][1] - pos[ic][1]
            dist = math.sqrt(dx * dx + dy * dy) + 0.001
            factor = DECOUPLER_BIAS_K * dist
            ux = dx / dist
            uy = dy / dist
            disp[cap][0] -= ux * factor
            disp[cap][1] -= uy * factor

        # Edge constraint — pulls components back into the outline.
        for r in refdeses:
            x, y = pos[r]
            if _RING is not None:
                # Polygon: pull toward the centroid once the body's corners are
                # closer to an edge than they may be. Same force law as the
                # disc, with `clearance` standing in for the radial distance.
                need = BOARD_MARGIN + edge_radii[r]
                slack = board_shape.clearance(_RING, x, y) - need
                if slack < 0:
                    tx, ty = board_shape.centroid(_RING)
                    dx, dy = tx - x, ty - y
                    d = math.hypot(dx, dy)
                    if d > 1e-6:
                        pull = EDGE_K * (-slack)
                        disp[r][0] += (dx / d) * pull
                        disp[r][1] += (dy / d) * pull
            elif shape == "round":
                # Centre at (cx0, cy0), radius = board_w/2.
                radius = board_w / 2 - BOARD_MARGIN - edge_radii[r]
                rx = x - cx0
                ry = y - cy0
                d = math.sqrt(rx * rx + ry * ry)
                if d > radius and d > 0:
                    pull = EDGE_K * (d - radius)
                    disp[r][0] -= (rx / d) * pull
                    disp[r][1] -= (ry / d) * pull
            else:
                margin = BOARD_MARGIN + radii[r]
                if x < margin:
                    disp[r][0] += EDGE_K * (margin - x)
                elif x > board_w - margin:
                    disp[r][0] -= EDGE_K * (x - (board_w - margin))
                if y < margin:
                    disp[r][1] += EDGE_K * (margin - y)
                elif y > board_h - margin:
                    disp[r][1] -= EDGE_K * (y - (board_h - margin))

        # Apply with temperature limit.
        for r in refdeses:
            if anchors and r in anchors:
                continue  # anchored — don't move
            d = disp[r]
            mag = math.sqrt(d[0] * d[0] + d[1] * d[1])
            if mag > 0:
                limit = min(mag, temperature)
                pos[r][0] += (d[0] / mag) * limit
                pos[r][1] += (d[1] / mag) * limit

            # HARD CLAMP — guarantees no component leaves the board, even if
            # the simulation goes unstable. Without this, repulsion can fling
            # parts arbitrarily far before the edge force catches up.
            if shape == "round":
                radius = board_w / 2 - BOARD_MARGIN - edge_radii[r]
                rx = pos[r][0] - cx0
                ry = pos[r][1] - cy0
                d_from_center = math.sqrt(rx * rx + ry * ry)
                if d_from_center > radius and d_from_center > 0:
                    pos[r][0] = cx0 + (rx / d_from_center) * radius
                    pos[r][1] = cy0 + (ry / d_from_center) * radius
            else:
                margin = BOARD_MARGIN + radii[r]
                pos[r][0] = max(margin, min(board_w - margin, pos[r][0]))
                pos[r][1] = max(margin, min(board_h - margin, pos[r][1]))

        temperature *= COOL_FACTOR

    return {r: (pos[r][0], pos[r][1]) for r in refdeses}


# ---- Overlap legalization -------------------------------------------------


# mm gap between bodies after separation. KiCad courtyards extend ~0.25 mm
# past each body, so a gap below ~0.5 mm trips courtyards_overlap DRC and
# leaves edge pads close enough to short — 0.6 clears the courtyard sum with a
# little pad breathing room. (Global bumps past this over-space cramped boards
# and cause new collisions; the row-packer inflates locked obstacles locally.)
OVERLAP_CLEARANCE = 0.6
# Extra keepout, ONLY for row-packing edge parts against locked bodies. The
# locked HAT header is a dense THT pad field; where a terminal's PTH pads meet
# it at a corner, body-clearance alone still shorts. Padding the obstacle
# interval clears the header's pads without over-spacing the interior.
EDGE_OBSTACLE_MARGIN = 2.0
LEGALIZE_ITERATIONS = 60
LEGALIZE_EPS = 1e-3       # mm slack so a part seated at *exactly* the clearance
#                           distance reads as clear, not "still touching" — a
#                           strict `<` with float rounding otherwise rejects the
#                           one valid escape and the legalizer gets stuck.


def _legalize_overlaps(
    positions: dict[str, tuple[float, float]],
    fp_map: dict,
    board_w: float,
    board_h: float,
    locked_refs: set[str],
    shape: str,
    rotations: dict[str, int] | None = None,
    edge_refs: set[str] | None = None,
    flush_refs: set[str] | None = None,
) -> None:
    """Nudge overlapping component bodies apart in-place until the layout is
    DRC-clean (PCB_R01). Force-directed placement minimizes wire length but
    doesn't guarantee non-overlap — small bypass caps in particular get pulled
    on top of their host IC (or each other, at identical coordinates). Beyond
    the DRC error, an overlap buries the smaller part's pads under the larger
    body's router keepout, so its nets can't be escape-routed. This final sweep
    pushes the *smaller* part of each overlapping pair aside (the larger
    IC/connector stays put), keeping everything inside the board outline.

    Locked parts never move; if both parts of a pair are locked the overlap is
    left for the user to resolve."""
    refs = [r for r in positions if r in fp_map]
    cx0, cy0 = board_w / 2, board_h / 2
    rot = rotations or {}
    edges = edge_refs or set()
    flush = flush_refs or set()

    def half(r: str) -> tuple[float, float]:
        fp = fp_map[r]
        # 90/270° placements swap the body's world extents — a rotated 2x20
        # header is 52 mm wide, not 6 mm. Ignoring this let edge parts overlap
        # the rotated HAT header undetected (the unroutable-shorts bug).
        hw, hh = fp.width / 2, fp.height / 2
        return (hh, hw) if rot.get(r, 0) % 180 else (hw, hh)

    def clamp(r: str, x: float, y: float) -> tuple[float, float]:
        hw, hh = half(r)
        margin = FLUSH_EDGE_MARGIN if r in flush else BOARD_MARGIN
        if _RING is not None:
            return board_shape.clamp(
                _RING, x, y, board_shape.half_diagonal(hw, hh) + margin
            )
        if shape == "round":
            # Half-diagonal, not max half-extent — the body's corners are the
            # first thing to leave a circular outline (PCB_R02 checks them).
            radius = board_w / 2 - BOARD_MARGIN - math.hypot(hw, hh)
            rx, ry = x - cx0, y - cy0
            d = math.hypot(rx, ry)
            if d > radius > 0:
                x = cx0 + (rx / d) * radius
                y = cy0 + (ry / d) * radius
            return x, y
        x = max(margin + hw, min(board_w - margin - hw, x))
        y = max(margin + hh, min(board_h - margin - hh, y))
        return x, y

    area = {r: fp_map[r].width * fp_map[r].height for r in refs}

    # Parts whose solid volume is NOT one box (an Arduino shield's header pair
    # is two strips along opposite edges with the whole middle free). Cached as
    # offsets from the part's BODY CENTRE — the space the solver works in — and
    # rotated into world axes, so the fast single-box path below is untouched
    # for the ~99 % of parts that are a plain rectangle.
    multi: dict[str, list[tuple[float, float, float, float]]] = {}
    for r in refs:
        fp = fp_map[r]
        rects = getattr(fp, "bodyRects", None)
        if not rects:
            continue
        ox, oy = _pad_centroid(fp)
        out = []
        for x0, y0, x1, y1 in rects:
            corners = [geometry.rotate_xy(x - ox, y - oy, rot.get(r, 0))
                       for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            xs = [c[0] for c in corners]
            ys = [c[1] for c in corners]
            out.append((min(xs), min(ys), max(xs), max(ys)))
        multi[r] = out

    def _rects_at(r: str, px: float, py: float,
                  hw: float, hh: float) -> list[tuple[float, float, float, float]]:
        if r in multi:
            return [(px + x0, py + y0, px + x1, py + y1) for x0, y0, x1, y1 in multi[r]]
        return [(px - hw, py - hh, px + hw, py + hh)]

    def clears(ax: float, ay: float, hw_a: float, hh_a: float, b: str,
               a_ref: str | None = None) -> bool:
        """True if a body of half-extents (hw_a, hh_a) at (ax, ay) is clear of
        component b. The EPS makes a part seated at exactly the clearance read as
        clear (a strict `<` would call it touching and stick)."""
        bx, by = positions[b]
        bhw, bhh = half(b)
        if a_ref in multi or b in multi:
            # Multi-strip part on either side: compare the real rectangles, or
            # the empty space BETWEEN a shield header's two strips reads as
            # occupied and every part gets shoved off the board's middle.
            gap = OVERLAP_CLEARANCE - LEGALIZE_EPS
            for ax0, ay0, ax1, ay1 in _rects_at(a_ref, ax, ay, hw_a, hh_a) \
                    if a_ref else [(ax - hw_a, ay - hh_a, ax + hw_a, ay + hh_a)]:
                for bx0, by0, bx1, by1 in _rects_at(b, bx, by, bhw, bhh):
                    if (ax0 < bx1 + gap and ax1 > bx0 - gap
                            and ay0 < by1 + gap and ay1 > by0 - gap):
                        return False
            return True
        return (
            abs(ax - bx) >= hw_a + bhw + OVERLAP_CLEARANCE - LEGALIZE_EPS
            or abs(ay - by) >= hh_a + bhh + OVERLAP_CLEARANCE - LEGALIZE_EPS
        )

    def overlaps(a: str, b: str) -> bool:
        ax, ay = positions[a]
        ahw, ahh = half(a)
        return not clears(ax, ay, ahw, ahh, b, a)

    def collisions_at(mref: str, px: float, py: float) -> int:
        """How many other components a body would overlap if seated at (px, py).
        Scoring against *every* neighbour — not just the anchor — is what stops
        a part being walked off one body straight onto another (the corner-jam
        oscillation: shove off the connector → land on the LDO → shove back)."""
        mhw, mhh = half(mref)
        return sum(
            1 for r in refs
            if r != mref and not clears(px, py, mhw, mhh, r, mref)
        )

    for _ in range(LEGALIZE_ITERATIONS):
        moved = False
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                a, b = refs[i], refs[j]
                if not overlaps(a, b):
                    continue

                a_locked, b_locked = a in locked_refs, b in locked_refs
                if a_locked and b_locked:
                    continue
                # An EDGE part (a connector that has to be pluggable) must not
                # be the one that moves: it is small, so the size rule below
                # would push it inboard — which is exactly how a USB-C
                # receptacle ended up stranded in open board area with no edge
                # access (2026-08-20 review, finding 5). It yields only to
                # another edge part or a locked one.
                a_edge, b_edge = a in edges, b in edges
                if a_locked or (b_edge and not a_edge and not b_locked):
                    mover, anchor = b, a
                elif b_locked or (a_edge and not b_edge):
                    mover, anchor = a, b
                elif area[a] > area[b]:
                    mover, anchor = b, a
                else:
                    mover, anchor = a, b
                if mover in locked_refs:
                    continue

                mx, my = positions[mover]
                lx, ly = positions[anchor]
                mhw, mhh = half(mover)
                lhw, lhh = half(anchor)
                gap_x = lhw + mhw + OVERLAP_CLEARANCE
                gap_y = lhh + mhh + OVERLAP_CLEARANCE

                # Seat the mover just clear of the anchor on each side; pick the
                # side that's clear of *every* part (fewest residual collisions),
                # then the nearest such side. This walks corner-jammed parts into
                # the open board interior instead of bouncing them between
                # neighbours or grinding them into the edge.
                best: tuple[float, float] | None = None
                best_score: tuple[int, float] | None = None
                for cx, cy in (
                    (lx + gap_x, my),  # right of anchor
                    (lx - gap_x, my),  # left
                    (mx, ly + gap_y),  # below
                    (mx, ly - gap_y),  # above
                ):
                    px, py = clamp(mover, cx, cy)
                    if not clears(px, py, mhw, mhh, anchor, mover):
                        continue  # clamp pulled it back into the anchor → no room
                    score = (collisions_at(mover, px, py), (px - mx) ** 2 + (py - my) ** 2)
                    if best_score is None or score < best_score:
                        best_score, best = score, (px, py)

                if best is None:
                    # Every side is blocked (over-constrained corner): shove the
                    # mover toward the board centre, where there is open space.
                    vx, vy = mx - cx0, my - cy0
                    if abs(vx) < 1e-6 and abs(vy) < 1e-6:
                        vx, vy = -1.0, -1.0
                    mag = math.hypot(vx, vy)
                    step = max(gap_x, gap_y)
                    best = clamp(mover, mx - vx / mag * step, my - vy / mag * step)

                positions[mover] = best
                moved = True
        if not moved:
            break

    # Guarantee: any part still overlapping after the loop is relocated to the
    # nearest grid point that is clear of everything, scanning outward from the
    # board centre. The board is mostly empty, so a free slot always exists.
    for mref in refs:
        if mref in locked_refs:
            continue
        if not any(overlaps(mref, r) for r in refs if r != mref):
            continue
        mhw, mhh = half(mref)
        found = None
        step = max(2.0 * mhw, 2.0 * mhh, 1.0)
        rings = int(max(board_w, board_h) / step) + 2
        for ring in range(rings):
            for sx in range(-ring, ring + 1):
                for sy in range(-ring, ring + 1):
                    if max(abs(sx), abs(sy)) != ring:
                        continue  # only the ring's perimeter
                    px, py = clamp(mref, cx0 + sx * step, cy0 + sy * step)
                    if all(clears(px, py, mhw, mhh, r, mref) for r in refs if r != mref):
                        found = (px, py)
                        break
                if found:
                    break
            if found:
                break
        if found:
            positions[mref] = found


# ---- Physical part grids (LED matrix / keypad) -----------------------------
# A `ComponentArray` with grid=True is a user-visible physical arrangement: the
# parts are pinned at their pitch and the board is sized around them, with the
# remaining "control" parts solved in a strip beside the grid — never inside
# it. This is what turns "10x10 LED matrix" into a board that IS a 10x10 grid.

GRID_MARGIN = 6.0     # mm from board edge to the grid's outer courtyard — leaves
#                       room for a corner M3 mounting boss (MOUNT_CLEAR_R 2.5)
GRID_GAP = 4.0        # mm between stacked grid blocks
STRIP_GAP = 4.0       # mm between the grid column and the control strip
STRIP_MIN_W = 18.0    # mm — narrowest useful control strip
FREE_DENSITY = 0.22   # same footprint-area density target as the plain heuristic
# Above this courtyard area a part's footprint already carries its own clearance
# (an RF module's antenna keep-out, a big connector shell) — sizing the board at
# 22 % density of THAT was the "generic ESP32 board is 120 mm wide" complaint:
# the ESP32-WROOM-32 courtyard is 48x41 mm (KiCad's includes the antenna
# keep-out) and at 22 % it alone demanded ~9,000 mm² of board.
BIG_PART_AREA = 400.0
BIG_PART_FACTOR = 1.3
# Room past the largest part on each side so a corner M3 boss (MOUNT_CLEAR_R
# 2.5 at a ~3.5 mm inset) can clear it — a compact board must not make the
# collision-aware hole placer fall back onto the module (MECH_R04).
MOUNT_ROOM = BOARD_MARGIN + 7.0
# How far INSIDE the outline a flush-seated connector's body may sit. The
# generic BOARD_MARGIN left a USB-C 2.5 mm inboard, where no cable can seat
# (2026-08-20 review). Its pads stay inboard of the shell, so copper-to-edge
# clearance is unaffected.
FLUSH_EDGE_MARGIN = 0.2


def _physical_grids(project: Project) -> list:
    alive = {c.refdes for c in project.ir.components}
    out = []
    for arr in getattr(project.ir, "arrays", None) or []:
        if not arr.grid or not arr.pitch_mm:
            continue
        if not any(r in alive for r in arr.refdeses):
            continue
        out.append(arr)
    return out


def _grid_plan(
    project: Project, fp_map: dict
) -> tuple[dict[str, tuple[float, float]], float, float, list[dict], dict[str, str]]:
    """Pin every physical-grid member. Returns (anchor-space positions,
    grid-column width, grid-column height, per-block info, per-part placement
    reasons). Blocks stack
    vertically (largest first) in a column at the board's left; row 0 of each
    block is at the TOP so the PCB matches the schematic's row order."""
    alive = {c.refdes for c in project.ir.components}
    blocks = []
    for arr in _physical_grids(project):
        member_fps = [fp_map[r] for r in arr.refdeses if r in fp_map]
        fw = max((f.courtyard_w or f.width) for f in member_fps) if member_fps else 2.0
        fh = max((f.courtyard_h or f.height) for f in member_fps) if member_fps else 2.0
        pitch = arr.pitch_mm
        bw = (arr.cols - 1) * pitch + fw
        bh = (arr.rows - 1) * pitch + fh
        blocks.append({"arr": arr, "fw": fw, "fh": fh, "pitch": pitch, "w": bw, "h": bh})
    blocks.sort(key=lambda b: -(b["w"] * b["h"]))

    col_w = max((b["w"] for b in blocks), default=0.0)
    col_h = sum(b["h"] for b in blocks) + GRID_GAP * max(0, len(blocks) - 1)

    positions: dict[str, tuple[float, float]] = {}
    reasons: dict[str, str] = {}
    # Row 0 at the smallest y — the PCB canvas draws y downward (see the edge
    # naming in _assemble_board), so this matches the schematic's row order.
    y_cursor = GRID_MARGIN
    for b in blocks:
        arr, pitch = b["arr"], b["pitch"]
        # Centre each block horizontally in the grid column.
        x_left = GRID_MARGIN + (col_w - b["w"]) / 2 + b["fw"] / 2
        y_row0 = y_cursor + b["fh"] / 2
        for idx, ref in enumerate(arr.refdeses):
            if ref not in alive or ref not in fp_map:
                continue
            r, c = divmod(idx, arr.cols)
            cx = x_left + c * pitch
            cy = y_row0 + r * pitch
            # Body-centre -> footprint anchor (rotation 0), the PlacedComponent
            # convention; _assemble_board converts anchors back to centres.
            ox, oy = _pad_centroid(fp_map[ref])
            positions[ref] = (cx - ox, cy - oy)
        b["origin"] = (round(x_left, 3), round(y_row0, 3))
        y_cursor += b["h"] + GRID_GAP
        for idx, ref in enumerate(arr.refdeses):
            if ref in positions:
                r, c = divmod(idx, arr.cols)
                reasons[ref] = f"grid {arr.key}: row {r}, col {c} @ {pitch} mm pitch"
    info = [
        {
            "array": b["arr"].key, "rows": b["arr"].rows, "cols": b["arr"].cols,
            "pitch_mm": b["pitch"], "extent_mm": (round(b["w"], 2), round(b["h"], 2)),
        }
        for b in blocks
    ]
    return positions, col_w, col_h, info, reasons


def has_physical_grid(project: Project) -> bool:
    """True when the design carries at least one pinned part grid."""
    return bool(_physical_grids(project))


# Tried and REVERTED (2026-08-19): easing the small-part density on dense
# RECT boards (22 % -> 16 % at 60 parts) to give the router room. Measured on
# ten saved builds: the 62-part relay board went 6 -> 12 rats — a bigger
# rectangle means longer nets crossing the MCU corridor, so "more room"
# backfires. Round boards DID improve with easing (see _round_diameter's fill),
# and that part is kept. Rect density stays flat; the real lever there is
# fan-out/escape quality and FreeRouting, not area.
def _small_density(n_small: int) -> float:  # noqa: ARG001 — kept as the single knob
    return FREE_DENSITY


def target_board_area(fp_map: dict, refs=None) -> float:
    """Board area a set of parts wants: small parts at FREE_DENSITY (they need
    routing room around them — eased on dense boards, see _small_density),
    big-courtyard parts at BIG_PART_FACTOR x their own courtyard (which
    already is the clearance)."""
    keys = list(refs if refs is not None else fp_map)
    small = [fp_map[r] for r in keys if fp_map.get(r) is not None and fp_map[r].width * fp_map[r].height < BIG_PART_AREA]
    density = _small_density(len(small))
    total = 0.0
    for r in keys:
        fp = fp_map.get(r)
        if fp is None:
            continue
        a = fp.width * fp.height
        total += a * BIG_PART_FACTOR if a >= BIG_PART_AREA else a / density
    return total


def _stack_need(fp_map: dict, refs, avail: float, along: str) -> float:
    """Minimum extent PERPENDICULAR to `along` ("x" or "y") that the two largest
    free parts need: if they cannot sit side by side within `avail` along that
    axis they must stack, so the region needs both their perpendicular extents
    (an 88 mm 18650 holder + a 48 mm ESP32 keep-out on a 100 mm strip). The
    area model alone can't see packing and left the legalizer to shove the
    holder into the sensor row."""
    fps = sorted((fp_map[r] for r in refs if r in fp_map),
                 key=lambda f: -(f.width * f.height))[:2]
    if not fps:
        return 0.0
    if len(fps) == 1:
        f = fps[0]
        return (f.height if along == "x" else f.width) + 2 * BOARD_MARGIN
    a, b = fps
    wa, ha = (a.width, a.height) if along == "x" else (a.height, a.width)
    wb, hb = (b.width, b.height) if along == "x" else (b.height, b.width)
    if wa + wb + 3 * BOARD_MARGIN > avail:
        return ha + hb + 3 * BOARD_MARGIN
    return max(ha, hb) + 2 * BOARD_MARGIN


def _max_extent(fp_map: dict, refs) -> tuple[float, float]:
    """(max width, max height) over the parts — the free region must at least
    hold the largest one, or it lands past the board edge (PCB_R02)."""
    w = h = 0.0
    for r in refs:
        fp = fp_map.get(r)
        if fp is not None:
            w = max(w, fp.width)
            h = max(h, fp.height)
    return w, h


# ---- Public API -----------------------------------------------------------


def auto_place(project: Project) -> Board:
    """Compute a board layout for the given project."""
    components = project.ir.components
    if not components:
        return Board(
            width=50.0, height=40.0, shape="rect",
            components=[], rats=[], auto_placed=True,
        )

    # Pre-fetch footprints once.
    fp_map: dict[str, footprints.Footprint] = {}
    for c in components:
        part = library.get_part(c.mpn)
        if part:
            fp_map[c.refdes] = footprints.for_part(part)
        else:
            fp_map[c.refdes] = footprints._generic(c.mpn)  # noqa: SLF001

    form = requested_form(project)

    # Editable-PCB workflow (#34): carry forward any locked parts from a prior
    # board so re-placing never discards a user's manual placement. Reuse the
    # previous outline so locked coordinates stay valid.
    prev = {pc.refdes: pc for pc in project.board.components} if project.board else {}
    locked_anchors = {
        ref: (pc.x, pc.y) for ref, pc in prev.items() if pc.locked
    }

    # Physical part grids (LED matrix / keypad): pin the grid, size the board
    # around it, and solve the remaining control parts in a strip beside it.
    grid_anchors, grid_w, grid_h, _, grid_reasons = _grid_plan(project, fp_map)
    if grid_anchors:
        free_refs = [c.refdes for c in components if c.refdes not in grid_anchors]
        if free_refs:
            # Control strip beside (right of) or below the grid — whichever
            # gives the smaller board; a short 1xN LED row wants the controls
            # UNDER it, a tall matrix wants them beside. The strip must hold
            # the largest free part (an ESP32 module's 48x41 courtyard) or it
            # hangs off the edge.
            area = target_board_area(fp_map, free_refs)
            max_w, max_h = _max_extent(fp_map, free_refs)
            # A SMALL grid with a LARGE control section must not pin the strip
            # to the grid's extent: three 1x4/2x2 arrays (44 mm tall) beside 50
            # free parts made a 290 x 56 mm strip (review #20). Let the strip's
            # cross dimension grow to the free area's natural aspect ratio.
            nat_h = math.sqrt(area / DEFAULT_BOARD_AR)
            nat_w = math.sqrt(area * DEFAULT_BOARD_AR)
            # Option A: strip to the right.
            h_a = max(2 * GRID_MARGIN + grid_h, max_h + 2 * MOUNT_ROOM, nat_h)
            sw = max(STRIP_MIN_W, max_w + MOUNT_ROOM, area / max(h_a - 2 * BOARD_MARGIN, 1.0),
                     _stack_need(fp_map, free_refs, h_a - 2 * BOARD_MARGIN, "y"))
            w_a = GRID_MARGIN + grid_w + STRIP_GAP + sw + BOARD_MARGIN
            # Option B: strip below.
            w_b = max(2 * GRID_MARGIN + grid_w, max_w + 2 * MOUNT_ROOM, nat_w)
            sh = max(STRIP_MIN_W, max_h + MOUNT_ROOM, area / max(w_b - 2 * BOARD_MARGIN, 1.0),
                     _stack_need(fp_map, free_refs, w_b - 2 * BOARD_MARGIN, "x"))
            h_b = GRID_MARGIN + grid_h + STRIP_GAP + sh + BOARD_MARGIN
            # Smaller board wins; beside (landscape, DEFAULT_BOARD_AR) only
            # gets the tie when the two are within 5 % — a long thin board
            # (163x60 for a robot) is what the reviewers called "not shaped
            # for the product".
            if w_a * h_a <= w_b * h_b * 1.05:
                board_w, board_h = w_a, h_a
                region = (GRID_MARGIN + grid_w + STRIP_GAP, BOARD_MARGIN, board_w - BOARD_MARGIN, board_h - BOARD_MARGIN)
            else:
                board_w, board_h = w_b, h_b
                region = (BOARD_MARGIN, GRID_MARGIN + grid_h + STRIP_GAP, board_w - BOARD_MARGIN, board_h - BOARD_MARGIN)
        else:
            board_w = 2 * GRID_MARGIN + grid_w
            board_h = 2 * GRID_MARGIN + grid_h
            region = None
        board_w = max(28.0, board_w)
        board_h = max(20.0, board_h)
        if project.board is not None and locked_anchors:
            # Keep a user's pinned parts valid: never shrink below the prior outline.
            board_w = max(board_w, project.board.width)
            board_h = max(board_h, project.board.height)
        anchors = dict(grid_anchors)
        anchors.update(locked_anchors)  # a hand-pinned part wins over the grid slot
        return _assemble_board(
            project, components, fp_map, "rect", board_w, board_h, anchors, prev,
            region=region, extra_reasons=grid_reasons,
        )

    if project.board is not None and locked_anchors:
        shape = project.board.shape
        board_w = project.board.width
        board_h = project.board.height
        anchors = dict(locked_anchors)
        # Round-board display anchor still applies on top of locked parts.
        if shape == "round":
            disp = _display_anchor_ref(project, fp_map)
            if disp and disp not in anchors:
                anchors[disp] = _display_center_anchor(
                    disp, fp_map, board_w / 2, board_h / 2
                )
        return _assemble_board(
            project, components, fp_map, shape, board_w, board_h, anchors, prev
        )

    if form is not None and form.shape == "polygon":
        ring = polygon_ring(form, fp_map)
        if ring is not None:
            x0, y0, x1, y1 = board_shape.bbox(ring)
            # width/height stay the BOUNDING BOX: every consumer that only
            # needs a canvas size keeps working, and the ring carries the truth.
            return _assemble_board(
                project, components, fp_map, "polygon", x1 - x0, y1 - y0,
                dict(locked_anchors), prev, ring=ring,
            )

    if form is not None and form.shape == "round":
        shape = "round"
        board_w = board_h = _round_diameter(fp_map, form.diameter_mm)
        # Anchor: the display (the product's face) dead centre.
        anchors: dict[str, tuple[float, float]] = {}
        disp = _display_anchor_ref(project, fp_map)
        if disp:
            anchors[disp] = _display_center_anchor(
                disp, fp_map, board_w / 2, board_h / 2
            )
    else:
        shape = "rect"
        # Heuristic size based on total component area + breathing room
        # (big-courtyard parts count at ~their own area — see BIG_PART_AREA).
        target_area = max(1500.0, target_board_area(fp_map))
        max_w, max_h = _max_extent(fp_map, list(fp_map))
        req_w = form.width_mm if form else None
        req_h = form.height_mm if form else None
        if req_w and req_h:
            board_w, board_h = req_w, req_h
        elif req_w:
            board_w = req_w
            board_h = max(20.0, target_area / board_w, max_h + 2 * MOUNT_ROOM)
        elif req_h:
            board_h = req_h
            board_w = max(28.0, target_area / board_h, max_w + 2 * MOUNT_ROOM)
        else:
            board_h = math.sqrt(target_area / DEFAULT_BOARD_AR)
            board_w = board_h * DEFAULT_BOARD_AR
            board_h = max(20.0, board_h, max_h + 2 * MOUNT_ROOM)
            board_w = max(28.0, board_w, max_w + 2 * MOUNT_ROOM)
        # A requested size is honoured as given — the layout DRC then reports
        # overlap/off-board honestly if it's too dense — but the single largest
        # part must at least physically fit inside the outline, or every result
        # (place, route, DRC) is meaningless. R57 reports the growth.
        board_w = max(board_w, max_w + 2 * BOARD_MARGIN)
        board_h = max(board_h, max_h + 2 * BOARD_MARGIN)
        # ...and when the design has two big bodies, ONE of the axes has to be
        # wide enough to stand them side by side, or they overlap by
        # construction. Grow the cheaper axis only (never both).
        need_w, need_h = _two_largest_need(fp_map)
        if need_w and board_w < need_w and board_h < need_h:
            if need_w / max(board_w, 1e-6) <= need_h / max(board_h, 1e-6):
                board_w = need_w
            else:
                board_h = need_h
        anchors = {}

    return _assemble_board(
        project, components, fp_map, shape, board_w, board_h, anchors, prev
    )


def _two_largest_need(fp_map: dict, refs=None) -> tuple[float, float]:
    """Width/height the two LARGEST bodies need to sit side by side.

    The area model can't see packing: a 43 x 43 mm motor-driver module beside an
    18 x 25.5 mm ESP32 is only ~2300 mm2 of body, so a 65 x 62 mm board looks
    generous — but 43 + 18 + margins does not fit across it, the legalizer has
    nowhere to push, and PCB_R01 reports an overlap the placer could not avoid.
    Surfaced by the 2026-08-20 module-body curation, which finally gave those
    modules their real 43 mm bodies instead of a 3.6 mm pin header."""
    keys = list(refs if refs is not None else fp_map)
    fps = sorted(
        (fp_map[r] for r in keys if fp_map.get(r) is not None),
        key=lambda f: f.width * f.height,
        reverse=True,
    )[:2]
    if len(fps) < 2:
        return (0.0, 0.0)
    a, b = fps
    if a.width * a.height < BIG_PART_AREA:
        return (0.0, 0.0)      # nothing big enough to dominate the packing
    gap = OVERLAP_CLEARANCE + 2 * BOARD_MARGIN
    return (a.width + b.width + gap, a.height + b.height + gap)

def _half_wh_pre(ref, fp_map, edge_rotations, prev) -> tuple[float, float]:
    """Half width/height of a part in the board frame, before the `rotations`
    map is assembled (used by the companion re-seat above)."""
    fp = fp_map.get(ref)
    if fp is None:
        return 1.0, 1.0
    rot = prev[ref].rotation if ref in prev and prev[ref].locked else edge_rotations.get(ref, 0)
    hw, hh = fp.width / 2, fp.height / 2
    return (hh, hw) if rot % 180 else (hw, hh)


def edge_facing_rotation(
    part, x: float, y: float, board_w: float, board_h: float
) -> int | None:
    """Rotation that points `part`'s mating face (matingFaceDir) off-board out
    the edge nearest to (x, y), or None when the part carries no hint (don't
    guess). Single authority for the mating-rotation formula — the auto-place
    edge loop and the board-edit move endpoint both use it."""
    mating = getattr(part, "matingFaceDir", None) if part else None
    if not mating:
        return None
    dists = {"left": x, "right": board_w - x, "top": y, "bottom": board_h - y}
    outward = {"left": "-X", "right": "+X", "top": "-Y", "bottom": "+Y"}[
        min(dists, key=dists.get)
    ]
    return (_DIR_ANGLE[outward] - _DIR_ANGLE[mating]) % 360


def _pad_centroid(fp) -> tuple[float, float]:
    """Centre of the pad bbox relative to the footprint anchor. Pin-1-anchored
    footprints (pin headers, terminal blocks) put the body up to half a
    footprint away from the anchor — (0, 0) only for centred footprints."""
    if not fp.pads:
        return (0.0, 0.0)
    xs = [p.x for p in fp.pads]
    ys = [p.y for p in fp.pads]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def _assemble_board(
    project: Project,
    components: list,
    fp_map: dict,
    shape: str,
    board_w: float,
    board_h: float,
    anchors: dict[str, tuple[float, float]],
    prev: dict[str, PlacedComponent],
    region: tuple[float, float, float, float] | None = None,
    extra_reasons: dict[str, str] | None = None,
    ring: list[tuple[float, float]] | None = None,
) -> Board:
    """Run the placement solve and build the Board. Shared by the fresh-place
    and locked-re-place paths so both honour constraints + carry locked state.
    `region` (grid boards) confines the free-part solve to the control strip;
    anchored parts outside it (the pinned grid) are excluded from the physics
    but stay fixed obstacles for edge row-packing and the legalizer.

    The solve runs in BODY-CENTRE space: every geometric assumption inside
    (repulsion radii, half-extents, edge insets, overlap legalization) treats a
    position as the centre of the part's body, but `PlacedComponent.x/y` is the
    footprint ANCHOR — for pin-1-anchored footprints those differ by up to half
    a footprint, so solving in anchor space had the solver separating phantom
    boxes while the real bodies overlapped (the unroutable-dense-board bug).
    Fixed anchors convert in on entry and every position converts back to an
    anchor on exit with the part's final rotation, so locked/form-factor parts
    round-trip to their exact prior coordinates."""
    global _RING
    _RING = ring          # read by the edge force, the clamp and the legalizer
    try:
        return _assemble_board_inner(
            project, components, fp_map, shape, board_w, board_h, anchors, prev,
            region=region, extra_reasons=extra_reasons, ring=ring,
        )
    finally:
        _RING = None      # a leaked ring would constrain the NEXT board


def _assemble_board_inner(
    project: Project,
    components: list,
    fp_map: dict,
    shape: str,
    board_w: float,
    board_h: float,
    anchors: dict[str, tuple[float, float]],
    prev: dict[str, PlacedComponent],
    region: tuple[float, float, float, float] | None = None,
    extra_reasons: dict[str, str] | None = None,
    ring: list[tuple[float, float]] | None = None,
) -> Board:
    offsets = {ref: _pad_centroid(fp) for ref, fp in fp_map.items()}

    def _center_shift(ref: str, rot: int) -> tuple[float, float]:
        ox, oy = offsets.get(ref, (0.0, 0.0))
        return geometry.rotate_xy(ox, oy, rot)

    converted_anchors: dict[str, tuple[float, float]] = {}
    for ref, (ax, ay) in anchors.items():
        sx, sy = _center_shift(ref, prev[ref].rotation if ref in prev else 0)
        converted_anchors[ref] = (ax + sx, ay + sy)
    anchors = converted_anchors

    if region is not None:
        # Grid board: the pinned parts are already placed; solve only the free
        # ones inside the control strip. Anchors that fall inside the strip
        # (hand-locked parts) still take part as fixed obstacles.
        rx0, ry0, rx1, ry1 = region
        free = {c.refdes for c in components if c.refdes not in anchors}
        in_strip = {
            r for r, (ax, ay) in anchors.items() if rx0 <= ax <= rx1 and ry0 <= ay <= ry1
        }
        positions = dict(anchors)
        if len(free) >= 10:
            positions.update(_force_directed(
                project, shape="rect", board_w=board_w, board_h=board_h,
                anchors={r: anchors[r] for r in in_strip},
                region=region, subset=free | in_strip,
            ))
        else:
            free_sorted = [c.refdes for c in components if c.refdes in free]
            cols = max(1, int(math.sqrt(max(1, len(free_sorted)))))
            rows_n = max(1, math.ceil(len(free_sorted) / cols))
            for idx, ref in enumerate(free_sorted):
                row, col = divmod(idx, cols)
                positions[ref] = (
                    rx0 + (col + 0.5) * (rx1 - rx0) / cols,
                    ry0 + (row + 0.5) * (ry1 - ry0) / rows_n,
                )
    elif len(components) >= 10:
        positions = _force_directed(
            project,
            shape=shape,
            board_w=board_w,
            board_h=board_h,
            anchors=anchors,
        )
    else:
        # Tiny designs: just space them out evenly.
        positions = {}
        cols = max(1, int(math.sqrt(len(components))))
        for idx, c in enumerate(components):
            row = idx // cols
            col = idx % cols
            positions[c.refdes] = (
                BOARD_MARGIN + (col + 0.5) * (board_w - 2 * BOARD_MARGIN) / cols,
                BOARD_MARGIN + (row + 0.5) * (board_h - 2 * BOARD_MARGIN) / max(1, math.ceil(len(components) / cols)),
            )
        # Anchored parts (a round board's centred display, grid slots) are
        # pinned regardless of design size — the even spread ignored them and
        # a 6-part watch put its display in a corner.
        positions.update(anchors)

    # Locked parts keep their exact prior coordinates regardless of the solve
    # (converted to body-centre space; the exit conversion restores them).
    for ref, pc in prev.items():
        if pc.locked:
            sx, sy = _center_shift(ref, pc.rotation)
            positions[ref] = (pc.x + sx, pc.y + sy)

    # Edge constraints (#34): nudge unlocked connectors to the nearest edge so a
    # motor/power/USB connector lands where you can plug into it.
    inferred = constraints.infer(project)
    reasons: dict[str, str] = dict(extra_reasons or {})
    edge_refs = set()
    for con in inferred:
        if con.reason and con.refdes not in reasons:
            reasons[con.refdes] = con.reason
        if con.kind == "edge":
            edge_refs.add(con.refdes)
    mpn_part = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    # Connectors whose mouth faces sideways seat flush with the outline.
    flush_refs = {
        ref for ref in edge_refs
        if getattr(mpn_part.get(ref), "matingFaceDir", None)
    }
    edge_rotations: dict[str, int] = {}
    edge_side: dict[str, str] = {}   # ref -> which edge it was parked against
    for ref in edge_refs:
        if ref in prev and prev[ref].locked:
            continue
        if ref not in positions or ref in anchors:
            continue  # anchored (grid slot / display) parts stay put
        x, y = positions[ref]
        fp = fp_map.get(ref)
        # Nearest edge by raw centre distance, and its OUTWARD normal direction.
        dists = {"left": x, "right": board_w - x, "top": y, "bottom": board_h - y}
        if region is not None:
            # Grid board: a connector may only go to a board edge the control
            # region actually touches — the grid's own edge (the LED/sensor
            # front) is off-limits, else an 88 mm battery holder gets parked
            # across the sensor row (seen live).
            rx0, ry0, rx1, ry1 = region
            eps = BOARD_MARGIN + 0.01
            allowed = {
                "left": rx0 <= eps, "right": rx1 >= board_w - eps,
                "top": ry0 <= eps, "bottom": ry1 >= board_h - eps,
            }
            dists = {k: v for k, v in dists.items() if allowed[k]} or dists
        nearest = min(dists, key=dists.get)
        edge_side[ref] = nearest

        # Rotate a connector so its mating face (plug opening) points off-board
        # out this edge — a USB/barrel/terminal then faces the wall you plug into
        # (Week-4 6.1). Needs the part's matingFaceDir hint; without it we leave
        # the part at 0° (no guess).
        rot = edge_facing_rotation(mpn_part.get(ref), x, y, board_w, board_h) or 0
        edge_rotations[ref] = rot

        # Inset by the body extent NORMAL to the edge, using the ROTATED footprint
        # (90/270° swaps width/height) so the body sits flush against the edge.
        # Using max(w,h) or the un-rotated extent (the old bugs) left a gap and
        # dropped parts out of the cutout-derivation proximity gate.
        fw, fh = (fp.width, fp.height) if fp else (2.0, 2.0)
        if rot in (90, 270):
            fw, fh = fh, fw
        # A connector with a MATING FACE (USB, barrel, audio, edge-launch RF)
        # is plugged into from off the board: its shell has to reach the
        # outline, not sit a board-margin inside it. The generic BOARD_MARGIN
        # inset left a USB-C 2.5 mm inboard, where no cable can seat — the
        # "receptacle stranded with no edge access" finding of the 2026-08-20
        # review. Top-entry connectors keep the margin: they plug from above.
        inset = 0.0 if getattr(mpn_part.get(ref), "matingFaceDir", None) else BOARD_MARGIN
        if nearest == "left":
            x = inset + fw / 2
        elif nearest == "right":
            x = board_w - (inset + fw / 2)
        elif nearest == "top":
            y = inset + fh / 2
        else:
            y = board_h - (inset + fh / 2)
        if shape == "round":
            # On a circle, "flush against the left edge" is only on the board
            # at the widest chord — pull the cross-axis coordinate to the
            # centreline so the connector sits at the rim, inside the disc.
            if nearest in ("left", "right"):
                y = board_h / 2
            else:
                x = board_w / 2
        positions[ref] = (x, y)

    # Companion passives follow their connector to the edge. The solve pulled a
    # USB-C's CC pull-downs next to the receptacle, then the edge snap MOVED the
    # receptacle and left them behind — a weather-station CC resistor ended up
    # in the opposite corner and its net could not be routed at all. Re-seat
    # each companion just inboard of its host's final spot; the legalizer below
    # then spreads them properly.
    for passive, host in _decoupling_pairs(project):
        if host not in edge_side or passive not in positions:
            continue
        if passive in edge_refs or (passive in prev and prev[passive].locked) or passive in anchors:
            continue  # it is an edge part in its own right
        hx, hy = positions[host]
        hw, hh = _half_wh_pre(host, fp_map, edge_rotations, prev)
        side = edge_side[host]
        step = 3.0
        if side == "left":
            positions[passive] = (min(board_w - BOARD_MARGIN, hx + hw + step), hy)
        elif side == "right":
            positions[passive] = (max(BOARD_MARGIN, hx - hw - step), hy)
        elif side == "top":
            positions[passive] = (hx, min(board_h - BOARD_MARGIN, hy + hh + step))
        else:
            positions[passive] = (hx, max(BOARD_MARGIN, hy - hh - step))

    # Rotations known so far: locked parts keep theirs, edge parts got a mating
    # rotation, everything else is 0. The legalizer needs these to size rotated
    # bodies correctly.
    rotations: dict[str, int] = {}
    for ref in positions:
        if ref in prev and prev[ref].locked:
            rotations[ref] = prev[ref].rotation
        else:
            rotations[ref] = edge_rotations.get(ref, 0)

    def _half_wh(ref: str) -> tuple[float, float]:
        fp = fp_map.get(ref)
        if fp is None:
            return 1.0, 1.0
        hw, hh = fp.width / 2, fp.height / 2
        return (hh, hw) if rotations.get(ref, 0) % 180 else (hw, hh)

    # Row-pack each edge: parts assigned to the same edge (and the HAT header
    # locked across it) share one strip, so several screw terminals pile onto
    # the same spot. Lay them out side-by-side ALONG the edge in solver order,
    # jumping over the intervals locked bodies occupy — a 1D sweep that keeps
    # every connector pluggable at the rim instead of shoving them inward (which
    # is what the general legalizer would do, burying pads mid-board). Parts
    # with room keep their preferred position; only crowded ones get packed.
    locked_pre = {ref for ref, pc in prev.items() if pc.locked} | set(anchors)
    for side in ("left", "right", "top", "bottom"):
        along_y = side in ("left", "right")  # L/R edges run vertically
        members = [
            r for r in edge_refs
            if edge_side.get(r) == side and r not in locked_pre and r in positions
        ]
        if not members:
            continue

        def along_half(r: str) -> float:
            hw, hh = _half_wh(r)
            return hh if along_y else hw  # noqa: B023 (along_y fixed per side)

        def along_coord(r: str) -> float:
            return positions[r][1] if along_y else positions[r][0]  # noqa: B023

        # Intervals along the edge blocked by locked bodies near this strip.
        # Padded by EDGE_OBSTACLE_MARGIN so an edge part clears the locked
        # header's dense pad field (not just its body) at the corner. Only
        # locked bodies whose extent actually reaches this edge's strip band
        # count — a pinned LED grid on the far side of the board must not
        # block the connectors' edge (it did, once anchors could be numerous).
        pad = OVERLAP_CLEARANCE + EDGE_OBSTACLE_MARGIN
        max_perp = max(
            ((_half_wh(r)[0] if along_y else _half_wh(r)[1]) for r in members), default=2.0
        )
        band = BOARD_MARGIN + 2 * max_perp + pad
        perp_len = board_w if along_y else board_h
        obstacles: list[tuple[float, float]] = []
        for lref in locked_pre:
            if lref not in positions:
                continue
            lx, ly = positions[lref]
            lhw, lhh = _half_wh(lref)
            c = ly if along_y else lx
            h = lhh if along_y else lhw
            perp_c = lx if along_y else ly
            perp_h = lhw if along_y else lhh
            near_edge = (
                perp_c - perp_h <= band if side in ("left", "top")
                else perp_c + perp_h >= perp_len - band
            )
            if not near_edge:
                continue
            obstacles.append((c - h - pad, c + h + pad))

        span_lo = BOARD_MARGIN
        span_hi = (board_h if along_y else board_w) - BOARD_MARGIN
        if shape == "round":
            # The packable strip on a circle is the chord at the rim, not the
            # full bounding-box edge — packing past it parks a connector in the
            # empty corner air. Conservative: chord where the parts' outer
            # face sits (BOARD_MARGIN in from the bounding box).
            r_b = board_w / 2
            d_c = max(0.0, r_b - BOARD_MARGIN)
            half_chord = math.sqrt(max(0.0, r_b * r_b - d_c * d_c))
            span_lo = max(span_lo, r_b - half_chord)
            span_hi = min(span_hi, r_b + half_chord)
        # Free segments = the strip minus the locked-body intervals. Packing
        # into these places a tall terminal in the room BELOW the header rather
        # than bumping it off the board end (which stranded it back on J1).
        obstacles.sort()
        free: list[tuple[float, float]] = []
        cur = span_lo
        for lo, hi in obstacles:
            if lo > cur:
                free.append((cur, min(lo, span_hi)))
            cur = max(cur, hi)
            if cur >= span_hi:
                break
        if cur < span_hi:
            free.append((cur, span_hi))

        si = 0
        fill = free[0][0] if free else span_lo
        for r in sorted(members, key=along_coord):
            ah = along_half(r)
            # Advance to a free segment that still has room from `fill`.
            while si < len(free) and fill + 2 * ah > free[si][1]:
                si += 1
                if si < len(free):
                    fill = free[si][0]
            if si >= len(free):
                continue  # every segment full — leave this one to the legalizer
            seg_lo, seg_hi = free[si]
            lo_bound = max(fill, seg_lo)
            # Honour the solver's preferred position, clamped into the segment.
            start = min(max(along_coord(r) - ah, lo_bound), seg_hi - 2 * ah)
            c = start + ah
            ex, ey = positions[r]
            positions[r] = (ex, c) if along_y else (c, ey)
            fill = start + 2 * ah + OVERLAP_CLEARANCE

    # Safety net: keep every movable body inside the outline. The force-directed
    # path clamps as it solves, but the <10-part even-spacing fallback ignores
    # part size — a 46 mm Arduino Nano on a small board hung off the edge
    # (PCB_R02, seen live on the buzzer board). Cheap and idempotent.
    locked_refs = {ref for ref, pc in prev.items() if pc.locked} | set(anchors)
    for ref, (px, py) in list(positions.items()):
        if ref in locked_refs:
            continue
        hw, hh = _half_wh(ref)
        # A flush-seated connector keeps its shell on the outline; everything
        # else keeps the board margin.
        _m = FLUSH_EDGE_MARGIN if ref in flush_refs else BOARD_MARGIN
        lo_x, hi_x = _m + hw, board_w - _m - hw
        lo_y, hi_y = _m + hh, board_h - _m - hh
        px = min(max(px, lo_x), hi_x) if lo_x <= hi_x else board_w / 2
        py = min(max(py, lo_y), hi_y) if lo_y <= hi_y else board_h / 2
        if _RING is not None:
            px, py = board_shape.clamp(
                _RING, px, py, board_shape.half_diagonal(hw, hh) + BOARD_MARGIN
            )
        elif shape == "round":
            # Circle outline: pull the body (by its half-diagonal — the
            # corners leave the disc first) radially inside the milled edge.
            r_lim = board_w / 2 - BOARD_MARGIN - math.hypot(hw, hh)
            rx, ry = px - board_w / 2, py - board_h / 2
            d = math.hypot(rx, ry)
            if d > r_lim > 0:
                px = board_w / 2 + rx / d * r_lim
                py = board_h / 2 + ry / d * r_lim
        positions[ref] = (px, py)

    # Final legalization: separate any overlapping bodies so the layout is
    # DRC-clean and the router can escape-route every part's pads. Locked and
    # anchored parts (e.g. a smartwatch display) stay fixed.
    _legalize_overlaps(
        positions, fp_map, board_w, board_h, locked_refs, shape, rotations,
        edge_refs=edge_refs, flush_refs=flush_refs,
    )

    placed: list[PlacedComponent] = []
    for c in components:
        x, y = positions.get(c.refdes, (board_w / 2, board_h / 2))
        old = prev.get(c.refdes)
        rotation = old.rotation if old and old.locked else edge_rotations.get(c.refdes, 0)
        if old and old.locked:
            # Bit-exact restore — the body-centre round-trip would add float
            # noise to coordinates the user (or a form-factor spec) pinned.
            x, y = old.x, old.y
        else:
            # Body-centre space -> anchor (see docstring).
            sx, sy = _center_shift(c.refdes, rotation)
            x, y = x - sx, y - sy
        placed.append(
            PlacedComponent(
                refdes=c.refdes,
                mpn=c.mpn,
                x=x,
                y=y,
                rotation=rotation,
                layer=old.layer if old and old.locked else "top",
                footprint=fp_map[c.refdes],
                locked=bool(old and old.locked),
                placementReason=(
                    old.placementReason if old and old.locked else reasons.get(c.refdes)
                ),
            )
        )

    # Rats-nest lines for the auto-router to chew on.
    rats: list[RatsLine] = []
    pad_positions: dict[tuple[str, str], tuple[float, float]] = {}
    for pc in placed:
        for pad in pc.footprint.pads:
            pad_positions[(pc.refdes, pad.pin_number)] = geometry.pad_world(pc, pad)

    for net in project.ir.nets:
        if len(net.pins) < 2:
            continue
        first = pad_positions.get(tuple(net.pins[0]))
        if not first:
            continue
        for other_pin in net.pins[1:]:
            other = pad_positions.get(tuple(other_pin))
            if not other:
                continue
            rats.append(
                RatsLine(
                    net=net.name,
                    x1=first[0],
                    y1=first[1],
                    x2=other[0],
                    y2=other[1],
                )
            )

    layer_count, planes = choose_stackup(project)
    return Board(
        width=board_w,
        height=board_h,
        shape=shape,  # type: ignore[arg-type]
        outline_points=[Point(x=round(x, 3), y=round(y, 3)) for x, y in (ring or [])],
        layer_count=layer_count,
        planes=planes,
        components=placed,
        traces=[],
        vias=[],
        rats=rats,
        issues=[],
        auto_placed=True,
        auto_routed=False,
        has_ground_pour=True,
    )


# 4-layer decision. Requested explicitly (set_form_factor layers=4) or, when
# nothing was said, chosen when the board is dense enough that a GND + power
# plane is what a professional would lay down anyway. Cost is not the
# constraint (JLC 4-layer is a few dollars more per lot); routability and a
# solid return path are.
FOUR_LAYER_PARTS = 30
# A dense THROUGH-HOLE host header forces 4 layers regardless of part count.
# Review round 2026-08-20: a Raspberry Pi HAT (40 pins in two 2.54 mm rows,
# ~20 parts) left 12 nets unrouted after 55 FreeRouting passes. Every
# peripheral net has to cross one of those rows, each THT pin blocks BOTH
# layers, and the bottom side is a GND pour — so the escapes all compete for
# top-layer corridors. A real HAT/shield is a 4-layer board for this exact
# reason: the inner planes carry the rails and free the outer layers for
# signals.
FOUR_LAYER_THT_HEADER_PINS = 24


# A trivial design stays 2-layer even behind a big header — 4 layers is a real
# cost and a 5-part board has corridors to spare.
FOUR_LAYER_MIN_PARTS = 8


def _dense_tht_header(project: Project) -> str | None:
    """Refdes of a dense through-hole HOST CONNECTOR: a Pi HAT bank, an Arduino
    shield header, a 2x20 stack connector — a pin field every peripheral net
    has to cross, on both layers.

    Deliberately connector-category only: an Arduino Nano MODULE also has 30
    THT pins, but it is a part the board carries, not a wall across it — and
    treating it as one turned every 5-part Nano board into a 4-layer board."""
    if len(project.ir.components) < FOUR_LAYER_MIN_PARTS:
        return None
    connectors = {
        c.refdes
        for c in project.ir.components
        if (part := library.get_part(c.mpn)) is not None
        and (part.category or "").lower() == "connector"
    }
    board = project.board
    comps = board.components if board is not None and board.components else []
    for pc in comps:
        if pc.refdes not in connectors or pc.footprint is None:
            continue
        tht = sum(1 for pad in pc.footprint.pads if pad.pad_type == "tht")
        if tht >= FOUR_LAYER_THT_HEADER_PINS:
            return pc.refdes
    if comps:
        return None
    # Before placement: fall back to the library pin count.
    for c in project.ir.components:
        if c.refdes in connectors:
            part = library.get_part(c.mpn)
            if part is not None and len(part.pins) >= FOUR_LAYER_THT_HEADER_PINS:
                return c.refdes
    return None


def _plane_candidates(project: Project) -> list[str]:
    """Nets that make sense as planes, best first: GND, then the logic supply
    rail with the most consumers (VCC_3V3 / VCC_5V / VDD ...). Motor/battery
    rails are deliberately NOT planed — they carry switching current and belong
    as wide traces near their loads, and a plane at 16.8 V under signals is
    the wrong stack-up."""
    from app.services import verifier as _v  # local: verifier imports placement

    gnd = next((n.name for n in project.ir.nets if _v._is_gnd_net(n.name)), None)
    out: list[str] = [gnd] if gnd else []
    logic: list[tuple[int, str]] = []
    for n in project.ir.nets:
        up = n.name.upper()
        if _v._is_gnd_net(n.name) or _v._classify_net(n.name) != "power":
            continue
        if up.startswith(("VBAT", "VM", "VMOT", "VIN", "VSYS", "VRAW", "VPWR")):
            continue
        logic.append((len(n.pins), n.name))
    logic.sort(reverse=True)
    if logic:
        out.append(logic[0][1])
    return out


def choose_stackup(project: Project) -> tuple[int, list]:
    from app.models import Plane

    spec = getattr(project.ir, "form_factor", None)
    want = spec.layers if spec is not None and spec.layers else None
    if want == 2:
        return 2, []
    dense_header = _dense_tht_header(project)
    if (
        want is None
        and len(project.ir.components) < FOUR_LAYER_PARTS
        and dense_header is None
    ):
        return 2, []
    cands = _plane_candidates(project)
    if not cands:
        return 2, []
    planes = [Plane(layer="In1", net=cands[0])]
    if len(cands) > 1:
        planes.append(Plane(layer="In2", net=cands[1]))
    return 4, planes
