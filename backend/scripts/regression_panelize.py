"""Guard: panelization (2026-08-20).

Fabs build and ship PANELS, not single boards; an assembly house needs a panel
(or at least breakaway rails) to hold a small board in the pick-and-place
machine at all; and small boards are QUOTED per panel, so ordering one-up pays
for mostly empty laminate. Synqit had nothing to say about any of it — two
review rounds asked, and one reviewer panelized by hand and wrote it up as a
workaround.

The checks below are mostly about the two separation methods NOT being
interchangeable. V-scoring drags a blade the full width of the panel in one
straight pass; mouse bites route each board free and hold it on drilled tabs.
Offering the wrong one produces a panel a fab has to phone you about.

Offline; no LLM spend, no network.
"""

from __future__ import annotations

import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.models import PanelSpec  # noqa: E402
from app.models_db import ProjectRow  # noqa: E402
from app.services import (  # noqa: E402
    gerber, mechanical_tools, mechanical_verifier, panelize, placement, store, tools,
)

PASSED = 0
FAILED: list[str] = []
_SCRATCH: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(label)
        print(f"  [FAIL] {label}" + (f"  ({detail})" if detail else ""))


def a_board(name: str = "panel regression"):
    p = store.create_project(name, user_id="regression-panel")
    _SCRATCH.append(p.id)
    for i, mpn in enumerate(["RC0603FR-0710KL", "CL10A106MQ8NNNC", "LED_3MM_R"]):
        tools.add_component(p, mpn=mpn, refdes=f"R{i + 1}")
    p.board = placement.auto_place(p)
    return p


def cleanup() -> None:
    with db.SessionLocal() as s:
        for pid in _SCRATCH:
            row = s.get(ProjectRow, pid)
            if row is not None:
                s.delete(row)
        s.commit()


def test_geometry() -> None:
    print("\n-- derived panel geometry")
    p = a_board()
    board = p.board
    spec = PanelSpec(rows=3, cols=2, method="v_score", rail=5.0)
    panel = panelize.derive(board, spec)

    check("board count is rows x cols", panel.boards == 6, str(panel.boards))
    check("panel size is rails + boards (V-score leaves no gap)",
          abs(panel.width - (10 + 2 * board.width)) < 1e-6
          and abs(panel.height - (10 + 3 * board.height)) < 1e-6,
          f"{panel.width:.2f} x {panel.height:.2f}")
    check("one origin per board, none overlapping",
          len(panel.origins) == 6 and len(set(panel.origins)) == 6)
    check("every board sits inside the panel",
          all(0 <= ox and ox + board.width <= panel.width + 1e-6
              and 0 <= oy and oy + board.height <= panel.height + 1e-6
              for ox, oy in panel.origins))
    check("rails hold four tooling holes and three fiducials",
          len(panel.tooling_holes) == 4 and len(panel.fiducials) == 3)
    check("the third fiducial is asymmetric (that is how orientation is read)",
          len({(round(x, 2), round(y, 2)) for x, y in panel.fiducials}) == 3)

    # A V-score is a straight pass across the WHOLE panel — that is the physical
    # constraint the whole method hangs on.
    check("every score line spans the full panel",
          all(abs(x1 - x2) < 1e-6 and abs(y1) < 1e-6 and abs(y2 - panel.height) < 1e-6
              or abs(y1 - y2) < 1e-6 and abs(x1) < 1e-6 and abs(x2 - panel.width) < 1e-6
              for x1, y1, x2, y2 in panel.v_scores),
          f"{len(panel.v_scores)} lines")
    check("V-scored panels have no tabs", not panel.tabs)

    bites = panelize.derive(board, PanelSpec(rows=2, cols=2, method="mouse_bites", rail=5.0))
    check("mouse-bitten panels have tabs, not score lines",
          bites.tabs and not bites.v_scores, f"{len(bites.tabs)} tabs")
    check("eight tabs per board — two per edge",
          len(bites.tabs) == 8 * bites.boards, str(len(bites.tabs)))
    check("a mouse-bitten panel forces a router slot between boards",
          bites.gap >= panelize.MIN_ROUTE_GAP, f"{bites.gap} mm")
    check("every tab carries a perforation row",
          all(len(t.holes) >= 3 for t in bites.tabs))


def test_suggestion_is_for_the_person_ordering() -> None:
    print("\n-- what we suggest")
    p = a_board("panel suggest")
    board = p.board
    sug = panelize.suggest(board)
    check("a board smaller than the cheap tier gets a suggestion", sug is not None)
    if sug:
        panel = panelize.derive(board, sug)
        check("the suggestion fits the fab's flat-rate 100 x 100 square",
              panel.width <= panelize.CHEAP_TIER and panel.height <= panelize.CHEAP_TIER,
              f"{panel.width:.0f} x {panel.height:.0f}")
        check("and it is more than one board", panel.boards > 1, str(panel.boards))

    biggest = panelize.best_fit(board)
    check("best_fit is for volume and is at least as large as the suggestion",
          biggest.rows * biggest.cols >= (sug.rows * sug.cols if sug else 1))
    big_panel = panelize.derive(board, biggest)
    check("best_fit still fits a real fab frame",
          big_panel.width <= panelize.MAX_PANEL_W and big_panel.height <= panelize.MAX_PANEL_H,
          f"{big_panel.width:.0f} x {big_panel.height:.0f}")

    # A board that already fills a panel gains nothing from arraying — saying so
    # is more honest than proposing a 1x1 "panel".
    p.board.width, p.board.height = 300.0, 400.0
    check("a board that already fills a panel gets no suggestion",
          panelize.suggest(p.board) is None)


def test_rules_refuse_impossible_panels() -> None:
    print("\n-- MECH_R06 refuses what a fab cannot build")
    check("MECH_R06 is registered",
          mechanical_verifier.MECH_R06_panel in mechanical_verifier.ALL_RULES)

    p = a_board("panel rules")
    mechanical_tools.derive_panel(p)
    check("the default derived panel is clean",
          not mechanical_verifier.MECH_R06_panel(p),
          "; ".join(i.message[:60] for i in mechanical_verifier.MECH_R06_panel(p)))

    # A round board cannot be V-scored, at the tool AND at the rule.
    p.board.shape = "round"
    try:
        mechanical_tools.set_panel(p, rows=2, cols=2, method="v_score")
        check("the tool refuses to V-score a round board", False, "it was accepted")
    except Exception as exc:  # noqa: BLE001
        check("the tool refuses to V-score a round board", True)
        check("and it names the physical reason and the alternative",
              "straight" in str(exc) and "mouse_bites" in str(exc), str(exc)[:70])
    p.mechanical.panel = PanelSpec(rows=2, cols=2, method="v_score", rail=5.0)
    found = mechanical_verifier.MECH_R06_panel(p)
    check("the rule catches it too, if it is set some other way",
          any(i.id.endswith("shape") and i.severity == "error" for i in found))
    p.board.shape = "rect"

    # Too big for the frame.
    try:
        mechanical_tools.set_panel(p, rows=20, cols=20)
        check("an oversized array is refused", False, "it was accepted")
    except Exception as exc:  # noqa: BLE001
        check("an oversized array is refused", True)
        check("and it says how big the panel would have been",
              "mm panel" in str(exc), str(exc)[:70])

    # A rail too narrow to hold the hardware it exists for.
    mechanical_tools.set_panel(p, rows=2, cols=1, method="v_score", rail=2.0)
    check("a rail too narrow for its tooling holes is a warning",
          any(i.id.endswith("rail") for i in mechanical_verifier.MECH_R06_panel(p)))

    # A part on the score line: the board flexes when it is snapped.
    mechanical_tools.set_panel(p, rows=2, cols=1, method="v_score", rail=5.0)
    edge_part = p.board.components[0]
    edge_part.x, edge_part.y = 0.2, 0.2
    found = mechanical_verifier.MECH_R06_panel(p)
    check("a component on the V-score line is an error",
          any(i.id.endswith("keepout") and i.severity == "error" for i in found),
          "; ".join(i.id for i in found))


def test_panel_cam_package() -> None:
    print("\n-- the CAM package a fab receives")
    p = a_board("panel cam")
    for method in ("v_score", "mouse_bites"):
        spec = PanelSpec(rows=2, cols=2, method=method, rail=5.0)
        panel = panelize.derive(p.board, spec)
        zf = zipfile.ZipFile(io.BytesIO(gerber.panel_zip(p, spec)))
        names = {n.split(".")[-1] for n in zf.namelist()}
        check(f"{method}: every copper/mask/silk/outline/drill layer is present",
              {"GTL", "GBL", "GTS", "GBS", "GTO", "GKO", "DRL"} <= names,
              ", ".join(sorted(names)))
        check(f"{method}: V-score lines ship on their OWN layer, never the outline",
              ("GM1" in names) == (method == "v_score"))

        readme = zf.read("README.txt").decode()
        check(f"{method}: the README states the panel size and board count",
              f"{panel.boards}" in readme and f"{panel.width:.2f}" in readme)
        check(f"{method}: it warns that fab quantities are panels, not boards",
              "PANELS, not boards" in readme)

        # The single-board holes must survive into the panel drill file.
        board_holes = gerber.excellon_drill(p.board).count("X")
        panel_holes = zf.read([n for n in zf.namelist() if n.endswith(".DRL")][0]).decode()
        check(f"{method}: the panel drills at least every board's holes",
              panel_holes.count("X") >= board_holes * panel.boards,
              f"{panel_holes.count('X')} vs {board_holes * panel.boards}")
        if method == "mouse_bites":
            check("mouse bites add their perforations to the drill file",
                  panel_holes.count("X") > board_holes * panel.boards)

    # The pour must be filled per board, never across the routing slots.
    spec = PanelSpec(rows=1, cols=2, method="mouse_bites", rail=5.0)
    panel = panelize.derive(p.board, spec)
    regions = panelize.pour_regions(panel)
    check("there is one pour region per board",
          len(regions) == panel.boards, str(len(regions)))
    check("no pour region straddles the gap between boards",
          all(w <= panel.board_w + 1e-6 for _, _, w, _ in regions))


def test_panel_board_is_a_faithful_copy() -> None:
    print("\n-- the synthetic panel board")
    p = a_board("panel board")
    spec = PanelSpec(rows=2, cols=3, method="v_score", rail=5.0)
    panel = panelize.derive(p.board, spec)
    pb = panelize.panel_board(p.board, panel)

    check("every slot's components are present",
          len(pb.components) == len(p.board.components) * panel.boards,
          f"{len(pb.components)}")
    check("the panel board takes the panel's outline",
          abs(pb.width - panel.width) < 1e-6 and abs(pb.height - panel.height) < 1e-6)
    check("the original board is untouched",
          len(p.board.components) == 3 and abs(p.board.width - panel.board_w) < 1e-6)
    check("nothing sits outside the panel",
          all(0 <= c.x <= pb.width and 0 <= c.y <= pb.height for c in pb.components))
    check("airwires are dropped — a panel is not a routing artifact", not pb.rats)


def main() -> int:
    db.init_db()
    try:
        test_geometry()
        test_suggestion_is_for_the_person_ordering()
        test_rules_refuse_impossible_panels()
        test_panel_cam_package()
        test_panel_board_is_a_faithful_copy()
    finally:
        cleanup()

    print(f"\n{PASSED} checks passed, {len(FAILED)} failed.")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("All panelization checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
