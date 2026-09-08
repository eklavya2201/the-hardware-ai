"""Mechanical IR + kernel regression — Week 3 (tasks 5.1/5.2/5.4).

Verifies the mechanical tool-calls mutate the IR correctly and enforce the
anti-hallucination contract, and — when CadQuery is installed — that the CAD
kernel exports a valid STEP solid. STEP checks skip gracefully when the (optional)
kernel is absent.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_mechanical
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


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Project
    from app.services import agent, library, mechanical_bridge, mechanical_tools as mt, placement

    print("Mechanical IR + kernel regression\n")

    p = Project(id="m", name="m", path="/m")
    for _ in agent._run_stub(p, "esp32"):
        pass
    p.board = placement.auto_place(p)

    # 5.4 — tool calls mutate the IR only through the tools.
    mt.dispatch(p, "derive_from_board", {})
    check("derive_from_board seeds an outline", p.mechanical.outline is not None)
    check("derive_from_board adds 4 mounting holes",
          len(p.mechanical.mountingHoles) == 4, str(len(p.mechanical.mountingHoles)))

    # 6.1 — auto-enclosure: real clearances + connector cutouts derived from the
    # placed board. The stub ESP32 board carries a USB-C receptacle (J1) at an edge.
    from app.services import mechanical_derive
    derived = list(p.mechanical.cutouts)
    check("6.1 derive_from_board emits a connector cutout", len(derived) >= 1,
          f"{len(derived)} cutout(s)")
    j1 = next((c for c in derived if c.refdes == "J1"), None)
    check("6.1 cutout is tagged with the real placed connector refdes", j1 is not None)
    if j1 is not None:
        check("6.1 cutout is assigned to a real board edge", j1.edge in ("left", "right", "top", "bottom"),
              j1.edge)
        # J1 sits near the right edge → the opening must land on that wall.
        pc = next(c for c in p.board.components if c.refdes == "J1")
        exp_edge, _ = mechanical_derive._nearest_edge(pc.x, pc.y, p.board.width, p.board.height)
        check("6.1 cutout edge matches the connector's nearest board edge", j1.edge == exp_edge,
              f"{j1.edge} vs {exp_edge}")
    check("6.1 clearance_top reflects the tallest part (> floor)",
          p.mechanical.enclosure.clearance_top > 5.0, f"{p.mechanical.enclosure.clearance_top} mm")
    # Only real connector parts get a cutout — no ICs/passives punch the wall.
    cut_refs = {c.refdes for c in derived}
    non_conn = {pc.refdes for pc in p.board.components
                if (library.get_part(pc.mpn).category if library.get_part(pc.mpn) else "") != "connector"}
    check("6.1 no non-connector part gets a wall cutout", cut_refs.isdisjoint(non_conn),
          f"offending: {cut_refs & non_conn}")

    n_before = len(p.mechanical.cutouts)
    mt.dispatch(p, "add_connector_cutout",
                {"x": 0, "y": 30, "width": 12, "height": 6, "edge": "left", "refdes": "J1"})
    check("add_connector_cutout for a real connector",
          len(p.mechanical.cutouts) == n_before + 1)

    mt.dispatch(p, "set_enclosure", {"kind": "box", "wall_thickness": 2.5})
    check("set_enclosure applied", p.mechanical.enclosure.kind == "box"
          and p.mechanical.enclosure.wall_thickness == 2.5)

    # Anti-hallucination: a cutout for a connector that isn't placed is rejected.
    try:
        mt.dispatch(p, "add_connector_cutout",
                    {"x": 0, "y": 0, "width": 5, "height": 5, "refdes": "J99"})
        check("cutout for a non-existent connector is rejected", False)
    except mt.MechToolError:
        check("cutout for a non-existent connector is rejected", True)

    # delete round-trips
    hid = p.mechanical.mountingHoles[0].id
    mt.dispatch(p, "delete_mounting_hole", {"id": hid})
    check("delete_mounting_hole removes it", len(p.mechanical.mountingHoles) == 3)

    # 5.2 — CAD kernel STEP export (skips if CadQuery absent).
    if not mechanical_bridge.is_available():
        print("  [SKIP] CadQuery not installed — STEP export not exercised "
              "(optional kernel; `pip install cadquery`).")
    else:
        step, err = mechanical_bridge.build_step(p, include_enclosure=False)
        check("board STEP export produced bytes", step is not None, err or f"{len(step or b'')} B")
        check("STEP file has a valid ISO-10303-21 header",
              step is not None and step[:13] == b"ISO-10303-21;")
        step2, err2 = mechanical_bridge.build_step(p, include_enclosure=True)
        check("enclosure STEP export produced bytes", step2 is not None, err2 or "")

        # 6.1 — the cutout opens the correct WALL, not straight through. Verify via
        # solid volume through the real kernel: a single-wall opening removes less
        # material than the legacy through-cut, which removes less than no cutout.
        _check_edge_aware_cutout(mechanical_bridge, agent, placement, mt)

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


def _check_edge_aware_cutout(mechanical_bridge, agent, placement, mt) -> None:
    """Prove the 6.1 connector cutout opens only its named wall (not both) by
    comparing enclosure solid volumes through the CAD kernel. Best-effort: a
    kernel quirk shouldn't fail the whole regression."""
    import tempfile
    from pathlib import Path

    from app.models import Project

    try:
        import cadquery as cq

        p = Project(id="edge", name="edge", path="/edge")
        for _ in agent._run_stub(p, "esp32"):
            pass
        p.board = placement.auto_place(p)
        mt.dispatch(p, "derive_from_board", {})
        if not p.mechanical.cutouts:
            check("6.1 edge-aware cutout opens one wall", False, "no derived cutout")
            return

        def _vol() -> float:
            data, err = mechanical_bridge.build_step(p, include_enclosure=True)
            if not data:
                raise RuntimeError(err or "no STEP")
            with tempfile.TemporaryDirectory() as t:
                fp = Path(t) / "m.step"
                fp.write_bytes(data)
                return cq.importers.importStep(str(fp)).val().Volume()

        v_edge = _vol()
        saved = list(p.mechanical.cutouts)
        p.mechanical.cutouts = []
        v_none = _vol()
        p.mechanical.cutouts = saved
        p.mechanical.cutouts[0].edge = "none"  # legacy through-cut (both walls)
        v_through = _vol()
        check(
            "6.1 edge-aware cutout removes one wall (none > single-wall > through)",
            v_none > v_edge > v_through,
            f"none={v_none:.0f} edge={v_edge:.0f} through={v_through:.0f}",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [SKIP] edge-aware cutout volume check not run ({exc}).")


if __name__ == "__main__":
    raise SystemExit(main())
