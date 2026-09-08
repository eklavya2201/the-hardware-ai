"""Consume datasheet-research footprint specs → author/assign footprints, verify.

Input: a JSON file that is a list of spec objects (see the research prompts).
For each part this:
  - action=existing  -> verify the stock footprint covers the part's pins.
  - action=author    -> build an IPC land pattern (gen_footprints.leadless/grid),
                        write a .kicad_mod into Synqit.pretty, verify it loads &
                        covers pins. Footprint id becomes "Synqit:<name>".
  - action=author_pads -> write an explicit pad list (e.g. radial THT sensors).
  - action=skip      -> left alone (reported).
Optionally repairs incomplete pin lists (correct_pins).

Every candidate is verified through the bridge (pin coverage) before it's
accepted. Nothing is applied to the library here — it prints a review table and
writes accepted footprint changes to proposals.json (apply separately) and
pin-repairs to pin_repairs.json.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
from dotenv import load_dotenv; load_dotenv()
from app.services import library, kicad_bridge
from scripts import gen_footprints as G

SCRATCH = Path(r"C:/Users/USER/AppData/Local/Temp/claude/D--cursor-for-hardware/1524c8ba-c303-435f-95a8-8edb93c0972c/scratchpad")
EP_DES = {"PAD", "EP", "EPAD", "THERMAL", "0", "GND_PAD", "DAP", "CENTER"}


def _safe_name(mpn):
    return "Synqit_" + re.sub(r"[^A-Za-z0-9_.-]", "_", mpn)


def _sig_pins(part):
    return {p.number for p in part.pins if str(p.number).upper() not in EP_DES}


def _covers(fpid, sig):
    r = kicad_bridge.run_script("dump_footprint.py", {"footprint": fpid})
    if not r.success:
        return False, r.error
    pads = {p["pin_number"] for p in r.data.get("pads", [])}
    return sig.issubset(pads), (sorted(sig - pads))


def _build_pads(spec):
    fam = (spec.get("family") or "").lower()
    if spec.get("grid") or fam in ("grid", "bga", "wlp", "wlcsp"):
        g = spec["grid"]
        return G.grid(g["rows"], g["cols"], g["pitch"], g["pad"], names=spec.get("names")), None
    ep = None
    if spec.get("ep"):
        npins = spec.get("pins") or (spec["ny_side"] + spec["nx_side"]) * 2
        ep = {"x": 0, "y": 0, "w": spec["ep"]["w"], "h": spec["ep"]["h"],
              "n": str(npins + 1)}
    if spec.get("rect"):
        pads = G.leadless_rect(spec["ny_side"], spec["nx_side"], spec["pitch"],
                               spec["pad_w"], spec["pad_l"], spec["span_x"], spec["span_y"])
        return pads, ep
    pads = G.leadless(spec["pins"], spec["pitch"], spec["pad_w"], spec["pad_l"],
                      spec["span"], sides=spec["sides"], cw=bool(spec.get("cw")))
    return pads, ep


def process(specs):
    fp_fixes = {}   # mpn -> new footprint id
    pin_repairs = {}
    rows = []
    for spec in specs:
        mpn = spec["mpn"]
        part = library.get_part(mpn)
        if part is None:
            rows.append((mpn, "NO PART", "")); continue
        sig = _sig_pins(part)
        # optional pin repair (only records; applied separately)
        if spec.get("correct_pins"):
            pin_repairs[mpn] = spec["correct_pins"]
            sig = {str(p["number"]) for p in spec["correct_pins"]
                   if str(p["number"]).upper() not in EP_DES}
        # The spec may be flat (action at top level) or nest the footprint spec
        # under "footprint" (used by the pin-repair batch). A string "footprint"
        # is just the id for an `existing` action, not a nested spec.
        nested = spec.get("footprint")
        sub = nested if isinstance(nested, dict) else spec
        act = sub.get("action") or spec.get("action")
        if act == "skip":
            rows.append((mpn, "SKIP", sub.get("reason", ""))); continue
        if act == "existing":
            fpid = sub["footprint"]
            ok, miss = _covers(fpid, sig)
            rows.append((mpn, "existing OK" if ok else f"existing NO {miss}", fpid))
            if ok: fp_fixes[mpn] = fpid
            continue
        # author paths -> write .kicad_mod then verify
        name = _safe_name(mpn)
        try:
            if act == "author_pads":
                G.write(name, sub["pads"], descr=mpn)
            else:
                pads, ep = _build_pads(sub)
                G.write(name, pads, ep=ep, descr=mpn)
        except Exception as e:
            rows.append((mpn, f"AUTHOR ERR {e}", "")); continue
        fpid = f"Synqit:{name}"
        ok, miss = _covers(fpid, sig)
        rows.append((mpn, "author OK" if ok else f"author NO {miss}", fpid))
        if ok: fp_fixes[mpn] = fpid
        else:
            (G.OUT_DIR / f"{name}.kicad_mod").unlink(missing_ok=True)  # drop bad
    # report
    print(f"{'MPN':20s} {'RESULT':22s} FOOTPRINT")
    for mpn, res, fp in rows:
        print(f"  {mpn:20s} {res:22s} {fp}")
    ok_n = sum(1 for _, r, _ in rows if r.endswith("OK"))
    print(f"\n{ok_n}/{len(rows)} footprints verified")
    # stage footprint changes
    out = {"tierA": {}, "tierB": {m: {"old": library.get_part(m).footprint, "new": fp}
                                  for m, fp in fp_fixes.items()}}
    (SCRATCH/"proposals.json").write_text(json.dumps(out, indent=2))
    (SCRATCH/"pin_repairs.json").write_text(json.dumps(pin_repairs, indent=2))
    print(f"staged {len(fp_fixes)} footprint fixes, {len(pin_repairs)} pin repairs")


if __name__ == "__main__":
    specs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    process(specs)
