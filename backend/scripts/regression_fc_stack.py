"""FPV flight-controller mounting-pattern regression.

A drone board mates with its frame through four screws on a published pitch
(30.5 / 25.5 / 20 / 16 mm square) — and unlike a HAT there is no header to lock,
so nothing physical holds the pattern in place. These checks assert the pattern
survives what the pipeline does to a board: it is exactly on pitch, centred,
sized for its screw, re-centred when the board grows, and REPORTED (MECH_R08)
when it drifts. The two connector standards must be untouched by all of it.

Exit codes: 0 pass, 1 an assertion failed. Offline, no LLM.

Usage: python -m scripts.regression_fc_stack
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import Project, SchematicIR  # noqa: E402
from app.services import form_factors, mechanical_verifier  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        fail += 1
        print(f"  [FAIL] {label}  ({detail})")


def fresh():
    return Project(id="t", name="t", path="t", kind="hardware", ir=SchematicIR())


print("FC stack form factors")
listed = {f["name"] for f in form_factors.listing()}
check("all four patterns are offered", {"fc_stack_30_5", "fc_stack_25_5",
                                        "fc_stack_20_0", "fc_stack_16_0"} <= listed,
      str(sorted(listed)))

# --- the full-size pattern, applied to an empty design ---------------------
p = fresh()
res, _ = form_factors.apply(p, "fc_stack_30_5")
holes = p.mechanical.mountingHoles
check("applies with no mating part", res["anchor"] is None)
check("board is the derived default", (p.board.width, p.board.height) == (36.0, 36.0),
      f"{p.board.width}x{p.board.height}")
check("four holes drilled", len(holes) == 4, str(len(holes)))
xs = sorted({round(h.x, 2) for h in holes})
ys = sorted({round(h.y, 2) for h in holes})
check("pitch is exactly 30.5 mm in both axes",
      round(xs[1] - xs[0], 3) == 30.5 and round(ys[1] - ys[0], 3) == 30.5,
      f"x {xs} y {ys}")
check("pattern is centred on the board",
      abs((xs[0] + xs[1]) / 2 - p.board.width / 2) < 1e-6
      and abs((ys[0] + ys[1]) / 2 - p.board.height / 2) < 1e-6)
check("holes take an M3 screw", all(h.diameter >= 3.2 for h in holes),
      str({h.diameter for h in holes}))
check("the design records the PATTERN, not an outline",
      p.ir.form_factor.width_mm is None and "mounting pattern" in p.ir.form_factor.note,
      p.ir.form_factor.note)
issues = mechanical_verifier.run(p)
r08 = [i for i in issues if i.rule and i.rule.startswith("MECH_R08")]
check("a correctly applied pattern reports no R08 defect", not r08,
      "; ".join(i.message[:70] for i in r08))

# --- the failure the rule exists for: the board grows, holes stay put ------
p2 = fresh()
form_factors.apply(p2, "fc_stack_30_5")
p2.board.width = 46.0          # what an auto-place growth does
p2.board.height = 46.0
grown = [i for i in mechanical_verifier.run(p2)
         if i.rule and i.rule.startswith("MECH_R08")]
check("a board that grew off its pattern is REPORTED", bool(grown),
      grown[0].message[:90] if grown else "silent")
check("and it is an error, not a warning",
      bool(grown) and grown[0].severity == "error")

# --- re-applying fixes it -------------------------------------------------
form_factors.apply(p2, "fc_stack_30_5")
fixed = [i for i in mechanical_verifier.run(p2)
         if i.rule and i.rule.startswith("MECH_R08")]
xs2 = sorted({round(h.x, 2) for h in p2.mechanical.mountingHoles})
check("re-applying re-centres the pattern on the grown board", not fixed,
      f"x {xs2} on {p2.board.width} mm")
check("and it kept the larger board rather than shrinking it",
      p2.board.width == 46.0, f"{p2.board.width}")

# --- a small pattern uses M2 ----------------------------------------------
p3 = fresh()
form_factors.apply(p3, "fc_stack_20_0")
h3 = p3.mechanical.mountingHoles
xs3 = sorted({round(h.x, 2) for h in h3})
check("20x20 pattern is 20 mm and M2", round(xs3[1] - xs3[0], 3) == 20.0
      and all(abs(h.diameter - 2.2) < 1e-6 for h in h3),
      f"pitch {round(xs3[1]-xs3[0],3)} dia {h3[0].diameter}")

# --- the connector standards must be untouched ----------------------------
p4 = fresh()
res4, _ = form_factors.apply(p4, "rpi_hat")
hat_holes = sorted((round(h.x, 2), round(h.y, 2)) for h in p4.mechanical.mountingHoles)
check("the HAT still locks its header", res4["anchor"] is not None
      and res4["anchor"]["locked"])
check("the HAT keeps its ABSOLUTE spec holes",
      hat_holes == [(3.5, 3.5), (3.5, 52.5), (61.5, 3.5), (61.5, 52.5)], str(hat_holes))
check("the HAT still records its spec outline",
      p4.ir.form_factor.width_mm == 65.0 and p4.ir.form_factor.height_mm == 56.5)
check("no R08 on a connector standard",
      not [i for i in mechanical_verifier.run(p4)
           if i.rule and i.rule.startswith("MECH_R08")])

print(f"\nRESULT: {ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
