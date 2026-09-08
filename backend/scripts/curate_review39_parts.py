"""Curate what review #39 (phrasing-sensitivity sheet, 2026-08-26) found missing.

Two parts the sheet failed were wired right and specified wrong, because the
library entry carried NO companions and NO application note — so the agent
answered from its own memory and got the numbers wrong:

  AP2112K-3.3TRG1  Output capacitor called "optional". Diodes AP2112 datasheet
                   (DS31456, "Typical Application Circuit" and the Stability
                   note): 1 uF minimum ceramic on VIN and on VOUT.
  TP4056           PROG resistor 2.2 k -> ~545 mA where 1 A was asked. NanJing
                   TP4056 datasheet, section "Programming the Charge Current":
                   I_BAT = (V_PROG / R_PROG) x 1200, V_PROG = 1.0 V, so
                   R_PROG = 1200 / I_BAT  ->  1.2 k for 1 A, 2.0 k for 600 mA,
                   10 k for 130 mA. TEMP pin: tie to GND to disable the NTC
                   function. Battery protection (asked for, silently dropped):
                   the bare TP4056 has none — DW01A + FS8205A is the standard
                   pair, already in the library.

Only companions + typicalApplication change. MPNs, pins and footprints are
untouched — the pin tables were right.

    python -m scripts.curate_review39_parts            # write
    python -m scripts.curate_review39_parts --dry-run  # show what would change
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models import CompanionPart, LibraryPart  # noqa: E402
from app.services import library  # noqa: E402

UPDATES: dict[str, dict] = {
    "AP2112K-3.3TRG1": {
        "companions": [
            CompanionPart(mpn="CC0603KPX7R9BB105", role="decoupling", pin="VIN", quantity=1,
                          notes="1 uF ceramic VIN to GND, close to the pin (datasheet minimum)."),
            CompanionPart(mpn="CC0603KPX7R9BB105", role="decoupling", pin="VOUT", quantity=1,
                          notes="1 uF ceramic VOUT to GND — REQUIRED for stability, not optional "
                                "(AP2112 datasheet: 1 uF minimum output capacitance)."),
        ],
        "typicalApplication": (
            "VIN (pin 1) from a 2.5-6 V source with a 1 uF ceramic to GND; EN (pin 3) tied to "
            "VIN to enable (or driven by a GPIO); VOUT (pin 5) with a 1 uF (or larger) ceramic "
            "to GND — the output capacitor is required by the datasheet for stability. NC "
            "(pin 4) is left open. Rated 600 mA; at 5 V in / 3.3 V out the drop is 1.7 V, so "
            "500 mA dissipates 0.85 W — check SOT-23-5 thermals, or use a switcher above "
            "~300 mA continuous."
        ),
    },
    "TP4056": {
        "companions": [
            CompanionPart(mpn="RC0603FR-071K2L", role="current_limit", pin="PROG", quantity=1,
                          notes="R_PROG sets the charge current: I_BAT = 1200 / R_PROG "
                                "(A, ohms). 1.2 k = 1 A, 2.0 k = 600 mA, 10 k = 130 mA. "
                                "2.2 k gives only ~545 mA."),
            CompanionPart(mpn="CC0603KPX7R9BB105", role="decoupling", pin="VCC", quantity=1,
                          notes="1 uF (10 uF preferred) on VCC to GND."),
            CompanionPart(mpn="CC0603KPX7R9BB105", role="decoupling", pin="VBAT", quantity=1,
                          notes="1 uF (10 uF preferred) on BAT to GND."),
            CompanionPart(mpn="DW01A", role="protection", pin="VBAT", quantity=1,
                          notes="The bare TP4056 has NO battery protection. For a bare cell add "
                                "DW01A + FS8205A between BAT- and the cell (over-charge, "
                                "over-discharge, over-current); a protected cell or TP4056_MOD "
                                "already includes it."),
        ],
        "typicalApplication": (
            "VCC (pin 7) from 5 V USB with 1-10 uF to GND; BAT (pin 4) to the cell + with "
            "1-10 uF to GND; PROG (pin 2) to GND through R_PROG where I_BAT = 1200 / R_PROG "
            "— 1.2 k for 1 A, 2.0 k for 600 mA (size for the cell, not the chip). TEMP "
            "(pin 1) to GND to disable the NTC check, or to a 10 k NTC on the cell. CE "
            "(pin 8) high to enable. CHRG (pin 6) and STDBY (pin 5) are open-drain status "
            "outputs — LEDs through 1 k to VCC. Charging terminates at 4.2 V internally. "
            "There is no protection inside the TP4056: a bare cell needs DW01A + FS8205A."
        ),
    },
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for mpn, upd in UPDATES.items():
        part = library.get_part(mpn)
        if part is None:
            print(f"   ?? {mpn} is not in the library")
            return 1
        data = part.model_dump()
        data["companions"] = [c.model_dump() for c in upd["companions"]]
        data["typicalApplication"] = upd["typicalApplication"]
        print(f"   {mpn:18} companions {len(part.companions or [])} -> {len(upd['companions'])}, "
              f"application note {'set' if part.typicalApplication else 'ADDED'}")
        if not args.dry_run:
            library.add_user_part(LibraryPart(**data))
    if args.dry_run:
        print("\n(dry run — nothing written)")
    else:
        print("\nWritten. Restart the backend — it caches the library.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
