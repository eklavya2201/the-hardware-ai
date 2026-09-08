"""Retype internal-regulator pins from power_in to power_out.

Framework audit (2026-08-28): STM32 VCAP is fed by the chip's OWN on-chip
core regulator — externally it takes only its datasheet capacitor to ground.
Five library entries typed these pins `power_in`, so a CORRECT design (VCAP +
cap) read as "power pin on an unsourced rail" (R26 error) and the agent then
tied VCAP to 3.3 V to clear the false alarm — a chip-damaging "fix".

The correctly-curated siblings already say `power_out` (STM32F411CEU6 VCAP1,
STM32F446RET6 VCAP_1, ATSAMD21/51 VDDCORE, ICM-20948/BQ76920 REGOUT): the pin
IS the source of its little rail. This script brings the stragglers in line.
Pin numbers, names and footprints are untouched.

    python -m scripts.curate_internal_rail_pins            # write
    python -m scripts.curate_internal_rail_pins --dry-run  # show what would change
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models import LibraryPart  # noqa: E402
from app.services import library  # noqa: E402

# MPN -> the internal-rail pin numbers still typed power_in.
FIXES: dict[str, list[str]] = {
    "STM32H743ZIT6": ["86", "125"],   # VCAP, VCAP
    "STM32U575ZIT6": ["137"],         # VCAP
    "STM32F405RGT6": ["31", "47"],    # VCAP_1, VCAP_2
    "PIC32MX250F128B": ["20"],        # VCAP
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for mpn, numbers in FIXES.items():
        part = library.get_part(mpn)
        if part is None:
            print(f"   ?? {mpn} is not in the library")
            return 1
        data = part.model_dump()
        changed = []
        for pin in data["pins"]:
            if pin["number"] in numbers:
                if pin["type"] != "power_in":
                    print(f"   ?? {mpn} pin {pin['number']} ({pin['name']}) is "
                          f"{pin['type']}, expected power_in — already fixed?")
                    continue
                pin["type"] = "power_out"
                changed.append(f"{pin['number']} ({pin['name']})")
        print(f"   {mpn:18} power_in -> power_out on: {', '.join(changed) or 'nothing'}")
        if changed and not args.dry_run:
            library.add_user_part(LibraryPart(**data))
    if args.dry_run:
        print("\n(dry run — nothing written)")
    else:
        print("\nWritten. Restart the backend — it caches the library.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
