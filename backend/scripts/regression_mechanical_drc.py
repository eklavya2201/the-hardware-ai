"""Mechanical DRC regression — Week-4 ECAD task 6.2.

Asserts the mechanical rules (`mechanical_verifier.py`, MECH_R01–R05):
  * are CLEAN on the known-good auto-derived enclosure (0 false positives), and
  * FIRE on deliberately-broken geometry (too-small outline, part taller than the
    cavity, sub-minimum wall, a mounting hole off the outline, a connector cutout
    moved off its connector), and
  * merge without clobbering the electrical / PCB diagnostics, and
  * carry source="mechanical" so the Review panel can badge them.

Exit codes: 0 pass · 1 an assertion failed.
Usage: python -m scripts.regression_mechanical_drc
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


def _rules(issues, prefix="MECH_R"):
    return {i.rule for i in issues if i.rule and i.rule.startswith(prefix)}


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import VerifierIssue
    from app.services import agent, mechanical_tools as mt, mechanical_verifier as mv, placement

    print("Mechanical DRC regression (6.2)\n")

    p = _make_project(agent, placement)
    mt.dispatch(p, "derive_from_board", {})

    # --- clean on the known-good derived enclosure ---
    clean = mv.run(p)
    check("derived enclosure is mechanically clean (0 issues)", not clean,
          f"{len(clean)} issue(s): {[i.rule for i in clean]}")

    # --- MECH_R01: shrink the outline below the placement extent ---
    p1 = _make_project(agent, placement)
    mt.dispatch(p1, "derive_from_board", {})
    mt.dispatch(p1, "set_board_outline", {"width": 10, "height": 10})
    r1 = _rules(mv.run(p1))
    check("MECH_R01 fires when the outline is too small for the parts", "MECH_R01" in r1, str(r1))

    # --- MECH_R02: drop clearance_top below the tallest part ---
    p2 = _make_project(agent, placement)
    mt.dispatch(p2, "derive_from_board", {})
    mt.dispatch(p2, "set_enclosure", {"clearance_top": 0.5})
    r2 = _rules(mv.run(p2))
    check("MECH_R02 fires when a part is taller than the cavity", "MECH_R02" in r2, str(r2))

    # --- MECH_R03: sub-minimum wall thickness ---
    p3 = _make_project(agent, placement)
    mt.dispatch(p3, "derive_from_board", {})
    mt.dispatch(p3, "set_enclosure", {"wall_thickness": 0.5})
    r3 = _rules(mv.run(p3))
    check("MECH_R03 fires on a sub-minimum wall", "MECH_R03" in r3, str(r3))

    # --- MECH_R04: move a mounting hole outside the outline ---
    p4 = _make_project(agent, placement)
    mt.dispatch(p4, "derive_from_board", {})
    hid = p4.mechanical.mountingHoles[0].id
    p4.mechanical.mountingHoles[0].x = p4.board.width + 50  # shove it off-board
    r4 = _rules(mv.run(p4))
    check("MECH_R04 fires when a mounting hole is off the outline", "MECH_R04" in r4, str(r4))

    # --- MECH_R05: move the connector away from its cutout's wall ---
    p5 = _make_project(agent, placement)
    mt.dispatch(p5, "derive_from_board", {})
    cuts = p5.mechanical.cutouts
    if cuts:
        ref = cuts[0].refdes
        pc = next(c for c in p5.board.components if c.refdes == ref)
        pc.x, pc.y = 1.0, 1.0  # yank it to the opposite corner (bottom-left)
        r5 = _rules(mv.run(p5))
        check("MECH_R05 fires when a connector moves off its cutout", "MECH_R05" in r5, str(r5))
    else:
        check("MECH_R05 fires when a connector moves off its cutout", False,
              "no derived cutout to perturb")

    # --- merge preserves non-mechanical diagnostics ---
    p6 = _make_project(agent, placement)
    p6.issues = [
        VerifierIssue(id="R01:x", severity="warning", message="keep me", rule="R01", source="verifier"),
        VerifierIssue(id="PCB_R07:y", severity="error", message="keep me too", rule="PCB_R07", source="pcb"),
    ]
    mt.dispatch(p6, "derive_from_board", {})
    mt.dispatch(p6, "set_enclosure", {"clearance_top": 0.5})  # force a MECH error
    n = mv.merge_into_project(p6)
    kept = {i.rule for i in p6.issues if i.rule in ("R01", "PCB_R07")}
    check("merge keeps the electrical/PCB rows", kept == {"R01", "PCB_R07"}, str(kept))
    check("merge adds mechanical rows", any(i.rule and i.rule.startswith("MECH_") for i in p6.issues))
    check("mechanical issues carry source='mechanical'",
          all(i.source == "mechanical" for i in p6.issues if i.rule and i.rule.startswith("MECH_")))
    # re-run replaces, not accumulates
    before = len([i for i in p6.issues if i.rule and i.rule.startswith("MECH_")])
    mv.merge_into_project(p6)
    after = len([i for i in p6.issues if i.rule and i.rule.startswith("MECH_")])
    check("re-running mechanical DRC replaces rather than accumulates", before == after,
          f"{before} -> {after}")

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


def _make_project(agent, placement):
    from app.models import Project
    p = Project(id="mdrc", name="mdrc", path="/mdrc")
    for _ in agent._run_stub(p, "esp32"):
        pass
    p.board = placement.auto_place(p)
    return p


if __name__ == "__main__":
    raise SystemExit(main())
