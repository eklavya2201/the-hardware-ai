"""Live-path evaluation harness — a quantitative trust metric for the LLM agent.

The stub agent is deterministic and always trustworthy; the *live* OpenAI/Azure
agent's output quality VARIES, and nothing measured it. This harness runs the
live agent over a fixed set of reference prompts and scores each generated design
on dimensions that proxy correctness:

  structural   — has components, a power rail, and a derived pin mapping
  expected     — the part categories the prompt implies are present
  drc          — 0 blocking DRC errors after the agent's own fix-up loop
  correctness  — no sizing/ERC hard errors (R31 undersized reg, R34 I²C clash,
                 R24 short/contention)
  grounded     — the final chat message makes no ungrounded connection claim

It prints a per-prompt scorecard and an aggregate 0–1 score — the number to watch
when changing the model or prompt, and to catch regressions the stub can't.

Requires a live key (OPENAI_API_KEY / Azure env). Without one it exits with a
note. `--stub` forces the deterministic agent to smoke-test the harness itself
(only the ESP32-sensor prompt will score well — the stub ignores the others).

Usage:
    python -m scripts.eval_live
    python -m scripts.eval_live --stub        # test the harness mechanics
    python -m scripts.eval_live --prompt 2    # run one prompt by index
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Reference prompts + the part categories each implies. Kept small and stable so
# scores are comparable across runs. `expect` = categories the design should
# contain; `needs_regulation` etc. are structural expectations.
REFERENCE_PROMPTS = [
    {
        "name": "esp32-sensor",
        "prompt": "low-power ESP32 sensor board",
        "expect": {"mcu", "regulator", "sensor"},
    },
    {
        "name": "obstacle-robot",
        "prompt": (
            "ESP32-based obstacle avoiding robot with two ultrasonic sensors, two "
            "DC motors, a DRV8833 motor driver, battery power, charging, and "
            "voltage regulation."
        ),
        "expect": {"mcu", "actuator", "sensor", "regulator"},
    },
    {
        "name": "usb-logger",
        "prompt": "USB-C powered temperature and humidity logger with an SD card slot",
        "expect": {"mcu", "sensor", "connector"},
    },
    {
        "name": "buck-supply",
        "prompt": "A 12V to 5V switching buck regulator board rated for 2A output",
        "expect": {"regulator"},
    },
]


def _categories(project) -> set[str]:
    from app.services import library as lib
    cats: set[str] = set()
    for c in project.ir.components:
        p = lib.get_part(c.mpn)
        if p:
            cats.add(p.category)
    return cats


def score_project(project, expect: set[str]) -> dict:
    """Score a generated design 0–1 across dimensions. Pure — reads project only."""
    from app.services import design_summary, verifier

    summary = design_summary.build(project)
    issues = verifier.run_all(project)

    # structural
    structural = sum([
        len(project.ir.components) > 0,
        len(summary.powerArchitecture) >= 1,
        len(summary.pinMapping) >= 1,
    ]) / 3.0

    # expected part categories present
    cats = _categories(project)
    expected = (len(expect & cats) / len(expect)) if expect else 1.0

    # DRC health (blocking errors, excluding advisory/LLM/PCB)
    errors = [
        i for i in issues
        if i.severity == "error" and i.rule
        and not i.rule.startswith(("LLM_", "PCB_"))
    ]
    drc = 1.0 if not errors else max(0.0, 1.0 - 0.2 * len(errors))

    # correctness — sizing/ERC hard errors specifically
    hard = [i for i in errors if (i.rule or "")[:3] in
            ("R31", "R32", "R34", "R24", "R11", "R21")]
    correctness = 1.0 if not hard else 0.0

    # grounded — the design_summary is IR-derived; a proxy is that no warning is
    # a claim-validator downgrade (source="llm" in missingItems).
    ungrounded = [i for i in summary.missingItems if getattr(i, "source", "") == "llm"]
    grounded = 1.0 if not ungrounded else 0.5

    overall = (
        0.20 * structural + 0.25 * expected + 0.25 * drc
        + 0.20 * correctness + 0.10 * grounded
    )
    return {
        "structural": structural, "expected": expected, "drc": drc,
        "correctness": correctness, "grounded": grounded, "overall": overall,
        "components": len(project.ir.components), "errors": len(errors),
        "missing_cats": sorted(expect - cats),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stub", action="store_true", help="force the deterministic stub")
    ap.add_argument("--prompt", type=int, default=None, help="run one prompt by index (0-based)")
    args = ap.parse_args()

    if args.stub:
        os.environ.pop("OPENAI_API_KEY", None)

    from app.models import Project
    from app.services import agent

    mode = "live (OpenAI/Azure)" if agent.has_api_key() else "stub (deterministic)"
    if not agent.has_api_key() and not args.stub:
        print("No live key (OPENAI_API_KEY / Azure env) — set one to evaluate the "
              "live agent, or pass --stub to smoke-test the harness.")
        return 0

    prompts = REFERENCE_PROMPTS
    if args.prompt is not None:
        prompts = [REFERENCE_PROMPTS[args.prompt]]

    print(f"Live-path eval — agent mode: {mode}")
    print(f"{'prompt':<16} {'struct':>6} {'expect':>6} {'drc':>5} {'correct':>7} "
          f"{'ground':>6} {'OVERALL':>7}  notes")
    print("-" * 78)

    scores = []
    for spec in prompts:
        project = Project(id=f"eval-{spec['name']}", name=spec["name"], path=f"/{spec['name']}")
        try:
            for _ in agent.run(project, spec["prompt"]):
                pass
            s = score_project(project, spec["expect"])
        except Exception as exc:  # noqa: BLE001
            print(f"{spec['name']:<16} ERROR: {str(exc)[:50]}")
            scores.append(0.0)
            continue
        scores.append(s["overall"])
        note = f"{s['components']} parts, {s['errors']} err"
        if s["missing_cats"]:
            note += f", missing {s['missing_cats']}"
        print(f"{spec['name']:<16} {s['structural']:>6.2f} {s['expected']:>6.2f} "
              f"{s['drc']:>5.2f} {s['correctness']:>7.2f} {s['grounded']:>6.2f} "
              f"{s['overall']:>7.2f}  {note}")

    print("-" * 78)
    agg = sum(scores) / len(scores) if scores else 0.0
    print(f"AGGREGATE TRUST SCORE: {agg:.2f} / 1.00  ({len(scores)} prompts, {mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
