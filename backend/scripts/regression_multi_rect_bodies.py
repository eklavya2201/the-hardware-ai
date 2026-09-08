"""Guard: a footprint may occupy MORE THAN ONE rectangle (2026-08-20).

An Arduino shield's header set is four female headers around the edge of the
board with the whole middle free — which is exactly where a shield's components
go. It was modelled with KiCad's `Module:Arduino_UNO_R3`, a footprint for
placing an Uno *as a component on a carrier board*: body 75.48 x 53.89 mm,
because it includes the USB-B and barrel-jack shells that overhang the Uno's own
PCB. Consequences, all on a perfectly correct shield:

  * four hard PCB_R01 overlap errors — one against every part the shield
    carried, unavoidable because the host header is locked,
  * a PCB_R02 "extends past the board edge" warning on the header itself, and
  * a 3D view that drew a whole Arduino Uno where the headers should be.

`Footprint.bodyRects` + `geometry.solid_rects` fix the class: the overlap rule
and the placement legalizer read the SAME occupied rectangles, so they cannot
disagree, and a normal single-box part takes the unchanged fast path.

Offline; no LLM spend, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.models import Board, PlacedComponent  # noqa: E402
from app.models_db import ProjectRow  # noqa: E402
from app.services import (  # noqa: E402
    footprints, geometry, library, pcb_verifier, placement, store, templates, tools,
)

PASSED = 0
FAILED: list[str] = []
_SCRATCH: list[str] = []

SHIELD_MPN = "Arduino_Shield_Headers"
SHIELD_FPID = "Synqit:Synqit_Arduino_Shield_Headers"


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(label)
        print(f"  [FAIL] {label}" + (f"  ({detail})" if detail else ""))


def scratch(name: str):
    p = store.create_project(name, user_id="regression-multirect")
    _SCRATCH.append(p.id)
    return p


def cleanup() -> None:
    with db.SessionLocal() as s:
        for pid in _SCRATCH:
            row = s.get(ProjectRow, pid)
            if row is not None:
                s.delete(row)
        s.commit()


def test_shield_footprint_geometry() -> None:
    print("\n-- the shield header set describes headers, not an Arduino Uno")
    part = library.get_part(SHIELD_MPN)
    check("the part points at the authored shield footprint",
          part is not None and part.footprint == SHIELD_FPID,
          part.footprint if part else "missing")

    fp = footprints.for_part(part)
    check("it declares more than one solid rectangle",
          len(fp.bodyRects) > 1, f"{len(fp.bodyRects)} rects")
    check("four strips — the Uno R3 has two headers per edge",
          len(fp.bodyRects) == 4, f"{len(fp.bodyRects)}")
    check("every strip is one header wide (2.54 mm)",
          all(abs((y1 - y0) - 2.54) < 0.01 for _, y0, _, y1 in fp.bodyRects))

    # The declared envelope must still fit the standard shield outline, or the
    # containment check is measuring something that cannot exist.
    check("the envelope fits inside the 68.58 x 53.34 shield outline",
          fp.width <= 68.58 and fp.height <= 53.34,
          f"{fp.width:.2f} x {fp.height:.2f}")

    # The strips together must occupy far less than their bounding box — that
    # difference IS the free middle of the board.
    strip_area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in fp.bodyRects)
    check("the strips cover under a fifth of their bounding box",
          strip_area < 0.2 * fp.width * fp.height,
          f"{strip_area:.0f} of {fp.width * fp.height:.0f} mm2")

    check("pads are unchanged from the Uno R3 shield standard (32 pins)",
          len(fp.pads) == 32, f"{len(fp.pads)}")
    d7 = next((p for p in fp.pads if p.pin_number == "22"), None)
    d8 = next((p for p in fp.pads if p.pin_number == "23"), None)
    check("the 1.52 mm D7/D8 offset survived (it is what makes a shield fit)",
          d7 is not None and d8 is not None and abs(abs(d7.x - d8.x) - 4.06) < 0.01,
          f"{abs(d7.x - d8.x):.2f} mm" if d7 and d8 else "pads missing")


def test_solid_rects_is_the_one_authority() -> None:
    print("\n-- geometry.solid_rects: the single 'is this space taken' answer")
    plain = library.get_part("RC0603FR-0710KL")
    pc = PlacedComponent(refdes="R1", mpn=plain.mpn, x=10, y=10, rotation=0,
                         layer="top", footprint=footprints.for_part(plain))
    rects = geometry.solid_rects(pc)
    check("a plain part is still exactly one rectangle", len(rects) == 1)

    part = library.get_part(SHIELD_MPN)
    fp = footprints.for_part(part)
    j = PlacedComponent(refdes="J1", mpn=SHIELD_MPN, x=21.08, y=50.80, rotation=0,
                        layer="top", footprint=fp)
    jr = geometry.solid_rects(j)
    check("the shield header reports each strip separately", len(jr) == 4)

    # A point in the middle of the board must be free.
    mid_x, mid_y = 34.29, 26.67
    inside = any(x0 <= mid_x <= x1 and y0 <= mid_y <= y1 for x0, y0, x1, y1 in jr)
    check("the middle of the board is NOT inside the header", not inside)

    # Rotation must carry the strips with it.
    j90 = PlacedComponent(refdes="J1", mpn=SHIELD_MPN, x=21.08, y=50.80, rotation=90,
                          layer="top", footprint=fp)
    r90 = geometry.solid_rects(j90)
    check("rotating swaps each strip's extents",
          all(abs((x1 - x0) - 2.54) < 0.01 for x0, _, x1, _ in r90),
          f"{len(r90)} rects")


def test_a_correct_shield_reports_nothing() -> None:
    print("\n-- a correct shield build is clean")
    p = scratch("multirect shield")
    templates.apply(p, template_name="arduino_shield")
    for i, mpn in enumerate(
        ["RC0603FR-0710KL", "RC0603FR-071KL", "CL10A106MQ8NNNC", "LED_3MM_R"]
    ):
        tools.add_component(p, mpn=mpn, refdes=f"X{i + 1}")
    p.board = placement.auto_place(p)

    placed = {pc.refdes for pc in p.board.components}
    check("every part reached the board",
          placed == {"J1", "X1", "X2", "X3", "X4"}, ", ".join(sorted(placed)))

    issues = pcb_verifier.run_all(p.board)
    overlaps = [i for i in issues if i.rule == "PCB_R01_component_overlap"]
    offboard = [i for i in issues if i.rule == "PCB_R02_off_board"]
    check("no phantom overlap against the host header",
          not overlaps, "; ".join(i.message for i in overlaps[:3]))
    check("the header is not reported as hanging off the board",
          not offboard, "; ".join(i.message for i in offboard[:2]))
    check("no errors at all on a correct shield",
          not [i for i in issues if i.severity == "error"],
          "; ".join(i.rule for i in issues if i.severity == "error"))

    # The template's spec registration must survive the footprint swap — a
    # shield that does not mate with the Uno's headers is not a shield.
    j1 = next(pc for pc in p.board.components if pc.refdes == "J1")
    check("the header still sits at its spec position, locked",
          abs(j1.x - 21.08) < 0.01 and abs(j1.y - 50.80) < 0.01 and j1.locked,
          f"({j1.x:.2f}, {j1.y:.2f}) locked={j1.locked}")


def test_real_overlaps_still_caught() -> None:
    print("\n-- and a REAL overlap still fails")
    plain = library.get_part("CL10A106MQ8NNNC")
    fp = footprints.for_part(plain)
    a = PlacedComponent(refdes="C1", mpn=plain.mpn, x=20, y=20, rotation=0,
                        layer="top", footprint=fp)
    b = PlacedComponent(refdes="C2", mpn=plain.mpn, x=20.05, y=20.05, rotation=0,
                        layer="top", footprint=fp)
    board = Board(width=50, height=50, components=[a, b])
    check("two parts stacked on the same spot are still an error",
          bool(pcb_verifier.PCB_R01_component_overlap(board)))

    # A part dropped INSIDE one of the shield's strips must still be caught —
    # the fix must not make the header transparent.
    part = library.get_part(SHIELD_MPN)
    sfp = footprints.for_part(part)
    j = PlacedComponent(refdes="J1", mpn=SHIELD_MPN, x=21.08, y=50.80, rotation=0,
                        layer="top", footprint=sfp)
    strip = geometry.solid_rects(j)[0]
    on_strip = PlacedComponent(
        refdes="C9", mpn=plain.mpn, layer="top", footprint=fp, rotation=0,
        x=(strip[0] + strip[2]) / 2, y=(strip[1] + strip[3]) / 2,
    )
    board2 = Board(width=68.58, height=53.34, components=[j, on_strip])
    check("a part sitting ON a header strip is still an overlap error",
          bool(pcb_verifier.PCB_R01_component_overlap(board2)))


def test_pcb_r06_no_longer_libels_pin1_anchored_parts() -> None:
    print("\n-- PCB_R06 measures the body where the body actually is")
    for mpn in (SHIELD_MPN, "LED_3MM_R", "RC0603FR-0710KL"):
        part = library.get_part(mpn)
        pc = PlacedComponent(refdes="U1", mpn=mpn, x=25, y=25, rotation=0,
                             layer="top", footprint=footprints.for_part(part))
        board = Board(width=90, height=90, components=[pc])
        found = pcb_verifier.PCB_R06_pad_in_component(board)
        check(f"{mpn}: pads read as inside their own body",
              not found, found[0].message if found else "")

    # It must still fire when a footprint really does understate its pads.
    part = library.get_part("RC0603FR-0710KL")
    fp = footprints.for_part(part).model_copy(update={"width": 0.1, "height": 0.1})
    pc = PlacedComponent(refdes="R9", mpn=part.mpn, x=10, y=10, rotation=0,
                         layer="top", footprint=fp)
    check("a body genuinely smaller than its pads is still reported",
          bool(pcb_verifier.PCB_R06_pad_in_component(
              Board(width=50, height=50, components=[pc]))))


def main() -> int:
    db.init_db()
    try:
        test_shield_footprint_geometry()
        test_solid_rects_is_the_one_authority()
        test_a_correct_shield_reports_nothing()
        test_real_overlaps_still_caught()
        test_pcb_r06_no_longer_libels_pin1_anchored_parts()
    finally:
        cleanup()

    print(f"\n{PASSED} checks passed, {len(FAILED)} failed.")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("All multi-rect body checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
