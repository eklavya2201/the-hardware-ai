"""Curate body HEIGHTS for the plug-in modules whose footprints we authored.

The 2026-08-20 module-body round gave twelve plug-in modules their real
footprint outline (they had been resolving to a bare pin header, so overlap,
board sizing, enclosure fit and hole clearance all under-measured them). Their
THIRD dimension was left behind: every one still resolved to the
`"MODULE": 3.2` package fallback, so `derived_clearances` sized a lid 3.2 mm
above a 27 mm-tall heatsinked L298N brick and the enclosure could not close.

Only the modules whose own provenance states a thickness are curated here —
each number below is the one cited in that footprint's `descr`, so the height
and the outline come from the same source and cannot disagree. Modules with no
cited thickness are deliberately left to the (now conservative) fallback rather
than guessed: a wrong height closes a lid onto a component.

Re-runnable, and preserves each JSON file's own encoding convention.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# mpn -> (height_mm, the source already cited in the footprint's descr)
HEIGHTS: dict[str, tuple[float, str]] = {
    "L298N_MOD": (27.0, "SunFounder + LC Technology 43 x 43 x 27 mm"),
    "PMS5003": (21.0, "Plantower PMS5003 manual v2.3, 50 x 38 x 21 mm"),
    "SIM800L": (8.0, "iFuture Technology 25.0 x 23.0 x 8 mm"),
    "MAX30102_Module": (8.0, "21 x 16 x 8 mm 7-pin variant"),
    "ESP32-CAM": (4.5, "AI-Thinker ESP32-CAM datasheet 27 x 40.5 x 4.5 mm"),
    "MPU6050_MOD": (3.3, "GY-521 datasheet 21.2 x 16.4 x 3.3 mm"),
}


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lib = os.path.join(root, "app", "library")

    changed = 0
    for fname in ("parts.json", "user_parts.json"):
        path = os.path.join(lib, fname)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data if isinstance(data, list) else data.get("parts", [])
        touched = False
        for entry in entries:
            hit = HEIGHTS.get(entry.get("mpn", ""))
            if hit is None:
                continue
            height, source = hit
            if entry.get("bodyHeightMm") == height:
                continue
            was = entry.get("bodyHeightMm")
            entry["bodyHeightMm"] = height
            touched = True
            changed += 1
            print(f"  {entry['mpn']:<20} {was} -> {height} mm   ({source})")
        if touched:
            # parts.json is raw UTF-8, user_parts.json is ASCII-escaped — keep
            # each file's own convention or the diff is the whole file.
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=(fname == "user_parts.json"))
                fh.write("\n")
            print(f"wrote {fname}")

    from app.services import library, mechanical_derive

    print("\nplug-in modules after this pass:")
    uncurated: list[str] = []
    for part in sorted(library.all_parts(), key=lambda p: p.mpn):
        fp = part.footprint or ""
        if "Synqit_" not in fp or "Module" not in fp:
            continue
        h = mechanical_derive.body_height_mm(part.mpn)
        tag = "curated" if part.bodyHeightMm else "fallback"
        print(f"  {part.mpn:<24} {h:5.1f} mm  ({tag})")
        if not part.bodyHeightMm:
            uncurated.append(part.mpn)

    if uncurated:
        print(
            f"\n{len(uncurated)} module(s) still on the fallback — they need a "
            f"manufacturer thickness before they can be curated:\n  "
            + ", ".join(uncurated)
        )
    print(f"\n{changed} height(s) written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
