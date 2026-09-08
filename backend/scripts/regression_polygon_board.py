"""Polygon board regression — a board shaped like the product, end to end.

Rect and round were each hard-coded into every consumer that needs to know
where the copper ends. Tier 2 routes all three through `board_shape`, so these
checks assert the whole chain agrees about ONE outline: the tool records it,
the placer keeps every body inside it, the router refuses to lay copper off it,
PCB_R02 reports a part that escapes, and R57 reports a shape that came back
wrong. A disagreement between any two of those is how a part ends up placed
where the router thinks there is no board.

Exit codes: 0 pass, 1 an assertion failed. Offline, no LLM, no KiCad.

Usage: python -m scripts.regression_polygon_board
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ["OPENAI_API_KEY"] = ""

from app.models import Project, SchematicIR  # noqa: E402
from app.services import (  # noqa: E402
    board_shape,
    geometry,
    mechanical_derive,
    mechanical_tools,
    mechanical_verifier,
    pcb_verifier,
    placement,
    routing,
    tools,
    verifier,
)

_OK = 0
_FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _OK, _FAIL
    if cond:
        _OK += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {label}  ({detail})")


def _design(n_caps: int = 8) -> Project:
    """A small real design: an MCU, a regulator and some passives."""
    p = Project(id="poly", name="poly", path="poly", kind="hardware", ir=SchematicIR())
    tools.add_component(p, mpn="ESP32-C3-MINI-1")
    tools.add_component(p, mpn="AP2112K-3.3TRG1")
    for _ in range(n_caps):
        tools.add_component(p, mpn="CC0603KRX7R9BB104")
    return p


def main() -> int:
    print("Geometry authority")
    tri = board_shape.regular_polygon(3, 80)
    # An equilateral triangle's inradius is exactly R/2, and its area is
    # (3*sqrt(3)/4)*R^2 — closed forms, so the maths is checked not assumed.
    cx, cy = board_shape.centroid(tri)
    check("triangle inradius is R/2", abs(board_shape.clearance(tri, cx, cy) - 20.0) < 1e-6,
          f"{board_shape.clearance(tri, cx, cy):.4f}")
    check("triangle area matches the closed form",
          abs(board_shape.area(tri) - (3 * 3 ** 0.5 / 4) * 40.0 ** 2) < 1e-6,
          f"{board_shape.area(tri):.2f}")
    check("a point outside reads negative clearance",
          board_shape.clearance(tri, 5, 75) < 0, f"{board_shape.clearance(tri, 5, 75):.2f}")
    hexa = board_shape.regular_polygon(6, 60)
    check("hexagon area matches the closed form",
          abs(board_shape.area(hexa) - (3 * 3 ** 0.5 / 2) * 30.0 ** 2) < 1e-6,
          f"{board_shape.area(hexa):.2f}")
    clamped = board_shape.clamp(tri, 40, 74, 6.0)
    check("clamp lands exactly on the requested clearance",
          abs(board_shape.clearance(tri, *clamped) - 6.0) < 0.01,
          f"{board_shape.clearance(tri, *clamped):.3f} mm")
    check("clamp leaves an already-legal point alone",
          board_shape.clamp(tri, cx, cy, 6.0) == (cx, cy))

    print("\\nThe tool")
    p = _design()
    r, _ = tools.set_form_factor(p, shape="polygon", sides=3, diameter_mm=90,
                                 note="triangular drone board")
    check("a triangle is recordable", p.ir.form_factor.shape == "polygon"
          and p.ir.form_factor.sides == 3, str(r["form_factor"]["sides"]))
    refused = 0
    for kw in (dict(shape="polygon", sides=2, diameter_mm=50),
               dict(shape="polygon", sides=3),
               dict(shape="polygon", points=[[0, 0], [50, 0], [100, 0]]),
               dict(shape="polygon", sides=3, diameter_mm=50, width_mm=20)):
        try:
            tools.set_form_factor(p, **kw)
        except Exception:
            refused += 1
    check("unbuildable polygon asks are refused, not silently squared off",
          refused == 4, f"{refused}/4")

    print("\\nThe placer")
    p = _design()
    tools.set_form_factor(p, shape="polygon", sides=3, diameter_mm=90, note="tri")
    board = placement.auto_place(p)
    p.board = board
    ring = board_shape.ring_for_board(board)
    check("the built board is a polygon", board.shape == "polygon", board.shape)
    check("it carries its outline", ring is not None and len(ring) == 3,
          str(len(ring or [])))
    check("width/height are the bounding box",
          abs(board.width - (board_shape.bbox(ring)[2] - board_shape.bbox(ring)[0])) < 0.01)

    outside = []
    for pc in board.components:
        w, h = geometry.body_dims(pc)
        if not board_shape.fits(ring, pc.x, pc.y, w / 2, h / 2, margin=0.0):
            outside.append(pc.refdes)
    check("EVERY body sits inside the triangle", not outside,
          f"{len(board.components)} parts; escaped: {outside[:4]}")

    r02 = pcb_verifier.PCB_R02_off_board(p.board)
    check("PCB_R02 is silent on a correct polygon board", not r02,
          "; ".join(i.refdes or "" for i in r02[:4]))
    r57 = [i for i in verifier.R57_form_factor(p)]
    check("R57 is silent on a correct polygon board", not r57,
          "; ".join(i.message[:50] for i in r57))

    print("\\nThe rules catch a board that escaped its outline")
    moved = board.components[0]
    x0, y0, x1, y1 = board_shape.bbox(ring)
    moved.x, moved.y = x0 + 1.0, y1 - 1.0     # a bbox corner OUTSIDE the triangle
    bad = pcb_verifier.PCB_R02_off_board(p.board)
    check("a part in the bounding-box corner is REPORTED",
          any(i.refdes == moved.refdes for i in bad),
          f"{moved.refdes} at ({moved.x:.1f}, {moved.y:.1f})")

    p.board.shape = "rect"                     # what a stale re-place would leave
    shape_bad = verifier.R57_form_factor(p)
    check("a polygon ask that shipped as a rectangle is an ERROR",
          any(i.severity == "error" for i in shape_bad),
          "; ".join(i.message[:60] for i in shape_bad[:1]))
    p.board.shape = "polygon"

    print("\\nThe router")
    p2 = _design(4)
    tools.set_form_factor(p2, shape="polygon", sides=3, diameter_mm=90, note="tri")
    p2.board = placement.auto_place(p2)
    ring2 = board_shape.ring_for_board(p2.board)
    grid = routing._build_grid(p2, p2.board)[0]
    blocked_outside = inside_free = 0
    for rr in range(0, grid.rows, 3):
        for cc in range(0, grid.cols, 3):
            x, y = cc * grid.pitch, rr * grid.pitch
            clear = board_shape.clearance(ring2, x, y)
            if clear < -1.0:                       # comfortably off the board
                if not grid.is_passable(0, rr, cc, "ANY_NET"):
                    blocked_outside += 1
                else:
                    inside_free += 1
    check("every cell off the copper is hard-blocked on the top layer",
          inside_free == 0, f"{blocked_outside} blocked, {inside_free} left routable")

    print("\\nRect and round are untouched")
    p3 = _design()
    p3.board = placement.auto_place(p3)
    check("a plain design still builds a rect board", p3.board.shape == "rect",
          f"{p3.board.width:.1f}x{p3.board.height:.1f}")
    check("and carries no outline points", not p3.board.outline_points)
    check("ring_for_board takes the rect fast path",
          board_shape.ring_for_board(p3.board) is None)
    p4 = _design()
    tools.set_form_factor(p4, shape="round", diameter_mm=45, note="watch")
    p4.board = placement.auto_place(p4)
    check("a round ask still builds a round board", p4.board.shape == "round",
          f"Ø{p4.board.width:.1f}")

    print("\\nThe enclosure follows the board")
    p5 = _design(6)
    tools.set_form_factor(p5, shape="polygon", sides=3, diameter_mm=95, note="tri")
    p5.board = placement.auto_place(p5)
    mechanical_tools.derive_from_board(p5)
    o = p5.mechanical.outline
    check("the mechanical outline is the SAME ring, not a bounding box",
          o.shape == "polygon" and len(o.points) == 3, f"{o.shape} {len(o.points)} pts")
    ring5 = board_shape.ring_for_board(p5.board)
    holes = p5.mechanical.mountingHoles
    check("a hole is drilled at every vertex", len(holes) == 3, str(len(holes)))
    check("every hole clears the milled edge by the boss radius",
          all(board_shape.clearance(ring5, h.x, h.y)
              >= mechanical_derive.MOUNT_CLEAR_R - 0.01 for h in holes),
          "; ".join(f"{board_shape.clearance(ring5, h.x, h.y):.2f}" for h in holes))
    mech_issues = mechanical_verifier.run(p5)
    check("the derived enclosure is mechanically clean", not mech_issues,
          "; ".join(f"{i.rule}:{i.severity}" for i in mech_issues[:3]))
    # The enclosure rule and the PCB rule must agree about the same outline.
    p5.board.components[0].x = 2.0
    p5.board.components[0].y = p5.board.height - 2.0
    r01 = mechanical_verifier.MECH_R01_board_fits_outline(p5)
    check("MECH_R01 reports a part off a polygon outline", bool(r01),
          r01[0].message[:70] if r01 else "silent")

    print(f"\\nRESULT: {_OK} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
