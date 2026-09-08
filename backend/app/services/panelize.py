"""Panelization — arraying a board onto the panel a fab actually builds.

Why this module exists
----------------------
Fabs build and ship PANELS, not single boards, and an assembly house needs a
panel (or at least breakaway rails) to hold a small board in the pick-and-place
machine at all. Small boards are quoted per panel too, so ordering a 20 x 20 mm
board one-up pays for mostly empty laminate. Synqit had nothing to say about any
of it, and two review rounds asked (2026-08-20) — one reviewer wrote their own
panel by hand and documented it as a workaround.

Everything here is a PURE FUNCTION of the board plus a `PanelSpec`: the panel is
derived, never stored, so it cannot go stale against a board that changed. The
mechanical tools compose these; the Gerber writer renders them.

Frames
------
Board coordinates are the IR/canvas frame used everywhere else (mm, origin at
the board's bottom-left). Panel coordinates share the units and axes with the
panel's own bottom-left as origin, so a board at panel slot (r, c) has its
origin at `board_origin(...)` and every board feature simply translates.

The two separation methods are not interchangeable
--------------------------------------------------
V-scoring runs a blade the FULL width of the panel in one straight pass, so it
is only valid when the cut line crosses nothing but straight, collinear board
edges — a round board, a board with a notch, or boards separated by a gap
cannot be V-scored. Mouse bites route the boards apart and hold them with small
drilled tabs, which works for any outline. `MECH_R06_panel` refuses the invalid
combination rather than emitting geometry a fab would have to query.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import Board, PanelSpec

# --- fab constants (conservative, and checked against the profile) -----------
# Tooling//fiducial hardware lives on the rails.
TOOLING_HOLE_D = 3.175          # 1/8", the industry-standard panel tooling hole
# A rail has to carry a 3.175 mm hole with laminate left around it, plus the
# clamp bite. 5 mm is what every fab's panelization guide asks for.
MIN_RAIL = 5.0
TOOLING_EDGE_INSET = 5.0        # centre distance from the panel corner
FIDUCIAL_D = 1.0                # copper dot; the mask opening is 2x this
FIDUCIAL_EDGE_INSET = 5.0
# A V-score groove needs clear board on both sides: no copper, and no component
# body, within this distance of the score line (the blade cuts a groove and the
# board flexes when it is snapped).
VSCORE_KEEPOUT = 1.0
# Mouse-bite tabs: a tab is a short bridge of laminate perforated by a row of
# small holes so it snaps cleanly.
TAB_WIDTH = 5.0                 # along the board edge
TAB_HOLE_D = 0.5
TAB_HOLE_PITCH = 1.0
# The routed slot the cutter travels in when boards are mouse-bitten.
MIN_ROUTE_GAP = 2.0
# Largest panel most low-cost fabs accept without a surcharge (JLCPCB/PCBWay).
MAX_PANEL_W = 400.0
MAX_PANEL_H = 500.0
# Below this, a board is too small to be handled or assembled on its own.
SMALL_BOARD_MM = 50.0


@dataclass
class Tab:
    """One mouse-bite tab: a bridge of laminate perforated by a row of holes."""
    x: float                    # centre, panel frame
    y: float
    horizontal: bool            # the tab bridges a horizontal seam
    holes: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Panel:
    """Derived panel geometry. Every coordinate is in the panel frame."""
    width: float
    height: float
    rows: int
    cols: int
    method: str
    board_w: float
    board_h: float
    rail: float
    gap: float
    origins: list[tuple[float, float]]              # one per board, row-major
    v_scores: list[tuple[float, float, float, float]]  # full-width score lines
    tabs: list[Tab]
    tooling_holes: list[tuple[float, float, float]]  # (x, y, diameter)
    fiducials: list[tuple[float, float]]

    @property
    def boards(self) -> int:
        return self.rows * self.cols

    @property
    def utilisation(self) -> float:
        """Fraction of the panel that is finished board — what you pay for
        versus what you keep."""
        area = self.width * self.height
        return (self.boards * self.board_w * self.board_h / area) if area else 0.0


def board_origin(spec: PanelSpec, board_w: float, board_h: float,
                 row: int, col: int) -> tuple[float, float]:
    """Panel-frame origin of the board in slot (row, col), row 0 at the bottom."""
    return (
        spec.rail + col * (board_w + spec.gap),
        spec.rail + row * (board_h + spec.gap),
    )


def derive(board: Board, spec: PanelSpec) -> Panel:
    """Compute the panel for `board` under `spec`. Geometry only — no checks;
    `mechanical_verifier.MECH_R06_panel` decides whether it is buildable."""
    bw, bh = board.width, board.height
    # V-scoring leaves no gap by definition: the groove is the boundary, and
    # asking for one would just produce a panel that cannot be scored.
    gap = 0.0 if spec.method == "v_score" else max(spec.gap, MIN_ROUTE_GAP)
    eff = spec.model_copy(update={"gap": gap})

    width = 2 * spec.rail + spec.cols * bw + (spec.cols - 1) * gap
    height = 2 * spec.rail + spec.rows * bh + (spec.rows - 1) * gap

    origins = [
        board_origin(eff, bw, bh, r, c)
        for r in range(spec.rows)
        for c in range(spec.cols)
    ]

    v_scores: list[tuple[float, float, float, float]] = []
    tabs: list[Tab] = []
    if spec.method == "v_score":
        # Full-width / full-height passes, including the rail boundaries — the
        # rails are snapped off along the same lines.
        for c in range(spec.cols + 1):
            x = spec.rail + c * bw
            v_scores.append((x, 0.0, x, height))
        for r in range(spec.rows + 1):
            y = spec.rail + r * bh
            v_scores.append((0.0, y, width, y))
    else:
        for (ox, oy) in origins:
            # Two tabs per edge, at the third-points, so a board is held at
            # eight places and does not sag or twist during assembly.
            for frac in (1 / 3, 2 / 3):
                tabs.append(_tab(ox + frac * bw, oy, True))            # bottom
                tabs.append(_tab(ox + frac * bw, oy + bh, True))       # top
                tabs.append(_tab(ox, oy + frac * bh, False))           # left
                tabs.append(_tab(ox + bw, oy + frac * bh, False))      # right

    tooling: list[tuple[float, float, float]] = []
    fiducials: list[tuple[float, float]] = []
    if spec.rail > 0:
        for x, y in (
            (TOOLING_EDGE_INSET, TOOLING_EDGE_INSET),
            (width - TOOLING_EDGE_INSET, TOOLING_EDGE_INSET),
            (TOOLING_EDGE_INSET, height - TOOLING_EDGE_INSET),
            (width - TOOLING_EDGE_INSET, height - TOOLING_EDGE_INSET),
        ):
            tooling.append((x, y, TOOLING_HOLE_D))
        # Three fiducials, deliberately NOT symmetric: the asymmetric third one
        # is how the machine tells which way round the panel is loaded.
        fiducials = [
            (FIDUCIAL_EDGE_INSET + 6.0, FIDUCIAL_EDGE_INSET),
            (width - FIDUCIAL_EDGE_INSET, FIDUCIAL_EDGE_INSET),
            (FIDUCIAL_EDGE_INSET, height - FIDUCIAL_EDGE_INSET),
        ]

    return Panel(
        # NOT rounded: the score lines, tab positions and tooling holes are all
        # derived from these, so rounding here alone would leave the geometry
        # disagreeing with the outline it is supposed to sit inside. Rounding
        # is a presentation concern — the API and the UI do it.
        width=width, height=height,
        rows=spec.rows, cols=spec.cols, method=spec.method,
        board_w=bw, board_h=bh, rail=spec.rail, gap=gap,
        origins=origins, v_scores=v_scores, tabs=tabs,
        tooling_holes=tooling, fiducials=fiducials,
    )


def _tab(x: float, y: float, horizontal: bool) -> Tab:
    """A tab centred at (x, y). The perforation row runs ACROSS the seam so the
    laminate snaps along the holes rather than tearing into the board."""
    n = max(3, int(TAB_WIDTH / TAB_HOLE_PITCH) + 1)
    span = (n - 1) * TAB_HOLE_PITCH
    holes = []
    for i in range(n):
        off = -span / 2 + i * TAB_HOLE_PITCH
        holes.append((x + off, y) if horizontal else (x, y + off))
    return Tab(x=x, y=y, horizontal=horizontal, holes=holes)


def best_fit(board: Board, max_w: float = MAX_PANEL_W, max_h: float = MAX_PANEL_H,
             method: str = "v_score", rail: float = 5.0) -> PanelSpec:
    """The largest array of this board that still fits a standard panel.

    Deliberately capped at 20 x 20 by the model: past that the panel is heavy
    enough to sag in reflow, and the cap keeps a typo from proposing a 400-up.
    """
    gap = 0.0 if method == "v_score" else MIN_ROUTE_GAP
    def fits(n: int, size: float, limit: float) -> bool:
        return 2 * rail + n * size + (n - 1) * gap <= limit

    cols = max(1, max((n for n in range(1, 21) if fits(n, board.width, max_w)), default=1))
    rows = max(1, max((n for n in range(1, 21) if fits(n, board.height, max_h)), default=1))
    return PanelSpec(rows=rows, cols=cols, method=method, gap=gap, rail=rail)


# Every low-cost fab's cheapest quote tier is a 100 x 100 mm panel; below it the
# price is flat, so a board that fits several-up inside that square gets those
# extra boards for nothing.
CHEAP_TIER = 100.0


def panel_board(board: Board, panel: Panel) -> Board:
    """The panel as a synthetic `Board` — every slot's copper translated into
    panel coordinates.

    A RENDER ARTIFACT only: it is never persisted, never verified, and never
    shown in the editor. Building it means the existing Gerber layer writers
    work on a panel unchanged, so the panel and the single board can never be
    produced by two different code paths that drift apart.

    Refdeses are deliberately NOT made unique per slot: each copy carries the
    same nets, which is what the copper writers key on, and every copy really
    is the same net — they are separate boards, so nothing can short between
    them.
    """
    comps = []
    traces = []
    vias = []
    for (ox, oy) in panel.origins:
        for pc in board.components:
            comps.append(pc.model_copy(update={"x": pc.x + ox, "y": pc.y + oy}))
        for tr in board.traces:
            traces.append(tr.model_copy(update={
                "segments": [
                    s.model_copy(update={
                        "x1": s.x1 + ox, "y1": s.y1 + oy,
                        "x2": s.x2 + ox, "y2": s.y2 + oy,
                    })
                    for s in tr.segments
                ],
            }))
        for v in board.vias:
            vias.append(v.model_copy(update={"x": v.x + ox, "y": v.y + oy}))

    return board.model_copy(update={
        "width": panel.width,
        "height": panel.height,
        "shape": "rect",
        "components": comps,
        "traces": traces,
        "vias": vias,
        "rats": [],
    })


def pour_regions(panel: Panel) -> list[tuple[float, float, float, float]]:
    """(cx, cy, w, h) of each board slot — where a ground pour may be filled.
    One pour across the whole panel would bridge the routing slots between
    boards and flood the breakaway rails."""
    return [
        (ox + panel.board_w / 2, oy + panel.board_h / 2, panel.board_w, panel.board_h)
        for ox, oy in panel.origins
    ]


def suggest(board: Board) -> PanelSpec | None:
    """A sensible DEFAULT panel, or None when one-up is the honest answer.

    Targets the fab's cheapest size tier rather than the biggest panel that
    physically fits: someone ordering prototypes wants the free boards inside
    the 100 x 100 mm flat-rate square, not 144-up on a 370 x 490 sheet. Call
    `best_fit` directly for the volume answer.
    """
    # Only mouse bites can separate a non-straight outline.
    method = "v_score" if board.shape == "rect" else "mouse_bites"
    spec = best_fit(board, max_w=CHEAP_TIER, max_h=CHEAP_TIER, method=method)
    if spec.rows * spec.cols <= 1:
        return None
    return spec
