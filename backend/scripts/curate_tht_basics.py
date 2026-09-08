"""Curate the through-hole basics a beginner prompt asks for by name.

2026-08-20 review: "asked for a through-hole resistor but it gave a
surface-mount version; asked for a 5mm LED, but it used a 3mm one". Both were
LIBRARY GAPS — the design could not have been right. R73 now errors on that
mismatch, so the parts have to exist before the rule can be fair.

Adds:
  * 5 mm THT LEDs (red / green / yellow / blue / white) on the official KiCad
    `LED_THT:LED_D5.0mm` land pattern;
  * axial THT resistors in the values a beginner names (150 / 220 / 330 / 470 /
    1k / 10k) on `Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal`.

Footprint-cache entries are derived from the published KiCad geometry (this box
has no KiCad), exactly as `derive_cache_entries.py` does for the other
generator-scripted parts.

    python -m scripts.curate_tht_basics            # write
    python -m scripts.curate_tht_basics --dry-run  # show what would change
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models import LibraryPart  # noqa: E402
from app.services import library  # noqa: E402

CACHE = _BACKEND / "app" / "library" / "footprint_cache.json"

# --- Footprint geometry (KiCad official land patterns) ---------------------- #
# LED_THT:LED_D5.0mm — two 1.8 mm round THT pads on a 2.54 mm pitch, 5 mm body.
LED_D5 = {
    "name": "LED_THT:LED_D5.0mm",
    "width": 5.0, "height": 5.0,
    "courtyard_w": 5.6, "courtyard_h": 5.6,
    "pin1_pad": "1",
    "confidence": "verified",
    "pads": [
        {"pin_number": "1", "x": -1.27, "y": 0.0, "width": 1.8, "height": 1.8,
         "shape": "rect", "pad_type": "tht", "drill": 0.9, "layer": "*.Cu"},
        {"pin_number": "2", "x": 1.27, "y": 0.0, "width": 1.8, "height": 1.8,
         "shape": "circle", "pad_type": "tht", "drill": 0.9, "layer": "*.Cu"},
    ],
}
# Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal — 10.16 mm
# pitch, 1.6 mm pads, 0.8 mm drill; body 6.3 x 2.5 mm lying flat.
R_AXIAL = {
    "name": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
    "width": 10.16, "height": 2.5,
    "courtyard_w": 11.4, "courtyard_h": 3.1,
    "pin1_pad": "1",
    "confidence": "verified",
    "pads": [
        {"pin_number": "1", "x": -5.08, "y": 0.0, "width": 1.6, "height": 1.6,
         "shape": "rect", "pad_type": "tht", "drill": 0.8, "layer": "*.Cu"},
        {"pin_number": "2", "x": 5.08, "y": 0.0, "width": 1.6, "height": 1.6,
         "shape": "circle", "pad_type": "tht", "drill": 0.8, "layer": "*.Cu"},
    ],
}

_LED_COLOURS = [
    ("R", "Red", 2.0, "#c62828"),
    ("G", "Green", 2.1, "#2e7d32"),
    ("Y", "Yellow", 2.1, "#f9a825"),
    ("B", "Blue", 3.0, "#1565c0"),
    ("W", "White", 3.0, "#eceff1"),
]
_R_VALUES = [
    ("150R", 150.0, "150Ω"),
    ("220R", 220.0, "220Ω"),
    ("330R", 330.0, "330Ω"),
    ("470R", 470.0, "470Ω"),
    ("1K", 1000.0, "1kΩ"),
    ("10K", 10000.0, "10kΩ"),
]


def _led_part(code: str, colour: str, vf: float) -> dict:
    return {
        "mpn": f"LED_5MM_{code}",
        "description": f"{colour} LED, 5mm through-hole, Vf={vf}V, If=20mA",
        "category": "led",
        "package": "THT-D5.0mm",
        "footprint": LED_D5["name"],
        "symbol": "Device:LED",
        "bodyHeightMm": 8.6,
        "voltageMin": vf,
        "voltageMax": vf + 0.4,
        "currentMaxMa": 20,
        "pins": [
            {"number": "1", "name": "A", "type": "passive"},
            {"number": "2", "name": "K", "type": "passive"},
        ],
        "typicalApplication": (
            f"5 mm through-hole {colour.lower()} indicator LED. ALWAYS in series with a "
            f"current-limiting resistor: R = (Vrail - {vf}) / 0.02 A — from 5 V that is "
            f"{round((5 - vf) / 0.02)}Ω, from 3.3 V it is {max(1, round((3.3 - vf) / 0.02))}Ω. "
            f"Anode (A, the long leg) to the source, cathode (K) toward ground."
        ),
        "alternatives": [],
    }


def _res_part(code: str, ohms: float, pretty: str) -> dict:
    return {
        "mpn": f"R_AXIAL_{code}",
        "description": f"{pretty} resistor, axial through-hole, 1/4W, 5%",
        "category": "passive",
        "package": "Axial-DIN0207",
        "footprint": R_AXIAL["name"],
        "symbol": "Device:R",
        "bodyHeightMm": 2.5,
        "powerMaxW": 0.25,
        "pins": [
            {"number": "1", "name": "1", "type": "passive"},
            {"number": "2", "name": "2", "type": "passive"},
        ],
        "typicalApplication": (
            f"{pretty} 1/4 W axial resistor for through-hole builds (breadboard-friendly, "
            f"hand-solderable). Use the 0603 chip equivalent on a surface-mount board."
        ),
        "alternatives": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    added_fp = []
    for entry in (LED_D5, R_AXIAL):
        if entry["name"] not in cache:
            cache[entry["name"]] = {k: v for k, v in entry.items() if k != "name"} | {"name": entry["name"]}
            added_fp.append(entry["name"])

    known = {p.mpn for p in library.all_parts()}
    new_parts = []
    for code, colour, vf, _hex in _LED_COLOURS:
        part = _led_part(code, colour, vf)
        if part["mpn"] not in known:
            new_parts.append(part)
    for code, ohms, pretty in _R_VALUES:
        part = _res_part(code, ohms, pretty)
        if part["mpn"] not in known:
            new_parts.append(part)

    print(f"footprint-cache entries to add: {added_fp or 'none'}")
    print(f"parts to add: {[p['mpn'] for p in new_parts] or 'none'}")
    if a.dry_run:
        return 0
    if added_fp:
        CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for part in new_parts:
        library.add_user_part(LibraryPart.model_validate(part))
    print(f"done: +{len(added_fp)} footprints, +{len(new_parts)} parts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
