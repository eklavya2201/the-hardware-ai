"""ECAD ↔ MCAD interchange via IDF — Week-4 ECAD task 6.3.

IDF (Intermediate Data Format) is the industry-standard board/enclosure exchange
every MCAD tool (SolidWorks, Fusion, Creo, KiCad) reads. We implement **IDF 3.0**
— the de-facto standard `.emn` (board) + `.emp` (part library) pair (the plan
says "IDF 4.0"; 4.0 folded into a single seldom-supported file, so the widely-read
3.0 pair is the pragmatic, interoperable choice). Plain text, no external tool —
so unlike the KiCad/CadQuery bridges this has no hard dependency and never
degrades to 503; `status()` is always "available".

**Export (ECAD → MCAD)** is a one-way writer like Gerber / STEP / `.kicad_pcb`:
board outline, mounting holes, keep-outs, connector cutouts, and component
placements + real body heights (the curated `bodyHeightMm`).

**Import (MCAD → ECAD) is the sensitive half** and obeys a hard architectural
contract (see `docs/WEEK4_ECAD_PLAN.md` §6.3 and the "IR is the single source of
truth / exports are one-way" invariant):

  1. It writes ONLY the *mechanical* IR (outline, mounting holes, keep-outs,
     connector cutouts) — NEVER the electrical IR or the Board.
  2. Every change lands through a validated `mechanical_tools` call (which e.g.
     rejects a cutout for an unplaced refdes) — NEVER a raw IR/Board write.
  3. Anything that maps to the electrical side (a component the MCAD user *moved*)
     is IGNORED and surfaced as a warning — never applied.

So a hand-edited MCAD file can only ever move validated *mechanical* geometry
through the same tool layer the agent uses; it can't inject electrical geometry or
bypass a guard. That is exactly why we allow this import when we refused a
`.kicad_pcb` → IR reader.
"""

from __future__ import annotations

import datetime
import io
import shlex
import zipfile
from dataclasses import dataclass, field

from app.models import PlacedComponent, Project
from app.services import mechanical_derive, mechanical_tools as mt


def status() -> str:
    """IDF is plain text — always available (no external kernel)."""
    return "available"


def _n(v: float) -> str:
    return f"{v:.4f}"


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "PART"


def _geom_name(pc: PlacedComponent) -> str:
    ex, ey = mechanical_derive._footprint_extent(pc)
    pkg = _sanitize(getattr(pc.footprint, "name", None) or pc.mpn)
    return f"{pkg}__{ex:.2f}x{ey:.2f}"


# ============================================================================ #
# Export (ECAD -> MCAD)
# ============================================================================ #
def export_idf(project: Project) -> tuple[str, str]:
    """Build the IDF 3.0 `(board_emn, library_emp)` text pair from the project.
    Uses the mechanical outline if set, else the placed board. Coordinates are mm
    in the IR frame (Y-up, origin bottom-left) — IDF's own convention, so the
    mapping is 1:1 (no flip, unlike KiCad). Raises IdfError if there's nothing to
    export."""
    board = project.board
    if board is None or not board.components:
        raise IdfError("No placed board to export — generate the PCB layout first.")
    mech = project.mechanical
    stamp = datetime.datetime.now().strftime("%Y/%m/%d.%H:%M:%S")
    name = _sanitize(project.name or project.id)

    # Outline (w, h) + optional polygon points.
    if mech is not None and mech.outline is not None:
        w, h = mech.outline.width, mech.outline.height
        poly = [(p.x, p.y) for p in mech.outline.points] if mech.outline.points else None
    else:
        w, h = board.width, board.height
        poly = None
    outline_pts = poly or [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]

    # ---- .emn (board) ----
    L: list[str] = []
    L.append(".HEADER")
    L.append(f'BOARD_FILE 3.0 "Synqit" {stamp} 1')
    L.append(f'"{name}" MM')
    L.append(".END_HEADER")

    L.append(".BOARD_OUTLINE ECAD")
    L.append(_n(1.6))  # board thickness
    for (x, y) in outline_pts:
        L.append(f"0 {_n(x)} {_n(y)} 0.0")
    L.append(f"0 {_n(outline_pts[0][0])} {_n(outline_pts[0][1])} 0.0")  # close the loop
    L.append(".END_BOARD_OUTLINE")

    holes = mech.mountingHoles if mech else []
    if holes:
        L.append(".DRILLED_HOLES")
        for hole in holes:
            plating = "PTH" if hole.plated else "NPTH"
            L.append(f"{_n(hole.diameter)} {_n(hole.x)} {_n(hole.y)} {plating} NOREFDES MTG BOARD")
        L.append(".END_DRILLED_HOLES")

    # Keep-outs → PLACE_KEEPOUT sections (one loop each).
    for ko in (mech.keepouts if mech else []):
        L.append(".PLACE_KEEPOUT ECAD")
        L.append("0.0")  # 0 height = full-height keepout
        for (x, y) in _rect_loop(ko.x, ko.y, ko.width, ko.height):
            L.append(f"0 {_n(x)} {_n(y)} 0.0")
        L.append(".END_PLACE_KEEPOUT")

    # Connector cutouts → OTHER_OUTLINE, refdes carried in the identifier so the
    # import can re-attach it (and hit the anti-hallucination refdes guard).
    for cut in (mech.cutouts if mech else []):
        ident = _sanitize(f"CUTOUT_{cut.refdes or 'NA'}_{cut.edge}")
        L.append(f'.OTHER_OUTLINE ECAD')
        L.append(f'"{ident}" {_n(cut.height)} TOP')
        for (x, y) in _rect_loop(cut.x, cut.y, cut.width, cut.height):
            L.append(f"0 {_n(x)} {_n(y)} 0.0")
        L.append(".END_OTHER_OUTLINE")

    L.append(".COMPONENT_PLACEMENT")
    for pc in board.components:
        geom = _geom_name(pc)
        side = "BOTTOM" if pc.layer == "bottom" else "TOP"
        L.append(f'"{geom}" "{_sanitize(pc.mpn)}" {pc.refdes}')
        L.append(f"{_n(pc.x)} {_n(pc.y)} 0.0 {_n(float(pc.rotation))} {side} PLACED")
    L.append(".END_COMPONENT_PLACEMENT")
    emn = "\n".join(L) + "\n"

    # ---- .emp (part library): one .ELECTRICAL per unique geometry ----
    P: list[str] = []
    P.append(".HEADER")
    P.append(f'LIBRARY_FILE 3.0 "Synqit" {stamp} 1')
    P.append(".END_HEADER")
    seen: set[str] = set()
    for pc in board.components:
        geom = _geom_name(pc)
        if geom in seen:
            continue
        seen.add(geom)
        ex, ey = mechanical_derive._footprint_extent(pc)
        height = mechanical_derive.body_height_mm(pc.mpn)
        P.append(f'.ELECTRICAL')
        P.append(f'"{geom}" "{_sanitize(pc.mpn)}" MM {_n(height)}')
        # Footprint outline: rectangle centred on the placement origin.
        for (x, y) in _rect_loop(-ex / 2, -ey / 2, ex, ey):
            P.append(f"0 {_n(x)} {_n(y)} 0.0")
        P.append(".END_ELECTRICAL")
    emp = "\n".join(P) + "\n"
    return emn, emp


def export_idf_zip(project: Project) -> bytes:
    """The `.emn` + `.emp` pair + a README, zipped (mirrors the fab-package export)."""
    emn, emp = export_idf(project)
    name = _sanitize(project.name or project.id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}.emn", emn)
        zf.writestr(f"{name}.emp", emp)
        zf.writestr("README.txt", _README)
    return buf.getvalue()


def _rect_loop(x: float, y: float, w: float, h: float) -> list[tuple[float, float]]:
    """Closed rectangle loop (5 points) from a bottom-left corner + size."""
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]


_README = (
    "Synqit IDF 3.0 export (ECAD -> MCAD)\n"
    "  <name>.emn  board file: outline, mounting holes, keep-outs, connector\n"
    "              cutouts (OTHER_OUTLINE), component placements.\n"
    "  <name>.emp  part library: one geometry per package with its body height.\n\n"
    "Coordinates are mm, Y-up, origin at the board's bottom-left.\n"
    "Round-trip: edit the board outline / mounting holes / keep-outs / connector\n"
    "cutouts in your MCAD tool and re-import the .emn into Synqit. Only mechanical\n"
    "features are read back — moving a component in MCAD is reported but NOT applied\n"
    "(placement stays owned by the Synqit PCB editor).\n"
)


# ============================================================================ #
# Import (MCAD -> ECAD) — mechanical IR only, via mechanical_tools
# ============================================================================ #
@dataclass
class IdfImportResult:
    outline_updated: bool = False
    holes_added: int = 0
    keepouts_added: int = 0
    cutouts_added: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.outline_updated:
            bits.append("outline updated")
        if self.holes_added:
            bits.append(f"{self.holes_added} mounting hole(s)")
        if self.keepouts_added:
            bits.append(f"{self.keepouts_added} keep-out(s)")
        if self.cutouts_added:
            bits.append(f"{self.cutouts_added} connector cutout(s)")
        head = "Imported: " + (", ".join(bits) if bits else "no new mechanical features")
        if self.warnings:
            head += f"; {len(self.warnings)} warning(s)"
        return head


def _sections(text: str) -> list[tuple[str, list[str], list[str]]]:
    """Split IDF text into (section_name, header_tokens, record_lines) tuples.
    A section runs from `.NAME [args]` to `.END_NAME`."""
    out: list[tuple[str, list[str], list[str]]] = []
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith(".") and not ln.upper().startswith(".END"):
            toks = shlex.split(ln[1:])
            name = toks[0].upper() if toks else ""
            header_args = toks[1:]
            body: list[str] = []
            i += 1
            end = f".END_{name}"
            while i < len(lines) and lines[i].upper() != end:
                if lines[i]:
                    body.append(lines[i])
                i += 1
            out.append((name, header_args, body))
        i += 1
    return out


def _units_scale(sections) -> float:
    for name, _args, body in sections:
        if name == "HEADER":
            for rec in body:
                toks = shlex.split(rec)
                if toks and toks[-1].upper() in ("MM", "THOU"):
                    return 1.0 if toks[-1].upper() == "MM" else 0.0254
    return 1.0


def _loop_points(body: list[str], scale: float) -> list[tuple[float, float]]:
    """Outline points of loop 0 (label 0). Each record: `label x y angle`."""
    pts: list[tuple[float, float]] = []
    for rec in body:
        toks = shlex.split(rec)
        if len(toks) >= 3:
            try:
                label = int(float(toks[0]))
                if label != 0:
                    continue
                pts.append((float(toks[1]) * scale, float(toks[2]) * scale))
            except ValueError:
                continue
    return pts


def _is_rectangle(pts: list[tuple[float, float]]) -> bool:
    uniq = pts[:-1] if len(pts) >= 2 and pts[0] == pts[-1] else pts
    if len(uniq) != 4:
        return False
    xs = {round(x, 3) for x, _ in uniq}
    ys = {round(y, 3) for _, y in uniq}
    return len(xs) == 2 and len(ys) == 2


def import_idf(project: Project, emn_text: str) -> IdfImportResult:
    """Parse an IDF `.emn` and apply ONLY its mechanical features to the project,
    exclusively through `mechanical_tools` (contract above). Never raises on
    malformed input — best-effort, collecting warnings. Returns an
    `IdfImportResult`. The caller persists + commits the project."""
    res = IdfImportResult()
    try:
        sections = _sections(emn_text)
    except Exception as exc:  # noqa: BLE001
        res.warnings.append(f"Could not parse IDF: {exc}")
        return res
    if not sections:
        res.warnings.append("No IDF sections found — is this an .emn board file?")
        return res
    scale = _units_scale(sections)
    board = project.board
    placed = {pc.refdes: pc for pc in (board.components if board else [])}
    mech = mt._mech(project)

    for name, args, body in sections:
        try:
            if name == "BOARD_OUTLINE":
                _import_outline(project, body, scale, res)
            elif name == "DRILLED_HOLES":
                _import_holes(project, body, scale, mech, res)
            elif name == "PLACE_KEEPOUT":
                _import_keepout(project, body, scale, mech, res)
            elif name == "OTHER_OUTLINE":
                _import_cutout(project, args, body, scale, res)
            elif name == "COMPONENT_PLACEMENT":
                _import_placement_check(body, scale, placed, res)
        except Exception as exc:  # noqa: BLE001 — one bad section can't abort the import
            res.warnings.append(f"{name}: skipped ({exc})")
    return res


def _import_outline(project, body, scale, res) -> None:
    # First non-loop record is the board thickness; loop-0 points are the outline.
    pts = _loop_points(body, scale)
    if len(pts) < 3:
        return
    if _is_rectangle(pts):
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        mt.set_board_outline(project, shape="rect",
                             width=max(xs) - min(xs), height=max(ys) - min(ys))
    else:
        uniq = pts[:-1] if pts[0] == pts[-1] else pts
        mt.set_board_outline(project, shape="polygon",
                             points=[{"x": x, "y": y} for x, y in uniq])
    res.outline_updated = True


def _import_holes(project, body, scale, mech, res) -> None:
    for rec in body:
        toks = shlex.split(rec)
        # dia x y plating refdes hole_type owner
        if len(toks) < 6:
            continue
        htype = toks[5].upper()
        if htype not in ("MTG", "MOUNT", "MOUNTING"):
            continue  # only mounting holes flow into the mechanical IR
        try:
            dia = float(toks[0]) * scale
            x = float(toks[1]) * scale
            y = float(toks[2]) * scale
        except ValueError:
            continue
        if any(abs(h.x - x) < 0.5 and abs(h.y - y) < 0.5 for h in mech.mountingHoles):
            continue  # already present
        plated = toks[3].upper() == "PTH"
        mt.add_mounting_hole(project, x=x, y=y, diameter=dia, plated=plated)
        res.holes_added += 1


def _import_keepout(project, body, scale, mech, res) -> None:
    pts = _loop_points(body, scale)
    if len(pts) < 3:
        return
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    x0, y0, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    if any(abs(k.x - x0) < 0.5 and abs(k.y - y0) < 0.5
           and abs(k.width - w) < 0.5 and abs(k.height - h) < 0.5
           for k in mech.keepouts):
        return
    mt.add_keepout(project, x=x0, y=y0, width=w, height=h, reason="MCAD import")
    res.keepouts_added += 1


def _import_cutout(project, args, body, scale, res) -> None:
    # The section's first record is `"identifier" thickness side`; the identifier
    # encodes CUTOUT_<refdes>_<edge>. (The `.OTHER_OUTLINE` header arg is the owner,
    # e.g. ECAD — not the identifier.) The remaining records are the loop points.
    refdes = None
    thickness: float | None = None
    for rec in body:
        toks = shlex.split(rec)
        if not toks:
            continue
        try:  # a loop point starts with an integer label — skip those
            int(float(toks[0]))
            continue
        except ValueError:
            pass
        parts = _sanitize(toks[0]).split("_")
        if len(parts) >= 2 and parts[0] == "CUTOUT":
            refdes = parts[1] if parts[1] != "NA" else None
        if len(toks) > 1 and _is_float(toks[1]):
            thickness = float(toks[1]) * scale
        break
    pts = _loop_points(body, scale)
    if len(pts) < 3:
        return
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    x0, y0, w, h = min(xs), min(ys), max(xs) - min(ys), max(ys) - min(ys)
    height = thickness if thickness is not None else h
    # Recompute the edge from the connector's current placement (consistent with
    # 6.1); default "none" if the connector isn't placed (the tool then rejects it).
    edge = "none"
    board = project.board
    if refdes and board:
        pc = next((c for c in board.components if c.refdes == refdes), None)
        if pc is not None:
            edge, _ = mechanical_derive._nearest_edge(pc.x, pc.y, board.width, board.height)
    try:
        # Lands through the tool → anti-hallucination refdes guard applies (a
        # cutout for an unplaced connector is rejected here).
        mt.add_connector_cutout(project, x=x0, y=y0, width=w, height=height,
                                edge=edge, refdes=refdes)
        res.cutouts_added += 1
    except mt.MechToolError as exc:
        res.warnings.append(f"Cutout for {refdes!r} rejected: {exc}")


def _import_placement_check(body, scale, placed, res) -> None:
    # Records come in pairs: `"geom" "part" refdes` then `x y zoff rot side status`.
    i = 0
    while i + 1 < len(body):
        head = shlex.split(body[i])
        data = shlex.split(body[i + 1])
        i += 2
        if len(head) < 3 or len(data) < 2:
            continue
        refdes = head[2]
        try:
            x, y = float(data[0]) * scale, float(data[1]) * scale
        except ValueError:
            continue
        pc = placed.get(refdes)
        if pc is None:
            continue
        if abs(pc.x - x) > 0.5 or abs(pc.y - y) > 0.5:
            # Contract: component placement is the electrical/Board side — a move
            # in MCAD is reported, NEVER written back.
            res.warnings.append(
                f"{refdes} was moved in MCAD to ({x:.1f}, {y:.1f}) — not applied "
                f"(move components in the Synqit PCB editor)."
            )


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


class IdfError(Exception):
    """Raised by the IDF exporter when there's nothing to export."""
