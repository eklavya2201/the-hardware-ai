"""Reference-design template regression — engineering-rules Phase 5 (compose).

Verifies that instantiating a vetted block produces the exact validated topology
through the real add_component/connect tools (so all guards apply), and that its
parts are all real library MPNs.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_templates
"""

from __future__ import annotations

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
    from app.models import Project
    from app.services import templates, tools

    print("Reference-design template regression\n")

    # Every template's parts must be real library MPNs (no hallucinated parts).
    for t in templates.TEMPLATES.values():
        ok = all(templates.part_exists(p.mpn) for p in t.parts)
        check(f"template '{t.name}' uses only real library parts", ok)

    # Instantiate the LM7805 block and verify the exact topology.
    p = Project(id="t", name="t", path="/t")
    res, _ = tools.dispatch(p, "apply_template", {
        "template_name": "lm7805_5v_linear",
        "net_map": {"VIN": "12V", "VOUT": "5V", "GND": "GND"},
    })
    check("LM7805 block added 3 parts (reg + 2 caps)",
          len(p.ir.components) == 3, str([c.mpn for c in p.ir.components]))

    def net(name):
        n = next((x for x in p.ir.nets if x.name == name), None)
        return {(r, pn) for r, pn in n.pins} if n else set()

    reg = res["created"]["reg"]
    check("regulator INPUT on VIN (12V)", (reg, "1") in net("12V"))
    check("regulator OUTPUT on VOUT (5V)", (reg, "3") in net("5V"))
    check("regulator GND on GND", (reg, "2") in net("GND"))
    check("both caps straddle their rail and GND",
          len(net("GND")) == 3 and len(net("12V")) == 2 and len(net("5V")) == 2)

    # net_map placeholders were substituted (no leftover VIN/VOUT nets).
    check("placeholder nets were mapped to real nets",
          not any(x.name in ("VIN", "VOUT") for x in p.ir.nets))

    # Unknown template fails loudly.
    try:
        tools.dispatch(p, "apply_template", {"template_name": "does_not_exist"})
        check("unknown template raises", False)
    except Exception:
        check("unknown template raises", True)

    # --- Mechanical templates (Week-4 6.4) ---------------------------------- #
    _check_mechanical_templates(check)

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


def _check_mechanical_templates(check) -> None:
    """Each mechanical block, applied to the stub board through the tool dispatch,
    must produce a mechanically-valid result (0 MECH_ errors) — the 6.4 acceptance
    — and go only through mechanical_tools (mechanical IR mutated, Board untouched)."""
    import os
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Project
    from app.services import agent, mechanical_verifier as mv, placement, templates, tools

    expect = {
        "enclosure_generic_box": ("box", 0),
        "enclosure_snap_fit": ("snap_fit", 0),
        "mount_din_rail": ("box", 1),          # + the 35 mm DIN channel keep-out
        "mount_robot_chassis": ("none", 0),    # open standoff mount, no walls
    }
    for name, (kind, n_keepouts) in expect.items():
        p = Project(id="mt", name="mt", path="/mt")
        for _ in agent._run_stub(p, "esp32"):
            pass
        p.board = placement.auto_place(p)
        board_before = [(c.refdes, c.x, c.y) for c in p.board.components]
        tools.dispatch(p, "apply_template", {"template_name": name})

        enc = p.mechanical.enclosure
        check(f"'{name}' sets enclosure kind={kind}", enc.kind == kind, enc.kind)
        check(f"'{name}' places 4 mounting holes", len(p.mechanical.mountingHoles) == 4,
              str(len(p.mechanical.mountingHoles)))
        check(f"'{name}' adds {n_keepouts} keep-out(s)",
              len(p.mechanical.keepouts) == n_keepouts, str(len(p.mechanical.keepouts)))
        errs = [i.rule for i in mv.run(p) if i.severity == "error"]
        check(f"'{name}' passes mechanical DRC (0 errors)", not errs, str(errs))
        board_after = [(c.refdes, c.x, c.y) for c in p.board.components]
        check(f"'{name}' does not touch the electrical Board", board_before == board_after)

    check("mount_din_rail keep-out is the 35 mm DIN channel",
          _din_rail_keepout_is_35mm(agent, placement, templates, tools))
    # Mechanical templates appear in the agent-facing listing.
    names = {t["name"] for t in templates.list_templates()}
    check("mechanical templates are listed for the agent",
          expect.keys() <= names, str(sorted(expect.keys() - names)))


def _din_rail_keepout_is_35mm(agent, placement, templates, tools) -> bool:
    import os
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Project
    p = Project(id="dr", name="dr", path="/dr")
    for _ in agent._run_stub(p, "esp32"):
        pass
    p.board = placement.auto_place(p)
    tools.dispatch(p, "apply_template", {"template_name": "mount_din_rail"})
    ko = p.mechanical.keepouts[0] if p.mechanical.keepouts else None
    return ko is not None and abs(ko.height - min(35.0, p.board.height)) < 0.01


if __name__ == "__main__":
    raise SystemExit(main())
