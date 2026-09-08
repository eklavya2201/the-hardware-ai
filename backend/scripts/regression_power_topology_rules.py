"""Regression — reviews #19-#23 rules (power topology + "placed but not wired").

Each rule fires on the exact defect its review shipped with and stays silent
on the known-good stub. Offline (no LLM):
    python -m scripts.regression_power_topology_rules

  R04 (generalized) AP2112 (6 V max) on a declared 12 V input rail
  R12 (tightened)   a diode whose input net holds only the diode
  R36 (one hop)     GPIO pulled up to 5 V through a resistor on a 3.3 V MCU
  R39 (coil=error)  relay coil on a FET with no flyback
  R43 (pooled)      1x + 4x of the same MPN judged as 5 total
  R46 (GPIO LED)    LED between rail and GPIO with no resistor = error
  R47 (switch)      button with both legs on GND
  R58               LM7805 from a 1S cell (too low) / LM7805 from 4S (linear drop)
  R59               LM317 with ADJ on GND
  R60               relay contacts unwired; part wired only on power/ground
  R61               passive island (divider touching nothing)
  R62               DW01A with OD/OC in the air
  R63               N-FET drain on rail, source on the load
  R64               series Schottky in a 4S ESC path
  R65               resistor bridging AC_L and a GPIO
  R66               INA219 with IN+/IN- unwired
  R67               3.3 V regulator feeding a net named VCC_5V
  _declared_input_voltage from requirement text
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import Project, SchematicIR  # noqa: E402
from app.services import tools, verifier  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def fresh(pid: str) -> Project:
    return Project(id=pid, name=pid, path=f"/tmp/{pid}", ir=SchematicIR())


def add(p: Project, mpn: str) -> str:
    r, _ = tools.add_component(p, mpn=mpn)
    return r["refdes"]


def con(p: Project, net: str, *pins: tuple[str, str]) -> None:
    tools.connect(p, net_name=net, pins=[[r, pn] for r, pn in pins])


def fires(rule, p: Project, sev: str = "error", contains: str | None = None) -> bool:
    hits = [i for i in rule(p) if i.severity == sev and (contains is None or contains in i.message)]
    return bool(hits)


def main() -> int:
    # ---- declared input voltage ------------------------------------------------
    p = fresh("v")
    tools.record_requirements(p, items=[{"description": "4S LiPo battery input connector", "quantity": 1, "category": "connector"}])
    check("declared 4S -> 16.8 V", verifier._declared_input_voltage(p) == 16.8, str(verifier._declared_input_voltage(p)))
    p = fresh("v2")
    tools.record_requirements(p, items=[{"description": "12V input connector", "quantity": 1, "category": "connector"}])
    check("declared '12V input' -> 12", verifier._declared_input_voltage(p) == 12.0)
    p = fresh("v3")
    tools.record_requirements(p, items=[{"description": "5V regulator for flight controller", "quantity": 1, "category": "regulator"}])
    check("'5V regulator' is NOT an input declaration", verifier._declared_input_voltage(p) is None)

    # ---- R58 (a): LM7805 from a 1S cell -----------------------------------------
    p = fresh("r58a")
    tools.record_requirements(p, items=[{"description": "single-cell Li-ion battery connector", "quantity": 1, "category": "connector"}])
    j = add(p, "S2B-PH-K-S(LF)(SN)"); u = add(p, "LM7805")
    con(p, "VBAT", (j, "1"), (u, "1")); con(p, "GND", (j, "2"), (u, "2")); con(p, "VCC_5V", (u, "3"))
    check("R58 errors: LM7805 from a 1S cell", fires(verifier.R58_regulator_input_range, p, contains="cannot regulate"))
    # ---- R58 (b): LM7805 from 4S ---------------------------------------------------
    p = fresh("r58b")
    tools.record_requirements(p, items=[{"description": "4S LiPo battery input connector", "quantity": 1, "category": "connector"}])
    j = add(p, "ScrewTerminal_2P_5mm"); u = add(p, "LM7805")
    con(p, "VBAT", (j, "1"), (u, "1")); con(p, "GND", (j, "2"), (u, "2")); con(p, "VCC_5V", (u, "3"))
    check("R58 errors: LM7805 linear from 16.8 V", fires(verifier.R58_regulator_input_range, p, contains="LINEAR"))
    # AP2112 (LDO, 250 mV) from a 3.7 V LiPo is fine
    p = fresh("r58c")
    j = add(p, "S2B-PH-K-S(LF)(SN)"); u = add(p, "AP2112K-3.3TRG1")
    con(p, "VBAT", (j, "1"), (u, "1")); con(p, "GND", (j, "2"), (u, "2")); con(p, "VCC_3V3", (u, "5"))
    check("R58 silent: AP2112 LDO from a LiPo", not fires(verifier.R58_regulator_input_range, p))
    # NCP1117 (min input 4.75) from a LiPo is not
    p = fresh("r58d")
    j = add(p, "S2B-PH-K-S(LF)(SN)"); u = add(p, "NCP1117-33")
    con(p, "VBAT", (j, "1"), (u, "3")); con(p, "GND", (j, "2"), (u, "1")); con(p, "VCC_3V3", (u, "2"))
    check("R58 errors: NCP1117 (4.75 V min) from a LiPo", fires(verifier.R58_regulator_input_range, p))

    # ---- R59: LM317 ADJ on GND ---------------------------------------------------
    p = fresh("r59")
    u = add(p, "LM317T"); j = add(p, "ScrewTerminal_2P_5mm")
    con(p, "VBAT", (j, "1"), (u, "3")); con(p, "GND", (j, "2"), (u, "1")); con(p, "VCC_9V", (u, "2"))
    check("R59 errors: LM317 ADJ tied to GND", fires(verifier.R59_adjustable_regulator_feedback, p))
    r1 = add(p, "RC0603FR-07220RL"); r2 = add(p, "RC0603FR-071K5L")
    tools.disconnect(p, net_name="GND", pins=[[u, "1"]])
    con(p, "ADJ", (u, "1"), (r1, "1"), (r2, "1")); con(p, "VCC_9V", (r1, "2")); con(p, "GND", (r2, "2"))
    check("R59 silent: LM317 with its divider", not fires(verifier.R59_adjustable_regulator_feedback, p))

    # ---- R67: 3.3 V regulator on VCC_5V ------------------------------------------
    p = fresh("r67")
    u = add(p, "LD1117-33"); j = add(p, "BarrelJack_DC_5.5x2.1mm")
    con(p, "VIN", (j, "1"), (u, "3")); con(p, "GND", (j, "2"), (u, "1")); con(p, "VCC_5V", (u, "2"))
    check("R67 errors: LD1117-33 feeding VCC_5V", fires(verifier.R67_rail_name_vs_regulator, p))

    # ---- R04 generalized: AP2112 on a declared 12 V rail -------------------------
    p = fresh("r04")
    tools.record_requirements(p, items=[{"description": "12V input connector", "quantity": 1, "category": "connector"}])
    u = add(p, "AP2112K-3.3TRG1"); j = add(p, "BarrelJack_DC_5.5x2.1mm")
    con(p, "VBAT", (j, "1"), (u, "1")); con(p, "GND", (j, "2"), (u, "2")); con(p, "VCC_3V3", (u, "5"))
    check("R04 errors: AP2112 (6 V max) on a 12 V input rail", fires(verifier.R04_voltage_rail_compat, p))

    # ---- R12: diode alone on its input net --------------------------------------
    p = fresh("r12")
    d = add(p, "SS54"); j = add(p, "ScrewTerminal_2P_5mm"); u = add(p, "LM7805")
    con(p, "VBAT_IN", (d, "2")); con(p, "VBAT", (d, "1"), (j, "1"), (u, "1")); con(p, "GND", (j, "2"), (u, "2"))
    check("R12 errors: reverse diode with an empty input net", fires(verifier.R12_orphan_net, p, contains="dangling"))

    # ---- R43 pooled -----------------------------------------------------------------
    p = fresh("r43")
    tools.record_requirements(p, items=[
        {"description": "battery input connector", "quantity": 1, "mpn": "ScrewTerminal_2P_5mm"},
        {"description": "ESC output connectors", "quantity": 4, "mpn": "ScrewTerminal_2P_5mm"},
    ])
    for _ in range(4):
        add(p, "ScrewTerminal_2P_5mm")
    hits = verifier.R43_requested_quantity(p)
    check("R43 pooled: 4 built vs 5 asked = ONE shortfall error, no over-count",
          len(hits) == 1 and hits[0].severity == "error" and "contains 4" in hits[0].message,
          str([(h.id, h.message[:80]) for h in hits]))
    add(p, "ScrewTerminal_2P_5mm")
    check("R43 pooled: 5 built = clean", verifier.R43_requested_quantity(p) == [])

    # ---- R60: relay contacts unwired; R39 coil error; R63 FET topology -------------
    p = fresh("r60")
    k = add(p, "SRD-05VDC-SL-C"); q = add(p, "AO3400A"); r = add(p, "RC0603FR-071KL"); m = add(p, "ESP32-WROOM-32")
    con(p, "VCC_5V", (k, "1"), (q, "3")); con(p, "COIL_RET", (k, "5"), (q, "2")); con(p, "GATE", (q, "1"), (r, "2"))
    con(p, "CTRL", (r, "1"), (m, "16")); con(p, "GND", (m, "1"))
    check("R60 errors: relay contacts unwired", fires(verifier.R60_function_pins_unwired, p, contains="contact"))
    check("R39 errors (not warns): relay coil on a FET without flyback", fires(verifier.R39_flyback_protection, p))
    check("R63 errors: N-FET drain on rail, source on the coil", fires(verifier.R63_lowside_switch_topology, p))
    # fix the topology: drain to coil, source to GND, contacts wired
    tools.disconnect(p, net_name="VCC_5V", pins=[[q, "3"]]); tools.disconnect(p, net_name="COIL_RET", pins=[[q, "2"]])
    con(p, "COIL_RET", (q, "3")); con(p, "GND", (q, "2"))
    j = add(p, "ScrewTerminal_3P_5mm")
    con(p, "LOAD1_L", (k, "3"), (j, "1")); con(p, "LOAD1_OUT", (k, "2"), (j, "2"))
    check("R63 silent after low-side rewire", not fires(verifier.R63_lowside_switch_topology, p))
    check("R60 silent once contacts reach the terminal", not fires(verifier.R60_function_pins_unwired, p))

    # ---- R47 switch, R46 LED on GPIO, R36 pull-up hop ------------------------------
    p = fresh("r47")
    m = add(p, "ESP32-WROOM-32"); sw = add(p, "TS-1187A-B-A-B"); d = add(p, "LED_G_0603"); r = add(p, "RC0603FR-0710KL")
    con(p, "GND", (m, "1"), (sw, "1"), (sw, "2")); con(p, "VCC_3V3", (m, "2"), (d, "2"))
    con(p, "LED1", (d, "1"), (m, "16"))
    check("R47 errors: button with both legs on GND", fires(verifier.R47_two_pin_part_shorted, p))
    check("R46 errors: LED between rail and GPIO with no resistor", fires(verifier.R46_led_current_limit, p))
    j = add(p, "BarrelJack_DC_5.5x2.1mm")
    con(p, "VCC_5V", (j, "1"), (r, "2")); con(p, "LIM_X", (r, "1"), (m, "33"))
    check("R36 errors: GPIO pulled up to 5 V through a resistor", fires(verifier.R36_gpio_5v_tolerance, p, contains="pulled up"))

    # ---- R61 passive island -----------------------------------------------------------
    p = fresh("r61")
    r1 = add(p, "RC0603FR-07100KL"); r2 = add(p, "RC0603FR-0710KL")
    con(p, "SENSE_HI", (r1, "1")); con(p, "SENSE", (r1, "2"), (r2, "1")); con(p, "SENSE_LO", (r2, "2"))
    check("R61 errors: divider touching nothing", fires(verifier.R61_passive_island, p))
    j = add(p, "ScrewTerminal_2P_5mm")
    tools.disconnect(p, net_name="SENSE_HI", pins=[[r1, "1"]]); tools.disconnect(p, net_name="SENSE_LO", pins=[[r2, "2"]])
    con(p, "VBAT", (j, "1"), (r1, "1")); con(p, "GND", (j, "2"), (r2, "2"))
    check("R61 silent once the divider spans rail->GND", not fires(verifier.R61_passive_island, p))

    # ---- R62 DW01A without FETs; R66 INA219 -------------------------------------------
    p = fresh("r62")
    u = add(p, "DW01A"); j = add(p, "S2B-PH-K-S(LF)(SN)")
    con(p, "VBAT", (j, "1"), (u, "5")); con(p, "GND", (j, "2"), (u, "6"))
    check("R62 errors: DW01A OD/OC reach no FET", fires(verifier.R62_battery_protection_fets, p))
    f = add(p, "FS8205A")
    con(p, "OD", (u, "1"), (f, "2")); con(p, "OC", (u, "3"), (f, "4"))
    check("R62 silent with FS8205A on OD/OC", not fires(verifier.R62_battery_protection_fets, p))
    p = fresh("r66")
    u = add(p, "INA219AIDCNR"); m = add(p, "ESP32-WROOM-32")
    con(p, "VCC_3V3", (u, "4"), (m, "2")); con(p, "GND", (u, "3"), (m, "1")); con(p, "SDA", (u, "6"), (m, "33")); con(p, "SCL", (u, "5"), (m, "36"))
    check("R66 errors: INA219 IN+/IN- unwired", fires(verifier.R66_sense_inputs_unwired, p))
    s = add(p, "WSL2512R1000FEA")
    con(p, "VBAT", (u, "1"), (s, "1")); con(p, "VLOAD", (u, "2"), (s, "2"))
    check("R66 silent with a shunt between IN+ and IN-", not fires(verifier.R66_sense_inputs_unwired, p))

    # ---- R64 series diode in an ESC path; R65 mains bridge -----------------------------
    p = fresh("r64")
    tools.record_requirements(p, items=[{"description": "4S LiPo battery input connector", "quantity": 1, "category": "connector"}])
    d = add(p, "SS34"); jb = add(p, "ScrewTerminal_2P_5mm"); j1 = add(p, "ScrewTerminal_2P_5mm"); j2 = add(p, "ScrewTerminal_2P_5mm")
    con(p, "VBAT_IN", (jb, "1"), (d, "2")); con(p, "VBAT", (d, "1"), (j1, "1"), (j2, "1")); con(p, "GND", (jb, "2"), (j1, "2"), (j2, "2"))
    check("R64 fires: 3 A Schottky in series with a 2-output battery rail",
          fires(verifier.R64_series_diode_high_current, p, "error") or fires(verifier.R64_series_diode_high_current, p, "warning"))
    p = fresh("r65")
    m = add(p, "ESP32-WROOM-32"); r = add(p, "RC0603FR-07100KL"); k = add(p, "SRD-05VDC-SL-C")
    con(p, "AC_L", (r, "1"), (k, "3")); con(p, "SENSE", (r, "2"), (m, "16")); con(p, "GND", (m, "1"))
    hits = verifier.R65_mains_isolation(p)
    check("R65 errors: resistor bridges AC_L and a GPIO net; relay is exempt",
          len(hits) == 1 and hits[0].refdes == r, str([(h.refdes, h.message[:60]) for h in hits]))

    # ---- stub stays clean --------------------------------------------------------------
    os.environ.pop("OPENAI_API_KEY", None)
    from app.services import agent
    stub = Project(id="stub", name="stub", path="/stub")
    for _ in agent._run_stub(stub, "esp32"):
        pass
    errs = [i for i in verifier.run_all(stub) if i.severity == "error"]
    check("stub design has 0 errors under the new rules", not errs, str([(i.rule, i.message[:60]) for i in errs]))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("All power-topology regression checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
