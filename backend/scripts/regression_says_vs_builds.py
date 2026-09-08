"""Regression — "the tool says one thing and builds another" (2026-08-20 batch).

Three of five reviewers hit the same class:
  * "It should have used a 150 Ω resistor, but it picked a 100 Ω one instead —
     the tool's own explanation text correctly said 150 Ω."
  * "asked for a through-hole resistor but it gave a surface-mount version;
     asked for a 5mm LED, but it used a 3mm one."
  * a 3.3 V LDO fed from a "3.0 V to 4.2 V" cell passed every check, because
     only the cell's FULL-CHARGE voltage was ever considered.

Guards:
  1. `constraints_text.extract_constraints` reads a stated value / mount style /
     size out of the prompt — and refuses to invent one from a vague prompt.
  2. `R73_explicit_part_constraint` errors when the built design contradicts a
     recorded constraint, and is silent when it satisfies it.
  3. The library can actually satisfy the common asks (5 mm LEDs, axial
     through-hole resistors) — a rule that cannot be satisfied is a trap.
  4. `_declared_input_range` reads a stated range / cell count, and R58 reports
     a regulator that drops out before the battery is empty — with severity
     proportional to how much of the range is lost.
  5. `rail_voltage_range` gives every rail a voltage for the power tree.

Offline: no LLM, no network, no DB.
    python -m scripts.regression_says_vs_builds
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import PartConstraintRecord, Project, SchematicIR  # noqa: E402
from app.services import agent, constraints_text, library, tools, verifier  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def fresh(pid: str = "t") -> Project:
    return Project(id=pid, name=pid, path=f"/tmp/{pid}", ir=SchematicIR())


LED_PROMPT = (
    "Design a simple LED indicator circuit powered by a 5V DC source. Use a single "
    "5mm through-hole red LED (forward voltage ~2V, forward current 20mA) with a "
    "150 ohm current-limiting resistor in series."
)


def test_stated_values_are_checked() -> None:
    """A number the model INVENTS and then contradicts (2026-08-20 follow-up).

    R73 only judges a value the USER named. A reviewer got a resistor value
    that was "inconsistent across four places in the same response", with no
    forward current stated, so it could not be verified at all — and nothing
    checked either failure.
    """
    print("\n-- values the copilot states are checked against the design")
    from app.services import claim_validator as cv

    p = fresh("says-values")
    tools.add_component(p, mpn="RC0603FR-0710KL", refdes="R1")   # 10 k
    tools.add_component(p, mpn="LED_3MM_R", refdes="D1")

    _, iss = cv.validate(p, "I used R1 (330 Ohm) to limit the LED current.")
    check("a value attributed to a part is compared with the design",
           any(i.rule == "LLM_value_mismatch" for i in iss))

    _, iss = cv.validate(p, "R1 is 10k, which sets the pull-up.")
    check("a value that MATCHES the design is silent", not iss)

    _, iss = cv.validate(
        p, "A 150 Ohm resistor limits it; the 220 ohm gives 12 mA, so R1 = 330 Ohm.")
    check("a reply quoting several values for one resistor is caught",
           any(i.id == "LLM_value:inconsistent" for i in iss),
           "; ".join(i.id for i in iss))

    _, iss = cv.validate(p, "The board runs from a 3.3 V rail over USB.")
    check("ordinary prose with no value claim is untouched", not iss)


def test_led_current_is_derived_not_asserted() -> None:
    """The forward current a resistor sets, computed from curated part data —
    so it is verifiable whatever the prose says (or omits)."""
    print("\n-- R75 states the current a limiting resistor actually sets")

    def led_board(res_mpn: str):
        p = fresh(f"r75-{res_mpn}")
        tools.add_component(p, mpn="LED_3MM_R", refdes="D1")
        tools.add_component(p, mpn=res_mpn, refdes="R1")
        tools.connect(p, net_name="VCC_5V", pins=[["R1", "1"]])
        tools.connect(p, net_name="LED_A", pins=[["R1", "2"], ["D1", "1"]])
        tools.connect(p, net_name="GND", pins=[["D1", "2"]])
        return p

    found = verifier.R75_led_current_stated(led_board("RC0603FR-07150RL"))
    check("a correct value reports the current and the arithmetic",
           bool(found) and found[0].severity == "info"
           and "20 mA" in found[0].message and "150" in found[0].message,
           found[0].message[:80] if found else "nothing")

    found = verifier.R75_led_current_stated(led_board("RC0603FR-0710RL"))
    check("a resistor that would burn the LED is an error",
           bool(found) and found[0].severity == "error"
           and "rating" in found[0].message)

    found = verifier.R75_led_current_stated(led_board("RC0603FR-0710KL"))
    check("a resistor that leaves the LED dark is a warning, not a pass",
           bool(found) and found[0].severity == "warning"
           and "too little to light it" in found[0].message)

    check("R75 is registered",
           verifier.R75_led_current_stated in verifier.ALL_RULES)


def main() -> int:
    # ---- 1. the extractor ------------------------------------------------
    cons = constraints_text.extract_constraints(LED_PROMPT)
    kinds = {(c.kind, c.target, c.value) for c in cons}
    check("extracts the stated resistor value", ("value", "resistor", "150") in kinds, f"{kinds}")
    check("extracts through-hole for the resistor", ("mount", "resistor", "tht") in kinds)
    check("extracts the 5 mm LED size", ("size", "led", "5mm") in kinds)
    check("a vague prompt yields NO constraints",
          not constraints_text.extract_constraints("make me a temperature logger"))
    check("a mounting-hole pitch is not a part constraint",
          not [c for c in constraints_text.extract_constraints(
              "mount it on the 30.5 x 30.5 mm mounting pattern") if c.kind == "value"])

    # ---- 2. R73 both ways -------------------------------------------------
    def _with_constraints(pid: str) -> Project:
        p = fresh(pid)
        agent._record_part_constraints(p, LED_PROMPT)
        return p

    wrong = _with_constraints("wrong")
    for mpn in ("LED_3MM_R", "RC0603FR-07100RL"):
        tools.add_component(wrong, mpn=mpn)
    issues = verifier.R73_explicit_part_constraint(wrong)
    ids = {i.id for i in issues}
    check("R73 catches the wrong resistor VALUE", "R73:value:resistor:150" in ids, f"{ids}")
    check("R73 catches the wrong MOUNT style", "R73:mount:resistor:tht" in ids)
    check("R73 catches the wrong LED SIZE", "R73:size:led:5mm" in ids)
    check("R73 findings are errors", all(i.severity == "error" for i in issues))

    right = _with_constraints("right")
    for mpn in ("LED_5MM_R", "R_AXIAL_150R"):
        tools.add_component(right, mpn=mpn)
    check("R73 is silent on a design that matches the ask",
          not verifier.R73_explicit_part_constraint(right),
          f"{[i.id for i in verifier.R73_explicit_part_constraint(right)]}")

    plain = fresh("plain")
    tools.add_component(plain, mpn="RC0603FR-07100RL")
    check("R73 is silent when nothing was stated",
          not verifier.R73_explicit_part_constraint(plain))

    # ---- 3. the library can satisfy the ask -------------------------------
    check("the library has a 5 mm LED", library.get_part("LED_5MM_R") is not None)
    check("the library has an axial through-hole resistor",
          library.get_part("R_AXIAL_150R") is not None)

    # ---- 4. the battery range, and dropout ---------------------------------
    bat = fresh("bat")
    tools.record_requirements(bat, items=[
        {"key": "cell", "description": "Power comes from a single Li-ion cell, 3.0 V to 4.2 V",
         "quantity": 1, "category": "connector"},
        {"key": "ldo", "description": "1x 3.3V LDO regulator from single Li-ion cell",
         "quantity": 1, "category": "regulator"},
    ])
    lo, hi = verifier._declared_input_range(bat)
    check("reads the stated cell range", (lo, hi) == (3.0, 4.2), f"got {(lo, hi)}")
    check("the max-voltage reader is not fooled by '3.3V LDO'",
          verifier._declared_input_voltage(bat) == 4.2,
          f"got {verifier._declared_input_voltage(bat)}")

    tools.add_component(bat, mpn="MCP1700-3302E/TO", refdes="U1")
    tools.add_component(bat, mpn="ESP32-C3-MINI-1", refdes="U2")
    tools.connect(bat, net_name="VBAT", pins=[["U1", "VIN"]])
    tools.connect(bat, net_name="VCC_3V3", pins=[["U1", "VOUT"], ["U2", "3V3"]])
    tools.connect(bat, net_name="GND", pins=[["U1", "GND"], ["U2", "GND"]])
    drop = [i for i in verifier.R58_regulator_input_range(bat) if i.id.endswith("dropout")]
    check("R58 reports the LDO dropping out before the cell is empty", len(drop) == 1,
          f"{[i.id for i in verifier.R58_regulator_input_range(bat)]}")
    check("the dropout message names the cut-off voltage",
          drop and "3.6" in drop[0].message, f"{drop[0].message if drop else ''}")
    check("VBAT carries the declared RANGE",
          verifier.rail_voltage_range(bat, "VBAT") == (3.0, 4.2),
          f"{verifier.rail_voltage_range(bat, 'VBAT')}")
    check("a regulated rail carries its regulator's fixed output",
          verifier.rail_voltage_range(bat, "VCC_3V3") == (3.3, 3.3),
          f"{verifier.rail_voltage_range(bat, 'VCC_3V3')}")

    # A regulator that cannot work even at full charge is the harder error.
    hard = fresh("hard")
    tools.record_requirements(hard, items=[
        {"key": "cell", "description": "single Li-ion cell input, 3.0 V to 4.2 V",
         "quantity": 1, "category": "connector"}])
    tools.add_component(hard, mpn="AMS1117-3.3", refdes="U1")
    tools.connect(hard, net_name="VBAT", pins=[["U1", "VIN"]])
    tools.connect(hard, net_name="VCC_3V3", pins=[["U1", "VOUT"]])
    sev = {i.severity for i in verifier.R58_regulator_input_range(hard)}
    check("a regulator that cannot regulate at all is an ERROR", "error" in sev, f"{sev}")

    # ---- 4. the schematic view labels POWER rails, not just ground ------
    # A reviewer could confirm the ground connection visually but not the power
    # one: the canvas knew only VCC/VDD/VBUS/VBAT/+*, so a rail called VIN, VM,
    # 12V, 3.3V or V+ drew as an ordinary signal with no rail label. The view's
    # rule is mirrored here so it cannot drift from the verifier's again.
    canvas = (_BACKEND_ROOT.parent / "frontend" / "src" / "components" / "editor"
              / "SchematicCanvas.tsx").read_text(encoding="utf-8")
    for rail in ("VIN", "VM", "VMOT", "VSYS", "VRAW", "VPWR", "VDDA", "AVDD",
                 "VREG", "VOUT"):
        check(f"the schematic view knows {rail} is a power rail",
              f'"{rail}"' in canvas)
    check("and it recognises a bare voltage name (5V, 3V3, 12V, +3.3V)",
          "VOLTAGE_NAME_RE" in canvas)
    check("without reading VSENSE-style signals as rails (exact/separated match)",
          "u === p || u.startsWith(`${p}_`)" in canvas)

    # ---- 5. values the copilot STATES, and the current it does not ------
    test_stated_values_are_checked()
    test_led_current_is_derived_not_asserted()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("All says-vs-builds checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
