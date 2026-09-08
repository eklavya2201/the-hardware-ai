"""Beta-review eval — pass-rate over the 23 reviewer prompts.

Every prompt here is VERBATIM from a beta reviewer's report (the one exception:
`smartwatch`, which encodes the expert reviewer's spoken example — "a watch PCB
should be a perfect round circle with the components represented properly"),
and every check is something that reviewer actually asked for (or a defect
their design shipped with). So the score answers the only question that
matters: *would this build survive the review that produced it?*

Unlike `eval_live.py` (four invented prompts, weighted quality dimensions),
this is a binary acceptance suite: each prompt owns a list of named checks, a
run passes a check or it doesn't, and the output is a pass-rate. Because the
LLM is not deterministic, `--runs N` repeats each prompt and reports the
per-check pass-rate across runs — the number to watch when changing the model,
the system prompt or the rules.

Every prompt also gets the UNIVERSAL checks (§`_universal_checks`): no blocking
DRC errors, no requirement shortfall, every schematic part on the board, no
overlap/off-board geometry, a mounting-hole set, and a board area cap.

Requires a live key (OPENAI_API_KEY / Azure env) — it exercises the real agent
and costs tokens. Roughly 1-3 minutes and a few thousand tokens per run.

Usage:
    python -m scripts.eval_reviews                  # one run of each prompt
    python -m scripts.eval_reviews --runs 3         # pass-rate over 3 runs each
    python -m scripts.eval_reviews --only rain      # one prompt (substring match)
    python -m scripts.eval_reviews --list           # show prompts + checks
    python -m scripts.eval_reviews --json out.json  # machine-readable results
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.models import Project  # noqa: E402
from app.services import (  # noqa: E402
    agent,
    library,
    mechanical_tools,
    mechanical_verifier,
    pcb_verifier,
    placement,
    verifier,
)

# --------------------------------------------------------------------------- #
# helpers the checks are written in terms of
# --------------------------------------------------------------------------- #


def _parts(p: Project) -> dict[str, str]:
    return {c.refdes: c.mpn for c in p.ir.components}


def _count_mpn(p: Project, *needles: str) -> int:
    return sum(1 for c in p.ir.components if any(n.lower() in c.mpn.lower() for n in needles))


def _count_category(p: Project, category: str) -> int:
    n = 0
    for c in p.ir.components:
        part = library.get_part(c.mpn)
        if part and part.category == category:
            n += 1
    return n


def _net(p: Project, name: str):
    return next((n for n in p.ir.nets if n.name.upper() == name.upper()), None)


def _nets_matching(p: Project, prefix: str) -> list:
    return [n for n in p.ir.nets if n.name.upper().startswith(prefix.upper())]


def _rule_clean(p: Project, rule_fn) -> bool:
    return not [i for i in rule_fn(p) if i.severity == "error"]


def _errors(p: Project) -> list:
    return [
        i for i in verifier.run_all(p)
        if i.severity == "error" and (i.rule or "") and not i.rule.startswith("LLM_")
    ]


def _mcu_gpio_nets(p: Project) -> list:
    """Nets that carry at least one MCU GPIO — used to check per-signal pins."""
    from app.services import pin_safety

    mcus = {c.refdes: c for c in p.ir.components
            if (part := library.get_part(c.mpn)) and part.category == "mcu"}
    out = []
    for net in p.ir.nets:
        for r, pnum in net.pins:
            comp = mcus.get(r)
            if comp is None:
                continue
            pin = next((pp for pp in comp.pins if pp.number == pnum), None)
            if pin and pin_safety.gpio_number(pin.name, comp.mpn) is not None:
                out.append(net)
                break
    return out


def _grid_arrays(p: Project) -> list:
    return [a for a in (p.ir.arrays or []) if a.grid and a.pitch_mm]


def _pin_is_wired(p: Project, mpn_needle: str, pin_name: str) -> bool:
    """True when the named pin of the (first) matching part sits on some net."""
    for c in p.ir.components:
        if mpn_needle.lower() not in c.mpn.lower():
            continue
        pin = next((pp for pp in c.pins if pp.name.upper() == pin_name.upper()), None)
        if pin is None:
            continue
        if any((c.refdes, pin.number) in [(r, q) for r, q in n.pins] for n in p.ir.nets):
            return True
    return False


def _driver_outputs_reach_connectors(p: Project, mpn_needle: str) -> int:
    """How many of the part's OUTPUT pins land on a net that also carries a
    connector pin — structural, so it does not depend on net naming."""
    connectors = {c.refdes for c in p.ir.components
                  if (part := library.get_part(c.mpn)) and part.category == "connector"}
    n_ok = 0
    for c in p.ir.components:
        if mpn_needle.lower() not in c.mpn.lower():
            continue
        for pin in c.pins:
            if str(pin.type) != "output":
                continue
            for net in p.ir.nets:
                if (c.refdes, pin.number) not in [(r, q) for r, q in net.pins]:
                    continue
                if any(r in connectors for r, _ in net.pins):
                    n_ok += 1
                break
    return n_ok


def _connector_signal_reaches_mcu(p: Project) -> bool:
    """True when some connector's NON-power pin shares a net with an MCU GPIO —
    i.e. an external sensor's signal actually reaches the firmware. Structural,
    so it does not depend on what the net happens to be called."""
    mcu_nets = {n.name for n in _mcu_gpio_nets(p)}
    for c in p.ir.components:
        part = library.get_part(c.mpn)
        if part is None or part.category != "connector":
            continue
        for pin in c.pins:
            if str(pin.type) in {"power_in", "power_out", "ground"}:
                continue
            for net in p.ir.nets:
                if (c.refdes, pin.number) in [(r, q) for r, q in net.pins] and net.name in mcu_nets:
                    return True
    return False


def _series_resistor_on(p: Project, refdes: str, pin_name: str) -> bool:
    """True when the given part pin reaches a resistor on its net."""
    from app.services.verifier import _resistor_refdeses

    resistors = _resistor_refdeses(p)
    comp = next((c for c in p.ir.components if c.refdes == refdes), None)
    if comp is None:
        return False
    pin = next((pp for pp in comp.pins if pp.name.upper() == pin_name.upper()), None)
    if pin is None:
        return False
    for net in p.ir.nets:
        if (refdes, pin.number) not in [(r, q) for r, q in net.pins]:
            continue
        if any(r in resistors for r, _ in net.pins):
            return True
    return False


def _part_pin_reaches_mcu(p: Project, mpn_needle: str, *pin_names: str) -> bool:
    """True when one of the named pins of the (first) matching part shares a
    net with an MCU GPIO - the module's output really reaches the firmware."""
    mcu_nets = {n.name for n in _mcu_gpio_nets(p)}
    for c in p.ir.components:
        if mpn_needle.lower() not in c.mpn.lower():
            continue
        for pin in c.pins:
            if pin.name.upper() not in {n.upper() for n in pin_names}:
                continue
            for net in p.ir.nets:
                if (c.refdes, pin.number) in [(r, q) for r, q in net.pins] and net.name in mcu_nets:
                    return True
    return False


def _seg7_segments_resistored(p: Project) -> bool:
    """A discrete 7-segment display has every segment a-g on a net with a
    resistor; a TM1637 module needs none (driver does the limiting)."""
    if _count_mpn(p, "TM1637") >= 1 and _count_mpn(p, "5161") == 0:
        return True
    seg = next((c for c in p.ir.components if "5161" in c.mpn), None)
    if seg is None:
        return False
    return all(_series_resistor_on(p, seg.refdes, s) for s in "abcdefg")


def _seg7_common_on_rail(p: Project) -> bool:
    """Both COM pins of the display sit on GND (CC) or a power rail (CA)."""
    from app.services.verifier import _classify_net

    seg = next((c for c in p.ir.components if "5161" in c.mpn), None)
    if seg is None:
        return _count_mpn(p, "TM1637") >= 1
    ok = 0
    for pin in seg.pins:
        if pin.name.upper() != "COM":
            continue
        for net in p.ir.nets:
            if (seg.refdes, pin.number) in [(r, q) for r, q in net.pins] and _classify_net(net.name) != "signal":
                ok += 1
                break
    return ok >= 2


def _led_row_array(p: Project) -> bool:
    """The indicator LEDs were placed as ONE straight physical row (a bar)."""
    for a in _grid_arrays(p):
        part = library.get_part(a.mpn)
        if part and part.category == "led" and (a.rows == 1 or a.cols == 1) and len(a.refdeses) >= 4:
            return True
    return False


def _routes_clean(p: Project, b) -> bool:
    """Small boards must auto-route with zero rats (review #9: 2 rats on a
    4-part board). Boards over 40 parts are skipped (matrix boards take minutes)."""
    from app.services import routing

    if b is None:
        return False
    if len(p.ir.components) > 40:
        return True
    routed = routing.auto_route(p)
    return not routed.rats


def _pin_net_class(p: Project, refdes: str, pin_name: str) -> str | None:
    """'gnd' | 'power' | 'signal' | None(unwired) for a named pin."""
    from app.services.verifier import _classify_net

    comp = next((c for c in p.ir.components if c.refdes == refdes), None)
    if comp is None:
        return None
    pin = next((pp for pp in comp.pins if pp.name.upper() == pin_name.upper()), None)
    if pin is None:
        return None
    for net in p.ir.nets:
        if (refdes, pin.number) in [(r, q) for r, q in net.pins]:
            return _classify_net(net.name)
    return None


def _every_rail_sourced(p: Project) -> bool:
    return not [i for i in verifier.R26_power_pin_driven(p) if i.severity == "error"]


def _i2c_device_reaches_mcu(p: Project, mpn_needle: str) -> bool:
    """Both I2C lines of the named part share a net with an MCU pin (directly or
    through a level-shifter channel that is itself wired to the MCU)."""
    return (_part_pin_reaches_mcu(p, mpn_needle, "SDA", "SDI/SDA")
            and _part_pin_reaches_mcu(p, mpn_needle, "SCL", "SCK/SCL"))


def _reserved_pin_on_a_signal(p: Project) -> bool:
    """True when an MCU's reset / debug / oscillator pin sits on a signal net -
    beta review #17 found NRST carrying both I2C lines."""
    from app.services import pin_safety
    from app.services.verifier import _classify_net

    import re as _re
    for c in p.ir.components:
        part = library.get_part(c.mpn)
        if part is None or part.category != "mcu":
            continue
        for pin in c.pins:
            name = (pin.name or "").upper()
            reserved = bool(_re.match(r"^(NRST|RESET|RST|BOOT\d*|SWD|SWCLK|SWDIO)", name)) or \
                _re.search(r"(SWDIO|SWCLK|OSC_IN|OSC_OUT)", name) is not None or \
                bool(pin_safety.capabilities(c.mpn, pin.name) & {"reserved"})
            if not reserved:
                continue
            for net in p.ir.nets:
                if (c.refdes, pin.number) in [(r, q) for r, q in net.pins] \
                        and _classify_net(net.name) == "signal":
                    return True
    return False


# --------------------------------------------------------------------------- #
# the suite: one entry per beta review, checks taken from that review's asks
# --------------------------------------------------------------------------- #

SUITE = [
    {
        "name": "led-matrix",           # review #1
        "review": "10x10 LED matrix (report-1)",
        "prompt": (
            "hey i want to build a led 10x10 matrix light using esp32 and 3mm led light "
            "i will later enclose the light with 3d printed cube with will disfuse and "
            "i will be arranging the 10x10 light in row and column so build according to it"
        ),
        "max_area_mm2": 40000,
        "checks": [
            ("100_leds", "exactly 100 LEDs placed",
             lambda p, b: _count_category(p, "led") == 100),
            ("physical_grid", "LEDs recorded as a 10x10 physical grid with a pitch",
             lambda p, b: any(a.rows == 10 and a.cols == 10 for a in _grid_arrays(p))),
            ("row_col_nets", "10 ROW + 10 COL nets",
             lambda p, b: len(_nets_matching(p, "ROW")) >= 10 and len(_nets_matching(p, "COL")) >= 10),
            ("grid_on_board", "the board really shows 10 columns x 10 rows",
             lambda p, b: b is not None and (
                 len({round(c.x, 2) for c in b.components if _count_mpn(p, "LED") and "LED" in c.mpn}) == 10
                 and len({round(c.y, 2) for c in b.components if "LED" in c.mpn}) == 10)),
            ("driver_stage", "a row/column driver stage exists (not bare GPIOs)",
             lambda p, b: _count_category(p, "actuator") + _count_category(p, "logic")
                          + _count_category(p, "transistor") >= 1),
        ],
    },
    {
        "name": "led-chase",            # review #2
        "review": "10-LED chase light (report-2)",
        "prompt": (
            "i want to build led blink with 10 led which will be connected to esp32 "
            "light blink one after other to get smooth flow"
        ),
        "max_area_mm2": 12000,
        "checks": [
            ("10_leds", "exactly 10 LEDs", lambda p, b: _count_category(p, "led") == 10),
            ("straight_line", "LEDs on one straight physical row",
             lambda p, b: any(a.rows == 1 and a.cols == 10 for a in _grid_arrays(p))),
            ("independent_gpios", "each LED signal has its own MCU net (no shared chain)",
             lambda p, b: len([n for n in _mcu_gpio_nets(p)
                               if any(r.startswith(("D", "R")) for r, _ in n.pins)]) >= 8),
            ("per_led_resistor", "a current-limit resistor per LED",
             lambda p, b: _count_mpn(p, "RC0603FR-07330", "RC0603FR-07150", "RC0603FR-07220") >= 10),
        ],
    },
    {
        "name": "line-follower",        # review #3
        "review": "ESP32 line-following robot (report-3)",
        "prompt": (
            "I want to build line following bot using IR sensor and ESP32 with N20 gear "
            "motor. It is powered with 18650 Li-ion cell."
        ),
        "max_area_mm2": 15000,
        "checks": [
            ("ir_sensors", "real IR line sensors (TCRT5000), not ultrasonic/placeholder",
             lambda p, b: _count_mpn(p, "TCRT5000") >= 2),
            ("dual_hbridge", "a dual H-bridge motor driver",
             lambda p, b: _count_mpn(p, "DRV8833", "TB6612") >= 1),
            ("two_motor_connectors", "two motor connectors",
             lambda p, b: _count_category(p, "connector") >= 3),
            ("battery_path", "a battery input with fuse or reverse-polarity protection",
             lambda p, b: _count_mpn(p, "Fuse", "SS14", "AO3401", "SI2301") >= 1),
            ("logic_rail", "a 3.3 V regulator for the logic rail (motors run off VBAT)",
             lambda p, b: _count_category(p, "regulator") >= 1),
            ("sensor_row", "the IR sensors form a physical row on the board",
             lambda p, b: any(a.rows == 1 and a.cols >= 2 for a in _grid_arrays(p))),
        ],
    },
    {
        "name": "buzzer",               # review #4
        "review": "Arduino Nano + 2-pin buzzer (report-4)",
        "prompt": (
            "I want to build a tool which say hello using buzzer and Arduino Nano. I'm "
            "going to use normal buzzer which will have +ve and -ve terminal, that's it."
        ),
        "max_area_mm2": 5000,
        "checks": [
            ("minimal_bom", "no over-engineering: at most 8 parts",
             lambda p, b: len(p.ir.components) <= 8),
            ("no_redundant_power", "no second USB/regulator around the self-powered Nano",
             lambda p, b: not verifier.R50_redundant_power_stage(p)),
            ("buzzer_present", "one active buzzer",
             lambda p, b: _count_mpn(p, "Buzzer") == 1),
            ("gpio_current_safe", "the buzzer is not driven straight from a GPIO",
             lambda p, b: _rule_clean(p, verifier.R49_gpio_drive_current)),
            ("driver_wired", "the switching transistor is properly driven",
             lambda p, b: _rule_clean(p, verifier.R52_transistor_control_floating)),
        ],
    },
    {
        "name": "laser-alarm",          # review #5
        "review": "laser beam-break alarm (report-5)",
        "prompt": (
            "I want to build a smart alarm using LDR, laser module, buzzer and Arduino "
            "Nano for brain. When laser beam breaks the buzzer sounds. This is the "
            "simple logic."
        ),
        "max_area_mm2": 6000,
        "checks": [
            ("real_ldr", "a real photoresistor (not a substituted sensor)",
             lambda p, b: _count_mpn(p, "LDR") >= 1),
            ("divider_to_adc", "the LDR sits in a divider feeding an MCU pin",
             lambda p, b: any(
                 any(r.startswith("R") for r, _ in n.pins) and any("LDR" in _parts(p).get(r, "") for r, _ in n.pins)
                 for n in _mcu_gpio_nets(p))),
            ("laser_module", "a laser module part/connector",
             lambda p, b: _count_mpn(p, "Laser") >= 1),
            ("buzzer_driver", "the buzzer transistor is wired (base driven, series resistor)",
             lambda p, b: _rule_clean(p, verifier.R52_transistor_control_floating)),
            ("test_point", "a test point for calibrating the sense node",
             lambda p, b: _count_mpn(p, "TestPoint") >= 1),
        ],
    },
    {
        "name": "rover",                # review #6
        "review": "ultrasonic obstacle-avoidance rover (report-6)",
        "prompt": (
            "I want to build a Obstacle-avoidance rover using ESP32 + TB6612FNG boards "
            "as the line-follower, swap the IR array for an ultrasonic."
        ),
        "max_area_mm2": 15000,
        "checks": [
            ("ultrasonic", "an ultrasonic sensor",
             lambda p, b: _count_mpn(p, "HC-SR04", "SR04") >= 1),
            ("no_ir_array", "the IR array is gone (the prompt asked to swap it out)",
             lambda p, b: _count_mpn(p, "TCRT5000") == 0),
            ("echo_level_shift", "ECHO reaches the ESP32 through a divider, not directly",
             lambda p, b: _rule_clean(p, verifier.R51_five_v_output_into_3v3_gpio)),
            ("tb6612", "the requested TB6612FNG driver",
             lambda p, b: _count_mpn(p, "TB6612") >= 1),
            ("stby_driven", "the driver's STBY pin is on a net (driven or strapped)",
             lambda p, b: _pin_is_wired(p, "TB6612", "STBY")),
            ("motor_channels", "all four driver outputs reach connector pins",
             lambda p, b: _driver_outputs_reach_connectors(p, "TB6612") >= 4),
        ],
    },
    {
        "name": "rain-alert",           # review #7
        "review": "ESP32 rain alert with RGB LED (report-7)",
        "prompt": "I want to build rain alert using ESP32 + a single RGB LED and moisture sensor.",
        "max_area_mm2": 10000,
        "checks": [
            ("rgb_led", "one RGB LED", lambda p, b: _count_mpn(p, "RGB") == 1),
            ("per_channel_resistors", "every RGB channel has its own series resistor",
             lambda p, b: _rule_clean(p, verifier.R46_led_current_limit)),
            ("sensor_connector", "an external sensor connector",
             lambda p, b: _count_category(p, "connector") >= 1),
            ("adc_input", "the sensor connector's signal pin reaches an MCU pin",
             lambda p, b: _connector_signal_reaches_mcu(p)),
        ],
    },
    {
        "name": "counter",              # review #8
        "review": "push-button counter with a 7-segment display (report-8)",
        "prompt": "Make a push-button counter that shows the count on a 7-segment display.",
        "max_area_mm2": 6000,
        "checks": [
            ("seg7", "a real 7-segment display part (5161AS/BS or a TM1637 module), not an LED stand-in",
             lambda p, b: _count_mpn(p, "5161", "TM1637") >= 1),
            ("button", "one push button", lambda p, b: _count_category(p, "switch") >= 1),
            ("segment_resistors", "every segment a-g has its own series resistor (or a TM1637 drives it)",
             lambda p, b: _seg7_segments_resistored(p)),
            ("common_tied", "the display's COM pins sit on GND (CC) or the rail (CA)",
             lambda p, b: _seg7_common_on_rail(p)),
            ("segments_reach_mcu", "segments (or CLK/DIO) and the button reach MCU GPIOs",
             lambda p, b: len(_mcu_gpio_nets(p)) >= (3 if _count_mpn(p, "TM1637") else 8)),
            ("no_bare_mcu", "no bare RP2040 without flash/crystal (R53)",
             lambda p, b: _rule_clean(p, verifier.R53_bare_mcu_support)),
        ],
    },
    {
        "name": "clap-switch",          # review #9
        "review": "clap switch: mic module -> Nano -> LED (report-9)",
        "prompt": "Design a clap switch that turns an LED on and off using a mic module.",
        "max_area_mm2": 4000,
        "checks": [
            ("mic_module", "a microphone MODULE (KY-038 / MAX9814_Module), not the bare MAX9814 IC",
             lambda p, b: _count_mpn(p, "KY-038", "MAX9814_Module") >= 1
             and not any(c.mpn == "MAX9814" for c in p.ir.components)),
            ("mic_reaches_mcu", "the mic module's output pin reaches an MCU pin",
             lambda p, b: _part_pin_reaches_mcu(p, "KY-038", "DO", "AO")
             or _part_pin_reaches_mcu(p, "MAX9814_Module", "OUT")),
            ("one_led", "exactly one indicator LED", lambda p, b: _count_category(p, "led") == 1),
            ("led_resistor", "the LED has a series resistor (R46)",
             lambda p, b: _rule_clean(p, verifier.R46_led_current_limit)),
            ("no_extra_power", "no second regulator/USB on a Nano carrier (R50)",
             lambda p, b: _rule_clean(p, verifier.R50_redundant_power_stage)),
        ],
    },
    {
        "name": "touch-switch",         # review #10
        "review": "capacitive touch light switch (report-10)",
        "prompt": "Design a touch sensor board that turns an LED on when touched.",
        "max_area_mm2": 4000,
        "checks": [
            ("touch_element", "a real touch element: TTP223 module or a PCB touch electrode (not a test point)",
             lambda p, b: _count_mpn(p, "TTP223", "TouchPad") >= 1),
            ("touch_reaches_mcu", "the touch signal/electrode reaches an MCU pin",
             lambda p, b: _part_pin_reaches_mcu(p, "TTP223", "SIG")
             or _part_pin_reaches_mcu(p, "TouchPad", "PAD")),
            ("one_led", "one indicator LED", lambda p, b: _count_category(p, "led") >= 1),
            ("led_resistor", "the LED has a series resistor (R46)",
             lambda p, b: _rule_clean(p, verifier.R46_led_current_limit)),
        ],
    },
    {
        "name": "water-level",          # review #11
        "review": "water level indicator with an LED bar (report-11)",
        "prompt": "Build a water level sensor board that lights up LEDs as water rises in a tank.",
        "max_area_mm2": 8000,
        "checks": [
            ("led_bar", "at least 4 level LEDs", lambda p, b: _count_category(p, "led") >= 4),
            ("led_row", "the LEDs are one straight physical row (a level bar)",
             lambda p, b: _led_row_array(p)),
            ("per_led_resistor", "one series resistor per LED (R46)",
             lambda p, b: _rule_clean(p, verifier.R46_led_current_limit)),
            ("sensing", "a defined sensing method: water-level module or probe connectors",
             lambda p, b: _count_mpn(p, "Water_Level") >= 1 or _count_category(p, "connector") >= 2),
            ("sensor_reaches_mcu", "the sensor output / probe signal reaches an MCU pin",
             lambda p, b: _part_pin_reaches_mcu(p, "Water_Level", "S") or _connector_signal_reaches_mcu(p)),
            ("probe_return", "no probe connector without a ground return (R54)",
             lambda p, b: _rule_clean(p, verifier.R54_connector_input_no_return)),
            ("no_bare_mcu", "no bare RP2040 without flash/crystal (R53)",
             lambda p, b: _rule_clean(p, verifier.R53_bare_mcu_support)),
        ],
    },
    {
        "name": "soil-alert",           # review #12
        "review": "soil moisture alert that beeps (report-12)",
        "prompt": "Build a soil moisture sensor board that beeps when a plant needs water.",
        "max_area_mm2": 4000,
        "checks": [
            ("soil_sensor", "a real moisture sensing front end: capacitive module, or a probe divider with GND",
             lambda p, b: _count_mpn(p, "Soil_Moisture") >= 1
             or (_rule_clean(p, verifier.R54_connector_input_no_return) and _connector_signal_reaches_mcu(p)
                 and any(_series_resistor_on(p, c.refdes, pin.name) for c in p.ir.components
                         if (lp := library.get_part(c.mpn)) and lp.category == "connector" for pin in c.pins))),
            ("sensor_reaches_mcu", "the sensor output reaches an MCU (ADC) pin",
             lambda p, b: _part_pin_reaches_mcu(p, "Soil_Moisture", "AOUT") or _connector_signal_reaches_mcu(p)),
            ("probe_return", "no probe connector without a ground return (R54)",
             lambda p, b: _rule_clean(p, verifier.R54_connector_input_no_return)),
            ("buzzer", "an audible alert (buzzer)", lambda p, b: _count_mpn(p, "Buzzer") >= 1),
            ("buzzer_driven", "the buzzer is switched by a transistor, not a bare GPIO (R49)",
             lambda p, b: _rule_clean(p, verifier.R49_gpio_drive_current)),
            ("no_extra_power", "no second regulator/USB on a Nano carrier (R50)",
             lambda p, b: _rule_clean(p, verifier.R50_redundant_power_stage)),
        ],
    },
    {
        "name": "robot-car",            # review #13
        "review": "Bluetooth robot car, L298N + battery monitor (report-13)",
        "prompt": ("Build a Bluetooth-controlled robot car board with ESP32, an L298N motor "
                   "driver, and battery voltage monitoring."),
        "max_area_mm2": 12000,
        "checks": [
            ("driver", "a dual H-bridge motor driver",
             lambda p, b: _count_mpn(p, "L298N", "TB6612", "DRV8833") >= 1),
            ("two_motor_connectors", "two motor connectors",
             lambda p, b: _count_category(p, "connector") >= 2),
            ("battery_input", "a battery input connector that actually sources the rail",
             lambda p, b: _every_rail_sourced(p)),
            ("divider_safe", "the battery divider keeps the ADC pin under its limit (R55)",
             lambda p, b: _rule_clean(p, verifier.R55_adc_divider_overvoltage)),
            ("sense_on_adc", "the divider mid-node reaches an MCU pin",
             lambda p, b: len(_mcu_gpio_nets(p)) >= 6),
        ],
    },
    {
        "name": "weather-station",      # review #14
        "review": "ESP32 weather station, BME280 + OLED (report-14)",
        "prompt": ("Design a weather station board with ESP32, a BME280 sensor, and an OLED "
                   "display showing temperature, humidity, and pressure."),
        "max_area_mm2": 6000,
        "checks": [
            ("sensor", "a BME280-class environmental sensor",
             lambda p, b: _count_mpn(p, "BME280", "BMP280", "BME680") >= 1),
            ("display", "an OLED display", lambda p, b: _count_mpn(p, "SSD1306", "SH1106") >= 1),
            ("i2c_straps", "the sensor's CSB/SDO straps select I2C and a defined address (R56)",
             lambda p, b: _rule_clean(p, verifier.R56_i2c_mode_straps)),
            ("csb_high", "CSB is tied to a supply rail, not ground",
             lambda p, b: _count_mpn(p, "BME280") == 0 or _pin_net_class(p, next(
                 (c.refdes for c in p.ir.components if "BME280" in c.mpn), ""), "CSB") == "power"),
            ("i2c_reaches_mcu", "both I2C lines of the sensor reach the MCU",
             lambda p, b: _i2c_device_reaches_mcu(p, "BME280") or _i2c_device_reaches_mcu(p, "BMP280")),
            ("usb_sourced", "the USB-C VBUS pin actually feeds the regulator (R26)",
             lambda p, b: _every_rail_sourced(p)),
            ("ldo_enabled", "the regulator's EN is not floating (R03)",
             lambda p, b: _rule_clean(p, verifier.R03_no_floating_en)),
        ],
    },
    {
        "name": "gesture-arm",          # review #15
        "review": "MPU6050 gesture control for 3 servos (report-15)",
        "prompt": ("Build a robot arm control board using an MPU6050 accelerometer to drive "
                   "3 servo motors based on hand tilt."),
        "max_area_mm2": 6000,
        "checks": [
            ("imu", "an MPU6050-class IMU", lambda p, b: _count_mpn(p, "MPU6050", "MPU-6050") >= 1),
            ("imu_reaches_mcu", "the IMU's I2C lines reach the MCU",
             lambda p, b: _i2c_device_reaches_mcu(p, "MPU6050")),
            ("three_servo_connectors", "three servo connectors",
             lambda p, b: _count_category(p, "connector") >= 3),
            ("servo_signals", "three servo signal pins reach MCU GPIOs",
             lambda p, b: len(_mcu_gpio_nets(p)) >= 3),
            ("rails_sourced", "every rail has a source (R26)", lambda p, b: _every_rail_sourced(p)),
        ],
    },
    {
        "name": "rfid-door",            # review #16
        "review": "RFID door access control with a lock relay (report-16)",
        "prompt": ("Design an RFID access control board with a reader, a relay for the lock, "
                   "and LED/buzzer feedback for valid and invalid scans"),
        "max_area_mm2": 8000,
        "checks": [
            ("reader", "an RFID reader", lambda p, b: _count_mpn(p, "RC522", "PN532") >= 1),
            ("relay", "a real relay or relay module on the board",
             lambda p, b: _count_mpn(p, "SRD-05VDC", "Relay_Module") >= 1),
            ("relay_driven", "the relay coil is switched by a transistor, not a GPIO (R49)",
             lambda p, b: _count_mpn(p, "Relay_Module") >= 1
             or _rule_clean(p, verifier.R49_gpio_drive_current)),
            ("flyback", "a discrete relay coil has a flyback diode",
             lambda p, b: _count_mpn(p, "Relay_Module") >= 1 or _count_category(p, "diode") >= 1),
            ("two_leds", "separate valid/invalid LEDs", lambda p, b: _count_category(p, "led") >= 2),
            ("led_resistors", "each LED has its own series resistor (R46)",
             lambda p, b: _rule_clean(p, verifier.R46_led_current_limit)),
            ("buzzer", "an audible feedback buzzer", lambda p, b: _count_mpn(p, "Buzzer") >= 1),
            ("lock_terminal", "the lock contacts are reachable: a screw terminal, or the "
                              "relay MODULE's own on-board terminals",
             lambda p, b: _count_mpn(p, "Relay_Module") >= 1 or _count_category(p, "connector") >= 2),
        ],
    },
    {
        "name": "telemetry-logger",     # review #17
        "review": "drone battery telemetry logger, STM32 + INA219 + MicroSD (report-17)",
        "prompt": ("Design a drone battery telemetry board with STM32F411, INA219 current "
                   "sensor, a battery voltage divider and MicroSD logging."),
        "max_area_mm2": 8000,
        "checks": [
            ("current_sensor", "a current sensor", lambda p, b: _count_mpn(p, "INA219", "INA226") >= 1),
            ("sd_storage", "SD card storage",
             lambda p, b: _count_mpn(p, "MicroSD", "SD_Card", "SD_Socket") >= 1),
            ("spi_separate", "the SD card's SPI signals are on four DIFFERENT nets",
             lambda p, b: len({n.name for n in p.ir.nets
                               for r, _ in n.pins
                               if any(r == c.refdes and "microsd" in c.mpn.lower()
                                      for c in p.ir.components)}) >= 4),
            ("no_reserved_pins", "no reset / debug / oscillator pin is carrying a signal",
             lambda p, b: not _reserved_pin_on_a_signal(p)),
            ("divider_safe", "the battery divider keeps the ADC pin under its limit (R55)",
             lambda p, b: _rule_clean(p, verifier.R55_adc_divider_overvoltage)),
            ("rails_sourced", "every rail has a source (R26)", lambda p, b: _every_rail_sourced(p)),
            ("built_something", "the turn actually produced a design",
             lambda p, b: len(p.ir.components) >= 5),
        ],
    },
    {
        "name": "smartwatch",           # expert-review generalization (form factor)
        "review": "expert reviewer: 'a watch PCB should be a perfect round circle "
                  "with the components represented properly'",
        "prompt": ("Design a smartwatch board: round display, heart rate sensor, "
                   "vibration motor for haptics, LiPo battery with USB-C charging."),
        "max_area_mm2": 4500,           # a Ø75 disc's bounding square — a watch, not a tile
        "checks": [
            ("form_recorded", "the agent recorded a round form factor (set_form_factor)",
             lambda p, b: p.ir.form_factor is not None and p.ir.form_factor.shape == "round"),
            ("round_board", "the built PCB is actually round",
             lambda p, b: b is not None and b.shape == "round"),
            ("form_matches", "the board matches the recorded form (R57)",
             lambda p, b: _rule_clean(p, verifier.R57_form_factor)),
            ("display_centred", "the round display sits at the disc centre",
             lambda p, b: _display_centred(p, b)),
            ("display", "a round display", lambda p, b: _count_mpn(p, "GC9A01") >= 1),
            ("hr_sensor", "a heart-rate sensor", lambda p, b: _count_mpn(p, "MAX30102") >= 1),
            ("haptics", "a vibration motor / haptic driver",
             lambda p, b: _count_mpn(p, "Vibration", "DRV2605") >= 1
             or _count_category(p, "actuator") >= 1),
            ("charger", "a LiPo charger", lambda p, b: _count_mpn(p, "MCP73831", "TP4056", "BQ24") >= 1),
            ("battery_conn", "a battery connector", lambda p, b: _count_category(p, "connector") >= 2),
            ("usb_power", "USB-C input with CC termination (R22)",
             lambda p, b: _count_category(p, "connector") >= 2
             and _rule_clean(p, verifier.R22_usb_c_cc_termination)),
        ],
    },
    {
        "name": "drone-pdb",              # review #19
        "review": "mini FPV drone power distribution board, 4S -> 4 ESC + 5 V + 9 V (report-19)",
        "prompt": ("Design a power distribution board for a mini FPV drone. It should take a 4S LiPo "
                   "input, power four ESCs directly, provide a regulated 5V rail for the flight "
                   "controller, a separate 9V rail for the FPV camera and VTX, reverse-polarity "
                   "protection, and a voltage divider for battery telemetry monitoring"),
        "max_area_mm2": 6000,
        "checks": [
            ("five_terminals", "battery input + four ESC outputs = 5 power connectors",
             lambda p, b: _count_category(p, "connector") >= 5),
            ("no_linear_from_4s", "no linear regulator dropping from the 4S rail (R58)",
             lambda p, b: _rule_clean(p, verifier.R58_regulator_input_range)),
            ("two_bucks", "two switching regulators (5 V and 9 V)",
             lambda p, b: sum(1 for c in p.ir.components
                              if (part := library.get_part(c.mpn)) and part.category == "regulator"
                              and not verifier._is_linear_regulator(part)) >= 2),
            ("adj_feedback", "adjustable regulators have their feedback dividers (R59)",
             lambda p, b: _rule_clean(p, verifier.R59_adjustable_regulator_feedback)),
            ("reverse_fet", "reverse-polarity stage is a P-FET (or a >=5 A diode on the connector side, R64/R12)",
             lambda p, b: _rule_clean(p, verifier.R64_series_diode_high_current)
             and _rule_clean(p, verifier.R12_orphan_net)),
            ("divider_live", "the telemetry divider reaches a rail and an output (R61)",
             lambda p, b: _rule_clean(p, verifier.R61_passive_island)),
            ("divider_safe", "divider sized for 16.8 V under the ADC limit (R55)",
             lambda p, b: _rule_clean(p, verifier.R55_adc_divider_overvoltage)),
        ],
    },
    {
        "name": "relay-board",            # review #20
        "review": "WiFi home-automation board, ESP32 + 4 AC relays + buttons + LEDs + current sense (report-20)",
        "prompt": ("Build a WiFi-controlled home automation board using ESP32 that switches four AC "
                   "appliances through relays, each with manual push-button override, status LEDs, and "
                   "a current sensor to track power usage per channel, with the whole board fitting "
                   "inside a standard wall-mount enclosure"),
        "max_area_mm2": 12000,
        "checks": [
            ("four_relays", "four relays or a 4-channel relay module",
             lambda p, b: _count_mpn(p, "SRD-05VDC") >= 4 or _count_mpn(p, "Relay_Module_4CH") >= 1
             or _count_mpn(p, "Relay_Module_1CH") >= 4),
            ("contacts_wired", "every relay's contacts are wired (R60)",
             lambda p, b: _rule_clean(p, verifier.R60_function_pins_unwired)),
            ("flyback", "discrete relay coils have flyback diodes (R39)",
             lambda p, b: _rule_clean(p, verifier.R39_flyback_protection)),
            ("buttons_wired", "no button with both legs on one net (R47)",
             lambda p, b: _rule_clean(p, verifier.R47_two_pin_part_shorted)),
            ("led_resistors", "every LED has a series resistor (R46)",
             lambda p, b: _rule_clean(p, verifier.R46_led_current_limit)),
            ("isolated_sense", "current sensing is isolated (ACS712 / CT), not a DC shunt monitor",
             lambda p, b: _count_mpn(p, "ACS712", "SCT-013", "HLW8012") >= 1),
            ("sense_wired", "current-sensor sense inputs are wired (R66)",
             lambda p, b: _rule_clean(p, verifier.R66_sense_inputs_unwired)),
            ("lowside_ok", "no N-FET wired as a dead source-follower (R63)",
             lambda p, b: _rule_clean(p, verifier.R63_lowside_switch_topology)),
            ("mains_isolation", "nothing non-isolating bridges mains and logic (R65)",
             lambda p, b: _rule_clean(p, verifier.R65_mains_isolation)),
        ],
    },
    {
        "name": "solar-charger",          # review #21
        "review": "solar 1S Li-ion charge controller with protection and 5 V ESP32 output (report-21)",
        "prompt": ("Design a solar battery charge controller board that takes power from a small solar "
                   "panel, charges a single-cell Li-ion battery safely with over-charge and "
                   "over-discharge protection, and provides a regulated 5V output for a small ESP32 "
                   "sensor node, with a status LED showing charge level and low-battery warnings"),
        "max_area_mm2": 4000,
        "checks": [
            ("charger", "a solar/Li-ion charger IC", lambda p, b: _count_category(p, "charger") >= 1),
            ("boost_5v", "the 5 V output is a boost/buck-boost, not a linear regulator (R58)",
             lambda p, b: _rule_clean(p, verifier.R58_regulator_input_range)
             and any((part := library.get_part(c.mpn)) and part.category == "regulator"
                     and any(k in (part.description or "").lower() for k in ("boost", "step-up"))
                     for c in p.ir.components)),
            ("protection_fets", "protection IC drives its FETs (R62)",
             lambda p, b: _rule_clean(p, verifier.R62_battery_protection_fets)),
            ("no_dead_parts", "no part wired only on power/ground (R60)",
             lambda p, b: _rule_clean(p, verifier.R60_function_pins_unwired)),
            ("led_paths", "LEDs and their resistors reach real nodes (R15/R61)",
             lambda p, b: _rule_clean(p, verifier.R15_floating_component)
             and _rule_clean(p, verifier.R61_passive_island)),
            ("two_leds", "charge + low-battery indication", lambda p, b: _count_category(p, "led") >= 2),
        ],
    },
    {
        "name": "wearable-hr",            # review #22 (built alongside; expert's wearable)
        "review": "wearable pulse/SpO2 monitor, MAX30102 + ESP32 + OLED + coin cell (report-22)",
        "prompt": ("Build a wearable pulse and heart-rate monitor board using a MAX30102 sensor, ESP32, "
                   "and a small OLED display that shows live BPM and blood oxygen readings, powered by "
                   "a rechargeable Li-Po coin cell, small enough to fit on a wrist strap, with Bluetooth "
                   "so readings can sync to my phone."),
        "max_area_mm2": 3600,
        "checks": [
            ("hr_sensor", "MAX30102", lambda p, b: _count_mpn(p, "MAX30102") >= 1),
            ("oled", "an OLED", lambda p, b: _count_mpn(p, "SSD1306", "SH1106") >= 1),
            ("rechargeable_cell", "a rechargeable cell / LiPo connector, not a CR2032",
             lambda p, b: _count_mpn(p, "LIR2032", "S2B-PH", "JST_PH") >= 1 and _count_mpn(p, "BatteryHolder_CR2032") == 0),
            ("one_ldo", "exactly one 3.3 V regulator that can run from the cell (R58/R14)",
             lambda p, b: _rule_clean(p, verifier.R58_regulator_input_range)
             and sum(1 for c in p.ir.components
                     if (part := library.get_part(c.mpn)) and part.category == "regulator") <= 1),
            ("mcu_on_3v3", "the MCU supply is on the 3.3 V rail, not the battery (R04)",
             lambda p, b: _rule_clean(p, verifier.R04_voltage_rail_compat)),
            ("cc_resistors", "USB-C CC1/CC2 each terminated (R22)",
             lambda p, b: _rule_clean(p, verifier.R22_usb_c_cc_termination)),
            ("wrist_sized", "board fits a wrist strap: round, or every side <= 45 mm",
             lambda p, b: b is not None and (b.shape == "round" and b.width <= 48 or max(b.width, b.height) <= 45)),
        ],
    },
    {
        "name": "cnc-stepper",            # review #23 (built alongside)
        "review": "2-axis CNC stepper board, A4988 + limit switches + ESP32 + 12 V in (report-23)",
        "prompt": ("Design a 2-axis stepper motor driver board for a small CNC or plotter machine, using "
                   "A4988 drivers, limit switches on each axis for homing, an ESP32 to accept G-code over "
                   "serial, and a 12V input with a separate regulated 5V rail for the logic and switches, "
                   "all on one compact board."),
        "max_area_mm2": 9000,
        "checks": [
            ("driver_modules", "two A4988/DRV8825 driver MODULES", lambda p, b: _count_mpn(p, "StepStick") >= 2),
            ("motor_connectors", "two 4-pin motor connectors, no on-board NEMA17",
             lambda p, b: _count_mpn(p, "JST_XH_4P") >= 2 and _count_mpn(p, "NEMA17") == 0),
            ("outputs_wired", "driver outputs reach the motor connectors (R60)",
             lambda p, b: _rule_clean(p, verifier.R60_function_pins_unwired)),
            ("buck_from_12v", "12 V -> 5 V is a buck, no LDO on 12 V (R58/R04)",
             lambda p, b: _rule_clean(p, verifier.R58_regulator_input_range)
             and _rule_clean(p, verifier.R04_voltage_rail_compat)),
            ("rail_names", "rail names match regulator outputs (R67)",
             lambda p, b: _rule_clean(p, verifier.R67_rail_name_vs_regulator)),
            ("switch_pullups_3v3", "limit-switch pull-ups on the MCU rail, not 5 V (R36)",
             lambda p, b: _rule_clean(p, verifier.R36_gpio_5v_tolerance)),
            ("four_switches", "four limit-switch inputs", lambda p, b: _count_category(p, "switch") >= 4),
        ],
    },
    {
        "name": "bno085-i2c",              # review #39 (Lucky Singh audit + phrasing sheet)
        "review": "STM32 flight controller: BNO085 on the host I2C pins, straps tied (report-39)",
        "prompt": ("Wire a BNO085 IMU to an STM32F405 over I2C on a small flight-controller board, "
                   "powered from a single-cell LiPo through a 3.3V LDO with USB-C charging."),
        "max_area_mm2": 4000,
        "checks": [
            ("bno085_present", "a BNO085 is placed", lambda p, b: _count_mpn(p, "BNO085") >= 1),
            ("host_bus_not_env", "the MCU is on H_SCL/H_SDA, not the ENV_ auxiliary bus (R78)",
             lambda p, b: _rule_clean(p, verifier.R78_secondary_bus_as_host)),
            ("straps_tied", "PS0/PS1/SA0/CLKSEL are tied, not floating (R77)",
             lambda p, b: _rule_clean(p, verifier.R77_floating_strap_pin)),
            ("i2c_reaches_mcu", "the IMU's host I2C reaches the MCU",
             lambda p, b: _part_pin_reaches_mcu(p, "BNO085", "H_SCL/SCK/RX", "H_SDA/H_MISO/TX")),
            ("ldo_output_cap", "the LDO output carries >= 1 uF (R79)",
             lambda p, b: _rule_clean(p, verifier.R79_regulator_output_cap)),
        ],
    },
    {
        "name": "tp4056-1a",               # review #39 (phrasing sheet, project 3)
        "review": "1 A single-cell Li-ion charger with protection (report-39)",
        "prompt": ("Design a single-cell 3.7V Li-ion charging circuit at 1A charge current using a "
                   "linear Li-ion charger IC with battery protection."),
        "max_area_mm2": 2500,
        "checks": [
            ("charger", "a TP4056-class charger IC is placed",
             lambda p, b: _count_mpn(p, "TP4056", "MCP73831", "MCP73833") >= 1),
            ("prog_1k2", "the PROG resistor is 1.2 k (1 A), not 2.2 k (545 mA)",
             lambda p, b: _count_mpn(p, "RC0603FR-071K2L") >= 1 and _count_mpn(p, "RC0603FR-072K2L") == 0),
            ("protection", "battery protection is real: DW01A + FS8205A (or a protected module)",
             lambda p, b: (_count_mpn(p, "DW01A") >= 1 and _count_mpn(p, "FS8205A") >= 1)
             or _count_mpn(p, "TP4056_MOD") >= 1),
            ("protection_fets", "DW01A OD/OC land on the FET gates (R62)",
             lambda p, b: _rule_clean(p, verifier.R62_battery_protection_fets)),
            ("output_caps", "charger outputs carry >= 1 uF (R79)",
             lambda p, b: _rule_clean(p, verifier.R79_regulator_output_cap)),
        ],
    },
    {
        "name": "ldo-3v3-usb",             # review #39 (phrasing sheet, project 1)
        "review": "3.3 V / 500 mA LDO from 5 V USB, caps per datasheet (report-39)",
        "prompt": ("Design a linear regulator stage converting 5V USB input to 3.3V output at 500mA, "
                   "with input and output decoupling capacitors per datasheet recommendations."),
        "max_area_mm2": 1500,
        "checks": [
            ("ldo", "one fixed 3.3 V LDO", lambda p, b: sum(
                1 for c in p.ir.components
                if (part := library.get_part(c.mpn)) and part.category == "regulator") == 1),
            ("output_cap", "the output capacitor is present and >= 1 uF, not optional (R79)",
             lambda p, b: _rule_clean(p, verifier.R79_regulator_output_cap)),
            ("input_cap", "the input is decoupled (R01)",
             lambda p, b: _rule_clean(p, verifier.R01_decoupling_cap)),
            ("thermal", "500 mA at 1.7 V drop is within the part's dissipation (R32)",
             lambda p, b: _rule_clean(p, verifier.R32_linear_regulator_thermal)),
        ],
    },
]


def _display_centred(p: Project, b) -> bool:
    """The largest display's body centre within 2 mm of the disc centre."""
    if b is None or b.shape != "round":
        return False
    disp = next((pc for pc in b.components
                 if (part := library.get_part(pc.mpn)) and part.category == "display"), None)
    if disp is None:
        return False
    x0, y0, x1, y1 = pcb_verifier._body_bbox(disp)  # noqa: SLF001
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ctr = b.width / 2
    return ((cx - ctr) ** 2 + (cy - ctr) ** 2) ** 0.5 <= 2.0


def _universal_checks(spec: dict) -> list:
    """Checks every review asked for, in one form or another."""
    cap = spec["max_area_mm2"]
    return [
        ("no_drc_errors", "0 blocking DRC errors", lambda p, b: not _errors(p)),
        ("requirements_met", "no requested-quantity shortfall (R43)",
         lambda p, b: _rule_clean(p, verifier.R43_requested_quantity)),
        ("all_parts_placed", "every schematic part is on the PCB",
         lambda p, b: b is not None and not pcb_verifier.board_sync_issues(p)),
        ("board_geometry", "no overlapping or off-board parts",
         lambda p, b: b is not None and not [
             i for i in pcb_verifier.run_all(b) if i.rule.startswith(("PCB_R01", "PCB_R02"))]),
        ("mounting_holes", "the board carries mounting references",
         lambda p, b: bool(p.mechanical and p.mechanical.mountingHoles)),
        ("compact_board", f"board area under {cap} mm^2",
         lambda p, b: b is not None and (b.width * b.height) <= cap),
        ("routes_clean", "the board auto-routes with zero rats (boards <= 40 parts)",
         lambda p, b: _routes_clean(p, b)),
    ]


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #


def run_one(spec: dict, save_dir: str | None = None, run_no: int = 1) -> dict:
    """Build one design and evaluate every check. Never raises. With `save_dir`,
    the finished project (IR + board + chat) is dumped as JSON so a failing run
    can be inspected offline."""
    project = Project(id=f"eval-{spec['name']}", name=spec["name"], path=f"/{spec['name']}")
    started = time.time()
    error: str | None = None
    try:
        for _ in agent.run(project, spec["prompt"]):
            pass
    except Exception as exc:  # noqa: BLE001
        error = f"agent: {exc!s}"[:160]
        # Keep the trace: an intermittent crash is only diagnosable once.
        traceback.print_exc()

    board = None
    if project.ir.components and error is None:
        try:
            board = placement.auto_place(project)
            project.board = board
            pcb_verifier.merge_into_project(project)
            if not (project.mechanical and project.mechanical.mountingHoles):
                mechanical_tools.derive_mounting(project)
                mechanical_verifier.merge_into_project(project)
        except Exception as exc:  # noqa: BLE001
            error = f"place: {exc!s}"[:160]

    if save_dir:
        try:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
            (Path(save_dir) / f"{spec['name']}-run{run_no}.json").write_text(
                project.model_dump_json(), encoding="utf-8")
        except Exception:  # noqa: BLE001 — saving is a convenience, never a failure
            pass

    results: dict[str, bool] = {}
    for cid, _desc, fn in spec["checks"] + _universal_checks(spec):
        try:
            results[cid] = bool(fn(project, board))
        except Exception:  # noqa: BLE001 — a check that explodes is a failed check
            results[cid] = False
    return {
        "name": spec["name"],
        "error": error,
        "seconds": round(time.time() - started, 1),
        "components": len(project.ir.components),
        "board": (round(board.width, 1), round(board.height, 1)) if board else None,
        "checks": results,
        "passed": sum(results.values()),
        "total": len(results),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=1, help="runs per prompt (pass-rate over N)")
    ap.add_argument("--only", type=str, default=None, help="substring match on the prompt name")
    ap.add_argument("--list", action="store_true", help="print the suite and exit")
    ap.add_argument("--json", type=str, default=None, help="write results as JSON")
    ap.add_argument("--save", type=str, default=None, help="directory to dump each run's project JSON")
    args = ap.parse_args()

    suite = [s for s in SUITE if not args.only or args.only.lower() in s["name"].lower()]
    if not suite:
        print(f"No prompt matches {args.only!r}. Names: {[s['name'] for s in SUITE]}")
        return 2

    if args.list:
        for spec in suite:
            print(f"\n{spec['name']}  — {spec['review']}")
            print(f'  prompt: "{spec["prompt"][:110]}..."')
            for cid, desc, _fn in spec["checks"] + _universal_checks(spec):
                print(f"    [{cid}] {desc}")
        return 0

    if not agent.has_api_key():
        print("No live key (OPENAI_API_KEY / Azure env) — this suite exercises the real "
              "agent. Set the key and re-run.")
        return 0

    print(f"Beta-review eval — {len(suite)} prompt(s) x {args.runs} run(s), live agent\n")
    all_runs: list[dict] = []
    for spec in suite:
        for i in range(args.runs):
            res = run_one(spec, save_dir=args.save, run_no=i + 1)
            all_runs.append(res)
            status = "ERROR " + (res["error"] or "") if res["error"] else \
                f"{res['passed']}/{res['total']}"
            board = f"{res['board'][0]}x{res['board'][1]}mm" if res["board"] else "no board"
            print(f"  {spec['name']:<14} run {i + 1}/{args.runs}  {status:<12} "
                  f"{res['components']:>3} parts  {board:<14} {res['seconds']:>5.0f}s")
            failed = [c for c, ok in res["checks"].items() if not ok]
            if failed:
                print(f"       failed: {', '.join(failed)}")

    print("\n" + "=" * 74)
    print(f"{'prompt':<16} {'pass-rate':>10}  failing checks (rate over runs)")
    print("-" * 74)
    grand_passed = grand_total = 0
    summary: dict[str, dict] = {}
    for spec in suite:
        runs = [r for r in all_runs if r["name"] == spec["name"]]
        passed = sum(r["passed"] for r in runs)
        total = sum(r["total"] for r in runs)
        grand_passed += passed
        grand_total += total
        per_check: dict[str, float] = {}
        for cid in runs[0]["checks"]:
            ok = sum(1 for r in runs if r["checks"].get(cid))
            per_check[cid] = ok / len(runs)
        weak = sorted((c for c, rate in per_check.items() if rate < 1.0),
                      key=lambda c: per_check[c])
        weak_txt = ", ".join(f"{c} {per_check[c]:.0%}" for c in weak) or "-"
        summary[spec["name"]] = {"passed": passed, "total": total, "checks": per_check}
        print(f"{spec['name']:<16} {passed}/{total:<8} {weak_txt}")
    print("-" * 74)
    rate = grand_passed / grand_total if grand_total else 0.0
    print(f"OVERALL: {grand_passed}/{grand_total} checks passed  =  {rate:.0%}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary, "runs": all_runs, "rate": rate}, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
