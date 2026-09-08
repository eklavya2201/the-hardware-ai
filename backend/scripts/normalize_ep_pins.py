"""Renumber exposed-pad pins to the KiCad pad number (2026-08-26).

The router and the .kicad_pcb export net a pin by exact string match against
the footprint's pad number. KiCad numbers an exposed pad as the last lead + 1
(QFN-24-1EP -> "25"), so a library pin numbered "PAD" / "EP" / "0" / "TAB"
matches no pad and its GND net silently never reaches the thermal copper.

For every part on a `*-1EP` (or Synqit EP) footprint whose EP pin carries a
label instead of a number, this sets `number` to the footprint's EP pad number.
The name is kept. Parts whose footprint has NO EP pad (a label on a plain
TQFP-32, a TO-220 "TAB") have the label pin REMOVED — there is nothing on the
board for it to land on and the TO-220 tab is electrically pin 2.

Usage:  python -m scripts.normalize_ep_pins [--dry-run]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
_LIB = _BACKEND / "app" / "library"
_LABELS = {"EP", "PAD", "EPAD", "TAB", "0", "THERMAL"}


def _ep_pad(cache: dict, fpid: str) -> str | None:
    fp = cache.get(fpid)
    if not fp:
        return None
    # Some footprints number the EP by name themselves (Nordic AQFN-73 -> "EP").
    for p in fp.get("pads", []):
        if str(p.get("pin_number", "")).upper() in _LABELS:
            return str(p["pin_number"])
    nums = [int(p["pin_number"]) for p in fp.get("pads", [])
            if str(p.get("pin_number", "")).isdigit()]
    if not nums:
        return None
    ep = max(nums)
    # Only trust it when the footprint actually has an extra centre pad.
    has_ep = "-1EP" in fpid or any(
        str(p.get("pin_number")) == str(ep) and abs(p["x"]) < 0.01 and abs(p["y"]) < 0.01
        for p in fp["pads"])
    return str(ep) if has_ep else None


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    cache = json.loads((_LIB / "footprint_cache.json").read_text(encoding="utf-8"))
    for fname in ("parts.json", "user_parts.json"):
        path = _LIB / fname
        parts = json.loads(path.read_text(encoding="utf-8"))
        touched = 0
        for p in parts:
            keep = []
            for pin in p.get("pins", []):
                num = str(pin.get("number", "")).strip().upper()
                if num not in _LABELS:
                    keep.append(pin)
                    continue
                if p["mpn"].startswith("Arduino_"):  # a module's pad "0" is D0, not an EP
                    keep.append(pin)
                    continue
                ep = _ep_pad(cache, p.get("footprint", ""))
                taken = {str(x.get("number")) for x in p["pins"]}
                if ep and ep not in taken:
                    print(f"{p['mpn']:18s} {pin['number']!r:8} -> {ep!r}  ({pin.get('name')})")
                    pin["number"] = ep
                    keep.append(pin)
                else:
                    why = "footprint has no EP pad" if not ep else f"pad {ep} already listed"
                    print(f"{p['mpn']:18s} {pin['number']!r:8} REMOVED  ({pin.get('name')}; {why})")
                touched += 1
            p["pins"] = keep
        if touched and not dry:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(parts, fh, indent=2, ensure_ascii=(fname == "user_parts.json"))
                fh.write("\n")
        print(f"{fname}: {touched} pin(s) {'would be ' if dry else ''}changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
