"""CAD brief — the board as a small set of NOUNS for an external CAD agent.

Synqit CAD (the OpenSCAD text-to-CAD app) designs enclosures and product
bodies around a Synqit board. It must never re-model the board from a
screenshot; it gets the facts: outline, thickness, mounting holes, every
part envelope with its height, connector cutouts, and the enclosure
clearances the mechanical IR already holds. Two forms of the same data:

  * `entities`   — compact JSON, the queryable truth (~1-2 KB for a 20-part
                    board). Cheap in tokens, exact.
  * `openscad`   — a prelude the agent pastes at the top of its script:
                    named variables plus `module synqit_board()` that draws the
                    PCB with holes and part envelopes, and
                    `module synqit_hole_pattern(d, h)` for standoffs/screws.
  * `prompt_block` — the few lines of prose that go into the chat message.

Everything comes from `mechanical_bridge.product_scene`, the same payload the
STEP/FreeCAD/Blender bridges consume, so the CAD app sees exactly what the
fab outputs see.
"""
from __future__ import annotations

from app.models import Project
from app.services import mechanical_bridge


def _round(v: float, nd: int = 2) -> float:
    return round(float(v), nd)


def build_brief(project: Project) -> dict | None:
    scene = mechanical_bridge.product_scene(project, include_enclosure=True)
    if scene is None:
        return None

    o = scene["outline"]
    thickness = float(scene["board_thickness"])
    holes = [
        {"id": h["id"], "x": _round(h["x"]), "y": _round(h["y"]),
         "diameter": _round(h["diameter"]), "standoff": h.get("standoff")}
        for h in scene["holes"]
    ]
    parts = []
    tallest = 0.0
    for p in scene["parts"]:
        w = max(0.6, p["x1"] - p["x0"])
        h = max(0.6, p["y1"] - p["y0"])
        z = float(p["height"])
        if p["layer"] == "top":
            tallest = max(tallest, z)
        parts.append({
            "refdes": p["refdes"], "mpn": p["mpn"], "category": p["category"],
            "layer": p["layer"],
            "x": _round(p["x0"]), "y": _round(p["y0"]),
            "w": _round(w), "h": _round(h), "z": _round(z),
        })
    cutouts = [
        {"refdes": c["refdes"], "edge": c["edge"], "x": _round(c["x"]), "y": _round(c["y"]),
         "width": _round(c["width"]), "height": _round(c["height"])}
        for c in scene["cutouts"]
    ]
    enc = scene.get("enclosure") or {}
    entities = {
        "project": {"id": project.id, "name": project.name},
        "units": "mm",
        "frame": "origin bottom-left of the board, X right, Y up (toward the top edge), Z up from the board top surface",
        "board": {
            "shape": o["shape"], "width": _round(o["width"]), "height": _round(o["height"]),
            "corner_radius": _round(o.get("corner_radius", 0.0)), "thickness": thickness,
            "points": [[_round(x), _round(y)] for x, y in o.get("points", [])],
        },
        "holes": holes,
        "parts": parts,
        "tallest_top_part_mm": _round(tallest),
        "cutouts": cutouts,
        "enclosure": {
            "kind": enc.get("kind", "box"),
            "wall": enc.get("wall", 2.0), "standoff": enc.get("standoff", 5.0),
            "clearance_top": enc.get("clearance_top", 12.0),
            "clearance_bottom": enc.get("clearance_bottom", 3.0), "lip": enc.get("lip", 1.0),
        },
    }
    return {
        "entities": entities,
        "openscad": _openscad_prelude(entities),
        "prompt_block": _prompt_block(entities),
    }


def _openscad_prelude(e: dict) -> str:
    b = e["board"]
    lines = [
        "// ---- Synqit board (generated; do not edit; call synqit_board()) ----",
        f"/* [Synqit board: {e['project']['name']}] */",
        f"board_width = {b['width']};      // mm, fixed by the PCB",
        f"board_height = {b['height']};    // mm, fixed by the PCB",
        f"board_thickness = {b['thickness']};",
        f"board_corner_radius = {b['corner_radius']};",
        f"tallest_part_height = {e['tallest_top_part_mm']};  // mm above the board top",
        f"enclosure_wall = {e['enclosure']['wall']};",
        f"enclosure_standoff = {e['enclosure']['standoff']};",
        f"enclosure_clearance_top = {e['enclosure']['clearance_top']};",
        f"enclosure_clearance_bottom = {e['enclosure']['clearance_bottom']};",
        "synqit_holes = [" + ", ".join(f"[{h['x']}, {h['y']}, {h['diameter']}]" for h in e["holes"]) + "];  // [x, y, diameter]",
        "synqit_parts = [" + ", ".join(
            f"[\"{p['refdes']}\", {p['x']}, {p['y']}, {p['w']}, {p['h']}, {p['z']}, \"{p['layer']}\"]" for p in e["parts"]
        ) + "];  // [refdes, x, y, w, h, height, layer]",
        "synqit_cutouts = [" + ", ".join(
            f"[\"{c['refdes']}\", \"{c['edge']}\", {c['x']}, {c['y']}, {c['width']}, {c['height']}]" for c in e["cutouts"]
        ) + "];  // [refdes, edge, x, y, width, height]",
        "",
        "module synqit_board_outline() {",
    ]
    if b["shape"] == "circle":
        lines.append(f"  translate([board_width/2, board_height/2]) circle(d = board_width, $fn = 96);")
    elif b["shape"] == "polygon" and b["points"]:
        pts = ", ".join(f"[{x}, {y}]" for x, y in b["points"])
        lines.append(f"  polygon([{pts}]);")
    else:
        lines += [
            "  r = board_corner_radius;",
            "  if (r > 0) hull() for (x = [r, board_width - r], y = [r, board_height - r]) translate([x, y]) circle(r = r, $fn = 32);",
            "  else square([board_width, board_height]);",
        ]
    lines += [
        "}",
        "",
        "// PCB slab with mounting holes, plus every part as a box envelope (top parts up, bottom parts down).",
        "module synqit_board() {",
        "  color(\"darkgreen\") difference() {",
        "    translate([0, 0, -board_thickness]) linear_extrude(board_thickness) synqit_board_outline();",
        "    for (h = synqit_holes) translate([h[0], h[1], -board_thickness - 1]) cylinder(d = h[2], h = board_thickness + 2, $fn = 32);",
        "  }",
        "  for (p = synqit_parts)",
        "    color(p[6] == \"top\" ? \"dimgray\" : \"slategray\")",
        "      translate([p[1], p[2], p[6] == \"top\" ? 0 : -board_thickness - p[5]]) cube([p[3], p[4], p[5]]);",
        "}",
        "",
        "// Cylinders on every mounting hole: use for standoffs (subtract a smaller one for the screw).",
        "module synqit_hole_pattern(d, h) { for (p = synqit_holes) translate([p[0], p[1], 0]) cylinder(d = d, h = h, $fn = 32); }",
        "// ---- end Synqit board ----",
    ]
    return "\n".join(lines)


def _prompt_block(e: dict) -> str:
    b = e["board"]
    conns = ", ".join(f"{c['refdes']} on the {c['edge']} edge ({c['width']}x{c['height']} mm)" for c in e["cutouts"]) or "none recorded"
    return (
        f"Synqit board \"{e['project']['name']}\": {b['shape']} {b['width']} x {b['height']} mm, {b['thickness']} mm thick, "
        f"{len(e['holes'])} mounting holes, {len(e['parts'])} parts, tallest top-side part {e['tallest_top_part_mm']} mm. "
        f"Connector openings: {conns}. Enclosure clearances: wall {e['enclosure']['wall']} mm, standoff {e['enclosure']['standoff']} mm, "
        f"{e['enclosure']['clearance_top']} mm above the tallest part, {e['enclosure']['clearance_bottom']} mm below the board.\n"
        "Rules: paste the OpenSCAD block below at the top of the script unchanged, call synqit_board() to place the PCB, "
        "never redraw the board or move its holes, keep every connector opening reachable from outside, "
        "and expose your own dimensions (wall, clearances, fillets) as parameters."
    )
