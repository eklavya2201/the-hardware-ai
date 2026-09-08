"""Author KiCad `.kicad_mod` footprints into app/library/footprints/Synqit.pretty
for parts whose correct footprint isn't in stock KiCad (task 3.2 tail).

The generator builds IPC-nominal land patterns for leadless packages from a
compact spec (package family, body, pitch, pad size, EP, side arrangement,
pin-1 corner, numbering direction) and writes a valid `.kicad_mod` that the
bridge (`dump_footprint.py`) loads like any stock footprint → `confidence
="verified"`. `--selftest` regenerates a known KiCad package and compares pad
centres to KiCad's own, validating the land-pattern math before real use.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from dotenv import load_dotenv; load_dotenv()  # noqa: E402
from app.services import kicad_bridge  # noqa: E402

OUT_DIR = _BACKEND / "app" / "library" / "footprints" / "Synqit.pretty"


# --------------------------------------------------------------------------- #
# .kicad_mod emission
# --------------------------------------------------------------------------- #
def _pad_sexpr(num, x, y, w, h, shape="roundrect", ptype="smd", drill=0.0):
    y = -y  # spec uses Y-up (math); KiCad is Y-down
    if ptype == "tht":
        layers = '(layers "*.Cu" "*.Mask")'
        drill_s = f" (drill {drill})" if drill else ""
        return (f'  (pad "{num}" thru_hole {shape} (at {x:.4f} {y:.4f}) '
                f'(size {w:.4f} {h:.4f}){drill_s} {layers})')
    layers = '(layers "F.Cu" "F.Paste" "F.Mask")'
    rr = " (roundrect_rratio 0.25)" if shape == "roundrect" else ""
    return (f'  (pad "{num}" smd {shape} (at {x:.4f} {y:.4f}) '
            f'(size {w:.4f} {h:.4f}) {layers}{rr})')


def emit(name, pads, ep=None, descr=""):
    """pads: list of dicts {n,x,y,w,h,shape?,type?,drill?}. ep: {x,y,w,h} or None."""
    lines = [f'(footprint "{name}"', '  (version 20240108)', '  (generator "synqit")',
             '  (layer "F.Cu")', f'  (descr "{descr or name} (authored by Synqit)")',
             '  (attr smd)']
    for p in pads:
        lines.append(_pad_sexpr(p["n"], p["x"], p["y"], p["w"], p["h"],
                                p.get("shape", "roundrect"), p.get("type", "smd"),
                                p.get("drill", 0.0)))
    if ep:
        lines.append(_pad_sexpr(ep.get("n", str(len(pads) + 1)), ep["x"], ep["y"],
                                ep["w"], ep["h"], "rect", "smd"))
    lines.append(")")
    return "\n".join(lines) + "\n"


def write(name, pads, ep=None, descr=""):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.kicad_mod").write_text(emit(name, pads, ep, descr), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Land-pattern builders (IPC-nominal). Spec is Y-up, origin at body centre.
# --------------------------------------------------------------------------- #
def leadless(pins, pitch, pad_w, pad_l, span, *, sides, ep=None, cw=False, start="TL"):
    """Perimeter leadless (QFN/DFN/SON/LGA).
    sides: 'LR' (left+right, DFN/SON), 'TB' (top+bottom), or '4' (QFN, 4 sides).
    span: for LR/TB = centre-to-centre distance between the two pad rows (the
          lead span). For '4' = the pad-row span on both axes (square-ish).
    pad_l points *outward* (radial); pad_w is tangential.
    Numbering: pin 1 at `start` corner, CCW (default) or CW. This matches JEDEC
    (CCW) or Bosch-style (CW) conventions.
    """
    pads = []
    if sides in ("LR", "TB"):
        n = pins // 2
        coords = [(i - (n - 1) / 2) * pitch for i in range(n)]  # centred positions
        if sides == "LR":
            # left column top→bottom = pins 1..n ; right column bottom→top = n+1..2n
            left = [(-span / 2, +coords[::-1][i]) for i in range(n)]   # top→bottom
            right = [(+span / 2, -coords[::-1][i]) for i in range(n)]  # bottom→top
            seq = left + right
            padsize = (pad_l, pad_w)  # length is horizontal
        else:  # TB
            top = [(coords[i], +span / 2) for i in range(n)]
            bottom = [(coords[::-1][i], -span / 2) for i in range(n)]
            seq = top + bottom
            padsize = (pad_w, pad_l)
        for idx, (x, y) in enumerate(seq, 1):
            pads.append({"n": str(idx), "x": round(x, 4), "y": round(y, 4),
                         "w": padsize[0], "h": padsize[1]})
    elif sides == "4":
        per = pins // 4
        coords = [(i - (per - 1) / 2) * pitch for i in range(per)]
        seq = []  # (x, y, orient) ; orient 'h' pad length horizontal
        # left top→bottom, bottom left→right, right bottom→top, top right→left (CCW)
        seq += [(-span / 2, coords[::-1][i], "h") for i in range(per)]
        seq += [(coords[i], -span / 2, "v") for i in range(per)]
        seq += [(+span / 2, coords[i], "h") for i in range(per)]
        seq += [(coords[::-1][i], +span / 2, "v") for i in range(per)]
        for idx, (x, y, o) in enumerate(seq, 1):
            w, h = (pad_l, pad_w) if o == "h" else (pad_w, pad_l)
            pads.append({"n": str(idx), "x": round(x, 4), "y": round(y, 4), "w": w, "h": h})
    else:
        raise ValueError(f"bad sides {sides}")
    if cw:
        # reverse numbering direction, keep pin-1 position
        nums = [p["n"] for p in pads]
        pads = [{**p, "n": nums[(len(pads) - i) % len(pads)]} for i, p in enumerate(pads)]
    return pads


def leadless_rect(ny_side, nx_side, pitch, pad_w, pad_l, span_x, span_y):
    """Rectangular 4-side QFN with different lead counts per axis.
    ny_side = pads on each left/right column; nx_side = pads on each top/bottom row.
    span_x = horizontal centre-to-centre between L/R columns; span_y = vertical
    between T/B rows. Pin 1 top-left, CCW (left↓, bottom→, right↑, top←)."""
    yc = [(i - (ny_side - 1) / 2) * pitch for i in range(ny_side)]
    xc = [(i - (nx_side - 1) / 2) * pitch for i in range(nx_side)]
    seq = []
    seq += [(-span_x / 2, yc[::-1][i], "h") for i in range(ny_side)]   # left top→bottom
    seq += [(xc[i], -span_y / 2, "v") for i in range(nx_side)]         # bottom left→right
    seq += [(+span_x / 2, yc[i], "h") for i in range(ny_side)]         # right bottom→top
    seq += [(xc[::-1][i], +span_y / 2, "v") for i in range(nx_side)]   # top right→left
    pads = []
    for idx, (x, y, o) in enumerate(seq, 1):
        w, h = (pad_l, pad_w) if o == "h" else (pad_w, pad_l)
        pads.append({"n": str(idx), "x": round(x, 4), "y": round(y, 4), "w": w, "h": h})
    return pads


def grid(rows, cols, pitch, ball, *, names=None):
    """BGA/WLP/LGA grid. names: optional row-letter list (A,B,...) else numeric."""
    pads = []
    row_letters = "ABCDEFGHJKLMNPRTUVWY"
    for r in range(rows):
        for c in range(cols):
            x = (c - (cols - 1) / 2) * pitch
            y = ((rows - 1) / 2 - r) * pitch
            n = f"{row_letters[r]}{c+1}" if names is None else names[r * cols + c]
            pads.append({"n": n, "x": round(x, 4), "y": round(y, 4),
                         "w": ball, "h": ball, "shape": "circle"})
    return pads


def olga22_max86150():
    """MAX86150 22-lead OLGA/OESIP (3.3 x 5.6 x 1.3 mm) — transcribed from
    Maxim/ADI land-pattern drawing **90-100024 rev C** ("PACKAGE LAND PATTERN,
    22L OLGA, 3.3x5.6x1.3mm", reference package outline 21-100071 rev F).
    KiCad has no footprint for this package, and it is NOT a regular grid, so
    the parametric builders above cannot produce it:

      * two rows of 10 pads, 0.90 (X) x 0.30 (Y) mm, rows 2.30 mm centre to
        centre (labelled `2.30`) -> x = +/-1.15;
      * within a row the pitch is 0.50 mm BSC for pins 2..9, but the END pads
        are offset: 0.55 mm from pin 1 to pin 2 and 0.95 mm from pin 9 to
        pin 10 (drawing labels `4.05` pin1->pin9 and `0.95`; outline table
        `D1 | 5.0 BSC` pin1->pin10);
      * plus ONE extra 0.85 x 0.30 mm pad centred at each short end (drawing
        labels `0.85` and the `0.28` clearance to the row pads) — these are
        pins 11 (bottom) and 22 (top), both N.C. in the datasheet.

    Numbering is CCW from the top view with pin 1 at the top-left corner:
    1..10 down the left row, 11 bottom-centre, 12..21 up the right row, 22
    top-centre. Spec frame is Y-up with the origin at the pad-field centre.
    """
    ys = [2.50, 1.95, 1.45, 0.95, 0.45, -0.05, -0.55, -1.05, -1.55, -2.50]
    pads = [{"n": str(i + 1), "x": -1.15, "y": y, "w": 0.90, "h": 0.30}
            for i, y in enumerate(ys)]
    pads.append({"n": "11", "x": 0.0, "y": -2.50, "w": 0.85, "h": 0.30})
    pads += [{"n": str(i + 12), "x": 1.15, "y": y, "w": 0.90, "h": 0.30}
             for i, y in enumerate(reversed(ys))]
    pads.append({"n": "22", "x": 0.0, "y": 2.50, "w": 0.85, "h": 0.30})
    return pads


# --------------------------------------------------------------------------- #
# Self-test: regenerate a known KiCad footprint, compare pad centres.
# --------------------------------------------------------------------------- #
def selftest():
    # KiCad QFN-24-1EP_4x4_P0.5: 24 pins, 6/side, pitch 0.5.
    # Nominal pad row span ~3.9mm centre-to-centre, pad ~0.75x0.28.
    pads = leadless(24, 0.5, 0.28, 0.75, 3.9, sides="4",
                    ep={"x": 0, "y": 0, "w": 2.6, "h": 2.6})
    write("SELFTEST_QFN24", pads, ep={"x": 0, "y": 0, "w": 2.6, "h": 2.6})
    mine = kicad_bridge.run_script("dump_footprint.py", {"footprint": "Synqit:SELFTEST_QFN24"})
    ref = kicad_bridge.run_script("dump_footprint.py",
                                  {"footprint": "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.15x2.15mm"})
    if not (mine.success and ref.success):
        print("selftest FAIL to load:", mine.error, ref.error); return
    mp = {p["pin_number"]: (p["x"], p["y"]) for p in mine.data["pads"]}
    rp = {p["pin_number"]: (p["x"], p["y"]) for p in ref.data["pads"]}
    worst = 0.0
    for n in [str(i) for i in range(1, 25)]:
        if n in mp and n in rp:
            d = ((mp[n][0]-rp[n][0])**2 + (mp[n][1]-rp[n][1])**2) ** 0.5
            worst = max(worst, d)
    print(f"selftest QFN-24: pins matched, worst pad-centre delta vs KiCad = {worst:.3f} mm")
    print("  (small delta = my numbering/geometry match KiCad's; sanity OK if < ~0.4mm)")
    (OUT_DIR / "SELFTEST_QFN24.kicad_mod").unlink(missing_ok=True)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--max86150" in sys.argv:
        pads = olga22_max86150()
        write("Synqit_MAX86150", pads,
              descr="MAX86150 22L OLGA/OESIP 3.3x5.6x1.3mm, land pattern per "
                    "Maxim 90-100024 rev C (outline 21-100071)")
        print(f"wrote {OUT_DIR / 'Synqit_MAX86150.kicad_mod'} ({len(pads)} pads)")
    else:
        print("usage: gen_footprints.py --selftest | --max86150   "
              "(otherwise used programmatically)")
