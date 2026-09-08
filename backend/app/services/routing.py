"""Auto-router: A* on a 0.4mm grid, 2 layers (top + bottom), Manhattan only.

Algorithm:
  1. Build a grid of cells covering the board area.
  2. Mark obstacles: component bodies (their layer), other-net pads (their
     layer for SMD, both layers for through-hole), already-routed traces.
  3. For each net, build a Steiner-like spanning tree by routing each pad to
     its nearest already-connected pad in the same net. A* with via penalty
     means the router prefers staying on one layer but will switch when needed.
  4. Convert the cell paths back into (segment, via) tuples for rendering.

Limitations vs. a real router (Freerouting et al):
  - No rip-up-and-retry: if a net can't be routed because earlier nets blocked
    it, we just emit a rats-nest line for the failed segment.
  - No rectilinear refinement (paths are 4-connected so they look stair-stepped
    until we post-process; we collapse co-linear runs at the end).
  - No diff-pair / impedance-controlled routing.
  - 2-layer only (PRD §7.5 says 2-layer at launch — fine).
"""

from __future__ import annotations

import heapq
import math
import os
import time
from dataclasses import dataclass

from app.models import (
    Board,
    PlacedComponent,
    Project,
    RatsLine,
    Trace,
    TraceSegment,
    Via,
)
from app.services import board_shape, geometry, placement

GRID_PITCH = 0.4              # mm
TRACE_WIDTH_DEFAULT = 0.15    # 6 mil — fine for low-current signals
TRACE_WIDTH_POWER = 0.3       # 12 mil — power rails
VIA_DRILL = 0.3
VIA_DIAMETER = 0.55
VIA_COST = 4.0                # discourages unnecessary layer switches
PAD_KEEPOUT_CELLS = 1         # extra cells around component bodies

# --- Performance knobs ---
# Maximum cells A* will expand per route attempt. Beyond this we give up on
# this segment and emit a rats-nest line. Stops pathological cases where the
# goal is unreachable and A* would otherwise explore the whole board.
ASTAR_EXPANSION_CAP = 55_000
# Slack (in cells) around the start↔goal bounding box. Restricts A* to a
# region rather than exploring the entire grid. 64 cells × 0.4mm = ~26mm of
# detour room — enough to weave a congested net the long way around obstacles
# instead of giving up when the direct corridor is blocked.
SEARCH_BBOX_SLACK = 52
# Connected pads to try as A* start points per new pin of a multi-pin net
# (nearest first). See the note at the call site.
NEAREST_STARTS = 3


# ---- Grid -----------------------------------------------------------------


# Cells are plain (layer, row, col) tuples. Dataclass had ~30% of routing
# wall-time in __hash__/__init__/__eq__ — tuples push all that to C-level.
Cell = tuple[int, int, int]


def _cell(layer: int, row: int, col: int) -> Cell:
    return (layer, row, col)


class Grid:
    def __init__(self, width_mm: float, height_mm: float, pitch: float = GRID_PITCH):
        self.pitch = pitch
        self.cols = int(math.ceil(width_mm / pitch)) + 1
        self.rows = int(math.ceil(height_mm / pitch)) + 1
        # owner[layer] is a flat list, indexed as row * cols + col.
        # Value = None (free), "" (blocked / hard obstacle), or net-name string.
        self._owners: list[list[str | None]] = [
            [None] * (self.rows * self.cols) for _ in range(2)
        ]
        # Parallel marker: 1 where a cell holds *committed copper* (a real pad,
        # via, or routed track) as opposed to a soft halo / edge / body-keepout
        # reservation. Lets the via-clearance guard reject a via next to another
        # net's actual copper without also rejecting it next to a soft
        # reservation (which would gut routability). See `_via_would_short`.
        self._copper: list[bytearray] = [
            bytearray(self.rows * self.cols) for _ in range(2)
        ]

    def mark_copper(self, layer: int, row: int, col: int) -> None:
        if self.in_bounds(row, col):
            self._copper[layer][self._idx(row, col)] = 1

    # Coordinate conversions
    def to_grid(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        return (
            max(0, min(self.rows - 1, int(round(y_mm / self.pitch)))),
            max(0, min(self.cols - 1, int(round(x_mm / self.pitch)))),
        )

    def to_mm(self, row: int, col: int) -> tuple[float, float]:
        return col * self.pitch, row * self.pitch

    def in_bounds(self, row: int, col: int) -> bool:
        return 0 <= row < self.rows and 0 <= col < self.cols

    def _idx(self, row: int, col: int) -> int:
        return row * self.cols + col

    def block(self, layer: int, row: int, col: int, owner: str = "") -> None:
        if not self.in_bounds(row, col):
            return
        cur = self._owners[layer][self._idx(row, col)]
        if cur is None or cur == "":
            self._owners[layer][self._idx(row, col)] = owner
        # If already owned by another net, keep it; that pad/cell stays as
        # the original owner (an SMD pad assigned to net A doesn't get
        # overwritten when net B's body keepout is applied).

    def hard_block(self, layer: int, row: int, col: int) -> None:
        if not self.in_bounds(row, col):
            return
        self._owners[layer][self._idx(row, col)] = ""

    def is_passable(self, layer: int, row: int, col: int, by_net: str) -> bool:
        if not self.in_bounds(row, col):
            return False
        owner = self._owners[layer][self._idx(row, col)]
        if owner is None:
            return True
        if owner == "":
            return False
        return owner == by_net


# ---- Helpers --------------------------------------------------------------


def _net_owner_for_pad(project: Project, refdes: str, pin_number: str) -> str | None:
    """Return the net name that owns this pad, if any."""
    for net in project.ir.nets:
        for r, p in net.pins:
            if r == refdes and p == pin_number:
                return net.name
    return None


# Clearance halo (in grid cells) reserved around every pad and via so a
# *different* net's copper can't be packed into the neighbouring cell and touch
# it. The router stamps copper cell-exact on a 0.4 mm grid; without this ring an
# adjacent-cell track (0.4 mm centre-to-centre) comes within a fab's min
# clearance of a pad/via — KiCad DRC reports it as a `shorting_items` /
# `copper_edge_clearance` violation, and a fabbed board would actually short.
# Pads/vias are sparse, so a 1-cell ring costs little routability; plain
# track-to-track clearance is left to the router-tuning work (see
# docs/ROUTING_KICAD_COMPARISON.md).
CLEARANCE_HALO = 1


def _stamp_halo(grid: Grid, layers: tuple[int, ...], row: int, col: int, owner: str) -> None:
    """Reserve the clearance ring around (row, col) on `layers`. A net-name
    `owner` blocks *other* nets from the ring while staying passable for the
    owner (so it can still approach its own pad); a hard "" owner blocks
    everyone (unconnected pad / board edge). NON-destructive: only fills cells
    that are still free (None), so it never clobbers a real pad centre or another
    net's committed track."""
    for dr in range(-CLEARANCE_HALO, CLEARANCE_HALO + 1):
        for dc in range(-CLEARANCE_HALO, CLEARANCE_HALO + 1):
            if dr == 0 and dc == 0:
                continue
            r, c = row + dr, col + dc
            if not grid.in_bounds(r, c):
                continue
            idx = grid._idx(r, c)
            for layer in layers:
                if grid._owners[layer][idx] is None:
                    grid._owners[layer][idx] = owner


def _via_would_short(
    copper: list[bytearray],
    owners: list[list[str | None]],
    rows: int,
    cols: int,
    row: int,
    col: int,
    by_net: str,
) -> bool:
    """True if dropping a via at (row, col) would place its plated-hole copper
    within one cell of ANOTHER net's committed copper (a track/pad/via) on
    either layer. Soft halo/edge reservations are ignored (they carry no copper),
    so this only blocks the genuine short — a via abutting an already-routed
    track — without the routability collapse of rejecting every reservation."""
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc = row + dr, col + dc
        if 0 <= rr < rows and 0 <= cc < cols:
            base = rr * cols + cc
            for layer in (0, 1):
                if copper[layer][base] and owners[layer][base] not in (None, by_net):
                    return True
    return False


# ---- Pad escape routing ---------------------------------------------------
#
# Dense IC pads sit inside the chip body where there's no room for traces.
# Real autorouters do "fanout" — a short stub trace from each pad to an
# escape point just outside the body, where the main router can take over in
# less-congested space. Without this, the router has to enter pads directly,
# crossing other pads to reach them, and bounded A* gives up.

ESCAPE_TRACE_WIDTH = 0.15           # mm — fanout uses signal trace width
ESCAPE_BODY_CLEARANCE = 1.0         # mm past the body edge for the escape point
DENSE_IC_THRESHOLD = 4              # ≥this many SMD pads → fan out
# Depth added to every OTHER escape in a fine-pitch row (see the stagger note
# in _compute_pad_escapes). 3 grid cells: enough that adjacent vias' 1-cell
# clearance halos can never share a row and seal each other in.
ESCAPE_STAGGER = 3 * GRID_PITCH


def _escape_cell(x: float, y: float, layer: int) -> tuple[int, int, int]:
    """Quantize an escape point to the routing-grid cell it will occupy.
    Mirrors Grid.to_grid (row = round(y/pitch), col = round(x/pitch))."""
    return (layer, int(round(y / GRID_PITCH)), int(round(x / GRID_PITCH)))


def _free_escape_cell(
    ex: float,
    ey: float,
    layer: int,
    horizontal: bool,
    net: str,
    claimed: dict[tuple[int, int, int], str],
) -> tuple[float, float]:
    """Return an (ex, ey) whose grid cell is not claimed by a *different* net,
    sliding along the body-edge tangent in grid-pitch steps. The tangent is the
    y axis for a horizontal escape (off a vertical edge) and the x axis for a
    vertical one. Records the chosen cell's owning net before returning.

    Without this, two adjacent pads of a dense IC on *different* nets can
    project to escape points that round to the same cell; whichever is built
    last claims grid ownership, leaving the earlier net's escape entry owned by
    a sibling net — unroutable, so the net drops straight to a rats-nest line.
    Same-net collisions are left alone: a shared cell owned by that one net is
    harmless (and separating them would only push an escape to a worse spot)."""
    cell = _escape_cell(ex, ey, layer)
    if claimed.get(cell, net) != net:
        for step in range(1, 17):
            found = False
            for sgn in (1.0, -1.0):
                if horizontal:
                    cand_y = ey + sgn * step * GRID_PITCH
                    cand = _escape_cell(ex, cand_y, layer)
                    if claimed.get(cand, net) == net:
                        ey, cell, found = cand_y, cand, True
                else:
                    cand_x = ex + sgn * step * GRID_PITCH
                    cand = _escape_cell(cand_x, ey, layer)
                    if claimed.get(cand, net) == net:
                        ex, cell, found = cand_x, cand, True
                if found:
                    break
            if found:
                break
    claimed[cell] = net
    return ex, ey


def _compute_pad_escapes(
    project: Project, board: Board, only_nets: set[str] | None = None,
    plane_nets: set[str] | None = None,
) -> tuple[
    dict[tuple[str, str], tuple[float, float, int]],   # (refdes, pin) -> (x, y, layer) on OPPOSITE layer
    list[Trace],                                       # short stubs from pad to escape via, on pad layer
    list[Via],                                         # escape vias dropping to opposite layer
]:
    """Real escape routing: every fanned-out pad gets a stub on its own
    layer + a via at the stub end + the main-router entry point on the
    OPPOSITE layer. With a GND pour underneath, the bottom layer is mostly
    free and signals can route across the board without weaving between
    top-layer pads.
    """
    escapes: dict[tuple[str, str], tuple[float, float, int]] = {}
    stubs: list[Trace] = []
    vias: list[Via] = []
    pour_on = bool(getattr(board, "has_ground_pour", True))
    # Maps each claimed escape cell to its owning net, so different-net
    # collisions get separated while same-net ones share (see _free_escape_cell).
    claimed_escape_cells: dict[tuple[int, int, int], str] = {}

    plane_nets = plane_nets or set()
    for pc in board.components:
        smd_count = sum(1 for p in pc.footprint.pads if p.pad_type == "smd")
        # On a 4-layer board every SMD pad of a PLANE net gets its own via down
        # to the plane — that is how the net is "routed" — dense part or not.
        # (A THT pad is a plated hole and reaches the plane by itself.)
        part_has_plane_pad = bool(plane_nets) and any(
            p.pad_type == "smd" and (_net_owner_for_pad(project, pc.refdes, p.pin_number) in plane_nets)
            for p in pc.footprint.pads
        )
        if smd_count < DENSE_IC_THRESHOLD and not part_has_plane_pad:
            continue
        dense_part = smd_count >= DENSE_IC_THRESHOLD

        comp_layer = 0 if pc.layer == "top" else 1
        opp_layer = 1 - comp_layer
        body_w = pc.footprint.width
        body_h = pc.footprint.height

        rbody_w, rbody_h = geometry.body_dims(pc)
        cands: list[tuple] = []  # (pad, net, horizontal, sign, along, dx, dy)
        for pad in pc.footprint.pads:
            if pad.pad_type != "smd":
                continue
            net = _net_owner_for_pad(project, pc.refdes, pad.pin_number)
            if net is None:
                continue
            if only_nets is not None and net not in only_nets and net not in plane_nets:
                continue  # partial reroute — only fan out pads we're rerouting
            if net in plane_nets:
                # Plane pad: gets its via down to the plane. GND is the one
                # exception — it already rides the pour with no vias in the
                # preview (FreeRouting/KiCad add the real thermal vias), and
                # measured: a via + halo per GND pad chokes the corridors
                # around every part (watch 2 -> 5 rats). Power plane only.
                if pour_on and net.upper() in {"GND", "VSS", "AGND", "DGND"}:
                    continue
            elif not dense_part:
                continue  # a sparse part only fans out its plane pads
            elif pour_on and net.upper() in {"GND", "VSS", "AGND", "DGND"}:
                continue

            # Rotate the pad offset + body dims into the board frame so a rotated
            # connector escapes toward the correct side (identity at rotation 0).
            dx, dy = geometry.rotate_xy(pad.x, pad.y, pc.rotation)
            # Escape toward the NEAREST body edge. Choosing by |dx| >= |dy|
            # sent every pad in the end-thirds of a long module (Pico: 2x20
            # castellations) out through the module's END, where 10+ escapes
            # per end walled each other in (a GP2 rat on an 8-LED board).
            to_side = rbody_w / 2 - abs(dx)
            to_end = rbody_h / 2 - abs(dy)
            horizontal = abs(dx) > 0.01 and (to_side <= to_end or abs(dy) <= 0.01)
            along = (pc.y + dy) if horizontal else (pc.x + dx)
            sign = (1.0 if dx > 0 else -1.0) if horizontal else (1.0 if dy > 0 else -1.0)
            cands.append((pad, net, horizontal, sign, along, dx, dy))

        # Fine-pitch rows (an FPC-style display header: 0.5 mm pitch on a
        # 0.4 mm grid) put adjacent escape vias so close that their clearance
        # halos overlap and SEAL the middle vias — the net's own entry point
        # becomes unreachable on an EMPTY grid (the smartwatch board's
        # SPI_MOSI/CS rats). Stagger such a row's escape depths (near/far by
        # index parity) so no two adjacent vias share a halo row.
        extra_depth = [0.0] * len(cands)
        groups: dict[tuple[bool, float], list[int]] = {}
        for i, cd in enumerate(cands):
            groups.setdefault((cd[2], cd[3]), []).append(i)
        for idxs in groups.values():
            ordered = sorted(idxs, key=lambda i: cands[i][4])
            spacings = [
                cands[b][4] - cands[a][4] for a, b in zip(ordered, ordered[1:])
            ]
            if spacings and min(spacings) < ESCAPE_STAGGER:
                for k, i in enumerate(ordered):
                    extra_depth[i] = (k % 2) * ESCAPE_STAGGER

        # A round board's routable area is the disc: an escape via outside it
        # lands in a hard-blocked cell and STRANDS the net's entry point (the
        # rim USB-C's USB_DP/DN rats). Flip such an escape out the opposite
        # body edge; if even that is off-board, skip the fanout so the router
        # enters at the pad itself.
        round_board = getattr(board, "shape", "rect") == "round"
        r_escape = board.width / 2.0 - 0.3 - VIA_DIAMETER / 2.0
        b_ctr = board.width / 2.0

        def _in_disc(x: float, y: float) -> bool:
            return (
                not round_board
                or (x - b_ctr) ** 2 + (y - b_ctr) ** 2 <= r_escape * r_escape
            )

        for i, (pad, net, horizontal, sign, along, dx, dy) in enumerate(cands):
            clearance = ESCAPE_BODY_CLEARANCE + extra_depth[i]
            if horizontal:
                ex = pc.x + sign * (rbody_w / 2 + clearance)
                ey = pc.y + dy
            else:
                ex = pc.x + dx
                ey = pc.y + sign * (rbody_h / 2 + clearance)
            if not _in_disc(ex, ey):
                if horizontal:
                    ex = pc.x - sign * (rbody_w / 2 + clearance)
                else:
                    ey = pc.y - sign * (rbody_h / 2 + clearance)
                if not _in_disc(ex, ey):
                    continue  # no legal escape — the router enters at the pad

            # Keep each pad's escape on its own grid cell — a shared cell would
            # be stolen by whichever sibling net is built last, stranding this
            # one with an unroutable entry point.
            ex, ey = _free_escape_cell(ex, ey, opp_layer, horizontal, net, claimed_escape_cells)
            if not _in_disc(ex, ey):
                continue  # the collision walk pushed it off-board — enter at the pad

            # Router enters on the OPPOSITE layer — the main A* runs on the
            # less-congested side of the board.
            escapes[(pc.refdes, pad.pin_number)] = (ex, ey, opp_layer)

            # Visible stub on the pad's own layer (top for top-mounted parts).
            pad_x, pad_y = geometry.pad_world(pc, pad)
            stubs.append(
                Trace(
                    net=net,
                    layer="top" if comp_layer == 0 else "bottom",
                    width=ESCAPE_TRACE_WIDTH,
                    segments=[TraceSegment(x1=pad_x, y1=pad_y, x2=ex, y2=ey)],
                )
            )

            # Via at the escape point — physically connects the two layers.
            vias.append(
                Via(
                    x=ex,
                    y=ey,
                    drill=VIA_DRILL,
                    diameter=VIA_DIAMETER,
                    net=net,
                )
            )

    return escapes, stubs, vias


def _mark_stub_in_grid(grid: Grid, stub: Trace, owner: str) -> None:
    """Walk a single-segment stub trace and stamp the grid cells along it as
    owned by `owner`. Used so other nets' A* can't ride over a fanout stub."""
    if not stub.segments:
        return
    seg = stub.segments[0]
    layer = 0 if stub.layer == "top" else 1
    r1, c1 = grid.to_grid(seg.x1, seg.y1)
    r2, c2 = grid.to_grid(seg.x2, seg.y2)
    # Bresenham-ish: walk grid cells from (r1,c1) to (r2,c2).
    steps = max(abs(r2 - r1), abs(c2 - c1)) + 1
    for k in range(steps + 1):
        t = k / max(steps, 1)
        r = int(round(r1 + (r2 - r1) * t))
        c = int(round(c1 + (c2 - c1) * t))
        if grid.in_bounds(r, c):
            grid._owners[layer][r * grid.cols + c] = owner
            grid._copper[layer][r * grid.cols + c] = 1


def _stamp_trace_in_grid(grid: Grid, trace: Trace) -> None:
    """Mark every cell a (multi-segment) trace covers as owned by its net, on
    the trace's layer. Used in partial reroute so kept traces become obstacles
    the rerouted nets must avoid (or via around)."""
    layer = 0 if trace.layer == "top" else 1
    for seg in trace.segments:
        r1, c1 = grid.to_grid(seg.x1, seg.y1)
        r2, c2 = grid.to_grid(seg.x2, seg.y2)
        steps = max(abs(r2 - r1), abs(c2 - c1))
        for k in range(steps + 1):
            t = k / max(steps, 1)
            r = int(round(r1 + (r2 - r1) * t))
            c = int(round(c1 + (c2 - c1) * t))
            if grid.in_bounds(r, c):
                grid._owners[layer][r * grid.cols + c] = trace.net
                grid._copper[layer][r * grid.cols + c] = 1


def _stamp_via_in_grid(grid: Grid, via: Via) -> None:
    """A kept via occupies its cell on both layers (it's a plated hole), plus a
    clearance ring so a rerouted net can't pack a track against it."""
    r, c = grid.to_grid(via.x, via.y)
    if grid.in_bounds(r, c):
        for layer in (0, 1):
            grid._owners[layer][r * grid.cols + c] = via.net
            grid._copper[layer][r * grid.cols + c] = 1
        _stamp_halo(grid, (0, 1), r, c, via.net)


def _component_body_cells(grid: Grid, pc: PlacedComponent) -> list[tuple[int, int]]:
    """Return the grid cells covered by a component's body (with a small keepout)."""
    bw, bh = geometry.body_dims(pc)  # swapped for 90/270° rotation
    half_w = bw / 2
    half_h = bh / 2
    margin = 0.15  # mm
    x_min = pc.x - half_w + margin
    x_max = pc.x + half_w - margin
    y_min = pc.y - half_h + margin
    y_max = pc.y + half_h - margin

    r0, c0 = grid.to_grid(x_min, y_min)
    r1, c1 = grid.to_grid(x_max, y_max)
    cells: list[tuple[int, int]] = []
    for r in range(min(r0, r1), max(r0, r1) + 1):
        for c in range(min(c0, c1), max(c0, c1) + 1):
            cells.append((r, c))
    return cells


def _build_grid(
    project: Project,
    board: Board,
    escapes: dict[tuple[str, str], tuple[float, float, int]] | None = None,
    escape_stubs: list[Trace] | None = None,
) -> tuple[Grid, dict[tuple[str, str], Cell], dict[tuple[str, str], Cell]]:
    """Set up the routing grid with component-body keepouts and pad ownership.

    Returns the grid plus a mapping from (refdes, pin_number) -> Cell so the
    router knows where each pad's entry point is. When `escapes` is provided,
    the entry point for each fanned-out pad is the escape via cell, NOT the
    pad cell — that's how dense ICs become routable.
    """
    grid = Grid(board.width, board.height)

    # Round board: the routable area is the disc, not its bounding square —
    # hard-block every cell outside the milled edge (minus copper-to-edge
    # clearance) on both layers, or the router happily runs traces through
    # the empty corners of the bounding box.
    if getattr(board, "shape", "rect") == "round":
        r_lim = board.width / 2.0 - 0.3
        cxy = board.width / 2.0
        for rr in range(grid.rows):
            dy = rr * grid.pitch - cxy
            for cc in range(grid.cols):
                dx = cc * grid.pitch - cxy
                if dx * dx + dy * dy > r_lim * r_lim:
                    grid.hard_block(0, rr, cc)
                    grid.hard_block(1, rr, cc)
    else:
        # Polygon board: same rule, arbitrary outline. Without this the router
        # runs traces through the air outside a triangle or between the arms of
        # an X and the board comes back from the mill with the net cut off.
        ring = board_shape.ring_for_board(board)
        if ring is not None:
            for rr in range(grid.rows):
                y = rr * grid.pitch
                for cc in range(grid.cols):
                    if board_shape.clearance(ring, cc * grid.pitch, y) < 0.3:
                        grid.hard_block(0, rr, cc)
                        grid.hard_block(1, rr, cc)

    # Mechanical KEEP-OUTS are no-copper regions the design itself declared (an
    # antenna clearance, a mounting boss, a moving part). The router used to
    # ignore them completely, so the exported board came back from KiCad's own
    # DRC with "tracks in a keepout area" — 3 errors on the 2026-08-20 review's
    # board, from a keepout Synqit had placed itself. Blocking them here is the
    # only way the two agree.
    mech = getattr(project, "mechanical", None)
    for zone in (getattr(mech, "keepouts", None) or []):
        layers = (0, 1) if zone.layer == "both" else ((0,) if zone.layer == "top" else (1,))
        r0, c0 = grid.to_grid(zone.x, zone.y)
        r1, c1 = grid.to_grid(zone.x + zone.width, zone.y + zone.height)
        for rr in range(min(r0, r1), max(r0, r1) + 1):
            for cc in range(min(c0, c1), max(c0, c1) + 1):
                for layer_idx in layers:
                    grid.hard_block(layer_idx, rr, cc)

    pad_cells: dict[tuple[str, str], Cell] = {}
    # For fanned-out pads: the RAW pad cell on its own layer, as a second entry
    # point. Forcing every dense-part pad through its escape via crowds the
    # bottom layer around the part and turns "no bottom corridor" into a rat
    # even when the pad has clear top-layer air right beside it (the sweep's
    # dense boards: 2-6 rats each). The router tries the via first, the pad
    # second (see _route_one_attempt).
    alt_cells: dict[tuple[str, str], Cell] = {}

    # Pass 1 — body keepouts. `footprint.width/height` is the whole footprint
    # extent (courtyard INCLUDING the pads), so this seals every pad of a
    # 2-/3-pad SMD part inside a hard block on its own layer. Remember which
    # cells are body-only so pass 2 can carve an approach ring around each
    # owned pad — without it a 0603's pads were reachable ONLY by dropping a
    # via straight into the pad from the other layer, and two nets on
    # neighbouring pads then fought over the same handful of bottom-layer
    # cells (VCC + LEDn on an LED row: 7 rats on a 23-part board).
    body_only: list[bytearray] = [bytearray(grid.rows * grid.cols) for _ in range(2)]
    for pc in board.components:
        comp_layer = 0 if pc.layer == "top" else 1
        for r, c in _component_body_cells(grid, pc):
            grid.hard_block(comp_layer, r, c)
            body_only[comp_layer][grid._idx(r, c)] = 1

    # Pass 2 — pads. Each pad belongs to a specific net (or no net for floating).
    for pc in board.components:
        comp_layer = 0 if pc.layer == "top" else 1
        for pad in pc.footprint.pads:
            world_x, world_y = geometry.pad_world(pc, pad)
            row, col = grid.to_grid(world_x, world_y)
            owner = _net_owner_for_pad(project, pc.refdes, pad.pin_number)

            # If this pad has been fanned out, the router enters at the
            # escape via instead of the pad itself.
            escape = escapes.get((pc.refdes, pad.pin_number)) if escapes else None
            if escape:
                ex, ey, elayer = escape
                erow, ecol = grid.to_grid(ex, ey)
                pad_cells[(pc.refdes, pad.pin_number)] = (elayer, erow, ecol)
                alt_cells[(pc.refdes, pad.pin_number)] = (comp_layer, row, col)
            else:
                pad_cells[(pc.refdes, pad.pin_number)] = (comp_layer, row, col)

            # Layers this pad's copper occupies: both for a plated through-hole,
            # else just its mount layer — used for the center block and its halo.
            pad_layers = (0, 1) if pad.pad_type == "tht" else (comp_layer,)

            if owner:
                # Pads of net A are passable for net A, blocked for everyone else.
                for layer_idx in pad_layers:
                    grid._owners[layer_idx][grid._idx(row, col)] = owner
                    grid.mark_copper(layer_idx, row, col)
                # Carve the pad's approach ring out of its OWN body keepout so
                # the net can reach the pad in-plane. Only body-only cells flip
                # (never another pad's copper or halo, never the board edge),
                # and only OUTWARD ones — cells no closer to the part centre
                # than the pad itself — so the carve never opens the strip
                # between two neighbouring pads of the same part.
                pad_d2 = (world_x - pc.x) ** 2 + (world_y - pc.y) ** 2
                for dr in (-CLEARANCE_HALO, 0, CLEARANCE_HALO):
                    for dc in (-CLEARANCE_HALO, 0, CLEARANCE_HALO):
                        rr, cc = row + dr, col + dc
                        if not (dr or dc) or not grid.in_bounds(rr, cc):
                            continue
                        cx, cy = grid.to_mm(rr, cc)
                        if (cx - pc.x) ** 2 + (cy - pc.y) ** 2 < pad_d2 - 1e-6:
                            continue
                        i = grid._idx(rr, cc)
                        for layer_idx in pad_layers:
                            if body_only[layer_idx][i] and grid._owners[layer_idx][i] == "":
                                grid._owners[layer_idx][i] = owner
                # Keep other nets a clearance ring away from the pad copper.
                _stamp_halo(grid, pad_layers, row, col, owner)

                # If escape, also mark the escape entry cell as owned — an escape
                # via is a plated hole, so own it on BOTH layers + a halo.
                if escape:
                    ex, ey, elayer = escape
                    erow, ecol = grid.to_grid(ex, ey)
                    if grid.in_bounds(erow, ecol):
                        for layer_idx in (0, 1):
                            grid._owners[layer_idx][grid._idx(erow, ecol)] = owner
                            grid.mark_copper(layer_idx, erow, ecol)
                        _stamp_halo(grid, (0, 1), erow, ecol, owner)
            else:
                # Unconnected pad — still physical copper. Hard-block it AND its
                # clearance ring so no net's trace can touch it (the classic
                # "track shorts an unused connector pad" defect).
                for layer_idx in pad_layers:
                    grid.hard_block(layer_idx, row, col)
                    grid.mark_copper(layer_idx, row, col)
                _stamp_halo(grid, pad_layers, row, col, "")

    # Stamp the fanout stubs as net-owned so other nets don't ride over them.
    if escape_stubs:
        for stub in escape_stubs:
            _mark_stub_in_grid(grid, stub, stub.net)

    # Board-edge keepout: reserve the outer ring so copper can't hug the cut
    # edge (KiCad `copper_edge_clearance`). One 0.4 mm cell ≈ the 0.3 mm fab
    # edge clearance.
    last_r, last_c = grid.rows - 1, grid.cols - 1
    for layer_idx in (0, 1):
        for r in range(grid.rows):
            for c in range(grid.cols):
                if r == 0 or c == 0 or r == last_r or c == last_c:
                    if grid._owners[layer_idx][grid._idx(r, c)] is None:
                        grid._owners[layer_idx][grid._idx(r, c)] = ""

    return grid, pad_cells, alt_cells


# ---- A* -------------------------------------------------------------------


def _astar(grid: Grid, start: Cell, goal: Cell, owner: str) -> list[Cell] | None:
    """Find a Manhattan path from start to goal across both layers, avoiding
    cells not passable for `owner`. Returns None if no path exists or if the
    expansion cap is hit (treated as 'no route' — caller falls back to a
    rats-nest line for that segment).

    Hot loop. Cells are (layer, row, col) tuples for cheap hash/eq. Local
    aliases for grid internals avoid attribute lookup in the inner loop."""
    g_layer, g_row, g_col = goal

    # Bound the search to a box around the start↔goal line. Cells far outside
    # this box almost never help and we waste time expanding them.
    row_lo = min(start[1], g_row) - SEARCH_BBOX_SLACK
    row_hi = max(start[1], g_row) + SEARCH_BBOX_SLACK
    col_lo = min(start[2], g_col) - SEARCH_BBOX_SLACK
    col_hi = max(start[2], g_col) + SEARCH_BBOX_SLACK

    # Local aliases — saves attribute lookup × ~1M times.
    grid_owners = grid._owners
    grid_copper = grid._copper
    grid_cols = grid.cols
    grid_rows = grid.rows

    def heuristic(layer: int, row: int, col: int) -> float:
        return abs(row - g_row) + abs(col - g_col) + (
            VIA_COST if layer != g_layer else 0
        )

    open_heap: list[tuple[float, int, Cell]] = []
    counter = 0
    heapq.heappush(open_heap, (heuristic(start[0], start[1], start[2]), counter, start))
    came_from: dict[Cell, Cell] = {}
    g_score: dict[Cell, float] = {start: 0.0}
    closed: set[Cell] = set()
    expansions = 0
    inf = math.inf

    while open_heap:
        if expansions > ASTAR_EXPANSION_CAP:
            return None
        _, _, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current == goal:
            # Reconstruct
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))
        closed.add(current)
        expansions += 1

        cur_layer, cur_row, cur_col = current
        cur_g = g_score[current]

        # Six neighbours: 4 in-plane + 1 via to other layer.
        candidates = (
            (cur_layer, cur_row + 1, cur_col),
            (cur_layer, cur_row - 1, cur_col),
            (cur_layer, cur_row, cur_col + 1),
            (cur_layer, cur_row, cur_col - 1),
            (1 - cur_layer, cur_row, cur_col),
        )

        for n in candidates:
            if n in closed:
                continue
            n_layer, n_row, n_col = n
            # Combined bounds + bbox check (inlined for speed).
            if (
                n_row < row_lo or n_row > row_hi
                or n_col < col_lo or n_col > col_hi
                or n_row < 0 or n_row >= grid_rows
                or n_col < 0 or n_col >= grid_cols
            ):
                continue
            owner_at = grid_owners[n_layer][n_row * grid_cols + n_col]
            if owner_at is not None and owner_at != owner:
                continue
            if n_layer != cur_layer and _via_would_short(
                grid_copper, grid_owners, grid_rows, grid_cols, n_row, n_col, owner
            ):
                # Via move: a plated hole spans both layers. Only block it when a
                # neighbouring cell already holds another net's *committed copper*
                # (a track/pad/via owner), NOT a soft halo/edge reservation — the
                # halo already keeps future tracks away, and rejecting vias next
                # to every reservation cratered routability. This kills the
                # via-drops-next-to-existing-track short without the density hit.
                continue
            step_cost = 1.0 if n_layer == cur_layer else VIA_COST
            tentative_g = cur_g + step_cost
            if tentative_g < g_score.get(n, inf):
                came_from[n] = current
                g_score[n] = tentative_g
                counter += 1
                heapq.heappush(
                    open_heap,
                    (tentative_g + heuristic(n_layer, n_row, n_col), counter, n),
                )

    return None


# ---- Path → traces + vias --------------------------------------------------


def _path_to_artifacts(
    grid: Grid, path: list[Cell], net_name: str, width: float
) -> tuple[list[Trace], list[Via]]:
    """Convert a Cell path into one Trace per layer-run and a Via at each
    layer transition. Cells are (layer, row, col) tuples."""
    if len(path) < 2:
        return [], []

    traces: list[Trace] = []
    vias: list[Via] = []
    current_layer = path[0][0]
    current_segments: list[TraceSegment] = []

    def flush() -> None:
        if current_segments:
            traces.append(
                Trace(
                    net=net_name,
                    layer="top" if current_layer == 0 else "bottom",
                    width=width,
                    segments=list(_collapse_collinear(current_segments)),
                )
            )

    for i in range(1, len(path)):
        prev = path[i - 1]
        curr = path[i]
        prev_layer, prev_row, prev_col = prev
        curr_layer, curr_row, curr_col = curr
        if curr_layer != prev_layer:
            # Via at the transition point.
            x, y = grid.to_mm(prev_row, prev_col)
            vias.append(
                Via(
                    x=x,
                    y=y,
                    drill=VIA_DRILL,
                    diameter=VIA_DIAMETER,
                    net=net_name,
                )
            )
            flush()
            current_layer = curr_layer
            current_segments = []
        else:
            x1, y1 = grid.to_mm(prev_row, prev_col)
            x2, y2 = grid.to_mm(curr_row, curr_col)
            current_segments.append(TraceSegment(x1=x1, y1=y1, x2=x2, y2=y2))
    flush()
    return traces, vias


def _collapse_collinear(segments: list[TraceSegment]) -> list[TraceSegment]:
    """Merge consecutive segments that go in the same direction."""
    if not segments:
        return []
    out: list[TraceSegment] = [segments[0]]
    for seg in segments[1:]:
        last = out[-1]
        if last.x2 != seg.x1 or last.y2 != seg.y1:
            out.append(seg)
            continue
        d_last = (last.x2 - last.x1, last.y2 - last.y1)
        d_seg = (seg.x2 - seg.x1, seg.y2 - seg.y1)
        # Same direction (or co-linear) → extend.
        if (d_last[0] == 0 and d_seg[0] == 0) or (d_last[1] == 0 and d_seg[1] == 0):
            out[-1] = TraceSegment(x1=last.x1, y1=last.y1, x2=seg.x2, y2=seg.y2)
        else:
            out.append(seg)
    return out


# ---- Main entry point -----------------------------------------------------


_POWER_NET_PREFIXES = ("VCC", "VDD", "VBUS", "VBAT", "+3V", "+5V", "3V3", "5V")


def _trace_width_for(net_name: str) -> float:
    upper = net_name.upper()
    if upper in {"GND", "VSS"} or any(upper.startswith(p) for p in _POWER_NET_PREFIXES):
        return TRACE_WIDTH_POWER
    return TRACE_WIDTH_DEFAULT


def _net_priority(net_name: str) -> int:
    """Lower = routed first. GND, then power, then signals (USB first)."""
    upper = net_name.upper()
    if upper in {"GND", "VSS", "AGND"}:
        return 0
    if any(upper.startswith(p) for p in _POWER_NET_PREFIXES):
        return 1
    if "USB" in upper:
        return 2
    return 3


def _order_salt(name: str, salt: int) -> int:
    """Deterministic per-net jitter for the routing order (NOT hash() — that is
    PYTHONHASHSEED-dependent and would make routing non-reproducible). Lets each
    rip-up attempt try a different relative order of the contested nets."""
    h = 2166136261
    for ch in name:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return (h ^ (salt * 2654435761)) & 0xFFFFFFFF


def _route_one_attempt(
    project: Project,
    board: Board,
    bumped_nets: set[str],
    *,
    only_nets: set[str] | None = None,
    preexisting_traces: list[Trace] | None = None,
    preexisting_vias: list[Via] | None = None,
    order_salt: int = 0,
    deadline: float | None = None,
) -> tuple[list[Trace], list[Via], list[RatsLine]]:
    """Single routing pass. `bumped_nets` get routed first (they failed in a
    previous pass and deserve priority on the empty grid this time).

    Partial reroute (#33): when `only_nets` is given, route only those nets and
    stamp `preexisting_traces`/`preexisting_vias` (the kept nets) as obstacles so
    the rerouted nets weave around what we're preserving."""
    # Compute pad escapes for dense ICs first — these become the entry points
    # the main router uses, plus the visible stub traces from pad to escape.
    plane_nets = {p.net for p in (getattr(board, "planes", None) or [])}
    escapes, escape_stubs, escape_vias = _compute_pad_escapes(project, board, only_nets, plane_nets)
    grid, pad_cells, alt_cells = _build_grid(project, board, escapes, escape_stubs)

    # Partial reroute: kept traces/vias are immovable obstacles for this pass.
    if preexisting_traces:
        for t in preexisting_traces:
            _stamp_trace_in_grid(grid, t)
    if preexisting_vias:
        for v in preexisting_vias:
            _stamp_via_in_grid(grid, v)

    # The escape stubs are emitted as real traces — they're part of the final
    # routed output so the user can see the fanout in the canvas.
    new_traces: list[Trace] = list(escape_stubs)
    new_vias: list[Via] = list(escape_vias)
    failed_segments: list[RatsLine] = []

    nets_sorted = sorted(
        project.ir.nets,
        key=lambda n: (
            0 if n.name in bumped_nets else 1,
            _net_priority(n.name),
            # On retries (salt != 0) shuffle the contested nets' relative order
            # so a previously-blocked net gets a clear corridor this time;
            # on the first pass (salt 0) this term is constant → original order.
            _order_salt(n.name, order_salt) if order_salt else 0,
            -len(n.pins),
        ),
    )

    pour_active = bool(getattr(board, "has_ground_pour", True))

    def _skip(n) -> bool:
        """Nets this pass never routes: too small, out of scope, or carried by
        the pour / an inner plane."""
        if len(n.pins) < 2:
            return True
        if only_nets is not None and n.name not in only_nets:
            return True
        if pour_active and n.name.upper() in {"GND", "VSS", "AGND", "DGND"}:
            return True
        return n.name in plane_nets

    for idx, net in enumerate(nets_sorted):
        if deadline is not None and time.monotonic() > deadline:
            # Out of budget. Everything still unrouted becomes an honest rat —
            # the canvas already offers Finalize when rats remain — rather than
            # the user waiting minutes more for a preview.
            for remaining in nets_sorted[idx:]:
                if _skip(remaining):
                    continue
                first = tuple(remaining.pins[0])
                start = pad_cells.get(first)
                if start is None:
                    continue
                sx, sy = grid.to_mm(start[1], start[2])
                for pin in remaining.pins[1:]:
                    cell = pad_cells.get(tuple(pin))
                    if cell is None:
                        continue
                    ex, ey = grid.to_mm(cell[1], cell[2])
                    failed_segments.append(
                        RatsLine(net=remaining.name, x1=sx, y1=sy, x2=ex, y2=ey)
                    )
            break
        if len(net.pins) < 2:
            continue
        if only_nets is not None and net.name not in only_nets:
            continue  # partial reroute — leave other nets' traces untouched
        # GND uses a copper pour on the bottom layer when enabled — skip
        # individual traces. The pour fills empty bottom-layer area and
        # connects all GND pads automatically (in a real fab spec it'd be a
        # polygon; for our visualizer we just render a green tint).
        if pour_active and net.name.upper() in {"GND", "VSS", "AGND", "DGND"}:
            continue
        if net.name in plane_nets:
            continue  # carried by an inner plane; every SMD pad got its via above
        width = _trace_width_for(net.name)

        connected_pads: list[tuple[str, str]] = [tuple(net.pins[0])]

        for pin in net.pins[1:]:
            target_key = tuple(pin)
            target_cell = pad_cells.get(target_key)
            if target_cell is None:
                continue

            best_path: list[Cell] | None = None
            # Only search from the few connected pads NEAREST the target. Trying
            # every connected pad made a k-pin net cost O(k²) A* runs — a 10x10
            # LED matrix's 11-pin ROW/COL nets alone were ~2,200 searches per
            # pass (minutes). The nearest pads give the shortest path in
            # practice; a couple of runners-up cover a blocked corridor.
            _, tr, tc = target_cell
            starts = sorted(
                (cp for cp in connected_pads if cp in pad_cells),
                key=lambda cp: abs(pad_cells[cp][1] - tr) + abs(pad_cells[cp][2] - tc),
            )[:NEAREST_STARTS]
            # Entry points: the escape via (both layers — it is a plated hole)
            # first; if nothing routes, the RAW pad on its own layer for both
            # ends. The second round only runs on failure, so the common case
            # costs nothing extra.
            target_options = [target_cell]
            if target_key in alt_cells:
                target_options.append(alt_cells[target_key])
            for round_no, tgt in enumerate(target_options):
                for cp in starts:
                    start_cells = [pad_cells[cp]]
                    if round_no == 1 and cp in alt_cells:
                        start_cells.append(alt_cells[cp])
                    for start_cell in start_cells:
                        _, sr, sc = start_cell
                        layers = (0, 1) if start_cell is pad_cells[cp] else (start_cell[0],)
                        for start_layer in layers:
                            start = (start_layer, sr, sc)
                            if not grid.is_passable(start_layer, sr, sc, net.name):
                                continue
                            path = _astar(grid, start, tgt, net.name)
                            if path is None:
                                continue
                            if best_path is None or len(path) < len(best_path):
                                best_path = path
                if best_path is not None:
                    break

            if best_path is None:
                # Rat from the NEAREST connected pad, not pins[0] — a big net
                # that fails wholesale otherwise fans every rat out of one pad,
                # which reads as a broken board instead of N missing hops.
                tx, ty = grid.to_mm(target_cell[1], target_cell[2])
                near: tuple[float, float] | None = None
                for cp in connected_pads:
                    sp = pad_cells.get(cp)
                    if sp is None:
                        continue
                    sx, sy = grid.to_mm(sp[1], sp[2])
                    if near is None or (sx - tx) ** 2 + (sy - ty) ** 2 < (
                        near[0] - tx
                    ) ** 2 + (near[1] - ty) ** 2:
                        near = (sx, sy)
                if near is None:
                    continue
                failed_segments.append(
                    RatsLine(net=net.name, x1=near[0], y1=near[1], x2=tx, y2=ty)
                )
                continue

            for c in best_path:
                grid._owners[c[0]][c[1] * grid.cols + c[2]] = net.name
                grid._copper[c[0]][c[1] * grid.cols + c[2]] = 1

            traces_seg, vias_seg = _path_to_artifacts(grid, best_path, net.name, width)
            new_traces.extend(traces_seg)
            new_vias.extend(vias_seg)
            # A via is a plated hole (big copper on both layers): keep other nets
            # a clearance ring away so a later net's track can't short it.
            for v in vias_seg:
                vr, vc = grid.to_grid(v.x, v.y)
                _stamp_halo(grid, (0, 1), vr, vc, net.name)
            connected_pads.append(target_key)

    # A net that failed WHOLESALE (rats but no main-route traces) must not keep
    # its escape stubs/vias — they'd be dangling copper (KICAD_via_dangling) next
    # to the rat for the same net. Partially-routed nets keep theirs: their
    # stubs feed the segments that did route.
    failed_nets = {r.net for r in failed_segments}
    if failed_nets:
        stub_ids = {id(t) for t in escape_stubs}
        routed_nets = {t.net for t in new_traces if id(t) not in stub_ids}
        orphan = failed_nets - routed_nets
        if orphan:
            new_traces = [
                t for t in new_traces
                if not (t.net in orphan and id(t) in stub_ids)
            ]
            escape_via_ids = {id(v) for v in escape_vias}
            new_vias = [
                v for v in new_vias
                if not (v.net in orphan and id(v) in escape_via_ids)
            ]

    return new_traces, new_vias, failed_segments


# Wall-clock budget for the PREVIEW route. The preview exists to be looked at;
# the manufacturing route is FreeRouting behind the Finalize button, which the
# canvas already offers whenever nets are left unrouted. Measured across the 60
# stored sweep designs before this existed: 57 boards cost 2048 s in total, and
# ONE of them — 117 parts on a 272 mm board — took 1316 s of that, twenty-two
# minutes, to end up with 6 rats anyway. Nobody waits twenty-two minutes for a
# preview, and the reviewers' "slow PCB generation" was this. Stopping at the
# budget and saying honestly what is left is both faster and truer.
# 45 s is the measured setting, from full 60-board sweeps at both candidates:
# at 25 s six boards got worse for +10 rats (every one of them needed >25 s
# unbounded); at 45 s only the two known-congested boards move (stepper-1
# 14 -> 15, keyboard-1 2 -> 5, +4 rats total) while the worst case drops
# 1316 s -> 46 s and the whole sweep runs 2033 s -> 699 s. Below ~20 s the
# first FULL pass cannot complete on the 117-part board and it collapses
# 6 -> 29 rats — do not lower this without re-running
# scripts/route_bench.py against docs/routing/bench-2026-08-20-before.txt.
ROUTE_TIME_BUDGET_S = float(os.environ.get("SYNQIT_ROUTE_BUDGET_S", "45"))

FULL_PASSES = 4      # whole-board re-routes (global rearrangement) before...
MAX_ATTEMPTS = 8     # ...cheap partial-reroute cleanup fills the rest
RIPUP_NEIGHBOURS = 6  # most extra nets a cleanup pass may rip up alongside
STALLED_PASSES = 2   # give up a phase after this many passes that buy nothing


def _blocking_neighbours(
    traces: list[Trace], failed: list[RatsLine], already: set[str]
) -> set[str]:
    """Nets whose copper crosses a failed rat's bounding box — the ones standing
    in its way. Bounded to RIPUP_NEIGHBOURS so a wide rip-up stays cheap."""
    boxes = [
        (min(r.x1, r.x2) - 1.0, min(r.y1, r.y2) - 1.0,
         max(r.x1, r.x2) + 1.0, max(r.y1, r.y2) + 1.0)
        for r in failed
    ]
    hits: dict[str, int] = {}
    for t in traces:
        if t.net in already:
            continue
        for seg in t.segments:
            for x0, y0, x1, y1 in boxes:
                if (min(seg.x1, seg.x2) <= x1 and max(seg.x1, seg.x2) >= x0
                        and min(seg.y1, seg.y2) <= y1 and max(seg.y1, seg.y2) >= y0):
                    hits[t.net] = hits.get(t.net, 0) + 1
                    break
    # Most-overlapping first: those are the real corridor hogs.
    return {n for n, _ in sorted(hits.items(), key=lambda kv: -kv[1])[:RIPUP_NEIGHBOURS]}


def auto_route(project: Project) -> Board:
    """Route the board: one full pass, then rip-up-and-reroute only the nets that
    failed — each retry reshuffles their relative order (a deterministic
    per-attempt salt) and weaves them around the nets that already routed, so a
    net blocked one round gets a clear corridor the next.

    Routing only the handful of failed nets on retries (instead of re-routing the
    whole board every attempt) is what keeps this fast on big boards while still
    closing the failures a greedy single pass leaves on the table. A* stays
    bounded by an expansion cap (ASTAR_EXPANSION_CAP) and a search bounding box
    (SEARCH_BBOX_SLACK) so worst-case time is predictable."""
    board = project.board
    if board is None:
        board = placement.auto_place(project)

    # Phase 1 — a few FULL re-routes. Re-routing the whole board lets every net
    # re-arrange, which is the only thing that frees a net wedged shut by its
    # neighbours; the salt reshuffles their order each pass. Bounded so a hard
    # board can't run the expensive full pass indefinitely.
    best_traces: list[Trace] = []
    best_vias: list[Via] = []
    best_failed: list[RatsLine] = []
    best_count = math.inf
    bumped: set[str] = set()
    # A board that simply cannot be routed must not cost the full budget: every
    # extra pass runs A* to its expansion cap on the same wedged nets and buys
    # nothing. Stop after STALLED_PASSES passes that fail to beat the best count
    # (a beta reviewer reported PCB routing "slowing down" — it was always the
    # unroutable boards paying for retries that never helped).
    stalled = 0
    deadline = time.monotonic() + ROUTE_TIME_BUDGET_S if ROUTE_TIME_BUDGET_S > 0 else None
    for attempt in range(FULL_PASSES):
        traces, vias, failed = _route_one_attempt(
            project, board, bumped_nets=bumped, order_salt=attempt, deadline=deadline
        )
        if len(failed) < best_count:
            best_count, best_traces, best_vias, best_failed = len(failed), traces, vias, failed
            stalled = 0
            if not failed:
                break
        else:
            stalled += 1
            if stalled >= STALLED_PASSES:
                break
        bumped.update(f.net for f in failed)

    # Phase 2 — cheap cleanup: rip up the still-failing nets AND the neighbours
    # sitting in their way, then retry that small set with a fresh order.
    #
    # Ripping up only the failed net cannot help when the blocker is a DIFFERENT
    # net that took the corridor first — the retry re-runs the same search
    # against the same obstacles and fails identically (a robot-car board sat at
    # 2 rats through all four retries even though both nets route fine in
    # isolation). Widening the rip-up to the traces that cross the failed net's
    # bounding box lets the corridor be renegotiated, and stays cheap because
    # the set is bounded.
    traces, vias, failed = best_traces, best_vias, best_failed
    attempt = FULL_PASSES
    stalled = 0
    # Every net that has been wedged in this phase routes FIRST on later
    # retries: ripping up the neighbour that took the corridor is pointless if
    # priority order hands it the same corridor again (a USB-C CC pin behind
    # the receptacle's pin rows lost its only exit to VCC_3V3 on every retry —
    # 7 sweep boards, 1 rat each, every one routable in isolation). Accumulated,
    # not just the current failure: two sibling nets (CC1/CC2) otherwise trade
    # the rat back and forth, and a same-count swap is accepted ONCE so that
    # second net gets its turn at the front (the stall counter still bounds it).
    wedged_acc: set[str] = set()
    seen_failed: set[frozenset[str]] = {frozenset(f.net for f in failed)}
    while failed and attempt < MAX_ATTEMPTS and stalled < STALLED_PASSES:
        wedged = {f.net for f in failed}
        wedged_acc |= wedged
        failed_nets = set(wedged)
        if attempt > FULL_PASSES:  # first retry is the cheap one; then widen
            failed_nets |= _blocking_neighbours(traces, failed, failed_nets)
        kept_traces = [t for t in traces if t.net not in failed_nets]
        kept_vias = [v for v in vias if v.net not in failed_nets]
        if deadline is not None and time.monotonic() > deadline:
            break  # out of preview budget — keep the best result and report it
        rt, rv, rf = _route_one_attempt(
            project, board, bumped_nets=set(wedged_acc), only_nets=failed_nets,
            preexisting_traces=kept_traces, preexisting_vias=kept_vias, order_salt=attempt,
            deadline=deadline,
        )
        if len(rf) >= len(failed) and attempt > FULL_PASSES:
            # Wedged-first is not always the better order (a power net bumped
            # behind three signals can lose its own corridor) — on a widened
            # retry also try the natural priority order and keep the better.
            rt2, rv2, rf2 = _route_one_attempt(
                project, board, bumped_nets=set(), only_nets=failed_nets,
                preexisting_traces=kept_traces, preexisting_vias=kept_vias, order_salt=attempt,
                deadline=deadline,
            )
            if len(rf2) < len(rf):
                rt, rv, rf = rt2, rv2, rf2
        rf_set = frozenset(f.net for f in rf)
        if len(rf) < len(failed):  # genuine improvement
            traces, vias, failed = kept_traces + rt, kept_vias + rv, rf
            stalled = 0
        elif len(rf) == len(failed) and rf_set not in seen_failed:
            traces, vias, failed = kept_traces + rt, kept_vias + rv, rf  # a swap: new wedged net goes first next
        else:
            stalled += 1
        seen_failed.add(rf_set)
        attempt += 1

    # Plane nets are carried by the inner planes: their fan-out via IS the
    # connection, and no trace will ever join it — never treat those as orphans.
    plane_nets = {pl.net for pl in (getattr(board, "planes", None) or [])}
    traces, vias = _drop_orphan_escapes(traces, vias, keep_nets=plane_nets)
    board.traces = traces
    board.vias = vias
    board.rats = failed
    board.auto_routed = True
    return board


# A fan-out stub is only copper worth having if the net's real routing actually
# arrives at its via. When a net routes partially, the leftover stubs are
# dangling track ends — KiCad DRC reported 31 of them on the 2026-08-20
# review's exported board, next to "0 unconnected items".
_ESCAPE_JOIN_MM = 0.45          # a trace end this close to the via counts as joined


def _drop_orphan_escapes(
    traces: list[Trace], vias: list[Via], keep_nets: set[str] | None = None
) -> tuple[list[Trace], list[Via]]:
    """Remove fan-out stubs (and their vias) that no routed trace connects to.
    `keep_nets` are never touched (a 4-layer plane net's via is its whole
    connection — nothing routes to it by design)."""
    keep_nets = keep_nets or set()
    by_net: dict[str, list[Trace]] = {}
    for tr in traces:
        by_net.setdefault(tr.net, []).append(tr)

    def _is_stub(tr: Trace) -> bool:
        # An escape stub is a single short segment at the fan-out width.
        if len(tr.segments) != 1:
            return False
        s = tr.segments[0]
        return math.hypot(s.x2 - s.x1, s.y2 - s.y1) <= 3.0 and tr.width <= ESCAPE_TRACE_WIDTH + 1e-6

    keep_traces: list[Trace] = []
    dropped_ends: list[tuple[str, float, float]] = []
    for tr in traces:
        if not _is_stub(tr) or tr.net in keep_nets:
            keep_traces.append(tr)
            continue
        s = tr.segments[0]
        ends = ((s.x1, s.y1), (s.x2, s.y2))
        joined = False
        for other in by_net.get(tr.net, ()):
            if other is tr or _is_stub(other):
                continue
            for os_ in other.segments:
                for ox, oy in ((os_.x1, os_.y1), (os_.x2, os_.y2)):
                    if any(math.hypot(ox - ex, oy - ey) <= _ESCAPE_JOIN_MM for ex, ey in ends):
                        joined = True
                        break
                if joined:
                    break
            if joined:
                break
        if joined:
            keep_traces.append(tr)
        else:
            dropped_ends.append((tr.net, s.x2, s.y2))

    if not dropped_ends:
        return traces, vias
    keep_vias = [
        v for v in vias
        if not any(
            v.net == net and math.hypot(v.x - x, v.y - y) <= _ESCAPE_JOIN_MM
            for net, x, y in dropped_ends
        )
    ]
    return keep_traces, keep_vias


def nets_touching(project: Project, refdeses: set[str]) -> set[str]:
    """Net names that include a pin on any of the given components."""
    return {
        net.name
        for net in project.ir.nets
        if any(r in refdeses for r, _ in net.pins)
    }


def reroute_nets(project: Project, affected_nets: set[str]) -> Board:
    """Partial reroute (#33): rip up only the affected nets, keep every other
    net's traces/vias, and reroute the affected ones around what's preserved.

    This is what makes the board editable — moving one part re-routes its nets
    without discarding the rest of the layout. Falls back to a full route when
    there's no board yet or nothing routed to preserve."""
    board = project.board
    if board is None:
        return auto_route(project)

    affected = {n for n in affected_nets}
    if not affected:
        return board

    kept_traces = [t for t in board.traces if t.net not in affected]
    kept_vias = [v for v in board.vias if v.net not in affected]
    kept_rats = [r for r in board.rats if r.net not in affected]

    new_traces, new_vias, failed = _route_one_attempt(
        project,
        board,
        bumped_nets=set(),
        only_nets=affected,
        preexisting_traces=kept_traces,
        preexisting_vias=kept_vias,
    )

    board.traces = kept_traces + new_traces
    board.vias = kept_vias + new_vias
    board.rats = kept_rats + failed
    board.auto_routed = True
    return board
