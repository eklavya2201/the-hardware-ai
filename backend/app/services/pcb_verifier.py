"""PCB-level Design Rule Checks (PRD §7.5).

These run on the Board (placement + routing) — distinct from the schematic-only
verifier in services/verifier.py. They surface problems that only exist in the
physical layout: component overlap, traces outside board outline, unrouted
nets, trace clearance, etc.

Issues are appended to the project's `issues` list with a `PCB_*` rule prefix
so the Inspector can distinguish them from schematic rules.
"""

from __future__ import annotations

from app.models import Board, PlacedComponent, Project, VerifierIssue
from app.services import board_shape, geometry, kicad_bridge, library
from app.services.footprints import _CRITICAL_FOOTPRINT_CATEGORIES


def _is_edge_port(part) -> bool:
    """A connector whose MOUTH faces sideways, so the plug arrives from off the
    board: USB, barrel jack, audio, edge-launch RF, card slot.

    Keyed on the curated `matingFaceDir` — the same field the 3D view and the
    enclosure cut-outs use, established by measuring each footprint's fab
    outline. A TOP-entry connector (JST XH/PH vertical, pin header, Qwiic) has
    no in-plane mouth and is deliberately un-seeded there: it plugs from above
    and is perfectly usable a few millimetres inboard, so flagging it would be
    noise."""
    from app.services import mechanical_derive

    if not getattr(part, "matingFaceDir", None):
        return False
    return mechanical_derive._is_cutout_connector(part.mpn)


MIN_BOARD_MARGIN = 0.5   # mm — components must be at least this far from edge
MIN_TRACE_WIDTH = 0.1
MIN_DRILL = 0.2


def _bbox(comp_x: float, comp_y: float, w: float, h: float) -> tuple[float, float, float, float]:
    """Return (x_min, y_min, x_max, y_max) for a component at (cx, cy)."""
    return (
        comp_x - w / 2,
        comp_y - h / 2,
        comp_x + w / 2,
        comp_y + h / 2,
    )


def _body_bbox(pc: PlacedComponent) -> tuple[float, float, float, float]:
    """Body bbox using the SAME model the placer solves in: `PlacedComponent.x/y`
    is the footprint ANCHOR (pin 1 on pin-1-anchored parts), the body sits on
    the pad-field centroid rotated by the part's rotation, and 90/270 swap the
    extents. Centring an un-rotated box on the anchor (the old behaviour) put a
    rotated USB-C's box millimetres from its real body and reported phantom
    overlaps against parts the placer had legally seated beside it."""
    fp = pc.footprint
    if fp.pads:
        xs = [p.x for p in fp.pads]
        ys = [p.y for p in fp.pads]
        ox, oy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    else:
        ox = oy = 0.0
    sx, sy = geometry.rotate_xy(ox, oy, pc.rotation)
    w, h = (fp.height, fp.width) if pc.rotation % 180 else (fp.width, fp.height)
    return _bbox(pc.x + sx, pc.y + sy, w, h)


def _bboxes_overlap(a, b, slack: float = 0.0) -> bool:
    return not (
        a[2] + slack < b[0]
        or b[2] + slack < a[0]
        or a[3] + slack < b[1]
        or b[3] + slack < a[1]
    )


# ---- Rules ----------------------------------------------------------------


def PCB_R01_component_overlap(board: Board) -> list[VerifierIssue]:
    """No two components on the same layer can overlap each other."""
    issues: list[VerifierIssue] = []
    by_layer: dict[str, list] = {"top": [], "bottom": []}
    for pc in board.components:
        by_layer[pc.layer].append(pc)

    for layer, comps in by_layer.items():
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                a = comps[i]
                b = comps[j]
                # Compare the rectangles each part actually OCCUPIES, not their
                # bounding boxes: an Arduino shield's header pair is two strips
                # along opposite edges, and its bounding box is the whole board
                # — so every component a shield is supposed to carry read as an
                # overlap against its own host header (2026-08-20).
                if any(
                    _bboxes_overlap(ra, rb)
                    for ra in geometry.solid_rects(a)
                    for rb in geometry.solid_rects(b)
                ):
                    issues.append(
                        VerifierIssue(
                            id=f"PCB_R01:{a.refdes}-{b.refdes}",
                            severity="error",
                            message=f"Components {a.refdes} and {b.refdes} overlap on the {layer} layer",
                            refdes=a.refdes,
                            rule="PCB_R01_component_overlap",
                        )
                    )
    return issues


def PCB_R02_off_board(board: Board) -> list[VerifierIssue]:
    """All components must fit inside the board outline (with margin). On a
    round board the outline is the disc, not the bounding square — a part in
    the square's corner is hanging in air past the milled edge."""
    issues: list[VerifierIssue] = []
    round_board = board.shape == "round"
    poly_ring = board_shape.ring_for_board(board) if board.shape == "polygon" else None
    r_lim = board.width / 2.0 - MIN_BOARD_MARGIN
    cx = cy = board.width / 2.0
    for pc in board.components:
        # Containment is judged on the real occupied rectangles, so a
        # multi-strip part is measured by its strips, not by a bounding box
        # that spans the empty middle between them.
        rects = geometry.solid_rects(pc)
        x_min = min(r[0] for r in rects)
        y_min = min(r[1] for r in rects)
        x_max = max(r[2] for r in rects)
        y_max = max(r[3] for r in rects)
        # A connector with a sideways MOUTH is SUPPOSED to reach the outline —
        # a USB-C shell overhangs the edge, that is how the cable seats. Its
        # pads stay inboard, so copper-to-edge is unaffected. Judging it by the
        # generic body margin reported the correct placement as an error.
        part = library.get_part(pc.mpn)
        margin = 0.0 if (part is not None and _is_edge_port(part)) else MIN_BOARD_MARGIN
        off = (
            x_min < margin - 0.05
            or y_min < margin - 0.05
            or x_max > board.width - margin + 0.05
            or y_max > board.height - margin + 0.05
        )
        if not off and round_board:
            r_lim_part = board.width / 2.0 - margin
            off = any(
                ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 > r_lim_part
                for px, py in (
                    (x_min, y_min), (x_max, y_min), (x_min, y_max), (x_max, y_max),
                )
            )
        if not off and poly_ring is not None:
            # Polygon board: a part inside the bounding box can still be off
            # the copper — the corners of a triangle and the notches of an X
            # are exactly where the placer likes to put a decoupling cap.
            off = any(
                board_shape.clearance(poly_ring, px, py) < margin
                for px, py in (
                    (x_min, y_min), (x_max, y_min), (x_min, y_max), (x_max, y_max),
                )
            )
        if off:
            issues.append(
                VerifierIssue(
                    id=f"PCB_R02:{pc.refdes}",
                    severity="warning",
                    message=f"{pc.refdes} extends past the board edge",
                    refdes=pc.refdes,
                    rule="PCB_R02_off_board",
                )
            )
    return issues


_GND_NETS = {"GND", "VSS", "AGND", "DGND", "0V"}


def PCB_R03_unrouted_nets(board: Board) -> list[VerifierIssue]:
    """Any leftover rats-nest line is an unrouted net. GND is exempt when the
    board uses a copper pour — the pour connects all GND pads through the
    bottom-layer fill, even though we don't route individual traces."""
    issues: list[VerifierIssue] = []
    seen: set[str] = set()
    pour_active = bool(getattr(board, "has_ground_pour", False))
    # 4-layer: a net carried by an inner plane is connected through its vias
    # (SMD) / plated holes (THT), never by routed traces — no rat is real.
    plane_nets = {p.net for p in (getattr(board, "planes", None) or [])}
    for r in board.rats:
        if r.net in seen:
            continue
        if pour_active and r.net.upper() in _GND_NETS:
            continue
        if r.net in plane_nets:
            continue
        seen.add(r.net)
        issues.append(
            VerifierIssue(
                id=f"PCB_R03:{r.net}",
                severity="error",
                message=f"Net {r.net} could not be fully routed — manual fix required",
                net=r.net,
                rule="PCB_R03_unrouted_nets",
            )
        )
    return issues


def PCB_R04_trace_width(board: Board) -> list[VerifierIssue]:
    """Trace widths below the manufacturable minimum."""
    issues: list[VerifierIssue] = []
    for t in board.traces:
        if t.width < MIN_TRACE_WIDTH:
            issues.append(
                VerifierIssue(
                    id=f"PCB_R04:{t.net}",
                    severity="warning",
                    message=f"Trace on net {t.net} is {t.width}mm — below {MIN_TRACE_WIDTH}mm minimum",
                    net=t.net,
                    rule="PCB_R04_trace_width",
                )
            )
            break  # one per net is enough
    return issues


def PCB_R05_drill_size(board: Board) -> list[VerifierIssue]:
    """Via drills below the fab minimum."""
    issues: list[VerifierIssue] = []
    bad = [v for v in board.vias if v.drill < MIN_DRILL]
    if bad:
        issues.append(
            VerifierIssue(
                id="PCB_R05:vias",
                severity="warning",
                message=f"{len(bad)} via(s) have drill below {MIN_DRILL}mm minimum",
                rule="PCB_R05_drill_size",
            )
        )
    return issues


def PCB_R06_pad_in_component(board: Board) -> list[VerifierIssue]:
    """Sanity: a footprint's declared body must contain its own pads.

    Measured against the body positioned the way every other consumer positions
    it — centred on the PAD-FIELD CENTROID, not on the footprint origin. Origin
    and body centre coincide only for centred footprints; a pin-1-anchored one
    (every pin header, every module) has its origin at a corner, so the old
    `abs(pad.x) > width/2` test reported a perfectly good header as having pads
    outside its body. What remains is the real defect: a declared body SMALLER
    than the pads it is supposed to cover.
    """
    issues: list[VerifierIssue] = []
    for pc in board.components:
        fp = pc.footprint
        if not fp.pads:
            continue
        xs = [p.x for p in fp.pads]
        ys = [p.y for p in fp.pads]
        ox, oy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        for pad in fp.pads:
            if abs(pad.x - ox) > fp.width / 2 + 0.5 or abs(pad.y - oy) > fp.height / 2 + 0.5:
                issues.append(
                    VerifierIssue(
                        id=f"PCB_R06:{pc.refdes}.{pad.pin_number}",
                        severity="info",
                        message=f"Pad {pad.pin_number} on {pc.refdes} sits outside its body — verify footprint",
                        refdes=pc.refdes,
                        pinNumber=pad.pin_number,
                        rule="PCB_R06_pad_in_component",
                    )
                )
                break
    return issues


def PCB_R07_unverified_footprint(board: Board) -> list[VerifierIssue]:
    """Every part should map to a real, verified KiCad footprint (task 3.2).

    A footprint whose `confidence` is not "verified" is parametric geometry, not
    fab-final. When KiCad is installed we flag each such part individually (error
    for critical categories, warning otherwise) so the gaps are actionable —
    typically a wrong `Lib:Name` in the library or a part with no stock KiCad
    footprint (modules, custom parts). When KiCad is *absent* we can't verify
    anything, so we emit one advisory instead of flooding a warning per part."""
    issues: list[VerifierIssue] = []

    if not kicad_bridge.is_available():
        if any(pc.footprint.confidence != "verified" for pc in board.components):
            issues.append(
                VerifierIssue(
                    id="PCB_R07:kicad-absent",
                    severity="info",
                    message=(
                        "KiCad not installed — footprint geometry is parametric, "
                        "not fab-verified. Install KiCad to verify footprints."
                    ),
                    rule="PCB_R07_unverified_footprint",
                )
            )
        return issues

    for pc in board.components:
        if pc.footprint.confidence == "verified":
            continue
        part = library.get_part(pc.mpn)
        critical = part is not None and part.category in _CRITICAL_FOOTPRINT_CATEGORIES
        issues.append(
            VerifierIssue(
                id=f"PCB_R07:{pc.refdes}",
                severity="error" if critical else "warning",
                message=(
                    f"{pc.refdes} ({pc.mpn}): no verified KiCad footprint — "
                    f"'{pc.footprint.name}' is {pc.footprint.confidence} geometry, "
                    f"not fab-final. Check the part's footprint id maps to a real "
                    f"KiCad library footprint."
                ),
                refdes=pc.refdes,
                rule="PCB_R07_unverified_footprint",
            )
        )
    return issues


# How far a connector body may sit from the outline and still be pluggable.
# A USB-C shell overhangs the edge; a screw terminal's mouth must face out.
_EDGE_ACCESS_MM = 1.5


def PCB_R09_connector_edge_access(board: Board) -> list[VerifierIssue]:
    """A physical port must reach the board EDGE.

    "A USB-C receptacle stranded in open board area with no edge access" — and
    the layout review scored the board 100 % on edge fit, because the test was
    "is every part inside the outline", which a marooned connector passes
    perfectly (2026-08-20 review, findings 5 and 9). Containment is not access:
    this measures the gap between the connector body and the nearest edge."""
    issues: list[VerifierIssue] = []
    for pc in board.components:
        part = library.get_part(pc.mpn)
        if part is None or (part.category or "").lower() != "connector":
            continue
        if not _is_edge_port(part):
            continue
        x0, y0, x1, y1 = _body_bbox(pc)
        if board.shape == "round":
            r = board.width / 2.0
            cx = cy = r
            far = max(
                ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
                for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
            )
            gap = r - far
        else:
            gap = min(x0, y0, board.width - x1, board.height - y1)
        if gap <= _EDGE_ACCESS_MM:
            continue
        issues.append(VerifierIssue(
            id=f"PCB_R09:{pc.refdes}",
            severity="warning",
            message=(
                f"{pc.refdes} ({pc.mpn}) is a port but sits {gap:.1f} mm inside the board — "
                f"nothing can plug into it there. Drag it to an edge (or re-run auto-place, "
                f"which seats connectors on the rim)."),
            refdes=pc.refdes,
            rule="PCB_R09_connector_edge_access",
        ))
    return issues


def board_sync_issues(project: Project) -> list[VerifierIssue]:
    """The PCB must contain exactly the schematic's components. A board that
    predates the last schematic edit silently shows the OLD layout -- beta
    review #5 saw a PCB with one connector on it while the schematic held the
    whole alarm, and read it as "the tool cannot place parts". Error, with the
    fix named: re-run auto-place."""
    board = project.board
    if board is None:
        return []
    ir_refs = {c.refdes for c in project.ir.components}
    board_refs = {pc.refdes for pc in board.components}
    missing = sorted(ir_refs - board_refs)
    extra = sorted(board_refs - ir_refs)
    if not missing and not extra:
        return []
    bits = []
    if missing:
        bits.append(f"{len(missing)} schematic part(s) are not on the board "
                    f"({', '.join(missing[:6])}{'…' if len(missing) > 6 else ''})")
    if extra:
        bits.append(f"{len(extra)} board part(s) are no longer in the schematic "
                    f"({', '.join(extra[:6])}{'…' if len(extra) > 6 else ''})")
    return [VerifierIssue(
        id="PCB_R08:sync",
        severity="error",
        message=(
            f"The PCB is out of date with the schematic: {'; '.join(bits)}. "
            f"Re-run auto-place so every component gets a footprint on the board."
        ),
        rule="PCB_R08_board_matches_schematic",
        source="pcb",
    )]


def PCB_R10_mixed_assembly(board: Board) -> list[VerifierIssue]:
    """A board with BOTH surface-mount and through-hole parts needs two
    assembly processes, and every assembly quote is priced that way.

    Reviewers on 2026-08-20 asked what a board would actually cost to have
    built and found nothing in the tool that said so — a design mixing four
    THT parts into an otherwise SMT board is roughly twice the setup, and on
    most services the THT parts are hand-soldered at a per-joint rate. This is
    advisory: mixed assembly is a legitimate, extremely common choice (every
    board with a THT connector is one). It exists so the cost is VISIBLE
    before the order button, not after the quote."""
    from app.services import footprints as _fp_mod

    smt: list[str] = []
    tht: list[str] = []
    for pc in board.components:
        part = library.get_part(pc.mpn)
        if part is None:
            continue
        cat = (part.category or "").lower()
        if cat in ("mounting", "testpoint", "fiducial"):
            continue
        fp = _fp_mod.for_part(part)
        if fp is None or not fp.pads:
            continue
        if any((pad.pad_type or "").lower() == "tht" for pad in fp.pads):
            tht.append(pc.refdes)
        else:
            smt.append(pc.refdes)
    if not smt or not tht:
        return []
    return [VerifierIssue(
        id="PCB_R10:mixed",
        severity="info",
        message=(
            f"Mixed assembly: {len(smt)} surface-mount and {len(tht)} through-hole "
            f"part{'s' if len(tht) != 1 else ''} ({', '.join(sorted(tht)[:6])}"
            f"{', …' if len(tht) > 6 else ''}). Assembly houses charge these as two "
            f"processes — the through-hole parts are usually hand-soldered per joint. "
            f"That is normal for connectors; if you want a single-process quote, ask "
            f"for surface-mount equivalents."
        ),
        rule="PCB_R10_mixed_assembly",
        source="pcb",
    )]


ALL_RULES = [
    PCB_R01_component_overlap,
    PCB_R02_off_board,
    PCB_R03_unrouted_nets,
    PCB_R04_trace_width,
    PCB_R05_drill_size,
    PCB_R06_pad_in_component,
    PCB_R07_unverified_footprint,
    PCB_R09_connector_edge_access,
    PCB_R10_mixed_assembly,
]


def run_all(board: Board | None) -> list[VerifierIssue]:
    if board is None:
        return []
    issues: list[VerifierIssue] = []
    for rule in ALL_RULES:
        issues.extend(rule(board))
    return issues


def merge_into_project(project: Project) -> None:
    """Run PCB DRC and merge results into project.issues, replacing any
    existing PCB_* entries (so successive runs don't accumulate stale ones)."""
    pcb_issues = run_all(project.board) + board_sync_issues(project)
    project.issues = [
        i for i in project.issues if not (i.rule and i.rule.startswith("PCB_"))
    ]
    project.issues.extend(pcb_issues)
