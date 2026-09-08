"""Robotics power-integrity rules regression — Week-5 task 7.5 (R37-R41).

Each rule must FIRE on its failure topology and stay SILENT on the guarded
good variant, and the known-good stub board must stay clean. Fixtures are
built from real library parts through the real tools so every guard applies.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_robotics_rules
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        _failures.append(label)


def _rule(issues, prefix):
    return [i for i in issues if (i.rule or "").startswith(prefix)]


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Project
    from app.services import agent, library, tools, verifier

    print("Robotics power-integrity rules regression (7.5)\n")

    # --- R37: 5 V MCU drives a 3.3 V part -----------------------------------
    p = Project(id="r37", name="r37", path="/r37")
    tools.add_component(p, mpn="Arduino_Uno_R3", refdes="U1")
    tools.add_component(p, mpn="BME280", refdes="U2")
    # Uno A4 (pin 13) -> BME280 SDI (pin 3): direct 5V->3.3V signal tie.
    tools.connect(p, net_name="SENSOR_SDA", pins=[["U1", "13"], ["U2", "3"]])
    issues = verifier.run_all(p)
    r37 = _rule(issues, "R37")
    check("R37 fires: Uno GPIO drives BME280 (3.6 V max) directly",
          any(i.refdes == "U2" and i.severity == "error" for i in r37),
          f"{[(i.refdes, i.net) for i in r37]}")

    # Guarded: a level shifter on the net exempts it.
    p2 = Project(id="r37b", name="r37b", path="/r37b")
    tools.add_component(p2, mpn="Arduino_Uno_R3", refdes="U1")
    tools.add_component(p2, mpn="BME280", refdes="U2")
    tools.add_component(p2, mpn="TXB0104", refdes="U3")
    tools.connect(p2, net_name="SENSOR_SDA",
                  pins=[["U1", "13"], ["U2", "3"], ["U3", "A1"]])
    r37b = _rule(verifier.run_all(p2), "R37")
    check("R37 silent with a level shifter on the net", not r37b,
          f"{[(i.refdes, i.net) for i in r37b]}")

    # Guarded: a 3.3 V MCU never fires it.
    p3 = Project(id="r37c", name="r37c", path="/r37c")
    tools.add_component(p3, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(p3, mpn="BME280", refdes="U2")
    tools.connect(p3, net_name="SENSOR_SDA", pins=[["U1", "IO21"], ["U2", "3"]])
    check("R37 silent for a 3.3 V MCU", not _rule(verifier.run_all(p3), "R37"))

    # --- R37 shield/HAT-as-MCU (7.3 gap) + open-drain guard ------------------
    # A shield's 5 V host reaches the board through J1; a push-pull GPIO into a
    # 3.3 V part must fire even with no MCU component on the board.
    sh = Project(id="r37sh", name="r37sh", path="/r37sh")
    tools.add_component(sh, mpn="Arduino_Shield_Headers", refdes="J1")
    tools.add_component(sh, mpn="BME280", refdes="U1")
    tools.connect(sh, net_name="SENSOR_CTRL", pins=[["J1", "D2"], ["U1", "3"]])
    r37sh = _rule(verifier.run_all(sh), "R37")
    check("R37 fires on a shield (5 V host) driving a 3.3 V part push-pull",
          any(i.refdes == "U1" for i in r37sh), f"{[(i.refdes) for i in r37sh]}")

    # Same shield but an I2C bus pulled up to 3.3 V is SAFE (open-drain guard) —
    # the false positive that would have hit the real HAT/shield designs.
    sh2 = Project(id="r37sh2", name="r37sh2", path="/r37sh2")
    tools.add_component(sh2, mpn="Arduino_Shield_Headers", refdes="J1")
    tools.add_component(sh2, mpn="BME280", refdes="U1")
    tools.add_component(sh2, mpn="RC0603FR-074K7L", refdes="R1")
    tools.add_component(sh2, mpn="MIC5219-3.3YM5", refdes="U2")
    tools.connect(sh2, net_name="VCC_3V3", pins=[["U2", "VOUT"], ["U1", "VDD"], ["R1", "2"]])
    tools.connect(sh2, net_name="I2C_SDA", pins=[["J1", "A4_SDA"], ["U1", "3"], ["R1", "1"]])
    r37sh2 = _rule(verifier.run_all(sh2), "R37")
    check("R37 silent on a shield I2C bus pulled up to 3.3 V (open-drain guard)",
          not r37sh2, f"{[(i.refdes, i.net) for i in r37sh2]}")

    # --- R36 HAT GPIO on a 5 V rail (host-header aware) ----------------------
    hat = Project(id="r36hat", name="r36hat", path="/r36hat")
    tools.add_component(hat, mpn="RPi_HAT_Header_40P", refdes="J1")
    tools.connect(hat, net_name="VCC_5V", pins=[["J1", "GPIO17"], ["J1", "5V"]])
    r36hat = _rule(verifier.run_all(hat), "R36")
    check("R36 fires: a HAT (3.3 V Pi) GPIO tied to a 5 V rail",
          any(i.refdes == "J1" for i in r36hat))

    # --- pin mapping treats the host header as the MCU surface (7.3) ---------
    from app.services import design_summary
    pm = design_summary.build(sh2).pinMapping
    check("pin mapping shows a shield peripheral reaching the host header",
          any(row.mcuRefdes == "J1" and row.net == "I2C_SDA" for row in pm),
          f"{[(r.device, r.mcuRefdes) for r in pm]}")

    # --- R38: motor rail shared with the MCU rail ----------------------------
    q = Project(id="r38", name="r38", path="/r38")
    tools.add_component(q, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(q, mpn="TB6612FNG", refdes="U2")
    tools.connect(q, net_name="VCC_3V3", pins=[["U1", "2"], ["U2", "VM"]])
    r38 = _rule(verifier.run_all(q), "R38")
    check("R38 fires: TB6612 VM on the MCU's rail",
          any(i.net == "VCC_3V3" and i.severity == "warning" for i in r38))

    q2 = Project(id="r38b", name="r38b", path="/r38b")
    tools.add_component(q2, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(q2, mpn="TB6612FNG", refdes="U2")
    tools.connect(q2, net_name="VCC_3V3", pins=[["U1", "2"]])
    tools.connect(q2, net_name="VMOTOR", pins=[["U2", "VM"]])
    check("R38 silent when the motor rail is separate",
          not _rule(verifier.run_all(q2), "R38"))

    # --- R39: flyback diode on a transistor-switched coil --------------------
    f = Project(id="r39", name="r39", path="/r39")
    tools.add_component(f, mpn="Vibration_Motor_8mm", refdes="M1")
    tools.add_component(f, mpn="AO3400A", refdes="Q1")
    tools.connect(f, net_name="VCC_3V3", pins=[["M1", "1"]])
    tools.connect(f, net_name="MOTOR_SW", pins=[["M1", "2"], ["Q1", "D"]])
    r39 = _rule(verifier.run_all(f), "R39")
    check("R39 fires: transistor-switched coil with no flyback diode",
          any(i.refdes == "M1" for i in r39))

    # Guarded: a diode across the coil's two nets.
    tools.add_component(f, mpn="1N4148WS", refdes="D1")
    tools.connect(f, net_name="VCC_3V3", pins=[["D1", "2"]])
    tools.connect(f, net_name="MOTOR_SW", pins=[["D1", "1"]])
    check("R39 silent with a flyback diode across the coil",
          not _rule(verifier.run_all(f), "R39"))

    # Guarded: a motor on an H-bridge output is already protected.
    f2 = Project(id="r39b", name="r39b", path="/r39b")
    tools.add_component(f2, mpn="Vibration_Motor_8mm", refdes="M1")
    tools.add_component(f2, mpn="TB6612FNG", refdes="U2")
    tools.connect(f2, net_name="MOTOR_A1", pins=[["U2", "AO1"], ["M1", "1"]])
    tools.connect(f2, net_name="MOTOR_A2", pins=[["U2", "AO1B"], ["M1", "2"]])
    check("R39 silent when an integrated H-bridge drives the coil",
          not _rule(verifier.run_all(f2), "R39"))

    # --- R40: connector pin current capacity ---------------------------------
    h = Project(id="r40", name="r40", path="/r40")
    tools.add_component(h, mpn="JST_SH_4P", refdes="J1")  # ~1 A per pin
    tools.add_component(h, mpn="BTS7960", refdes="U1")   # 43 A driver
    # One SH pin feeds the driver's supply rail.
    tools.connect(h, net_name="VBAT", pins=[["J1", "1"], ["U1", "VS"]])
    r40 = _rule(verifier.run_all(h), "R40")
    check("R40 fires: 43 A load through a 1 A JST-SH pin",
          any(i.refdes == "J1" and i.net == "VBAT" for i in r40),
          f"{[(i.refdes, i.net) for i in r40]}")

    # Capacity table sanity: family ratings resolve from real library parts.
    sh_amps = verifier._connector_pin_amps(library.get_part("JST_SH_4P"))
    xh_amps = verifier._connector_pin_amps(library.get_part("JST_XH_2P"))
    hdr_amps = verifier._connector_pin_amps(library.get_part("RPi_HAT_Header_40P"))
    check("R40 capacity table: JST-SH ~1 A/pin", sh_amps == 1.0, f"{sh_amps}")
    check("R40 capacity table: JST-XH ~3 A/pin", xh_amps == 3.0, f"{xh_amps}")
    check("R40 capacity table: 0.1\" header ~3 A/pin", hdr_amps == 3.0, f"{hdr_amps}")
    check("R40 ignores non-connectors",
          verifier._connector_pin_amps(library.get_part("BME280")) is None)

    # --- R41: motor-stall brown-out -------------------------------------------
    s = Project(id="r41", name="r41", path="/r41")
    tools.add_component(s, mpn="TB6612FNG", refdes="U2")
    tools.add_component(s, mpn="28BYJ-48", refdes="M1")   # 240 mA rated -> ~720 mA stall
    tools.add_component(s, mpn="AP2112K-3.3TRG1", refdes="U3")  # 600 mA LDO
    ap = library.get_part("AP2112K-3.3TRG1")
    have_data = ap is not None and ap.currentMaxMa is not None and ap.currentMaxMa < 720
    tools.connect(s, net_name="MOTOR_A", pins=[["U2", "AO1"], ["M1", "1"]])
    # LDO output feeds the motor rail (undersized for stall).
    vout = next((pin.number for pin in ap.pins if pin.type == "power_out"), None) if ap else None
    if vout:
        tools.connect(s, net_name="VCC_MOTOR", pins=[["U3", vout], ["U2", "VM"]])
        r41 = _rule(verifier.run_all(s), "R41")
        if have_data:
            check("R41 fires: stall estimate exceeds the LDO's rating",
                  any(i.net == "VCC_MOTOR" for i in r41),
                  f"{[(i.refdes, i.net) for i in r41]}")
        else:
            check("R41 skipped: library lacks current data for the fixture "
                  "(rule fires only when data exists)", not r41)
    else:
        check("R41 fixture regulator has a power_out pin", False)

    # --- 7.6: robotics power-tree in the design summary ----------------------
    from app.services import design_summary

    q.issues = verifier.run_all(q)  # the R38 shared-rail fixture
    rails = {r.rail: r for r in design_summary.build(q).powerArchitecture}
    shared = rails.get("VCC_3V3")
    check("power-tree: shared motor+MCU rail is role 'mixed'",
          shared is not None and shared.role == "mixed",
          f"{shared.role if shared else None}")
    check("power-tree: shared rail rolls up its power-integrity issues",
          shared is not None and shared.issueCount >= 1
          and shared.worstSeverity in {"warning", "error"})

    q2.issues = verifier.run_all(q2)  # separate motor rail fixture
    rails2 = {r.rail: r for r in design_summary.build(q2).powerArchitecture}
    check("power-tree: a separate motor rail is role 'motor'",
          "VMOTOR" in rails2 and rails2["VMOTOR"].role == "motor")
    check("power-tree: the MCU rail stays role 'logic'",
          "VCC_3V3" in rails2 and rails2["VCC_3V3"].role == "logic")

    s.issues = verifier.run_all(s)  # the R41 stall fixture
    rails3 = {r.rail: r for r in design_summary.build(s).powerArchitecture}
    vm = rails3.get("VCC_MOTOR")
    check("power-tree: current budget columns populated when data exists",
          vm is not None and vm.estCurrentMa is not None and vm.sourceMaxMa is not None,
          f"est={vm.estCurrentMa if vm else None} max={vm.sourceMaxMa if vm else None}")

    # --- stub board stays clean ------------------------------------------------
    good = Project(id="good", name="good", path="/good")
    for _ in agent.run(good, "low-power ESP32 sensor board"):
        pass
    g = verifier.run_all(good)
    spurious = [i for i in g if (i.rule or "").startswith(("R37", "R38", "R39", "R40", "R41"))]
    check("no robotics-rule false positives on the good stub board",
          len(spurious) == 0, f"{[(i.rule, i.refdes) for i in spurious][:5]}")
    good.issues = g
    stub_rails = design_summary.build(good).powerArchitecture
    check("power-tree: every stub rail is role 'logic'",
          all(r.role == "logic" for r in stub_rails),
          f"{[(r.rail, r.role) for r in stub_rails if r.role != 'logic']}")

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
