"""Regression guard for the user-facing footprint generator (offline).

Ground truth is the committed *verified* footprint cache (KiCad's own
geometry): generated land patterns for known packages must match the cached
pads within tolerance. Also asserts numbering conventions, THT drills,
.kicad_mod well-formedness, and spec validation.

Run: python -m scripts.regression_footprint_gen
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import footprint_gen

CACHE = json.loads(
    (Path(__file__).resolve().parents[1] / "app" / "library" / "footprint_cache.json")
    .read_text(encoding="utf-8")
)


def check(name: str, ok: bool) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return ok


def worst_delta(gen_pads: list[dict], cached_name: str) -> float:
    """Worst pad-centre distance between generated pads and the cached KiCad
    truth. The cache is already in the IR frame (Y-up — the bridge converts),
    same as the generator spec, so coordinates compare directly."""
    ref = {p["pin_number"]: (p["x"], p["y"]) for p in CACHE[cached_name]["pads"]}
    worst = 0.0
    for p in gen_pads:
        if p["n"] not in ref:
            return 99.0
        rx, ry = ref[p["n"]]
        worst = max(worst, ((p["x"] - rx) ** 2 + (p["y"] - ry) ** 2) ** 0.5)
    return worst


def main() -> int:
    results: list[bool] = []

    # --- castellated edge pads (2026-08-20 review round) --------------------
    cast = footprint_gen.generate(
        "castellated",
        {"per_side": 10, "pitch": 2.54, "board_w": 25.4, "board_h": 50.8,
         "pad_w": 1.0, "pad_l": 2.0},
    )
    results.append(check("castellated: 2x10 gives 20 pads", len(cast["pads"]) == 20))
    xs = sorted({round(pad["x"], 2) for pad in cast["pads"]})
    results.append(check(
        "castellated pads sit on both board edges",
        len(xs) == 2 and abs(xs[0] + 11.7) < 0.01 and abs(xs[1] - 11.7) < 0.01,
    ))
    ys = sorted(pad["y"] for pad in cast["pads"] if abs(pad["x"] + 11.7) < 0.01)
    pitches = {round(b - a, 2) for a, b in zip(ys, ys[1:])}
    results.append(check("castellated pitch is exact", pitches == {2.54}))
    left = [pad for pad in cast["pads"] if abs(pad["x"] + 11.7) < 0.01]
    results.append(check(
        "pin 1 is the top-left pad and numbering runs counter-clockwise",
        left[0]["n"] == "1" and cast["pads"][-1]["n"] == "20"
        and abs(cast["pads"][-1]["x"] - 11.7) < 0.01,
    ))
    results.append(check(
        "castellated warns that the fab must plate the edge",
        any("edge plating" in w or "castellated holes" in w for w in cast["warnings"]),
    ))
    quad = footprint_gen.generate(
        "castellated", {"per_side": 6, "sides": "4", "pitch": 2.0, "board_w": 20, "board_h": 20}
    )
    results.append(check("castellated 4-sided gives 4x pads", len(quad["pads"]) == 24))
    for label, params in (
        ("pads longer than the module", {"per_side": 30, "pitch": 2.54, "board_h": 20}),
        ("pad wider than the pitch", {"per_side": 4, "pitch": 1.0, "pad_w": 1.2}),
    ):
        try:
            footprint_gen.generate("castellated", params)
            results.append(check(f"castellated rejects {label}", False))
        except ValueError:
            results.append(check(f"castellated rejects {label}", True))

    # --- chip vs KiCad R_0603 / R_0402 -------------------------------------
    r0603 = footprint_gen.generate("chip", {"size": "0603"})
    d = worst_delta(r0603["pads"], "Resistor_SMD:R_0603_1608Metric")
    results.append(check(f"0603 pads within 0.1 mm of KiCad ({d:.3f})", d <= 0.1))
    r0402 = footprint_gen.generate("chip", {"size": "0402"})
    d = worst_delta(r0402["pads"], "Resistor_SMD:R_0402_1005Metric")
    results.append(check(f"0402 pads within 0.15 mm of KiCad ({d:.3f})", d <= 0.15))

    # --- dual gull-wing vs KiCad SOIC-8 ------------------------------------
    soic8 = footprint_gen.generate(
        "dual", {"pins": 8, "pitch": 1.27, "span": 6.0, "lead_len": 1.0}
    )
    d = worst_delta(soic8["pads"], "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    results.append(check(f"SOIC-8 pads within 0.15 mm of KiCad ({d:.3f})", d <= 0.15))

    # --- quad gull-wing vs KiCad TQFP-32 (7x7, lead span 9.0) ---------------
    tqfp32 = footprint_gen.generate(
        "quad", {"pins": 32, "pitch": 0.8, "span": 9.0, "lead_len": 0.75}
    )
    d = worst_delta(tqfp32["pads"], "Package_QFP:TQFP-32_7x7mm_P0.8mm")
    results.append(check(f"TQFP-32 pads within 0.2 mm of KiCad ({d:.3f})", d <= 0.2))

    # --- leadless numbering convention (QFN-24: pin 1 top-left, 7 bottom-mid,
    # 13 right, 19 top) — same math the KiCad selftest validated ------------
    qfn = footprint_gen.generate(
        "leadless", {"pins": 24, "sides": "4", "pitch": 0.5, "span": 3.9,
                     "pad_w": 0.28, "pad_l": 0.75, "ep": 2.6}
    )
    by_n = {p["n"]: p for p in qfn["pads"]}
    results.append(check("QFN pin 1 on the left column, top",
                         by_n["1"]["x"] < 0 and by_n["1"]["y"] > 0))
    results.append(check("QFN pin 13 on the right column", by_n["13"]["x"] > 0))
    results.append(check("QFN EP emitted as pad 25", "25" in by_n))

    # --- THT families -------------------------------------------------------
    dip = footprint_gen.generate("dip", {"pins": 8, "row_span": 7.62})
    by_n = {p["n"]: p for p in dip["pads"]}
    results.append(check("DIP-8 drills present, JEDEC corners (1 TL, 8 TR)",
                         all(p["drill"] > 0 for p in dip["pads"])
                         and by_n["1"]["x"] == -3.81 and by_n["8"]["x"] == 3.81
                         and by_n["1"]["y"] == by_n["8"]["y"]
                         and by_n["4"]["y"] == by_n["5"]["y"]))
    hdr = footprint_gen.generate("header", {"rows": 2, "count": 3})
    by_n = {p["n"]: p for p in hdr["pads"]}
    results.append(check("2x3 header zigzag numbering (1,2 share a row)",
                         by_n["1"]["y"] == by_n["2"]["y"] and by_n["1"]["x"] < by_n["2"]["x"]
                         and by_n["3"]["y"] < by_n["1"]["y"]))

    # --- emitted .kicad_mod well-formedness ---------------------------------
    for res in (r0603, soic8, qfn, dip):
        c = res["content"]
        ok = (c.count("(") == c.count(")")
              and c.startswith("(footprint")
              and "IPC-nominal" in c
              and f'"{res["name"]}"' in c)
        results.append(check(f"{res['name']} .kicad_mod well-formed", ok))

    # --- validation ---------------------------------------------------------
    for family, params, why in [
        ("quad", {"pins": 30}, "pins not multiple of 4"),
        ("dual", {"pins": 8, "pitch": 1.27, "span": 1.0, "lead_len": 1.0}, "span < lead"),
        ("nope", {}, "unknown family"),
        ("bga", {"rows": 99}, "rows over max"),
    ]:
        try:
            footprint_gen.generate(family, params)
            results.append(check(f"rejects {why}", False))
        except ValueError:
            results.append(check(f"rejects {why}", True))

    if all(results):
        print("\nAll footprint-generator regressions pass.")
        return 0
    print("\nFAILURES present.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
