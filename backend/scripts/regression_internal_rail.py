"""Guard: internal-regulator pins (VCAP class) — framework audit, 2026-08-28.

Two defects, one incident: the STM32's VCAP pin (fed by the chip's OWN
on-chip regulator, needs only a capacitor to ground) was (a) reported by R26
as a power pin on an unsourced rail — a false alarm — and (b) the agent then
tied VCAP to 3.3 V to make the alarm go away, which can destroy the chip.

  - library: the five mistyped VCAP-class pins are power_out
    (run scripts.curate_internal_rail_pins first if this fails)
  - R26 is silent on the CORRECT topology (VCAP + cap to GND) and still
    fires on a genuinely unsourced ordinary rail
  - R80 errors on VCAP tied to a supply rail / to ground / used to power
    another part, warns when the required capacitor is missing, and is
    silent on the correct topology and on real LDO-output pins (REGOUT)
  - the agent prompt and fix-up prompt forbid rewiring to silence a finding

Offline, synthetic projects, no LLM spend.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import Project, SchematicIR, VerifierIssue  # noqa: E402
from app.services import agent, library, tools, verifier  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def fresh(pid: str) -> Project:
    return Project(id=pid, name=pid, path=f"/tmp/{pid}", ir=SchematicIR())


def r80(p: Project) -> list[VerifierIssue]:
    return verifier.R80_internal_rail_pin(p)


def main() -> int:
    print("\n-- library: internal-rail pins are typed power_out")
    expected = {
        "STM32H743ZIT6": ["86", "125"],
        "STM32U575ZIT6": ["137"],
        "STM32F405RGT6": ["31", "47"],
        "PIC32MX250F128B": ["20"],
        "STM32F411CEU6": ["22"],   # was already right — must stay right
    }
    for mpn, numbers in expected.items():
        part = library.get_part(mpn)
        for num in numbers:
            pin = next((pp for pp in part.pins if pp.number == num), None)
            check(f"{mpn} pin {num} ({pin.name if pin else '?'}) is power_out",
                  pin is not None and pin.type == "power_out",
                  f"got {pin.type if pin else 'missing'}")

    print("\n-- R26: the correct VCAP topology is not an unsourced rail")
    p = fresh("vcap-good")
    tools.add_component(p, mpn="STM32F405RGT6", refdes="U1")
    tools.add_component(p, mpn="CL10A105KB8NNNC", refdes="C1")  # 1 uF
    tools.add_component(p, mpn="CL10A105KB8NNNC", refdes="C2")
    tools.connect(p, net_name="VCAP1", pins=[["U1", "31"], ["C1", "1"]])
    tools.connect(p, net_name="VCAP2", pins=[["U1", "47"], ["C2", "1"]])
    tools.connect(p, net_name="GND", pins=[["U1", "18"], ["C1", "2"], ["C2", "2"]])
    r26 = verifier.R26_power_pin_driven(p)
    check("R26 silent on VCAP nets",
          not any(i.net in {"VCAP1", "VCAP2"} for i in r26),
          "; ".join(i.id for i in r26))
    check("R80 silent on the correct topology", not r80(p),
          "; ".join(i.id for i in r80(p)))

    print("\n-- R26 still fires on a genuinely unsourced ordinary rail")
    p2 = fresh("vdd-unsourced")
    tools.add_component(p2, mpn="STM32F405RGT6", refdes="U1")
    tools.connect(p2, net_name="VCC_3V3", pins=[["U1", "19"]])  # VDD, nothing sources it
    tools.connect(p2, net_name="GND", pins=[["U1", "18"]])
    check("R26 fires on the bare VDD rail",
          any(i.net == "VCC_3V3" for i in verifier.R26_power_pin_driven(p2)))

    print("\n-- R80: VCAP tied to a supply rail is an ERROR")
    p3 = fresh("vcap-rail")
    tools.add_component(p3, mpn="STM32F405RGT6", refdes="U1")
    tools.add_component(p3, mpn="AP2112K-3.3TRG1", refdes="U2")
    # the audited failure verbatim: VCAP wired onto the 3.3 V rail
    tools.connect(p3, net_name="VCC_3V3", pins=[["U1", "31"], ["U2", "5"], ["U1", "19"]])
    found = r80(p3)
    check("R80:U1:31 fires", any(i.id == "R80:U1:31" for i in found))
    check("it is an error",
          all(i.severity == "error" for i in found if i.id == "R80:U1:31"))
    check("VDD (pin 19) on the same rail is NOT flagged",
          not any(i.id == "R80:U1:19" for i in found))

    print("\n-- R80: VCAP tied to ground is an ERROR")
    p4 = fresh("vcap-gnd")
    tools.add_component(p4, mpn="STM32F405RGT6", refdes="U1")
    tools.connect(p4, net_name="GND", pins=[["U1", "31"], ["U1", "18"]])
    check("R80:U1:31 fires on the ground tie",
          any(i.id == "R80:U1:31" and i.severity == "error" for i in r80(p4)))

    print("\n-- R80: powering another part from VCAP is an ERROR")
    p5 = fresh("vcap-feeds")
    tools.add_component(p5, mpn="STM32F405RGT6", refdes="U1")
    tools.add_component(p5, mpn="BNO085", refdes="U2")
    tools.connect(p5, net_name="CORE", pins=[["U1", "31"], ["U2", "3"]])  # U2 VDD power_in
    check("R80:U1:31 fires when a power_in shares the net",
          any(i.id == "R80:U1:31" and i.severity == "error" for i in r80(p5)))

    print("\n-- R80: a wired VCAP with no capacitor is a WARNING")
    p6 = fresh("vcap-nocap")
    tools.add_component(p6, mpn="STM32F405RGT6", refdes="U1")
    tools.add_component(p6, mpn="RC0603FR-0710KL", refdes="R1")  # a resistor is not the cap
    tools.connect(p6, net_name="VCAP1", pins=[["U1", "31"], ["R1", "1"]])
    found = r80(p6)
    check("R80:U1:31 warns on the missing capacitor",
          any(i.id == "R80:U1:31" and i.severity == "warning" for i in found))

    print("\n-- R80: an unwired VCAP is not ours (R07/R16 own it)")
    p7 = fresh("vcap-unwired")
    tools.add_component(p7, mpn="STM32F405RGT6", refdes="U1")
    check("nothing wired -> silent", not r80(p7))

    print("\n-- R80: a real LDO-output pin (REGOUT) may feed external circuits")
    p8 = fresh("regout")
    tools.add_component(p8, mpn="BQ76920PWR", refdes="U1")
    tools.add_component(p8, mpn="BNO085", refdes="U2")
    tools.connect(p8, net_name="VREG", pins=[["U1", "8"], ["U2", "3"]])
    check("REGOUT feeding a load is silent", not r80(p8),
          "; ".join(i.id for i in r80(p8)))

    print("\n-- the rule is registered and the agent is instructed")
    check("R80 is in ALL_RULES", verifier.R80_internal_rail_pin in verifier.ALL_RULES)
    check("_FIX_HINTS carries R80", "R80" in agent._FIX_HINTS)
    check("SYSTEM_PROMPT forbids rewiring to silence a finding",
          "NEVER REWIRE A DESIGN JUST TO SILENCE A FINDING" in agent.SYSTEM_PROMPT)
    fixup = agent._build_fixup_prompt(
        [VerifierIssue(id="R26:X", severity="error", message="m", net="X",
                       rule="R26_power_pin_driven")], 1)
    check("the fix-up prompt demands data-justified fixes",
          "A FIX MUST BE JUSTIFIED BY THE PART'S DATA" in fixup)
    check("the fix-up prompt names the VCAP class", "VCAP" in fixup)

    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
