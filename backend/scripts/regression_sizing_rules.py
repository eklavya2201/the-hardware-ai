"""Parametric sizing-rules regression — engineering-rules Phase 5.

Guards the "correctness, not just legality" rules: regulator current adequacy
(R31), linear-regulator thermal (R32), and I²C address collision (R34). They must
FIRE on genuinely-wrong component choices and stay SILENT on the known-good stub.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_sizing_rules
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


def _real(refdes, mpn):
    """A Component whose pins mirror the library part exactly (the sizing rules
    read library pin types/numbers)."""
    from app.models import Component, Pin
    from app.services import library as lib
    p = lib.get_part(mpn)
    return Component(
        refdes=refdes, mpn=mpn,
        pins=[Pin(number=pn.number, name=pn.name, type=pn.type,
                  side=getattr(pn, "side", "left")) for pn in p.pins],
    )


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Net, Project, SchematicIR
    from app.services import agent, library as lib, verifier

    print("Parametric sizing-rules regression\n")
    by = lambda issues, pfx: [i for i in issues if (i.rule or "").startswith(pfx)]

    # R31/R32 — LM7805 (1.5 A, linear) forced to feed an ESP32-S2 (1.8 A) from 24 V.
    u5, u1 = _real("U5", "LM7805"), _real("U1", "ESP32-S2")
    ir = SchematicIR(components=[u5, u1], nets=[
        Net(name="24V", pins=[("U5", "1")]),
        Net(name="5V", pins=[("U5", "3"), ("U1", "1")]),
    ])
    iss = verifier.run_all(Project(id="s", name="s", path="/s", ir=ir))
    r31 = by(iss, "R31")
    check("R31 flags an undersized regulator (error)",
          any(i.severity == "error" for i in r31), f"{len(r31)} found")
    check("R32 flags a hot linear regulator",
          bool(by(iss, "R32")))

    # R34 — two same-address I²C parts on one bus.
    i2c = [p for p in lib.all_parts()
           if "I2C" in (p.interfaces or []) and p.defaultI2cAddress]
    addr_groups: dict[str, list] = {}
    for p in i2c:
        addr_groups.setdefault(p.defaultI2cAddress, []).append(p)
    pair = next((g for g in addr_groups.values() if len(g) >= 1), None)
    if pair:
        a = pair[0]
        ca, cb = _real("U3", a.mpn), _real("U4", a.mpn)
        sda = next((pn.number for pn in ca.pins
                    if pn.name.upper().replace("/", "") in
                    ("SDA", "SDIO", "SDASDIO", "SDISDA", "DATA")), ca.pins[0].number)
        ir2 = SchematicIR(components=[ca, cb], nets=[
            Net(name="I2C_SDA", pins=[("U3", sda), ("U4", sda)])])
        r34 = by(verifier.run_all(Project(id="b", name="b", path="/b", ir=ir2)), "R34")
        check("R34 flags an I²C address collision", bool(r34))
    else:
        check("R34 flags an I²C address collision", False, "no I²C parts in library")

    # No false positives on the known-good stub.
    good = Project(id="g", name="g", path="/g")
    for _ in agent.run(good, "low-power ESP32 sensor board"):
        pass
    sz = by(verifier.run_all(good), "R3")
    sz = [i for i in sz if (i.rule or "")[:3] in ("R31", "R32", "R34")]
    check("no sizing false positives on the good stub", len(sz) == 0,
          f"{len(sz)}: {[i.rule for i in sz][:5]}")

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
