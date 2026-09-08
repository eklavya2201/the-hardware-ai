"""Stamp `matingFaceDir` onto panel-mount connectors (Week-4 ECAD 6.1).

`matingFaceDir` is the direction the connector's plug opening points in the
footprint's LOCAL frame at rotation 0. The placer uses it to rotate an
edge-mounted connector so the opening faces off-board (out the nearest edge) — so
a USB/barrel/terminal faces the wall you actually plug into.

Only PANEL-mount connectors are seeded (USB, barrel/DC jacks, RJ45/RJ11, audio,
HDMI, screw terminals, wire-to-board JST, M12). INTERNAL connectors (pin/debug
headers, coin-cell/battery holders, micro-SD/SD sockets, FFC/FPC ribbon) are left
unset — they don't mount at a panel edge, so they shouldn't be auto-rotated.

Values are inferred from footprint pad geometry (pins cluster on the board-attach
side; the opening faces the opposite way) and are approximate — VERIFY VISUALLY in
the 3D view and correct any that face the wrong way (each is a one-line edit here,
then re-run). Re-runnable; preserves each JSON file's encoding convention.

Usage:  python -m scripts.curate_connector_orientation [--check]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "app" / "library"

# MPN -> mating-face direction ("+X"/"-X"/"+Y"/"-Y") in the footprint local frame.
# None = REMOVE the field: top-entry ("Vertical") connectors mate straight up out
# of the board plane, so they have no in-plane mouth and must not be auto-rotated.
#
# 2026-07-23 audit: every seed below was checked against the official KiCad
# footprint's F.Fab body outline (mouth = direction from the pad centroid toward
# the fab-bbox centre, KiCad Y negated into the local frame) — the original
# pad-cluster heuristic compared against the footprint ORIGIN instead of the body
# centre and got the two GCT USB-C seeds exactly 180° backwards.
MATING: dict[str, str | None] = {
    # --- USB ---
    # GCT USB4085: pads sit at the REAR of the shell (fab body spans local
    # Y -9.11..+1.06, pads -4.36..0) — the mouth is -Y, not +Y.
    "USB_C_16Pin_Power_Only": "-Y",
    "USB4085-GF-A": "-Y",
    # GCT USB4115-03-C is a VERTICAL receptacle ("USB TYPE C, VERT RCPT") —
    # fully symmetric footprint, mouth points up.
    "USB_C_24Pin_Full": None,
    "10118194-0001LF": "-Y",   # micro-USB B
    # USB_A/USB_B corrected against the measured stock-footprint fab outline
    # (scripts/regression_geometry_truth.py mirror audit, 2026-07-16): the body
    # overhangs the pads on the mouth side — Stewart USB-A mouth is -Y (was
    # +Y), OST USB-B is +X (was -X).
    "USB_A_Female": "-Y",
    "USB_B_Female": "+X",
    # --- video / audio ---
    "HDMI_TypeA": "+X",
    "HDMI_MicroD": "-Y",
    "MicroHDMI": "-Y",
    # PJ320D: the jack barrel runs along X (fab X -8.73..6.07) — plug enters -X.
    "Audio_Jack_3.5mm": "-X",
    # --- power in ---
    # Barrel jacks: the 3D model's opening faces the SAME side as the pins
    # (verified visually), so mating dir is toward the pins, not away.
    "DC_Jack_2.5mm": "-X",
    "BarrelJack_DC_5.5x2.1mm": "-X",   # verified in the 3D view (was +X, faced inward)
    # Screw terminals: wire enters along Y (the deep axis of each cell),
    # perpendicular to the pin row — so the mating face is a Y face, not X.
    "ScrewTerminal_2P_5mm": "-Y",
    "ScrewTerminal_3P_5mm": "-Y",
    # --- network ---
    "RJ45_Ethernet": "+X",
    # RJ11 corrected (same audit): both the Amphenol footprint's fab outline
    # AND its own GLB body extend -Y from the pads — the jack opening is -Y.
    "RJ11": "-Y",
    "M12_5Pin": None,   # plain vertical pin header — mates upward
    # --- wire-to-board JST (external wiring) ---
    # S2B-PH-K is the SIDE-entry PH shell: wires exit away from the pad row (-Y).
    "S2B-PH-K-S(LF)(SN)": "-Y",
    # B2B-XH / BM04B (GH/SH) are TOP-entry shells (KiCad "_Vertical") — no
    # in-plane mouth; the side-entry variants are S2B/SM04B.
    "JST_XH_2P": None,
    "JST_XH_3P": None,
    "JST_GH_4P": None,
    "JST_SH_4P": None,
    "QwiicConnector_4P": None,
}


def _load(name: str) -> list[dict]:
    p = _LIB / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _write(name: str, parts: list[dict]) -> None:
    p = _LIB / name
    raw = any(ord(c) > 127 for c in p.read_text(encoding="utf-8")) if p.exists() else False
    p.write_text(json.dumps(parts, ensure_ascii=not raw, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report only; exit 1 if any MATING key is missing from the library")
    args = ap.parse_args()

    lib_mpns: set[str] = set()
    stamped = 0
    files_changed: list[str] = []
    for fname in ("parts.json", "user_parts.json"):
        parts = _load(fname)
        changed = False
        for part in parts:
            lib_mpns.add(part["mpn"])
            if part["mpn"] not in MATING or args.check:
                continue
            d = MATING[part["mpn"]]
            if d is None:
                if part.pop("matingFaceDir", None) is not None:
                    changed = True
            elif part.get("matingFaceDir") != d:
                part["matingFaceDir"] = d
                changed = True
            stamped += 1
        if changed:
            _write(fname, parts)
            files_changed.append(fname)

    unknown = sorted(m for m in MATING if m not in lib_mpns)
    print(f"panel connectors defined : {len(MATING)}")
    if unknown:
        print(f"WARNING — MATING MPNs not in the library: {unknown}")
    if args.check:
        return 1 if unknown else 0
    print(f"stamped matingFaceDir on {stamped} connector(s); files: {files_changed or 'none'}")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
