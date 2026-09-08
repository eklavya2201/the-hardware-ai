"""IR → `.kicad_sch` (KiCad 7/8 schematic) — the "clean handoff" half of the
market-research recommendation: interoperate with the tool engineers already
trust instead of trying to replace it (item #5; the beta tester's #1 ask).

Pure Python, no KiCad dependency — unlike `export.kicad_pcb`, which shells out
to pcbnew. That matters: the schematic handoff must work on a KiCad-less box
(local dev, and any deploy where the bridge is absent).

STRICTLY one-way, like every other KiCad writer here: we generate `.kicad_sch`,
we never read one back. Reading a hand-edited schematic into the IR would break
the "IR is the single source of truth / the LLM only mutates it through tools"
anti-hallucination guarantee.

Topology is expressed the way netlist-derived schematics conventionally are:
each part is drawn as a rectangular symbol with its real pins, and every
connected pin carries a short wire stub ending in a **global label** with the
net name. Nets connect by label, so the schematic is electrically correct and
ERC-clean in KiCad without needing a routed wire graph.
"""

from __future__ import annotations

import hashlib
from app.models import LibraryPart, Project
from app.services import library

# KiCad schematic grid is 1.27 mm; everything must land on it or KiCad's ERC
# reports "pin not on grid" and wires won't attach.
GRID = 1.27
PIN_LEN = 2.54
SYMBOL_PAD = 2.54  # body margin beyond the outermost pin row


def _uuid(*parts: str) -> str:
    """Deterministic UUID from stable inputs, so re-exporting the same design
    produces the same file (diffable in git, no spurious churn)."""
    h = hashlib.sha1("::".join(parts).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# KiCad electrical types, mapped from our PinType vocabulary.
_ETYPE = {
    "power_in": "power_in",
    "power_out": "power_out",
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "passive": "passive",
    "ground": "power_in",
    "unspecified": "unspecified",
    "open_drain": "open_collector",
    "tristate": "tri_state",
    "no_connect": "no_connect",
}

# Pin side → (KiCad rotation angle, unit direction the pin extends from the body)
_SIDE_ANGLE = {"left": 0, "right": 180, "top": 270, "bottom": 90}


def _symbol_geometry(part: LibraryPart | None, mpn: str) -> dict:
    """Lay a part's pins out on a rectangle: left/right pins stack vertically,
    top/bottom pins spread horizontally. Returns body size + per-pin placement
    in symbol-local coordinates (Y-up, KiCad symbol convention)."""
    pins = list(part.pins) if part and part.pins else []
    by_side: dict[str, list] = {"left": [], "right": [], "top": [], "bottom": []}
    for p in pins:
        by_side.get(p.side, by_side["left"]).append(p)

    rows = max(len(by_side["left"]), len(by_side["right"]), 1)
    cols = max(len(by_side["top"]), len(by_side["bottom"]), 1)
    half_h = _snap(max(rows - 1, 1) * GRID * 2 / 2 + SYMBOL_PAD)
    half_w = _snap(max(cols * GRID * 2, 10.16) / 2 + SYMBOL_PAD)

    placed: list[dict] = []
    for side in ("left", "right"):
        group = by_side[side]
        for i, p in enumerate(group):
            y = _snap((len(group) - 1) / 2 * GRID * 2 - i * GRID * 2)
            x = -half_w if side == "left" else half_w
            placed.append({"pin": p, "x": x, "y": y, "angle": _SIDE_ANGLE[side]})
    for side in ("top", "bottom"):
        group = by_side[side]
        for i, p in enumerate(group):
            x = _snap(-(len(group) - 1) / 2 * GRID * 2 + i * GRID * 2)
            y = half_h if side == "top" else -half_h
            placed.append({"pin": p, "x": x, "y": y, "angle": _SIDE_ANGLE[side]})

    return {"half_w": half_w, "half_h": half_h, "pins": placed}


def _lib_symbol(mpn: str, part: LibraryPart | None, geo: dict) -> str:
    """One `lib_symbols` entry: rectangle body + pins. Names are sanitised
    because KiCad symbol ids can't contain `:` or spaces."""
    sym_id = f"Synqit:{mpn.replace(':', '_').replace(' ', '_')}"
    hw, hh = geo["half_w"], geo["half_h"]
    desc = _esc((part.description if part else mpn)[:120])
    out = [
        f'    (symbol "{_esc(sym_id)}" (pin_names (offset 0.508)) (in_bom yes) (on_board yes)',
        f'      (property "Reference" "U" (at 0 {hh + 2.54:.4f} 0) '
        '(effects (font (size 1.27 1.27))))',
        f'      (property "Value" "{_esc(mpn)}" (at 0 {-hh - 2.54:.4f} 0) '
        '(effects (font (size 1.27 1.27))))',
        f'      (property "Footprint" "{_esc(part.footprint if part else "")}" (at 0 0 0) '
        '(effects (font (size 1.27 1.27)) hide))',
        f'      (property "Datasheet" "{_esc((part.datasheetUrl if part else None) or "")}" (at 0 0 0) '
        '(effects (font (size 1.27 1.27)) hide))',
        f'      (property "Description" "{desc}" (at 0 0 0) '
        '(effects (font (size 1.27 1.27)) hide))',
        f'      (symbol "{_esc(sym_id.split(":")[1])}_0_1"',
        f'        (rectangle (start {-hw:.4f} {hh:.4f}) (end {hw:.4f} {-hh:.4f})',
        '          (stroke (width 0.254) (type default)) (fill (type background)))',
        "      )",
        f'      (symbol "{_esc(sym_id.split(":")[1])}_1_1"',
    ]
    for pl in geo["pins"]:
        p = pl["pin"]
        etype = _ETYPE.get(p.type, "passive")
        out.append(
            f'        (pin {etype} line (at {pl["x"]:.4f} {pl["y"]:.4f} {pl["angle"]}) '
            f'(length {PIN_LEN})'
        )
        out.append(f'          (name "{_esc(p.name)}" (effects (font (size 1.27 1.27))))')
        out.append(f'          (number "{_esc(p.number)}" (effects (font (size 1.27 1.27))))')
        out.append("        )")
    out += ["      )", "    )"]
    return "\n".join(out)


def kicad_sch(project: Project) -> str:
    """Generate `.kicad_sch` text for the project's schematic IR."""
    ir = project.ir
    parts: dict[str, LibraryPart | None] = {}
    geos: dict[str, dict] = {}
    for c in ir.components:
        if c.mpn not in parts:
            parts[c.mpn] = library.get_part(c.mpn)
            geos[c.mpn] = _symbol_geometry(parts[c.mpn], c.mpn)

    # Net lookup per (refdes, pin number) for the label stubs.
    net_of: dict[tuple[str, str], str] = {}
    for n in ir.nets:
        for refdes, pin in n.pins:
            net_of[(refdes, str(pin))] = n.name

    # IR canvas coords are px-ish and Y-up; KiCad sheets are mm, Y-down, origin
    # top-left. Scale down and lay parts out on the grid inside an A3 sheet.
    xs = [c.x for c in ir.components] or [0]
    ys = [c.y for c in ir.components] or [0]
    min_x, min_y = min(xs), min(ys)
    max_y = max(ys)
    SCALE = 0.12

    body: list[str] = []
    for c in ir.components:
        geo = geos[c.mpn]
        sym_id = f"Synqit:{c.mpn.replace(':', '_').replace(' ', '_')}"
        sx = _snap(25.4 + (c.x - min_x) * SCALE)
        sy = _snap(25.4 + (max_y - c.y) * SCALE)  # flip Y
        uid = _uuid(project.id, c.refdes)
        part = parts[c.mpn]
        body.append(
            f'  (symbol (lib_id "{_esc(sym_id)}") (at {sx:.4f} {sy:.4f} 0) (unit 1)\n'
            f'    (in_bom yes) (on_board yes) (dnp no) (uuid "{uid}")\n'
            f'    (property "Reference" "{_esc(c.refdes)}" '
            f'(at {sx:.4f} {sy - geo["half_h"] - 2.54:.4f} 0) '
            '(effects (font (size 1.27 1.27))))\n'
            f'    (property "Value" "{_esc(c.value or c.mpn)}" '
            f'(at {sx:.4f} {sy + geo["half_h"] + 2.54:.4f} 0) '
            '(effects (font (size 1.27 1.27))))\n'
            f'    (property "Footprint" "{_esc(c.footprint or (part.footprint if part else ""))}" '
            f'(at {sx:.4f} {sy:.4f} 0) (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "MPN" "{_esc(c.mpn)}" (at {sx:.4f} {sy:.4f} 0) '
            '(effects (font (size 1.27 1.27)) hide))\n'
            "  )"
        )

        # Wire stub + global label on every connected pin.
        for pl in geo["pins"]:
            pin = pl["pin"]
            net = net_of.get((c.refdes, str(pin.number)))
            if not net:
                continue
            px = _snap(sx + pl["x"])
            py = _snap(sy - pl["y"])  # symbol Y-up → sheet Y-down
            dx, dy = {0: (-1, 0), 180: (1, 0), 270: (0, -1), 90: (0, 1)}[pl["angle"]]
            ex = _snap(px + dx * PIN_LEN)
            ey = _snap(py + dy * PIN_LEN)
            body.append(
                f'  (wire (pts (xy {px:.4f} {py:.4f}) (xy {ex:.4f} {ey:.4f}))\n'
                f'    (stroke (width 0) (type default)) (uuid "{_uuid(project.id, c.refdes, pin.number, "w")}"))'
            )
            label_angle = {0: 180, 180: 0, 270: 270, 90: 90}[pl["angle"]]
            body.append(
                f'  (global_label "{_esc(net)}" (shape input) (at {ex:.4f} {ey:.4f} {label_angle})\n'
                f'    (effects (font (size 1.27 1.27)) (justify left))\n'
                f'    (uuid "{_uuid(project.id, c.refdes, pin.number, "l")}"))'
            )

    lib_syms = "\n".join(_lib_symbol(mpn, parts[mpn], geos[mpn]) for mpn in sorted(parts))
    head = [
        "(kicad_sch (version 20231120) (generator \"synqit\")",
        f'  (uuid "{_uuid(project.id, "sheet")}")',
        '  (paper "A3")',
        "  (title_block",
        f'    (title "{_esc(project.name)}")',
        '    (comment 1 "Generated by Synqit — IR is the source of truth; edits here do not flow back")',
        "  )",
        "  (lib_symbols",
        lib_syms,
        "  )",
    ]
    return "\n".join(head + body + [")"]) + "\n"
