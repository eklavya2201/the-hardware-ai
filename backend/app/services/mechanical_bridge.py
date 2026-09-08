"""Mechanical kernel bridge — Week 3 task 5.2 (OpenCASCADE via CadQuery).

Builds the board-as-a-3D-solid + enclosure from the mechanical IR and exports a
STEP file, using CadQuery (which bundles the OpenCASCADE OCP bindings). Mirrors
the ngspice / KiCad pattern: the heavy CAD kernel is OPTIONAL — if CadQuery isn't
installed the bridge degrades gracefully (returns None + a reason) so stub mode
and the offline demo keep working. CadQuery + OCP are tooling deps (not in
requirements.txt), like the Week-1 STEP→GLB tooling.

Consumes `MechanicalIR` (outline, mounting holes, cutouts, enclosure) in the IR
coordinate frame (Y-up, origin bottom-left) and emits STEP text. Coordinate
handling: CadQuery boxes are centred at the origin, so IR (x, y) maps to
CadQuery (x − w/2, y − h/2).
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from app.models import MechanicalIR, Project

BOARD_THICKNESS = 1.6  # mm — standard 2-layer FR-4


def _cadquery():
    try:
        import cadquery as cq  # noqa: PLC0415
        return cq
    except Exception:
        return None


def is_available() -> bool:
    """True if the CAD kernel (CadQuery/OCP) is importable."""
    return _cadquery() is not None


def status() -> str:
    return "available" if is_available() else "absent"


def _outline_wp(cq, outline):
    """A CadQuery Workplane 2D sketch of the board outline, centred at origin."""
    w, h = outline.width, outline.height
    if outline.shape == "circle":
        return cq.Workplane("XY").circle(w / 2.0)
    if outline.shape == "polygon" and len(outline.points or []) >= 3:
        # The STEP solid is milled to the SAME ring the copper was placed and
        # routed against — an enclosure derived from a bounding rectangle would
        # wrap air on three sides of a triangular board. Points are IR frame
        # (origin bottom-left); the sketch is centred, so shift by half the bbox.
        pts = [(p.x - w / 2.0, p.y - h / 2.0) for p in outline.points]
        return cq.Workplane("XY").polyline(pts).close()
    wp = cq.Workplane("XY").rect(w, h)
    return wp


def build_step(project: Project, include_enclosure: bool = True) -> tuple[bytes | None, str | None]:
    """Build the board solid (+ optional enclosure) from the project's mechanical
    IR and return `(step_bytes, error)`. `error` explains any graceful failure;
    `step_bytes` is None then. Never raises."""
    cq = _cadquery()
    if cq is None:
        return None, (
            "CadQuery not installed — the mechanical CAD kernel is optional. "
            "`pip install cadquery` to enable STEP export."
        )

    mech = project.mechanical
    # Fall back to the placed board's outline if the mechanical IR has none yet.
    if mech is None or mech.outline is None:
        if project.board is not None and project.board.components:
            from app.models import BoardOutlineMech
            outline = BoardOutlineMech(
                shape="rect", width=project.board.width, height=project.board.height
            )
            mech = mech or MechanicalIR()
        else:
            return None, "No board outline — set one or place the board first."
    else:
        outline = mech.outline

    w, h = outline.width, outline.height

    try:
        # --- board solid: extrude the outline, round corners, drill holes ---
        board = _outline_wp(cq, outline).extrude(BOARD_THICKNESS)
        if outline.shape == "rounded_rect" and outline.corner_radius > 0:
            try:
                board = board.edges("|Z").fillet(min(outline.corner_radius, min(w, h) / 2 - 0.1))
            except Exception:
                pass  # fillet can fail on tight geometry — skip, keep sharp corners

        for hole in (mech.mountingHoles if mech else []):
            # IR (x,y) -> CadQuery centre frame.
            cx, cy = hole.x - w / 2.0, hole.y - h / 2.0
            board = (
                board.faces(">Z").workplane(centerOption="CenterOfBoundBox")
                .pushPoints([(cx, cy)]).hole(hole.diameter)
            )

        # --- part envelopes: every placed part as a solid of its real footprint
        # extent x curated body height, seated on its side of the board. Not the
        # vendor's STEP (the server ships no KiCad 3D-model package), but the true
        # volume an enclosure has to clear - and a STEP that is "a plain white
        # board" is not a 3D model of the product (founder, 2026-09-06).
        if project.board is not None:
            from app.services import geometry as _geom
            from app.services import library as _lib
            from app.services import mechanical_derive as _md

            for pc in project.board.components:
                try:
                    part = _lib.get_part(pc.mpn)
                    if part is not None and getattr(part, "offBoard", False):
                        continue  # a servo/probe hangs off a cable; the board carries its connector
                    # BODY rectangles (geometry.solid_rects), never the courtyard:
                    # the ESP32-WROOM courtyard is 48x41 mm of antenna keep-out
                    # and rendered as a block that swallowed the whole board.
                    height = max(_md.body_height_mm(pc.mpn), 0.4)
                    z0 = BOARD_THICKNESS if pc.layer != "bottom" else -height
                    for x0, y0, x1, y1 in _geom.solid_rects(pc):
                        bw, bh = max(x1 - x0, 0.6), max(y1 - y0, 0.6)
                        cx, cy = (x0 + x1) / 2.0 - w / 2.0, (y0 + y1) / 2.0 - h / 2.0
                        body_solid = (
                            cq.Workplane("XY")
                            .box(bw, bh, height, centered=(True, True, False))
                            .translate((cx, cy, z0))
                        )
                        board = board.union(body_solid)
                except Exception:
                    continue  # one odd footprint must not sink the export

        assembly = board

        # --- optional enclosure: a box shell around the board with wall cutouts ---
        enc = mech.enclosure if mech else None
        if include_enclosure and enc and enc.kind in ("box", "snap_fit"):
            t = enc.wall_thickness
            inner_h = enc.standoff_height + BOARD_THICKNESS + enc.clearance_top
            outer_w = w + 2 * (t + 1.0)
            outer_d = h + 2 * (t + 1.0)
            outer_h = inner_h + enc.clearance_bottom + t
            shell = (
                cq.Workplane("XY")
                .box(outer_w, outer_d, outer_h, centered=(True, True, False))
                .faces(">Z").shell(-t)  # hollow, open top
            )
            # connector cutouts — punch only the wall named by `edge` (6.1), so a
            # right-edge USB port opens the right wall, not straight through.
            board_z = enc.clearance_bottom + enc.standoff_height
            wall_cut = t + 2.0  # box depth normal to the wall, straddling it
            for cut in (mech.cutouts if mech else []):
                z = board_z + cut.height / 2
                try:
                    if cut.edge in ("left", "right"):
                        # wall perpendicular to X; opening span (cut.width) runs along Y.
                        yc = cut.y + cut.width / 2 - h / 2
                        xc = (-outer_w / 2 + t / 2) if cut.edge == "left" else (outer_w / 2 - t / 2)
                        tool = cq.Workplane("XY").box(
                            wall_cut, cut.width, cut.height, centered=(True, True, True)
                        ).translate((xc, yc, z))
                    elif cut.edge in ("top", "bottom"):
                        # wall perpendicular to Y; opening span (cut.width) runs along X.
                        xc = cut.x + cut.width / 2 - w / 2
                        yc = (-outer_d / 2 + t / 2) if cut.edge == "bottom" else (outer_d / 2 - t / 2)
                        tool = cq.Workplane("XY").box(
                            cut.width, wall_cut, cut.height, centered=(True, True, True)
                        ).translate((xc, yc, z))
                    else:  # edge="none" — legacy: cut straight through (X-span).
                        xc = cut.x + cut.width / 2 - w / 2
                        tool = cq.Workplane("XY").box(
                            cut.width, outer_d, cut.height, centered=(True, True, True)
                        ).translate((xc, 0, z))
                    shell = shell.cut(tool)
                except Exception:
                    pass
            # sit the board on standoffs inside the shell
            board_z = enc.clearance_bottom + enc.standoff_height
            assembly = shell.union(board.translate((0, 0, board_z)))

        with tempfile.TemporaryDirectory(prefix="voltron_mech_") as tmp:
            step_path = Path(tmp) / "model.step"
            cq.exporters.export(assembly, str(step_path), exportType="STEP")
            data = step_path.read_bytes()
        return data, None
    except Exception as exc:  # noqa: BLE001
        return None, f"CAD kernel failed: {exc}"


# --------------------------------------------------------------------------- #
# Product scene — the ONE description of the physical product that every
# downstream CAD tool builds from (Prompt to Product, 2026-09): the CadQuery
# STEP above, FreeCAD's editable .FCStd (freecad_bridge) and Blender's render
# (blender_bridge). Pure data, IR frame (mm, Y-up, origin bottom-left); each
# consumer re-centres as it likes. No CAD kernel needed to build it.
# --------------------------------------------------------------------------- #


def product_scene(project: Project, include_enclosure: bool = True) -> dict | None:
    """Board outline + holes, every placed part as an envelope (footprint extent
    x curated height, on its side), the enclosure parameters and the connector
    cutouts. None when there is nothing physical yet."""
    from app.services import geometry as _geom
    from app.services import library as _lib
    from app.services import mechanical_derive as _md

    mech = project.mechanical
    board = project.board
    if (mech is None or mech.outline is None) and not (board is not None and board.components):
        return None
    if mech is not None and mech.outline is not None:
        o = mech.outline
        outline = {
            "shape": o.shape, "width": o.width, "height": o.height,
            "corner_radius": getattr(o, "corner_radius", 0.0) or 0.0,
            "points": [[p.x, p.y] for p in (getattr(o, "points", None) or [])],
        }
    else:
        outline = {"shape": "rect", "width": board.width, "height": board.height,
                   "corner_radius": 0.0, "points": []}
    if outline["shape"] == "circle" and board is not None and board.shape == "round":
        outline["width"] = outline["height"] = board.width

    parts = []
    if board is not None:
        for pc in board.components:
            try:
                part = _lib.get_part(pc.mpn)
                if part is not None and getattr(part, "offBoard", False):
                    continue
                rects = _geom.solid_rects(pc)   # body, not courtyard (see build_step)
                for i, (x0, y0, x1, y1) in enumerate(rects):
                    parts.append({
                        "refdes": pc.refdes if len(rects) == 1 else f"{pc.refdes}_{i + 1}",
                        "mpn": pc.mpn, "layer": pc.layer,
                        "x0": x0, "y0": y0, "x1": max(x1, x0 + 0.6), "y1": max(y1, y0 + 0.6),
                        "height": max(_md.body_height_mm(pc.mpn), 0.4),
                        "category": (part.category if part else "") or "",
                    })
            except Exception:
                continue

    enc = None
    if include_enclosure and mech is not None and mech.enclosure and mech.enclosure.kind != "none":
        e = mech.enclosure
        enc = {"kind": e.kind, "wall": e.wall_thickness, "standoff": e.standoff_height,
               "clearance_top": e.clearance_top, "clearance_bottom": e.clearance_bottom,
               "lip": e.lip}
    return {
        "name": project.name,
        "board_thickness": BOARD_THICKNESS,
        "outline": outline,
        "holes": [{"id": h.id, "x": h.x, "y": h.y, "diameter": h.diameter,
                   "standoff": h.standoff} for h in (mech.mountingHoles if mech else [])],
        "parts": parts,
        "enclosure": enc,
        "cutouts": [{"refdes": c.refdes, "edge": c.edge, "x": c.x, "y": c.y,
                     "width": c.width, "height": c.height} for c in (mech.cutouts if mech else [])],
    }


def export_meshes(project: Project, out_dir: str, include_enclosure: bool = True) -> tuple[dict | None, str | None]:
    """Write board.stl / parts.stl / case.stl (whichever exist) for the renderer.
    Same solids the STEP is made of, in the CadQuery centred frame. Returns
    ({name: path}, error)."""
    cq = _cadquery()
    if cq is None:
        return None, "CadQuery not installed."
    scene = product_scene(project, include_enclosure)
    if scene is None:
        return None, "No board outline — place the board first."
    o = scene["outline"]
    w, h, t = o["width"], o["height"], scene["board_thickness"]
    try:
        from app.models import BoardOutlineMech
        outline_model = BoardOutlineMech(shape=o["shape"], width=w, height=h)
        board = _outline_wp(cq, outline_model).extrude(t)
        for hole in scene["holes"]:
            board = (board.faces(">Z").workplane(centerOption="CenterOfBoundBox")
                     .pushPoints([(hole["x"] - w / 2, hole["y"] - h / 2)]).hole(hole["diameter"]))
        parts = None
        for p in scene["parts"]:
            bw, bh = p["x1"] - p["x0"], p["y1"] - p["y0"]
            cx, cy = (p["x0"] + p["x1"]) / 2 - w / 2, (p["y0"] + p["y1"]) / 2 - h / 2
            z0 = t if p["layer"] != "bottom" else -p["height"]
            solid = cq.Workplane("XY").box(bw, bh, p["height"], centered=(True, True, False)).translate((cx, cy, z0))
            parts = solid if parts is None else parts.union(solid)
        out: dict[str, str] = {}
        board_z = 0.0
        shell = None
        enc = scene["enclosure"]
        if enc:
            wall = enc["wall"]
            inner_h = enc["standoff"] + t + enc["clearance_top"]
            outer_w, outer_d = w + 2 * (wall + 1.0), h + 2 * (wall + 1.0)
            outer_h = inner_h + enc["clearance_bottom"] + wall
            shell = (cq.Workplane("XY").box(outer_w, outer_d, outer_h, centered=(True, True, False))
                     .faces(">Z").shell(-wall))
            board_z = enc["clearance_bottom"] + enc["standoff"]
            for cut in scene["cutouts"]:
                z = board_z + cut["height"] / 2
                try:
                    if cut["edge"] in ("left", "right"):
                        yc = cut["y"] + cut["width"] / 2 - h / 2
                        xc = (-outer_w / 2 + wall / 2) if cut["edge"] == "left" else (outer_w / 2 - wall / 2)
                        tool = cq.Workplane("XY").box(wall + 2, cut["width"], cut["height"]).translate((xc, yc, z))
                    elif cut["edge"] in ("top", "bottom"):
                        xc = cut["x"] + cut["width"] / 2 - w / 2
                        yc = (-outer_d / 2 + wall / 2) if cut["edge"] == "bottom" else (outer_d / 2 - wall / 2)
                        tool = cq.Workplane("XY").box(cut["width"], wall + 2, cut["height"]).translate((xc, yc, z))
                    else:
                        continue
                    shell = shell.cut(tool)
                except Exception:
                    pass
        p_board = str(Path(out_dir) / "board.stl")
        cq.exporters.export(board.translate((0, 0, board_z)), p_board, exportType="STL", tolerance=0.05)
        out["board"] = p_board
        if parts is not None:
            p_parts = str(Path(out_dir) / "parts.stl")
            cq.exporters.export(parts.translate((0, 0, board_z)), p_parts, exportType="STL", tolerance=0.05)
            out["parts"] = p_parts
        if shell is not None:
            p_case = str(Path(out_dir) / "case.stl")
            cq.exporters.export(shell, p_case, exportType="STL", tolerance=0.05)
            out["case"] = p_case
        return out, None
    except Exception as exc:  # noqa: BLE001
        return None, f"CAD kernel failed: {exc}"

