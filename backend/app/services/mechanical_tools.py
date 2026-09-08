"""Mechanical tool-calls — Week 3 task 5.4.

The mechanical side obeys the SAME anti-hallucination contract as the electrical
agent: the LLM never writes the mechanical IR as text, it only mutates it through
these strict, typed tools (mirroring services/tools.py). Every tool takes a
`Project`, mutates `project.mechanical` in place, and returns
`(result_dict, DiffEntry)` so the change is a revertable commit like any other.

Coordinates are mm in the IR frame (Y-up, origin bottom-left) — the same frame
the Board uses, so a mounting hole at (x, y) lines up with placement.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from app.models import (
    BoardOutlineMech,
    ConnectorCutout,
    DiffEntry,
    Enclosure,
    KeepoutZone,
    MechanicalIR,
    MountingHole,
    PanelSpec,
    Point,
    Project,
)


from app.services import mechanical_derive
from app.services.tools import ToolError


class MechToolError(ToolError):
    """Raised when a mechanical tool call is invalid (bad ref, missing target).
    Subclasses ToolError so the agent's tool loop catches it like any other."""


def _mech(project: Project) -> MechanicalIR:
    """Return the project's mechanical IR, creating an empty one on first use."""
    if project.mechanical is None:
        project.mechanical = MechanicalIR()
    return project.mechanical


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# Outline
# --------------------------------------------------------------------------- #
def set_board_outline(
    project: Project,
    *,
    shape: str = "rounded_rect",
    width: float | None = None,
    height: float | None = None,
    corner_radius: float | None = None,
    points: list | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    """Set/replace the physical board outline. Defaults width/height from the
    placed board when omitted, so the mechanical outline starts matched to the
    layout. `points` (for shape="polygon") is a list of {x, y} dicts / [x, y]
    pairs in the IR frame — used by the IDF import to carry a custom MCAD
    outline; width/height are then derived from its bounding box."""
    mech = _mech(project)
    cur = mech.outline
    pts: list[Point] = []
    if points:
        for p in points:
            if isinstance(p, dict):
                pts.append(Point(x=float(p["x"]), y=float(p["y"])))
            else:
                pts.append(Point(x=float(p[0]), y=float(p[1])))
    if pts:
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        width = width if width is not None else (max(xs) - min(xs))
        height = height if height is not None else (max(ys) - min(ys))
    w = width if width is not None else (cur.width if cur else None)
    h = height if height is not None else (cur.height if cur else None)
    if w is None or h is None:
        if project.board is not None:
            w = w if w is not None else project.board.width
            h = h if h is not None else project.board.height
        else:
            w = w if w is not None else 50.0
            h = h if h is not None else 40.0
    mech.outline = BoardOutlineMech(
        shape=shape,  # type: ignore[arg-type]
        width=float(w),
        height=float(h),
        corner_radius=(
            corner_radius if corner_radius is not None
            else (cur.corner_radius if cur else 3.0)
        ),
        points=pts,
    )
    return (
        {"outline": mech.outline.model_dump()},
        DiffEntry(kind="modify", summary=f"Board outline: {shape} {w:.0f}×{h:.0f} mm"),
    )


# --------------------------------------------------------------------------- #
# Mounting holes
# --------------------------------------------------------------------------- #
def add_mounting_hole(
    project: Project,
    *,
    x: float,
    y: float,
    diameter: float = 3.2,
    plated: bool = False,
    standoff: str | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    mech = _mech(project)
    hole = MountingHole(
        id=_id("mh"), x=float(x), y=float(y), diameter=float(diameter),
        plated=bool(plated), standoff=standoff,
    )
    mech.mountingHoles.append(hole)
    return (
        {"mountingHole": hole.model_dump()},
        DiffEntry(kind="add", summary=f"Mounting hole Ø{diameter:.1f} at ({x:.1f}, {y:.1f})"),
    )


def delete_mounting_hole(
    project: Project, *, id: str
) -> tuple[dict[str, Any], DiffEntry]:
    mech = _mech(project)
    before = len(mech.mountingHoles)
    mech.mountingHoles = [h for h in mech.mountingHoles if h.id != id]
    if len(mech.mountingHoles) == before:
        raise MechToolError(f"No mounting hole {id!r}.")
    return ({"deleted": id}, DiffEntry(kind="remove", summary=f"Removed mounting hole {id}"))


# --------------------------------------------------------------------------- #
# Keep-out zones
# --------------------------------------------------------------------------- #
def add_keepout(
    project: Project,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    layer: str = "both",
    reason: str | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    mech = _mech(project)
    zone = KeepoutZone(
        id=_id("ko"), x=float(x), y=float(y), width=float(width),
        height=float(height), layer=layer, reason=reason,  # type: ignore[arg-type]
    )
    mech.keepouts.append(zone)
    return (
        {"keepout": zone.model_dump()},
        DiffEntry(kind="add", summary=f"Keep-out {width:.0f}×{height:.0f} mm at ({x:.0f}, {y:.0f})"),
    )


def delete_keepout(project: Project, *, id: str) -> tuple[dict[str, Any], DiffEntry]:
    mech = _mech(project)
    before = len(mech.keepouts)
    mech.keepouts = [z for z in mech.keepouts if z.id != id]
    if len(mech.keepouts) == before:
        raise MechToolError(f"No keep-out {id!r}.")
    return ({"deleted": id}, DiffEntry(kind="remove", summary=f"Removed keep-out {id}"))


# --------------------------------------------------------------------------- #
# Connector cutouts
# --------------------------------------------------------------------------- #
def add_connector_cutout(
    project: Project,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    edge: str = "none",
    refdes: str | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    """Add an enclosure-wall opening for a connector. If `refdes` is given it
    must be a real placed component (the anti-hallucination guard — no cutout for
    a connector that doesn't exist)."""
    if refdes is not None and not any(
        c.refdes == refdes for c in project.ir.components
    ):
        raise MechToolError(f"Component {refdes!r} is not in the schematic.")
    mech = _mech(project)
    cut = ConnectorCutout(
        id=_id("cut"), refdes=refdes, x=float(x), y=float(y),
        width=float(width), height=float(height), edge=edge,  # type: ignore[arg-type]
    )
    mech.cutouts.append(cut)
    return (
        {"cutout": cut.model_dump()},
        DiffEntry(
            kind="add",
            summary=f"Connector cutout {width:.0f}×{height:.0f} mm"
            + (f" for {refdes}" if refdes else ""),
            refdes=refdes,
        ),
    )


def delete_connector_cutout(
    project: Project, *, id: str
) -> tuple[dict[str, Any], DiffEntry]:
    mech = _mech(project)
    before = len(mech.cutouts)
    mech.cutouts = [c for c in mech.cutouts if c.id != id]
    if len(mech.cutouts) == before:
        raise MechToolError(f"No cutout {id!r}.")
    return ({"deleted": id}, DiffEntry(kind="remove", summary=f"Removed cutout {id}"))


# --------------------------------------------------------------------------- #
# Enclosure
# --------------------------------------------------------------------------- #
def set_enclosure(
    project: Project,
    *,
    kind: str = "box",
    wall_thickness: float | None = None,
    standoff_height: float | None = None,
    clearance_top: float | None = None,
    clearance_bottom: float | None = None,
    lip: float | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    mech = _mech(project)
    cur = mech.enclosure
    mech.enclosure = Enclosure(
        kind=kind,  # type: ignore[arg-type]
        wall_thickness=wall_thickness if wall_thickness is not None else cur.wall_thickness,
        standoff_height=standoff_height if standoff_height is not None else cur.standoff_height,
        clearance_top=clearance_top if clearance_top is not None else cur.clearance_top,
        clearance_bottom=clearance_bottom if clearance_bottom is not None else cur.clearance_bottom,
        lip=lip if lip is not None else cur.lip,
    )
    return (
        {"enclosure": mech.enclosure.model_dump()},
        DiffEntry(kind="modify", summary=f"Enclosure: {kind}"),
    )


# --------------------------------------------------------------------------- #
# Convenience: derive a sensible starting mechanical IR from the placed board
# --------------------------------------------------------------------------- #
def _outline_for_board(board) -> BoardOutlineMech:
    """The mechanical outline matching the electrical board's shape — a round
    PCB (smartwatch, badge) gets a true circle, everything else the standard
    rounded rectangle. Keeping this in one place is what stops a derive call
    from silently squaring off a round board."""
    if board.shape == "round":
        return BoardOutlineMech(
            shape="circle", width=board.width, height=board.width,
        )
    if board.shape == "polygon" and board.outline_points:
        # The enclosure is milled to the SAME ring the copper was placed and
        # routed against — a triangular board inside a rounded-rect case would
        # leave the case wrapping air on three sides.
        return BoardOutlineMech(
            shape="polygon", width=board.width, height=board.height,
            points=[Point(x=pt.x, y=pt.y) for pt in board.outline_points],
        )
    return BoardOutlineMech(
        shape="rounded_rect", width=board.width, height=board.height,
        corner_radius=3.0,
    )


def derive_from_board(project: Project) -> tuple[dict[str, Any], DiffEntry]:
    """Auto-generate a real starting enclosure from the placed board (Week-4 6.1):
    outline matched to the board, an M3 mounting hole inset at each corner, a box
    enclosure whose clearances are sized from the actual part heights, and a
    connector cutout for every edge-mounted port — located from where the
    connectors really sit. A one-call starting point the user/agent then refines.

    Real geometry (heights, cutouts) comes from `mechanical_derive`; all of it is
    written into the mechanical IR here so the change is a single revertable
    commit under the anti-hallucination contract."""
    board = project.board
    if board is None or not board.components:
        raise MechToolError("No placed board — run auto-place first.")
    mech = _mech(project)
    w, h = board.width, board.height
    mech.outline = _outline_for_board(board)
    # Corner M3 holes, nudged clear of part courtyards so a screw never lands on a
    # part (MECH_R04) — the auto-placer isn't mechanically aware and readily fills
    # the corners with passives. (Round boards get a bolt circle instead.)
    mech.mountingHoles = mechanical_derive.place_mounting_holes(project, w, h)

    # Clearances sized from the tallest real part on each side (fallback table in
    # mechanical_derive until per-part bodyHeightMm is curated), with sane floors
    # + a small assembly gap so the lid closes over the tallest component.
    clearance_top, clearance_bottom = mechanical_derive.derived_clearances(project)

    cur = mech.enclosure
    mech.enclosure = Enclosure(
        kind="box" if cur.kind == "none" else cur.kind,
        wall_thickness=cur.wall_thickness,
        standoff_height=cur.standoff_height,
        clearance_top=clearance_top,
        clearance_bottom=clearance_bottom,
        lip=cur.lip,
    )

    # Connector cutouts from real placed-port positions.
    mech.cutouts = mechanical_derive.derive_cutouts(project)
    n_cut = len(mech.cutouts)

    return (
        {"mechanical": mech.model_dump()},
        DiffEntry(
            kind="add",
            summary=(
                f"Derived mechanical: {w:.0f}×{h:.0f} mm outline, 4 M3 holes, "
                f"box enclosure (top clr {clearance_top:.1f} mm), "
                f"{n_cut} connector cutout{'s' if n_cut != 1 else ''}"
            ),
        ),
    )


def derive_mounting(project: Project) -> tuple[dict[str, Any], DiffEntry]:
    """Outline + collision-aware corner M3 holes from the placed board — the
    mechanical *references* a panel needs to bolt to whatever the user is
    building around it (a diffuser frame, a chassis), WITHOUT presuming an
    enclosure. Auto-place calls this for physical-grid boards (LED matrix /
    keypad) that have no mounting holes yet; `derive_from_board` remains the
    full enclosure derivation. Existing holes/enclosure settings are kept."""
    board = project.board
    if board is None or not board.components:
        raise MechToolError("No placed board — run auto-place first.")
    mech = _mech(project)
    w, h = board.width, board.height
    mech.outline = _outline_for_board(board)
    if not mech.mountingHoles:
        mech.mountingHoles = mechanical_derive.place_mounting_holes(project, w, h)
    return (
        {"outline": mech.outline.model_dump(), "mountingHoles": [m.model_dump() for m in mech.mountingHoles]},
        DiffEntry(
            kind="add",
            summary=(
                f"Derived mounting: {w:.0f}×{h:.0f} mm outline, "
                f"{len(mech.mountingHoles)} M3 holes"
            ),
        ),
    )


def set_panel(
    project: Project,
    *,
    rows: int = 1,
    cols: int = 1,
    method: str = "v_score",
    gap: float | None = None,
    rail: float | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    """Array this board onto a manufacturing panel.

    Fabs build and ship panels, and an assembly house needs one (or at least
    breakaway rails) to hold a small board in the pick-and-place machine at
    all. The geometry is DERIVED from the board, never stored, so a panel
    cannot go stale against a board that changed.
    """
    from app.services import panelize

    board = project.board
    if board is None or not board.components:
        raise MechToolError(
            "Panelizing needs a placed board — run auto-place first, then panelize."
        )
    if method not in ("v_score", "mouse_bites"):
        raise MechToolError(
            f"Unknown separation method {method!r}. Use 'v_score' (straight blade, "
            f"rectangular boards only) or 'mouse_bites' (routed tabs, any outline)."
        )
    if method == "v_score" and board.shape != "rect":
        raise MechToolError(
            f"A {board.shape} board cannot be V-scored — the blade cuts a straight line "
            f"across the whole panel. Use method='mouse_bites'."
        )

    mech = _mech(project)
    cur = mech.panel
    try:
        spec = PanelSpec(
            rows=rows, cols=cols, method=method,  # type: ignore[arg-type]
            gap=gap if gap is not None else (cur.gap if cur else 0.0),
            rail=rail if rail is not None else (cur.rail if cur else 5.0),
        )
    except ValidationError as e:
        raise MechToolError(f"Invalid panel: {e}") from e

    panel = panelize.derive(board, spec)
    if panel.width > panelize.MAX_PANEL_W or panel.height > panelize.MAX_PANEL_H:
        raise MechToolError(
            f"{cols} x {rows} of this board makes a {panel.width:.0f} x {panel.height:.0f} mm "
            f"panel, past the {panelize.MAX_PANEL_W:.0f} x {panelize.MAX_PANEL_H:.0f} mm most "
            f"fabs accept. Try a smaller array."
        )

    mech.panel = spec
    return (
        {
            "panel": spec.model_dump(),
            "boards_per_panel": panel.boards,
            "panel_size_mm": [panel.width, panel.height],
            "utilisation_pct": round(panel.utilisation * 100),
        },
        DiffEntry(
            kind="modify",
            summary=(f"Panel: {cols} x {rows} = {panel.boards} up, "
                     f"{panel.width:.0f} x {panel.height:.0f} mm, "
                     f"{'V-score' if method == 'v_score' else 'mouse bites'}"),
        ),
    )


def derive_panel(project: Project) -> tuple[dict[str, Any], DiffEntry]:
    """Pick a sensible panel for this board and record it.

    Targets the fab's cheapest quote tier (a 100 x 100 mm panel) rather than
    the largest array that physically fits: someone ordering prototypes wants
    the boards that come free inside the flat-rate square, not 144-up.
    """
    from app.services import panelize

    board = project.board
    if board is None or not board.components:
        raise MechToolError(
            "Panelizing needs a placed board — run auto-place first, then panelize."
        )
    spec = panelize.suggest(board)
    if spec is None:
        raise MechToolError(
            f"This board is {board.width:.0f} x {board.height:.0f} mm — it already fills a "
            f"standard panel, so arraying it would not fit more boards. Order it one-up."
        )
    return set_panel(project, rows=spec.rows, cols=spec.cols,
                     method=spec.method, gap=spec.gap, rail=spec.rail)


def clear_panel(project: Project) -> tuple[dict[str, Any], DiffEntry]:
    """Go back to ordering one board at a time."""
    mech = _mech(project)
    mech.panel = None
    return ({"panel": None}, DiffEntry(kind="modify", summary="Panel removed (one-up)"))


MECHANICAL_TOOLS = {
    "set_board_outline": set_board_outline,
    "add_mounting_hole": add_mounting_hole,
    "delete_mounting_hole": delete_mounting_hole,
    "add_keepout": add_keepout,
    "delete_keepout": delete_keepout,
    "add_connector_cutout": add_connector_cutout,
    "delete_connector_cutout": delete_connector_cutout,
    "set_enclosure": set_enclosure,
    "derive_from_board": derive_from_board,
    "derive_mounting": derive_mounting,
    "set_panel": set_panel,
    "derive_panel": derive_panel,
    "clear_panel": clear_panel,
}


def dispatch(project: Project, name: str, args: dict) -> tuple[dict[str, Any], DiffEntry]:
    fn = MECHANICAL_TOOLS.get(name)
    if fn is None:
        raise MechToolError(f"Unknown mechanical tool {name!r}.")
    try:
        return fn(project, **args)
    except (TypeError, ValueError) as e:  # malformed model call — back to the model, not a crash
        raise MechToolError(f"Bad arguments for {name}: {e}") from e
