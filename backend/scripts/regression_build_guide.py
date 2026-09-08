"""Guard for the build guide (Prompt to Product, 2026-09). Offline: the
deterministic stub design, no LLM spend.

Run from backend/:  python -m scripts.regression_build_guide
"""
from __future__ import annotations

import os
import sys

os.environ["OPENAI_API_KEY"] = ""  # force the stub agent + skeleton-only guide


def main() -> int:
    from app.models import Project
    from app.services import agent, build_guide, mechanical_tools, placement, routing, tools

    project = Project(id="bg-guard", name="bg-guard", path="/tmp/bg-guard")
    for _ in agent.run(project, "ESP32 sensor board"):
        pass
    project.bom = tools.recompute_bom(project)
    project.board = placement.auto_place(project)
    project.board = routing.auto_route(project)
    mechanical_tools.dispatch(project, "derive_from_board", {})

    g = build_guide.skeleton(project)
    refs = {c.refdes for c in project.ir.components}
    step_refs = [r for s in g.steps for r in s.refdes]
    titles = [s.title for s in g.steps]
    order = {t: i for i, t in enumerate(titles)}

    def before(a: str, b: str) -> bool:
        ia = [i for t, i in order.items() if t.startswith(a)]
        ib = [i for t, i in order.items() if t.startswith(b)]
        return bool(ia and ib) and ia[0] < ib[0]

    md = build_guide.to_markdown(Project(**{**project.model_dump(), "buildGuide": g}))

    checks = [
        (len(g.steps) >= 5, f"{len(g.steps)} steps (>= 5)"),
        (all(s.n == i + 1 for i, s in enumerate(g.steps)), "steps numbered 1..n"),
        (set(step_refs) <= refs, "every refdes in a step exists in the design"),
        (len(set(step_refs)) == len(refs), f"every part appears in exactly one step ({len(set(step_refs))}/{len(refs)})"),
        (len(step_refs) == len(set(step_refs)), "no part is assigned to two steps"),
        (titles[0].startswith("Inspect"), "first step inspects the bare board"),
        (all(before("Populate the power stage", t) for t in titles
             if t.startswith(("Small SMD", "Fine-pitch", "Remaining SMD", "Through-hole", "Connectors", "Clean"))),
         "power stage goes down before the rest"),
        (before("Power-up test #1", "Connectors"), "rails are tested before connectors/modules"),
        (before("Small SMD", "Through-hole") if "Through-hole parts, shortest first" in titles else True,
         "SMD before through-hole"),
        (titles[-1].startswith("Power-up test #2"), "last step is the full power-up"),
        (any("VCC_3V3" in c or "3.3" in c for c in g.powerUpChecks), "power-up check names the 3.3 V rail"),
        (any("Soldering iron" in t for t in g.tools), "tools include a soldering iron"),
        (any("Multimeter" in t for t in g.tools), "tools include a multimeter"),
        (len(g.partsToOrder) == len(project.bom), "one parts-to-order line per BOM line"),
        (any("pin 1" in x for x in g.gotchas), "gotchas warn about pin-1 orientation on an IC"),
        (any("mounting holes" in x for x in g.enclosure), "enclosure section lists the mounting holes"),
        (any("Cutout" in x for x in g.enclosure), "enclosure section lists a connector cutout"),
        (g.model_used is None, "no key -> deterministic skeleton, model_used is None"),
        ("## Assembly" in md and "**Check:**" in md, "markdown export renders steps and checks"),
    ]

    # The prose gate: a model reply that names a part the design lacks is dropped.
    g2 = build_guide._apply_prose(
        build_guide.skeleton(project),
        {"summary": "ok", "steps": [{"n": 1, "body": "Solder U99 first."}, {"n": 2, "body": "Now the power stage."}]},
        refs,
    )
    checks.append((g2.steps[0].body == g.steps[0].body, "prose naming an unknown refdes (U99) is rejected"))
    checks.append((g2.steps[1].body == "Now the power stage.", "prose without invented parts is accepted"))

    failures = [label for ok, label in checks if not ok]
    for ok, label in checks:
        print(("PASS " if ok else "FAIL ") + label)
    print(f"\n{len(checks) - len(failures)}/{len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
