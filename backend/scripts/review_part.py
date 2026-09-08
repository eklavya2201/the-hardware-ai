"""Review CLI for queued proposed parts.

Lets you walk the queue, see what the bot extracted (pinout, electrical
envelope, companions, provenance), and approve / reject / drop each one.

Usage:
    python -m backend.scripts.review_part --list
    python -m backend.scripts.review_part --show SCD40
    python -m backend.scripts.review_part --approve SCD40
    python -m backend.scripts.review_part --reject SCD40
    python -m backend.scripts.review_part --walk            # interactive loop
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import LibraryPart
from app.services import curation


def show(part: LibraryPart) -> None:
    print("=" * 70)
    print(f"{part.mpn}    [{part.category}]  status={part.curationStatus}")
    print(f"  {part.description}")
    print(f"  manufacturer: {part.manufacturer or '(unknown)'}")
    print(f"  package: {part.package}")
    print(f"  footprint: {part.footprint}")
    print(f"  symbol: {part.symbol}")
    if part.voltageMin is not None or part.voltageMax is not None:
        print(f"  voltage: {part.voltageMin} – {part.voltageMax} V")
    if part.currentTypMa is not None:
        print(f"  current: typ {part.currentTypMa} mA, max {part.currentMaxMa} mA")
    if part.interfaces:
        print(f"  interfaces: {', '.join(part.interfaces)}")
    if part.defaultI2cAddress:
        print(f"  default I2C address: {part.defaultI2cAddress}")
    if part.firmwareLibrary:
        print(f"  PlatformIO lib: {part.firmwareLibrary}")
    if part.firmwareNotes:
        print(f"  firmware notes: {part.firmwareNotes}")

    print()
    print(f"  pins ({len(part.pins)}):")
    for pin in part.pins:
        print(f"    {pin.number:>4s}  {pin.name:20s} {pin.side:<6s} {pin.type}")

    if part.companions:
        print()
        print(f"  companions ({len(part.companions)}):")
        for c in part.companions:
            print(f"    {c.role:14s} {c.mpn}  ×{c.quantity}  pin={c.pin or '-'}")
            if c.notes:
                print(f"        {c.notes}")

    if part.typicalApplication:
        print()
        print("  typical application:")
        for line in part.typicalApplication.splitlines():
            print(f"    {line}")

    if part.provenance:
        prov = part.provenance
        print()
        print("  provenance:")
        print(f"    datasheet: {prov.datasheetUrl}")
        print(f"    extracted: {prov.extractedAt}  by {prov.extractorVersion} "
              f"using {prov.extractorModel}")
        print(f"    confidence: {prov.confidenceScore}")
        if prov.sourcePages:
            print(f"    source pages: {prov.sourcePages}")
        if prov.verifierChecks:
            failed = [k for k, v in prov.verifierChecks.items() if not v]
            passed = [k for k, v in prov.verifierChecks.items() if v]
            print(f"    checks passed ({len(passed)}): {', '.join(passed)}")
            if failed:
                print(f"    checks FAILED ({len(failed)}): {', '.join(failed)}")
        if prov.notes:
            print("    notes:")
            for n in prov.notes[:8]:
                print(f"      - {n}")


def list_all() -> int:
    parts = curation.list_proposed()
    if not parts:
        print("Queue is empty.")
        return 0
    auto = [p for p in parts if p.curationStatus == "auto_merged"]
    review = [p for p in parts if p.curationStatus == "needs_review"]
    print(f"Queued: {len(parts)}  (auto_merged: {len(auto)}, needs_review: {len(review)})")
    for p in parts:
        score = p.provenance.confidenceScore if p.provenance else None
        score_s = f"score={score}" if score is not None else "score=?"
        print(f"  [{p.curationStatus:14s}] {p.mpn:30s} {p.category:12s} {score_s}")
    return 0


def cmd_approve(mpn: str) -> int:
    part = curation.load_proposed(mpn)
    if not part:
        print(f"No queued part: {mpn}", file=sys.stderr)
        return 1
    curation.merge_into_user_parts(part)
    curation.delete_proposed(mpn)
    print(f"[MERGED] promoted {mpn} -> user_parts.json")
    return 0


def cmd_reject(mpn: str) -> int:
    if curation.delete_proposed(mpn):
        print(f"[DROPPED] {mpn} from queue")
        return 0
    print(f"No queued part: {mpn}", file=sys.stderr)
    return 1


def cmd_walk() -> int:
    """Interactive walk through the queue."""
    parts = curation.list_proposed()
    if not parts:
        print("Queue is empty.")
        return 0
    print(f"Walking {len(parts)} queued parts. Commands: a=approve, "
          f"r=reject, s=skip, q=quit")
    for i, part in enumerate(parts, 1):
        print(f"\n[{i}/{len(parts)}]")
        show(part)
        choice = ""
        while choice not in {"a", "r", "s", "q"}:
            choice = input("\n[a]pprove / [r]eject / [s]kip / [q]uit > ").strip().lower()
        if choice == "a":
            curation.merge_into_user_parts(part)
            curation.delete_proposed(part.mpn)
            print(f"  [MERGED] {part.mpn}")
        elif choice == "r":
            curation.delete_proposed(part.mpn)
            print(f"  [DROPPED] {part.mpn}")
        elif choice == "q":
            print("Bye.")
            return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Review queued parts")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--show", type=str, metavar="MPN")
    g.add_argument("--approve", type=str, metavar="MPN")
    g.add_argument("--reject", type=str, metavar="MPN")
    g.add_argument("--walk", action="store_true")
    args = p.parse_args(argv)

    if args.list:
        return list_all()
    if args.show:
        part = curation.load_proposed(args.show)
        if not part:
            print(f"No queued part: {args.show}", file=sys.stderr)
            return 1
        show(part)
        return 0
    if args.approve:
        return cmd_approve(args.approve)
    if args.reject:
        return cmd_reject(args.reject)
    if args.walk:
        return cmd_walk()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
