"""Form-factor template regression — Week-5 robotics task 7.3.

Guards the HAT / Arduino-shield form factors: applying one must seed the spec
board size, lock the standard header at its exact mating position (pad-field
centre on the spec point, correct rotation), write the spec outline + hole
pattern into the mechanical IR, and survive auto-place (the anchor never
moves, the board is never resized) with 0 mechanical-DRC errors.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_form_factors
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


def _pad_field_center(pc) -> tuple[float, float]:
    from app.services import geometry

    pts = [geometry.pad_world(pc, pad) for pad in pc.footprint.pads]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Project
    from app.services import mechanical_verifier, placement, templates, tools

    print("Form-factor templates regression (7.3)\n")

    # ---- Raspberry Pi HAT ---------------------------------------------------
    p = Project(id="hat", name="hat", path="/hat")
    result, diff = templates.apply(p, template_name="rpi_hat")
    check("rpi_hat applies through templates.apply", result["form_factor"] == "rpi_hat")
    check("header part added to the electrical IR",
          any(c.mpn == "RPi_HAT_Header_40P" for c in p.ir.components))
    check("board sized to the HAT spec (65 x 56.5)",
          p.board is not None and (p.board.width, p.board.height) == (65.0, 56.5))

    anchor = next(pc for pc in p.board.components if pc.mpn == "RPi_HAT_Header_40P")
    check("header is locked with a spec placementReason",
          anchor.locked and "form-factor" in (anchor.placementReason or ""))
    cx, cy = _pad_field_center(anchor)
    check("header pad-field centre lands on the spec point (32.5, 52.5)",
          abs(cx - 32.5) < 0.05 and abs(cy - 52.5) < 0.05, f"got ({cx:.2f},{cy:.2f})")
    check("header rotated to run along X (rot 90)", anchor.rotation == 90)

    mech = p.mechanical
    check("mechanical outline is the spec rounded rect",
          mech is not None and mech.outline is not None
          and mech.outline.shape == "rounded_rect"
          and (mech.outline.width, mech.outline.height) == (65.0, 56.5)
          and abs(mech.outline.corner_radius - 3.5) < 1e-9)
    holes = sorted((h.x, h.y) for h in mech.mountingHoles)
    check("4 x M2.5 holes at the HAT spec positions",
          holes == [(3.5, 3.5), (3.5, 52.5), (61.5, 3.5), (61.5, 52.5)]
          and all(abs(h.diameter - 2.75) < 1e-9 for h in mech.mountingHoles),
          f"got {holes}")

    # Fill the board a little, then auto-place: the anchor must not move and
    # the board must keep the spec size.
    for mpn in ("BME280", "RC0603FR-074K7L", "RC0603FR-074K7L", "CC0603KRX7R9BB104"):
        tools.add_component(p, mpn=mpn)
    p.board = placement.auto_place(p)
    check("auto-place keeps the spec board size",
          (p.board.width, p.board.height) == (65.0, 56.5),
          f"got {(p.board.width, p.board.height)}")
    re_anchor = next(pc for pc in p.board.components if pc.mpn == "RPi_HAT_Header_40P")
    check("auto-place never moves the locked header",
          (re_anchor.x, re_anchor.y, re_anchor.rotation)
          == (anchor.x, anchor.y, anchor.rotation) and re_anchor.locked)
    others_inside = all(
        0 <= pc.x <= 65.0 and 0 <= pc.y <= 56.5
        for pc in p.board.components
    )
    check("all placed parts sit inside the spec outline", others_inside)

    # A HAT sits on a whole Raspberry Pi, and the HAT specification requires
    # spacers of 8 mm or larger — recorded at apply time so the default floor
    # gap is right rather than the generic 3 mm minimum (2026-08-20).
    check("the HAT records the >= 8 mm clearance the Pi needs underneath",
          p.mechanical.enclosure.clearance_bottom >= 8.0,
          f"{p.mechanical.enclosure.clearance_bottom} mm")

    issues = mechanical_verifier.run(p)
    errs = [i for i in issues if i.severity == "error"]
    check("HAT mechanical DRC has 0 errors", len(errs) == 0,
          f"{[i.rule for i in errs][:4]}")

    # ---- Arduino Uno shield -------------------------------------------------
    q = Project(id="sh", name="sh", path="/sh")
    result, _ = templates.apply(q, template_name="arduino_shield")
    check("arduino_shield applies", result["form_factor"] == "arduino_shield")
    check("board sized to the Uno spec (68.58 x 53.34)",
          (q.board.width, q.board.height) == (68.58, 53.34))
    sh = next(pc for pc in q.board.components if pc.mpn == "Arduino_Shield_Headers")
    from app.services import geometry
    ys = sorted({round(geometry.pad_world(sh, pad)[1], 2) for pad in sh.footprint.pads})
    check("header rows sit 2.54 mm off each long edge (spec 100 mil)",
          abs(ys[0] - 2.54) < 0.05 and abs(ys[-1] - 50.80) < 0.05, f"rows at {ys}")
    check("shield header is locked", sh.locked)
    check("shield emits no mounting holes (retained by headers)",
          q.mechanical is not None and len(q.mechanical.mountingHoles) == 0)

    # ---- 3D manifest: pin-anchored header bodies land on their pads ---------
    # The blanket -90degX rotation mirrors footprint-frame Y; pin-1-anchored
    # header GLBs need offset_z = -(rows-1)*pitch so the (symmetric) body
    # covers the pad field instead of hanging off the board (the HAT header
    # rendered 48 mm off-board without this).
    import json as _json
    manifest_file = _BACKEND_ROOT.parent / "frontend" / "src" / "lib" / "models3d.manifest.json"
    if manifest_file.exists():
        manifest = _json.loads(manifest_file.read_text(encoding="utf-8"))
        e = manifest.get("Connector_PinHeader_2.54mm:PinHeader_2x20_P2.54mm_Vertical")
        check("manifest: 2x20 header body offset onto its pads (-48.26)",
              bool(e) and abs((e.get("offset") or [0, 0, 0])[2] + 48.26) < 0.01,
              f"{e.get('offset') if e else None}")
        e6 = manifest.get("Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
        check("manifest: 1x06 header body offset onto its pads (-12.70)",
              bool(e6) and abs((e6.get("offset") or [0, 0, 0])[2] + 12.70) < 0.01)
    else:
        check("models3d manifest present", False)

    # ---- dispatch & listing -------------------------------------------------
    names = {t["name"] for t in templates.list_templates()}
    check("list_templates advertises both form factors",
          {"rpi_hat", "arduino_shield"} <= names)
    try:
        templates.apply(q, template_name="nope")
        check("unknown template raises with the full roster", False)
    except tools.ToolError as e:
        check("unknown template raises with the full roster", "rpi_hat" in str(e))

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} assertion(s) failed.")
        return 1
    print("RESULT: all assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
