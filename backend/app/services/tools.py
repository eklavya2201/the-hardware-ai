"""Agent tool implementations — see PRD §9.3.

Each tool receives the current Project IR, mutates it in-place, and returns a
result dict + a DiffEntry summarizing the change. The Claude API exposes these
to the model via tool_use; the same set is callable from the stub agent so
both code paths exercise the same surface.

Implemented tools (M1 picks 8 of the 11 in the PRD):
  add_component, delete_component, connect, disconnect,
  set_value, set_property, search_parts, run_drc

estimate_bom_cost is computed automatically server-side; read_datasheet is a
stub for M3 (no PDF index yet); simulate is M3 (ngspice not in scope).
"""

from __future__ import annotations

import re
import time
from typing import Any

import math

from app.models import (
    Acknowledgement,
    BomLine,
    Component,
    ComponentArray,
    DesignRequirement,
    DiffEntry,
    FormFactorSpec,
    Net,
    Pin,
    Point,
    Project,
)
from app.services import board_shape, demand_log, library, pin_safety, verifier


class ToolError(Exception):
    """Raised when a tool call cannot be applied — surfaces back to the agent."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _next_refdes(project: Project, prefix: str) -> str:
    used = {c.refdes for c in project.ir.components}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


def _refdes_prefix_for(category: str) -> str:
    return {
        "mcu": "U",
        "regulator": "U",
        "charger": "U",
        "sensor": "U",
        "transistor": "Q",
        "led": "D",
        "diode": "D",
        "passive": "R",  # overridden below for caps/inductors
        "switch": "SW",
        "connector": "J",
        "crystal": "Y",
        "protection": "F",
        "mechanical": "MH",
    }.get(category, "U")


def _refdes_prefix_for_part(part) -> str:
    if part.category == "passive":
        symbol = part.symbol.lower()
        if "cap" in symbol or symbol.endswith(":c"):
            return "C"
        if "ind" in symbol or symbol.endswith(":l"):
            return "L"
        return "R"
    return _refdes_prefix_for(part.category)


# -- Tools -------------------------------------------------------------------


def add_component(
    project: Project,
    *,
    mpn: str,
    refdes: str | None = None,
    value: str | None = None,
    x: float | None = None,
    y: float | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    part = library.get_part(mpn)
    if part is None:
        raise ToolError(
            f"MPN {mpn!r} is not in the curated library. "
            f"Call search_parts first to find a real MPN."
        )

    if refdes is None:
        refdes = _next_refdes(project, _refdes_prefix_for_part(part))
    if any(c.refdes == refdes for c in project.ir.components):
        raise ToolError(f"refdes {refdes!r} already exists.")

    placed_x = x if x is not None else _auto_x(project)
    placed_y = y if y is not None else _auto_y(project)

    component = Component(
        refdes=refdes,
        mpn=mpn,
        value=value,
        footprint=part.footprint,
        datasheetUrl=part.datasheetUrl,
        x=placed_x,
        y=placed_y,
        pins=[Pin(**p.model_dump()) for p in part.pins],
    )
    project.ir.components.append(component)
    # Return the full pin list so the agent knows what to wire to. Without this
    # it has to guess pin numbers/names and most connects fail.
    return (
        {
            "refdes": refdes,
            "mpn": mpn,
            "x": placed_x,
            "y": placed_y,
            "pin_count": len(part.pins),
            "pins": [
                {"number": p.number, "name": p.name, "type": p.type}
                for p in part.pins
            ],
            # Review #39 (2026-08-26): the agent called an LDO's output capacitor
            # "optional" (the datasheet says 1 uF minimum) and left a sensor's
            # PS0/PS1 mode pins floating, while the library entry for each part
            # already said exactly what to do. The agent never saw it — only the
            # pin list came back. Hand it the curated application note and the
            # parts the entry says must sit beside it.
            "applicationNote": (part.typicalApplication or None),
            "requiredCompanions": [
                {"mpn": c.mpn, "role": c.role, "pin": c.pin,
                 "quantity": c.quantity, "notes": c.notes}
                for c in (part.companions or [])
            ] or None,
        },
        DiffEntry(
            kind="add",
            summary=f"Added <b>{refdes}</b> ({part.description})",
            refdes=refdes,
        ),
    )


# Sanity ceiling for one array call. Far above any real board (a 16x16 matrix
# is 256) but low enough that a confused model can't allocate 10^6 parts.
MAX_ARRAY_COUNT = 400


def default_grid_pitch_mm(part) -> float:
    """Smallest sane PCB pitch for a physical grid of `part`: its verified
    courtyard plus body clearance, rounded up to a 0.5 mm step. A caller that
    knows the product (diffuser cubes, keycaps) should pass an explicit pitch."""
    from app.services import footprints  # lazy: footprints imports library

    fp = footprints.for_part(part)
    extent = max(fp.courtyard_w or fp.width, fp.courtyard_h or fp.height, 1.0)
    return math.ceil((extent + 1.0) * 2) / 2


def add_component_array(
    project: Project,
    *,
    mpn: str,
    count: int,
    rows: int | None = None,
    cols: int | None = None,
    value: str | None = None,
    start_x: float | None = None,
    start_y: float | None = None,
    pitch_x: float = 200.0,
    pitch_y: float = 200.0,
    pitch_mm: float | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    """Place `count` copies of one part in a rows x cols grid with sequential
    refdeses (D1..D100). This is the repeat primitive that makes matrix/array
    designs (LED matrices, keypads, connector banks) fit in the agent's turn
    budget — without it, 100 parts cost 100 add_component calls.

    Physical intent: when the caller gives rows/cols or a `pitch_mm`, the block
    is recorded as a PCB grid (`ComponentArray.grid=True`) — the auto-placer
    then pins the parts at that pitch and sizes the board from the grid. A bare
    `count` is a convenience batch that places freely."""
    part = library.get_part(mpn)
    if part is None:
        raise ToolError(
            f"MPN {mpn!r} is not in the curated library. "
            f"Call search_parts first to find a real MPN."
        )
    if count < 2:
        raise ToolError("add_component_array needs count >= 2; use add_component for one part.")
    if count > MAX_ARRAY_COUNT:
        raise ToolError(f"count {count} exceeds the {MAX_ARRAY_COUNT}-part array ceiling.")
    if rows is not None and cols is not None and rows * cols < count:
        raise ToolError(f"rows*cols ({rows}x{cols}) is smaller than count ({count}).")
    if pitch_mm is not None and pitch_mm <= 0:
        raise ToolError("pitch_mm must be positive.")

    physical = rows is not None or cols is not None or pitch_mm is not None
    if cols is None:
        cols = math.ceil(count / rows) if rows else math.ceil(math.sqrt(count))
    if rows is None:
        rows = math.ceil(count / cols)

    min_pitch = default_grid_pitch_mm(part)
    if physical:
        if pitch_mm is None:
            pitch_mm = min_pitch
        elif pitch_mm < min_pitch:
            raise ToolError(
                f"pitch_mm {pitch_mm} is smaller than the part's footprint allows "
                f"(min {min_pitch} mm for {mpn}) — bodies would overlap."
            )

    prefix = _refdes_prefix_for_part(part)
    # Sequential block: find the first index where `count` consecutive refdeses
    # are free, so D1..D100 stay contiguous (connect_matrix depends on that).
    used = {c.refdes for c in project.ir.components}
    start_index = 1
    while any(f"{prefix}{start_index + i}" in used for i in range(count)):
        start_index += 1

    base_x = start_x if start_x is not None else _auto_x(project)
    base_y = start_y if start_y is not None else _auto_y(project)

    refdeses: list[str] = []
    for i in range(count):
        r, c = divmod(i, cols)
        refdes = f"{prefix}{start_index + i}"
        project.ir.components.append(
            Component(
                refdes=refdes,
                mpn=mpn,
                value=value,
                footprint=part.footprint,
                datasheetUrl=part.datasheetUrl,
                x=base_x + c * pitch_x,
                y=base_y + r * pitch_y,
                pins=[Pin(**p.model_dump()) for p in part.pins],
            )
        )
        refdeses.append(refdes)

    array_key = f"{refdeses[0]}-{refdeses[-1]}"
    project.ir.arrays.append(
        ComponentArray(
            key=array_key, mpn=mpn, refdeses=refdeses, rows=rows, cols=cols,
            grid=physical, pitch_mm=pitch_mm if physical else None,
        )
    )

    result: dict[str, Any] = {
        "mpn": mpn,
        "count": count,
        "rows": rows,
        "cols": cols,
        "refdes_prefix": prefix,
        "start_index": start_index,
        "refdeses_first": refdeses[0],
        "refdeses_last": refdeses[-1],
        "array_key": array_key,
        "order": "row-major (refdes index = start + row*cols + col)",
        "pin_count": len(part.pins),
        "pins": [
            {"number": p.number, "name": p.name, "type": p.type}
            for p in part.pins
        ],
    }
    if physical:
        result["pcb_grid"] = {
            "pitch_mm": pitch_mm,
            "extent_mm": [round((cols - 1) * pitch_mm, 2), round((rows - 1) * pitch_mm, 2)],
            "note": (
                "The PCB auto-placer will pin these parts in this grid at this "
                "pitch and size the board around it. Tell the user the pitch; "
                "call set_array_pitch to change it."
            ),
        }
    else:
        result["pcb_grid"] = None
    grid_note = f", {rows}x{cols} grid @ {pitch_mm} mm" if physical else ""
    return (
        result,
        DiffEntry(
            kind="add",
            summary=(
                f"Added <b>{refdeses[0]}–{refdeses[-1]}</b> "
                f"({count}x {part.description}{grid_note})"
            ),
            refdes=refdeses[0],
        ),
    )


def arrange_grid(
    project: Project,
    *,
    refdeses: list[str],
    rows: int | None = None,
    cols: int | None = None,
    pitch_mm: float | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    """Declare EXISTING parts a physical rows x cols grid on the PCB (row-major
    in the order given) — e.g. three template-built IR sensors as a front row,
    or connectors as an aligned bank. Records a ComponentArray exactly like
    add_component_array does, so the auto-placer pins them at `pitch_mm` and
    sizes the board around them. A part already in a physical grid is moved to
    this one."""
    if not refdeses or len(refdeses) < 2:
        raise ToolError("arrange_grid needs at least 2 refdeses.")
    if len(set(refdeses)) != len(refdeses):
        raise ToolError("arrange_grid: duplicate refdes in the list.")
    comps = {c.refdes: c for c in project.ir.components}
    missing = [r for r in refdeses if r not in comps]
    if missing:
        raise ToolError(f"arrange_grid: unknown refdes {', '.join(missing)}.")
    n = len(refdeses)
    if rows is not None and cols is not None and rows * cols < n:
        raise ToolError(f"rows*cols ({rows}x{cols}) is smaller than the {n} parts.")
    if cols is None:
        cols = math.ceil(n / rows) if rows else n  # default: one straight row
    if rows is None:
        rows = math.ceil(n / cols)
    # Pitch floor: the largest member must fit.
    min_pitch = 0.0
    for r in refdeses:
        part = library.get_part(comps[r].mpn)
        if part:
            min_pitch = max(min_pitch, default_grid_pitch_mm(part))
    if pitch_mm is None:
        pitch_mm = min_pitch
    elif pitch_mm < min_pitch:
        raise ToolError(
            f"pitch_mm {pitch_mm} is smaller than the largest member allows "
            f"(min {min_pitch} mm) — bodies would overlap."
        )
    # Drop these refdeses from any other physical grid.
    for arr in project.ir.arrays:
        if arr.grid and any(r in arr.refdeses for r in refdeses):
            arr.refdeses = [r for r in arr.refdeses if r not in refdeses]
    project.ir.arrays = [a for a in project.ir.arrays if a.refdeses]
    key = f"{refdeses[0]}-{refdeses[-1]}"
    project.ir.arrays.append(
        ComponentArray(
            key=key, mpn=comps[refdeses[0]].mpn, refdeses=list(refdeses),
            rows=rows, cols=cols, grid=True, pitch_mm=pitch_mm,
        )
    )
    return (
        {
            "array_key": key, "rows": rows, "cols": cols, "pitch_mm": pitch_mm,
            "extent_mm": [round((cols - 1) * pitch_mm, 2), round((rows - 1) * pitch_mm, 2)],
            "note": "The auto-placer pins these parts in this grid; re-run auto-place to see it.",
        },
        DiffEntry(
            kind="modify",
            summary=f"Arranged <b>{key}</b> as a {rows}x{cols} grid @ {pitch_mm} mm",
        ),
    )


def set_array_pitch(
    project: Project, *, array_key: str, pitch_mm: float
) -> tuple[dict[str, Any], DiffEntry]:
    """Change the PCB centre-to-centre pitch of a physical grid array (e.g. to
    match a diffuser cube / keycap size). Re-run auto-place to apply."""
    arr = next((a for a in project.ir.arrays if a.key == array_key), None)
    if arr is None:
        keys = ", ".join(a.key for a in project.ir.arrays) or "none"
        raise ToolError(f"No array {array_key!r}. Known arrays: {keys}")
    if pitch_mm <= 0:
        raise ToolError("pitch_mm must be positive.")
    part = library.get_part(arr.mpn)
    min_pitch = default_grid_pitch_mm(part) if part else 0.0
    if pitch_mm < min_pitch:
        raise ToolError(
            f"pitch_mm {pitch_mm} is below the footprint minimum ({min_pitch} mm "
            f"for {arr.mpn}) — bodies would overlap."
        )
    old = arr.pitch_mm
    arr.pitch_mm = pitch_mm
    arr.grid = True
    return (
        {
            "array_key": array_key,
            "old_pitch_mm": old,
            "pitch_mm": pitch_mm,
            "extent_mm": [
                round((arr.cols - 1) * pitch_mm, 2), round((arr.rows - 1) * pitch_mm, 2)
            ],
            "note": "Re-run auto-place for the board to pick up the new pitch.",
        },
        DiffEntry(
            kind="modify",
            summary=f"Array <b>{array_key}</b> pitch: {old or '∅'} → {pitch_mm} mm",
        ),
    )


def connect_matrix(
    project: Project,
    *,
    refdes_prefix: str,
    rows: int,
    cols: int,
    row_pin: str,
    col_pin: str,
    start_index: int = 1,
    row_net_prefix: str = "ROW",
    col_net_prefix: str = "COL",
) -> tuple[dict[str, Any], DiffEntry]:
    """Wire a rows x cols block of identical parts into a scanned matrix: part
    at (r, c) gets `row_pin` on net ROW<r> and `col_pin` on net COL<c>. Refdes
    order is row-major from `start_index` (the add_component_array layout). One
    call replaces rows*cols*2 individual connect operations."""
    if rows < 1 or cols < 1:
        raise ToolError("rows and cols must both be >= 1.")
    if rows * cols > MAX_ARRAY_COUNT:
        raise ToolError(f"{rows}x{cols} exceeds the {MAX_ARRAY_COUNT}-part matrix ceiling.")

    # Validate every refdes + resolve both pins up front so the wiring is
    # all-or-nothing — a half-wired matrix is worse than a loud failure.
    resolved: list[tuple[str, str, str, int, int]] = []  # refdes, row_pin#, col_pin#, r, c
    for r in range(rows):
        for c in range(cols):
            refdes = f"{refdes_prefix}{start_index + r * cols + c}"
            comp = next(
                (x for x in project.ir.components if x.refdes == refdes), None
            )
            if comp is None:
                raise ToolError(
                    f"Component {refdes!r} does not exist — expected a contiguous "
                    f"row-major block {refdes_prefix}{start_index}.."
                    f"{refdes_prefix}{start_index + rows * cols - 1} "
                    f"(add_component_array creates one)."
                )
            resolved.append(
                (refdes, _resolve_pin(comp, row_pin), _resolve_pin(comp, col_pin), r, c)
            )

    row_nets = [f"{row_net_prefix}{r}" for r in range(rows)]
    col_nets = [f"{col_net_prefix}{c}" for c in range(cols)]
    for refdes, rp, cp, r, c in resolved:
        connect(project, net_name=row_nets[r], pins=[[refdes, rp]])
        connect(project, net_name=col_nets[c], pins=[[refdes, cp]])

    return (
        {
            "wired": len(resolved),
            "row_nets": row_nets,
            "col_nets": col_nets,
            "note": (
                "Each row/col net still needs its driver (transistor/shift "
                "register/driver IC) and the columns need current limiting — "
                "wire those to the MCU next."
            ),
        },
        DiffEntry(
            kind="modify",
            summary=(
                f"Wired <b>{len(resolved)}</b> parts into a {rows}x{cols} matrix "
                f"({row_nets[0]}–{row_nets[-1]} / {col_nets[0]}–{col_nets[-1]})"
            ),
        ),
    )


_REQUIREMENT_CATEGORIES = {
    "mcu", "regulator", "charger", "sensor", "passive", "led", "switch",
    "transistor", "diode", "connector", "crystal", "protection", "mechanical",
    "display", "actuator", "memory", "interface", "audio", "logic",
}

# Requirement descriptions that re-word the ask into whatever got built.
_SUBSTITUTION_WORDS = re.compile(
    r"\b(replac\w*|substitut\w*|instead of|in place of|in lieu of|placeholder|"
    r"stand-?in|proxy for|approximat\w+ (?:by|with)|represented by|emulat\w+)\b",
    re.IGNORECASE,
)


def record_requirements(
    project: Project, *, items: list[dict]
) -> tuple[dict[str, Any], DiffEntry]:
    """Record the user's countable requirements as structured data (replaces the
    previous list). Verifier rule R43 compares them against the built IR, so a
    design that doesn't contain what was asked for is a hard error — the
    anti-"generic board sold as a matrix" guard. Only record quantities the
    user actually stated or directly implied (10x10 => 100)."""
    if not items:
        raise ToolError("record_requirements needs at least one item.")
    reqs: list[DesignRequirement] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ToolError(f"items[{i}] must be an object.")
        desc = str(item.get("description") or "").strip()
        if not desc:
            raise ToolError(f"items[{i}] needs a description.")
        try:
            qty = int(item.get("quantity", 1))
        except (TypeError, ValueError):
            raise ToolError(f"items[{i}].quantity must be an integer.") from None
        if qty < 1:
            raise ToolError(f"items[{i}].quantity must be >= 1.")
        mpn = (item.get("mpn") or "").strip() or None
        category = (item.get("category") or "").strip().lower() or None
        unavailable = bool(item.get("unavailable", False))
        # A requirement describes what the USER asked for. Review #8: the agent
        # recorded "7-segment display replaced by single LED indicator" with the
        # LED's mpn, which satisfied R43 while the display was missing. Refuse
        # substitution language outright - the honest path is unavailable=true.
        if _SUBSTITUTION_WORDS.search(desc):
            raise ToolError(
                f"items[{i}] describes a substitution ({desc!r}). Requirements must "
                f"state what the USER asked for. If the curated library has no such "
                f"part, record the ORIGINAL ask with unavailable=true (no mpn) so the "
                f"design is flagged incomplete, and tell the user - never rewrite the "
                f"requirement to match a stand-in part."
            )
        if unavailable:
            if mpn:
                raise ToolError(
                    f"items[{i}] is unavailable=true but names mpn {mpn!r} - an "
                    f"unavailable requirement has no library part; drop the mpn."
                )
            key = str(item.get("key") or "").strip() or f"unavailable_{i}"
            reqs.append(
                DesignRequirement(
                    key=key, description=desc, quantity=qty, mpn=None,
                    category=category if category in _REQUIREMENT_CATEGORIES else None,
                    unavailable=True,
                )
            )
            continue
        if mpn and library.get_part(mpn) is None:
            raise ToolError(
                f"items[{i}].mpn {mpn!r} is not in the curated library — "
                f"use search_parts to find the real MPN, or record the "
                f"category instead."
            )
        if category and category not in _REQUIREMENT_CATEGORIES:
            raise ToolError(
                f"items[{i}].category {category!r} is not a library category. "
                f"Valid: {', '.join(sorted(_REQUIREMENT_CATEGORIES))}"
            )
        if not mpn and not category:
            raise ToolError(f"items[{i}] needs an mpn or a category to be checkable.")
        key = str(item.get("key") or "").strip() or (
            (mpn or category or f"req{i}").lower().replace(" ", "_")
        )
        reqs.append(
            DesignRequirement(
                key=key, description=desc, quantity=qty, mpn=mpn, category=category
            )
        )

    project.ir.requirements = reqs
    listing = ", ".join(
        f"{r.quantity}x {r.description}" + (" (NOT IN LIBRARY)" if r.unavailable else "")
        for r in reqs
    )
    missing = [r for r in reqs if r.unavailable]
    note = "run_drc will fail (R43) until the design contains these quantities."
    if missing:
        note += (
            " " + "; ".join(f"{r.description!r} is not in the curated library" for r in missing)
            + " - R43 keeps the design INCOMPLETE (not ready) until such a part is curated; "
            "say so plainly to the user and do not present a stand-in as the requested part."
        )
    return (
        {
            "recorded": len(reqs),
            "requirements": [r.model_dump() for r in reqs],
            "note": note,
        },
        DiffEntry(kind="modify", summary=f"Recorded requirements: {listing}"),
    )


# Sanity bounds for a requested board dimension (mm). Below 10 mm nothing
# fits a connector; above 500 mm it's outside every fab's standard service.
_FORM_DIM_MIN = 10.0
_FORM_DIM_MAX = 500.0


def set_form_factor(
    project: Project,
    *,
    shape: str,
    diameter_mm: float | None = None,
    width_mm: float | None = None,
    height_mm: float | None = None,
    note: str = "",
    layers: int | None = None,
    sides: int | None = None,
    points: list | None = None,
) -> tuple[dict[str, Any], DiffEntry]:
    """Record the user's requested board form (shape + optional dimensions).
    The auto-placer honours it and verifier rule R57 compares the built board
    against it — the form-factor sibling of record_requirements/R43, so a
    round-watch ask can never quietly ship as a generic rectangle.

    shape="polygon" takes EITHER `sides` (a regular n-gon inscribed in
    diameter_mm — 3 for the triangular drone board someone pictures) OR an
    explicit `points` outline for a shape no n-gon describes, such as the X of
    a quad frame."""
    shape = (shape or "").strip().lower()
    if shape not in {"rect", "round", "polygon"}:
        raise ToolError("shape must be 'rect', 'round' or 'polygon'.")

    def _dim(name: str, val: float | None) -> float | None:
        if val is None:
            return None
        try:
            v = float(val)
        except (TypeError, ValueError):
            raise ToolError(f"{name} must be a number (mm).") from None
        if not (_FORM_DIM_MIN <= v <= _FORM_DIM_MAX):
            raise ToolError(
                f"{name} {v:g} mm is outside the sane range "
                f"({_FORM_DIM_MIN:g}-{_FORM_DIM_MAX:g} mm)."
            )
        return round(v, 2)

    diameter = _dim("diameter_mm", diameter_mm)
    width = _dim("width_mm", width_mm)
    height = _dim("height_mm", height_mm)
    if shape == "round":
        if width is not None or height is not None:
            raise ToolError("a round board takes diameter_mm, not width/height.")
    elif shape == "polygon":
        if width is not None or height is not None:
            raise ToolError(
                "a polygon board is sized by diameter_mm (the circle it fits "
                "inside), not width/height."
            )
    else:
        if diameter is not None:
            raise ToolError("a rect board takes width_mm/height_mm, not diameter_mm.")

    # --- polygon validation ------------------------------------------------
    # Refuse a shape we cannot build rather than accept it and quietly ship a
    # rectangle: an unbuildable ask must be an error the user sees, which is
    # the same contract record_requirements has for an unavailable part.
    ring_pts: list[Point] = []
    n_sides: int | None = None
    if shape == "polygon":
        if sides is not None and points:
            raise ToolError("give EITHER sides (a regular n-gon) OR points, not both.")
        if sides is not None:
            try:
                n_sides = int(sides)
            except (TypeError, ValueError):
                raise ToolError("sides must be a whole number.") from None
            if not (3 <= n_sides <= 12):
                raise ToolError(
                    "sides must be 3-12 — past a dodecagon a board is a circle, "
                    "and shape='round' routes and mills better."
                )
            if diameter is None:
                raise ToolError(
                    "a regular polygon needs diameter_mm (the circle it fits inside)."
                )
        elif points:
            raw = []
            for i, pt in enumerate(points):
                try:
                    if isinstance(pt, dict):
                        x, y = float(pt["x"]), float(pt["y"])
                    else:
                        x, y = float(pt[0]), float(pt[1])
                except (TypeError, ValueError, KeyError, IndexError):
                    raise ToolError(
                        f"point {i} must be [x, y] or {{'x': .., 'y': ..}} in mm."
                    ) from None
                raw.append((x, y))
            ring = board_shape.normalize(raw)
            if len(ring) < 3:
                raise ToolError("a polygon outline needs at least 3 distinct points.")
            if len(ring) > 64:
                raise ToolError("a polygon outline is capped at 64 points.")
            x0, y0, x1, y1 = board_shape.bbox(ring)
            span = max(x1 - x0, y1 - y0)
            if not (_FORM_DIM_MIN <= span <= _FORM_DIM_MAX):
                raise ToolError(
                    f"the outline spans {span:g} mm, outside the sane range "
                    f"({_FORM_DIM_MIN:g}-{_FORM_DIM_MAX:g} mm)."
                )
            if board_shape.area(ring) < 100.0:
                raise ToolError(
                    "that outline encloses almost no area — check the point order "
                    "(it must trace the edge, not zig-zag across the board)."
                )
            ring_pts = [Point(x=round(x, 3), y=round(y, 3))
                        for x, y in board_shape.translate_to_origin(ring)]
        else:
            raise ToolError("a polygon board needs either sides or points.")

    if layers is not None:
        try:
            layers = int(layers)
        except (TypeError, ValueError):
            raise ToolError("layers must be 2 or 4.") from None
        if layers not in (2, 4):
            raise ToolError("layers must be 2 or 4 (Synqit builds 2-layer or 4-layer boards).")
    project.ir.form_factor = FormFactorSpec(
        shape=shape, diameter_mm=diameter, width_mm=width, height_mm=height,
        note=str(note or "").strip(), layers=layers,  # type: ignore[arg-type]
        sides=n_sides, points=ring_pts,
    )
    if shape == "round":
        desc = "round" + (f" Ø{diameter:g} mm" if diameter else " (auto diameter)")
    elif shape == "polygon":
        if n_sides:
            named = {3: "triangular", 5: "pentagonal", 6: "hexagonal",
                     8: "octagonal"}.get(n_sides, f"{n_sides}-sided")
            desc = f"{named} Ø{diameter:g} mm"
        else:
            x0, y0, x1, y1 = board_shape.bbox([(p.x, p.y) for p in ring_pts])
            desc = f"custom {len(ring_pts)}-point outline, {x1 - x0:g}x{y1 - y0:g} mm"
    else:
        if width and height:
            desc = f"rect {width:g}x{height:g} mm"
        elif width or height:
            desc = f"rect ({'w' if width else 'h'}={ (width or height):g} mm, other auto)"
        else:
            desc = "rect (auto size)"
    if layers:
        desc += f", {layers}-layer"
    return (
        {
            "form_factor": project.ir.form_factor.model_dump(),
            "note": (
                "auto-place will build the board to this form; DRC rule R57 "
                "reports any deviation (e.g. parts that physically can't fit "
                "the requested size)."
            ),
        },
        DiffEntry(kind="modify", summary=f"Set board form factor: {desc}"),
    )


def delete_component(
    project: Project, *, refdes: str
) -> tuple[dict[str, Any], DiffEntry]:
    before = len(project.ir.components)
    project.ir.components = [c for c in project.ir.components if c.refdes != refdes]
    if len(project.ir.components) == before:
        raise ToolError(f"refdes {refdes!r} not found.")
    # Strip the refdes from any nets it appeared in.
    for net in project.ir.nets:
        net.pins = [p for p in net.pins if p[0] != refdes]
    project.ir.nets = [n for n in project.ir.nets if n.pins]
    # Arrays keep their slot list (row-major index) so a removed member simply
    # leaves a gap in the grid; an array with no surviving member disappears.
    alive = {c.refdes for c in project.ir.components}
    project.ir.arrays = [a for a in project.ir.arrays if any(r in alive for r in a.refdeses)]
    return (
        {"refdes": refdes},
        DiffEntry(kind="remove", summary=f"Removed <b>{refdes}</b>", refdes=refdes),
    )


_POWER_NET_PREFIXES = ("VCC", "VDD", "VBUS", "VBAT", "+3V", "+5V", "3V3", "5V")
_GND_NET_NAMES = {"GND", "VSS", "AGND", "DGND", "0V"}


def _net_role(name: str) -> str:
    """Classify a net as 'power', 'gnd', or 'signal'. Used by rail-conflict
    prevention so we can refuse pin moves that would short the rails."""
    upper = name.upper()
    if upper in _GND_NET_NAMES:
        return "gnd"
    if any(upper.startswith(p) for p in _POWER_NET_PREFIXES):
        return "power"
    return "signal"


def _resolve_pin(comp, pin_ident: str) -> str:
    """Resolve a pin identifier (number, name, or common alias) to the canonical
    pin number for storage. Raises if not found.

    The agent often supplies pin *names* like "GND", "VDD", "SDA" — but the
    verifier and exporter look up pins by *number*. We canonicalize at write
    time so storage is uniform."""
    if not comp.pins:
        # Fix B (PIN_QUALITY_ANALYSIS §2 — "connect() silently accepts a pin
        # that doesn't exist"). A pinless component used to fall through here and
        # return the raw identifier, writing a phantom (refdes, pin) onto the net
        # that maps to no rendered handle — a silent broken connection. Mechanical
        # parts (mounting holes, heatsinks) genuinely have no pins and are
        # legitimately tied to GND, so trust the caller *only* for those; any
        # electrical part with no pin metadata is a data gap we must fail loud on.
        part = library.get_part(comp.mpn)
        if part is not None and part.category == "mechanical":
            return pin_ident
        raise ToolError(
            f"{comp.refdes} ({comp.mpn}) has no pin metadata, so pin "
            f"{pin_ident!r} cannot be resolved. Its pins are missing from the "
            f"library entry — pick a fully-defined part rather than wiring a "
            f"pin that doesn't exist."
        )

    ident = pin_ident.strip()
    # 1. Exact pin-number match (the canonical case).
    for p in comp.pins:
        if p.number == ident:
            return p.number
    # 2. Exact pin-name match (case-insensitive).
    upper = ident.upper()
    for p in comp.pins:
        if p.name.upper() == upper:
            return p.number
    # 3. Common ground/power aliases.
    aliases = {
        "GND": ("GND", "VSS", "AGND", "DGND", "0V"),
        "VSS": ("GND", "VSS", "AGND", "DGND", "0V"),
        "VCC": ("VCC", "VDD", "VDDIO", "+VCC", "3V3", "VBAT"),
        "VDD": ("VCC", "VDD", "VDDIO", "+VCC", "3V3", "VBAT"),
        "SDA": ("SDA", "SDI/SDA", "SDI", "I2C_SDA"),
        "SCL": ("SCL", "SCK/SCL", "SCK", "I2C_SCL"),
        "DP": ("DP", "D+", "USB_DP"),
        "DN": ("DN", "D-", "USB_DN", "DM"),
    }.get(upper, ())
    for p in comp.pins:
        if p.name.upper() in {a.upper() for a in aliases}:
            return p.number
    # 4. Substring match (handles "SDI/SDA" when caller passed "SDA"). Only
    # accept an *unambiguous* hit: if the fragment matches more than one distinct
    # pin, guessing would silently bind the wire to the wrong physical pin
    # (PIN_QUALITY_ANALYSIS §3 — "wrong pin connected"), so surface the ambiguity
    # instead of picking the first match.
    substring_hits: list = []
    seen_numbers: set[str] = set()
    for p in comp.pins:
        if upper in p.name.upper() and p.number not in seen_numbers:
            substring_hits.append(p)
            seen_numbers.add(p.number)
    if len(substring_hits) == 1:
        return substring_hits[0].number
    if len(substring_hits) > 1:
        cands = ", ".join(f"{p.number}({p.name})" for p in substring_hits)
        raise ToolError(
            f"Pin {pin_ident!r} is ambiguous on {comp.refdes} ({comp.mpn}) — it "
            f"matches {cands}. Use an exact pin number or name to disambiguate."
        )

    available = ", ".join(f"{p.number}({p.name})" for p in comp.pins[:20])
    raise ToolError(
        f"Pin {pin_ident!r} not on {comp.refdes} ({comp.mpn}). "
        f"Available pins: {available}"
    )


def _validated_pairs(pins, tool: str) -> list[tuple[str, str]]:
    """Coerce the model's pin list into [refdes, pin] pairs, or fail with a
    message it can act on. A three-element entry (`["U2","AO1","J1"]` — the
    model trying to wire two parts in one pair) used to raise a bare ValueError
    from tuple unpacking, which escaped the tool layer and killed the whole
    streaming turn (seen live: an entire rover build lost)."""
    out: list[tuple[str, str]] = []
    for i, pair in enumerate(pins):
        if isinstance(pair, str) or not hasattr(pair, "__len__"):
            raise ToolError(
                f"{tool}: pins[{i}] must be a [refdes, pin] pair, got {pair!r}."
            )
        if len(pair) != 2:
            raise ToolError(
                f"{tool}: pins[{i}] has {len(pair)} entries ({list(pair)!r}) but a pair is "
                f"exactly [refdes, pin]. To join several pins, list each as its own pair: "
                f'[["U2","AO1"],["J1","1"]].'
            )
        out.append((str(pair[0]), str(pair[1])))
    return out


def connect(
    project: Project, *, net_name: str, pins: list[list[str]]
) -> tuple[dict[str, Any], DiffEntry]:
    """Add the given (refdes, pin) pairs to a net. `pin` may be a pin number,
    a pin name, or a common alias — stored canonically as a pin number."""
    if not pins:
        raise ToolError("connect requires at least one [refdes, pin] pair.")

    canonicalized: list[tuple[str, str]] = []
    for pair in _validated_pairs(pins, "connect"):
        raw_refdes, raw_pin = pair
        comp = next((c for c in project.ir.components if c.refdes == raw_refdes), None)
        if comp is None:
            raise ToolError(f"Component {raw_refdes!r} does not exist.")
        pin_number = _resolve_pin(comp, raw_pin)
        canonicalized.append((raw_refdes, pin_number))

    # Rail-conflict guard. If this is a power or ground net, refuse to add a
    # pin that's already on a *different* power/ground net — that would short
    # the rails. Caught the t2 smartwatch project's TPS61023 VIN-on-both-VBUS
    # -and-VBAT bug.
    new_role = _net_role(net_name)
    if new_role in {"power", "gnd"}:
        for refdes, pin_number in canonicalized:
            for existing in project.ir.nets:
                if existing.name == net_name:
                    continue
                if not any(r == refdes and p == pin_number for r, p in existing.pins):
                    continue
                existing_role = _net_role(existing.name)
                # Power vs power (different rail), power vs gnd, gnd vs power
                # — all of these are shorts. Power vs signal is fine; signal
                # vs anything is fine.
                conflict = (
                    (new_role == "power" and existing_role in {"power", "gnd"})
                    or (new_role == "gnd" and existing_role == "power")
                )
                if conflict:
                    raise ToolError(
                        f"Pin {refdes}.{pin_number} is already on net "
                        f"{existing.name!r} ({existing_role}). Adding it to "
                        f"{net_name!r} ({new_role}) would short the rails. "
                        f"If you really mean to move it, call disconnect first."
                    )

    # One pin, one node — for EVERY net class, not only rails. The prompt sweep
    # showed R21 (a pin on two nets) as the single most common defect: 12 hits
    # in 12 builds (J2.1 on MOTOR_X and MOTOR_X_A; F1.2 on CHG_IN and VIN_REG),
    # each unroutable by construction and each spending fix-up turns after the
    # fact. Refuse at the source and say how to move the pin instead. The
    # power/ground guard above keeps its more specific "would short the rails"
    # wording; this catches everything it let through (signal vs signal,
    # signal vs rail).
    for refdes, pin_number in canonicalized:
        for existing_net in project.ir.nets:
            if existing_net.name == net_name:
                continue
            if any(r == refdes and p == pin_number for r, p in existing_net.pins):
                raise ToolError(
                    f"Pin {refdes}.{pin_number} is already on net {existing_net.name!r}; "
                    f"a pin can be on ONE net only (R21). If it belongs on {net_name!r} "
                    f"instead, call disconnect(net_name={existing_net.name!r}, "
                    f"pins=[[{refdes!r}, {pin_number!r}]]) first; if you meant to join "
                    f"the two nets, connect the OTHER pins to {existing_net.name!r}."
                )

    net = next((n for n in project.ir.nets if n.name == net_name), None)
    if net is None:
        net = Net(name=net_name, pins=[])
        project.ir.nets.append(net)

    added: list[str] = []
    existing = {tuple(x) for x in net.pins}
    for refdes, pin_number in canonicalized:
        if (refdes, pin_number) not in existing:
            net.pins.append((refdes, pin_number))
            existing.add((refdes, pin_number))
            added.append(f"{refdes}.{pin_number}")

    summary = (
        f"Connected {', '.join(added)} to net <b>{net_name}</b>"
        if added
        else f"Net <b>{net_name}</b> already had those pins"
    )
    return (
        {"net": net_name, "added": added, "total_pins": len(net.pins)},
        DiffEntry(kind="modify", summary=summary),
    )


def disconnect(
    project: Project, *, net_name: str, pins: list[list[str]]
) -> tuple[dict[str, Any], DiffEntry]:
    net = next((n for n in project.ir.nets if n.name == net_name), None)
    if net is None:
        raise ToolError(f"Net {net_name!r} not found.")
    # Canonicalize pin NAMES to numbers exactly like `connect` does — the net
    # stores numbers, so `disconnect(pins=[["U1","IO16"]])` used to match
    # nothing, remove nothing, report "removed 0"... and the agent's follow-up
    # connect then put the pin on a second net (the sweep's #1 defect, R21).
    pair_tuples: list[tuple[str, str]] = []
    for raw_refdes, raw_pin in _validated_pairs(pins, "disconnect"):
        comp = next((c for c in project.ir.components if c.refdes == raw_refdes), None)
        if comp is None:
            raise ToolError(f"refdes {raw_refdes!r} not found.")
        pair_tuples.append((raw_refdes, _resolve_pin(comp, raw_pin)))
    before = len(net.pins)
    net.pins = [p for p in net.pins if tuple(p) not in pair_tuples]
    removed = before - len(net.pins)
    if removed == 0:
        raise ToolError(
            f"None of {[f'{r}.{p}' for r, p in pair_tuples]} is on net {net_name!r} — "
            f"nothing to disconnect. Its pins: {[f'{r}.{p}' for r, p in net.pins][:12]}."
        )
    if not net.pins:
        project.ir.nets = [n for n in project.ir.nets if n.name != net_name]
    return (
        {"net": net_name, "removed": removed, "remaining_pins": len(net.pins)},
        DiffEntry(
            kind="modify",
            summary=f"Disconnected {removed} pin(s) from <b>{net_name}</b>",
        ),
    )


def set_value(
    project: Project, *, refdes: str, value: str
) -> tuple[dict[str, Any], DiffEntry]:
    comp = next((c for c in project.ir.components if c.refdes == refdes), None)
    if comp is None:
        raise ToolError(f"Component {refdes!r} not found.")
    old = comp.value
    comp.value = value
    return (
        {"refdes": refdes, "old": old, "new": value},
        DiffEntry(
            kind="modify",
            summary=f"<b>{refdes}</b> value: {old or '∅'} → {value}",
            refdes=refdes,
        ),
    )


def set_property(
    project: Project, *, refdes: str, key: str, value: str
) -> tuple[dict[str, Any], DiffEntry]:
    comp = next((c for c in project.ir.components if c.refdes == refdes), None)
    if comp is None:
        raise ToolError(f"Component {refdes!r} not found.")
    if key not in {"footprint", "datasheetUrl", "x", "y", "rotation"}:
        raise ToolError(
            f"Property {key!r} is not settable. "
            f"Allowed: footprint, datasheetUrl, x, y, rotation"
        )
    if key in {"x", "y"}:
        try:
            setattr(comp, key, float(value))
        except ValueError as exc:
            raise ToolError(f"{key} must be a number, got {value!r}") from exc
    elif key == "rotation":
        try:
            rot = int(value)
        except ValueError as exc:
            raise ToolError(f"rotation must be an integer, got {value!r}") from exc
        if rot not in (0, 90, 180, 270):
            raise ToolError("rotation must be 0, 90, 180, or 270.")
        comp.rotation = rot  # type: ignore[assignment]
    else:
        setattr(comp, key, value)
    return (
        {"refdes": refdes, "key": key, "value": value},
        DiffEntry(
            kind="modify",
            summary=f"<b>{refdes}</b>.{key} = {value}",
            refdes=refdes,
        ),
    )


def search_parts(
    project: Project,  # unused but keeps a uniform signature
    *,
    query: str | None = None,
    category: str | None = None,
    limit: int = 10,
) -> tuple[dict[str, Any], DiffEntry | None]:
    results = library.search(query=query, category=category)[:limit]
    # Every search is demand evidence; a search that found NOTHING is the
    # strongest evidence we get that the library is missing something a user
    # actually asked for. See services/demand_log.py.
    demand_log.record(query, category, len(results), [p.mpn for p in results],
                      getattr(project, "id", None))
    del project
    return (
        {
            "count": len(results),
            "parts": [
                {
                    "mpn": p.mpn,
                    "description": p.description,
                    "category": p.category,
                    "package": p.package,
                    "unitCost": p.unitCost,
                    "jlcStock": p.jlcStock,
                    "voltageMin": p.voltageMin,
                    "voltageMax": p.voltageMax,
                    "alternatives": p.alternatives,
                }
                for p in results
            ],
        },
        None,  # search doesn't mutate IR — no diff entry
    )


def _mcu_component(project: Project, refdes: str | None = None) -> Component | None:
    if refdes is not None:
        return next((c for c in project.ir.components if c.refdes == refdes), None)
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part and part.category == "mcu":
            return c
    return None



# Pin NAMES that must never be handed out as a general-purpose I/O, however
# desperate the allocator is. Review #17: a stub MCU entry broke out none of its
# family's GPIOs, the allocator fell through to "any free pin", and NRST was
# handed out as BOTH I2C lines - the reset pin became the bus.
_NEVER_ALLOCATE = re.compile(
    r"^(NRST|RESET|RST|BOOT\d*|VCAP\w*|VBAT|VREF\w*|VDD\w*|VSS\w*|VCC\w*|GND\w*|"
    r"AVDD|AVSS|VDDA|VSSA|VDDIO\w*|VDDUSB|SWD\w*|SWCLK|SWDIO|TCK|TMS|TDI|TDO|"
    r"OSC\w*|XTAL\w*|NC|DNC|EP|PAD)$",
    re.IGNORECASE,
)


def _allocatable_pins(mcu, used: set[str]) -> list:
    """Free pins on `mcu` that could plausibly carry a signal: a real I/O type,
    not a reset / supply / debug / crystal pin by name, and not one the part's
    family marks RESERVED (an STM32's PA13/PA14 are the debug port and PH0/PH1
    the oscillator — their bare names give no hint, so ask pin_safety)."""
    out = []
    for pn in mcu.pins:
        if pn.number in used:
            continue
        if str(pn.type) not in {"bidirectional", "input", "output"}:
            continue
        name = (pn.name or "").strip()
        if _NEVER_ALLOCATE.match(name):
            continue
        # "PA13_SWDIO"-style names carry their reserved role in a suffix.
        if re.search(r"(SWDIO|SWCLK|_NRST|_RESET|_BOOT)", name.upper()):
            continue
        caps = pin_safety.capabilities(mcu.mpn, name)
        if caps & {"reserved", "flash"}:
            continue
        out.append(pn)
    return out


def allocate_pin(
    project: Project,
    *,
    role: str,
    count: int = 1,
    mcu_refdes: str | None = None,
) -> tuple[dict[str, Any], DiffEntry | None]:
    """Deterministically pick safe MCU GPIO(s) for a role (#17).

    Read-only — it does NOT mutate the IR. The agent calls this to get a known-
    good pin (avoiding boot-strapping / flash / input-only pins) for a role
    like "gpio", "pwm", "adc", "i2c_sda", "i2c_scl", "uart_tx", "uart_rx", then
    wires the returned pin with `connect`. Replaces the agent free-choosing a
    GPIO, which is how it kept landing on GPIO0/2/12/15 (feedback #5).
    """
    mcu = _mcu_component(project, mcu_refdes)
    if mcu is None:
        raise ToolError(
            "No MCU in the design yet. Add the microcontroller before allocating pins."
        )
    # A role that is physically impossible on this family (e.g. ADC on a
    # Raspberry Pi) gets a deterministic, actionable refusal — not a guess.
    unavailable = pin_safety.role_unavailable_reason(mcu.mpn, role)
    if unavailable:
        raise ToolError(f"Role {role!r} is not available on {mcu.mpn}: {unavailable}")
    if not pin_safety.is_supported(mcu.mpn):
        # Unknown family — return any free pins so the agent isn't blocked, but
        # say so explicitly.
        used = {p for n in project.ir.nets for (r, p) in n.pins if r == mcu.refdes}
        usable = _allocatable_pins(mcu, used)[: max(count, 1)]
        if not usable:
            raise ToolError(
                f"{mcu.mpn} has no free general-purpose I/O pin left for role {role!r} "
                f"(reset, supply, debug and crystal pins are never allocated). Free a "
                f"pin, or pick a part whose pin list actually breaks out I/O."
            )
        return (
            {
                "mcu": mcu.refdes,
                "role": role,
                "pins": [{"number": pn.number, "name": pn.name} for pn in usable],
                "safe": False,
                "note": f"No pin-safety table for {mcu.mpn}; pins not risk-checked.",
            },
            None,
        )

    # Nets store physical pin numbers (e.g. "31"), but GPIO safety reasons in
    # GPIO numbers (IO19 / GP4 / D3 / GPIO17). Translate used physical pins to
    # their pin *names* so the family-aware parser sees the GPIO, not the
    # package pin number.
    by_number = {pn.number: pn for pn in mcu.pins}
    used_phys = {p for n in project.ir.nets for (r, p) in n.pins if r == mcu.refdes}
    used = {by_number[p].name for p in used_phys if p in by_number}
    # Over-request so GPIOs the component doesn't actually break out (skipped
    # below) don't starve the result.
    want = max(count, 1)
    numbers = pin_safety.allocate(mcu.mpn, role, used, count=want + 8)

    # Map back to the component's real pins. Name-based GPIO match FIRST: the
    # physical pin number and the GPIO number are different spaces on modules
    # (Pico physical 6 = GP4), so a by_number hit can silently be the wrong
    # pin. Only fall back to by_number when the number spaces coincide (bare
    # chips whose pins are numbered by GPIO).
    pins_out = []
    for num in numbers:
        if len(pins_out) >= want:
            break
        match = next(
            (
                pn
                for pn in mcu.pins
                if pin_safety.gpio_number(pn.name, mcu.mpn) == int(num)
            ),
            None,
        )
        if match is None:
            candidate = by_number.get(num)
            # The by-NUMBER fallback assumes the physical and GPIO number spaces
            # coincide (true for bare chips numbered by GPIO). It must still
            # refuse a reset / supply / debug pin that merely happens to share
            # the number: on an STM32, physical pin 7 is NRST while GPIO 7 is
            # PA7, and that coincidence is how NRST became an I2C line
            # (beta review #17).
            if (
                candidate is not None
                and str(candidate.type) in {"bidirectional", "input", "output"}
                and pin_safety.gpio_number(candidate.name, mcu.mpn) in (int(num), None)
                and candidate in _allocatable_pins(mcu, used_phys)
            ):
                match = candidate
        if match is not None:
            pins_out.append({"number": match.number, "name": match.name})

    if not pins_out:
        if numbers:
            # The family table allocated GPIOs but this component doesn't model
            # them (a stub entry with only power/reset/crystal pins). Hand back
            # real I/O only - never a reset or supply pin, which is how NRST
            # ended up carrying both I2C lines in beta review #17.
            usable = _allocatable_pins(mcu, used_phys)[:want]
            if not usable:
                raise ToolError(
                    f"{mcu.mpn}'s pin list breaks out no usable I/O for role {role!r} "
                    f"(only supply / reset / debug pins are left). The part entry is "
                    f"incomplete - choose a different MCU, or the library needs its "
                    f"full pin table before this design can be wired."
                )
            return (
                {
                    "mcu": mcu.refdes,
                    "role": role,
                    "pins": [{"number": pn.number, "name": pn.name} for pn in usable],
                    "safe": False,
                    "note": (
                        f"{mcu.mpn} does not break out the family's safe GPIOs "
                        f"in its pin list; pins not risk-checked."
                    ),
                },
                None,
            )
        raise ToolError(
            f"No free safe pin left for role {role!r} on {mcu.refdes}. "
            f"Free up a GPIO or pick a different role."
        )

    return (
        {
            "mcu": mcu.refdes,
            "role": role,
            "pins": pins_out,
            "safe": True,
            "note": "Boot-strapping, flash, reserved and input-only pins were avoided.",
        },
        None,
    )


def acknowledge_finding(
    project: Project, *, issue_id: str, reason: str,
) -> tuple[dict[str, Any], DiffEntry]:
    """Mark a WARNING as intentional, with a reason.

    "This I2C bus has no pull-ups because the Raspberry Pi provides them" is a
    design decision, not a defect — but before this there was no way to say so,
    and every review of a correct board carried "resolve the N warnings" as a
    blocking item. Errors can NOT be acknowledged: an error means the board
    does not work.
    """
    issue = next((i for i in project.issues if i.id == issue_id), None)
    if issue is None:
        known = ", ".join(i.id for i in project.issues[:6]) or "none"
        raise ToolError(
            f"No finding with id {issue_id!r} on this project (current: {known}). "
            f"Run run_drc first, then acknowledge by the exact id."
        )
    if issue.severity == "error":
        raise ToolError(
            f"{issue_id} is an ERROR, not a warning — it means the board will not work "
            f"as drawn. Fix it instead: {issue.message}"
        )
    reason = (reason or "").strip()
    if len(reason) < 8:
        raise ToolError(
            "acknowledge_finding needs a real reason (>= 8 chars) — say WHY it is "
            "intentional, e.g. 'the Pi supplies the I2C pull-ups on its header'."
        )
    ir = project.ir
    existing = next((a for a in ir.acknowledgements if a.issue_id == issue_id), None)
    if existing is not None:
        existing.reason = reason
        existing.at = _now_iso()
    else:
        ir.acknowledgements.append(
            Acknowledgement(issue_id=issue_id, rule=issue.rule, reason=reason, at=_now_iso())
        )
    return (
        {"acknowledged": issue_id, "reason": reason},
        DiffEntry(kind="modify", summary=f"Acknowledged {issue_id}: {reason[:60]}"),
    )


def unacknowledge_finding(project: Project, *, issue_id: str) -> tuple[dict[str, Any], DiffEntry]:
    """Undo an acknowledgement — the finding counts again."""
    ir = project.ir
    before = len(ir.acknowledgements)
    ir.acknowledgements = [a for a in ir.acknowledgements if a.issue_id != issue_id]
    if len(ir.acknowledgements) == before:
        raise ToolError(f"{issue_id} is not acknowledged.")
    return (
        {"unacknowledged": issue_id},
        DiffEntry(kind="modify", summary=f"Un-acknowledged {issue_id}"),
    )


def run_drc(project: Project) -> tuple[dict[str, Any], DiffEntry | None]:
    issues = verifier.run_all(project)
    project.issues = issues
    project.drcErrorCount = sum(1 for i in issues if i.severity == "error")
    return (
        {
            "issue_count": len(issues),
            "errors": project.drcErrorCount,
            "warnings": sum(1 for i in issues if i.severity == "warning"),
            "issues": [i.model_dump() for i in issues],
        },
        None,
    )


# Deterministic repair of floating decoupling caps ---------------------------


def _decoupling_cap_mpns() -> set[str]:
    """MPNs of caps whose job is to sit across a supply rail and GND
    (decoupling / bypass / bulk). Pulled from the library description so it
    tracks the catalog. Crystal load caps and coupling caps are excluded — they
    have other topologies and must not be auto-wired power↔GND."""
    out: set[str] = set()
    for p in library.all_parts():
        d = (p.description or "").lower()
        if "decoupl" in d or "bypass" in d or "bulk" in d:
            out.add(p.mpn)
    return out


def _primary_gnd_net(project: Project) -> str | None:
    gnds = [n for n in project.ir.nets if _net_role(n.name) == "gnd"]
    return max(gnds, key=lambda n: len(n.pins)).name if gnds else None


def _primary_power_net(project: Project) -> str | None:
    """The rail decoupling caps should sit on. Prefer a regulated logic rail
    (3V3 / VCC / VDD) over a raw input rail (VBUS / VBAT / 5V) — that's where
    bypass caps belong on a typical MCU board — then break ties by pin count."""
    pwr = [n for n in project.ir.nets if _net_role(n.name) == "power"]
    if not pwr:
        return None

    def rank(n: Net) -> tuple[int, int]:
        u = n.name.upper()
        regulated = ("VBUS" not in u and "VBAT" not in u) and any(
            k in u for k in ("3V3", "3.3", "VCC", "VDD")
        )
        return (1 if regulated else 0, len(n.pins))

    return max(pwr, key=rank).name


def repair_floating_caps(project: Project) -> list[str]:
    """Wire decoupling caps the agent placed but left floating: one pin to the
    main supply rail, the other to GND. Reuses `connect` so the rail-short guard
    still applies. Returns the refdeses repaired. Best-effort and conservative —
    only touches known decoupling caps, and only when both rails already exist."""
    cap_mpns = _decoupling_cap_mpns()
    gnd_net = _primary_gnd_net(project)
    power_net = _primary_power_net(project)
    if not gnd_net or not power_net:
        return []

    pin_net: dict[tuple[str, str], str] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            pin_net[(r, p)] = net.name

    fixed: list[str] = []
    for comp in project.ir.components:
        if not comp.refdes.startswith("C") or comp.mpn not in cap_mpns:
            continue
        pins = comp.pins or []
        if len(pins) != 2:
            continue

        conn = [(p.number, pin_net.get((comp.refdes, p.number))) for p in pins]
        if all(net for _, net in conn):
            continue  # already fully wired

        has_power = any(net and _net_role(net) == "power" for _, net in conn)
        has_gnd = any(net and _net_role(net) == "gnd" for _, net in conn)
        free = [num for num, net in conn if not net]

        try:
            if not any(net for _, net in conn):
                # Floating on both sides: pin 1 → rail, pin 2 → GND.
                connect(project, net_name=power_net, pins=[[comp.refdes, pins[0].number]])
                connect(project, net_name=gnd_net, pins=[[comp.refdes, pins[1].number]])
            elif has_power and not has_gnd:
                connect(project, net_name=gnd_net, pins=[[comp.refdes, free[0]]])
            elif has_gnd and not has_power:
                connect(project, net_name=power_net, pins=[[comp.refdes, free[0]]])
            else:
                connect(project, net_name=gnd_net, pins=[[comp.refdes, free[0]]])
        except ToolError:
            continue
        fixed.append(comp.refdes)

    return fixed


# Auto-layout helpers ---------------------------------------------------------


def _auto_x(project: Project) -> float:
    """Place new components in a 4-wide grid with 280-unit columns (wider than
    the largest node, so they don't overlap)."""
    if not project.ir.components:
        return 0.0
    n = len(project.ir.components)
    return (n % 4) * 280.0


def _auto_y(project: Project) -> float:
    if not project.ir.components:
        return 0.0
    n = len(project.ir.components)
    return (n // 4) * 350.0


# BOM rebuilds --------------------------------------------------------------

def recompute_bom(project: Project) -> list[BomLine]:
    by_mpn: dict[str, list[str]] = {}
    for c in project.ir.components:
        by_mpn.setdefault(c.mpn, []).append(c.refdes)

    from app.services import pricing

    lines: list[BomLine] = []
    for mpn, refdeses in by_mpn.items():
        part = library.get_part(mpn)
        # Price provenance: a cached live price wins (flagged when past its
        # TTL — B4); else the library's reference cost (labelled "est." in the
        # UI); else honestly no price.
        live, stale = pricing.cached_price_any(mpn)
        if live is not None:
            # B1: the price at THIS line's quantity, not the 1-off break.
            unit = pricing.price_for_qty(live, len(refdeses))
            source: str = "live"
            currency: str | None = live.currency or "USD"
        else:
            unit = part.unitCost if part else 0.0
            source = "reference" if unit > 0 else "none"
            # The curated library's ballpark costs are USD by construction.
            currency = "USD" if unit > 0 else None
        # Description joins the refdes with the part description short form.
        first = refdeses[0]
        rest = f" +{len(refdeses) - 1}" if len(refdeses) > 1 else ""
        desc = f"{first}{rest} ({part.description.split(',')[0]})" if part else first
        lines.append(
            BomLine(
                refdes=first,
                description=desc,
                qty=len(refdeses),
                unitCost=unit,
                mpn=mpn,
                priceSource=source,  # type: ignore[arg-type]
                stock=live.stock if live else None,
                priceVendor=live.vendor if live else None,
                priceAsOf=time.strftime("%Y-%m-%d", time.localtime(live.fetchedAt)) if live else None,
                priceCurrency=currency,
                priceStale=bool(live and stale),
                breaks=list(live.breaks) if live else [],
                sku=live.sku if live else None,
                moq=live.moq if live else None,
                lifecycle=live.lifecycle if live else None,
                leadDays=live.leadDays if live else None,
            )
        )
    lines.sort(key=lambda l: l.refdes)
    project.bom = lines
    return lines


# Public dispatch table -------------------------------------------------------

def _apply_template(
    project: Project, *, template_name: str, net_map: dict | None = None
) -> tuple[dict[str, Any], DiffEntry]:
    """Compose a vetted reference sub-circuit block (services/templates.py). The
    LLM picks a validated block + maps its placeholder nets onto real nets, rather
    than hand-wiring a common topology. Lazy import breaks the tools<->templates
    cycle."""
    from app.services import templates
    return templates.apply(project, template_name=template_name, net_map=net_map)


TOOLS = {
    "add_component": add_component,
    "add_component_array": add_component_array,
    "set_array_pitch": set_array_pitch,
    "arrange_grid": arrange_grid,
    "connect_matrix": connect_matrix,
    "record_requirements": record_requirements,
    "set_form_factor": set_form_factor,
    "delete_component": delete_component,
    "connect": connect,
    "disconnect": disconnect,
    "set_value": set_value,
    "set_property": set_property,
    "search_parts": search_parts,
    "allocate_pin": allocate_pin,
    "run_drc": run_drc,
    "acknowledge_finding": acknowledge_finding,
    "unacknowledge_finding": unacknowledge_finding,
    "apply_template": _apply_template,
}


def dispatch(
    project: Project, name: str, args: dict
) -> tuple[dict[str, Any], DiffEntry | None]:
    fn = TOOLS.get(name)
    if fn is None:
        # Mechanical tools (Week 3 / 5.4) share the same dispatch surface so the
        # agent mutates the mechanical IR only through tools, same contract as
        # the electrical side. Lazy import breaks the tools<->mechanical cycle.
        from app.services import mechanical_tools
        if name in mechanical_tools.MECHANICAL_TOOLS:
            return mechanical_tools.dispatch(project, name, args)
        raise ToolError(f"Unknown tool {name!r}.")
    try:
        return fn(project, **args)  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        # A malformed call from the model — wrong/extra/missing keyword
        # (`connect(parameters={...})`) or a mis-shaped value (a 3-element pin
        # "pair") — must go back to the model as a tool error. Both were seen
        # live escaping and killing the whole streaming turn.
        raise ToolError(f"Bad arguments for {name}: {e}") from e
