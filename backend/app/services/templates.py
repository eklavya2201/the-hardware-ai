"""Reference-design templates — vetted sub-circuit blocks (correctness lever).

The IR anti-hallucination guarantee makes connections *real*, not *correct* — the
LLM can still pick a wrong topology. Templates close part of that gap: instead of
free-designing a common block (a regulator with its caps, an I²C bus's pull-ups),
the agent *composes* a HUMAN-VALIDATED block and only parameterises it. The
topology is right because a person authored it, not because the model guessed.

A template is: an ordered set of parts (each with a role + MPN + value) and a set
of connections over *placeholder* nets (VIN/VOUT/GND/…) that the caller maps onto
real project nets. `instantiate()` replays it through the same `add_component` /
`connect` tools the agent uses — so every anti-hallucination and rail-conflict
guard still applies, and a template can never introduce a part outside the
library.

Only fully-correct blocks whose parts have complete library pins belong here — a
wrong template is worse than none. Extend by adding to `TEMPLATES` (and fix any
library pin gaps the block needs, e.g. a regulator missing its VOUT pin).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import DiffEntry, Project
from app.services import library


@dataclass(frozen=True)
class TemplatePart:
    role: str                 # stable handle used by connections, e.g. "reg", "cin"
    mpn: str
    value: str | None = None


@dataclass(frozen=True)
class TemplateConnection:
    net: str                  # placeholder net name (mapped by the caller)
    pins: list[tuple[str, str]]  # (role, pin number/name)


@dataclass(frozen=True)
class Template:
    name: str
    title: str
    category: str
    description: str
    nets: list[str]           # placeholder nets the caller should map
    parts: list[TemplatePart]
    connections: list[TemplateConnection] = field(default_factory=list)
    # The INPUT-VOLTAGE window this block is actually vetted for, as
    # (min_v, max_v) on the net named by `input_net`. A template is only
    # "DRC-clean by construction" INSIDE its envelope: an LM7805 block is a
    # correct 5 V supply from 12 V and a dead short of a promise from a 3.7 V
    # cell. A 2026-08-20 reviewer's point exactly — the vetted label vouches
    # for the block, so the block has to refuse the cases it does not cover.
    # None = no voltage precondition (a pull-up pair, a header).
    input_range: tuple[float, float] | None = None
    input_net: str = "VIN"


# --- the curated block library (author only fully-verified blocks) ----------

TEMPLATES: dict[str, Template] = {
    "lm7805_5v_linear": Template(
        name="lm7805_5v_linear",
        input_range=(7.5, 20.0),
        title="5 V linear regulator (LM7805) with I/O caps",
        category="power",
        description=(
            "LM7805 fixed 5 V linear regulator with a 10 µF input cap and a 10 µF "
            "output cap to GND (datasheet reference)."
        ),
        nets=["VIN", "VOUT", "GND"],
        parts=[
            TemplatePart("reg", "LM7805", "5V"),
            TemplatePart("cin", "CL10A106MQ8NNNC", "10uF"),
            TemplatePart("cout", "CL10A106MQ8NNNC", "10uF"),
        ],
        connections=[
            TemplateConnection("VIN", [("reg", "INPUT"), ("cin", "1")]),
            TemplateConnection("VOUT", [("reg", "OUTPUT"), ("cout", "1")]),
            TemplateConnection("GND", [("reg", "GND"), ("cin", "2"), ("cout", "2")]),
        ],
    ),
    # LM2596-ADJ buck stages, wired per the datasheet's typical application:
    # VIN -> 220 uF + LM2596 VIN; Output(SW) -> 33 uH -> VOUT with the SS34
    # catch diode from SW to GND (cathode on SW) and 470 uF on VOUT; FB from
    # the VOUT divider (Vout = 1.23 V * (1 + R2/R1), R1 FB->GND, R2 VOUT->FB);
    # ON/OFF to GND = always on. Values from stock 1 % E24 parts, output within
    # +/-4 % of the nominal — stated in each description. Prompt-sweep finding:
    # the copilot free-designed an MP1584EN with no inductor at all; a vetted
    # block is the correctness lever (see relay_lock_driver).
    "buck_lm2596_5v": Template(
        name="buck_lm2596_5v",
        input_range=(7.0, 40.0),
        title="5 V / 3 A buck (LM2596-ADJ)",
        category="power",
        description=(
            "LM2596 adjustable buck set for 5.0 V (R2 12 kΩ / R1 3.9 kΩ → 5.01 V): 220 µF input "
            "cap, 33 µH RLB0914 coil from the switch node to VOUT, SS34 catch diode SW→GND, "
            "470 µF output cap, ON/OFF tied to GND. Input 7-40 V. Map VIN to the raw input rail "
            "(battery / barrel jack, AFTER any reverse-polarity stage) and VOUT to the 5 V rail."
        ),
        nets=["VIN", "VOUT", "GND"],
        parts=[
            TemplatePart("reg", "LM2596", "5V"),
            TemplatePart("cin", "EEU-FR1V221", "220uF"),
            TemplatePart("l", "RLB0914-330KL", "33uH"),
            TemplatePart("d", "SS34"),
            TemplatePart("cout", "EEU-FR1E471", "470uF"),
            TemplatePart("r1", "RC0603FR-073K9L", "3.9k"),
            TemplatePart("r2", "RC0603FR-0712KL", "12k"),
        ],
        connections=[
            TemplateConnection("VIN", [("reg", "VIN"), ("cin", "1")]),
            TemplateConnection("SW", [("reg", "SW"), ("l", "1"), ("d", "Cathode")]),
            TemplateConnection("VOUT", [("l", "2"), ("cout", "1"), ("r2", "1")]),
            TemplateConnection("FB", [("reg", "Feedback"), ("r1", "1"), ("r2", "2")]),
            TemplateConnection("GND", [("reg", "Ground"), ("reg", "ON/OFF"), ("cin", "2"), ("d", "Anode"), ("cout", "2"), ("r1", "2")]),
        ],
    ),
    "buck_lm2596_3v3": Template(
        name="buck_lm2596_3v3",
        input_range=(5.5, 40.0),
        title="3.3 V / 3 A buck (LM2596-ADJ)",
        category="power",
        description=(
            "LM2596 adjustable buck set for 3.3 V (R2 5.6 kΩ / R1 3.3 kΩ → 3.32 V): 220 µF input "
            "cap, 33 µH coil, SS34 catch diode, 470 µF output cap, ON/OFF to GND. Input 7-40 V. "
            "The efficient way to make 3.3 V from a 12/24 V input for an ESP32-class load."
        ),
        nets=["VIN", "VOUT", "GND"],
        parts=[
            TemplatePart("reg", "LM2596", "3.3V"),
            TemplatePart("cin", "EEU-FR1V221", "220uF"),
            TemplatePart("l", "RLB0914-330KL", "33uH"),
            TemplatePart("d", "SS34"),
            TemplatePart("cout", "EEU-FR1E471", "470uF"),
            TemplatePart("r1", "RC0603FR-073K3L", "3.3k"),
            TemplatePart("r2", "RC0603FR-075K6L", "5.6k"),
        ],
        connections=[
            TemplateConnection("VIN", [("reg", "VIN"), ("cin", "1")]),
            TemplateConnection("SW", [("reg", "SW"), ("l", "1"), ("d", "Cathode")]),
            TemplateConnection("VOUT", [("l", "2"), ("cout", "1"), ("r2", "1")]),
            TemplateConnection("FB", [("reg", "Feedback"), ("r1", "1"), ("r2", "2")]),
            TemplateConnection("GND", [("reg", "Ground"), ("reg", "ON/OFF"), ("cin", "2"), ("d", "Anode"), ("cout", "2"), ("r1", "2")]),
        ],
    ),
    "buck_lm2596_12v": Template(
        name="buck_lm2596_12v",
        input_range=(14.0, 40.0),
        title="12 V / 3 A buck (LM2596-ADJ)",
        category="power",
        description=(
            "LM2596 adjustable buck set for ~11.5 V (R2 100 kΩ / R1 12 kΩ → 11.5 V, -4 %; use a "
            "trimmer if you need exactly 12.0): 220 µF input cap, 33 µH coil, SS34 catch diode, "
            "470 µF output cap, ON/OFF to GND. Input 15-40 V (a 4S pack at 16.8 V qualifies). "
            "For camera/VTX rails on a drone PDB or a 24 V→12 V step."
        ),
        nets=["VIN", "VOUT", "GND"],
        parts=[
            TemplatePart("reg", "LM2596", "12V"),
            TemplatePart("cin", "EEU-FR1V221", "220uF"),
            TemplatePart("l", "RLB0914-330KL", "33uH"),
            TemplatePart("d", "SS34"),
            TemplatePart("cout", "EEU-FR1E471", "470uF"),
            TemplatePart("r1", "RC0603FR-0712KL", "12k"),
            TemplatePart("r2", "RC0603FR-07100KL", "100k"),
        ],
        connections=[
            TemplateConnection("VIN", [("reg", "VIN"), ("cin", "1")]),
            TemplateConnection("SW", [("reg", "SW"), ("l", "1"), ("d", "Cathode")]),
            TemplateConnection("VOUT", [("l", "2"), ("cout", "1"), ("r2", "1")]),
            TemplateConnection("FB", [("reg", "Feedback"), ("r1", "1"), ("r2", "2")]),
            TemplateConnection("GND", [("reg", "Ground"), ("reg", "ON/OFF"), ("cin", "2"), ("d", "Anode"), ("cout", "2"), ("r1", "2")]),
        ],
    ),
    # The three blocks the sweep's senior-engineer read asked for on almost
    # every board — USB-C sink termination + ESD, the MCP73831 charge path, and
    # an ESP32-C3 core with its EN/boot strapping. Each is DRC-clean by
    # construction and replaces ~15 tool calls, which is where complex prompts
    # were running out of turns.
    "usb_c_5v_input": Template(
        name="usb_c_5v_input",
        title="USB-C 5 V power input (CC pull-downs + ESD + bulk cap)",
        category="power",
        description=(
            "USB4085-GF-A receptacle wired as a USB-C SINK: CC1 and CC2 each pulled to GND "
            "through their OWN 5.1 kΩ (one resistor bridging CC1–CC2 is wrong), VBUS to the "
            "VBUS rail with a 10 µF bulk cap, USBLC6-2SC6 ESD array on D+/D− with its VBUS "
            "pin on the rail. Map VBUS to the board's 5 V input rail; D+/D− to the MCU's USB "
            "pins (or leave those nets unmapped and the ESD still protects VBUS)."
        ),
        nets=["VBUS", "GND", "USB_DP", "USB_DN"],
        parts=[
            TemplatePart("j", "USB4085-GF-A"),
            TemplatePart("rcc1", "RC0603FR-075K1L", "5.1k"),
            TemplatePart("rcc2", "RC0603FR-075K1L", "5.1k"),
            TemplatePart("esd", "USBLC6-2SC6"),
            TemplatePart("cbulk", "CL10A106MQ8NNNC", "10uF"),
        ],
        connections=[
            TemplateConnection("VBUS", [("j", "A4"), ("esd", "5"), ("cbulk", "1")]),
            TemplateConnection("GND", [("j", "A1"), ("j", "B12"), ("rcc1", "2"), ("rcc2", "2"), ("esd", "2"), ("cbulk", "2")]),
            TemplateConnection("USB_CC1", [("j", "A5"), ("rcc1", "1")]),
            TemplateConnection("USB_CC2", [("j", "B5"), ("rcc2", "1")]),
            TemplateConnection("USB_DP", [("j", "A6"), ("esd", "1")]),
            TemplateConnection("USB_DN", [("j", "A7"), ("esd", "3")]),
        ],
    ),
    "lipo_charger_mcp73831": Template(
        name="lipo_charger_mcp73831",
        input_range=(4.35, 6.0),
        input_net="VBUS",
        title="1S LiPo charge path (MCP73831, 450 mA, STAT LED, JST-PH)",
        category="power",
        description=(
            "MCP73831T-2ACI/OT single-cell charger: VDD from VBUS with 4.7 µF, VBAT to the "
            "battery rail with 4.7 µF and a JST-PH 2-pin battery connector, PROG 2.2 kΩ to GND "
            "(≈450 mA — lower the current for a small cell: 10 kΩ ≈ 100 mA), STAT driving a "
            "green LED through 1 kΩ from VBUS (lit while charging). Map VBUS to the USB 5 V "
            "rail and VBAT to the battery rail; the 3.3 V LDO/boost then takes VBAT."
        ),
        nets=["VBUS", "VBAT", "GND"],
        parts=[
            TemplatePart("chg", "MCP73831T-2ACI/OT"),
            TemplatePart("cin", "CL10A475KP8NNNC", "4.7uF"),
            TemplatePart("cout", "CL10A475KP8NNNC", "4.7uF"),
            TemplatePart("rprog", "RC0603FR-072K2L", "2.2k"),
            TemplatePart("rled", "RC0603FR-071KL", "1k"),
            TemplatePart("dstat", "LED_G_0603"),
            TemplatePart("jbat", "S2B-PH-K-S(LF)(SN)"),
        ],
        connections=[
            TemplateConnection("VBUS", [("chg", "VDD"), ("cin", "1"), ("rled", "1")]),
            TemplateConnection("VBAT", [("chg", "VBAT"), ("cout", "1"), ("jbat", "1")]),
            TemplateConnection("GND", [("chg", "VSS"), ("cin", "2"), ("cout", "2"), ("rprog", "2"), ("jbat", "2")]),
            TemplateConnection("PROG", [("chg", "PROG"), ("rprog", "1")]),
            TemplateConnection("STAT_LED", [("rled", "2"), ("dstat", "A")]),
            TemplateConnection("STAT", [("chg", "STAT"), ("dstat", "K")]),
        ],
    ),
    "esp32_c3_core": Template(
        name="esp32_c3_core",
        title="ESP32-C3-MINI-1 core (power, EN strap, boot button)",
        category="mcu",
        description=(
            "ESP32-C3-MINI-1 with the bring-up parts the datasheet requires: 3V3 with 10 µF "
            "bulk + 100 nF decoupling, EN pulled up through 10 kΩ with a 100 nF delay cap "
            "to GND, and a BOOT button on IO9 to GND (hold at reset to enter the download "
            "mode; IO9 has an internal pull-up). Map VCC_3V3 and GND; then allocate_pin "
            "for every peripheral. Its native USB (USB_D+/USB_D−) can go straight to the "
            "usb_c_5v_input template's USB_DP/USB_DN nets for programming."
        ),
        nets=["VCC_3V3", "GND"],
        parts=[
            TemplatePart("mcu", "ESP32-C3-MINI-1"),
            TemplatePart("cbulk", "CL10A106MQ8NNNC", "10uF"),
            TemplatePart("cdec", "CC0603KRX7R9BB104", "100nF"),
            TemplatePart("ren", "RC0603FR-0710KL", "10k"),
            TemplatePart("cen", "CC0603KRX7R9BB104", "100nF"),
            TemplatePart("swboot", "TS-1187A-B-A-B"),
        ],
        connections=[
            TemplateConnection("VCC_3V3", [("mcu", "3V3"), ("cbulk", "1"), ("cdec", "1"), ("ren", "1")]),
            TemplateConnection("GND", [("mcu", "1"), ("mcu", "3"), ("mcu", "16"), ("mcu", "24"),
                                       ("cbulk", "2"), ("cdec", "2"), ("cen", "2"), ("swboot", "2")]),
            TemplateConnection("EN", [("mcu", "EN"), ("ren", "2"), ("cen", "1")]),
            TemplateConnection("BOOT", [("mcu", "IO9"), ("swboot", "1")]),
        ],
    ),
    "ir_line_sensor": Template(
        name="ir_line_sensor",
        title="IR reflectance line sensor (TCRT5000)",
        category="sensor",
        description=(
            "One TCRT5000 reflective IR sensor wired the datasheet way: IR LED anode "
            "through a 150 Ω series resistor to VCC, LED cathode to GND; phototransistor "
            "emitter to GND, collector pulled up to VCC through 10 kΩ — the collector "
            "node is OUT (low over a reflective/white surface, high over black). Apply "
            "once per sensor and map OUT to a distinct net (IR_LEFT / IR_CENTER / "
            "IR_RIGHT), then wire each OUT net to its own MCU input (allocate_pin gpio "
            "or adc). Use arrange_grid afterwards to line the sensors up as a front row."
        ),
        nets=["VCC", "GND", "OUT"],
        parts=[
            TemplatePart("sensor", "TCRT5000"),
            TemplatePart("rled", "RC0603FR-07150RL", "150"),
            TemplatePart("rpu", "RC0603FR-0710KL", "10k"),
        ],
        connections=[
            TemplateConnection("VCC", [("rled", "1"), ("rpu", "1")]),
            # Internal (not in `nets`): instantiate() suffixes it per instance so
            # three sensors don't share one LED node.
            TemplateConnection("LED_A", [("rled", "2"), ("sensor", "1")]),
            TemplateConnection("GND", [("sensor", "2"), ("sensor", "4")]),
            TemplateConnection("OUT", [("sensor", "3"), ("rpu", "2")]),
        ],
    ),
    "relay_lock_driver": Template(
        name="relay_lock_driver",
        title="Relay lock driver (SRD 5 V relay + NPN + flyback)",
        category="actuator",
        description=(
            "The switching stage a door lock / lamp / pump needs, wired the datasheet way: "
            "a GPIO drives an MMBT3904 through a 1 kOhm base resistor, the relay coil sits "
            "between the 5 V rail and the collector, and a 1N4148WS flyback diode across the "
            "coil (cathode on the 5 V side) absorbs the collapse spike that would otherwise "
            "destroy the transistor. Map CTRL to an allocate_pin gpio and bring the contact "
            "side (COM + NO for fail-secure, or NC for fail-safe) out to a screw terminal - "
            "never drive a relay coil straight from a GPIO."
        ),
        nets=["VCC_5V", "GND", "CTRL"],
        parts=[
            TemplatePart("relay", "SRD-05VDC-SL-C"),
            TemplatePart("q", "MMBT3904"),
            TemplatePart("rbase", "RC0603FR-071KL", "1k"),
            TemplatePart("dfly", "1N4148WS"),
        ],
        connections=[
            TemplateConnection("VCC_5V", [("relay", "1"), ("dfly", "1")]),
            TemplateConnection("CTRL", [("rbase", "1")]),
            TemplateConnection("BASE", [("rbase", "2"), ("q", "1")]),
            TemplateConnection("COIL_LOW", [("relay", "5"), ("q", "3"), ("dfly", "2")]),
            TemplateConnection("GND", [("q", "2")]),
        ],
    ),
    "i2c_pullups": Template(
        name="i2c_pullups",
        title="I²C bus pull-ups (4.7 kΩ)",
        category="interface",
        description=(
            "The two 4.7 kΩ pull-up resistors an I²C bus needs — SDA and SCL each "
            "pulled up to the logic rail."
        ),
        nets=["SDA", "SCL", "VCC"],
        parts=[
            TemplatePart("rsda", "RC0603FR-074K7L", "4.7k"),
            TemplatePart("rscl", "RC0603FR-074K7L", "4.7k"),
        ],
        connections=[
            TemplateConnection("SDA", [("rsda", "1")]),
            TemplateConnection("SCL", [("rscl", "1")]),
            TemplateConnection("VCC", [("rsda", "2"), ("rscl", "2")]),
        ],
    ),
}


# --- mechanical block library (Week-4 6.4) ---------------------------------- #
# Mechanical templates are the enclosure/mount analogue of the electrical blocks:
# a vetted, parametric sequence of `mechanical_tools` calls (so they inherit the
# same anti-hallucination guard + produce a revertable commit), sized from the
# placed board. Same rule — author only fully-verified blocks; where a dimension
# is a real standard (35 mm DIN rail, EN 50022) it's encoded, and where a pattern
# is inherently generic the description says so.

@dataclass(frozen=True)
class MechTemplate:
    name: str
    title: str
    category: str            # "enclosure" | "mount"
    description: str
    build: "callable"        # build(project) -> None ; composes mechanical_tools


def _ensure_outline(project) -> None:
    from app.services import mechanical_tools as mt
    if project.mechanical is None or project.mechanical.outline is None:
        mt.set_board_outline(project, shape="rounded_rect")  # defaults from the board


def _replace_corner_holes(project, standoff: str = "M3") -> None:
    """Rebuild the 4 collision-aware corner M3 holes (the deriver keeps them clear
    of parts, so the result passes mechanical DRC MECH_R04)."""
    from app.services import mechanical_derive as md, mechanical_tools as mt
    board = project.board
    project.mechanical.mountingHoles = []  # this template owns the hole pattern
    for hole in md.place_mounting_holes(project, board.width, board.height):
        mt.add_mounting_hole(project, x=hole.x, y=hole.y, diameter=hole.diameter,
                             standoff=standoff)


def _build_enclosure(project, *, kind: str, lip: float | None = None) -> None:
    from app.services import mechanical_derive as md, mechanical_tools as mt
    _ensure_outline(project)
    _replace_corner_holes(project)
    ct, cb = md.derived_clearances(project)
    mt.set_enclosure(project, kind=kind, clearance_top=ct, clearance_bottom=cb, lip=lip)


def _build_din_rail(project) -> None:
    from app.services import mechanical_tools as mt
    _build_enclosure(project, kind="box")
    board = project.board
    w, h = board.width, board.height
    rail = min(35.0, round(h, 2))              # TS35 / EN 50022 rail width = 35 mm
    y0 = round((h - rail) / 2.0, 2)
    mt.add_keepout(project, x=0.0, y=y0, width=w, height=rail,
                   reason="DIN rail channel (35 mm, EN 50022)")


def _build_robot_chassis(project) -> None:
    # Open standoff mount (no enclosure walls): a 4-point M3 bolt pattern with M3
    # standoffs to lift the board off the chassis. Generic 4-corner pattern (sized
    # to the board), kept clear of parts.
    _ensure_outline(project)
    _replace_corner_holes(project, standoff="M3")


MECH_TEMPLATES: dict[str, MechTemplate] = {
    "enclosure_generic_box": MechTemplate(
        name="enclosure_generic_box", title="Generic box enclosure", category="enclosure",
        description=(
            "A closed box enclosure sized to the board: outline, 4 corner M3 mounting "
            "holes (kept clear of parts), and top/bottom clearances set from the "
            "tallest components so the lid closes."
        ),
        build=lambda p: _build_enclosure(p, kind="box"),
    ),
    "enclosure_snap_fit": MechTemplate(
        name="enclosure_snap_fit", title="Snap-fit box enclosure", category="enclosure",
        description=(
            "Like the generic box but with a 1.5 mm snap-fit lid lip (no screws for "
            "the lid) — outline, 4 corner M3 holes, height-derived clearances."
        ),
        build=lambda p: _build_enclosure(p, kind="snap_fit", lip=1.5),
    ),
    "mount_din_rail": MechTemplate(
        name="mount_din_rail", title="DIN-rail mount enclosure", category="mount",
        description=(
            "A box enclosure with a 35 mm DIN-rail channel keep-out (TS35 / EN 50022) "
            "down the board centre for a DIN-rail clip, plus corner M3 holes."
        ),
        build=_build_din_rail,
    ),
    "mount_robot_chassis": MechTemplate(
        name="mount_robot_chassis", title="Robot-chassis standoff mount", category="mount",
        description=(
            "An open 4-point M3 chassis mount with M3 standoffs to bolt the board to "
            "a robot chassis (no enclosure walls). Generic 4-corner pattern sized to "
            "the board and kept clear of parts."
        ),
        build=_build_robot_chassis,
    ),
}


def get_mech_template(name: str) -> MechTemplate | None:
    return MECH_TEMPLATES.get(name)


def list_templates() -> list[dict]:
    """Serializable summary for the agent's tool description / a UI picker —
    electrical blocks (with nets/parts) + mechanical blocks (enclosure/mount)."""
    out = [
        {
            "name": t.name, "title": t.title, "category": t.category,
            "description": t.description, "nets": list(t.nets),
            "parts": [{"role": p.role, "mpn": p.mpn, "value": p.value} for p in t.parts],
        }
        for t in TEMPLATES.values()
    ]
    out += [
        {
            "name": m.name, "title": m.title, "category": m.category,
            "description": m.description, "kind": "mechanical",
        }
        for m in MECH_TEMPLATES.values()
    ]
    # Form-factor blocks (Week-5 7.3): fixed-outline starter boards (HAT/shield).
    from app.services import form_factors
    out += form_factors.listing()
    return out


def get_template(name: str) -> Template | None:
    return TEMPLATES.get(name)


def instantiate(
    project: Project,
    template: Template,
    net_map: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Replay a template into the project: add each part (real refdes assigned by
    add_component) then wire the connections over the mapped nets. `net_map` maps
    placeholder net -> real project net; unmapped placeholders are created as-is.
    Returns (role->refdes, [messages]). Raises ToolError (via the tools) on any
    library/rail violation, so a bad instantiation fails loudly rather than
    silently producing a broken block."""
    from app.services import tools  # local import avoids a cycle

    net_map = net_map or {}
    role_to_refdes: dict[str, str] = {}
    for tp in template.parts:
        res, _ = tools.add_component(project, mpn=tp.mpn, value=tp.value)
        role_to_refdes[tp.role] = res["refdes"]

    msgs: list[str] = []
    first = role_to_refdes[template.parts[0].role]
    for conn in template.connections:
        if conn.net in net_map:
            net = net_map[conn.net]
        elif conn.net in template.nets:
            net = conn.net  # public placeholder the caller chose not to map
        else:
            # Internal node: unique per instance, else two applications of the
            # same block would merge their internal nets (three IR sensors'
            # LED anodes tied together, each behind its own resistor).
            net = f"{conn.net}_{first}"
        pins = [[role_to_refdes[role], pin] for role, pin in conn.pins]
        tools.connect(project, net_name=net, pins=pins)
        msgs.append(f"{net}: {', '.join(f'{r}.{p}' for r, p in pins)}")
    return role_to_refdes, msgs


def apply(
    project: Project, *, template_name: str, net_map: dict[str, str] | None = None
) -> tuple[dict, DiffEntry]:
    """Agent tool entry point — instantiate a named template (electrical OR
    mechanical). Returns the tool result + a DiffEntry."""
    from app.services.tools import ToolError

    template = get_template(template_name)
    if template is not None:
        _check_input_envelope(project, template, net_map)
        role_to_refdes, msgs = instantiate(project, template, net_map)
        return (
            {"template": template_name, "created": role_to_refdes, "wired": msgs},
            DiffEntry(
                kind="add",
                summary=f"Applied template '{template.title}' ({len(role_to_refdes)} parts)",
            ),
        )

    mech = get_mech_template(template_name)
    if mech is not None:
        return apply_mech(project, mech)

    # Form-factor blocks (7.3): standard-outline starter boards. Unlike the
    # mechanical blocks they need no placed board — they SEED the board (spec
    # dimensions + the standard header locked at its spec position).
    from app.services import form_factors
    if form_factors.get(template_name) is not None:
        return form_factors.apply(project, template_name)

    raise ToolError(
        f"No template named {template_name!r}. Available: "
        f"{', '.join(list(TEMPLATES) + list(MECH_TEMPLATES) + list(form_factors.FORM_FACTORS))}."
    )


def _check_input_envelope(
    project: Project, template: Template, net_map: dict[str, str] | None
) -> None:
    """Refuse a vetted power block outside the input voltage it was vetted for.

    Only judged when the block's input placeholder is mapped onto the design's
    INPUT rail and the user actually stated what feeds it — a block whose input
    is a regulated rail downstream (12 V -> buck 5 V -> LDO 3.3 V) is a normal
    cascade and is left alone, because the declared input says nothing about it.
    """
    from app.services import verifier
    from app.services.tools import ToolError

    if template.input_range is None:
        return
    target = (net_map or {}).get(template.input_net, template.input_net)
    if not verifier.up_is_input_rail(target):
        return  # a downstream rail — R58 owns that judgement, with real numbers

    lo_declared, hi_declared = verifier._declared_input_range(project)
    if lo_declared is None and hi_declared is None:
        single = verifier._declared_input_voltage(project)
        if single is None:
            return  # nothing stated — build it and let R58 judge the result
        lo_declared = hi_declared = single

    lo_ok, hi_ok = template.input_range
    worst = lo_declared if lo_declared is not None else hi_declared
    best = hi_declared if hi_declared is not None else lo_declared
    if worst is not None and worst < lo_ok:
        raise ToolError(
            f"'{template.name}' is only vetted for {lo_ok:g}-{hi_ok:g} V on {target}, but "
            f"this design's input goes down to {worst:g} V. Applying it here would build a "
            f"supply that stops working before the battery is empty. Use a boost or a "
            f"low-dropout block instead, or state a different input."
        )
    if best is not None and best > hi_ok:
        raise ToolError(
            f"'{template.name}' is only vetted for {lo_ok:g}-{hi_ok:g} V on {target}, but "
            f"this design's input reaches {best:g} V. Applying it here would exceed the "
            f"regulator's rating. Pick a block rated for the real input voltage."
        )


def apply_mech(project: Project, mech: MechTemplate) -> tuple[dict, DiffEntry]:
    """Compose a vetted mechanical block onto the project's mechanical IR through
    `mechanical_tools`. Requires a placed board (the geometry is sized from it)."""
    from app.services.tools import ToolError

    if project.board is None or not project.board.components:
        raise ToolError(
            "No placed board — run auto-place before applying a mechanical template."
        )
    mech.build(project)
    return (
        {"template": mech.name, "kind": "mechanical", "category": mech.category},
        DiffEntry(kind="add", summary=f"Applied template '{mech.title}'"),
    )


def part_exists(mpn: str) -> bool:
    return library.get_part(mpn) is not None
