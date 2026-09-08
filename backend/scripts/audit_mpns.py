"""Does this part actually exist? Ask a distributor about every MPN we ship.

The library's rule has always been that a part is real — `add_component`
refuses anything not in it, so an invented MPN becomes a board nobody can
order. Nothing enforced that rule at the point of entry until the passive
expansion started confirming each part against Mouser's catalogue.

This audits what was already there. It only READS: every MPN is looked up, and
the ones the catalogue does not carry are reported with whatever Mouser
suggests instead. Deciding what to do about them — correct, mark, or remove —
is a judgement call about each part, not something a script should do silently.

    python -m scripts.audit_mpns --only-unverified --limit 120
    python -m scripts.audit_mpns --category passive --json audit.json

Mouser: 30 calls/minute, 1000/day, so a full sweep of a 1700-part library takes
two days. `--only-unverified` skips parts this project already confirmed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_BACKEND / ".env")

# Descriptions carry non-breaking hyphens and the like; on a Windows console
# (cp1252) printing one killed a run 11 parts in, on 2026-08-25. The verdicts
# already written survived, the remaining calls did not happen.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from app.services import library, pricing  # noqa: E402


STAMP = time.strftime("%Y-%m-%d")


def _mark(part, verdict: str, listing: dict | None = None) -> None:
    """Record the verdict on the part itself.

    A hand-checked part that nobody wrote down gets hand-checked again, and a
    part nobody ever checked looks identical to one that passed. Writing the
    verdict into provenance is what makes "has this been confirmed?" a
    question the data can answer."""
    from app.models import LibraryPart, Provenance

    data = part.model_dump()
    prov = data.get("provenance") or {}
    prov["extractorVersion"] = verdict
    prov.setdefault("extractedAt", time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    data["provenance"] = Provenance(**prov).model_dump()

    # The same call that answers "do they carry it" also carries the SKU and
    # the datasheet link, and we were throwing both away — 1751 parts with no
    # Mouser SKU and 1523 with no datasheet, while paying for the lookups that
    # would have supplied them. Fill only what is EMPTY: a curated datasheet
    # was read by a human off the manufacturer's page and outranks a
    # distributor's copy of it.
    if listing:
        sku = (listing.get("MouserPartNumber") or "").strip()
        if sku and not data.get("mouserPart"):
            data["mouserPart"] = sku
        ds = (listing.get("DataSheetUrl") or "").strip()
        if ds and not data.get("datasheetUrl"):
            data["datasheetUrl"] = ds

    library.add_user_part(LibraryPart(**data))


def confirmed_here(part) -> bool:
    """Has a catalogue already ANSWERED about this part?

    Named for what it originally did — recognise the parts born from a
    catalogue query — but --only-unverified needs the broader question, or a
    re-run spends the daily quota re-asking about parts this very script
    already settled. It did: the 116 parts audited on 2026-08-23 were queued
    again on 08-25 because a verdict this script wrote was not something it
    could read back.

    A verdict of any kind counts, including a negative one. Re-checking a
    "not carried" is a deliberate act (drop --only-unverified, or pass the
    category), not something a routine sweep should spend calls on."""
    prov = getattr(part, "provenance", None)
    ver = getattr(prov, "extractorVersion", "") or "" if prov else ""
    return ("expand_passives" in ver or "discover_parts" in ver
            or ver.startswith("audit "))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--category")
    ap.add_argument("--only-unverified", action="store_true")
    ap.add_argument("--recheck-near-misses", action="store_true",
                    help="only parts whose verdict is 'no exact listing' and "
                         "that still have no mouserPart — the set the "
                         "packaging-suffix rule (2026-08-25) can flip")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--json")
    ap.add_argument("--mark", action="store_true",
                    help="record the verdict on each part so it is not re-checked "
                         "and the app can show what has been confirmed")
    args = ap.parse_args(argv)

    key = os.environ.get("MOUSER_API_KEY")
    if not key:
        print("no MOUSER_API_KEY (a SEARCH key — see scripts/check_mouser_key.py)")
        return 1

    parts = library.all_parts()
    if args.category:
        parts = [p for p in parts if p.category == args.category]
    if args.only_unverified:
        parts = [p for p in parts if not confirmed_here(p)]
    if args.recheck_near_misses:
        parts = [p for p in parts
                 if "no exact listing" in ((getattr(p.provenance, "extractorVersion", "")
                                            or "") if p.provenance else "")
                 and not getattr(p, "mouserPart", None)]
    # Actives first. The quota is finite and a truncated run should have spent
    # it on the parts a design actually reaches for — a sensor nobody stocks
    # breaks a build, an unlisted 3.9k resistor has a hundred substitutes.
    parts = sorted(parts, key=lambda p: (p.category == "passive", p.category))
    parts = parts[: args.limit]
    print(f"auditing {len(parts)} MPNs against Mouser\n")

    missing: list[dict] = []
    found = 0
    for i, part in enumerate(parts):
        if i:
            time.sleep(pricing.MIN_CALL_INTERVAL_S)
        raw = pricing._fetch_mouser_raw(part.mpn, key)  # noqa: SLF001
        if raw is not None:
            # A response full of near-misses is not evidence about OUR part.
            # The old code confirmed on any LivePrice, and _parse_mouser falls
            # back to parts[0] — so a part Mouser has never heard of could be
            # marked "confirmed at Mouser" on the strength of a different
            # part's listing. Require the exact manufacturer part number.
            listing = pricing.exact_part(part.mpn, raw)
            if listing is not None:
                found += 1
                if args.mark:
                    how = listing.get("_matchedSuffix")
                    _mark(part, f"audit {STAMP}: confirmed at Mouser"
                          + (f" as {listing.get('ManufacturerPartNumber')}"
                             f" (packaging suffix {how})" if how else ""),
                          listing)
                continue
            hits = (raw.get("SearchResults") or {}).get("Parts") or []
            others = len(hits)
            verdict = ("no exact listing at Mouser" if others
                       else "NOT carried at Mouser")
            missing.append({"mpn": part.mpn, "category": part.category,
                            "description": part.description,
                            "near_misses": others,
                            "near_miss_mpns": [h.get("ManufacturerPartNumber")
                                               for h in hits[:6]]})
            if args.mark:
                _mark(part, f"audit {STAMP}: {verdict}")
            print(f"   {'NO EXACT' if others else 'NOT CARRIED':12} "
                  f"{part.mpn:26} {part.category:10} "
                  f"{(part.description or '')[:34]}"
                  f"{f'  ({others} near-misses)' if others else ''}")
            continue
        err = pricing.LAST_ERROR or ""
        if err:
            # The catalogue did not answer. That is a fact about the CONNECTION,
            # never about the part, and recording it as "not carried" is how a
            # quota outage becomes 245 wrong verdicts. Stop instead: a partial
            # audit is worth something, a corrupted one is not.
            print(f"stopping — the catalogue stopped answering: {err[:80]}")
            print("   (nothing was recorded for this part; re-run when it "
                  "answers again)")
            break
        # No response and no error should be impossible — _fetch_mouser_raw
        # sets LAST_ERROR on every failure path. If it ever happens, it is a
        # bug in the fetcher, and the one thing we must not do is turn it into
        # a verdict about the part. Stop and say so.
        print(f"stopping — no response and no error for {part.mpn}; "
              "that is a fetcher bug, not a fact about the part")
        break

    total = found + len(missing)
    if total:
        print(f"\n{found}/{total} confirmed at Mouser, {len(missing)} not "
              f"({100 * len(missing) / total:.0f}%)")
    if args.json and missing:
        Path(args.json).write_text(json.dumps(missing, indent=1), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
