"""Stamp real body heights onto the library's connector parts (Week-4 ECAD 6.1).

The mechanical enclosure derivation (`services/mechanical_derive.py`) needs a
per-part body Z-height (solder plane → top of part). Absent a curated value it
falls back to a coarse package-family table; for connectors that table's category
default (~8.5 mm) badly over-estimates low SMD ports (a USB-C is ~3.2 mm), which
inflates enclosure clearance and cutout opening size.

This script populates the durable `LibraryPart.bodyHeightMm` for every
connector-category part with a nominal height from its datasheet/family (mm above
the PCB, unmated body). Values are typical for the connector family — good to
~±0.5 mm, which is what the clearance/cutout maths needs. Re-runnable: it upserts
the field in place and leaves every other field untouched.

Usage:  python -m scripts.curate_connector_heights [--check]
  --check : report coverage/mismatches and exit non-zero if any connector is
            unstamped (a CI-style gate), writing nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "app" / "library"

# Nominal body height above the PCB (mm), by MPN. Sourced from the part family's
# datasheet dimensions (unmated connector body, not the mating cable/plug).
HEIGHTS: dict[str, float] = {
    # --- USB ---
    "USB4085-GF-A": 3.2,            # USB-C 2.0 16-pin SMD receptacle
    "USB_C_16Pin_Power_Only": 3.2,  # USB-C SMD
    "USB_C_24Pin_Full": 3.2,        # USB-C SMD
    "10118194-0001LF": 2.9,         # Micro-USB Type B SMD (Amphenol)
    "USB_A_Female": 6.5,            # USB-A horizontal THT receptacle
    "USB_B_Female": 11.5,           # USB-B square THT receptacle
    # --- power in ---
    "BarrelJack_DC_5.5x2.1mm": 11.0,  # THT DC barrel jack
    "DC_Jack_2.5mm": 11.0,            # THT barrel jack
    "ScrewTerminal_2P_5mm": 10.0,     # 5 mm-pitch terminal block
    "ScrewTerminal_3P_5mm": 10.0,
    # --- video / audio / data ---
    "HDMI_TypeA": 6.0,          # full HDMI Type-A SMD
    "HDMI_MicroD": 3.0,         # micro HDMI Type-D
    "MicroHDMI": 3.0,
    "Audio_Jack_3.5mm": 5.5,    # 3.5 mm TRRS SMD jack
    "RJ45_Ethernet": 13.5,      # RJ45 jack w/ magnetics, THT
    "RJ11": 13.0,               # RJ11 6P4C THT
    "M12_5Pin": 12.0,           # M12 PCB-mount header
    # --- card / storage ---
    "MicroSD_Socket_Push": 1.5,  # micro-SD push-push SMD
    "SD_Card_Full": 2.2,         # full-size SD socket SMD
    # --- FFC / FPC ---
    "FFC_30P_0.5mm": 1.5,   # 0.5 mm-pitch FFC/FPC SMD
    "FPC_40P_0.5mm": 1.5,
    # --- JST wire-to-board ---
    "S2B-PH-K-S(LF)(SN)": 6.0,  # JST-PH 2.0 mm THT
    "JST_XH_2P": 8.5,           # JST-XH 2.5 mm THT
    "JST_XH_3P": 8.5,
    "JST_GH_4P": 4.5,           # JST-GH 1.25 mm SMD
    "JST_SH_4P": 3.0,           # JST-SH 1.0 mm SMD (Qwiic)
    "QwiicConnector_4P": 3.0,   # JST-SH 1.0 mm SMD
    # --- pin / debug headers (2.54 mm unless noted) ---
    "PinHeader_1x2_P2.54mm": 8.5,
    "PinHeader_1x3_P2.54mm": 8.5,
    "PinHeader_1x4_P2.54mm": 8.5,
    "PinHeader_1x6_P2.54mm": 8.5,
    "PinHeader_1x8_P2.54mm": 8.5,
    "PinHeader_1x10_P2.54mm": 8.5,
    "PinHeader_2x4_P2.54mm": 9.0,    # IDC shrouded box header
    "PinHeader_2x10_P2.54mm": 9.0,   # IDC box header
    "PinHeader_2x05_P1.27mm": 5.0,   # 1.27 mm SWD debug header
    "Pin_Header_2x40": 8.5,          # Pi GPIO 2.54 mm
    # --- battery holders ---
    "BatteryHolder_CR2032": 5.0,  # CR2032 coin holder SMD
    "Coin_Cell_CR1220": 4.5,      # CR1220 coin holder THT
    "Spring_Battery_AAA": 12.0,   # single AAA holder THT
}


def _load(name: str) -> list[dict]:
    p = _LIB / name
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _write(name: str, parts: list[dict]) -> None:
    """Write back preserving the file's original encoding convention. parts.json
    is hand-curated with raw UTF-8; user_parts.json is bot-written ASCII-escaped
    (\\uXXXX). Detect which by whether the current file holds a raw non-ASCII byte,
    so we never mass-reformat the file we're only adding one field to."""
    p = _LIB / name
    raw = any(ord(c) > 127 for c in p.read_text(encoding="utf-8")) if p.exists() else False
    text = json.dumps(parts, ensure_ascii=not raw, indent=2)
    p.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report coverage only; exit 1 if any connector is unstamped")
    args = ap.parse_args()

    connectors: list[str] = []
    unknown: list[str] = []  # MPNs we have a height for that aren't in the library
    stamped = 0
    files_changed: list[str] = []

    seen_mpns: set[str] = set()
    for fname in ("parts.json", "user_parts.json"):
        parts = _load(fname)
        changed = False
        for part in parts:
            if part.get("category") != "connector":
                continue
            mpn = part["mpn"]
            seen_mpns.add(mpn)
            connectors.append(mpn)
            h = HEIGHTS.get(mpn)
            if h is None:
                continue
            if args.check:
                continue
            if part.get("bodyHeightMm") != h:
                part["bodyHeightMm"] = h
                changed = True
            stamped += 1
        if changed:
            _write(fname, parts)
            files_changed.append(fname)

    missing = sorted(m for m in connectors if m not in HEIGHTS)
    unknown = sorted(m for m in HEIGHTS if m not in seen_mpns)

    print(f"connectors in library : {len(connectors)}")
    print(f"heights defined        : {len(HEIGHTS)}")
    if unknown:
        print(f"WARNING — heights for MPNs not in the library: {unknown}")
    if missing:
        print(f"UNSTAMPED connectors ({len(missing)}): {missing}")
    else:
        print("all connectors covered.")

    if args.check:
        return 1 if missing else 0

    print(f"stamped bodyHeightMm on {stamped} connector(s); "
          f"files changed: {files_changed or 'none'}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
