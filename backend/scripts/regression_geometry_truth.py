"""Geometry-truth regression — the orientation/flush/cutout-anchor chain.

Locks in the six 3D-geometry fixes of docs/3D_GEOMETRY_FIDELITY.md (the one
piece of that work that had no guard):

  1. COVERAGE   — every connector-category part has a measured body centre in
                  model_body_centers.json (no pad-centre fallbacks left).
  2. STALENESS  — each measured entry still matches the part's current
                  footprint (a footprint remap invalidates the measurement).
  3. MIRROR     — pads-vs-body audit: a panel connector's body must sit
                  displaced from its pad field TOWARD its matingFaceDir
                  (calibrated on the visually-verified USB-C / barrel jack /
                  screw terminals). A body significantly AWAY from the mating
                  face means a Y-mirrored GLB or a wrong matingFaceDir seed.
  4. CHAIN      — on the stub board: the edge connector is rotated so its
                  mating face points off-board, sits flush against that edge,
                  its derived cutout lands on the same edge centred on the
                  measured body anchor, and mechanical DRC reports 0 errors.

Uses only committed data (no trimesh/KiCad needed) + the deterministic stub.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_geometry_truth
"""

from __future__ import annotations

import json
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


_DIR_UNIT = {"+X": (1.0, 0.0), "-X": (-1.0, 0.0), "+Y": (0.0, 1.0), "-Y": (0.0, -1.0)}
_DIR_ANGLE = {"+X": 0, "+Y": 90, "-X": 180, "-Y": 270}
# A body whose centre projects this far AWAY from the mating face (mm) is a
# suspected mirrored GLB / wrong matingFaceDir seed. Generous: legit bodies
# straddle their pad field, but never sit centimetres behind it.
_MIRROR_TOL_MM = 1.0


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    from app.models import Project
    from app.services import agent, geometry, library, mechanical_derive, mechanical_verifier
    from app.services import mechanical_tools as mt
    from app.services import placement

    lib_dir = _BACKEND_ROOT / "app" / "library"
    centers = json.loads((lib_dir / "model_body_centers.json").read_text(encoding="utf-8"))
    cache = json.loads((lib_dir / "footprint_cache.json").read_text(encoding="utf-8"))

    connectors = [p for p in library.all_parts() if (p.category or "").lower() == "connector"]

    print("Geometry-truth regression\n")
    print("1) body-centre coverage")
    unmeasured = [p.mpn for p in connectors if p.mpn not in centers]
    check("every connector has a measured body centre",
          not unmeasured, ", ".join(unmeasured) or f"{len(connectors)}/{len(connectors)}")

    print("\n2) measurement staleness")
    stale = [
        p.mpn for p in connectors
        if p.mpn in centers and centers[p.mpn].get("footprint") != p.footprint
    ]
    check("measured centres match current footprints", not stale, ", ".join(stale))

    print("\n3) pads-vs-body mirror audit (connectors with a matingFaceDir)")
    suspects: list[str] = []
    audited = 0
    for p in connectors:
        mating = getattr(p, "matingFaceDir", None)
        entry = centers.get(p.mpn)
        fp = cache.get(p.footprint)
        if not mating or mating not in _DIR_UNIT or not entry or not fp or not fp.get("pads"):
            continue
        audited += 1
        xs = [pad["x"] for pad in fp["pads"]]
        ys = [pad["y"] for pad in fp["pads"]]
        pad_cx, pad_cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        ux, uy = _DIR_UNIT[mating]
        proj = (entry["cx"] - pad_cx) * ux + (entry["cy"] - pad_cy) * uy
        if proj < -_MIRROR_TOL_MM:
            suspects.append(f"{p.mpn} (proj {proj:+.2f} mm, mating {mating})")
    check(f"no body sits behind its mating face ({audited} audited)",
          not suspects, "; ".join(suspects))

    print("\n4) stub-board chain: flush + orientation + cutout anchor + DRC")
    project = Project(id="g", name="g", path="/g")
    for _ in agent._run_stub(project, "esp32"):
        pass
    project.board = placement.auto_place(project)
    board = project.board

    edge_pcs = []
    for pc in board.components:
        part = library.get_part(pc.mpn)
        if part and getattr(part, "matingFaceDir", None):
            edge_pcs.append((pc, part))
    check("stub board places at least one mating-face connector", bool(edge_pcs))

    mt.dispatch(project, "derive_from_board", {})
    cutouts = {c.refdes: c for c in project.mechanical.cutouts}

    for pc, part in edge_pcs:
        ref = pc.refdes
        dists = {"left": pc.x, "right": board.width - pc.x,
                 "top": pc.y, "bottom": board.height - pc.y}
        nearest = min(dists, key=dists.get)  # type: ignore[arg-type]
        outward = {"left": "-X", "right": "+X", "top": "-Y", "bottom": "+Y"}[nearest]

        expected_rot = (_DIR_ANGLE[outward] - _DIR_ANGLE[part.matingFaceDir]) % 360
        check(f"{ref} rotated so mating face points off-board",
              pc.rotation % 360 == expected_rot,
              f"rot={pc.rotation} expected={expected_rot} edge={nearest}")

        fw, fh = pc.footprint.width, pc.footprint.height
        if pc.rotation % 180 == 90:
            fw, fh = fh, fw
        extent = fw / 2 if nearest in ("left", "right") else fh / 2
        # Measure from the pad-field centre — the placer's body proxy (it solves
        # in body-centre space and converts to the anchor on exit). The anchor of
        # a pad-asymmetric connector (USB-C) legitimately sits FARTHER from the
        # edge than the seated body once the mouth is rotated to face it.
        pxs = [p.x for p in pc.footprint.pads]
        pys = [p.y for p in pc.footprint.pads]
        scx, scy = geometry.rotate_xy(
            (min(pxs) + max(pxs)) / 2.0, (min(pys) + max(pys)) / 2.0, pc.rotation
        ) if pxs else (0.0, 0.0)
        body_d = {"left": pc.x + scx, "right": board.width - (pc.x + scx),
                  "top": pc.y + scy, "bottom": board.height - (pc.y + scy)}[nearest]
        check(f"{ref} sits flush against the {nearest} edge",
              body_d <= placement.BOARD_MARGIN + extent + 0.6,
              f"dist={body_d:.2f} body-half={extent:.2f}")

        cut = cutouts.get(ref)
        check(f"{ref} has a derived enclosure cutout", cut is not None)
        if cut is None:
            continue
        cut_edge_map = {"left": "left", "right": "right", "top": "bottom", "bottom": "top"}
        check(f"{ref} cutout is on its wall", cut.edge in (nearest, cut_edge_map[nearest]),
              f"cutout edge={cut.edge} vs placement edge={nearest}")

        ax, ay = mechanical_derive._connector_anchor_world(pc)
        along = ay if cut.edge in ("left", "right") else ax
        lo = cut.y if cut.edge in ("left", "right") else cut.x
        centre = lo + cut.width / 2.0
        check(f"{ref} cutout centres on the measured body anchor",
              abs(centre - along) <= 1.0, f"cutout centre={centre:.2f} anchor={along:.2f}")

    issues = mechanical_verifier.run(project)
    errors = [i for i in issues if i.severity == "error"]
    check("mechanical DRC: 0 errors on the derived enclosure",
          not errors, "; ".join(f"{i.rule}: {i.message}" for i in errors[:3]))

    print(f"\n{'ALL PASS' if not _failures else f'{len(_failures)} FAILURE(S)'}")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
