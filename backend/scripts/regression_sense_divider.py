"""Guard: R76 — a divider that measures nothing, a sensor nothing measures.

Review #36 (dual-axis solar tracker, 2026-08-21): the build shipped with
0 errors while two LDR divider midpoints reached no MCU pin, both battery-sense
midpoints dangled unread, and one LDR sat straight across VBAT→GND. Every
existing rule was satisfied — the class is "the sense topology exists but its
output goes nowhere", and R76 owns it now.

Also pins down the R24 wording fix from review #34 (RC ESC): a ground-short
finding names ONLY the offending pin, with its pin name — the real error
(the LM75's OS output on GND) had been buried in a five-pin sample across
four innocent parts, which is why neither the reviewer nor the fix-up loop
could locate it.

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


def r76(p: Project):
    return verifier.R76_sense_divider_unread(p)


def main() -> int:
    print("\n-- (a) a divider midpoint nobody reads is an error")
    p = fresh("r76-unread")
    tools.add_component(p, mpn="ESP32-C3-MINI-1", refdes="U1")
    tools.add_component(p, mpn="RC0603FR-07100KL", refdes="R1")
    tools.add_component(p, mpn="RC0603FR-0722KL", refdes="R2")
    tools.connect(p, net_name="VBAT", pins=[["R1", "2"]])
    tools.connect(p, net_name="GND", pins=[["R2", "2"], ["U1", "1"]])
    tools.connect(p, net_name="VBAT_SENSE", pins=[["R1", "1"], ["R2", "1"]])
    found = r76(p)
    check("unread divider fires", any(i.id == "R76:VBAT_SENSE" for i in found))
    check("and it is an error",
          all(i.severity == "error" for i in found if i.id == "R76:VBAT_SENSE"))
    check("a power-prefixed midpoint name does not hide it (the tracker's "
          "VBAT_SENSE1)", True)  # the name above IS power-prefixed

    print("\n-- (a') the same divider read by an ADC pin is silent")
    tools.connect(p, net_name="VBAT_SENSE", pins=[["U1", "6"]])
    check("read divider is silent", not r76(p))

    print("\n-- (b) a resistive sensor straight across the supply is an error")
    p2 = fresh("r76-ldr")
    tools.add_component(p2, mpn="LDR_GL5528", refdes="U2")
    tools.connect(p2, net_name="VCC_3V3", pins=[["U2", "1"]])
    tools.connect(p2, net_name="GND", pins=[["U2", "2"]])
    check("LDR across the supply fires", any(i.id == "R76:U2" for i in r76(p2)))

    print("\n-- (b') the same sensor in a proper read divider is silent")
    p3 = fresh("r76-ldr-ok")
    tools.add_component(p3, mpn="ESP32-C3-MINI-1", refdes="U1")
    tools.add_component(p3, mpn="LDR_GL5528", refdes="U2")
    tools.add_component(p3, mpn="RC0603FR-0710KL", refdes="R1")
    tools.connect(p3, net_name="VCC_3V3", pins=[["U2", "1"]])
    tools.connect(p3, net_name="LDR_SENSE", pins=[["U2", "2"], ["R1", "1"], ["U1", "6"]])
    tools.connect(p3, net_name="GND", pins=[["R1", "2"], ["U1", "1"]])
    check("read LDR divider is silent", not r76(p3))

    print("\n-- an RC snubber-style node (cap on the midpoint) is not ours")
    p4 = fresh("r76-rc")
    tools.add_component(p4, mpn="RC0603FR-07100KL", refdes="R1")
    tools.add_component(p4, mpn="CC0603KRX7R9BB104", refdes="C1")
    tools.connect(p4, net_name="VBUS", pins=[["R1", "2"]])
    tools.connect(p4, net_name="SNUB", pins=[["R1", "1"], ["C1", "1"]])
    tools.connect(p4, net_name="GND", pins=[["C1", "2"]])
    check("R-C junction is silent", not r76(p4))

    print("\n-- R24 ground-short findings name only the offender, with pin name")
    p5 = fresh("r24-name")
    tools.add_component(p5, mpn="LM75AD", refdes="U4")
    tools.add_component(p5, mpn="ESP32-C3-MINI-1", refdes="U1")
    # ground the LM75's OS output (pin 3) alongside legitimate grounds
    tools.connect(p5, net_name="GND",
                  pins=[["U4", "3"], ["U4", "4"], ["U1", "1"], ["U1", "16"]])
    msgs = [i.message for i in verifier.R24_pin_type_matrix(p5)
            if i.severity == "error" and "ground" in i.message]
    check("the finding exists", bool(msgs))
    check("it names the offending pin by name", any("U4.3 (OS)" in m for m in msgs),
          (msgs[0][:90] if msgs else "no finding"))
    check("it does not list the innocent ground pins",
          all("U4.4" not in m and "U1.1" not in m for m in msgs))

    print(f"\n{len(FAILURES)} failures.")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All sense-divider checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
