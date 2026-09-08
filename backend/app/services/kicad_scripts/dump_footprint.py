"""Dump real footprint geometry from KiCad's stock libraries — task 3.2.

Runs INSIDE KiCad's python (has pcbnew), invoked by
`kicad_bridge.run_script("dump_footprint.py", {"footprint": "Lib:Name"})`.
Loads the named footprint from KiCad's installed footprint libraries and returns
exact pad positions / sizes / shapes + courtyard bbox as JSON, in the same
schema the backend `Footprint`/`FootprintPad` models use (mm, origin at the
footprint centre).

Coordinate note: KiCad's Y axis points DOWN; the backend board world is
bottom-left / Y-up (models.py). We negate Y here so pad positions match the
hand-coded footprints and render right-way-up.

Stdlib + pcbnew only (this is not backend/.venv). Written defensively across
KiCad majors — pad-shape/attribute enums have been renamed over versions, so we
resolve them by name with fallbacks and keep every pcbnew call in this one file
(version drift is contained here).
"""

import glob
import json
import os
import sys


def _out(path, ok, data=None, error=None):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"ok": ok, "data": data or {}, "error": error}, fh)


def _footprint_base_dirs():
    """Candidate directories that hold `*.pretty` libraries. Project-authored
    footprints (app/library/footprints/Synqit.pretty, task 3.2 tail) are searched
    FIRST so a curated `Synqit:` footprint wins over any stock name clash."""
    dirs = []
    # Project footprint library: this file is app/services/kicad_scripts/, so the
    # project library lives three levels up at app/library/footprints/.
    here = os.path.dirname(os.path.abspath(__file__))
    proj = os.path.normpath(os.path.join(here, "..", "..", "library", "footprints"))
    dirs.append(proj)
    # Explicit env overrides (KiCad sets a versioned one, e.g. KICAD9_FOOTPRINT_DIR).
    for key, val in os.environ.items():
        if key.startswith("KICAD") and key.endswith("FOOTPRINT_DIR") and val:
            dirs.append(val)
    # Derive from the running interpreter: <prefix>/bin/python -> <prefix>/share/kicad/footprints.
    prefix = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    dirs.append(os.path.join(prefix, "share", "kicad", "footprints"))
    # Common install locations.
    dirs += [
        "/usr/share/kicad/footprints",
        "/usr/local/share/kicad/footprints",
        "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
    ]
    dirs += glob.glob(r"C:\Program Files\KiCad\*\share\kicad\footprints")
    # De-dup, keep existing only.
    seen = set()
    out = []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _find_pretty(lib):
    """Resolve a library nickname (`Lib`) to its `.pretty` directory path."""
    for base in _footprint_base_dirs():
        cand = os.path.join(base, lib + ".pretty")
        if os.path.isdir(cand):
            return cand
    return None


def _shape_name(pcbnew, pad):
    """Map a pcbnew pad shape enum to our PadShape ('circle'|'rect'|'rounded')."""
    shape = pad.GetShape()
    circle = getattr(pcbnew, "PAD_SHAPE_CIRCLE", object())
    oval = getattr(pcbnew, "PAD_SHAPE_OVAL", object())
    roundrect = getattr(pcbnew, "PAD_SHAPE_ROUNDRECT", object())
    chamfered = getattr(pcbnew, "PAD_SHAPE_CHAMFERED_RECT", object())
    if shape == circle:
        return "circle"
    if shape in (oval, roundrect, chamfered):
        return "rounded"
    return "rect"  # RECT/RECTANGLE, TRAPEZOID, CUSTOM → treat as rectangular


def _is_tht(pcbnew, pad):
    attr = pad.GetAttribute()
    pth = getattr(pcbnew, "PAD_ATTRIB_PTH", None)
    npth = getattr(pcbnew, "PAD_ATTRIB_NPTH", None)
    return attr == pth or attr == npth


def _on_bottom_only(pcbnew, pad):
    try:
        f_cu = pcbnew.F_Cu
        b_cu = pcbnew.B_Cu
        return pad.IsOnLayer(b_cu) and not pad.IsOnLayer(f_cu)
    except Exception:
        return False


def main():
    out_path = sys.argv[2] if len(sys.argv) > 2 else "out.json"
    try:
        payload = json.load(open(sys.argv[1], encoding="utf-8")) if len(sys.argv) > 1 else {}
    except Exception as exc:
        _out(out_path, False, error="bad payload: %s" % exc)
        return

    fpid = (payload.get("footprint") or "").strip()
    if ":" not in fpid:
        _out(out_path, False, error="footprint id must be 'Lib:Name', got %r" % fpid)
        return
    lib, name = fpid.split(":", 1)

    try:
        import pcbnew
    except Exception as exc:
        _out(out_path, False, error="pcbnew import failed: %s" % exc)
        return

    pretty = _find_pretty(lib)
    if pretty is None:
        _out(out_path, False, error="footprint library not found for nickname %r" % lib)
        return

    try:
        fp = pcbnew.FootprintLoad(pretty, name)
    except Exception as exc:
        _out(out_path, False, error="FootprintLoad failed: %s" % exc)
        return
    if fp is None:
        _out(out_path, False, error="footprint %r not in library %s" % (name, pretty))
        return

    to_mm = pcbnew.ToMM
    origin = fp.GetPosition()  # loaded footprints sit near (0,0); subtract to be safe

    pads = []
    for pad in fp.Pads():
        number = pad.GetNumber()
        if not number:
            continue  # mechanical / mounting pad — no electrical pin
        pos = pad.GetPosition()
        x = to_mm(pos.x - origin.x)
        y = -to_mm(pos.y - origin.y)  # flip KiCad Y-down → backend Y-up
        if _is_tht(pcbnew, pad):
            # THT: our schema stores the drill diameter in `width`.
            try:
                drill = to_mm(pad.GetDrillSizeX())
            except Exception:
                drill = to_mm(pad.GetSizeX())
            pads.append({
                "pin_number": number, "x": round(x, 4), "y": round(y, 4),
                "width": round(drill or 0.6, 4), "height": 0.0,
                "shape": "circle", "pad_type": "tht",
                "layer": "top",
            })
        else:
            try:
                sx = to_mm(pad.GetSizeX())
                sy = to_mm(pad.GetSizeY())
            except Exception:
                size = pad.GetSize()
                sx, sy = to_mm(size.x), to_mm(size.y)
            pads.append({
                "pin_number": number, "x": round(x, 4), "y": round(y, 4),
                "width": round(sx, 4), "height": round(sy, 4),
                "shape": _shape_name(pcbnew, pad), "pad_type": "smd",
                "layer": "bottom" if _on_bottom_only(pcbnew, pad) else "top",
            })

    # Body / courtyard bounding box. Prefer the real courtyard outline; fall back
    # to the footprint bbox, then to the extent of the pads.
    width = height = 0.0
    courtyard_w = courtyard_h = 0.0
    try:
        crt = fp.GetCourtyard(pcbnew.F_CrtYd)
        if crt and not crt.IsEmpty():
            bb = crt.BBox()
            courtyard_w = to_mm(bb.GetWidth())
            courtyard_h = to_mm(bb.GetHeight())
    except Exception:
        pass
    try:
        bb = fp.GetBoundingBox(False, False)  # exclude invisible text
        width = to_mm(bb.GetWidth())
        height = to_mm(bb.GetHeight())
    except Exception:
        try:
            bb = fp.GetBoundingBox()
            width = to_mm(bb.GetWidth())
            height = to_mm(bb.GetHeight())
        except Exception:
            pass
    if width <= 0 or height <= 0:
        if pads:
            xs = [p["x"] for p in pads]
            ys = [p["y"] for p in pads]
            width = (max(xs) - min(xs)) + 1.0
            height = (max(ys) - min(ys)) + 1.0
    if courtyard_w <= 0:
        courtyard_w = width
    if courtyard_h <= 0:
        courtyard_h = height

    # Embedded 3D model references (task 3.3): the raw ${..._3DMODEL_DIR}/..step
    # filename(s) the footprint points at, for the STEP->GLB sweep.
    models = []
    try:
        for m in fp.Models():
            fn = getattr(m, "m_Filename", "") or ""
            if fn:
                models.append(fn)
    except Exception:
        pass

    data = {
        "name": fpid,
        "width": round(width, 4),
        "height": round(height, 4),
        "courtyard_w": round(courtyard_w, 4),
        "courtyard_h": round(courtyard_h, 4),
        "pads": pads,
        "models": models,
    }
    _out(out_path, True, data=data)


if __name__ == "__main__":
    main()
