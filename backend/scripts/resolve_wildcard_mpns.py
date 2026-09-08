"""Resolve KiCad family wildcards into real, orderable part numbers.

The 31 August symbol harvest brought 13,305 parts in with published pin tables
— and with KiCad's SYMBOL names as their MPNs. A KiCad symbol often stands for
a family: the lowercase x in `MCP2515-xST` is a wildcard for the temperature
grade, and the orderable part is `MCP2515-I/ST`. 2,833 parts (11.8 % of the
library, 2,681 of them MCUs) carry one. `scripts/audit_library.py` grades them
F because a BOM built on one cannot be bought.

This resolves them. For each family it searches the distributor for the STEM
(the text before the wildcard), then scores every candidate on how well it fits
the family's shape — stem prefix, the package/suffix letters after the
wildcard, digits preserved — and keeps the best in-stock exact manufacturer
part number.

Three rules, non-negotiable:
  * It NEVER writes user_parts.json. Every result is a proposal for review, in
    `app/library/wildcard_resolutions.json`.
  * An unanswered distributor call is not an answer. A quota error, a timeout
    or a refusal is recorded as `not_checked` and retried on the next run —
    never as "this part does not exist".
  * The PIN TABLE is not touched. The symbol's pins are published data and are
    correct; only the part number was a placeholder.

Checkpointed and rate-paced, so it can be killed and resumed:

    python -m scripts.resolve_wildcard_mpns --plan              # size the job
    python -m scripts.resolve_wildcard_mpns --limit 400         # one session
    python -m scripts.resolve_wildcard_mpns --limit 900 --loop  # until the cap
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LIBRARY = BACKEND / "app" / "library"
OUT = LIBRARY / "wildcard_resolutions.json"

WILDCARD = re.compile(r"(?<![0-9a-z])x(?![0-9a-z])")
PACE_SECONDS = 2.2          # Mouser publishes 30 calls/minute; stay under it
DAILY_CAP = 900             # their limit is 1000/day/key; leave room for the app


def wildcard_parts(parts) -> list:
    return [p for p in parts if WILDCARD.search(p.mpn or "")]


def family_shape(mpn: str) -> tuple[str, str]:
    """Split `MCP2515-xST` into the stem to search ("MCP2515") and the suffix
    that identifies the variant ("ST")."""
    m = WILDCARD.search(mpn)
    stem = mpn[: m.start()].rstrip("-_/ ")
    # A family can carry SEVERAL wildcards (MCP4725xxx-xCH, CS434x-xZZ). Any
    # trailing run of them is still a placeholder, and searching a distributor
    # for a literal "x" returns nothing — trim back to the real stem.
    stem = re.sub(r"[x]+$", "", stem).rstrip("-_/ ")
    suffix = re.sub(r"[^0-9A-Za-z]", "", mpn[m.end():])
    suffix = re.sub(r"^x+", "", suffix)
    return stem, suffix


def score(candidate: str, stem: str, suffix: str) -> float:
    """How well a distributor part number fits the family. Deliberately strict:
    a wrong package is a wrong part, and a plausible-looking near-match is the
    failure mode this whole exercise exists to remove."""
    c = candidate.upper()
    s = stem.upper()
    if not c.startswith(s):
        return 0.0
    value = 1.0
    tail = re.sub(r"[^0-9A-Z]", "", c[len(s):])
    if suffix:
        up = suffix.upper()
        if tail.endswith(up):
            value += 2.0        # exact package code in the right place
        elif up in tail:
            value += 1.0
        else:
            return 0.0          # the family's package letters are absent: not this part
    # Prefer the shortest well-formed variant — vendors append reel/packaging
    # codes (…-TR, …/2K) that are the same silicon in a different box.
    value += max(0.0, 1.0 - 0.02 * max(0, len(c) - len(s) - len(suffix)))
    return value


def resolve_one(part, key: str) -> dict:
    """One family -> one proposal. Returns a row with its own verdict."""
    from app.services import pricing

    stem, suffix = family_shape(part.mpn)
    row = {
        "family_mpn": part.mpn, "stem": stem, "suffix": suffix,
        "category": part.category, "footprint": part.footprint,
        "pins": len(part.pins or []),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if len(stem) < 4:
        return {**row, "verdict": "skipped", "reason": "stem too short to search safely"}
    try:
        raw = pricing._fetch_mouser_raw(stem, key)
    except Exception as exc:  # noqa: BLE001
        return {**row, "verdict": "not_checked", "reason": f"{type(exc).__name__}: {exc}"[:120]}
    if raw is None:
        return {**row, "verdict": "not_checked", "reason": "distributor did not answer"}

    candidates = []
    for entry in ((raw.get("SearchResults") or {}).get("Parts") or []):
        mpn = (entry.get("ManufacturerPartNumber") or "").strip()
        if not mpn:
            continue
        value = score(mpn, stem, suffix)
        if value <= 0:
            continue
        stock = entry.get("Availability") or ""
        digits = re.sub(r"[^0-9]", "", str(stock))
        candidates.append({
            "mpn": mpn,
            "manufacturer": (entry.get("Manufacturer") or "")[:60],
            "stock": int(digits) if digits else 0,
            "lifecycle": (entry.get("LifecycleStatus") or "")[:40],
            "score": round(value, 3),
        })
    if not candidates:
        return {**row, "verdict": "no_candidate",
                "reason": f"distributor returned nothing matching {stem}*{suffix}"}
    candidates.sort(key=lambda c: (-c["score"], -c["stock"], len(c["mpn"])))
    best = candidates[0]
    in_stock = [c for c in candidates if c["stock"] > 0]
    chosen = in_stock[0] if in_stock and in_stock[0]["score"] >= best["score"] - 0.5 else best
    return {**row, "verdict": "resolved", "resolved_mpn": chosen["mpn"],
            "manufacturer": chosen["manufacturer"], "stock": chosen["stock"],
            "lifecycle": chosen["lifecycle"], "confidence": chosen["score"],
            "alternatives": [c["mpn"] for c in candidates[1:4]]}


def load_state() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "calls_today": 0, "day": datetime.now(timezone.utc).date().isoformat(),
            "rows": {}}


def save_state(state: dict) -> None:
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(OUT)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=200, help="families to attempt this run")
    ap.add_argument("--plan", action="store_true", help="size the job and exit, no calls")
    ap.add_argument("--loop", action="store_true", help="keep going until the daily cap")
    ap.add_argument("--retry-unchecked", action="store_true",
                    help="also retry rows a distributor never answered for")
    args = ap.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
    from app.services import library as lib

    families = wildcard_parts(lib.all_parts())
    state = load_state()
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("day") != today:
        state["day"], state["calls_today"] = today, 0

    done = {k for k, v in state["rows"].items()
            if v.get("verdict") not in (None, "not_checked") or not args.retry_unchecked}
    todo = [p for p in families if p.mpn not in done]

    if args.plan:
        by_cat: dict[str, int] = {}
        for p in families:
            by_cat[p.category or "?"] = by_cat.get(p.category or "?", 0) + 1
        verdicts: dict[str, int] = {}
        for v in state["rows"].values():
            verdicts[v.get("verdict", "?")] = verdicts.get(v.get("verdict", "?"), 0) + 1
        print(f"wildcard families in the library : {len(families)}")
        print(f"already attempted                : {len(state['rows'])}  {verdicts}")
        print(f"remaining                        : {len(todo)}")
        print(f"pace                             : {PACE_SECONDS}s/call, cap {DAILY_CAP}/day")
        print(f"estimated                        : {len(todo) * PACE_SECONDS / 60:.0f} min of calls, "
              f"{max(1, -(-len(todo) // DAILY_CAP))} day(s) at the cap")
        print("by category                      :", dict(sorted(by_cat.items(), key=lambda kv: -kv[1])[:8]))
        return 0

    key = os.environ.get("MOUSER_API_KEY")
    if not key:
        print("MOUSER_API_KEY is not set — nothing to do.")
        return 1

    attempted = 0
    while todo:
        batch = todo[: args.limit]
        for part in batch:
            if state["calls_today"] >= DAILY_CAP:
                print(f"daily cap reached ({DAILY_CAP}) — stopping cleanly, resume tomorrow")
                save_state(state)
                return 0
            row = resolve_one(part, key)
            state["rows"][part.mpn] = row
            state["calls_today"] += 1
            attempted += 1
            mark = {"resolved": "OK", "no_candidate": "--", "not_checked": "??", "skipped": "skip"}
            print(f"  {mark.get(row['verdict'], '?'):>4}  {part.mpn:<34} -> "
                  f"{row.get('resolved_mpn') or row.get('reason', '')[:44]}", flush=True)
            if attempted % 20 == 0:
                save_state(state)
            time.sleep(PACE_SECONDS)
        save_state(state)
        todo = todo[len(batch):]
        if not args.loop:
            break

    verdicts: dict[str, int] = {}
    for v in state["rows"].values():
        verdicts[v.get("verdict", "?")] = verdicts.get(v.get("verdict", "?"), 0) + 1
    print(f"\nattempted this run: {attempted} | calls today: {state['calls_today']}")
    print("verdicts so far   :", verdicts)
    print(f"wrote {OUT}")
    print("NOTHING was merged — review the proposals, then promote deliberately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
