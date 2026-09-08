"""Coverage report — show how far we are toward the 1000-part target.

Reads the coverage matrix and the live library, and reports per-category
and per-tier completion.

Usage:
    python -m backend.scripts.coverage_report
    python -m backend.scripts.coverage_report --tier 1
    python -m backend.scripts.coverage_report --remaining        # just MPNs left to do
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services import curation
from app.services import library as lib


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Library coverage report")
    p.add_argument("--tier", type=int, choices=[1, 2, 3], default=None)
    p.add_argument("--category", type=str, default=None)
    p.add_argument("--remaining", action="store_true",
                   help="Print only the MPNs that are not yet curated")
    args = p.parse_args(argv)

    matrix = curation.load_coverage_matrix()
    if not matrix:
        print(f"No coverage matrix at {curation.COVERAGE_MATRIX}", file=sys.stderr)
        return 2
    live = {p.mpn for p in lib.all_parts()}
    queued = {p.mpn for p in curation.list_proposed()}

    if args.tier is not None:
        matrix = [t for t in matrix if t.tier == args.tier]
    if args.category:
        matrix = [t for t in matrix if t.category == args.category]

    if args.remaining:
        for t in matrix:
            if t.mpn in live or t.mpn in queued:
                continue
            print(f"{t.mpn}\t{t.category}\ttier{t.tier}\t{t.archetype}")
        return 0

    # By-tier rollup.
    by_tier: dict[int, dict[str, int]] = defaultdict(
        lambda: {"target": 0, "live": 0, "queued": 0, "remaining": 0})
    by_cat: dict[str, dict[str, int]] = defaultdict(
        lambda: {"target": 0, "live": 0, "queued": 0, "remaining": 0})

    for t in matrix:
        for bucket in (by_tier[t.tier], by_cat[t.category]):
            bucket["target"] += 1
            if t.mpn in live:
                bucket["live"] += 1
            elif t.mpn in queued:
                bucket["queued"] += 1
            else:
                bucket["remaining"] += 1

    total_target = len(matrix)
    total_live = sum(1 for t in matrix if t.mpn in live)
    total_queued = sum(1 for t in matrix if t.mpn in queued)
    total_rem = total_target - total_live - total_queued

    print("=" * 70)
    print(f"Coverage matrix: {curation.COVERAGE_MATRIX.name}")
    print(f"Total parts in live library: {len(live)}")
    print(f"Total parts in proposed queue: {len(queued)}")
    print()
    print(f"Matrix target: {total_target}   live: {total_live}   "
          f"queued: {total_queued}   remaining: {total_rem}")
    pct = (total_live / total_target * 100) if total_target else 0.0
    print(f"Coverage: {pct:.1f}% live")
    print()

    print("By tier:")
    for tier in sorted(by_tier):
        b = by_tier[tier]
        pct = (b["live"] / b["target"] * 100) if b["target"] else 0.0
        print(f"  tier {tier}: {b['live']:4d}/{b['target']:4d}  "
              f"({pct:5.1f}%)   queued={b['queued']:3d}   remaining={b['remaining']:4d}")

    print()
    print("By category:")
    for cat in sorted(by_cat):
        b = by_cat[cat]
        pct = (b["live"] / b["target"] * 100) if b["target"] else 0.0
        print(f"  {cat:15s}  {b['live']:4d}/{b['target']:4d}  "
              f"({pct:5.1f}%)   queued={b['queued']:3d}   remaining={b['remaining']:4d}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
