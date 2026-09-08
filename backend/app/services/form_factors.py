"""Standard board form factors — Week-5 robotics task 7.3.

A form factor is a published mechanical standard a board must match to mate
with its host: the Raspberry Pi HAT (65 x 56.5 mm, 4 x M2.5, the 40-pin header
on the top-hole centreline) and the Arduino Uno shield (the four header banks
of the Uno pin map). Applying one turns the standard into FIXED layout
constraints the rest of the pipeline already honours:

  - the connector part is added to the electrical IR through the normal
    `add_component` tool (anti-hallucination guard applies),
  - the Board is sized to the spec and the connector is placed at its exact
    spec position and **locked** — `placement.auto_place` treats locked parts
    as fixed anchors and reuses the board dimensions, so re-running
    auto-place/route can never move the header or resize the board,
  - the mechanical IR gets the spec outline (rounded corners) and the spec
    mounting-hole pattern through `mechanical_tools` (revertable commit,
    mechanical DRC re-runs on it).

Anchor placement is computed from the part's *real verified footprint* (the
pad-field centre must land on the spec point), not hand-typed origins — the
same "measured truth" rule as the footprint/3D layers.

Only fully-verified numbers are encoded. The HAT dimensions are the published
HAT mechanical spec. For the shield, what mates is the header pattern (exact,
from the verified KiCad Uno module footprint) and the row-to-edge distance
(100 mil); the outline's X registration is nominal and the description says
so — shields are retained by their headers, so no hole pattern is emitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import Board, DiffEntry, FormFactorSpec, PlacedComponent, Project
from app.services import footprints, geometry, library


@dataclass(frozen=True)
class FormFactor:
    name: str
    title: str
    description: str
    board_w: float
    board_h: float
    corner_radius: float
    # A standard can mate two ways. A HAT/shield mates through a CONNECTOR, so
    # the anchor part is the standard and the outline follows it. An FPV flight
    # controller mates through its MOUNTING PATTERN — there is no mandatory
    # board-to-board header, the stack is joined by screws and a loose cable —
    # so the anchor is absent and the hole pitch is the standard.
    anchor_mpn: str | None = None   # the standard header part (must be in the library)
    anchor_rotation: int = 0        # 0/90/180/270, IR CCW convention
    anchor_pad_center: tuple[float, float] = (0.0, 0.0)  # spec point the pad-field centre lands on
    holes: list[tuple[float, float]] = field(default_factory=list)
    # Centre-relative alternative to `holes`: four holes at board centre +/-
    # pitch/2. Absolute coordinates are wrong for a pattern standard — the
    # outline is a default the placer may grow, and a pattern that stays put
    # while the board grows around it ends up off-centre and unmountable.
    hole_pitch: tuple[float, float] | None = None
    hole_diameter: float = 2.75
    standoff: str | None = None
    # False when the outline is a sensible default rather than part of the
    # standard (FC stacks standardise the pattern, not the board edge). Such a
    # form factor records no width/height in FormFactorSpec, so R57 does not
    # report a legitimate growth as a defect.
    outline_is_spec: bool = True
    # Returned in the apply() result so the agent wires the header's power
    # pins — a HAT/shield is POWERED through its header; live-testing showed
    # the model happily leaves the rails floating without this nudge (and the
    # dead board only surfaced as R26/R42 warnings).
    wiring_guide: str = ""


FORM_FACTORS: dict[str, FormFactor] = {
    # Raspberry Pi HAT mechanical spec: 65 x 56.5 mm board, 3.5 mm corner
    # radius, 4 x M2.5 holes (dia 2.75) at 3.5/61.5 x 3.5/52.5, and the 40-pin
    # header centred between the two top holes on their centreline (32.5, 52.5).
    "rpi_hat": FormFactor(
        name="rpi_hat",
        title="Raspberry Pi HAT",
        description=(
            "The Raspberry Pi HAT form factor: 65 x 56.5 mm outline with 3.5 mm "
            "corner radius, four M2.5 mounting holes (dia 2.75 mm) at the spec "
            "positions, and the 40-pin GPIO header (RPi_HAT_Header_40P) locked on "
            "the top-hole centreline so it mates with the Pi. Auto-place/route "
            "treat the header and board size as fixed."
        ),
        board_w=65.0,
        board_h=56.5,
        corner_radius=3.5,
        anchor_mpn="RPi_HAT_Header_40P",
        anchor_rotation=90,  # 2x20 footprint's long axis is Y; the HAT header runs along X
        anchor_pad_center=(32.5, 52.5),
        holes=[(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)],
        hole_diameter=2.75,
        standoff="M2.5",
        wiring_guide=(
            "The Pi POWERS the HAT through this header — wire the rails NOW or "
            "the board is dead: connect your 3.3 V rail (e.g. VCC_3V3) to J1 "
            "pin 1 '3V3' (pin 17 '3V3_2' also available); a 5 V rail to J1 "
            "pin 2 '5V' / pin 4 '5V_2' if anything needs 5 V; and GND to at "
            "least one of the GND pins (6, 9, 14, 20, 25, 30, 34, 39). "
            "I2C1 is pins 3/5 (GPIO2_SDA1/GPIO3_SCL1)."
        ),
    ),
    # Arduino Uno shield: 68.58 x 53.34 mm (2700 x 2100 mil) outline. The header
    # bank geometry comes from the verified KiCad Uno module footprint (exact);
    # rows sit 100 mil (2.54 mm) from the top/bottom edges per the Uno drawing.
    # X registration of the outline is nominal (centred on the pad field) —
    # shields are retained by their headers, so no mounting holes are emitted.
    "arduino_shield": FormFactor(
        name="arduino_shield",
        title="Arduino Uno shield",
        description=(
            "The Arduino Uno R3 shield form factor: 68.58 x 53.34 mm outline with "
            "the four header banks (Arduino_Shield_Headers) locked at the Uno pin "
            "map so the shield mates with any Uno-compatible base board. Header "
            "rows sit 2.54 mm from the long edges (spec); the outline's X "
            "registration is nominal. No mounting holes - shields are retained by "
            "their headers."
        ),
        board_w=68.58,
        board_h=53.34,
        corner_radius=1.0,
        anchor_mpn="Arduino_Shield_Headers",
        anchor_rotation=0,
        # Pad rows are 48.26 mm apart; centring the field at (53.34/2) puts them
        # at y = 2.54 and 50.80 — exactly 100 mil off each edge, per the spec.
        anchor_pad_center=(68.58 / 2.0, 53.34 / 2.0),
        holes=[],
        standoff=None,
        wiring_guide=(
            "The base board POWERS the shield through these headers — wire the "
            "rails NOW or the shield is dead: connect a 5 V rail to the '5V' "
            "pin (pin 5), a 3.3 V rail to '3V3' (pin 4) if needed, and GND to "
            "GND1/GND2/GND3 (pins 6, 7, 29). I2C is on A4_SDA/A5_SCL (also "
            "duplicated on the SDA/SCL pins 31/32); SPI on D10-D13."
        ),
    ),
}


# --- FPV flight-controller stack patterns ------------------------------------
# What a drone board must match to bolt into a frame. The PATTERN is the
# standard (30.5x30.5, 25.5x25.5, 20x20 and 16x16 mm are the four in use;
# 5"/7" quads run 30.5, smaller craft 25.5/20/16) — screws are M3 on the
# 30.5 pattern and M2 on the smaller ones.
#
# The board OUTLINE is deliberately NOT claimed as spec: no standard fixes it,
# products vary, and `outline_is_spec=False` keeps R57 from calling a
# legitimate growth a defect. The default below is the smallest outline that
# leaves material around each boss — pattern + 2 x (hole radius + 1.15 mm edge)
# — which reproduces the 36 x 36 mm of a real full-size FC, a useful check that
# the derivation is sane rather than invented. Holes are centre-relative so the
# pattern stays centred when the placer grows the board.
#
# Hole diameters are ISO 273 close-fit clearance for the screw (M3 -> 3.2,
# M2 -> 2.2), not a number taken from any one product; boards sold with 4 mm
# holes are fitted for soft-mount grommets, which is a design choice on top.
def _fc_stack(pitch: float, screw: str, hole_d: float, sizes: str) -> FormFactor:
    edge = hole_d / 2.0 + 1.15
    side = round(pitch + 2 * edge, 2)
    key = f"fc_stack_{str(pitch).replace('.', '_')}"
    return FormFactor(
        name=key,
        title=f"FPV flight-controller stack {pitch}x{pitch} mm ({screw})",
        description=(
            f"The {pitch} x {pitch} mm {screw} flight-controller stack pattern — "
            f"the mounting standard for {sizes}. Four {screw} holes "
            f"(dia {hole_d} mm) centred on the board so it bolts to the frame "
            f"and stacks with an ESC/VTX of the same pattern. The pattern is "
            f"the standard; the {side} x {side} mm outline is the smallest that "
            f"leaves material around each boss and the placer may grow it. No "
            f"mating header: an FC stack is joined by screws and a cable."
        ),
        board_w=side,
        board_h=side,
        corner_radius=2.0,
        anchor_mpn=None,
        holes=[],
        hole_pitch=(pitch, pitch),
        hole_diameter=hole_d,
        standoff=screw,
        outline_is_spec=False,
        wiring_guide=(
            f"This board bolts into the frame on the {pitch} x {pitch} mm "
            f"pattern and carries no host header — it must bring its OWN power "
            f"in (battery/XT60 pads or an ESC connector), and keep the four "
            f"{screw} bosses clear of parts so the screws can pass."
        ),
    )


for _pitch, _screw, _dia, _sizes in (
    (30.5, "M3", 3.2, '5" and 7" quads — the full-size pattern'),
    (25.5, "M2", 2.2, "3-4\" and cinewhoop craft"),
    (20.0, "M2", 2.2, "2-3\" micro quads"),
    (16.0, "M2", 2.2, "tiny whoops and sub-2\" craft"),
):
    _ff = _fc_stack(_pitch, _screw, _dia, _sizes)
    FORM_FACTORS[_ff.name] = _ff


def get(name: str) -> FormFactor | None:
    return FORM_FACTORS.get(name)


def listing() -> list[dict]:
    return [
        {
            "name": ff.name,
            "title": ff.title,
            "description": ff.description,
            "board": [ff.board_w, ff.board_h],
            "anchor": ff.anchor_mpn,
            "kind": "form_factor",
        }
        for ff in FORM_FACTORS.values()
    ]


def _pad_center_local(fp) -> tuple[float, float]:
    """Centre of the footprint's pad field in its local frame. Pads are stored
    origin-relative (not centred), so this is the measured anchor datum."""
    if not fp.pads:
        return (0.0, 0.0)
    xs = [p.x for p in fp.pads]
    ys = [p.y for p in fp.pads]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _anchor_origin(ff: FormFactor, fp) -> tuple[float, float]:
    """Component origin such that the rotated pad-field centre lands on the
    spec point."""
    cx, cy = _pad_center_local(fp)
    rx, ry = geometry.rotate_xy(cx, cy, ff.anchor_rotation)
    return (ff.anchor_pad_center[0] - rx, ff.anchor_pad_center[1] - ry)


def pattern_holes(ff: FormFactor, board_w: float, board_h: float
                  ) -> list[tuple[float, float]]:
    """Where this standard's mounting holes go on a board of this size.

    Absolute for a CONNECTOR standard (the HAT pattern is fixed relative to the
    header it mates with); centre-relative for a PATTERN standard, because what
    the board must line up with is the frame's four screws, and those sit about
    its centre however large the board grows."""
    if ff.hole_pitch is None:
        return list(ff.holes)
    px, py = ff.hole_pitch
    cx, cy = board_w / 2.0, board_h / 2.0
    return [
        (round(cx - px / 2.0, 3), round(cy - py / 2.0, 3)),
        (round(cx + px / 2.0, 3), round(cy - py / 2.0, 3)),
        (round(cx - px / 2.0, 3), round(cy + py / 2.0, 3)),
        (round(cx + px / 2.0, 3), round(cy + py / 2.0, 3)),
    ]


def apply(project: Project, name: str) -> tuple[dict, DiffEntry]:
    """Apply a form factor: add + lock the standard header at its spec position,
    size the Board to the spec, and write the spec outline + hole pattern into
    the mechanical IR. Every mutation flows through the strict tool layers."""
    from app.services import mechanical_tools as mt
    from app.services import tools
    from app.services.tools import ToolError

    ff = get(name)
    if ff is None:
        raise ToolError(
            f"No form factor named {name!r}. Available: {', '.join(FORM_FACTORS)}."
        )
    # A pattern standard (an FC stack) has no mating part: the boards are
    # joined by screws and a loose cable, so there is nothing to add to the
    # schematic and nothing to lock.
    refdes: str | None = None
    placed: PlacedComponent | None = None
    if ff.anchor_mpn is not None:
        part = library.get_part(ff.anchor_mpn)
        if part is None:
            raise ToolError(
                f"Form-factor anchor part {ff.anchor_mpn!r} is not in the library."
            )

        # 1. The header part in the electrical IR (through the guarded tool).
        existing = next(
            (c for c in project.ir.components if c.mpn == ff.anchor_mpn), None
        )
        if existing is not None:
            refdes = existing.refdes
        else:
            res, _ = tools.add_component(project, mpn=ff.anchor_mpn)
            refdes = res["refdes"]

        # 2. The Board: spec dimensions + the header locked at its spec position.
        fp = footprints.for_part(part)
        ox, oy = _anchor_origin(ff, fp)
        reason = f"Fixed by the {ff.title} form-factor spec."
        placed = PlacedComponent(
            refdes=refdes,
            mpn=ff.anchor_mpn,
            x=round(ox, 2),
            y=round(oy, 2),
            rotation=ff.anchor_rotation,  # type: ignore[arg-type]
            footprint=fp,
            locked=True,
            placementReason=reason,
        )

    # When the outline is NOT the standard, an existing larger board is a
    # legitimate design (its parts need the room) and shrinking it to the
    # default would push them off the edge. The pattern re-centres either way.
    board_w, board_h = ff.board_w, ff.board_h
    if not ff.outline_is_spec and project.board is not None:
        board_w = max(board_w, project.board.width)
        board_h = max(board_h, project.board.height)

    if project.board is None:
        project.board = Board(
            width=board_w, height=board_h, shape="rect",
            components=[placed] if placed is not None else [],
        )
    else:
        project.board.width = board_w
        project.board.height = board_h
        project.board.shape = "rect"
        if placed is not None:
            others = [pc for pc in project.board.components if pc.refdes != refdes]
            project.board.components = [*others, placed]
        # Prior routing is stale against the new outline/anchor.
        project.board.traces = []
        project.board.vias = []
        project.board.rats = []
        project.board.auto_routed = False

    # 2b. RECORD the spec as the design's form factor. Without this, R57 never
    # guards a HAT/shield board — a later auto-place that grows the board past
    # the standard outline passes silently (review round 2026-08-20: an Uno
    # shield shipped at 94.5 x 100 mm, spec 68.58 x 53.34).
    project.ir.form_factor = FormFactorSpec(
        shape="rect",
        # Claim only the dimensions the standard actually fixes. An FC stack
        # standardises the hole pattern, so recording an outline here would make
        # R57 report a board that grew for good reason as a defect.
        width_mm=ff.board_w if ff.outline_is_spec else None,
        height_mm=ff.board_h if ff.outline_is_spec else None,
        note=(f"{ff.title} standard outline" if ff.outline_is_spec
              else f"{ff.title} mounting pattern"),
    )

    # 3. The mechanical IR: spec outline + spec hole pattern (strict tools).
    mt.set_board_outline(
        project,
        shape="rounded_rect",
        width=board_w,
        height=board_h,
        corner_radius=ff.corner_radius,
    )
    if project.mechanical is not None:
        project.mechanical.mountingHoles = []  # the form factor owns the pattern
    for hx, hy in pattern_holes(ff, board_w, board_h):
        mt.add_mounting_hole(
            project, x=hx, y=hy, diameter=ff.hole_diameter, standoff=ff.standoff
        )

    # A board that stacks on a HOST needs room for the host underneath it,
    # whether or not anyone builds an enclosure — a Raspberry Pi HAT sits on a
    # whole computer, and the HAT specification requires spacers of 8 mm or
    # larger. Recording it here means the default is right rather than being a
    # 3 mm floor gap that MECH_R07 then has to complain about.
    from app.services import mechanical_derive

    host = mechanical_derive.HOST_BOARD_CLEARANCE_MM.get(ff.anchor_mpn)
    if host is not None and project.mechanical is not None:
        cur = project.mechanical.enclosure
        if cur.clearance_bottom < host:
            mt.set_enclosure(project, kind=cur.kind, clearance_bottom=host)

    return (
        {
            "form_factor": ff.name,
            "board": [board_w, board_h],
            "anchor": None if placed is None else {
                "refdes": refdes,
                "mpn": ff.anchor_mpn,
                "x": placed.x,
                "y": placed.y,
                "rotation": ff.anchor_rotation,
                "locked": True,
            },
            "mounting_holes": len(pattern_holes(ff, board_w, board_h)),
            "REQUIRED_NEXT_STEP": (
                ff.wiring_guide.replace("J1", refdes) if refdes else ff.wiring_guide
            ) or None,
        },
        DiffEntry(
            kind="add",
            summary=(
                f"Applied {ff.title} form factor: {ff.board_w:g}x{ff.board_h:g} mm "
                f"board, {refdes} locked at spec position"
                + (f", {len(ff.holes)} mounting holes" if ff.holes else "")
            ),
        ),
    )
