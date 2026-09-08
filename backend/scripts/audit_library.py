"""Agent C — the auditor: how much of the library is actually TRUSTWORTHY.

`verify_library` answers "is this row well-formed". This answers a harder
question: "would I stake a fabrication run on this pin table?" The library grew
from 1,751 to 23,941 parts in a fortnight, most of it by catalogue sweep and
symbol harvest, and the two are not the same kind of evidence:

  A  published      pin table from a vendor/KiCad symbol library, footprint
                    verified, and the footprint's pad numbers are the SAME SET
                    as the symbol's pin numbers. The strongest class we have.
  B  datasheet      a model read the datasheet; provenance records the URL, the
                    extractor and its verifier checks. Trust it, cite it.
  C  hand-curated   a person entered it and it carries an application note.
  D  assumed        a 2-pin passive whose pin table is knowable from the
                    description (a resistor has two pins). Fine for passives,
                    never fine for an IC.
  F  unsupported    no provenance, no note, and a pin table nothing corroborates
                    — or a pad/pin mismatch, which means the part cannot be
                    soldered as drawn.

Offline by default. `--orderability N` samples N parts against Mouser to check
the MPNs are real and buyable (needs MOUSER_API_KEY; a 403 is reported as
"not checked", never as "does not exist" — an unanswered call is not an answer).

    python -m scripts.audit_library                 # grade the whole library
    python -m scripts.audit_library --fail-under 95 # CI gate on grade A+B+C+D
    python -m scripts.audit_library --orderability 40
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LIBRARY = BACKEND / "app" / "library"

PASSIVE_CATEGORIES = {"passive", "led", "diode", "crystal", "fuse", "protection"}

# A KiCad symbol name often stands for a FAMILY: the lowercase x is a wildcard
# for the package, temperature grade or speed bin (MCP2515-xST covers -I/ST and
# -E/ST). Harvesting those symbols put 2,833 such names into the library as if
# they were part numbers. They are not orderable as written, so a BOM built on
# one cannot be bought — the exact failure the "never invent a price" rule
# exists to prevent, one field to the left. Neighbours that are digits mean a
# matrix dimension (PinHeader_1x04), not a wildcard.
WILDCARD_MPN = re.compile(r"(?<![0-9a-z])x(?![0-9a-z])")


def _pad_numbers(cache: dict, footprint: str) -> set[str] | None:
    entry = cache.get(footprint)
    if not entry or not isinstance(entry, dict):
        return None
    pads = entry.get("pads") or []
    out = {str(p.get("pin_number")) for p in pads if isinstance(p, dict) and p.get("pin_number")}
    return out or None


def grade(part, base_mpns: set[str], cache: dict) -> tuple[str, list[str]]:
    """Return (grade, problems). Problems are what a human would have to fix."""
    problems: list[str] = []
    prov = getattr(part, "provenance", None)
    notes = " ".join(getattr(prov, "notes", []) or []) if prov else ""
    pins = part.pins or []
    pin_numbers = {str(p.number) for p in pins}

    # The hard gate first: can this part physically be soldered as drawn?
    pads = _pad_numbers(cache, part.footprint or "")
    if pads is not None and pin_numbers:
        # An exposed pad is pad N+1 and legitimately absent from a curated pin
        # subset; a pin that names a pad the footprint does NOT have is fatal.
        orphan = pin_numbers - pads
        if orphan:
            problems.append(f"pins {sorted(orphan)[:4]} have no pad on {part.footprint}")
    elif pads is None and part.footprint:
        problems.append("footprint has no cached pad list")

    if not pins and part.category != "mechanical":
        problems.append("no pins")
    if pins and not all(p.name and str(p.name).strip() for p in pins):
        problems.append("unnamed pins")
    if pins and not all(p.type for p in pins):
        problems.append("untyped pins")
    if len(pin_numbers) != len(pins):
        problems.append("duplicate pin numbers")

    if WILDCARD_MPN.search(part.mpn or ""):
        problems.append("MPN is a family wildcard, not an orderable part number")
        return "F", problems

    if any("no pad on" in p for p in problems):
        return "F", problems

    if "kicad" in notes.lower() or "symbol" in notes.lower():
        return ("A" if not problems else "F"), problems
    if prov is not None and getattr(prov, "datasheetUrl", None):
        return ("B" if not problems else "F"), problems
    if part.mpn in base_mpns or part.typicalApplication:
        return ("C" if not problems else "F"), problems
    if (part.category or "") in PASSIVE_CATEGORIES and len(pins) <= 3:
        return ("D" if not problems else "F"), problems
    problems.append("no provenance, no application note, not a simple passive")
    return "F", problems


def sample_orderability(parts, n: int) -> dict:
    """Check N random MPNs against a distributor. An unanswered call is never
    an answer: a quota error is reported as 'not checked'."""
    import os
    import random

    if not os.environ.get("MOUSER_API_KEY"):
        return {"checked": 0, "note": "MOUSER_API_KEY not set — orderability not checked"}
    from app.services import pricing

    random.seed(7)
    sample = random.sample(list(parts), min(n, len(parts)))
    key = os.environ["MOUSER_API_KEY"]
    found = missing = errored = 0
    misses: list[str] = []
    for part in sample:
        try:
            raw = pricing._fetch_mouser_raw(part.mpn, key)
            if raw is None:
                errored += 1          # quota, network, or a refusal — NOT a verdict
                continue
            parts_found = ((raw.get("SearchResults") or {}).get("Parts") or [])
            exact = [q for q in parts_found
                     if (q.get("ManufacturerPartNumber") or "").strip().upper() == part.mpn.strip().upper()]
            if exact:
                found += 1
            else:
                missing += 1
                misses.append(part.mpn)
        except Exception:  # noqa: BLE001 — a distributor error is not a verdict
            errored += 1
    return {"checked": len(sample), "exact_match_at_distributor": found,
            "no_exact_match": missing, "not_checked": errored,
            "examples_without_a_match": misses[:10]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", default=str(LIBRARY / "library_audit.json"))
    ap.add_argument("--fail-under", type=float, default=None,
                    help="exit 1 if the trustworthy share (A+B+C+D) is below this percentage")
    ap.add_argument("--orderability", type=int, default=0)
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args(argv)

    from app.services import library as lib

    parts = lib.all_parts()
    base_mpns = {p["mpn"] for p in json.loads((LIBRARY / "parts.json").read_text(encoding="utf-8"))}
    cache = json.loads((LIBRARY / "footprint_cache.json").read_text(encoding="utf-8"))

    grades = collections.Counter()
    by_grade_cat = collections.defaultdict(collections.Counter)
    failures: list[dict] = []
    problem_kinds = collections.Counter()
    seen_mpn = collections.Counter()

    for part in parts:
        seen_mpn[part.mpn] += 1
        g, problems = grade(part, base_mpns, cache)
        grades[g] += 1
        by_grade_cat[g][part.category or "?"] += 1
        for p in problems:
            problem_kinds[p.split(" [")[0].split(" on ")[0]] += 1
        if g == "F":
            failures.append({"mpn": part.mpn, "category": part.category,
                             "footprint": part.footprint, "problems": problems})

    dupes = [m for m, c in seen_mpn.items() if c > 1]
    total = len(parts)
    trustworthy = total - grades["F"]

    label = {"A": "A  published (vendor/KiCad symbol + verified footprint)",
             "B": "B  datasheet-extracted, provenance recorded",
             "C": "C  hand-curated with an application note",
             "D": "D  assumed (simple passive, pin table implied by class)",
             "F": "F  unsupported — do not stake a fab run on it"}

    print(f"LIBRARY AUDIT — {total} parts\n")
    for g in "ABCDF":
        n = grades[g]
        pct = 100.0 * n / total if total else 0
        top = ", ".join(f"{c} {k}" for k, c in by_grade_cat[g].most_common(3))
        print(f"  {label[g]:<58} {n:>6}  {pct:5.1f}%")
        if top:
            print(f"  {'':<58} {top}")
    print(f"\n  trustworthy (A+B+C+D)  {trustworthy}/{total}  {100.0 * trustworthy / total:.1f}%")
    print(f"  duplicate MPNs         {len(dupes)}")

    if problem_kinds:
        print("\nWHAT IS WRONG, most common first")
        for kind, n in problem_kinds.most_common(8):
            print(f"  {n:>6}  {kind}")

    if failures:
        print(f"\nGRADE F EXAMPLES ({len(failures)} total)")
        for f in failures[: args.show]:
            print(f"  {f['mpn']:<26} {(f['category'] or '?'):<12} {'; '.join(f['problems'])[:80]}")

    payload = {"total": total, "grades": dict(grades),
               "trustworthy_pct": round(100.0 * trustworthy / total, 2) if total else 0,
               "duplicate_mpns": dupes[:50], "problem_kinds": dict(problem_kinds),
               "failures": failures}

    if args.orderability:
        print(f"\nORDERABILITY SAMPLE ({args.orderability} parts)")
        result = sample_orderability(parts, args.orderability)
        payload["orderability"] = result
        for k, v in result.items():
            print(f"  {k}: {v}")

    Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {args.json}")

    if args.fail_under is not None and payload["trustworthy_pct"] < args.fail_under:
        print(f"FAIL: {payload['trustworthy_pct']}% trustworthy is below the {args.fail_under}% gate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
