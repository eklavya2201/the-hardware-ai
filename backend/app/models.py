"""Pydantic models matching the frontend IR (frontend/src/types/ir.ts) and PRD §9.2."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class Point(BaseModel):
    x: float
    y: float


PinSide = Literal["left", "right", "top", "bottom"]
PinType = Literal[
    "power_in",
    "power_out",
    "input",
    "output",
    "bidirectional",
    "passive",
    "ground",
    "unspecified",
]


class Pin(BaseModel):
    number: str
    name: str
    side: PinSide = "left"
    type: PinType = "passive"


class Component(BaseModel):
    refdes: str
    mpn: str
    value: str | None = None
    description: str | None = None
    footprint: str | None = None
    datasheetUrl: str | None = None
    x: float = 0
    y: float = 0
    rotation: Literal[0, 90, 180, 270] = 0
    pins: list[Pin] = Field(default_factory=list)


class Net(BaseModel):
    name: str
    pins: list[tuple[str, str]] = Field(default_factory=list)


class DesignRequirement(BaseModel):
    """A countable, user-stated requirement the design must satisfy — recorded by
    the agent via the `record_requirements` tool at the start of a build turn
    (e.g. "100x 3mm LED"). Deterministically checked against the IR by verifier
    rule R43, so "asked for 100, built 3" is a hard error, never a silent gap.
    Matching: `mpn` (exact, plus library `alternatives`) when set, else the
    library `category` of each placed component."""
    key: str                        # short stable id, e.g. "led_3mm"
    description: str                # human-readable, e.g. "3mm through-hole LED"
    quantity: int = 1               # minimum count the design must contain
    mpn: str | None = None          # exact MPN match (preferred when known)
    category: str | None = None     # library category match (e.g. "led")
    # The user asked for this and the curated library has NO such part. The
    # design is incomplete until it exists - R43 keeps it a hard error, so a
    # missing display can never be quietly "replaced by an LED" (review #8).
    unavailable: bool = False


class Wire(BaseModel):
    net: str
    segments: list[Point] = Field(default_factory=list)


class ComponentArray(BaseModel):
    """A block of identical parts created by `add_component_array`, remembered
    so the PCB side can honour the physical arrangement (LED matrix, keypad).
    `grid=True` means the parts must form a rows x cols grid on the BOARD at
    `pitch_mm` — the auto-placer pins them there and sizes the outline from the
    grid extent instead of scattering them. `grid=False` is a mere batch of
    identical parts (decoupling caps) that place freely."""
    key: str                          # stable id, e.g. "D1-D100"
    mpn: str
    refdeses: list[str]               # row-major
    rows: int
    cols: int
    grid: bool = True
    pitch_mm: float | None = None     # centre-to-centre on the PCB (grid only)


class FormFactorSpec(BaseModel):
    """The user's requested physical board form, recorded by the agent via the
    `set_form_factor` tool ("smartwatch" => round, "50x20 strip" => rect with
    dims). Same contract as DesignRequirement: it states what the USER asked
    for, `placement.auto_place` honours it, and verifier rule R57 compares the
    built board against it — so a watch that ships as a 70x60 rectangle is a
    reported defect, never a silent substitution. Dimensions are optional: a
    bare shape lets the placer size the board from the parts."""
    shape: Literal["rect", "round", "polygon"] = "rect"
    diameter_mm: float | None = None    # round/polygon: circumscribed circle
    width_mm: float | None = None       # rect only; None = auto-size
    height_mm: float | None = None      # rect only; None = auto-size
    # polygon only. `sides` asks for a regular n-gon inscribed in diameter_mm
    # (3 = triangle, 6 = hex — what someone means by "a triangular board");
    # `points` is an explicit outline for a shape no n-gon describes, such as
    # the X of a drone frame. One or the other, never both.
    sides: int | None = None
    points: list[Point] = Field(default_factory=list)
    note: str = ""                      # why, e.g. "smartwatch", "fits 50mm tube"
    # 2 or 4 copper layers. None = let the placer decide (4 when the board is
    # dense enough that a GND + power plane is what a professional would do).
    layers: Literal[2, 4] | None = None


class SchematicIR(BaseModel):
    components: list[Component] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    wires: list[Wire] = Field(default_factory=list)
    # User-stated countable requirements (see DesignRequirement). Lives on the
    # IR so it persists with the project and is visible to every verifier rule.
    requirements: list[DesignRequirement] = Field(default_factory=list)
    # Part arrays with physical-grid intent (see ComponentArray).
    arrays: list[ComponentArray] = Field(default_factory=list)
    # User-requested board form (see FormFactorSpec). None = no stated ask.
    form_factor: FormFactorSpec | None = None
    # Findings the designer marked intentional (see Acknowledgement).
    acknowledgements: list["Acknowledgement"] = Field(default_factory=list)
    # Explicit part constraints parsed from the user's own words (a value, a
    # package/mount style, a size). Checked by R73 — the "said 150 Ω, built
    # 100 Ω" class that three of five reviewers hit on 2026-08-20.
    partConstraints: list["PartConstraintRecord"] = Field(default_factory=list)
    # True when the last build turn ran out of agent turns with tool calls
    # still pending: the design is UNFINISHED, whatever the checks say. It
    # blocks export/order until the build is continued, because "the verifier
    # reports 0 errors on a design the agent has itself declared unfinished"
    # was two contradicting indicators on one screen (2026-08-20, finding 11).
    buildIncomplete: bool = False


class PartConstraintRecord(BaseModel):
    """An explicit part constraint the USER stated, in checkable form.

    Parsed deterministically from the prompt (`services.constraints_text`), not
    by the model — the recurring failure is precisely that the model states the
    right number in prose and then builds something else."""
    kind: Literal["value", "mount", "size", "mpn"]
    target: str                    # "resistor" | "capacitor" | "led" | "any" ...
    value: str                     # canonical: "150" (ohms), "tht", "5mm", "CH340N"
    text: str                      # the user's own words
    tolerance: float = 0.0


class Acknowledgement(BaseModel):
    """A finding the designer has marked INTENTIONAL, with the reason.

    Only warnings/info can be acknowledged — an error means the board does not
    work, and no note makes it work. The reason is required (an unexplained
    dismissal is how a real defect gets buried) and travels with the project,
    so the Review panel, the export gate and any future reviewer see WHY."""
    issue_id: str                  # VerifierIssue.id, e.g. "R02:I2C_SDA"
    rule: str | None = None        # the rule it came from, for context
    reason: str                    # the designer's justification (required)
    at: str | None = None          # ISO timestamp


VerifierSeverity = Literal["error", "warning", "info"]

# Provenance of a user-facing fact (Phase 2 — grounded claims). Every row the
# Review panel shows carries one of these so the UI can never present an
# LLM-authored claim with the same authority as an IR-derived one.
#   ir       — derived from the source-of-truth IR (components / nets / pins)
#   verifier — produced by a deterministic schematic DRC rule
#   pcb      — produced by the board verifier / layout engine
#   manual   — entered by the user
#   llm      — asserted by an LLM (advisory only; never a grounded fact)
#   kicad    — produced by KiCad's industry DRC engine (kicad-cli, task 4.2),
#              surfaced alongside — not replacing — the in-house rules
#   erc      — the ERC pin-type connection matrix (engineering-rules Phase 1,
#              R24–R26): the exhaustive electrical correctness floor
#   ipc      — an IPC parametric rule (engineering-rules Phase 3/4, R27–R30):
#              trace width / clearance / SI-PI, each citing its IPC clause
FactSource = Literal["ir", "verifier", "pcb", "manual", "llm", "kicad", "erc", "ipc", "mechanical"]


class VerifierIssue(BaseModel):
    id: str
    severity: VerifierSeverity
    message: str
    refdes: str | None = None
    net: str | None = None
    pinNumber: str | None = None
    rule: str | None = None  # rule id, e.g. "RULE_DECOUPLING_CAP"
    # Where this issue came from. Rule-based DRC issues are "verifier"; the LLM
    # critic and the claim validator stamp "llm" so the Review panel can badge
    # ungrounded advisories distinctly (feedback #2).
    source: FactSource = "verifier"
    # Evidence the finding is grounded in (LLM-critic findings only): the
    # specific library/datasheet fact relied on, and a link to the datasheet
    # (with #page=N when the curated provenance carries source pages). A critic
    # finding without a citation is dropped before it reaches the project.
    citation: str | None = None
    citationUrl: str | None = None


class BomLine(BaseModel):
    refdes: str
    description: str
    qty: int
    unitCost: float
    mpn: str | None = None
    # Price provenance (honest-sourcing round): "live" = fetched from a
    # distributor API (stock/vendor/date set), "reference" = the curated
    # library's ballpark unitCost, "none" = no price known (unitCost 0 and the
    # UI shows a dash — we never invent a price).
    priceSource: Literal["live", "reference", "none"] = "reference"
    stock: int | None = None
    priceVendor: str | None = None
    priceAsOf: str | None = None  # ISO date the live price was fetched
    # The currency `unitCost` is IN. A distributor API answers in the currency
    # of the account that owns the key — a Mouser India key quotes INR — so a
    # hardcoded "$" was an ~85x overstatement on every live line (found
    # 2026-08-22, the first hour a real Search key existed). Library reference
    # costs are USD. None when there is no price at all.
    priceCurrency: str | None = None
    # B4: a live quote past its 24 h TTL is still shown (it beats a ballpark)
    # but must LOOK stale. B1/B5: the break table and the buyability inputs
    # the distributor actually returned — None means "not stated", never a guess.
    priceStale: bool = False
    breaks: list[dict] = Field(default_factory=list)   # [{"qty": 1, "price": 3.10}, ...]
    sku: str | None = None          # distributor part number (what the cart needs)
    moq: int | None = None
    lifecycle: str | None = None
    leadDays: int | None = None


class DiffEntry(BaseModel):
    kind: Literal["add", "remove", "modify"]
    summary: str
    refdes: str | None = None


class Diff(BaseModel):
    status: Literal["pending", "accepted", "rejected"] = "pending"
    changes: list[DiffEntry] = Field(default_factory=list)


class ToolCallSummary(BaseModel):
    tool: str
    args: dict
    # The model's OWN sentence of design intent from the turn this call was made
    # in. It was already being produced (the system prompt asks for one line of
    # intent between tool calls) but was flattened into the final message text,
    # so the trace shown to the user was raw machinery — `add_component(mpn=…)`
    # — under a heading that said "reasoning". A reviewer asked for the real
    # rationale behind a part choice and got a tool log (2026-08-20).
    rationale: str | None = None


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    toolCalls: list[ToolCallSummary] = Field(default_factory=list)
    diff: Diff | None = None


ProjectStatus = Literal[
    "draft", "routing", "verifying", "manufactured", "drc-errors"
]


# What does this project produce?
#   "hardware"          — schematic + PCB only (default, M2 scope)
#   "hardware-software" — schematic + PCB + AI-generated firmware (M3+)
ProjectKind = Literal["hardware", "hardware-software"]


class Project(BaseModel):
    id: str
    name: str
    path: str
    kind: ProjectKind = "hardware"
    status: ProjectStatus = "draft"
    thumbnailUrl: str | None = None
    layers: int | None = None
    componentCount: int = 0
    drcErrorCount: int | None = None
    updatedAt: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ir: SchematicIR = Field(default_factory=SchematicIR)
    bom: list[BomLine] = Field(default_factory=list)
    issues: list[VerifierIssue] = Field(default_factory=list)
    chat: list[ChatMessage] = Field(default_factory=list)
    board: "Board | None" = None
    firmware: "Firmware | None" = None
    mechanical: "MechanicalIR | None" = None
    buildGuide: "BuildGuide | None" = None
    # Present only on the owner's copy; the public share response strips it.
    shareToken: str | None = None


# ============================================================================
# Design Summary (Phase 1 — Suyash-feedback roadmap)
# ============================================================================
# A scannable, IR-derived review of a project. Every row is computed from
# project.ir / project.issues, NEVER from LLM prose — so a connection can never
# be shown unless it actually exists on a net (the anti-mismatch guarantee).


class SummaryComponent(BaseModel):
    refdes: str
    mpn: str
    role: str                       # human-readable, e.g. "MCU", "Regulator"
    description: str | None = None
    source: FactSource = "ir"       # always IR-derived (feedback #11)


class PinRisk(BaseModel):
    """Authoritative MCU-pin risk verdict (Phase 3 / feedback #5), so the
    Review panel's boot-pin badge (#20) reads real data instead of a hardcoded
    client table. Mirrors services/pin_safety.risk()."""
    level: Literal["high", "warn"]
    note: str


class PinMapRow(BaseModel):
    """One peripheral-pin ↔ MCU-pin association, derived from a shared net."""
    device: str                     # human label, e.g. "U3 ECHO"
    source: str                     # "<refdes>.<pin>" on the peripheral
    mcuPin: str                     # the MCU pin (GPIO) the signal reaches
    mcuRefdes: str                  # which MCU
    net: str
    # Provenance is "ir" by construction — this row only exists because both
    # pins share a real net. Named distinctly from `source` (the peripheral pin)
    # to avoid colliding with that older field the frontend already reads.
    factSource: FactSource = "ir"
    # Boot/strap/flash/input-only risk of the MCU pin this signal lands on, from
    # the authoritative pin_safety table. None when the pin is unremarkable.
    risk: PinRisk | None = None


class PowerRailRow(BaseModel):
    rail: str                       # net name, e.g. "VCC_3V3"
    sources: list[str] = Field(default_factory=list)   # refdes(es) driving it
    loads: list[str] = Field(default_factory=list)     # refdes(es) consuming it
    factSource: FactSource = "ir"
    # Where this rail sits in the supply chain (Phase 3 / feedback #6):
    #   input      — raw input (USB VBUS, battery VBAT, barrel VIN)
    #   regulated  — output of an on-board regulator/charger
    #   rail       — a power net we couldn't classify further
    kind: str = "rail"
    # Upstream rail name(s) feeding this rail's source(s) — e.g. the regulator's
    # own input rail — so the UI can draw battery → charger → rail → loads.
    upstream: list[str] = Field(default_factory=list)
    # Robotics power-tree (Week-5 7.6):
    #   role       — "motor" (feeds a motor driver's VM-class pin), "logic"
    #                (everything else), or "mixed" (motor AND MCU on one rail —
    #                the R38 shared-rail hazard, surfaced right in the tree)
    #   estCurrentMa — estimated draw (sum of consumers' ratings, R27's model)
    #   sourceMaxMa  — the rail source's rated output current, when known
    #   issueCount / worstSeverity — power-integrity findings on this rail
    #                (R11/R14/R23/R26/R31/R32/R36/R38/R40/R41) rolled up per row
    role: str = "logic"
    estCurrentMa: float | None = None
    sourceMaxMa: float | None = None
    issueCount: int = 0
    worstSeverity: str | None = None
    # Rail VOLTAGE (2026-08-20 review): the power tree modelled current and
    # topology but not volts, so VBUS (5 V), VBAT (3.0-4.2 V) and VCC_3V3
    # (3.3 V) all read as identical "LOGIC" rows and every voltage-related
    # judgement had nothing to stand on. A regulated rail has min == max; an
    # input rail carries the range the user declared.
    voltageMin: float | None = None
    voltageMax: float | None = None


class DesignSummary(BaseModel):
    components: list[SummaryComponent] = Field(default_factory=list)
    pinMapping: list[PinMapRow] = Field(default_factory=list)
    powerArchitecture: list[PowerRailRow] = Field(default_factory=list)
    warnings: list[VerifierIssue] = Field(default_factory=list)
    missingItems: list[VerifierIssue] = Field(default_factory=list)


# ----- Net inspection (Phase 4 / feedback #8) -------------------------------
# Backs the net-detail panel: all pins on a net + routed status, computed
# server-side with the authoritative library category (not the client's
# most-pins heuristic).


class NetEndpoint(BaseModel):
    refdes: str
    pin: str                        # canonical pin number
    name: str                       # pin display name, e.g. "IO19"
    isMcu: bool
    pinType: str | None = None


class NetDetail(BaseModel):
    net: str
    endpoints: list[NetEndpoint] = Field(default_factory=list)
    mcuRefdes: str | None = None
    mcuPin: str | None = None       # the GPIO this net reaches, if any
    role: str = "signal"            # "power" | "gnd" | "signal"
    routed: bool | None = None      # None when no board has been generated
    traceCount: int = 0
    vias: int = 0
    ratsnest: int = 0
    issues: list[VerifierIssue] = Field(default_factory=list)


# ============================================================================
# Firmware (PRD §7.7) — AI-generated PlatformIO project
# ============================================================================


class FirmwareFile(BaseModel):
    path: str               # forward-slash relative path, e.g. "src/main.cpp"
    content: str            # full file body


class Firmware(BaseModel):
    framework: str = "arduino"
    target_mcu: str | None = None       # MPN of the MCU this firmware targets
    summary: str = ""                   # one-sentence description from the LLM
    files: list[FirmwareFile] = Field(default_factory=list)
    readme_path: str = "README.md"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_used: str | None = None


# ============================================================================
# Build guide — "step-by-step: assembly order, tools, gotchas" (Prompt to
# Product, 2026-09). Derived from the IR, board, BOM and mechanical IR by
# services/build_guide.py; the LLM only rewrites prose over facts the
# skeleton already established, never adds a part, a value or a voltage.
# ============================================================================


class BuildStep(BaseModel):
    n: int
    title: str
    body: str
    refdes: list[str] = Field(default_factory=list)
    check: str | None = None      # what to verify before moving on


class BuildGuide(BaseModel):
    summary: str = ""
    tools: list[str] = Field(default_factory=list)
    partsToOrder: list[str] = Field(default_factory=list)
    steps: list[BuildStep] = Field(default_factory=list)
    powerUpChecks: list[str] = Field(default_factory=list)
    gotchas: list[str] = Field(default_factory=list)
    enclosure: list[str] = Field(default_factory=list)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    model_used: str | None = None   # None = deterministic skeleton only


# ============================================================================
# Board IR — PCB layout (PRD §7.5)
# ============================================================================
# Units: millimeters throughout. Coordinate origin at board's bottom-left.
# 2-layer boards only at launch.

PadShape = Literal["circle", "rect", "rounded"]
PadType = Literal["smd", "tht"]      # surface-mount vs through-hole
BoardLayer = Literal["top", "bottom"]


class FootprintPad(BaseModel):
    """A single solderable pad on a footprint, in the footprint's local coords."""
    pin_number: str
    x: float
    y: float
    width: float                # for SMD: pad width; for TH: drill diameter
    height: float = 0.0         # SMD-only
    shape: PadShape = "rect"
    pad_type: PadType = "smd"
    layer: BoardLayer = "top"


# Footprint trust level (Phase 5 / feedback #7):
#   verified    — curated against the real package (manufacturing-ready)
#   approximate — recognisable package geometry, pad count matches the pinout,
#                 but dimensions are parametric estimates (not fab-final)
#   generic     — a placeholder rectangle with no real pad layout
FootprintConfidence = Literal["verified", "approximate", "generic"]


class Footprint(BaseModel):
    """Physical footprint definition for a part. Cached per MPN."""
    name: str                   # e.g. "Resistor_SMD:R_0603_1608Metric"
    width: float                # bounding box, mm
    height: float
    pads: list[FootprintPad] = Field(default_factory=list)
    # Manufacturability metadata (Phase 5).
    confidence: FootprintConfidence = "approximate"
    courtyard_w: float = 0.0    # body + assembly clearance, mm (0 = unset)
    courtyard_h: float = 0.0
    pin1_pad: str | None = None  # pad designated pin-1, for the silkscreen marker
    # Parts whose solid volume is NOT one rectangle: an Arduino shield's header
    # pair occupies two thin strips along opposite edges and leaves the whole
    # middle of the board free — which is exactly where a shield's components
    # go. Modelling that as one bounding box made every correct shield layout
    # report four unavoidable overlap errors against its own host header
    # (2026-08-20). Each entry is (x0, y0, x1, y1) in the FOOTPRINT frame, same
    # origin and axes as `pads`. Empty (the normal case) means the single
    # width x height box is the whole story.
    bodyRects: list[tuple[float, float, float, float]] = Field(default_factory=list)


class PlacedComponent(BaseModel):
    """A component placed at a specific (x, y, rotation, layer) on the board."""
    refdes: str
    mpn: str
    x: float
    y: float
    rotation: Literal[0, 90, 180, 270] = 0
    layer: BoardLayer = "top"
    footprint: Footprint
    # Editable-PCB workflow (Phase 6 / feedback #9, #10). When a user pins a part,
    # auto-place anchors it instead of moving it; placementReason records why a
    # part sits where it does (inferred constraint or a user edit).
    locked: bool = False
    placementReason: str | None = None


class TraceSegment(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class Trace(BaseModel):
    net: str
    layer: BoardLayer
    width: float = 0.2          # mm, default 8 mil
    segments: list[TraceSegment] = Field(default_factory=list)


class Via(BaseModel):
    x: float
    y: float
    drill: float = 0.3
    diameter: float = 0.6
    net: str


class RatsLine(BaseModel):
    """Unrouted connection — drawn as a thin straight line on the canvas to
    show what still needs routing."""
    net: str
    x1: float
    y1: float
    x2: float
    y2: float


BoardShape = Literal["rect", "round", "polygon"]
PlaneLayer = Literal["In1", "In2"]


class Plane(BaseModel):
    """A solid copper plane on an INNER layer of a 4-layer board, dedicated to
    one net (the classic signal / GND / power / signal stack-up). Every pad of
    that net connects to the plane through a via (SMD) or its own plated hole
    (THT), so the net needs no routed traces — the single biggest routability
    win on a dense board, and what a professional 4-layer board looks like."""
    layer: PlaneLayer            # In1 = directly under the top copper, In2 = above the bottom
    net: str                     # the net the plane carries (GND, VCC_3V3, ...)


class Board(BaseModel):
    width: float = 50.0         # board outline, mm (or diameter when shape="round")
    height: float = 40.0
    shape: BoardShape = "rect"
    # shape="polygon": the milled outline, IR frame (mm, Y up, origin bottom
    # left), counter-clockwise, not closed. width/height stay the bounding box
    # so every consumer that only needs a canvas size keeps working unchanged.
    # `board_shape.ring_for_board` is the single reader — never interpret these
    # points anywhere else, or the placer and the router will disagree about
    # where the copper ends.
    outline_points: list[Point] = Field(default_factory=list)
    layer_count: int = 2        # 2, or 4 with `planes` on In1/In2
    planes: list[Plane] = Field(default_factory=list)
    components: list[PlacedComponent] = Field(default_factory=list)
    traces: list[Trace] = Field(default_factory=list)
    vias: list[Via] = Field(default_factory=list)
    rats: list[RatsLine] = Field(default_factory=list)
    issues: list[VerifierIssue] = Field(default_factory=list)
    auto_placed: bool = False
    auto_routed: bool = False
    # When True, the bottom layer is a solid GND copper pour — every GND pad
    # is implicitly connected through the pour, so the autorouter doesn't need
    # to draw individual GND traces. Saves ~25 traces on a typical SoC board.
    has_ground_pour: bool = True


# ============================================================================
# Mechanical IR (Week 3 — task 5.1)
# ============================================================================
# A typed JSON intermediate representation for the *mechanical* side, sitting
# ALONGSIDE the electrical IR and the Board with the same discipline: the LLM
# never writes it as text — it only mutates it through strict tool calls
# (mechanical_tools.py), the same anti-hallucination contract as the electrical
# side. It drives the OpenCASCADE/CadQuery kernel (task 5.2 — board-as-3D-solid
# + STEP export) and the 3D render (task 5.3). All dimensions in mm, IR
# coordinate frame (Y-up, origin bottom-left) — the same frame the Board uses.

MechShape = Literal["rect", "rounded_rect", "circle", "polygon"]


class BoardOutlineMech(BaseModel):
    """The physical board outline the enclosure/fab is cut to. Distinct from
    `Board.width/height` (the placement canvas) so a designer can round corners,
    go circular, or give a custom polygon without disturbing placement."""
    shape: MechShape = "rounded_rect"
    width: float = 50.0            # bounding width, mm (diameter when shape="circle")
    height: float = 40.0
    corner_radius: float = 3.0     # for rounded_rect
    points: list[Point] = Field(default_factory=list)  # for polygon (mm, IR frame)


class MountingHole(BaseModel):
    id: str
    x: float                       # mm, IR frame (centre)
    y: float
    diameter: float = 3.2          # mm — M3 clearance by default
    plated: bool = False
    standoff: str | None = None    # e.g. "M3" — the screw/standoff spec


class KeepoutZone(BaseModel):
    """A rectangular region no copper/component may occupy — under an antenna,
    a mounting boss, a moving part. `layer="both"` covers top + bottom."""
    id: str
    x: float                       # mm, IR frame (bottom-left corner)
    y: float
    width: float
    height: float
    layer: Literal["top", "bottom", "both"] = "both"
    reason: str | None = None


class ConnectorCutout(BaseModel):
    """An opening in the enclosure wall for a connector to poke through (USB,
    barrel jack, headers). `refdes` ties it to the placed connector; `edge` names
    the board edge it sits on so the enclosure wall is cut there.

    Coordinate convention (honoured by the CAD bridge and the 3D overlay):
      x, y     bottom-left corner of the opening's board-plane footprint (mm, IR
               frame — Y-up, origin bottom-left).
      width    span of the opening PARALLEL to its `edge`: along X for top/bottom
               edges, along Y for left/right edges.
      height   vertical opening height, measured up from the board's top surface
               (i.e. the connector's body height above the PCB).
    For `edge="none"` (a manually-added cutout with no wall assigned) width is
    treated as an X-span, matching the pre-6.1 behaviour."""
    id: str
    refdes: str | None = None
    x: float
    y: float
    width: float
    height: float
    edge: Literal["left", "right", "top", "bottom", "none"] = "none"


class Enclosure(BaseModel):
    """Parametric enclosure derived from the outline + component heights. The
    kernel (5.2) grows a box around the board with these clearances."""
    kind: Literal["none", "box", "snap_fit"] = "none"
    wall_thickness: float = 2.0    # mm
    standoff_height: float = 5.0   # board sits this high off the enclosure floor
    clearance_top: float = 12.0    # headroom above the tallest top-side part, mm
    clearance_bottom: float = 3.0  # below the board (bottom-side parts / leads)
    lip: float = 1.0               # snap-fit/lid lip


# How the individual boards are separated from the panel after assembly.
#   v_score   — a V-shaped groove cut most of the way through from both faces;
#               the boards snap apart along it. Cheapest and cleanest, but the
#               blade runs the FULL width of the panel in a straight line, so
#               it only works when every board edge on that axis is straight
#               and collinear. A round or irregular board cannot be V-scored.
#   mouse_bites — the boards are routed apart and held by small tabs drilled
#               with a row of closely-spaced holes. Works for any outline;
#               leaves a slightly ragged edge where the tab breaks.
PanelMethod = Literal["v_score", "mouse_bites"]


class PanelSpec(BaseModel):
    """How this board is arrayed onto a manufacturing panel.

    Fabs build and ship PANELS, not single boards, and assembly houses need a
    panel to hold a board in the pick-and-place machine at all. Small boards
    are also quoted per panel, so a 20 x 20 mm board ordered one-up wastes most
    of what you pay for. Synqit had nothing to say about any of this — two
    review rounds asked for it (2026-08-20).

    Geometry is derived (`services/panelize.py`), never stored: the panel is a
    function of the board and this spec, so it can never go stale against a
    board that changed.
    """
    rows: int = Field(default=1, ge=1, le=20)
    cols: int = Field(default=1, ge=1, le=20)
    method: PanelMethod = "v_score"
    # Space between adjacent boards. V-scoring needs none (the groove IS the
    # boundary); mouse-biting needs a routed slot for the cutter to travel.
    gap: float = Field(default=0.0, ge=0.0, le=10.0)
    # Breakaway rail along the panel's two long edges — where the tooling holes
    # and fiducials live. 0 = no rails (the fab clamps the boards directly).
    rail: float = Field(default=5.0, ge=0.0, le=20.0)


class MechanicalIR(BaseModel):
    """The mechanical intermediate representation — the single source of truth
    for the physical/enclosure side, mutated only through mechanical_tools."""
    outline: BoardOutlineMech | None = None
    mountingHoles: list[MountingHole] = Field(default_factory=list)
    keepouts: list[KeepoutZone] = Field(default_factory=list)
    cutouts: list[ConnectorCutout] = Field(default_factory=list)
    enclosure: Enclosure = Field(default_factory=Enclosure)
    panel: PanelSpec | None = None


Project.model_rebuild()
Firmware.model_rebuild()
BuildGuide.model_rebuild()


class BoardEditRequest(BaseModel):
    """Edit one placed component (Phase 6 / #32). Every field optional — only
    the provided ones are applied; moving/rotating triggers a partial reroute."""
    x: float | None = None
    y: float | None = None
    rotation: Literal[0, 90, 180, 270] | None = None
    layer: BoardLayer | None = None
    locked: bool | None = None


# Placement constraints (Phase 6 / #34). Inferred from the project archetype or
# set by the user; consumed by the placer.
ConstraintKind = Literal["edge", "near", "group", "separate", "fixed", "keepout"]


class PlacementConstraint(BaseModel):
    kind: ConstraintKind
    refdes: str                                   # the component the rule is about
    target: str | None = None                     # other refdes (near/separate/group)
    reason: str | None = None                     # human-readable provenance
    inferred: bool = True                         # inferred vs user-set


class CreateProjectRequest(BaseModel):
    name: str
    kind: ProjectKind = "hardware"


class ChatRequest(BaseModel):
    text: str


class DiffActionRequest(BaseModel):
    action: Literal["accept", "reject"]


class PropertyUpdateRequest(BaseModel):
    key: Literal["value", "footprint", "x", "y", "rotation"]
    value: str
    # Which structure x/y/rotation edit: "ir" = the schematic symbol (the
    # Inspector's coords), "board" = the placed PCB component (canvas drag).
    # The two hold different coordinate systems — writing a board move into
    # the IR was the drag-snaps-back bug.
    target: Literal["ir", "board"] = "ir"


# Library — see app/library/parts.json


class LibraryPin(BaseModel):
    number: str
    name: str
    side: PinSide = "left"
    type: PinType = "passive"


CurationStatus = Literal["draft", "auto_merged", "human_verified", "needs_review"]


class CompanionPart(BaseModel):
    """A part that should usually be placed alongside this one — e.g. a
    decoupling cap next to an MCU's VDD pin. Drives the schematic-rule engine
    and the agent's "what else does this part need" heuristic."""
    mpn: str                                       # MPN of the companion part
    role: str                                      # "decoupling" | "pullup" | "load_cap" | "crystal_load" | "current_limit" | "level_shift" | "filter"
    pin: str | None = None                         # which pin of *this* part the companion attaches to
    quantity: int = 1
    notes: str | None = None


class Provenance(BaseModel):
    """Where this part data came from. Lets us audit any pin claim back to a
    specific datasheet revision."""
    datasheetUrl: str | None = None
    datasheetRevision: str | None = None           # rev letter / date if extractable
    extractedAt: str | None = None                 # ISO timestamp
    extractorVersion: str | None = None            # bot version that produced this
    extractorModel: str | None = None              # e.g. "gpt-4o-2024-08-06"
    sourcePages: list[int] = Field(default_factory=list)
    confidenceScore: float | None = None           # 0.0–1.0
    verifierChecks: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class PartSourcing(BaseModel):
    """Has anyone checked this part can actually be bought?

    A buy-link to a part number no distributor carries sends the user to a
    "0 results" page, and there is no way to tell that from the card — an
    unchecked part looks exactly like a confirmed one. Measured 2026-09-04: a
    40-part sample of the never-audited KiCad-symbol harvest was 48% not
    carried at Mouser. The audit records what it found and this carries it to
    the UI, so the product can say "we have not confirmed this" instead of
    implying it did."""
    checked: bool = False          # has any catalogue been asked about it
    carried: bool = False          # did a catalogue answer yes
    checkedOn: str | None = None   # ISO date of the check
    note: str | None = None


class Offer(BaseModel):
    """A place to buy this part. Populated by the curation bot's buy-link
    resolver (distributor APIs) and shown on the Library page. `url` may carry
    an affiliate tag — `affiliate` records whether it does, so the UI can
    disclose it and mark the link rel="sponsored"."""
    vendor: str                                    # "DigiKey" | "Mouser" | "LCSC" | "Amazon" | "JLCPCB" | ...
    url: str                                       # product/buy URL (may include an affiliate tag)
    sku: str | None = None                         # vendor's own part number
    price: float | None = None                     # unit price at last resolve
    currency: str = "USD"
    inStock: bool | None = None
    imageUrl: str | None = None                    # product photo from this vendor, if any
    affiliate: bool = False                        # true when `url` carries our affiliate tag
    resolvedAt: str | None = None                  # ISO timestamp of last resolve


class LibraryPart(BaseModel):
    mpn: str
    description: str
    category: str  # "mcu" | "regulator" | "sensor" | "passive" | ...
    package: str
    footprint: str
    symbol: str
    pins: list[LibraryPin]
    # Product image + purchase offers (buy links). Both are populated by the
    # curation bot's resolver; all optional so existing parts stay valid.
    imageUrl: str | None = None
    offers: list[Offer] = Field(default_factory=list)
    # JLC sourcing info — fake but shaped correctly.
    jlcPart: str | None = None
    jlcStock: int | None = None
    unitCost: float = 0.0
    voltageMin: float | None = None
    voltageMax: float | None = None
    # Voltage-domain metadata (Phase 3 / feedback #4). recommendedSupplyV is the
    # rail this part is normally run from; when outputTracksSupply is true its
    # digital outputs swing to that rail (so a 5 V-supplied part outputs 5 V
    # logic). logicLevelV, when set, is the part's I/O logic level — used to
    # decide whether a level shifter is needed between two domains.
    recommendedSupplyV: float | None = None
    outputTracksSupply: bool = False
    logicLevelV: float | None = None
    datasheetUrl: str | None = None
    alternatives: list[str] = Field(default_factory=list)
    width: int = 80   # default canvas size when placed
    height: int = 60
    # Firmware-generation hints (PRD §7.7). When present, the firmware
    # generator surfaces these to the LLM so it picks the right Arduino
    # library instead of guessing.
    firmwareLibrary: str | None = None  # PlatformIO lib_deps entry (or comma-sep)
    firmwareNotes: str | None = None    # short hint, e.g. "I2C addr 0x76"

    # ----- Curation-bot fields (all optional; populated for new parts) -----

    # Manufacturer & sourcing — for multi-distributor BOM export.
    manufacturer: str | None = None
    digikeyPart: str | None = None
    mouserPart: str | None = None
    lcscPart: str | None = None         # alias of jlcPart for explicit naming
    adafruitSku: str | None = None       # for the US maker ecosystem
    sparkfunSku: str | None = None
    seedSku: str | None = None

    # Electrical envelope — beyond voltage. Drives DRC and SPICE.
    currentTypMa: float | None = None    # typical operating current, mA
    currentMaxMa: float | None = None    # max rated current, mA
    powerMaxW: float | None = None       # max power dissipation, W
    operatingTempMinC: float | None = None
    operatingTempMaxC: float | None = None
    # Body height above the PCB (solder-plane to top of the part), mm. Drives the
    # mechanical enclosure clearance + part-to-lid checks (Week-4 ECAD 6.1/6.2).
    # Optional/curation-populated; mechanical_derive.body_height_mm() falls back
    # to a package-family table when this is absent.
    bodyHeightMm: float | None = None
    # For edge-mounted connectors: the direction the mating face / plug opening
    # points in the footprint's LOCAL frame at rotation 0 (Week-4 6.1). The placer
    # rotates the connector so this points off-board (out the nearest edge) — so a
    # USB port faces the wall you plug into. None ⇒ not auto-oriented (stays 0°).
    matingFaceDir: Literal["+X", "-X", "+Y", "-Y"] | None = None
    # True for dev-board MODULES that carry their own USB input and voltage
    # regulator (Arduino Nano/Uno/Mega, ESP32/Pico dev boards). A carrier board
    # for one of these should be powered THROUGH the module, not given a second
    # USB connector + LDO (verifier R50).
    onboardPower: bool = False
    # True for a device that lives OFF the PCB and reaches it through a
    # connector: a servo, a stepper motor, a membrane keypad, a probe on a
    # cable, a panel-mount switch. For these the header IS the whole board
    # footprint — there is no module body to draw, nothing to fit in the
    # enclosure, and no height to clear. R72 flagged an SG90 servo and a
    # 28BYJ-48 stepper as hard ERRORS for "having no body on the board", which
    # is not a defect and not something the agent can fix — and R72 errors feed
    # the fix-up loop, so it would have burned turns trying (2026-08-20).
    offBoard: bool = False

    # Protocol / interface tags — drives the agent's "this is an I2C device,
    # wire it to the bus" routing.
    interfaces: list[str] = Field(default_factory=list)   # "I2C","SPI","UART","USB","CAN","LIN","I2S","PWM","Analog","BLE","WiFi","LoRa","Ethernet","NFC","GPIO"
    defaultI2cAddress: str | None = None                  # "0x76", "0x77", "0x68"
    defaultSpiMode: int | None = None                     # 0–3
    defaultBaudRate: int | None = None                    # for UART/etc.

    # Companion parts — decoupling caps, pull-ups, load caps, etc.
    companions: list[CompanionPart] = Field(default_factory=list)

    # Free-text example of how this part is typically wired — short, the agent
    # sees this when adding the part to a schematic.
    typicalApplication: str | None = None

    # Where did this data come from and how confident are we?
    provenance: Provenance | None = None
    curationStatus: CurationStatus = "human_verified"     # legacy parts default to verified

    # ----- end curation-bot fields -----
