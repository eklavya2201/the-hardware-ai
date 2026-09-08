"""Author `Synqit_Arduino_Shield_Headers` — the shield-side header set.

Why this exists
---------------
`Arduino_Shield_Headers` pointed at KiCad's `Module:Arduino_UNO_R3`, which is a
footprint for placing an *Arduino Uno as a component on a carrier board*. Its
body is the whole Uno: 75.48 x 53.89 mm, because it includes the USB-B and
barrel-jack shells that overhang the Uno's own PCB edge.

A SHIELD has none of that. It has four female headers around the edge of a
68.58 x 53.34 mm board and an empty middle, which is exactly where a shield's
components go. Modelling that as one 75 mm box meant:

  * every component the shield carried reported a hard overlap error against
    its own host header (4 errors on a correct 4-part shield), and
  * the header itself reported "extends past the board edge", because the Uno's
    body genuinely is bigger than the shield outline it is supposed to sit in.

So the geometry was describing a different object than the one being built.
This script authors a footprint for the real one: identical pads (the Arduino
Uno R3 shield pin positions, including the notorious 1.52 mm offset between D7
and D8, which is what makes shields shield-shaped), and a body expressed as the
FOUR header strips it actually occupies — derived from the pad rows themselves,
not typed in, so the body cannot drift from the pads.

Re-runnable. Writes:
  app/library/footprints/Synqit.pretty/Synqit_Arduino_Shield_Headers.kicad_mod
  a `Synqit:Synqit_Arduino_Shield_Headers` entry in footprint_cache.json
  repoints the library part at it
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import footprints, library  # noqa: E402

SOURCE_FPID = "Module:Arduino_UNO_R3"
NEW_NAME = "Synqit_Arduino_Shield_Headers"
NEW_FPID = f"Synqit:{NEW_NAME}"
MPN = "Arduino_Shield_Headers"

# A 2.54 mm single-row female header: the plastic is 2.54 mm across the row and
# overhangs the end pads by half a pitch.
HALF_PITCH = 1.27
# Pads further apart than this along a row belong to different strips — the Uno
# R3 splits each edge into two headers (8 + 6 power/analog, 10 + 8 digital).
STRIP_GAP = 3.0


def strips(pads) -> list[tuple[float, float, float, float]]:
    """Group pads into header strips and return each strip's body rectangle."""
    rows: dict[float, list[float]] = {}
    for pad in pads:
        rows.setdefault(round(pad.y, 2), []).append(pad.x)

    out: list[tuple[float, float, float, float]] = []
    for y, xs in sorted(rows.items()):
        xs.sort()
        run = [xs[0]]
        for x in xs[1:]:
            if x - run[-1] > STRIP_GAP:
                out.append((run[0] - HALF_PITCH, y - HALF_PITCH,
                            run[-1] + HALF_PITCH, y + HALF_PITCH))
                run = [x]
            else:
                run.append(x)
        out.append((run[0] - HALF_PITCH, y - HALF_PITCH,
                    run[-1] + HALF_PITCH, y + HALF_PITCH))
    return out


def kicad_mod(pads, rects) -> str:
    lines = [
        f'(footprint "{NEW_NAME}"',
        "  (version 20240108)",
        '  (generator "synqit")',
        '  (layer "F.Cu")',
        '  (descr "Arduino Uno R3 SHIELD header set — the four female headers a shield '
        'presents to the base board. Pads are the Uno R3 shield standard (including the '
        '1.52 mm D7/D8 offset), taken pad-for-pad from KiCad Module:Arduino_UNO_R3. The '
        'body is the four header strips ONLY: a shield\'s middle is free board, which is '
        'where its components go. (authored by Synqit)")',
        '  (tags "arduino shield header form-factor")',
        "  (attr through_hole)",
        '  (property "Reference" "REF**" (at 0.00 3.00) (layer "F.SilkS") '
        "(effects (font (size 1 1) (thickness 0.15))))",
        f'  (property "Value" "{NEW_NAME}" (at 0.00 -51.00) (layer "F.Fab") '
        "(effects (font (size 1 1) (thickness 0.15))))",
    ]
    for i, pad in enumerate(pads):
        shape = "rect" if i == 0 else "circle"   # pin 1 square, KiCad convention
        lines.append(
            f'  (pad "{pad.pin_number}" thru_hole {shape} (at {pad.x:.2f} {pad.y:.2f}) '
            f"(size {pad.width + 0.8:.2f} {pad.width + 0.8:.2f}) (drill {pad.width:.2f}) "
            '(layers "*.Cu" "*.Mask"))'
        )
    for x0, y0, x1, y1 in rects:
        lines.append(
            f"  (fp_rect (start {x0:.2f} {y0:.2f}) (end {x1:.2f} {y1:.2f}) "
            '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))'
        )
        lines.append(
            f"  (fp_rect (start {x0 - 0.25:.2f} {y0 - 0.25:.2f}) "
            f"(end {x1 + 0.25:.2f} {y1 + 0.25:.2f}) "
            '(stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))'
        )
    lines.append(")")
    return "\n".join(lines) + "\n"


def main() -> int:
    src = footprints._kicad_fp_cache.get(SOURCE_FPID)  # noqa: SLF001
    if src is None:
        footprints._load_cache()  # noqa: SLF001
        src = footprints._kicad_fp_cache.get(SOURCE_FPID)  # noqa: SLF001
    if src is None or not src.pads:
        print(f"ERROR: no cached pads for {SOURCE_FPID}")
        return 1

    pads = sorted(src.pads, key=lambda p: int(p.pin_number))
    rects = strips(pads)
    print(f"{len(pads)} pads -> {len(rects)} header strips:")
    for x0, y0, x1, y1 in rects:
        print(f"  {x1 - x0:6.2f} x {y1 - y0:4.2f} mm  at y={y0 + HALF_PITCH:7.2f}")

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pretty = os.path.join(root, "app", "library", "footprints", "Synqit.pretty")
    os.makedirs(pretty, exist_ok=True)
    path = os.path.join(pretty, f"{NEW_NAME}.kicad_mod")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(kicad_mod(pads, rects))
    print(f"wrote {path}")

    # Overall extent stays the true outer envelope of the strips, so the
    # containment check still measures something real.
    xs = [r[0] for r in rects] + [r[2] for r in rects]
    ys = [r[1] for r in rects] + [r[3] for r in rects]
    width, height = max(xs) - min(xs), max(ys) - min(ys)

    cache_path = os.path.join(root, "app", "library", "footprint_cache.json")
    with open(cache_path, encoding="utf-8") as fh:
        cache = json.load(fh)
    cache[NEW_FPID] = {
        "name": NEW_FPID,
        "width": round(width, 2),
        "height": round(height, 2),
        "courtyard_w": round(width + 0.5, 2),
        "courtyard_h": round(height + 0.5, 2),
        "confidence": "verified",
        "pin1_pad": pads[0].pin_number,
        "bodyRects": [[round(v, 2) for v in r] for r in rects],
        "pads": [p.model_dump() for p in pads],
    }
    with open(cache_path, "w", encoding="utf-8") as fh:
        # Match the committed file's own style (indent=2, sorted keys) exactly,
        # or a one-entry addition rewrites 100k lines and buries the change.
        json.dump(cache, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print(f"cache entry {NEW_FPID}: {width:.2f} x {height:.2f}, {len(rects)} solid rects")

    part = library.get_part(MPN)
    if part is None:
        print(f"ERROR: {MPN} not in the library")
        return 1
    for name in ("parts.json", "user_parts.json"):
        pj = os.path.join(root, "app", "library", name)
        with open(pj, encoding="utf-8") as fh:
            raw = fh.read()
        data = json.loads(raw)
        entries = data if isinstance(data, list) else data.get("parts", [])
        hit = next((e for e in entries if e.get("mpn") == MPN), None)
        if hit is None:
            continue
        hit["footprint"] = NEW_FPID
        ascii_only = name == "user_parts.json"
        with open(pj, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=ascii_only)
            fh.write("\n")
        print(f"repointed {MPN} -> {NEW_FPID} in {name}")
        break
    else:
        print(f"ERROR: {MPN} not found in either parts file")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
