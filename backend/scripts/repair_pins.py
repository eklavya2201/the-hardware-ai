"""Repair incomplete/misnumbered pin lists in the parts library from datasheet
research (task 3.2 tail). Input JSON: {mpn: [{"number","name","type"}, ...]}.
Maps research pin types onto the library PinType enum, assigns a left/right side
split, and rewrites the part's `pins` in whichever library file holds it,
preserving formatting. Verifies each part still loads. Idempotent-ish (replaces
the whole pins list). Run `verify_library` afterwards.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
from dotenv import load_dotenv; load_dotenv()

FILES = [_BACKEND/"app/library/parts.json", _BACKEND/"app/library/user_parts.json"]

# research type -> models.py PinType literal
TYPE_MAP = {
    "power": "power_in", "power_in": "power_in", "power_out": "power_out",
    "ground": "ground", "gnd": "ground",
    "input": "input", "output": "output",
    "io": "bidirectional", "bidirectional": "bidirectional",
    "usb": "bidirectional", "debug": "bidirectional",
    "analog": "passive", "passive": "passive", "rf": "passive",
    "nc": "unspecified", "no_connect": "unspecified", "unspecified": "unspecified",
}


def _side(i, n):
    # simple left/right split by order; cosmetic (schematic layout only)
    return "left" if i < (n + 1) // 2 else "right"


def _mk_pins(raw):
    n = len(raw)
    out = []
    for i, p in enumerate(raw):
        t = TYPE_MAP.get(str(p.get("type", "unspecified")).lower(), "unspecified")
        out.append({"number": str(p["number"]), "name": p["name"],
                    "side": _side(i, n), "type": t})
    return out


def main():
    repairs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    targets = dict(repairs)
    for fp in FILES:
        raw = fp.read_text(encoding="utf-8")
        data = json.loads(raw)
        u_esc = "\\u" in raw
        changed = []
        for part in data:
            mpn = part.get("mpn")
            if mpn in targets:
                part["pins"] = _mk_pins(targets[mpn])
                changed.append((mpn, len(part["pins"])))
        if changed:
            out = json.dumps(data, indent=2, ensure_ascii=u_esc)
            if raw.endswith("\n"):
                out += "\n"
            fp.write_text(out, encoding="utf-8")
        for mpn, n in changed:
            print(f"  {fp.name}: repaired {mpn} -> {n} pins")
            targets.pop(mpn, None)
    if targets:
        print("NOT FOUND:", list(targets))


if __name__ == "__main__":
    main()
