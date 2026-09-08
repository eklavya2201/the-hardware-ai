"""Guard for the Prompt-to-Product launch round (2026-09-06). Offline, stub
agent, no LLM spend.

What the loop check found and this pins down:
  * R81 — two controllers on one board when the request asked for one.
  * R57 must be evaluated as soon as a board exists (the auto-place path
    refreshes it), and an oversize board is a readiness blocker.
  * A board with airwires is never ready:true; the blocker names Finalize.
  * A remaining schematic error is a readiness blocker too.

Run from backend/:  python -m scripts.regression_prompt_to_product
"""
from __future__ import annotations

import os
import sys

os.environ["OPENAI_API_KEY"] = ""


def main() -> int:
    from app.models import DesignRequirement, FormFactorSpec, Project
    from app.services import agent, export, placement, routing, tools, verifier

    checks: list[tuple[bool, str]] = []

    def check(ok: bool, label: str) -> None:
        checks.append((bool(ok), label))

    # --- R81: the stub (one ESP32) is silent; a second module speaks
    p = Project(id="p2p", name="p2p", path="/tmp/p2p")
    for _ in agent.run(p, "ESP32 sensor board"):
        pass
    check(not verifier.R81_duplicate_controller(p), "one controller: R81 silent")
    mcu = next(c for c in p.ir.components if c.mpn.startswith("ESP32"))
    tools.dispatch(p, "add_component", {"mpn": "ESP32-C3-MINI-1", "refdes": "U9"})
    r81 = verifier.R81_duplicate_controller(p)
    check(len(r81) == 1 and r81[0].severity == "warning", "second controller nobody asked for: one R81 warning")
    check("U9" in r81[0].message and mcu.refdes in r81[0].message, "R81 names both controllers")
    p.ir.requirements.append(DesignRequirement(key="mcu-x2", description="two ESP32 modules", quantity=2, category="mcu"))
    check(not verifier.R81_duplicate_controller(p), "a requirement asking for two controllers silences R81")
    tools.dispatch(p, "delete_component", {"refdes": "U9"})
    p.ir.requirements = [r for r in p.ir.requirements if r.category != "mcu"]

    # --- readiness: schematic clean + placed + routed with rats => routing blocker only
    p.board = placement.auto_place(p)
    p.board = routing.auto_route(p)
    report = export.manufacturability_report(p)
    kinds = [b["kind"] for b in report["blockers"]]
    if p.board.rats:
        check(not report["ready"] and kinds == ["routing"], f"airwires => not ready, routing is the only blocker ({kinds})")
        check("Finalize" in report["blockers"][0]["reason"], "routing blocker tells the user to Finalize")
        check(export.export_blockers(report), "an unrouted board also blocks the Gerber export (force still allowed)")
    else:
        check(report["ready"], "fully routed stub is ready")
    p.board.rats = []  # pretend Finalize closed them
    check(export.manufacturability_report(p)["ready"], "with every net routed the stub is ready")

    # --- R57 after placement: ask for a disc far too small for the parts
    p.ir.form_factor = FormFactorSpec(shape="round", diameter_mm=20)
    p.board = placement.auto_place(p)
    fresh = verifier.R57_form_factor(p)
    check(any(i.severity == "error" for i in fresh), "Ø20 mm ask on an ESP32 board: R57 error after placement")
    p.board.rats = []
    report = export.manufacturability_report(p)
    check(not report["ready"] and "form_factor" in [b["kind"] for b in report["blockers"]],
          "oversize board is a readiness blocker (form_factor)")
    check(not any(b["kind"] == "form_factor" and b["blocksExport"] for b in report["blockers"]),
          "form-factor blocker does not stop a Gerber download")
    p.ir.form_factor = None

    # --- a remaining schematic error blocks readiness
    p.board.rats = []
    tools.dispatch(p, "add_component", {"mpn": "ESP32-C3-MINI-1", "refdes": "U9"})
    # Short two GPIO outputs of the new module together: an ERC hard error (R44/R24).
    try:
        tools.dispatch(p, "connect", {"net_name": "SHORT_TEST", "pins": [["U9", "IO4"], ["U9", "IO5"]]})
    except Exception:  # noqa: BLE001 — the guard below tolerates either outcome
        pass
    tools.dispatch(p, "run_drc", {})
    errors = [i for i in p.issues if i.severity == "error"]
    report = export.manufacturability_report(p)
    if errors:
        check("drc" in [b["kind"] for b in report["blockers"]] or not report["ready"],
              f"{len(errors)} schematic error(s) => not ready")
    else:
        check(True, "no error could be provoked on the stub (skipped)")

    failures = [label for ok, label in checks if not ok]
    for ok, label in checks:
        print(("PASS " if ok else "FAIL ") + label)
    print(f"\n{len(checks) - len(failures)}/{len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
