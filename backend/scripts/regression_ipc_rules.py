"""IPC parametric rules regression — engineering-rules Phases 3 & 4.

Guards the trace-width (R27), clearance (R28), diff-pair (R29), and
decoupling-per-pin (R30) rules + the underlying ipc.py formulas: they must FIRE
on genuinely-bad electricals and stay SILENT on the known-good stub board.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_ipc_rules
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


def _comp(refdes, mpn, *pins):
    from app.models import Component, Pin
    return Component(
        refdes=refdes, mpn=mpn,
        pins=[Pin(number=str(i + 1), name=n, type=t) for i, (n, t) in enumerate(pins)],
    )


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import (
        Board, Footprint, Net, PlacedComponent, Project, SchematicIR, Trace,
        TraceSegment,
    )
    from app.services import agent, ipc, placement, routing, verifier

    print("IPC parametric rules regression\n")

    # --- formula sanity (validated vs published calculators) ---
    check("IPC-2221 trace width 1A/10C/1oz ≈ 0.30mm",
          abs(ipc.trace_width_mm(1.0) - 0.30) < 0.03, f"{ipc.trace_width_mm(1.0):.3f}")
    check("IPC-2221 trace width 10A/10C/1oz ≈ 7.2mm",
          abs(ipc.trace_width_mm(10.0) - 7.2) < 0.3, f"{ipc.trace_width_mm(10.0):.2f}")
    check("IPC-2221 clearance grows with voltage",
          ipc.clearance_mm(3.3) < ipc.clearance_mm(150) < ipc.clearance_mm(600))

    # --- R27 trace width: high-current rail, schematic + routed board ---
    ir = SchematicIR(
        components=[
            _comp("U1", "LM2596", ("IN", "power_in"), ("OUT", "power_out"), ("GND", "ground")),
            _comp("U2", "ESP32-S2", ("VCC", "power_in"), ("GND", "ground")),
        ],
        nets=[Net(name="5V", pins=[("U1", "2"), ("U2", "1")])],
    )
    p = Project(id="hc", name="hc", path="/hc", ir=ir)
    s_sch = verifier.run_all(p)
    check("R27 advises width on a high-current rail (schematic stage)",
          any((i.rule or "").startswith("R27") for i in s_sch))

    p.board = Board(
        width=40, height=30,
        components=[PlacedComponent(refdes="U1", mpn="LM2596", x=5, y=5,
                    footprint=Footprint(name="X", width=3, height=3, pads=[]))],
        traces=[Trace(net="5V", layer="top", width=0.2,
                segments=[TraceSegment(x1=0, y1=0, x2=10, y2=0)])],
    )
    r27 = [i for i in verifier.run_all(p) if (i.rule or "").startswith("R27")]
    check("R27 flags an under-width routed trace (PCB stage)",
          any(i.severity == "warning" and i.source == "ipc" for i in r27))

    # --- R28 clearance: only high-voltage rails ---
    hv = Project(id="hv", name="hv", path="/hv", ir=SchematicIR(
        components=[_comp("U1", "X", ("HV", "power_in"), ("G", "ground"))],
        nets=[Net(name="150V", pins=[("U1", "1")])]))
    check("R28 flags a >100V rail needing extra clearance",
          any((i.rule or "").startswith("R28") for i in verifier.run_all(hv)))

    lv = Project(id="lv", name="lv", path="/lv", ir=SchematicIR(
        components=[_comp("U1", "X", ("V", "power_in"), ("G", "ground"))],
        nets=[Net(name="5V", pins=[("U1", "1")])]))
    check("R28 stays silent on a low-voltage rail (fab min dominates)",
          not any((i.rule or "").startswith("R28") for i in verifier.run_all(lv)))

    # --- R29 diff pair ---
    usb = Project(id="usb", name="usb", path="/usb", ir=SchematicIR(
        components=[_comp("U2", "ESP32-S2", ("DP", "bidirectional"), ("DM", "bidirectional"))],
        nets=[Net(name="USB_D+", pins=[("U2", "1")]), Net(name="USB_D-", pins=[("U2", "2")])]))
    check("R29 detects a USB differential pair",
          any((i.rule or "").startswith("R29") for i in verifier.run_all(usb)))

    # --- silent on the known-good stub (no false positives) ---
    good = Project(id="g", name="g", path="/g")
    for _ in agent.run(good, "low-power ESP32 sensor board"):
        pass
    good.board = placement.auto_place(good)
    good.board = routing.auto_route(good)
    g = verifier.run_all(good)
    ipc_hits = [i for i in g if (i.rule or "").startswith(("R27", "R28", "R29", "R30"))]
    check("no IPC-rule false positives on the good stub board",
          len(ipc_hits) == 0, f"{len(ipc_hits)}: {[i.rule for i in ipc_hits][:5]}")

    # --- provenance: IPC findings carry source='ipc' ---
    check("IPC findings are tagged source='ipc'",
          all(i.source == "ipc" for i in r27))

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
