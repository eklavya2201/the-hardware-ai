"""Mechanical DRC — Week-4 ECAD task 6.2 (the mechanical R01–R23).

Mirrors `verifier.py`: each rule is a pure `Project -> list[VerifierIssue]`
function with a stable `MECH_R0x` id and `source="mechanical"`. These catch the
"physically un-buildable" defects a board can have even when the electrical/PCB
DRC is clean — lid won't close, board doesn't fit its outline, a screw lands on a
part, a connector cutout no longer lines up with its connector.

Runs ALONGSIDE the electrical (`R01`–`R35`), PCB (`PCB_R0x`), KiCad (`KICAD_*`)
and ERC/IPC rules — `merge_into_project` replaces only prior `MECH_*` rows and
leaves every other diagnostic untouched, exactly like `kicad_drc.merge_into_
project`. Deliberately does NOT feed the agent fix-up loop (there is no schematic
tool that resolves a mechanical defect — the agent excludes `MECH_*`, same call
as the IPC / R35 rules); the fixes are mechanical-IR or placement edits.

Advisory-vs-error split follows the engineering-rules precedent: hard geometric
impossibilities (board off its outline, part taller than the cavity, cutout on
the wrong wall) are **errors**; best-practice margins (a thin-but-printable wall,
a screw boss grazing a part) are **warnings**. Coordinates are mm in the IR frame
(Y-up, origin bottom-left), same as the Board and the mechanical IR.
"""

from __future__ import annotations

from app.models import Project, VerifierIssue
from app.services import board_shape, geometry
from app.services import mechanical_derive

# Board substrate thickness the enclosure headroom is measured on top of — matches
# mechanical_bridge.BOARD_THICKNESS (kept local so this stays import-light).
_BOARD_THICKNESS = 1.6

# Minimum enclosure wall thickness (mm). Below `_WALL_MIN_HARD` the wall is
# physically unmakeable on any common process (FDM/SLA/injection) — an error;
# between that and `_WALL_MIN_REC` it prints but is fragile — a warning. A tiny
# inline "profile" in the fab_profiles.py spirit; promote to a module if more
# processes need distinct minimums.
_WALL_MIN_HARD = 0.8
_WALL_MIN_REC = 1.5

# Assembly gap: a part this close to (or past) the lid/floor is flagged. The
# cavity must clear the tallest part by at least this margin (mm).
_ASSEMBLY_GAP = 0.5

# Edge margin: a placed part whose courtyard extends beyond the outline by more
# than this is "off the board" (mm of slop tolerated for rounding).
_OUTLINE_SLOP = 0.2

# Physical-fit questions measure the body, not the courtyard (see _body_bbox).
_component_body_bbox = mechanical_derive._body_bbox


def _outline_extent(project: Project) -> tuple[float, float] | None:
    """Physical outline (w, h) — the mechanical outline if set, else the board."""
    mech = project.mechanical
    if mech is not None and mech.outline is not None:
        return (mech.outline.width, mech.outline.height)
    if project.board is not None:
        return (project.board.width, project.board.height)
    return None


def _outline_polygon(project: Project):
    """The mechanical outline as a ring when it is a polygon, else None."""
    mech = project.mechanical
    if mech is not None and mech.outline is not None:
        if mech.outline.shape == "polygon" and len(mech.outline.points or []) >= 3:
            return [(p.x, p.y) for p in mech.outline.points]
    if project.board is not None:
        return board_shape.ring_for_board(project.board) if \
            project.board.shape == "polygon" else None
    return None


def _outline_circle(project: Project) -> tuple[float, float, float] | None:
    """(cx, cy, r) when the physical outline is a circle, else None. The bbox
    checks alone would pass a part tucked into the square's corner — which on
    a round board is empty air past the milled edge."""
    mech = project.mechanical
    if mech is not None and mech.outline is not None:
        if mech.outline.shape == "circle":
            r = mech.outline.width / 2.0
            return (r, r, r)
        return None
    if project.board is not None and project.board.shape == "round":
        r = project.board.width / 2.0
        return (r, r, r)
    return None


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def MECH_R01_board_fits_outline(project: Project) -> list[VerifierIssue]:
    """Every placed part must lie within the physical board outline. An outline
    smaller than the placement extent means parts hang off the board — the
    enclosure (which wraps the outline) then can't contain them. Hard error."""
    board = project.board
    ext = _outline_extent(project)
    if board is None or not board.components or ext is None:
        return []
    w, h = ext
    circle = _outline_circle(project)
    poly = _outline_polygon(project)
    issues: list[VerifierIssue] = []
    for pc in board.components:
        x0, y0, x1, y1 = _component_body_bbox(pc)
        off = (
            x0 < -_OUTLINE_SLOP or y0 < -_OUTLINE_SLOP
            or x1 > w + _OUTLINE_SLOP or y1 > h + _OUTLINE_SLOP
        )
        if not off and poly is not None:
            # Polygon outline: a body inside the bounding box can still hang off
            # the milled edge — the notches of an X and the corners of a
            # triangle are exactly where the placer likes to tuck a capacitor.
            off = any(
                board_shape.clearance(poly, px, py) < -_OUTLINE_SLOP
                for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
            )
        if not off and circle is not None:
            # Round outline: every body corner must lie inside the disc.
            cx, cy, r = circle
            off = any(
                ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 > r + _OUTLINE_SLOP
                for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
            )
        if off:
            desc = f"Ø{w:.1f} mm round" if circle is not None else f"{w:.1f}×{h:.1f} mm"
            issues.append(VerifierIssue(
                id=f"MECH_R01:{pc.refdes}",
                severity="error",
                message=(
                    f"{pc.refdes} extends past the board outline "
                    f"({desc}) — enlarge the outline or move the part "
                    f"inboard so the enclosure can contain it."
                ),
                refdes=pc.refdes,
                rule="MECH_R01",
                source="mechanical",
            ))
    return issues


def MECH_R02_part_to_lid(project: Project) -> list[VerifierIssue]:
    """No part may be taller than the cavity that covers it. Top-side parts vs
    `clearance_top`, bottom-side parts/leads vs `clearance_bottom` (both measured
    from the board surface). Taller-than-cavity is a hard error (lid won't close);
    within the assembly gap is a warning."""
    mech = project.mechanical
    if mech is None or mech.enclosure.kind == "none":
        return []
    enc = mech.enclosure
    issues: list[VerifierIssue] = []
    for layer, clearance, where in (
        ("top", enc.clearance_top, "lid"),
        ("bottom", enc.clearance_bottom, "floor"),
    ):
        h, ref = mechanical_derive.tallest_on_side(project, layer)
        if ref is None:
            continue
        if h > clearance:
            issues.append(VerifierIssue(
                id=f"MECH_R02:{layer}",
                severity="error",
                message=(
                    f"Tallest {layer}-side part {ref} is {h:.1f} mm but the "
                    f"enclosure only allows {clearance:.1f} mm to the {where} — "
                    f"the case won't close. Raise clearance_{ 'top' if layer=='top' else 'bottom'} "
                    f"or use a lower-profile part."
                ),
                refdes=ref,
                rule="MECH_R02",
                source="mechanical",
            ))
        elif clearance - h < _ASSEMBLY_GAP:
            issues.append(VerifierIssue(
                id=f"MECH_R02:{layer}",
                severity="warning",
                message=(
                    f"Tallest {layer}-side part {ref} ({h:.1f} mm) leaves under "
                    f"{_ASSEMBLY_GAP:.1f} mm to the {where} ({clearance:.1f} mm cavity) "
                    f"— add assembly clearance."
                ),
                refdes=ref,
                rule="MECH_R02",
                source="mechanical",
            ))
    return issues


def MECH_R03_wall_thickness(project: Project) -> list[VerifierIssue]:
    """Enclosure wall must be thick enough for the process. Below the hard
    minimum it can't be made (error); below the recommended minimum it's fragile
    (warning)."""
    mech = project.mechanical
    if mech is None or mech.enclosure.kind == "none":
        return []
    t = mech.enclosure.wall_thickness
    if t < _WALL_MIN_HARD:
        return [VerifierIssue(
            id="MECH_R03",
            severity="error",
            message=(
                f"Enclosure wall {t:.2f} mm is below the {_WALL_MIN_HARD:.1f} mm "
                f"process minimum — it can't be manufactured. Thicken the wall."
            ),
            rule="MECH_R03",
            source="mechanical",
        )]
    if t < _WALL_MIN_REC:
        return [VerifierIssue(
            id="MECH_R03",
            severity="warning",
            message=(
                f"Enclosure wall {t:.2f} mm is under the recommended "
                f"{_WALL_MIN_REC:.1f} mm — printable but fragile."
            ),
            rule="MECH_R03",
            source="mechanical",
        )]
    return []


def MECH_R04_mounting_hole_collision(project: Project) -> list[VerifierIssue]:
    """Mounting holes (and their standoff bosses) must be inside the outline and
    clear of placed parts. A hole outside the outline is an error; a boss
    overlapping a part courtyard is a warning (resolvable by moving the hole)."""
    mech = project.mechanical
    board = project.board
    if mech is None or not mech.mountingHoles or board is None:
        return []
    ext = _outline_extent(project)
    issues: list[VerifierIssue] = []
    circle = _outline_circle(project)
    for hole in mech.mountingHoles:
        if ext is not None:
            w, h = ext
            outside = not (0 <= hole.x <= w and 0 <= hole.y <= h)
            if not outside and circle is not None:
                cx, cy, r = circle
                # The whole drilled hole must stay inside the milled circle.
                outside = (
                    ((hole.x - cx) ** 2 + (hole.y - cy) ** 2) ** 0.5
                    > r - hole.diameter / 2.0
                )
            if outside:
                desc = f"Ø{w:.1f} mm round" if circle is not None else f"{w:.1f}×{h:.1f} mm"
                issues.append(VerifierIssue(
                    id=f"MECH_R04:{hole.id}:outline",
                    severity="error",
                    message=(
                        f"Mounting hole at ({hole.x:.1f}, {hole.y:.1f}) is outside "
                        f"the {desc} outline."
                    ),
                    rule="MECH_R04",
                    source="mechanical",
                ))
                continue
        # Boss-vs-part: reuse the deriver's exact clearance test so a hole the
        # deriver placed clear always passes here (same circle, same radius).
        for pc in board.components:
            if not mechanical_derive._hole_clear(hole.x, hole.y, [_component_body_bbox(pc)]):
                issues.append(VerifierIssue(
                    id=f"MECH_R04:{hole.id}:{pc.refdes}",
                    severity="warning",
                    message=(
                        f"Mounting hole/standoff at ({hole.x:.1f}, {hole.y:.1f}) "
                        f"is within {mechanical_derive.MOUNT_CLEAR_R:.0f} mm of {pc.refdes} "
                        f"— the screw boss may hit the part. Move the hole or add a keep-out."
                    ),
                    refdes=pc.refdes,
                    rule="MECH_R04",
                    source="mechanical",
                ))
                break  # one warning per hole is enough
    return issues


def MECH_R05_connector_alignment(project: Project) -> list[VerifierIssue]:
    """Each connector cutout must still line up with its connector: the connector
    must be placed, on the wall the cutout names, and its along-wall position must
    fall within the opening. Catches the "port is off its hole" class after a
    connector or cutout is moved. Hard error (the case won't mate)."""
    mech = project.mechanical
    board = project.board
    if mech is None or not mech.cutouts or board is None:
        return []
    ext = _outline_extent(project)
    if ext is None:
        return []
    w, h = ext
    by_ref = {pc.refdes: pc for pc in board.components}
    issues: list[VerifierIssue] = []
    for cut in mech.cutouts:
        if cut.refdes is None:
            continue  # a generic opening not tied to a part — nothing to align to
        pc = by_ref.get(cut.refdes)
        if pc is None:
            issues.append(VerifierIssue(
                id=f"MECH_R05:{cut.id}:missing",
                severity="error",
                message=(
                    f"Cutout references {cut.refdes}, which isn't placed on the "
                    f"board — remove the cutout or place the connector."
                ),
                refdes=cut.refdes,
                rule="MECH_R05",
                source="mechanical",
            ))
            continue
        exp_edge, _ = mechanical_derive._nearest_edge(pc.x, pc.y, w, h)
        if cut.edge != exp_edge:
            issues.append(VerifierIssue(
                id=f"MECH_R05:{cut.id}:edge",
                severity="error",
                message=(
                    f"Cutout for {cut.refdes} is on the {cut.edge} wall but the "
                    f"connector now sits nearest the {exp_edge} edge — the port "
                    f"won't line up. Re-derive the enclosure or move the connector."
                ),
                refdes=cut.refdes,
                rule="MECH_R05",
                source="mechanical",
            ))
            continue
        # Along-wall alignment: the connector's centre parallel to the wall must
        # fall inside the opening span [start, start+width]. Same anchor as the
        # deriver — the measured 3D-body centre (pad-centre/origin fallback).
        pcx, pcy = mechanical_derive._connector_anchor_world(pc)
        if cut.edge in ("left", "right"):
            center, start = pcy, cut.y
        else:
            center, start = pcx, cut.x
        if not (start - 0.5 <= center <= start + cut.width + 0.5):
            issues.append(VerifierIssue(
                id=f"MECH_R05:{cut.id}:offset",
                severity="error",
                message=(
                    f"Cutout for {cut.refdes} no longer aligns with the connector "
                    f"(off by {min(abs(center - start), abs(center - start - cut.width)):.1f} mm "
                    f"along the {cut.edge} wall) — re-derive the enclosure."
                ),
                refdes=cut.refdes,
                rule="MECH_R05",
                source="mechanical",
            ))
    return issues


def MECH_R06_panel(project: Project) -> list[VerifierIssue]:
    """The panel must be one a fab can actually build.

    Panelization is where a design meets the machine that separates it, and
    every failure here is expensive and late: a V-score line that crosses a
    component, a panel too big for the fab's frame, a round board someone tried
    to score. Each check names the physical reason, because "invalid panel" is
    not something a user can act on.
    """
    from app.services import panelize

    mech = project.mechanical
    board = project.board
    if mech is None or mech.panel is None or board is None:
        return []
    spec = mech.panel
    issues: list[VerifierIssue] = []

    def add(key: str, severity: str, message: str) -> None:
        issues.append(VerifierIssue(
            id=f"MECH_R06:{key}", severity=severity, message=message,
            rule="MECH_R06_panel", source="mechanical",
        ))

    panel = panelize.derive(board, spec)

    # 1. V-scoring is a straight blade across the whole panel.
    if spec.method == "v_score" and board.shape != "rect":
        add("shape", "error",
            f"This board is {board.shape}, so it cannot be V-scored — the blade cuts a "
            f"straight line across the whole panel and would run through the board. Use "
            f"mouse-bite tabs, which route around any outline.")

    # 2. Nothing may sit on or beside a score line.
    if spec.method == "v_score" and board.shape == "rect":
        near: list[str] = []
        for pc in board.components:
            for x0, y0, x1, y1 in geometry.solid_rects(pc):
                if (min(x0, board.width - x1) < panelize.VSCORE_KEEPOUT
                        or min(y0, board.height - y1) < panelize.VSCORE_KEEPOUT):
                    near.append(pc.refdes)
                    break
        if near:
            add("keepout", "error",
                f"{', '.join(sorted(set(near))[:6])} sit{'s' if len(set(near)) == 1 else ''} "
                f"within {panelize.VSCORE_KEEPOUT:g} mm of the board edge, which is where the "
                f"V-score groove runs. The board flexes when it is snapped apart, so parts that "
                f"close to the edge crack or shear off. Move them inboard, or switch to "
                f"mouse-bite tabs.")

    # 3. The panel has to fit the fab's frame.
    if panel.width > panelize.MAX_PANEL_W or panel.height > panelize.MAX_PANEL_H:
        add("size", "error",
            f"The panel is {panel.width:.0f} x {panel.height:.0f} mm, past the "
            f"{panelize.MAX_PANEL_W:.0f} x {panelize.MAX_PANEL_H:.0f} mm most low-cost fabs "
            f"accept without a surcharge. Use fewer boards per panel.")

    # 4. Rails carry the tooling holes and fiducials, so they need real width.
    if 0 < spec.rail < panelize.MIN_RAIL:
        add("rail", "warning",
            f"A {spec.rail:g} mm rail is too narrow for the {panelize.TOOLING_HOLE_D:g} mm "
            f"tooling holes it has to carry — {panelize.MIN_RAIL:g} mm is what fabs ask for.")
    elif spec.rail == 0 and board.width < panelize.SMALL_BOARD_MM:
        add("rail", "warning",
            f"This panel has no breakaway rails, so there is nowhere to put tooling holes or "
            f"fiducials. An assembly house needs both to align a {board.width:.0f} mm board. "
            f"Add a 5 mm rail unless you are ordering bare boards.")

    # 5. Mouse bites need a slot wide enough for the router bit.
    if spec.method == "mouse_bites" and spec.gap and spec.gap < panelize.MIN_ROUTE_GAP:
        add("gap", "warning",
            f"A {spec.gap:g} mm gap is narrower than the {panelize.MIN_ROUTE_GAP:g} mm router "
            f"slot mouse-biting needs; the panel will be built with {panel.gap:g} mm instead.")

    # 6. Say when the panel is mostly waste — this is the number people order on.
    if panel.boards > 1 and panel.utilisation < 0.5:
        add("waste", "info",
            f"Only {panel.utilisation * 100:.0f}% of this panel is finished board — the rest is "
            f"rail and routing slot that you pay for and throw away. A larger array or a "
            f"narrower rail would improve it.")

    return issues


def MECH_R07_host_board_clearance(project: Project) -> list[VerifierIssue]:
    """A board that stacks on a host must leave room for the host underneath.

    A Raspberry Pi HAT sits on top of a whole computer, and the official HAT
    specification requires the GPIO connector to space it "at least 8mm from
    the Pi". Nothing is ever placed on a board's bottom side, so the derived
    floor gap came out at its 3 mm minimum and an enclosure for a HAT closed on
    the Pi (2026-08-20).
    """
    from app.services import mechanical_derive

    mech = project.mechanical
    if mech is None or mech.enclosure is None or project.board is None:
        return []
    host = mechanical_derive.host_board_clearance(project)
    if host is None:
        return []
    need, why = host
    have = mech.enclosure.clearance_bottom
    if have >= need - 0.05:
        return []
    return [VerifierIssue(
        id="MECH_R07:host",
        severity="error",
        message=(
            f"The enclosure leaves {have:g} mm under this board, but {why}. "
            f"Raise the floor clearance to at least {need:g} mm."
        ),
        rule="MECH_R07_host_board_clearance",
        source="mechanical",
    )]


def MECH_R08_mounting_pattern(project: Project) -> list[VerifierIssue]:
    """A board built to a mounting standard must still MATCH that standard.

    An FPV flight controller mates with its frame through four screws on a
    published pitch (30.5, 25.5, 20 or 16 mm square). Unlike a HAT, nothing
    physical on the board enforces it: there is no header to lock, so the
    pattern is four holes that anything downstream can move, and a board that
    grows during placement leaves them off-centre. A 0.5 mm error is a board
    that will not bolt down — the frame's screws are already in the frame.

    Fires only when the design RECORDED a pattern standard (the note the form
    factor writes), so an ordinary board with four corner holes is never
    judged against a pattern it never claimed.
    """
    from app.services import form_factors

    mech = project.mechanical
    if mech is None or project.board is None:
        return []
    spec = project.ir.form_factor
    if spec is None or not spec.note:
        return []
    ff = next(
        (f for f in form_factors.FORM_FACTORS.values()
         if f.hole_pitch is not None and f.title in spec.note),
        None,
    )
    if ff is None:
        return []

    want = form_factors.pattern_holes(ff, project.board.width, project.board.height)
    holes = list(mech.mountingHoles or [])
    issues: list[VerifierIssue] = []
    px, py = ff.hole_pitch  # type: ignore[misc]

    # Every spec position needs a hole within TOL, and it must take the screw.
    TOL = 0.25
    missing: list[str] = []
    for wx, wy in want:
        near = min(
            holes, key=lambda h: (h.x - wx) ** 2 + (h.y - wy) ** 2
        ) if holes else None
        if near is None:
            missing.append(f"({wx:g}, {wy:g})")
            continue
        off = ((near.x - wx) ** 2 + (near.y - wy) ** 2) ** 0.5
        if off > TOL:
            missing.append(f"({wx:g}, {wy:g}) — nearest hole is {off:.2f} mm away")
        elif near.diameter + 1e-6 < ff.hole_diameter:
            issues.append(VerifierIssue(
                id=f"MECH_R08:dia:{wx:g},{wy:g}",
                severity="error",
                message=(
                    f"The mounting hole at ({wx:g}, {wy:g}) is "
                    f"{near.diameter:g} mm, too small for the {ff.standoff} screw "
                    f"this pattern uses (needs {ff.hole_diameter:g} mm)."
                ),
                rule="MECH_R08_mounting_pattern",
                source="mechanical",
            ))
    if missing:
        issues.append(VerifierIssue(
            id="MECH_R08:pattern",
            severity="error",
            message=(
                f"This board claims the {px:g} x {py:g} mm {ff.standoff} mounting "
                f"pattern, but {len(missing)} of its four screw positions are not "
                f"drilled where the frame expects them: {'; '.join(missing[:4])}. "
                f"Re-apply the {ff.name} form factor so the pattern is re-centred "
                f"on the board as built."
            ),
            rule="MECH_R08_mounting_pattern",
            source="mechanical",
        ))
    return issues[:5]


ALL_RULES = [
    MECH_R01_board_fits_outline,
    MECH_R02_part_to_lid,
    MECH_R03_wall_thickness,
    MECH_R04_mounting_hole_collision,
    MECH_R05_connector_alignment,
    MECH_R06_panel,
    MECH_R07_host_board_clearance,
    MECH_R08_mounting_pattern,
]

# Defensive cap so a pathological design can't flood the Review panel; mechanical
# rules realistically emit a handful, but mirror the kicad_drc rollup discipline.
MAX_ISSUES = 40


def run(project: Project) -> list[VerifierIssue]:
    """Run every mechanical rule and return the issues (capped + rolled up).
    Returns [] when there's no mechanical IR to check. Never raises."""
    if project.mechanical is None:
        return []
    issues: list[VerifierIssue] = []
    for rule in ALL_RULES:
        try:
            issues.extend(rule(project))
        except Exception:  # noqa: BLE001 — a buggy rule must never break the panel
            continue
    if len(issues) > MAX_ISSUES:
        extra = len(issues) - MAX_ISSUES
        issues = issues[:MAX_ISSUES]
        issues.append(VerifierIssue(
            id="MECH_summary",
            severity="warning",
            message=f"Mechanical DRC — {extra} more issue(s) not shown.",
            rule="MECH_summary",
            source="mechanical",
        ))
    return issues


def merge_into_project(project: Project) -> int:
    """Run mechanical DRC and merge into `project.issues`, replacing any prior
    `MECH_*` rows (so re-runs don't accumulate stale ones) while leaving every
    other diagnostic (`R*`, `PCB_*`, `KICAD_*`, ERC/IPC) untouched. Returns the
    number of mechanical issues after the run."""
    issues = run(project)
    project.issues = [
        i for i in project.issues if not (i.rule and i.rule.startswith("MECH_"))
    ]
    project.issues.extend(issues)
    return len(issues)
