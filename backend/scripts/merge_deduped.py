"""Merge catalogue discoveries WITHOUT importing the catalogue's redundancy.

The 2026-09-04 discovery run returned 12,498 new parts, 11,681 of them chip
resistors — covering 776 distinct value+size+tolerance combinations. That is
105 part numbers for "100 ohm 0805", any one of which a designer would accept.
Rows are not coverage: merging all of them would take the library from 18%
alternates to 44%, slow every library surface, and inflate the part count the
landing page publishes.

So a commodity part enters only until its DESIGN IDENTITY has `--keep`
alternates. Alternates are worth having (one vendor is out of stock, another
is not — `services/sourcing.py` proposes them), which is why the answer is a
few and not one. Preference order within an identity:

  1. a different manufacturer from the ones already kept — that is what makes
     an alternate useful; three MPNs from one vendor share one supply risk
  2. a datasheet URL over none
  3. MPN order, so a re-run picks the same parts (this must be deterministic:
     a merge that shuffles its own output cannot be reviewed)

Existing library rows are NEVER removed or reordered. A stored design
references parts by MPN and `add_component` rejects an MPN the library does
not hold, so pruning a part would break boards that already use it. Pruning
is a separate job that must read the projects table first.

    python -m scripts.merge_deduped --from curator.json --dry-run
    python -m scripts.merge_deduped --from curator.json --keep 3
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models import LibraryPart  # noqa: E402
from app.services import library  # noqa: E402

# Same identities the redundancy audit measures. "ohm" and "Ω" both appear —
# the field was written by two versions of the discovery script.
_RES_RE = re.compile(r"^([\d.]+\s?[kM]?)(?:\s?(?:ohm|Ω))? resistor, (\d{4})(?:, ([\d.]+)%)?", re.I)
_CAP_RE = re.compile(r"^([\d.]+\s?[munp]?F) (\w+) capacitor, (\d{4}), ([\d.]+)V", re.I)
_LED_RE = re.compile(r"^(\w+) LED, (\d{4})", re.I)
_IND_RE = re.compile(r"^([\d.]+\s?[munp]?H) inductor, (\d{4})", re.I)
_XTAL_RE = re.compile(r"^([\d.]+)MHz crystal, (\d+)", re.I)
_DIODE_RE = re.compile(r"^(\w+) diode, ([\w-]+)(?:, ([\d.]+)V)?", re.I)

_FAMILIES = (("resistor", _RES_RE), ("capacitor", _CAP_RE), ("led", _LED_RE),
             ("inductor", _IND_RE), ("crystal", _XTAL_RE), ("diode", _DIODE_RE))

# Manufacturer read from the MPN's leading letters when the field is empty —
# catalogue rows often carry the series prefix and nothing else.
_PREFIX_RE = re.compile(r"^([A-Za-z]{1,5})")


def identity(desc: str) -> tuple[str, tuple] | None:
    d = (desc or "").strip()
    for family, rx in _FAMILIES:
        m = rx.match(d)
        if m:
            return family, tuple((g or "").lower().replace(" ", "") for g in m.groups())
    return None


def vendor_of(part: dict) -> str:
    mfr = (part.get("manufacturer") or "").strip().lower()
    if mfr:
        return mfr
    m = _PREFIX_RE.match(part.get("mpn") or "")
    return m.group(1).lower() if m else ""


def load(path: Path) -> list[dict]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="src", required=True,
                    help="a user_parts.json (or .gz) to merge FROM")
    ap.add_argument("--keep", type=int, default=3,
                    help="alternates to keep per design identity (default 3)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    incoming = load(Path(args.src))
    held = library.all_parts()
    have = {p.mpn.upper() for p in held}

    # What the library ALREADY holds per identity counts against the budget:
    # a run that ignores this re-fills identities that are already covered.
    covered: Counter = Counter()
    vendors: dict[tuple, set[str]] = defaultdict(set)
    for p in held:
        hit = identity(p.description)
        if hit:
            covered[hit] += 1
            vendors[hit].add(vendor_of(p.model_dump()))

    fresh = [p for p in incoming if (p.get("mpn") or "").upper() not in have]
    # Deterministic: sort once, then take in order.
    fresh.sort(key=lambda p: ((p.get("provenance") or {}).get("datasheetUrl") is None,
                              p.get("mpn") or ""))

    kept: list[dict] = []
    dropped = Counter()
    # Two passes so a NEW vendor beats a third MPN from a vendor already kept.
    for want_new_vendor in (True, False):
        for p in fresh:
            if p in kept:
                continue
            hit = identity(p.get("description") or "")
            if hit is None:                      # one-of-a-kind: always keep
                if want_new_vendor:
                    kept.append(p)
                continue
            if covered[hit] >= args.keep:
                continue
            v = vendor_of(p)
            if want_new_vendor and v in vendors[hit]:
                continue
            kept.append(p)
            covered[hit] += 1
            vendors[hit].add(v)
    kept_ids = {id(p) for p in kept}
    for p in fresh:
        if id(p) not in kept_ids:
            hit = identity(p.get("description") or "")
            dropped[hit[0] if hit else "other"] += 1

    print(f"incoming {len(incoming)} | new to us {len(fresh)}")
    print(f"KEEP {len(kept)}  (<= {args.keep} alternates per design identity)")
    print(f"DROP {sum(dropped.values())}  already covered: {dict(dropped.most_common())}")

    if not kept:
        return 0
    for p in kept[:6]:
        print(f"   + {p['mpn']:24} {p.get('description','')[:46]}")
    if args.dry_run:
        print("\n[dry run] nothing written")
        return 0

    validated = [LibraryPart.model_validate(p) for p in kept]
    path = Path(library._USER_PARTS_PATH)  # noqa: SLF001
    existing = json.loads(path.read_text(encoding="utf-8"))
    existing.extend(p.model_dump(exclude_none=True) for p in validated)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    tmp.replace(path)
    library._user_cache = None  # noqa: SLF001
    print(f"\nuser_parts.json {len(existing) - len(validated)} -> {len(existing)}")
    print("next: python -m scripts.verify_library && python -m scripts.regression_footprints")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
