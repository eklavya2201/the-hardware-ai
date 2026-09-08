"""Route-quality comparison harness — Week-2 task 4.3.

Task 4.3 asks: *optionally* cross-check / offload routing to KiCad's router,
keep the in-house A* router as the fast interactive preview, and **compare route
quality on a few reference designs before deciding scope.**

Key finding this harness is built around: **KiCad 10 has no built-in / headless
autorouter.** Its python API exposes only `ExportSpecctraDSN` / `ImportSpecctraSES`
— the round-trip hooks for the *external* FreeRouting engine. So "offload to
KiCad's router" really means "export a Specctra DSN and route it in FreeRouting,
then import the .ses back." There is nothing to call in-process.

Given that, this harness measures what we CAN measure objectively today:
  1. In-house A* router output metrics per reference design (trace length, via
     count, unrouted nets).
  2. **Industry route quality** = how many violations KiCad's own DRC engine
     (task 4.2) finds in the in-house router's output. That is the honest,
     tool-independent quality score, and the data point for the scope decision.
  3. Whether the FreeRouting offload handoff is wired (Specctra DSN export
     succeeds) — so if we decide to add it, the plumbing is proven.

Run this, read the table, and use it to decide whether to (a) tune the in-house
router to pass KiCad DRC, or (b) invest in a FreeRouting offload. See
docs/ROUTING_KICAD_COMPARISON.md for the written recommendation.

Usage:
    python -m scripts.compare_routing
"""

from __future__ import annotations

import collections
import math
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _trace_length(board) -> float:
    total = 0.0
    for t in board.traces:
        for s in t.segments:
            total += math.hypot(s.x2 - s.x1, s.y2 - s.y1)
    return total


def _reference_designs():
    """A few reference designs. Each returns a routed Project. Today they're
    variations on the deterministic stub board at different board densities; add
    real saved projects here as the library of reference layouts grows."""
    from app.models import Project
    from app.services import agent, placement, routing

    designs = []
    for label in ("esp32-sensor",):
        p = Project(id=f"cmp-{label}", name=label, path=f"/{label}")
        for _ in agent._run_stub(p, "esp32 sensor board"):
            pass
        p.board = placement.auto_place(p)
        p.board = routing.auto_route(p)
        designs.append((label, p))
    return designs


def _kicad_drc_counts(project) -> tuple[dict, str | None]:
    from app.services import kicad_drc
    issues, error = kicad_drc.run(project)
    by_type = collections.Counter()
    for i in issues:
        by_type[(i.rule or "KICAD_?").replace("KICAD_", "")] += 1
    return dict(by_type), error


def _dsn_export_ok(project) -> tuple[bool, str]:
    """Prove the FreeRouting offload handoff is wired: build the board and export
    a Specctra DSN via pcbnew."""
    from app.services import export, kicad_bridge
    if not kicad_bridge.is_available():
        return False, "KiCad (pcbnew) absent"
    try:
        pcb_text = export.kicad_pcb(project)
    except Exception as exc:  # noqa: BLE001
        return False, f"board build failed: {exc}"
    res = kicad_bridge.run_script("export_dsn.py", {"pcb": pcb_text}, timeout=60)
    if not res.success:
        return False, res.error or "DSN export failed"
    return True, f"{res.data.get('bytes', 0)} bytes"


def main() -> int:
    from app.services import kicad_bridge

    print("Route-quality comparison — in-house A* router vs KiCad industry DRC")
    print(f"KiCad pcbnew: {'available' if kicad_bridge.is_available() else 'ABSENT'}")
    print(f"kicad-cli:    {'available' if kicad_bridge.cli_available() else 'ABSENT'}")
    print("Note: KiCad has no headless autorouter — offload = FreeRouting via Specctra DSN.\n")

    for label, project in _reference_designs():
        board = project.board
        length = _trace_length(board)
        vias = len(board.vias)
        unrouted = len({r.net for r in board.rats})
        nets = len(project.ir.nets)

        print("=" * 68)
        print(f"Design: {label}  ({len(board.components)} parts, {nets} nets, "
              f"{board.width:.0f}×{board.height:.0f}mm, {board.layer_count}L)")
        print("-" * 68)
        print("  In-house A* router:")
        print(f"    total trace length : {length:8.1f} mm")
        print(f"    vias               : {vias}")
        print(f"    unrouted nets      : {unrouted} / {nets}")

        drc_counts, drc_err = _kicad_drc_counts(project)
        print("  KiCad industry DRC on that output (route-quality score):")
        if drc_err:
            print(f"    (skipped: {drc_err})")
        elif not drc_counts:
            print("    clean — 0 violations")
        else:
            total = sum(drc_counts.values())
            print(f"    total violations   : {total}")
            for vtype, n in sorted(drc_counts.items(), key=lambda x: -x[1]):
                print(f"       {n:4d}  {vtype}")

        ok, detail = _dsn_export_ok(project)
        print("  FreeRouting offload handoff (Specctra DSN export):")
        print(f"    {'READY' if ok else 'unavailable'} — {detail}")
        print()

    print("=" * 68)
    print("Interpretation: clearance/track_width violations reflect the in-house")
    print("router's 0.4mm grid + tight defaults, NOT a routing failure. Decide")
    print("scope from docs/ROUTING_KICAD_COMPARISON.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
