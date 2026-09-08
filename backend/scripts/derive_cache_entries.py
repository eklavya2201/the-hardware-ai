"""Derive footprint-cache entries for stock KiCad footprints on a KiCad-less box.

The committed `footprint_cache.json` is a dump of KiCad's real footprint
geometry; a part whose `Lib:Name` has no entry falls back to parametric
geometry and is flagged unverified (PCB_R07). On a box without KiCad we can't
dump new entries — but for the *generator-scripted* KiCad families the geometry
is defined by a formula, and this script writes it in the cache's exact
convention:

  * chip R/C (0805/1206/2512)      — `footprint_gen` (IPC-7351 nominal, proven
                                     within 0.04 mm of the KiCad dump on 0603/0402)
  * TSSOP-8                        — `footprint_gen` dual gull-wing
  * DIP-4 / DIP-6 (W7.62)          — the DIP generator (pitch 2.54, row 7.62,
                                     drill 0.8) copied from the committed DIP-16
  * PinHeader_1x16 / 2x08 module   — the header generator (pitch 2.54, drill 1.0)
                                     copied from the committed 1x08
  * CP_Radial D5/D6.3/D8/D10       — 2 THT pads at the pitch, pin-1 origin
  * Fiducial_1mm_Mask2mm           — one 1 mm SMD dot
  * Bourns 3296W trimpot           — 3 inline THT pins, 2.54 pitch

Fab export never uses these numbers: `build_pcb.py` loads KiCad's own footprint
by name, so the cache only feeds preview placement/routing/3D. Idempotent —
existing entries are never overwritten.

    python -m scripts.derive_cache_entries
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services import footprint_gen  # noqa: E402

CACHE = _BACKEND_ROOT / "app" / "library" / "footprint_cache.json"


def _smd(n: str, x: float, y: float, w: float, h: float) -> dict:
    return {"pin_number": n, "x": round(x, 4), "y": round(y, 4), "width": round(w, 3),
            "height": round(h, 3), "shape": "rounded", "pad_type": "smd", "layer": "top"}


def _tht(n: str, x: float, y: float, drill: float) -> dict:
    return {"pin_number": n, "x": round(x, 4), "y": round(y, 4), "width": drill, "height": 0.0,
            "shape": "circle", "pad_type": "tht", "layer": "top"}


def _entry(name: str, pads: list[dict], w: float, h: float, pin1: str | None = "1") -> dict:
    return {"name": name, "width": round(w, 3), "height": round(h, 3), "pads": pads,
            "confidence": "approximate", "courtyard_w": round(w + 0.04, 3),
            "courtyard_h": round(h + 0.04, 3), "pin1_pad": pin1}


def chip(name: str, size: str) -> dict:
    g = footprint_gen.generate("chip", {"size": size})
    pads = [_smd(p["n"], p["x"], p["y"], p["w"], p["h"]) for p in g["pads"]]
    span = max(abs(p["x"]) + p["w"] / 2 for p in g["pads"]) * 2
    return _entry(name, pads, span + 0.6, g["pads"][0]["h"] + 0.56)


def dual(name: str, pins: int, pitch: float, span: float, lead: float, body_w: float, body_l: float) -> dict:
    g = footprint_gen.generate("dual", {"pins": pins, "pitch": pitch, "span": span, "lead_len": lead})
    pads = [_smd(p["n"], p["x"], p["y"], p["w"], p["h"]) for p in g["pads"]]
    xs = [abs(p["x"]) + p["w"] / 2 for p in g["pads"]]
    ys = [abs(p["y"]) + p["h"] / 2 for p in g["pads"]]
    return _entry(name, pads, max(2 * max(xs), body_w) + 0.5, max(2 * max(ys), body_l) + 0.5)


def dip(n: int) -> dict:
    half = n // 2
    pads = [_tht(str(i + 1), 0.0, -i * 2.54, 0.8) for i in range(half)]
    pads += [_tht(str(half + i + 1), 7.62, -(half - 1 - i) * 2.54, 0.8) for i in range(half)]
    # from the committed DIP-16: width 9.78, height (N/2-1)*2.54 + 3.09
    return _entry(f"Package_DIP:DIP-{n}_W7.62mm", pads, 9.78, (half - 1) * 2.54 + 3.09, None)


def header(rows: int, count: int, name: str, row_span: float = 2.54) -> dict:
    pads = []
    k = 1
    for r in range(rows):
        for i in range(count):
            pads.append(_tht(str(k), r * row_span, -i * 2.54, 1.0))
            k += 1
    # from the committed 1x08: width 3.59, height (N-1)*2.54 + 3.59
    return _entry(name, pads, (rows - 1) * row_span + 3.59, (count - 1) * 2.54 + 3.59, None)


def cp_radial(d: float, p: float, drill: float) -> dict:
    name = f"Capacitor_THT:CP_Radial_D{d:.1f}mm_P{p:.2f}mm"
    pads = [_tht("1", 0.0, 0.0, drill), _tht("2", p, 0.0, drill)]
    return _entry(name, pads, d + 0.5, d + 0.5)


def l_radial(d: float, p: float, drill: float, name: str) -> dict:
    """Radial power inductor (Bourns RLB09xx / Fastron 09HCP class): 2 THT
    pads at the lead pitch, pin-1 origin, body Ø d."""
    pads = [_tht("1", 0.0, 0.0, drill), _tht("2", p, 0.0, drill)]
    return _entry(name, pads, d + 0.5, d + 0.5)


def fiducial() -> dict:
    pads = [{"pin_number": "1", "x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0,
             "shape": "circle", "pad_type": "smd", "layer": "top"}]
    return _entry("Fiducial:Fiducial_1mm_Mask2mm", pads, 2.5, 2.5)


def jst_xh(n: int) -> dict:
    """JST XH B{n}B-XH-A: pin-1 origin, pins along +X at 2.5 mm; the committed
    2P/3P entries show width = 5.95 + 2.5·n, height 6.8, drill 0.95."""
    name = f"Connector_JST:JST_XH_B{n}B-XH-A_1x{n:02d}_P2.50mm_Vertical"
    pads = [_tht(str(i + 1), i * 2.5, 0.0, 0.95) for i in range(n)]
    return _entry(name, pads, 5.95 + 2.5 * n, 6.8, None)


def trimpot_3296w() -> dict:
    pads = [_tht(str(i + 1), i * 2.54, 0.0, 0.8) for i in range(3)]
    return _entry("Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical", pads, 9.53 + 0.5, 4.83 + 0.5)


# --------------------------------------------------------------------------- #
# Transcribed stock footprints
#
# A few KiCad stock land patterns are NOT reproducible from the IPC formulas in
# `footprint_gen` (KiCad's SC-70 pads, for one, are tighter than IPC nominal),
# so these two carry the upstream geometry verbatim — read off the official
# kicad-footprints `.kicad_mod` files, with KiCad's Y-down coordinates negated
# into the cache's Y-up convention (the same route the LED_D3.0mm / LTS-6760 /
# SRD-relay entries took). Verified against those files pad-for-pad; the
# paste-only apertures over the exposed pad are dropped, exactly as a KiCad
# `dump_footprint` run drops them.
# --------------------------------------------------------------------------- #


def _rect(n: str, x: float, y: float, w: float, h: float) -> dict:
    return {"pin_number": n, "x": round(x, 4), "y": round(y, 4), "width": round(w, 3),
            "height": round(h, 3), "shape": "rect", "pad_type": "smd", "layer": "top"}


def _official(name: str, pads: list[dict], cw: float, ch: float) -> dict:
    """Cache entry whose courtyard is the file's real F.CrtYd rectangle."""
    return {"name": name, "width": round(cw - 0.04, 3), "height": round(ch - 0.04, 3),
            "pads": pads, "confidence": "approximate", "courtyard_w": cw,
            "courtyard_h": ch, "pin1_pad": "1"}


def synqit_max86150() -> dict:
    """`Synqit:Synqit_MAX86150` — the MAX86150's 22-lead OLGA/OESIP land pattern.
    The pad table lives in `scripts/gen_footprints.olga22_max86150` (which also
    authors `Synqit.pretty/Synqit_MAX86150.kicad_mod`, the file the fab export
    actually loads), so the cache and the footprint can never drift apart.
    Body 3.3 x 5.6 mm, copper extent 3.20 x 5.30 mm -> courtyard 3.8 x 6.1 mm."""
    from scripts.gen_footprints import olga22_max86150   # noqa: PLC0415
    pads = [_smd(p["n"], p["x"], p["y"], p["w"], p["h"]) for p in olga22_max86150()]
    return {"name": "Synqit:Synqit_MAX86150", "width": 3.3, "height": 5.6, "pads": pads,
            "confidence": "approximate", "courtyard_w": 3.8, "courtyard_h": 6.1,
            "pin1_pad": "1"}


def sc70_6() -> dict:
    """Package_TO_SOT_SMD/SOT-363_SC-70-6.kicad_mod — 6 pads, 0.65 mm pitch,
    ±0.95 mm rows, 0.65 x 0.4 mm each; courtyard 3.2 x 2.8 mm."""
    ys = [0.65, 0.0, -0.65]
    pads = [_rect(str(i + 1), -0.95, y, 0.65, 0.4) for i, y in enumerate(ys)]
    pads += [_rect(str(i + 4), 0.95, y, 0.65, 0.4) for i, y in enumerate(reversed(ys))]
    return _official("Package_TO_SOT_SMD:SOT-363_SC-70-6", pads, 3.2, 2.8)


def htssop20_1ep() -> dict:
    """Package_SO/HTSSOP-20-1EP_4.4x6.5mm_P0.65mm_EP3.4x6.5mm.kicad_mod — the
    TI PowerPAD TSSOP-20 land (same family as the committed HTSSOP-16/-28
    entries): 20 gull-wing pads 1.45 x 0.45 mm at x = ±2.95, 0.65 mm pitch,
    plus the 3.4 x 6.5 mm exposed pad as pad 21; courtyard 7.9 x 7.1 mm."""
    ys = [2.92, 2.27, 1.62, 0.97, 0.33, -0.33, -0.97, -1.62, -2.27, -2.92]
    pads = [_rect(str(i + 1), -2.95, y, 1.45, 0.45) for i, y in enumerate(ys)]
    pads += [_rect(str(i + 11), 2.95, y, 1.45, 0.45) for i, y in enumerate(reversed(ys))]
    pads.append(_rect("21", 0.0, 0.0, 3.4, 6.5))
    return _official("Package_SO:HTSSOP-20-1EP_4.4x6.5mm_P0.65mm_EP3.4x6.5mm",
                     pads, 7.9, 7.1)


NEW = [
    chip("Resistor_SMD:R_0805_2012Metric", "0805"),
    chip("Resistor_SMD:R_1206_3216Metric", "1206"),
    chip("Resistor_SMD:R_2512_6332Metric", "2512"),
    chip("Capacitor_SMD:C_1210_3225Metric", "1210"),
    dual("Package_SO:TSSOP-8_4.4x3mm_P0.65mm", 8, 0.65, 6.4, 0.6, 4.4, 3.0),
    dip(4), dip(6),
    header(1, 16, "Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical"),
    header(1, 7, "Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical"),
    header(2, 8, "Module:Pololu_Breakout-16_15.2x20.3mm", row_span=15.24),
    cp_radial(5.0, 2.0, 0.8), cp_radial(6.3, 2.5, 0.8), cp_radial(8.0, 3.5, 0.8), cp_radial(10.0, 5.0, 1.0),
    fiducial(),
    trimpot_3296w(),
    jst_xh(4), jst_xh(5), jst_xh(6),
    l_radial(9.5, 5.0, 1.0, "Inductor_THT:L_Radial_D9.5mm_P5.00mm_Fastron_09HCP"),
    sc70_6(),
    htssop20_1ep(),
    synqit_max86150(),
]


def main() -> int:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    added = []
    for e in NEW:
        if e["name"] in cache:
            continue
        cache[e["name"]] = e
        added.append(e["name"])
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    for n in added:
        e = cache[n]
        print(f"  + {n:60s} {e['width']:6.2f} x {e['height']:6.2f}  pads={len(e['pads'])}")
    print(f"added {len(added)} entries; cache now {len(cache)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
