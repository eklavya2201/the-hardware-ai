"""IDF ECAD↔MCAD bridge regression — Week-4 ECAD task 6.3.

Asserts:
  * export produces a well-formed IDF 3.0 .emn/.emp pair (headers, sections),
  * a round-trip (export → import into a fresh project) reproduces the mechanical
    features (outline, mounting holes, keep-outs, connector cutouts),
  * the ARCHITECTURAL CONTRACT holds on import:
      - a component MOVED in the .emn is reported but NOT written to the Board,
      - a connector cutout for an UNPLACED refdes is rejected (anti-hallucination),
      - only mechanical_tools are used (electrical IR / Board untouched).

Exit codes: 0 pass · 1 an assertion failed.
Usage: python -m scripts.regression_idf
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


def _project(agent, placement):
    from app.models import Project
    p = Project(id="idf", name="idf_demo", path="/idf")
    for _ in agent._run_stub(p, "esp32"):
        pass
    p.board = placement.auto_place(p)
    return p


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.services import agent, idf_bridge, mechanical_tools as mt, placement

    print("IDF ECAD<->MCAD bridge regression (6.3)\n")

    src = _project(agent, placement)
    mt.dispatch(src, "derive_from_board", {})
    n_holes = len(src.mechanical.mountingHoles)
    n_cuts = len(src.mechanical.cutouts)
    mt.dispatch(src, "add_keepout",
                {"x": 20, "y": 20, "width": 8, "height": 6, "reason": "antenna"})

    # --- export shape ---
    emn, emp = idf_bridge.export_idf(src)
    check("emn has an IDF 3.0 BOARD_FILE header", "BOARD_FILE 3.0" in emn)
    check("emn declares MM units", '" MM' in emn or " MM" in emn)
    for sec in (".BOARD_OUTLINE", ".DRILLED_HOLES", ".COMPONENT_PLACEMENT",
                ".PLACE_KEEPOUT", ".OTHER_OUTLINE"):
        check(f"emn contains {sec}", sec in emn)
    check("emp has a LIBRARY_FILE header + ELECTRICAL geometry",
          "LIBRARY_FILE 3.0" in emp and ".ELECTRICAL" in emp)
    check("emp carries a real body height (not 0)",
          any(t not in ("0.0000", "0") for t in _electrical_heights(emp)),
          str(_electrical_heights(emp)[:4]))

    # --- round-trip into a FRESH project (no mechanical IR yet) ---
    dst = _project(agent, placement)
    res = idf_bridge.import_idf(dst, emn)
    check("round-trip updates the outline", res.outline_updated)
    check("round-trip re-creates the mounting holes",
          len(dst.mechanical.mountingHoles) == n_holes,
          f"{len(dst.mechanical.mountingHoles)} vs {n_holes}")
    check("round-trip re-creates the keep-out", len(dst.mechanical.keepouts) == 1)
    check("round-trip re-creates the connector cutout(s)",
          len(dst.mechanical.cutouts) == n_cuts, f"{len(dst.mechanical.cutouts)} vs {n_cuts}")
    check("re-imported cutout keeps its refdes",
          all(c.refdes for c in dst.mechanical.cutouts) if dst.mechanical.cutouts else True)

    # --- CONTRACT: a moved component is reported, NOT applied to the Board ---
    dst2 = _project(agent, placement)
    ref = dst2.board.components[0].refdes
    moved_emn = _move_component_in_emn(emn, ref, dx=25.0)
    before = {c.refdes: (c.x, c.y) for c in dst2.board.components}
    res2 = idf_bridge.import_idf(dst2, moved_emn)
    after = {c.refdes: (c.x, c.y) for c in dst2.board.components}
    check("component move is NOT written to the Board (IR untouched)", before == after)
    check("component move is surfaced as a warning",
          any(ref in w and "not applied" in w for w in res2.warnings),
          "; ".join(res2.warnings[:2]) or "no warnings")

    # --- CONTRACT: a cutout for an UNPLACED refdes is rejected on import ---
    dst3 = _project(agent, placement)
    bogus = _inject_bogus_cutout(emn, refdes="J404")
    res3 = idf_bridge.import_idf(dst3, bogus)
    check("cutout for an unplaced connector is rejected (anti-hallucination)",
          any("J404" in w and "reject" in w.lower() for w in res3.warnings),
          "; ".join(w for w in res3.warnings if "J404" in w) or "not rejected")
    check("the rejected cutout did NOT land in the IR",
          all(c.refdes != "J404" for c in dst3.mechanical.cutouts))

    # --- malformed input degrades gracefully (never raises) ---
    res4 = idf_bridge.import_idf(_project(agent, placement), "garbage not idf")
    check("malformed IDF import degrades gracefully", isinstance(res4.warnings, list))

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


def _electrical_heights(emp: str) -> list[str]:
    """The 4th token of each `"geom" "part" MM <height>` record."""
    out = []
    for ln in emp.splitlines():
        toks = ln.split()
        if len(toks) >= 4 and toks[-2] == "MM":
            out.append(toks[-1])
    return out


def _move_component_in_emn(emn: str, refdes: str, dx: float) -> str:
    """Shift the given component's X in its COMPONENT_PLACEMENT data record."""
    lines = emn.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().endswith(f" {refdes}") and i + 1 < len(lines):
            toks = lines[i + 1].split()
            if len(toks) >= 2:
                toks[0] = f"{float(toks[0]) + dx:.4f}"
                lines[i + 1] = " ".join(toks)
                break
    return "\n".join(lines) + "\n"


def _inject_bogus_cutout(emn: str, refdes: str) -> str:
    """Append an OTHER_OUTLINE cutout tagged with an unplaced refdes."""
    block = (
        f'.OTHER_OUTLINE ECAD\n"CUTOUT_{refdes}_none" 4.0000 TOP\n'
        "0 5.0000 5.0000 0.0\n0 15.0000 5.0000 0.0\n0 15.0000 12.0000 0.0\n"
        "0 5.0000 12.0000 0.0\n0 5.0000 5.0000 0.0\n.END_OTHER_OUTLINE\n"
    )
    return emn + block


if __name__ == "__main__":
    raise SystemExit(main())
