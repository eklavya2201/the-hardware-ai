"""Runs INSIDE freecadcmd (FreeCAD's own Python). Builds the product from the
Synqit scene payload as NAMED parametric features and saves a .FCStd.

Env: SYNQIT_IN (scene json), SYNQIT_OUT (result json), SYNQIT_FCSTD, SYNQIT_STEP.
Feature names are the contract the user sees in FreeCAD's tree:
  Board, Hole_<n>, BoardDrilled, Part_<refdes>, CaseOuter, CaseInner, Case,
  Cutout_<refdes>, Standoff_<n>, and an App::Part group "Synqit_<name>".
"""
import json
import os
import traceback

result = {"ok": False}
try:
    import FreeCAD  # noqa: F401 — only importable inside freecadcmd
    import Part

    scene = json.load(open(os.environ["SYNQIT_IN"], encoding="utf-8"))
    o = scene["outline"]
    W, H, T = float(o["width"]), float(o["height"]), float(scene["board_thickness"])
    enc = scene.get("enclosure")
    # Board sits on its standoffs inside the case; everything is centred on the
    # board's XY centre so the document reads sensibly in FreeCAD's view.
    board_z = (enc["clearance_bottom"] + enc["standoff"]) if enc else 0.0

    doc = FreeCAD.newDocument("Synqit")
    group = doc.addObject("App::Part", "Synqit_" + "".join(c if c.isalnum() else "_" for c in scene.get("name", "product"))[:40])

    def place(obj, x, y, z):
        obj.Placement = FreeCAD.Placement(FreeCAD.Vector(x, y, z), FreeCAD.Rotation())

    # --- board --------------------------------------------------------------
    if o["shape"] == "circle":
        board = doc.addObject("Part::Cylinder", "Board")
        board.Radius = W / 2.0
        board.Height = T
        place(board, 0, 0, board_z)
    elif o["shape"] == "polygon" and len(o.get("points") or []) >= 3:
        pts = [FreeCAD.Vector(p[0] - W / 2.0, p[1] - H / 2.0, 0) for p in o["points"]]
        pts.append(pts[0])
        face = Part.Face(Part.makePolygon(pts))
        board = doc.addObject("Part::Feature", "Board")
        board.Shape = face.extrude(FreeCAD.Vector(0, 0, T))
        place(board, 0, 0, board_z)
    else:
        board = doc.addObject("Part::Box", "Board")
        board.Length, board.Width, board.Height = W, H, T
        place(board, -W / 2.0, -H / 2.0, board_z)
    group.addObject(board)

    drilled = board
    holes = scene.get("holes") or []
    if holes:
        tools = []
        for i, hole in enumerate(holes, 1):
            cyl = doc.addObject("Part::Cylinder", "Hole_%d" % i)
            cyl.Radius = float(hole["diameter"]) / 2.0
            cyl.Height = T + 2.0
            place(cyl, hole["x"] - W / 2.0, hole["y"] - H / 2.0, board_z - 1.0)
            group.addObject(cyl)
            tools.append(cyl)
        fusion = doc.addObject("Part::MultiFuse", "HoleTools")
        fusion.Shapes = tools
        group.addObject(fusion)
        drilled = doc.addObject("Part::Cut", "BoardDrilled")
        drilled.Base, drilled.Tool = board, fusion
        group.addObject(drilled)

    # --- parts (envelopes) --------------------------------------------------
    for p in scene.get("parts") or []:
        bw, bh, ph = p["x1"] - p["x0"], p["y1"] - p["y0"], float(p["height"])
        box = doc.addObject("Part::Box", "Part_" + "".join(c if c.isalnum() else "_" for c in p["refdes"]))
        box.Length, box.Width, box.Height = bw, bh, ph
        box.Label = "%s (%s)" % (p["refdes"], p["mpn"])
        z0 = board_z + (T if p.get("layer") != "bottom" else -ph)
        place(box, p["x0"] - W / 2.0, p["y0"] - H / 2.0, z0)
        group.addObject(box)

    # --- enclosure ----------------------------------------------------------
    if enc:
        wall = float(enc["wall"])
        inner_h = enc["standoff"] + T + enc["clearance_top"]
        outer_w, outer_d = W + 2 * (wall + 1.0), H + 2 * (wall + 1.0)
        outer_h = inner_h + enc["clearance_bottom"] + wall
        outer = doc.addObject("Part::Box", "CaseOuter")
        outer.Length, outer.Width, outer.Height = outer_w, outer_d, outer_h
        place(outer, -outer_w / 2.0, -outer_d / 2.0, 0.0)
        inner = doc.addObject("Part::Box", "CaseInner")
        inner.Length, inner.Width, inner.Height = outer_w - 2 * wall, outer_d - 2 * wall, outer_h  # open top
        place(inner, -outer_w / 2.0 + wall, -outer_d / 2.0 + wall, wall)
        group.addObject(outer)
        group.addObject(inner)
        shell = doc.addObject("Part::Cut", "CaseShell")
        shell.Base, shell.Tool = outer, inner
        group.addObject(shell)
        cut_tools = []
        for c in scene.get("cutouts") or []:
            cw, ch = float(c["width"]), float(c["height"])
            tool = doc.addObject("Part::Box", "Cutout_" + "".join(ch_ if ch_.isalnum() else "_" for ch_ in c["refdes"]))
            z = board_z + 0.0
            if c["edge"] in ("left", "right"):
                tool.Length, tool.Width, tool.Height = wall + 2.0, cw, ch
                x = (-outer_w / 2.0 - 1.0) if c["edge"] == "left" else (outer_w / 2.0 - wall - 1.0)
                place(tool, x, c["y"] - H / 2.0, z)
            elif c["edge"] in ("top", "bottom"):
                tool.Length, tool.Width, tool.Height = cw, wall + 2.0, ch
                y = (-outer_d / 2.0 - 1.0) if c["edge"] == "bottom" else (outer_d / 2.0 - wall - 1.0)
                place(tool, c["x"] - W / 2.0, y, z)
            else:
                doc.removeObject(tool.Name)
                continue
            group.addObject(tool)
            cut_tools.append(tool)
        case = shell
        if cut_tools:
            fuse = doc.addObject("Part::MultiFuse", "CutoutTools")
            fuse.Shapes = cut_tools
            group.addObject(fuse)
            case = doc.addObject("Part::Cut", "Case")
            case.Base, case.Tool = shell, fuse
            group.addObject(case)
        else:
            shell.Label = "Case"
        for i, hole in enumerate(holes, 1):
            so = doc.addObject("Part::Cylinder", "Standoff_%d" % i)
            so.Radius = max(float(hole["diameter"]) / 2.0 + 1.5, 2.5)
            so.Height = float(enc["standoff"])
            place(so, hole["x"] - W / 2.0, hole["y"] - H / 2.0, enc["clearance_bottom"])
            group.addObject(so)

    doc.recompute()
    doc.saveAs(os.environ["SYNQIT_FCSTD"])
    names = [obj.Name for obj in doc.Objects]
    try:
        import Import
        visible = [obj for obj in doc.Objects if obj.TypeId in ("Part::Cut", "Part::Box", "Part::Cylinder", "Part::Feature")
                   and obj.Name not in ("CaseOuter", "CaseInner", "Board") and not obj.Name.startswith(("Hole_", "Cutout_", "HoleTools", "CutoutTools", "CaseShell"))]
        if not holes:
            visible.append(board)
        Import.export(visible, os.environ["SYNQIT_STEP"])
    except Exception:  # noqa: BLE001 — the .FCStd is the product; STEP is a bonus
        pass
    result = {"ok": True, "data": {"features": names, "count": len(names)}}
except Exception as exc:  # noqa: BLE001
    result = {"ok": False, "error": "%s\n%s" % (exc, traceback.format_exc()[-1500:])}

with open(os.environ.get("SYNQIT_OUT", "out.json"), "w", encoding="utf-8") as fh:
    json.dump(result, fh)
