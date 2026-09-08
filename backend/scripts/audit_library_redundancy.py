"""What does the library actually COVER, versus how many rows does it hold?

A catalogue-driven library grows rows much faster than it grows capability: on
2026-09-04 the resistor family held 12,370 MPNs across 776 distinct
value+size combinations — 105 different part numbers for "100 ohm 0805", any
one of which a designer would accept. Rows are not coverage, and a library
that reports rows as coverage is padding its own number.

This measures the gap per family, so a dedupe policy is chosen from data
rather than taste. It only READS the library.

    python -m scripts.audit_library_redundancy
    python -m scripts.audit_library_redundancy --family resistor --show 20
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models import LibraryPart  # noqa: E402
from app.services import library  # noqa: E402

# The identity that matters to a DESIGN. Two parts sharing it are
# interchangeable on a schematic: same value, same land pattern, same
# tolerance class. Anything the description does not state is not part of the
# identity — an unstated tolerance cannot distinguish two rows.
_RES_RE = re.compile(r"^([\d.]+ ?[kM]?(?: ohm)?) resistor, (\d{4})(?:, ([\d.]+)%)?", re.I)
_CAP_RE = re.compile(r"^([\d.]+ ?[munp]?F) (\w+) capacitor, (\d{4}), ([\d.]+)V", re.I)
_LED_RE = re.compile(r"^(\w+) LED, (\d{4})", re.I)
_IND_RE = re.compile(r"^([\d.]+ ?[munp]?H) inductor, (\d{4})", re.I)
_XTAL_RE = re.compile(r"^([\d.]+)MHz crystal, (\d+)", re.I)
_DIODE_RE = re.compile(r"^(\w+) diode, ([\w-]+)(?:, ([\d.]+)V)?", re.I)


def design_identity(part: LibraryPart) -> tuple[str, tuple] | None:
    """(family, identity) when the part is a catalogue commodity, else None.

    None means "judge this row on its own merits" — an IC, a module, a
    connector: parts where two rows are never interchangeable.
    """
    d = (part.description or "").strip()
    for family, rx in (("resistor", _RES_RE), ("capacitor", _CAP_RE),
                       ("led", _LED_RE), ("inductor", _IND_RE),
                       ("crystal", _XTAL_RE), ("diode", _DIODE_RE)):
        m = rx.match(d)
        if m:
            ident = tuple(g.lower().replace(" ", "") if g else "" for g in m.groups())
            return family, ident
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--family", help="show the worst duplicates for one family")
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args(argv)

    parts = library.all_parts()
    rows_by_family: Counter = Counter()
    idents_by_family: dict[str, set] = defaultdict(set)
    dupes: dict[str, Counter] = defaultdict(Counter)
    unique_rows = 0

    for p in parts:
        hit = design_identity(p)
        if hit is None:
            unique_rows += 1
            continue
        family, ident = hit
        rows_by_family[family] += 1
        idents_by_family[family].add(ident)
        dupes[family][ident] += 1

    print(f"LIBRARY: {len(parts)} rows\n")
    print(f"{'family':12} {'rows':>7} {'distinct':>9} {'dupes/each':>11} {'padding':>9}")
    print("-" * 52)
    total_rows = total_ident = 0
    for family, rows in rows_by_family.most_common():
        n = len(idents_by_family[family])
        total_rows += rows
        total_ident += n
        print(f"{family:12} {rows:>7} {n:>9} {rows / n:>11.1f} {rows - n:>9}")
    print("-" * 52)
    print(f"{'commodity':12} {total_rows:>7} {total_ident:>9} "
          f"{total_rows / max(total_ident, 1):>11.1f} {total_rows - total_ident:>9}")
    print(f"{'one-of-a-kind':12} {unique_rows:>7} {unique_rows:>9} {1.0:>11.1f} {0:>9}"
          "   (ICs, modules, connectors — never interchangeable)")
    print(f"\nA design can reach {total_ident + unique_rows} distinct parts; "
          f"{total_rows - total_ident} rows are alternates of something already held.")

    if args.family:
        print(f"\nworst duplication in {args.family}:")
        for ident, n in dupes[args.family].most_common(args.show):
            print(f"  {n:>4}x  {' '.join(x for x in ident if x)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
