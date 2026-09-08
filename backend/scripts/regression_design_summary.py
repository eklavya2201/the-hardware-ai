"""Regression fixture for the IR-derived design summary (Phase 1).

Builds a project end-to-end, computes the design summary, and asserts its shape
and a few claim-provenance invariants. Hermetic by default: it forces the
deterministic stub agent (no OPENAI_API_KEY) so the result is reproducible in
CI. Pass --live to keep your real key and exercise the actual LLM agent with
Suyash's obstacle-avoiding-robot prompt (the permanent regression scenario).

Exit codes:
    0 — all assertions pass
    1 — an assertion failed

Usage:
    python -m scripts.regression_design_summary
    python -m scripts.regression_design_summary --live
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Suyash's reviewed scenario — kept verbatim as the permanent regression prompt.
ROBOT_PROMPT = (
    "ESP32-based obstacle avoiding robot with two ultrasonic sensors, two DC "
    "motors, a DRV8833 motor driver, battery power, charging, and voltage "
    "regulation."
)

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    if not ok:
        _failures.append(label)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the real LLM agent + robot prompt instead of the stub",
    )
    args = parser.parse_args()

    if not args.live:
        # Force the deterministic stub so the fixture is hermetic.
        os.environ.pop("OPENAI_API_KEY", None)

    from app.models import Project
    from app.services import agent, design_summary

    prompt = ROBOT_PROMPT if args.live else "low-power ESP32 sensor board"
    mode = "live (OpenAI)" if agent.has_api_key() else "stub (deterministic)"
    print(f"Design-summary regression — agent mode: {mode}")
    print(f"Prompt: {prompt}\n")

    project = Project(id="regression", name="regression", path="/regression")
    for _ in agent.run(project, prompt):
        pass

    summary = design_summary.build(project)

    print("Summary sections:")
    print(f"  components       : {len(summary.components)}")
    print(f"  pinMapping       : {len(summary.pinMapping)}")
    print(f"  powerArchitecture: {len(summary.powerArchitecture)}")
    print(f"  warnings         : {len(summary.warnings)}")
    print(f"  missingItems     : {len(summary.missingItems)}\n")

    print("Assertions:")
    # --- structural shape (holds in any mode) ---
    check("project has components", bool(project.ir.components),
          f"{len(project.ir.components)} placed")
    check("summary lists every placed component",
          len(summary.components) == len(project.ir.components))
    check("at least one power rail derived", len(summary.powerArchitecture) >= 1,
          ", ".join(r.rail for r in summary.powerArchitecture))
    check("a power rail has both a source and loads",
          any(r.sources and r.loads for r in summary.powerArchitecture))
    check("pin mapping derived from signal nets", len(summary.pinMapping) >= 1,
          f"{len(summary.pinMapping)} rows")
    check("warnings and missing items are disjoint lists",
          {id(i) for i in summary.warnings}.isdisjoint(
              {id(i) for i in summary.missingItems}))

    # --- claim provenance: every pin-map row is real on its net ---
    grounded = all(
        design_summary.net_contains(project, [(row.source.split(".")[0],
                                                row.source.split(".")[1]),
                                               (row.mcuRefdes, row.mcuPin)])
        for row in summary.pinMapping
    )
    check("every pin-map row exists on its net (claim provenance)", grounded)

    # --- net_contains rejects a connection that isn't there ---
    check("net_contains rejects a fabricated connection",
          not design_summary.net_contains(
              project, [("U1", "1"), ("__nope__", "999")]))

    # --- real-footprint layer + fab exports (Week-2 task 4.4) ---
    _check_fab_layer(project)

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: all assertions passed.")
    return 0


def _check_fab_layer(project) -> None:
    """Place + route the board, then assert the real-footprint / fab layer
    invariants (task 4.4): every placed part resolves to verified geometry, the
    manufacturability gate is clean, and — when KiCad is installed — the fab
    package and the industry DRC both produce output mapped correctly. KiCad
    steps degrade to a skip when KiCad is absent so the fixture stays hermetic."""
    from app.services import (
        export,
        kicad_bridge,
        kicad_drc,
        pcb_verifier,
        placement,
        routing,
    )

    project.board = placement.auto_place(project)
    project.board = routing.auto_route(project)
    check("board placed all components",
          len(project.board.components) == len(project.ir.components),
          f"{len(project.board.components)} placed")

    # Every placed component resolves to a verified real KiCad footprint, so the
    # manufacturability gate has zero blockers (0-blocking, from the committed
    # cache — no runtime KiCad needed).
    report = export.manufacturability_report(project)
    # The preview router may leave a rat for Finalize (FreeRouting) to close;
    # since 2026-09-06 that is an honest `routing` blocker, never ready:true.
    # Everything else (footprints, sync, R43, R57, schematic errors) must be
    # clean on the stub.
    kinds = {b["kind"] for b in report["blockers"]}
    if project.board.rats:
        check("unrouted preview board is NOT ready, and only routing blocks it",
              (not report["ready"]) and kinds == {"routing"},
              f"rats={len(project.board.rats)} blockers={sorted(kinds)}")
    else:
        check("manufacturability report is ready (0 export blockers)",
              report["ready"], f"{len(report['blockers'])} blocker(s)")
    check("no placed part is on generic footprint geometry",
          all(c["confidence"] != "generic" for c in report["components"]))

    # In-house PCB rules still populate alongside — sanity that they run.
    pcb_verifier.merge_into_project(project)
    check("PCB verifier rules run on the board",
          any((i.rule or "").startswith("PCB_") for i in project.issues)
          or not project.board.rats,  # a fully-clean board may emit none
          "PCB_* diagnostics present")

    if not kicad_bridge.cli_available():
        print("  [SKIP] KiCad not installed — skipping fab-package + KiCad-DRC checks")
        return

    # Fab package (task 4.1): a real zip with Gerbers + drill + pick-and-place.
    try:
        blob = export.fab_package(project)
        import io, zipfile
        names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
        has_gerber = any(n.endswith(".gbr") for n in names)
        has_drill = any(n.endswith(".drl") for n in names)
        has_pos = any("pos" in n.lower() for n in names)
        check("fab package contains Gerbers + drill + pick-and-place",
              has_gerber and has_drill and has_pos,
              f"{len(names)} files")
    except Exception as exc:  # noqa: BLE001
        check("fab package generated", False, str(exc)[:80])

    # KiCad industry DRC (task 4.2): maps into VerifierIssues without clobbering
    # the in-house rules.
    pre_inhouse = [i for i in project.issues
                   if not (i.rule or "").startswith("KICAD_")]
    error, count = kicad_drc.merge_into_project(project)
    kic = [i for i in project.issues if i.source == "kicad"]
    check("KiCad DRC ran and mapped violations", error is None,
          error or f"{count} issue(s)")
    check("KiCad DRC issues carry source='kicad' + KICAD_ rule",
          all(i.source == "kicad" and (i.rule or "").startswith("KICAD_")
              for i in kic))
    post_inhouse = [i for i in project.issues
                    if not (i.rule or "").startswith("KICAD_")]
    check("KiCad DRC runs ALONGSIDE the in-house rules (didn't replace them)",
          len(post_inhouse) == len(pre_inhouse))

    # Router shorts guard: the clearance-halo + copper-aware via guard must keep
    # the in-house router's output free of net-to-net shorts (a fabbed board with
    # a short is a hard defect now that we ship real Gerbers).
    shorts = [i for i in kic if (i.rule or "") == "KICAD_shorting_items"]
    check("in-house router output has zero net-to-net shorts",
          len(shorts) == 0, f"{len(shorts)} short(s)")


if __name__ == "__main__":
    raise SystemExit(main())
