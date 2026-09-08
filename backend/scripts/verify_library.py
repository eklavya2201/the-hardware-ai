"""Library verifier — runs the same checks the curation bot uses, but across
the entire merged library. Designed to run in CI.

Exit codes:
    0 — all parts pass
    1 — at least one part fails a check
    2 — couldn't load the library at all

Usage:
    python -m backend.scripts.verify_library
    python -m backend.scripts.verify_library --strict     # fail on warnings too
    python -m backend.scripts.verify_library --mpn ESP32-WROOM-32
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import LibraryPart
from app.services import curation
from app.services import library as lib


# Checks that are advisory rather than blocking.
ADVISORY_CHECKS = {
    "pin_count_matches_package",
    "pin_count_plausible",
    "has_power_and_ground",
    # Footprint geometry is parametric/approximate (Phase 5 / #29) — a pad/pin
    # mismatch is worth surfacing but must not block the curated library.
    "footprint_pads_match_pins",
    # Pin-type quality feeds the ERC matrix (engineering-rules Phase 0). An
    # all-passive/all-unspecified IC weakens R24 but shouldn't block the library
    # — surface it for curation follow-up.
    "pin_types_meaningful",
}


def verify_one(part: LibraryPart) -> tuple[list[str], list[str]]:
    """Return (failed_blocking, failed_advisory) check names for this part."""
    checks = curation.run_verifier(part)
    blocking: list[str] = []
    advisory: list[str] = []
    for name, ok in checks.items():
        if ok:
            continue
        if name in ADVISORY_CHECKS:
            advisory.append(name)
        else:
            blocking.append(name)
    return blocking, advisory


def cross_part_checks(parts: list[LibraryPart]) -> list[str]:
    """Library-wide checks that can't be done per-part."""
    issues: list[str] = []

    # Duplicate MPNs.
    counts = Counter(p.mpn for p in parts)
    dups = [m for m, c in counts.items() if c > 1]
    for d in dups:
        issues.append(f"duplicate MPN: {d}")

    # Companion-part references that don't resolve.
    mpn_set = {p.mpn for p in parts}
    for p in parts:
        for c in p.companions:
            if c.mpn not in mpn_set:
                issues.append(f"{p.mpn}: companion {c.mpn} ({c.role}) is not in library")

    # Alternative-MPN references that don't resolve. Soft — only warn.
    # (Alternatives are often deliberately referring to parts not in lib.)

    # Real-footprint layer (Week-2 task 4.4): every `Lib:Name` footprint a part
    # references must be present in the committed footprint cache, so prod (which
    # has no runtime KiCad) resolves verified geometry instead of silently
    # falling back to a parametric placeholder. A gap here is a cross-part defect
    # because the cache is shared library state, not per-part.
    try:
        from app.services import footprints
        footprints._load_cache()
        # Only *positive* entries count. `run_verifier` above already tried to
        # resolve every part's footprint, and a miss writes an in-memory
        # `fpid -> None` negative entry — so a plain `set(...)` here contains the
        # very ids we are looking for and the check silently passes. (That is
        # why the MAX86150 cache gap only ever showed up in
        # `scripts.regression_footprints`, which reads the file directly.)
        cached = {k for k, v in footprints._kicad_fp_cache.items() if v is not None}
        for p in parts:
            fp = (p.footprint or "").strip()
            if ":" in fp and fp not in cached:
                issues.append(
                    f"{p.mpn}: footprint {fp} missing from committed cache "
                    f"(prod would fall back to parametric geometry)"
                )
    except Exception as exc:  # noqa: BLE001
        issues.append(f"footprint-cache check could not run: {exc}")

    return issues


# Pin "numbers" that are really labels. The router and the .kicad_pcb export net
# a pin by exact string match against the footprint's pad number, and KiCad
# numbers an exposed pad N+1 (QFN-24-1EP -> "25"), so a pin numbered "PAD" lands
# on nothing and the thermal/GND pad is silently left unconnected.
_EP_LABELS = {"EP", "PAD", "EPAD", "TAB", "0", "THERMAL"}


def exposed_pad_checks(parts: list[LibraryPart]) -> list[str]:
    """Advisory: EP numbered by label, or a *-1EP footprint whose EP pad has no pin."""
    from app.services import footprints
    footprints._load_cache()
    cache = footprints._kicad_fp_cache
    out: list[str] = []
    for p in parts:
        fp = cache.get((p.footprint or "").strip())
        pad_nums = {str(q.pin_number) for q in fp.pads} if fp else set()
        # A label is fine when the footprint itself numbers the pad that way
        # (Nordic AQFN-73 -> "EP"); otherwise it lands on nothing.
        for x in p.pins:
            n = str(x.number).strip()
            if n.upper() in _EP_LABELS and n not in pad_nums and not p.mpn.startswith("Arduino_"):
                out.append(f"{p.mpn}: pin '{x.number}' ({x.name}) is a label, not a pad "
                           f"number — it nets nothing; number the EP as last lead + 1")
        if not fp or "-1EP" not in (p.footprint or ""):
            continue
        nums = [int(q.pin_number) for q in fp.pads if str(q.pin_number).isdigit()]
        labelled_pads = [n for n in pad_nums if n.upper() in _EP_LABELS]
        ep = labelled_pads[0] if labelled_pads else (str(max(nums)) if nums else None)
        if ep and ep not in {str(x.number) for x in p.pins}:
            out.append(f"{p.mpn}: footprint {p.footprint} has EP pad {ep} "
                       f"but no pin lists it — the thermal/GND pad will never be netted")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verify the curated parts library")
    p.add_argument("--strict", action="store_true",
                   help="Fail on advisory issues too (default: blocking only)")
    p.add_argument("--mpn", type=str, default=None,
                   help="Verify a single MPN")
    p.add_argument("--show-passes", action="store_true",
                   help="Print parts that pass too (default: only fails)")
    args = p.parse_args(argv)

    try:
        parts = lib.all_parts()
    except Exception as exc:    # noqa: BLE001
        print(f"Couldn't load library: {exc}", file=sys.stderr)
        return 2

    if args.mpn:
        parts = [x for x in parts if x.mpn == args.mpn]
        if not parts:
            print(f"No part with MPN {args.mpn} in library.", file=sys.stderr)
            return 2

    blocking_failures = 0
    advisory_failures = 0
    per_check_fail: dict[str, int] = defaultdict(int)
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})

    for part in parts:
        blocking, advisory = verify_one(part)
        if blocking:
            blocking_failures += 1
            by_category[part.category]["fail"] += 1
            print(f"[FAIL] {part.mpn:30s} [{part.category}] blocking: {', '.join(blocking)}")
            for c in blocking:
                per_check_fail[c] += 1
        elif advisory:
            advisory_failures += 1
            by_category[part.category]["pass"] += 1
            print(f"[WARN] {part.mpn:30s} [{part.category}] advisory: {', '.join(advisory)}")
            for c in advisory:
                per_check_fail[c] += 1
        else:
            by_category[part.category]["pass"] += 1
            if args.show_passes:
                print(f"[OK]   {part.mpn:30s} [{part.category}]")

    print()
    cross_issues = cross_part_checks(parts)
    for ci in cross_issues:
        print(f"[FAIL] cross-part: {ci}")
    ep_issues = exposed_pad_checks(parts)
    for ei in ep_issues:
        print(f"[WARN] exposed-pad: {ei}")
    advisory_failures += len(ep_issues)

    print()
    print("=" * 70)
    print(f"Total parts checked: {len(parts)}")
    print(f"Blocking failures:   {blocking_failures}")
    print(f"Advisory failures:   {advisory_failures}")
    print(f"Cross-part issues:   {len(cross_issues)}")
    print(f"Exposed-pad issues:  {len(ep_issues)}")
    print()
    print("Failures by category:")
    for cat, counts in sorted(by_category.items()):
        if counts["fail"]:
            print(f"  {cat:15s}  {counts['fail']} fail / {counts['pass']} pass")
    print()
    print("Most common failed checks:")
    for chk, n in sorted(per_check_fail.items(), key=lambda x: -x[1])[:10]:
        kind = "advisory" if chk in ADVISORY_CHECKS else "BLOCKING"
        print(f"  {n:4d}  {chk}  ({kind})")

    if blocking_failures or cross_issues:
        return 1
    if args.strict and advisory_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
