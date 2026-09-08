"""Curation bot CLI.

Usage:
    python -m backend.scripts.curate_bot --batch 25
    python -m backend.scripts.curate_bot --tier 1
    python -m backend.scripts.curate_bot --mpn SCD40
    python -m backend.scripts.curate_bot --category sensor --batch 50
    python -m backend.scripts.curate_bot --auto-merge       # commit auto-merge eligible

The bot reads `backend/app/library/coverage_matrix.csv`, fetches datasheets,
runs LLM extraction with the rich schema, runs the verifier, and writes
proposed parts to `backend/app/library/proposed_parts/<MPN>.json`.

By default the bot does NOT push anything into the live library — even
high-confidence parts land in the proposed queue. Use --auto-merge to
promote auto-merge-eligible parts into user_parts.json in the same run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make `app.*` imports work when run as a script.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# The web app loads backend/.env via load_dotenv() in app/main.py, but this CLI
# never imports main, so without this the extractor's OPENAI_API_KEY (and model
# config) sit unread in .env and every curation fails "OPENAI_API_KEY not set".
# override=True so backend/.env is authoritative: a stale OPENAI_API_KEY left in
# the OS/user environment otherwise silently shadows the .env value (load_dotenv
# skips already-set vars by default) and every extraction 401s on the old key.
from dotenv import load_dotenv
load_dotenv(_BACKEND_ROOT / ".env", override=True)

from app.services import curation
from app.services import library as lib


def _rejected_mpns() -> set[str]:
    """MPNs a human reviewed and REFUSED, recorded in promote_tier1.REJECTIONS.

    Without this the skip set is only library+queued, so a rejected part looks
    untouched and gets curated again on the next run. Measured 2026-08-31: 8 of
    24 proposals in one run were parts already refused (synthetic BGA pin
    numbers on ATSAMD51N20A/ATSAME54N20A, ISO1050DUBR's wide-body isolation SOP
    mapped to a narrow SOIC-8), so a third of the run spent tokens re-filling
    the queue with work a reviewer had already thrown out.

    `--refresh` still overrides this, so a rejection can be revisited
    deliberately — e.g. once ISO1050DUBR's real DUB land pattern is cached."""
    try:
        from scripts.promote_tier1 import REJECTIONS
    except Exception:  # noqa: BLE001 — the review record is optional
        return set()
    return set(REJECTIONS)


def _existing_mpns() -> set[str]:
    return ({p.mpn for p in lib.all_parts()}
            | {p.mpn for p in curation.list_proposed()}
            | _rejected_mpns())


def cmd_run(args: argparse.Namespace) -> int:
    matrix = curation.load_coverage_matrix()
    if not matrix:
        print(
            f"Coverage matrix at {curation.COVERAGE_MATRIX} is empty or missing.\n"
            "Create it (or check the path) and rerun.",
            file=sys.stderr,
        )
        return 2

    skip = set() if args.refresh else _existing_mpns()
    targets = curation.filter_targets(
        matrix,
        only_tier=args.tier,
        only_category=args.category,
        only_mpn=args.mpn,
        skip_existing_mpns=skip,
    )
    if args.batch:
        targets = targets[: args.batch]

    if not targets:
        print("Nothing to do — all matching targets already curated or queued.")
        print("Use --refresh to re-run them anyway.")
        return 0

    print(f"Curating {len(targets)} parts. Output queue: {curation.PROPOSED_DIR}")
    print("=" * 78)

    successes = 0
    auto_merges = 0
    failures = 0

    for i, target in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {target.mpn}  (tier {target.tier}, "
              f"{target.category}, {target.archetype})")
        if args.dry_run:
            print("  DRY RUN - would fetch:", target.datasheet_url)
            continue

        outcome = curation.curate_one(target)

        if not outcome.success:
            failures += 1
            print(f"  [FAIL] ({outcome.elapsed_seconds:.1f}s): {outcome.error}")
            continue

        successes += 1
        part = outcome.proposed_part
        assert part is not None
        path = curation.write_proposed(part)
        failed_checks = [k for k, v in outcome.verifier_checks.items() if not v]
        status_tag = "[AUTO]" if outcome.auto_mergeable else "[QUEUE]"
        print(f"  {status_tag} score={outcome.confidence_score} "
              f"llm={outcome.confidence} "
              f"pins={len(part.pins)} pkg={part.package} "
              f"({outcome.elapsed_seconds:.1f}s)")
        if failed_checks:
            print(f"     failed checks: {', '.join(failed_checks)}")
        if outcome.notes:
            for n in outcome.notes[:3]:
                print(f"     note: {n}")
        print(f"     queued -> {path.relative_to(_BACKEND_ROOT)}")

        if outcome.auto_mergeable and args.auto_merge:
            curation.merge_into_user_parts(part)
            curation.delete_proposed(part.mpn)
            auto_merges += 1
            print("     [MERGED] into user_parts.json")

    print("\n" + "=" * 78)
    print(f"Summary: {successes} succeeded, {failures} failed, "
          f"{auto_merges} auto-merged")
    print(f"Queue dir: {curation.PROPOSED_DIR}")
    print(f"Review with: python -m backend.scripts.review_part --list")
    return 0 if failures == 0 else 1


def cmd_auto_merge_all(args: argparse.Namespace) -> int:
    """Promote every already-queued auto-merge-eligible part. Useful when you
    ran the bot without --auto-merge and later decided to trust the queue."""
    queued = curation.list_proposed()
    eligible = [p for p in queued if p.curationStatus == "auto_merged"]
    if not eligible:
        print("Nothing to promote — no auto_merged parts in the queue.")
        return 0
    print(f"Promoting {len(eligible)} auto_merged parts...")
    for p in eligible:
        curation.merge_into_user_parts(p)
        curation.delete_proposed(p.mpn)
        print(f"  ⤴ {p.mpn}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Datasheet notes routinely contain non-ASCII (µ, °, en-dashes, the U+2011
    # non-breaking hyphen TI loves in part numbers). On Windows the console
    # defaults to cp1252, so printing such a note raises UnicodeEncodeError and
    # aborts the whole batch mid-run — once cost us the entire sensor group.
    # Force UTF-8 with replacement so a stray glyph can never kill a run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # py3.7+
        except (AttributeError, ValueError):
            pass

    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    # If the first argument starts with "-" or there are no args, default to
    # the `run` subcommand. Lets users skip typing `run` for the common case.
    if not raw_argv or raw_argv[0].startswith("-"):
        raw_argv = ["run", *raw_argv]
    elif raw_argv[0] not in {"run", "promote"}:
        raw_argv = ["run", *raw_argv]

    parser = argparse.ArgumentParser(description="Synqit library curation bot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Curate parts from the coverage matrix (default)")
    p_run.add_argument("--batch", type=int, default=10,
                       help="Max parts to curate this run (default 10)")
    p_run.add_argument("--tier", type=int, choices=[1, 2, 3], default=None)
    p_run.add_argument("--category", type=str, default=None)
    p_run.add_argument("--mpn", type=str, default=None,
                       help="Curate only this MPN")
    p_run.add_argument("--refresh", action="store_true",
                       help="Re-curate even if part already exists in library or queue")
    p_run.add_argument("--auto-merge", action="store_true",
                       help="Promote high-confidence parts to user_parts.json in this run")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Print targets but don't call the LLM")
    p_run.set_defaults(func=cmd_run)

    p_promote = sub.add_parser("promote", help="Promote all queued auto_merged parts")
    p_promote.set_defaults(func=cmd_auto_merge_all)

    args = parser.parse_args(raw_argv)

    if not os.environ.get("OPENAI_API_KEY") and getattr(args, "func", None) is cmd_run \
            and not getattr(args, "dry_run", False):
        print("OPENAI_API_KEY not set - extractor will fail. "
              "Set it and rerun, or use --dry-run.", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
