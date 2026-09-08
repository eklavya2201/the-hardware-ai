"""FreeRouting offload regression — manufacturing-grade routing.

Verifies the external-router path end to end: FreeRouting routes the stub board
COMPLETELY (0 unrouted) and DRC-CLEAN on every routing-controllable class
(clearance / track width / annular / shorts / dangling vias), where the in-house
A* router leaves unrouted nets and trips those checks.

Degrades gracefully: if Java / FreeRouting / KiCad isn't installed, every
assertion is SKIPPED (the in-house router is the fallback) so CI without the
tool still passes. Set FREEROUTING_JAR to exercise it.

Exit codes: 0 pass (or skipped) · 1 an assertion failed.

Usage: python -m scripts.regression_freerouting
"""

from __future__ import annotations

import collections
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


# DRC classes the router is responsible for (as opposed to footprint-inherent
# pad geometry, which we tag KICAD_FP_* and which no router changes).
_ROUTING_CLASSES = {
    "KICAD_clearance", "KICAD_track_width", "KICAD_annular_width",
    "KICAD_shorting_items", "KICAD_via_dangling",
}


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Project
    from app.services import (
        agent, kicad_drc, placement, routing, routing_freerouting,
    )

    print("FreeRouting offload regression\n")

    if not routing_freerouting.is_available():
        print("  [SKIP] FreeRouting/KiCad not installed — in-house router is the "
              "fallback. Set FREEROUTING_JAR to exercise this path.")
        print("\nRESULT: skipped (tool absent).")
        return 0

    p = Project(id="frr", name="frr", path="/frr")
    for _ in agent.run(p, "low-power ESP32 sensor board"):
        pass
    p.board = placement.auto_place(p)
    p.board = routing.auto_route(p)
    ih_unrouted = len({r.net for r in p.board.rats})
    print(f"  in-house router: {len(p.board.traces)} traces, "
          f"{ih_unrouted} unrouted nets\n")

    routed, error = routing_freerouting.route(p, "jlcpcb_2layer", passes=10)
    check("FreeRouting returned a routed board", routed is not None, error or "")
    if routed is None:
        print("\nRESULT: 1 assertion(s) failed.")
        return 1

    check("FreeRouting routed every net (0 unrouted)",
          len({r.net for r in routed.rats}) == 0,
          f"{len({r.net for r in routed.rats})} unrouted")

    p.board = routed
    issues, drc_err = kicad_drc.run(p, "jlcpcb_2layer")
    by = collections.Counter(i.rule for i in issues if i.rule)
    routing_defects = {k: n for k, n in by.items() if k in _ROUTING_CLASSES}
    check("KiCad DRC: 0 routing-geometry violations",
          not routing_defects, str(routing_defects))

    # Round-trip fidelity: extracting FreeRouting's geometry into our Board and
    # re-exporting must not split any net (0 unconnected). Guards the v2.1.0 fix
    # + the PCB_ARC handling in apply_ses.py.
    unconnected = by.get("KICAD_unconnected_items", 0)
    check("re-export preserves connectivity (0 unconnected)",
          unconnected == 0, f"{unconnected} unconnected")

    # Footprint-inherent items (part pads) are info, correctly classified.
    fp = [i for i in issues if (i.rule or "").startswith("KICAD_FP_")]
    check("footprint-inherent items classified as info",
          all(i.severity == "info" for i in fp), f"{len(fp)} fp items")

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
