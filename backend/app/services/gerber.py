"""Gerber (RS-274X) + Excellon drill fabrication export (affiliate Phase 2).

Generates the standard 2-layer CAM package JLCPCB / PCBWay auto-detect from
the file extensions: top/bottom copper (.GTL/.GBL), top/bottom solder mask
(.GTS/.GBS), top silkscreen (.GTO), board outline (.GKO), and the Excellon
drill file (.DRL) — straight from the Board model, so what the PCB tab shows
is what gets quoted/fabbed.

Fidelity notes:
- The bottom GND pour (`board.has_ground_pour`) is emitted for real: a solid
  copper flash inset from the outline, then clearance voids (LPC) around all
  non-GND bottom copper, then the true copper redrawn (LPD). GND pads/vias
  merge into the pour — matching the router, which never draws GND traces.
- Refdes text is not emitted (no stroke font yet); silkscreen carries body
  outlines + pin-1 dots.
- Footprint geometry is only as good as its confidence level; the API's
  export gate (manufacturability report) is the guard, not this module.

Coordinates: the Board stores canvas coordinates (y grows downward, exactly
as PcbCanvas renders them). Gerber/Excellon Y grows upward, so every emitted
Y is flipped (y' = board.height - y). Pad rotation replicates the SVG
`rotate()` the canvas applies (clockwise on screen).
"""

from __future__ import annotations

import io
import zipfile

from app.models import Board, FootprintPad, PlacedComponent, Project
from app.services import board_shape

# Nets carried by the bottom copper pour — must match routing.py's skip set.
GND_NETS = {"GND", "VSS", "AGND", "DGND"}

ANNULAR = 0.3          # THT annular ring per side, mm (pad dia = drill + 2*ANNULAR)
CLEARANCE = 0.25       # pour-to-copper isolation, mm
POUR_MARGIN = 0.3      # pour inset from the board edge, mm
MASK_EXPANSION = 0.05  # solder-mask opening growth per side, mm
SILK_WIDTH = 0.15      # silkscreen line width, mm
EDGE_WIDTH = 0.1       # outline line width, mm


def _rot(x: float, y: float, deg: int) -> tuple[float, float]:
    """Rotate a footprint-local point the way the canvas SVG does (clockwise
    on screen, y-down coords). deg is 0/90/180/270."""
    if deg == 90:
        return -y, x
    if deg == 180:
        return -x, -y
    if deg == 270:
        return y, -x
    return x, y


def _pad_abs(pc: PlacedComponent, pad: FootprintPad) -> tuple[float, float, float, float]:
    """Absolute canvas (x, y) of a pad plus its rotated (w, h)."""
    rx, ry = _rot(pad.x, pad.y, pc.rotation)
    w, h = pad.width, pad.height or pad.width
    if pc.rotation in (90, 270):
        w, h = h, w
    return pc.x + rx, pc.y + ry, w, h


def _pad_net(project: Project) -> dict[tuple[str, str], str]:
    """(refdes, pin_number) -> net name, from the IR."""
    out: dict[tuple[str, str], str] = {}
    for net in project.ir.nets:
        for refdes, pin in net.pins:
            out[(refdes, str(pin))] = net.name
    return out


class _GerberLayer:
    """Buffers draw ops, assigns aperture D-codes, emits a valid RS-274X file."""

    def __init__(self) -> None:
        self._apertures: dict[tuple, int] = {}
        self._ops: list[str] = []
        self._polarity = "D"
        self._current_d: int | None = None

    # -- aperture management -------------------------------------------------
    def _dcode(self, key: tuple) -> int:
        if key not in self._apertures:
            self._apertures[key] = 10 + len(self._apertures)
        return self._apertures[key]

    def _select(self, key: tuple, polarity: str) -> None:
        if polarity != self._polarity:
            self._ops.append(f"%LP{polarity}*%")
            self._polarity = polarity
        d = self._dcode(key)
        if d != self._current_d:
            self._ops.append(f"D{d}*")
            self._current_d = d

    @staticmethod
    def _c(v: float) -> int:
        """mm -> 4.6 integer coordinate."""
        return round(v * 1_000_000)

    # -- drawing primitives (canvas coords; flip_h = board height) -----------
    def flash(self, shape: str, w: float, h: float, x: float, y: float,
              flip_h: float, polarity: str = "D") -> None:
        if shape == "circle":
            key = ("C", round(w, 6))
        elif shape == "rounded":
            key = ("O", round(w, 6), round(h, 6))
        else:
            key = ("R", round(w, 6), round(h, 6))
        self._select(key, polarity)
        self._ops.append(f"X{self._c(x)}Y{self._c(flip_h - y)}D03*")

    def line(self, width: float, x1: float, y1: float, x2: float, y2: float,
             flip_h: float, polarity: str = "D") -> None:
        self._select(("C", round(width, 6)), polarity)
        self._ops.append(f"X{self._c(x1)}Y{self._c(flip_h - y1)}D02*")
        self._ops.append(f"X{self._c(x2)}Y{self._c(flip_h - y2)}D01*")

    def polygon_fill(self, ring, flip_h: float, polarity: str = "D") -> None:
        """A filled REGION bounded by  (RS-274X G36/G37).

        A flash cannot express an arbitrary outline, so a polygon pour is a
        region: the contour is walked once and closed back to its first point,
        which is what the format requires and what every CAM parser expects.
        Regions need no aperture, but a D-code must still be selected for the
        polarity block to be well formed."""
        if len(ring) < 3:
            return
        self._select(("C", 0.01), polarity)
        self._ops.append("G36*")
        x0, y0 = ring[0]
        self._ops.append(f"X{self._c(x0)}Y{self._c(flip_h - y0)}D02*")
        for x, y in ring[1:]:
            self._ops.append(f"X{self._c(x)}Y{self._c(flip_h - y)}D01*")
        self._ops.append(f"X{self._c(x0)}Y{self._c(flip_h - y0)}D01*")
        self._ops.append("G37*")

    def circle_outline(self, width: float, cx: float, cy: float, radius: float,
                       flip_h: float) -> None:
        """Full circle as two 180° arcs (single 360° arcs trip some CAM tools)."""
        self._select(("C", round(width, 6)), polarity="D")
        gy = flip_h - cy
        west, east = self._c(cx - radius), self._c(cx + radius)
        yc = self._c(gy)
        i = self._c(radius)
        self._ops.append("G75*")
        self._ops.append(f"X{west}Y{yc}D02*")
        self._ops.append(f"G03X{east}Y{yc}I{i}J0D01*")
        self._ops.append(f"G03X{west}Y{yc}I{-i}J0D01*")
        self._ops.append("G01*")

    def render(self) -> str:
        out = [
            "%TF.GenerationSoftware,Synqit,voltron,0.1*%",
            "%FSLAX46Y46*%",
            "%MOMM*%",
            "G01*",
            "%LPD*%",
        ]
        for key, d in self._apertures.items():
            if key[0] == "C":
                out.append(f"%ADD{d}C,{key[1]:.6f}*%")
            elif key[0] == "O":
                out.append(f"%ADD{d}O,{key[1]:.6f}X{key[2]:.6f}*%")
            else:
                out.append(f"%ADD{d}R,{key[1]:.6f}X{key[2]:.6f}*%")
        out.extend(self._ops)
        out.append("M02*")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Layer builders
# ---------------------------------------------------------------------------

def _tht_outer(pad: FootprintPad) -> float:
    return pad.width + 2 * ANNULAR


def _copper_items(project: Project, board: Board, layer: str) -> list[dict]:
    """Everything that is copper on `layer`, with its net, as flash/line ops."""
    nets = _pad_net(project)
    items: list[dict] = []
    for t in board.traces:
        if t.layer != layer:
            continue
        for s in t.segments:
            items.append({"kind": "line", "w": t.width, "p": (s.x1, s.y1, s.x2, s.y2), "net": t.net})
    for v in board.vias:
        items.append({"kind": "flash", "shape": "circle", "w": v.diameter, "h": v.diameter,
                      "p": (v.x, v.y), "net": v.net})
    for pc in board.components:
        for pad in pc.footprint.pads:
            x, y, w, h = _pad_abs(pc, pad)
            net = nets.get((pc.refdes, pad.pin_number), "")
            if pad.pad_type == "tht":
                d = _tht_outer(pad)
                items.append({"kind": "flash", "shape": "circle", "w": d, "h": d, "p": (x, y), "net": net})
            elif pc.layer == layer:
                items.append({"kind": "flash", "shape": pad.shape, "w": w, "h": h, "p": (x, y), "net": net})
    return items


def _emit_copper_item(g: _GerberLayer, it: dict, H: float, grow: float = 0.0,
                      polarity: str = "D") -> None:
    if it["kind"] == "line":
        x1, y1, x2, y2 = it["p"]
        g.line(it["w"] + 2 * grow, x1, y1, x2, y2, H, polarity)
    else:
        x, y = it["p"]
        g.flash(it["shape"], it["w"] + 2 * grow, it["h"] + 2 * grow, x, y, H, polarity)


def copper_layer(project: Project, board: Board, layer: str,
                 pour_regions: list[tuple[float, float, float, float]] | None = None,
                 fiducials: list[tuple[float, float]] | None = None) -> str:
    """One copper layer. `pour_regions` — (cx, cy, w, h) rectangles — overrides
    where the ground pour is filled; a PANEL pours each board slot separately,
    or one flash would bridge the routing slots between boards and flood the
    breakaway rails with copper."""
    g = _GerberLayer()
    H = board.height
    items = _copper_items(project, board, layer)
    pour = layer == "bottom" and board.has_ground_pour
    if pour:
        # 1. Solid pour, inset from the edge. A flashed aperture is a filled
        #    shape, so a rect/circle flash IS the pour region.
        if pour_regions is not None:
            for cx, cy, w, h in pour_regions:
                g.flash("rect", w - 2 * POUR_MARGIN, h - 2 * POUR_MARGIN, cx, cy, H)
        elif board.shape == "round":
            d = board.width - 2 * POUR_MARGIN
            g.flash("circle", d, d, board.width / 2, board.width / 2, H)
        elif board.shape == "polygon" and board_shape.ring_for_board(board):
            g.polygon_fill(
                _inset_ring(board_shape.ring_for_board(board), POUR_MARGIN), H)
        else:
            g.flash("rect", board.width - 2 * POUR_MARGIN, board.height - 2 * POUR_MARGIN,
                    board.width / 2, board.height / 2, H)
        # 2. Isolation voids around everything that must NOT touch the pour.
        for it in items:
            if it["net"].upper() not in GND_NETS:
                _emit_copper_item(g, it, H, grow=CLEARANCE, polarity="C")
    # 3. The actual copper (over the voids; GND merges into the pour).
    for it in items:
        _emit_copper_item(g, it, H)
    # 4. Panel fiducials: a bare copper dot the pick-and-place camera locates
    #    the panel by. Drawn last so it sits over any pour void.
    for fx, fy in (fiducials or ()):
        from app.services import panelize
        g.flash("circle", panelize.FIDUCIAL_D, panelize.FIDUCIAL_D, fx, fy, H)
    return g.render()


def plane_layer(project: Project, board: Board, plane_net: str) -> str:
    """An INNER-layer plane (4-layer stack-up): a solid pour of `plane_net`
    over the outline, with clearance voids around every plated hole (via /
    THT pad) that belongs to a different net. Only through-copper reaches an
    inner layer, so SMD pads and top/bottom traces are irrelevant here; the
    plane's own vias/holes simply merge into the fill. Same CAM primitives as
    the bottom GND pour."""
    g = _GerberLayer()
    H = board.height
    if board.shape == "round":
        d = board.width - 2 * POUR_MARGIN
        g.flash("circle", d, d, board.width / 2, board.width / 2, H)
    elif board.shape == "polygon" and board_shape.ring_for_board(board):
        g.polygon_fill(_inset_ring(board_shape.ring_for_board(board), POUR_MARGIN), H)
    else:
        g.flash("rect", board.width - 2 * POUR_MARGIN, board.height - 2 * POUR_MARGIN,
                board.width / 2, board.height / 2, H)
    nets = _pad_net(project)
    # Voids (clear polarity) around foreign through-copper.
    for v in board.vias:
        if v.net != plane_net:
            g.flash("circle", v.diameter + 2 * CLEARANCE, v.diameter + 2 * CLEARANCE, v.x, v.y, H, "C")
    for pc in board.components:
        for pad in pc.footprint.pads:
            if pad.pad_type != "tht":
                continue
            net = nets.get((pc.refdes, pad.pin_number), "")
            if net != plane_net:
                x, y, _, _ = _pad_abs(pc, pad)
                d = _tht_outer(pad) + 2 * CLEARANCE
                g.flash("circle", d, d, x, y, H, "C")
    # The plane's own through-copper, drawn dark again over the voids region.
    for v in board.vias:
        if v.net == plane_net:
            g.flash("circle", v.diameter, v.diameter, v.x, v.y, H)
    for pc in board.components:
        for pad in pc.footprint.pads:
            if pad.pad_type == "tht" and nets.get((pc.refdes, pad.pin_number), "") == plane_net:
                x, y, _, _ = _pad_abs(pc, pad)
                d = _tht_outer(pad)
                g.flash("circle", d, d, x, y, H)
    return g.render()


def mask_layer(board: Board, layer: str,
               fiducials: list[tuple[float, float]] | None = None) -> str:
    """Negative layer: a drawn shape is an OPENING in the solder mask.
    SMD pads open on their side, THT pads on both sides; vias stay tented.
    A panel fiducial needs an opening twice its copper diameter, or the camera
    sees mask instead of the bare dot it is looking for."""
    g = _GerberLayer()
    H = board.height
    for pc in board.components:
        for pad in pc.footprint.pads:
            x, y, w, h = _pad_abs(pc, pad)
            if pad.pad_type == "tht":
                d = _tht_outer(pad) + 2 * MASK_EXPANSION
                g.flash("circle", d, d, x, y, H)
            elif pc.layer == layer:
                g.flash(pad.shape, w + 2 * MASK_EXPANSION, h + 2 * MASK_EXPANSION, x, y, H)
    for fx, fy in (fiducials or ()):
        from app.services import panelize
        d = 2 * panelize.FIDUCIAL_D
        g.flash("circle", d, d, fx, fy, H)
    return g.render()


def silk_top(board: Board) -> str:
    """Body outlines + pin-1 dots for top-side parts (no refdes text yet)."""
    g = _GerberLayer()
    H = board.height
    for pc in board.components:
        if pc.layer != "top":
            continue
        w2, h2 = pc.footprint.width / 2, pc.footprint.height / 2
        corners = [(-w2, -h2), (w2, -h2), (w2, h2), (-w2, h2)]
        pts = [_rot(cx, cy, pc.rotation) for cx, cy in corners]
        for i in range(4):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % 4]
            g.line(SILK_WIDTH, pc.x + x1, pc.y + y1, pc.x + x2, pc.y + y2, H)
        fp = pc.footprint
        if fp.pin1_pad:
            p1 = next((p for p in fp.pads if p.pin_number == fp.pin1_pad), None)
            if p1 and (p1.x or p1.y):
                # Dot just outboard of pin 1, past the pad.
                dx, dy = _rot(p1.x * 1.35, p1.y * 1.35, pc.rotation)
                g.flash("circle", 0.4, 0.4, pc.x + dx, pc.y + dy, H)
    return g.render()


def _inset_ring(ring, inset: float):
    """Shrink a ring toward its centroid by roughly `inset` mm — enough for a
    pour that must not run off the milled edge. Exact offsetting needs a
    polygon library; scaling about the centroid is conservative for the convex
    outlines a board actually uses, and a pour that stops slightly short is a
    cosmetic loss, never a short."""
    cx, cy = board_shape.centroid(ring)
    x0, y0, x1, y1 = board_shape.bbox(ring)
    span = max(x1 - x0, y1 - y0) / 2.0
    if span <= inset:
        return list(ring)
    k = (span - inset) / span
    return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in ring]


def edge_cuts(board: Board) -> str:
    g = _GerberLayer()
    H = board.height
    ring = board_shape.ring_for_board(board) if board.shape == "polygon" else None
    if ring is not None:
        # The fab mills exactly this ring. It is the same list the placer and
        # the router used, so what is manufactured is what was verified.
        for i in range(len(ring)):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % len(ring)]
            g.line(EDGE_WIDTH, x1, y1, x2, y2, H)
    elif board.shape == "round":
        r = board.width / 2
        g.circle_outline(EDGE_WIDTH, r, r, r, H)
    else:
        w, h = board.width, board.height
        for x1, y1, x2, y2 in ((0, 0, w, 0), (w, 0, w, h), (w, h, 0, h), (0, h, 0, 0)):
            g.line(EDGE_WIDTH, x1, y1, x2, y2, H)
    return g.render()


def excellon_drill(board: Board) -> str:
    """All plated holes: vias + THT pads. Metric, decimal coordinates."""
    holes: list[tuple[float, float, float]] = []  # (drill, x, y) canvas coords
    for v in board.vias:
        holes.append((v.drill, v.x, v.y))
    for pc in board.components:
        for pad in pc.footprint.pads:
            if pad.pad_type == "tht":
                x, y, _, _ = _pad_abs(pc, pad)
                holes.append((pad.width, x, y))
    tools: dict[float, int] = {}
    for d, _, _ in holes:
        key = round(d, 3)
        if key not in tools:
            tools[key] = len(tools) + 1
    out = ["M48", ";GenerationSoftware,Synqit,voltron,0.1", "FMAT,2", "METRIC,TZ"]
    for d, n in tools.items():
        out.append(f"T{n}C{d:.3f}")
    out.extend(["%", "G90", "G05"])
    H = board.height
    for d, n in tools.items():
        out.append(f"T{n}")
        for drill, x, y in holes:
            if round(drill, 3) == d:
                out.append(f"X{x:.3f}Y{H - y:.3f}")
    out.append("M30")
    return "\n".join(out) + "\n"


def fab_zip(project: Project) -> bytes:
    """The full CAM package as a zip — the file you upload to a fab's quote page."""
    board = project.board
    if board is None or not board.components:
        raise ValueError("No PCB layout yet — generate the board before exporting Gerbers.")
    name = f"synqit-{project.id}"
    planes = list(getattr(board, "planes", None) or [])
    layers = int(getattr(board, "layer_count", 2) or 2)
    files = {
        f"{name}.GTL": copper_layer(project, board, "top"),
        f"{name}.GBL": copper_layer(project, board, "bottom"),
        f"{name}.GTS": mask_layer(board, "top"),
        f"{name}.GBS": mask_layer(board, "bottom"),
        f"{name}.GTO": silk_top(board),
        f"{name}.GKO": edge_cuts(board),
        f"{name}.DRL": excellon_drill(board),
    }
    # 4-layer: inner planes as G2L / G3L (the extensions JLCPCB/PCBWay read as
    # inner copper 1 / 2), each a solid pour of its net.
    inner_lines = ""
    if layers > 2:
        ext = {"In1": "G2L", "In2": "G3L"}
        for pl in planes:
            files[f"{name}.{ext.get(pl.layer, 'G2L')}"] = plane_layer(project, board, pl.net)
        inner_lines = "".join(
            f"{ext.get(pl.layer, 'G2L')}      inner copper {pl.layer} — solid {pl.net} plane\n" for pl in planes
        )
    files["README.txt"] = (
        f"Synqit fabrication export ({layers}-layer)\n"
        f"Board: {board.width} x {board.height} mm ({board.shape})\n\n"
        "GTL/GBL  copper top/bottom (bottom includes the GND pour)\n"
        + inner_lines +
        "GTS/GBS  solder mask top/bottom\n"
        "GTO      silkscreen top\n"
        "GKO      board outline\n"
        "DRL      Excellon drill (plated)\n\n"
        + ("Order as a 4-layer board (stack-up: signal / "
           + " / ".join(pl.net for pl in planes) + " / signal).\n" if layers > 2 else "")
        + "Upload this zip unchanged to JLCPCB / PCBWay's quote page.\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fname, body in files.items():
            z.writestr(fname, body)
    return buf.getvalue()


def panel_edge_cuts(panel) -> str:
    """Panel outline plus the internal cuts that free each board.

    V-scored panels have NO internal outline: the score lines are a separate
    layer (a groove, not a cut), and drawing them here would tell the fab to
    route right through the panel. Mouse-bitten panels DO get a routed slot
    around every board, broken by the tabs that hold it.
    """
    g = _GerberLayer()
    H = panel.height
    w, h = panel.width, panel.height
    for x1, y1, x2, y2 in ((0, 0, w, 0), (w, 0, w, h), (w, h, 0, h), (0, h, 0, 0)):
        g.line(EDGE_WIDTH, x1, y1, x2, y2, H)

    if panel.method != "mouse_bites":
        return g.render()

    half = 0.5 * 5.0  # half a tab, the span left uncut where the bridge sits
    for ox, oy in panel.origins:
        bw, bh = panel.board_w, panel.board_h
        for (sx, sy, ex, ey), horiz in (
            ((ox, oy, ox + bw, oy), True),
            ((ox, oy + bh, ox + bw, oy + bh), True),
            ((ox, oy, ox, oy + bh), False),
            ((ox + bw, oy, ox + bw, oy + bh), False),
        ):
            # Cut the edge in the clear spans between this edge's two tabs.
            length = bw if horiz else bh
            breaks = sorted((length / 3, 2 * length / 3))
            marks = [0.0]
            for b in breaks:
                marks.extend((b - half, b + half))
            marks.append(length)
            for i in range(0, len(marks) - 1, 2):
                a, b = marks[i], marks[i + 1]
                if b - a <= 0.01:
                    continue
                if horiz:
                    g.line(EDGE_WIDTH, sx + a, sy, sx + b, sy, H)
                else:
                    g.line(EDGE_WIDTH, sx, sy + a, sx, sy + b, H)
    return g.render()


def panel_vscore_layer(panel) -> str:
    """The V-score lines, on their own mechanical layer.

    Fabs want these separated from the outline: a score is a groove cut part
    way through from both faces, not a route. Every line runs the FULL width or
    height of the panel — that is what a scoring blade does, and it is why a
    non-rectangular board cannot be scored at all.
    """
    g = _GerberLayer()
    H = panel.height
    for x1, y1, x2, y2 in panel.v_scores:
        g.line(0.1, x1, y1, x2, y2, H)
    return g.render()


def panel_drill(project: Project, panel, pboard: Board) -> str:
    """Excellon for the panel: every board's plated holes, plus the panel's own
    tooling holes and any mouse-bite perforations."""
    base = excellon_drill(pboard)
    extra: list[tuple[float, float, float]] = []
    for x, y, d in panel.tooling_holes:
        extra.append((d, x, y))
    for tab in panel.tabs:
        for hx, hy in tab.holes:
            extra.append((0.5, hx, hy))
    if not extra:
        return base

    # Append as additional tools rather than rebuilding: keeps the board holes
    # byte-identical to the single-board export.
    lines = base.rstrip().split("\n")
    assert lines[-1] == "M30"
    lines = lines[:-1]
    used = {ln.split("C")[1] for ln in lines if ln.startswith("T") and "C" in ln}
    next_tool = 1 + max(
        (int(ln[1:].split("C")[0]) for ln in lines if ln.startswith("T") and "C" in ln),
        default=0,
    )
    by_d: dict[float, list[tuple[float, float]]] = {}
    for d, x, y in extra:
        by_d.setdefault(round(d, 3), []).append((x, y))

    header_end = lines.index("%")
    body = lines[header_end:]
    head = lines[:header_end]
    H = panel.height
    for d, pts in by_d.items():
        if f"{d:.3f}" in used:
            continue
        head.append(f"T{next_tool}C{d:.3f}")
        body.append(f"T{next_tool}")
        for x, y in pts:
            body.append(f"X{x:.3f}Y{H - y:.3f}")
        next_tool += 1
    return "\n".join(head + body + ["M30"]) + "\n"


def panel_zip(project: Project, spec) -> bytes:
    """The CAM package for a PANEL — the file a fab quotes and builds.

    Same writers as the single-board export, driven by a synthetic panel Board,
    so the two can never drift apart.
    """
    from app.services import panelize

    board = project.board
    if board is None or not board.components:
        raise ValueError("No PCB layout yet — generate the board before exporting a panel.")

    panel = panelize.derive(board, spec)
    pboard = panelize.panel_board(board, panel)
    name = f"synqit-{project.id}-panel-{panel.cols}x{panel.rows}"

    files = {
        f"{name}.GTL": copper_layer(project, pboard, "top", fiducials=panel.fiducials),
        f"{name}.GBL": copper_layer(project, pboard, "bottom",
                                    pour_regions=panelize.pour_regions(panel)),
        f"{name}.GTS": mask_layer(pboard, "top", fiducials=panel.fiducials),
        f"{name}.GBS": mask_layer(pboard, "bottom"),
        f"{name}.GTO": silk_top(pboard),
        f"{name}.GKO": panel_edge_cuts(panel),
        f"{name}.DRL": panel_drill(project, panel, pboard),
    }
    if panel.v_scores:
        files[f"{name}.GM1"] = panel_vscore_layer(panel)

    method = ("V-scored (snap apart along the grooves)" if panel.method == "v_score"
              else "routed with mouse-bite tabs (break the tabs, then sand the nubs)")
    files["README.txt"] = (
        f"Synqit PANEL export\n"
        f"Panel:  {panel.width:.2f} x {panel.height:.2f} mm\n"
        f"Boards: {panel.cols} x {panel.rows} = {panel.boards} of "
        f"{panel.board_w:.2f} x {panel.board_h:.2f} mm\n"
        f"Separation: {method}\n"
        f"Rails:  {panel.rail:g} mm (tooling holes + fiducials)\n"
        f"Panel utilisation: {panel.utilisation * 100:.0f}% finished board\n\n"
        "GTL/GBL  copper top/bottom (the pour is filled per board, never across the slots)\n"
        "GTS/GBS  solder mask top/bottom\n"
        "GTO      silkscreen top\n"
        "GKO      panel outline"
        + (" + the routed slots around each board, broken by the tabs\n"
           if panel.method == "mouse_bites" else " (the panel edge only)\n")
        + ("GM1      V-SCORE lines — grooves, NOT routed cuts. Score both faces.\n"
           if panel.v_scores else "")
        + "DRL      Excellon drill: board holes, panel tooling holes"
        + (", and the mouse-bite perforations\n" if panel.tabs else "\n")
        + f"\nORDER AS: one {panel.width:.0f} x {panel.height:.0f} mm panel, "
          f"{panel.boards} up, {'V-cut' if panel.method == 'v_score' else 'tab-routed'}.\n"
          "Quantities on the fab's quote page are PANELS, not boards.\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fname, body in files.items():
            z.writestr(fname, body)
    return buf.getvalue()
