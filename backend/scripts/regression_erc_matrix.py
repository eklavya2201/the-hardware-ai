"""ERC pin-type matrix regression — engineering-rules Phase 1.

Guards the matrix + its net-level companions (R24/R25/R26): the matrix must
FIRE on genuinely-bad topologies and stay SILENT on the known-good stub board
(no false positives on a real design). Also checks matrix symmetry and the
severity ramp.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_erc_matrix
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


def _comp(refdes, *pins):
    from app.models import Component, Pin
    return Component(
        refdes=refdes, mpn=refdes,
        pins=[Pin(number=str(i + 1), name=n, type=t) for i, (n, t) in enumerate(pins)],
    )


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Net, Project, SchematicIR
    from app.services import agent, erc_matrix, verifier

    print("ERC matrix regression\n")

    # --- matrix integrity ---
    order = erc_matrix._ORDER
    sym = all(
        erc_matrix.verdict(a, b) == erc_matrix.verdict(b, a)
        for a in order for b in order
    )
    check("matrix is symmetric", sym)
    check("output↔output is an error cell", erc_matrix.verdict("output", "output") == "E")
    check("power_in↔passive is legal", erc_matrix.verdict("power_in", "passive") == "OK")
    check("unspecified↔anything warns",
          all(erc_matrix.verdict("unspecified", t) in ("W",) for t in order))

    # --- fires on bad topologies ---
    ir = SchematicIR(
        components=[
            _comp("U1", ("GPIO1", "output"), ("GPIO2", "output"),
                  ("VCC", "power_in"), ("GND", "ground")),
            _comp("U2", ("OUT", "output"), ("VDD", "power_in")),
            _comp("U3", ("IN1", "input"), ("IN2", "input")),
            _comp("REG", ("VOUT", "power_out")),
        ],
        nets=[
            Net(name="CONTEND", pins=[("U1", "1"), ("U2", "1")]),   # output↔output
            Net(name="SHORT", pins=[("REG", "1"), ("U1", "4")]),    # power_out↔ground
            Net(name="FLOAT_IN", pins=[("U3", "1"), ("U3", "2")]),  # inputs, no driver
            Net(name="DEADRAIL", pins=[("U1", "3"), ("U2", "2")]),  # power_in, no source
        ],
    )
    bad = Project(id="bad", name="bad", path="/bad", ir=ir)
    issues = verifier.run_all(bad)
    by = lambda pfx: [i for i in issues if (i.rule or "").startswith(pfx)]
    check("R24 flags output↔output contention",
          any(i.net == "CONTEND" for i in by("R24")))
    check("R24 flags a rail↔ground short",
          any(i.net == "SHORT" for i in by("R24")))
    check("R25 flags a floating-input net", any(i.net == "FLOAT_IN" for i in by("R25")))
    check("R26 flags an undriven rail", any(i.net == "DEADRAIL" for i in by("R26")))

    # --- R26 tightening: shunt passives no longer mask an undriven rail ------
    # (live-test finding: a HAT's 3V3 with caps + pull-ups but no header
    # connection passed clean — the board was dead on arrival)
    ir2 = SchematicIR(
        components=[
            _comp("U1", ("VDD", "power_in"), ("SDA", "bidirectional")),
            _comp("C1", ("1", "passive"), ("2", "passive")),
            _comp("R1", ("1", "passive"), ("2", "passive")),
            _comp("REG", ("VOUT", "power_out"), ("FB", "passive")),
            _comp("FB1", ("1", "passive"), ("2", "passive")),  # ferrite bead
            _comp("U2", ("VDD", "power_in")),
        ],
        nets=[
            # Undriven rail dressed with shunt passives: cap to GND + pull-up
            # whose other pin is on a signal net. Must FIRE.
            Net(name="VCC_3V3", pins=[("U1", "1"), ("C1", "1"), ("R1", "1")]),
            Net(name="GND", pins=[("C1", "2")]),
            Net(name="SIG", pins=[("R1", "2"), ("U1", "2")]),
            # Rail fed through a series ferrite from a real source. Must PASS.
            Net(name="REG_OUT", pins=[("REG", "1"), ("FB1", "1")]),
            Net(name="VCC_FILT", pins=[("FB1", "2"), ("U2", "1")]),
        ],
    )
    p26 = Project(id="r26", name="r26", path="/r26", ir=ir2)
    i26 = [i for i in verifier.run_all(p26) if (i.rule or "").startswith("R26")]
    check("R26 fires: undriven rail with only shunt passives (caps/pull-ups)",
          any(i.net == "VCC_3V3" for i in i26), f"{[(i.net) for i in i26]}")
    check("R26 passes: rail bridged to a source through a series passive",
          not any(i.net == "VCC_FILT" for i in i26))

    # --- R42: ground must return to a connector ground pin -------------------
    from app.models import Component as _Comp, Pin as _Pin
    hat_pin = _Comp(refdes="J1", mpn="RPi_HAT_Header_40P",
                    pins=[_Pin(number="6", name="GND", type="ground")])
    ir3 = SchematicIR(
        components=[hat_pin, _comp("U1", ("GND", "ground"), ("VDD", "power_in"))],
        nets=[Net(name="GND", pins=[("U1", "1")])],
    )
    p42 = Project(id="r42", name="r42", path="/r42", ir=ir3)
    i42 = [i for i in verifier.run_all(p42) if (i.rule or "").startswith("R42")]
    check("R42 fires: GND never reaches the connector's ground pins", bool(i42))
    ir3.nets[0].pins.append(("J1", "6"))
    i42b = [i for i in verifier.run_all(p42) if (i.rule or "").startswith("R42")]
    check("R42 silent once GND reaches the connector", not i42b)

    # --- silent on the known-good stub (no false positives) ---
    good = Project(id="good", name="good", path="/good")
    for _ in agent.run(good, "low-power ESP32 sensor board"):
        pass
    g = verifier.run_all(good)
    erc = [i for i in g if (i.rule or "").startswith(("R24", "R25", "R26", "R42"))]
    check("no ERC-matrix false positives on the good stub board",
          len(erc) == 0, f"{len(erc)} spurious: {[i.rule for i in erc][:5]}")

    # --- severity ramp (two-stage) ---
    check("unambiguous shorts/contention are hard errors (output↔output)",
          erc_matrix.severity("output", "output") == "error")
    check("two-sources-on-a-rail is a hard error (power_out↔power_out)",
          erc_matrix.severity("power_out", "power_out") == "error")
    check("rail↔ground short is a hard error",
          erc_matrix.severity("ground", "power_in") == "error")
    check("context-dependent cell stays a warning during the ramp "
          "(power_in↔input, e.g. EN-to-VCC)",
          erc_matrix.severity("power_in", "input") == "warning"
          and erc_matrix.STRICT_ERRORS is False)

    # --- R20 retired (subsumed by the matrix) ---
    check("R20 retired from the active rule set (subsumed by R24)",
          all(getattr(r, "__name__", "") != "R20_one_source_per_rail"
              for r in verifier.ALL_RULES))

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
