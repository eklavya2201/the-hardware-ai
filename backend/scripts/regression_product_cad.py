"""Guard for the FreeCAD + Blender integration (Prompt to Product, 2026-09).

Offline for the scene + meshes (CadQuery); the FreeCAD and Blender halves run
only where the binaries exist (the Docker image) and SKIP elsewhere — a missing
tool must degrade to a reason, never a traceback.

Run from backend/:  python -m scripts.regression_product_cad
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile

os.environ["OPENAI_API_KEY"] = ""


def main() -> int:
    from app.models import Project
    from app.services import (
        agent, blender_bridge, freecad_bridge, mechanical_bridge, mechanical_tools, placement,
    )

    checks: list[tuple[bool, str]] = []

    def check(ok, label):
        checks.append((bool(ok), label))

    p = Project(id="cad", name="cad guard", path="/tmp/cad")
    for _ in agent.run(p, "ESP32 sensor board"):
        pass
    check(mechanical_bridge.product_scene(p) is None, "no board => no scene (nothing physical yet)")
    p.board = placement.auto_place(p)
    mechanical_tools.dispatch(p, "derive_from_board", {})

    scene = mechanical_bridge.product_scene(p)
    check(scene is not None and scene["outline"]["width"] > 10, "scene has the board outline")
    check(scene and len(scene["parts"]) >= len(p.board.components), f"scene has at least one envelope per placed part ({len(scene['parts'])})")
    check(scene and all(pt["x1"] > pt["x0"] and pt["y1"] > pt["y0"] and pt["height"] > 0 for pt in scene["parts"]), "every envelope has positive extent and height")
    # The courtyard bug: an ESP32's 48x41 mm antenna keep-out rendered as a block
    # that swallowed the board. Envelopes are BODIES, so none may approach the
    # board's own area, and every one must sit inside the outline.
    if scene:
        bw, bh = scene["outline"]["width"], scene["outline"]["height"]
        biggest = max(((q["x1"] - q["x0"]) * (q["y1"] - q["y0"])) for q in scene["parts"])
        check(biggest < 0.45 * bw * bh, f"largest envelope is a body, not a courtyard ({biggest:.0f} mm2 of {bw * bh:.0f})")
        check(all(q["x0"] >= -1 and q["y0"] >= -1 and q["x1"] <= bw + 1 and q["y1"] <= bh + 1 for q in scene["parts"]),
              "every envelope sits on the board")
    check(scene and len(scene["holes"]) == 4, "four mounting holes in the scene")
    check(scene and scene["enclosure"] and scene["enclosure"]["kind"] == "box", "enclosure parameters present")
    check(scene and any(c["edge"] in ("left", "right", "top", "bottom") for c in scene["cutouts"]), "a connector cutout names its wall")

    with tempfile.TemporaryDirectory() as tmp:
        meshes, err = mechanical_bridge.export_meshes(p, tmp)
        check(meshes is not None, f"meshes exported ({err or ''})")
        if meshes:
            check(set(meshes) == {"board", "parts", "case"}, f"board/parts/case meshes ({sorted(meshes)})")
            check(all(os.path.getsize(v) > 1000 for v in meshes.values()), "each STL is non-trivial")

    if freecad_bridge.is_available():
        res = freecad_bridge.build_fcstd(p)
        check(res.success, f"FreeCAD built the document ({res.error or ''})")
        if res.success:
            names = res.data.get("features", [])
            check("Board" in names and "Case" in names, "features Board and Case exist")
            check(any(n.startswith("Part_") for n in names), "parts are named features")
            check(any(n.startswith("Standoff_") for n in names), "standoffs are named features")
            check(any(n.startswith("Cutout_") for n in names), "connector cutout is a named feature")
            fc = res.data["fcstd"]
            check(zipfile.is_zipfile(__import__("io").BytesIO(fc)), ".FCStd is a zip document")
            check(res.data.get("step", b"")[:13] == b"ISO-10303-21;", "FreeCAD also wrote a STEP")
    else:
        res = freecad_bridge.build_fcstd(p)
        check(not res.success and "FreeCAD" in (res.error or ""), "no FreeCAD => graceful reason (SKIP build)")

    if blender_bridge.is_available():
        png, err = blender_bridge.render(p, width=480, height=300, samples=8)
        check(png is not None and png[:8] == b"\x89PNG\r\n\x1a\n", f"Blender rendered a PNG ({err or ''})")
    else:
        png, err = blender_bridge.render(p)
        check(png is None and "Blender" in (err or ""), "no Blender => graceful reason (SKIP render)")

    failures = [l for ok, l in checks if not ok]
    for ok, l in checks:
        print(("PASS " if ok else "FAIL ") + l)
    print(f"\n{len(checks) - len(failures)}/{len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
