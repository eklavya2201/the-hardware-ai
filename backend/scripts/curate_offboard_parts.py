"""Mark the parts that live OFF the PCB and reach it through a connector.

R72 reports a plug-in module whose footprint is still a bare pin header,
because Synqit then measures the pin strip instead of the module and gets
overlap, board size, enclosure fit and the 3D view wrong for it. That is a real
defect — for a MODULE.

It is not a defect for a servo. An SG90 hangs off three wires; a 28BYJ-48
stepper hangs off five; a membrane keypad is a sticker on a ribbon cable. The
board carries their connector and nothing else, so the header is exactly the
right footprint and there is no body to draw. R72 was reporting the servo and
the stepper as hard ERRORS, and R72 errors feed the agent's fix-up loop, so it
would have spent turns trying to fix something that was already correct.

Each entry below says WHY the part is off-board, in the terms a person would
use. Re-runnable; preserves each JSON file's own encoding convention.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OFF_BOARD: dict[str, str] = {
    "SG90": "hobby servo — a cased gearmotor on a 3-wire lead",
    "28BYJ-48": "stepper motor — a can on a 5-wire lead with a JST plug",
    "Keypad_4x4_Membrane": "membrane keypad — a flexible sticker on a ribbon tail",
    "Laser_Module_650nm": "laser diode module — a brass barrel on flying leads",
    "Limit_Switch_KW12": "microswitch — mounted on the chassis, wired back to the board",
    "Water_Level_Sensor_Module": "probe board — sits in the tank, not on the PCB",
    "Soil_Moisture_Capacitive_v1.2": "probe — goes in the soil, wired back to the board",
}

# Deliberately NOT off-board, though it is tempting to call them that:
#   DHT11 / DHT22  — a 4-pin plastic housing that is routinely soldered
#                    straight onto a PCB. It has a real body and needs a real
#                    footprint; it is a curation gap, not an off-board device.
#   JSN-SR04T      — the TRANSDUCER is on a cable, but the module is a board
#                    that mounts like any other.
# The distinction is whether the PCB carries the part or only its connector.
NOT_OFF_BOARD = ("DHT11", "DHT22", "JSN-SR04T")


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
            mpn = entry.get("mpn", "")
            if mpn in NOT_OFF_BOARD and entry.get("offBoard"):
                entry.pop("offBoard", None)
                touched = True
                print(f"  {mpn:<30} NOT off-board after all — it mounts on the PCB")
                continue
            why = OFF_BOARD.get(mpn)
            if why is None or entry.get("offBoard") is True:
                continue
            entry["offBoard"] = True
            touched = True
            changed += 1
            print(f"  {mpn:<30} off-board — {why}")
        if touched:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=(fname == "user_parts.json"))
                fh.write("\n")
            print(f"wrote {fname}")

    from app.services import library, verifier

    missed = [m for m in OFF_BOARD if not getattr(library.get_part(m), "offBoard", False)]
    if missed:
        print(f"\nNOT FOUND in the library: {', '.join(missed)}")

    still = [
        p.mpn for p in library.all_parts()
        if (getattr(p, "package", "") or "").upper().startswith("MODULE")
        and verifier._HEADER_FP_RE.search(p.footprint or "")  # noqa: SLF001
        and not getattr(p, "offBoard", False)
    ]
    print(f"\n{changed} part(s) marked off-board.")
    print(f"{len(still)} genuine plug-in module(s) still need a real footprint:")
    print("  " + ", ".join(sorted(still)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
