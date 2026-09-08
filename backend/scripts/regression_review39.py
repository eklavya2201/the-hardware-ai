"""Guard: review #39 — Lucky Singh's audit (2026-08-18) + the 45-prompt phrasing
sensitivity sheet (2026-08-26).

Three dead-on-arrival classes that shipped with 0 errors:

  R77  BNO085 PS0/PS1 mode-select pins left floating — the part never picks a
       protocol. R16 saw them as one more "still to wire" warning; R03 owns
       only EN/CE/RESET/BOOT.
  R78  The MCU I2C landed on ENV_SCL/ENV_SDA (pins 15/16, the IMU's own
       auxiliary-sensor bus) while H_SCL/H_SDA (19/20) stayed open. Nothing
       is shorted, so no rule saw it; the MCU talks to a port that never
       answers. The misleading prompt "use pins 15 and 16" reproduced it.
  R79  AP2112K output capacitor called "optional" — the datasheet requires
       1 uF on VOUT; R01 only checks power_in pins.

Plus: add_component now returns the curated applicationNote and
requiredCompanions, because the library already said "tie PS0/PS1 low" and
the agent never saw it.

Offline, synthetic projects, no LLM spend.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import Project, SchematicIR  # noqa: E402
from app.services import tools, verifier  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def fresh(pid: str) -> Project:
    return Project(id=pid, name=pid, path=f"/tmp/{pid}", ir=SchematicIR())


def bno_on_host(p: Project) -> None:
    """STM32 + BNO085 wired the RIGHT way: host I2C on 19/20, straps to GND."""
    tools.add_component(p, mpn="STM32F405RGT6", refdes="U1")
    tools.add_component(p, mpn="BNO085", refdes="U2")
    tools.connect(p, net_name="GND", pins=[["U1", "1"], ["U2", "2"], ["U2", "25"]])
    tools.connect(p, net_name="VCC_3V3", pins=[["U2", "3"], ["U2", "28"]])
    tools.connect(p, net_name="I2C1_SCL", pins=[["U1", "58"], ["U2", "19"]])
    tools.connect(p, net_name="I2C1_SDA", pins=[["U1", "59"], ["U2", "20"]])


def main() -> int:
    print("\n-- R77: floating strap pins are an ERROR")
    p = fresh("r77")
    bno_on_host(p)
    found = verifier.R77_floating_strap_pin(p)
    ids = {i.id for i in found}
    check("PS1 (pin 5) floating fires", "R77:U2:5" in ids)
    check("PS0/WAKE (pin 6) floating fires", "R77:U2:6" in ids)
    check("CLKSEL0 (pin 10) floating fires", "R77:U2:10" in ids)
    check("all are errors", all(i.severity == "error" for i in found))
    check("SA0 (pin 17, I2C address strap) floating fires", "R77:U2:17" in ids)
    check("BOOTN is R03's, not ours", "R77:U2:4" not in ids)
    check("NRST is R03's, not ours", "R77:U2:11" not in ids)

    print("\n-- R77': the same part with straps tied is silent")
    tools.connect(p, net_name="GND", pins=[["U2", "5"], ["U2", "6"], ["U2", "10"], ["U2", "17"]])
    check("tied straps are silent", not verifier.R77_floating_strap_pin(p))

    print("\n-- R77'': an unplaced-in-use part is not ours (R15 owns it)")
    p2 = fresh("r77-idle")
    tools.add_component(p2, mpn="BNO085", refdes="U2")
    check("nothing wired -> silent", not verifier.R77_floating_strap_pin(p2))

    print("\n-- R78: MCU on the ENV_ bus while H_ pins are open is an ERROR")
    p3 = fresh("r78")
    tools.add_component(p3, mpn="STM32F405RGT6", refdes="U1")
    tools.add_component(p3, mpn="BNO085", refdes="U2")
    tools.connect(p3, net_name="GND", pins=[["U1", "1"], ["U2", "2"]])
    tools.connect(p3, net_name="I2C1_SCL", pins=[["U1", "58"], ["U2", "15"]])
    tools.connect(p3, net_name="I2C1_SDA", pins=[["U1", "59"], ["U2", "16"]])
    found = verifier.R78_secondary_bus_as_host(p3)
    ids = {i.id for i in found}
    check("ENV_SCL (pin 15) on the MCU bus fires", "R78:U2:15" in ids)
    check("ENV_SDA (pin 16) on the MCU bus fires", "R78:U2:16" in ids)
    check("finding names the host pins",
          any("H_SCL" in i.message and "pin 19" in i.message for i in found))
    check("all are errors", all(i.severity == "error" for i in found))

    print("\n-- R78': host bus wired correctly is silent")
    p4 = fresh("r78-ok")
    bno_on_host(p4)
    check("H_SCL/H_SDA on the MCU -> silent", not verifier.R78_secondary_bus_as_host(p4))

    print("\n-- R78'': ENV_ bus used for its own purpose (no MCU on it) is silent")
    tools.add_component(p4, mpn="BME280", refdes="U3")
    tools.connect(p4, net_name="ENV_SCL", pins=[["U2", "15"], ["U3", "4"]])
    tools.connect(p4, net_name="ENV_SDA", pins=[["U2", "16"], ["U3", "3"]])
    check("ENV_ bus to its own sensor -> silent", not verifier.R78_secondary_bus_as_host(p4))

    print("\n-- R79: a regulator output with no cap is an ERROR")
    p5 = fresh("r79")
    tools.add_component(p5, mpn="AP2112K-3.3TRG1", refdes="U1")
    tools.add_component(p5, mpn="CC0603KPX7R9BB105", refdes="C1")
    tools.connect(p5, net_name="VBUS", pins=[["U1", "1"], ["U1", "3"], ["C1", "1"]])
    tools.connect(p5, net_name="GND", pins=[["U1", "2"], ["C1", "2"]])
    tools.connect(p5, net_name="VCC_3V3", pins=[["U1", "5"]])
    found = verifier.R79_regulator_output_cap(p5)
    check("bare VOUT fires", any(i.id == "R79:U1:5" for i in found))
    check("and it is an error", all(i.severity == "error" for i in found))

    print("\n-- R79': 100 nF alone is a WARNING, 1 uF is silent")
    tools.add_component(p5, mpn="CC0603KRX7R9BB104", refdes="C2")
    tools.connect(p5, net_name="VCC_3V3", pins=[["C2", "1"]])
    tools.connect(p5, net_name="GND", pins=[["C2", "2"]])
    found = verifier.R79_regulator_output_cap(p5)
    check("100 nF only -> warning", len(found) == 1 and found[0].severity == "warning")
    tools.add_component(p5, mpn="CC0603KPX7R9BB105", refdes="C3")
    tools.connect(p5, net_name="VCC_3V3", pins=[["C3", "1"]])
    tools.connect(p5, net_name="GND", pins=[["C3", "2"]])
    check("1 uF on VOUT -> silent", not verifier.R79_regulator_output_cap(p5))

    print("\n-- add_component hands the agent the application note + companions")
    p6 = fresh("addc")
    result, _ = tools.add_component(p6, mpn="BNO085", refdes="U1")
    check("applicationNote returned",
          bool(result.get("applicationNote")) and "PS0" in result["applicationNote"])
    check("requiredCompanions returned",
          any(c["role"] == "pullup" for c in (result.get("requiredCompanions") or [])))
    result, _ = tools.add_component(p6, mpn="AP2112K-3.3TRG1", refdes="U2")
    check("AP2112K lists its 1 uF output cap as required",
          any(c["pin"] == "VOUT" for c in (result.get("requiredCompanions") or [])),
          "run scripts.curate_review39_parts first")
    result, _ = tools.add_component(p6, mpn="TP4056", refdes="U3")
    check("TP4056 lists a PROG resistor with the formula",
          any(c["pin"] == "PROG" and "1200" in (c["notes"] or "")
              for c in (result.get("requiredCompanions") or [])),
          "run scripts.curate_review39_parts first")

    print("\n-- the whole rule set still runs on these projects")
    for proj in (p, p3, p4, p5):
        verifier.run_all(proj)
    check("run_all completes", True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("All review-39 regressions pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
