"""Real-footprint layer regression gate — Week-2 task 4.4.

Guards the invariant that the KiCad-foundation "real footprint" layer stays
**0-blocking / 0-cross-part**: every curated part must resolve to fab-final
geometry with zero export blockers, purely from the *committed*
`footprint_cache.json` — i.e. the exact path prod takes with no runtime KiCad.

Why cache-only: prod (Netlify/Render) has no KiCad; it resolves footprints from
the committed cache. So this gate forces `find_kicad_python`/`find_kicad_cli` to
None and validates what actually ships — not whatever the dev box happens to
have installed. If the cache regresses (a part drops to parametric/generic, a
critical part loses pad coverage, or an fpid falls out of the cache), CI fails
here instead of a user discovering it at fab time.

Exit codes:
    0 — the footprint layer is 0-blocking / 0-cross-part (and fully verified)
    1 — a regression (blockers, unverified criticals, or cache gaps)
    2 — couldn't load the library at all

Usage:
    python -m scripts.regression_footprints
    python -m scripts.regression_footprints --allow-approximate   # non-critical
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# The current shipped baseline — every curated part resolves to a verified real
# KiCad footprint from the committed cache. Bump this only when the library
# legitimately grows; it exists so a silent *shrink* (parts lost, cache gutted)
# is caught too.
VERIFIED_BASELINE = 532


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Footprint-layer regression gate")
    ap.add_argument(
        "--allow-approximate",
        action="store_true",
        help="Permit non-critical parts to be 'approximate' (still 0-blocking).",
    )
    ap.add_argument(
        "--use-kicad",
        action="store_true",
        help="Resolve via live KiCad too (default: cache-only, the prod path).",
    )
    args = ap.parse_args(argv)

    from app.services import kicad_bridge, footprints

    if not args.use_kicad:
        # Validate the committed-cache path exactly as prod resolves it.
        kicad_bridge.find_kicad_python = lambda: None  # type: ignore[assignment]
        kicad_bridge.find_kicad_cli = lambda: None      # type: ignore[assignment]
        kicad_bridge.reset_cache()

    try:
        from app.services import library as lib
        parts = lib.all_parts()
    except Exception as exc:  # noqa: BLE001
        print(f"Couldn't load library: {exc}", file=sys.stderr)
        return 2

    conf = collections.Counter()
    blockers: list[str] = []
    missing_pads: list[str] = []
    unverified_critical: list[str] = []
    # Cross-part: a footprint id used by a part but absent from the resolved
    # cache (would silently fall back to parametric geometry on a KiCad-less box).
    cache_gaps: list[str] = []

    footprints._load_cache()
    resolved_ids = set(footprints._kicad_fp_cache)

    critical = footprints._CRITICAL_FOOTPRINT_CATEGORIES

    for p in parts:
        status = footprints.footprint_status(p)
        conf[status["confidence"]] += 1

        if status["blocksExport"]:
            blockers.append(
                f"{p.mpn} [{p.category}] {status['confidence']} "
                f"missing={status['missingPads']}"
            )
        if status["missingPads"]:
            missing_pads.append(f"{p.mpn}: {status['missingPads']}")
        if p.category in critical and status["confidence"] != "verified":
            unverified_critical.append(f"{p.mpn} [{p.category}] {status['confidence']}")

        fpid = _fpid(p)
        # A verified part MUST have its Lib:Name in the committed cache; a colon
        # fpid that's absent means prod would silently fall back to parametric.
        if fpid and fpid not in resolved_ids:
            cache_gaps.append(f"{p.mpn}: {fpid}")

    verified = conf.get("verified", 0)
    total = len(parts)

    print("=" * 70)
    print(f"Parts checked:        {total}")
    print(f"Confidence breakdown: {dict(conf)}")
    print(f"Export blockers:      {len(blockers)}")
    print(f"Missing-pad parts:    {len(missing_pads)}")
    print(f"Unverified criticals: {len(unverified_critical)}")
    print(f"Cache gaps (x-part):  {len(cache_gaps)}")
    print(f"Verified baseline:    {verified}/{total} (baseline ≥ {VERIFIED_BASELINE})")
    print("=" * 70)

    failed = False

    if blockers:
        failed = True
        print(f"\n[FAIL] {len(blockers)} export blocker(s) — the footprint layer is NOT 0-blocking:")
        for b in blockers[:30]:
            print(f"   - {b}")

    if unverified_critical and not args.allow_approximate:
        failed = True
        print(f"\n[FAIL] {len(unverified_critical)} critical part(s) not verified:")
        for u in unverified_critical[:30]:
            print(f"   - {u}")

    if cache_gaps:
        failed = True
        print(f"\n[FAIL] {len(cache_gaps)} cache gap(s) — fpid not in committed cache (would fall back):")
        for g in cache_gaps[:30]:
            print(f"   - {g}")

    if verified < VERIFIED_BASELINE:
        failed = True
        print(
            f"\n[FAIL] verified footprint count regressed: {verified} < "
            f"baseline {VERIFIED_BASELINE}."
        )

    if missing_pads:
        # Missing pads on a non-critical part is advisory (won't block), but a
        # missing pad that flips blocksExport is already caught above.
        print(f"\n[WARN] {len(missing_pads)} part(s) have signal pins without a pad:")
        for m in missing_pads[:15]:
            print(f"   - {m}")

    if failed:
        print("\nFOOTPRINT REGRESSION — the real-footprint layer is not 0-blocking / 0-cross-part.")
        return 1

    print("\nOK — footprint layer is 0-blocking / 0-cross-part, fully verified.")
    return 0


def _fpid(part) -> str | None:
    """The `Lib:Name` footprint id used for cache resolution (mirror of
    footprints._resolve_from_kicad's key derivation)."""
    fp = getattr(part, "footprint", None)
    if fp and ":" in fp:
        return fp
    return None


if __name__ == "__main__":
    raise SystemExit(main())
