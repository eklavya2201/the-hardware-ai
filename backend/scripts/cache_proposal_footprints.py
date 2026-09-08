"""Resolve the land patterns queued proposals are waiting on (needs KiCad).

A curated part cannot enter the library until its footprint exists in
`footprint_cache.json` — `regression_footprints` fails on a cache gap and
`PCB_R07` flags any placed part whose geometry is not verified. Measured
2026-08-31: **167 of the 314 queued proposals** name a real KiCad stock
footprint we simply never cached (SOIC-20W, QFN-16-1EP, VSSOP-8, HTSSOP-28…),
so a third of the review queue is blocked on nothing but a lookup.

This asks KiCad for each missing `Lib:Name` through the existing bridge and
writes the resolved geometry into the cache. It resolves ONLY footprints KiCad
itself ships — nothing is generated, guessed, or approximated here, so a
resolved entry is the vendor land pattern KiCad publishes and a failure is
reported rather than filled in.

    python -m scripts.cache_proposal_footprints            # resolve + write
    python -m scripts.cache_proposal_footprints --dry-run  # just report

Needs a machine with KiCad (the prod image has it). Costs no API quota and no
LLM tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    from dotenv import load_dotenv
    load_dotenv(_BACKEND / ".env")
except Exception:  # noqa: BLE001
    pass

from app.models import LibraryPart  # noqa: E402
from app.services import footprints as F  # noqa: E402
from app.services import kicad_bridge  # noqa: E402

PROPOSALS = _BACKEND / "app" / "library" / "proposed_parts"


def wanted_footprints() -> Counter:
    """Every `Lib:Name` the queue references, with how many proposals want it."""
    want: Counter = Counter()
    for path in sorted(PROPOSALS.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        part = raw.get("part") or raw
        fpid = (part.get("footprint") or "").strip()
        if ":" in fpid:  # a bare "SMD"/"MODULE" is not a KiCad id
            want[fpid] += 1
    return want


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N lookups")
    ap.add_argument("--from-file", metavar="PATH",
                    help="resolve the Lib:Name ids listed in this file (one per "
                         "line) instead of the ones the proposal queue needs")
    args = ap.parse_args(argv)

    if kicad_bridge.find_kicad_python() is None:
        print("KiCad is not available to the bridge — nothing can be resolved here.")
        print("Run this where KiCad is installed (the prod image has it).")
        return 1

    F._load_cache()  # noqa: SLF001 — the module's own loader
    cached = set(F._kicad_fp_cache)  # noqa: SLF001
    if args.from_file:
        lines = Path(args.from_file).read_text(encoding="utf-8").splitlines()
        want = Counter(ln.strip() for ln in lines if ":" in ln)
    else:
        want = wanted_footprints()
    missing = [(fp, n) for fp, n in want.most_common() if fp not in cached]
    print(f"{len(want)} distinct footprints referenced by the queue, "
          f"{len(want) - len(missing)} already cached, {len(missing)} missing "
          f"(blocking {sum(n for _, n in missing)} proposals)")
    if args.dry_run:
        for fp, n in missing[:40]:
            print(f"  {n:2}x {fp}")
        return 0

    ok = failed = 0
    unresolved: list[str] = []
    for i, (fpid, n) in enumerate(missing, 1):
        if args.limit and i > args.limit:
            break
        # A LibraryPart is the resolver's input shape; only `footprint` matters.
        probe = LibraryPart(mpn=f"probe-{i}", description="footprint probe",
                            category="passive", package="probe", symbol="Device:R",
                            footprint=fpid, pins=[])
        fp = F._resolve_from_kicad(probe, persist=True)  # noqa: SLF001
        if fp is not None:
            ok += 1
            print(f"[{i}/{len(missing)}] resolved {fpid}  ({len(fp.pads)} pads, {n} proposal(s))",
                  flush=True)
        else:
            failed += 1
            unresolved.append(fpid)
            print(f"[{i}/{len(missing)}] NOT IN KICAD {fpid}", flush=True)

    print(f"\nresolved {ok}, unresolved {failed}")
    if unresolved:
        print("These need an authored footprint or a corrected id "
              "(scripts/gen_footprints.py, scripts/derive_cache_entries.py):")
        for fp in unresolved[:30]:
            print(f"  {fp}")
    print("\nNext: python -m scripts.regression_footprints  (must stay 0 cache gaps)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
