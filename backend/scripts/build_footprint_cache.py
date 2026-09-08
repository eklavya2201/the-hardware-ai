"""Pre-generate the real-KiCad footprint geometry cache (task 3.2).

`footprints.for_part` resolves each part's `Lib:Name` footprint against KiCad's
stock libraries the first time it's seen, then caches the geometry to
`app/library/footprint_cache.json`. Running this once (on a machine with KiCad
installed) populates that file for the whole library so production reads it
instantly instead of shelling out to KiCad on the first build. It also prints a
verification summary (how many parts got real geometry vs. couldn't resolve),
which doubles as a data-quality report on the library's footprint strings.

Only positive (resolved) footprints are written to the cache; parts whose
footprint id doesn't map to a real KiCad footprint are reported here and flagged
at runtime by verifier rule PCB_R07.

Usage (needs KiCad installed / discoverable by the bridge):
    python -m scripts.build_footprint_cache
    python -m scripts.build_footprint_cache --show-unresolved
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.services import footprints as F  # noqa: E402
from app.services import kicad_bridge  # noqa: E402
from app.services import library  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show-unresolved", action="store_true",
                    help="list every part whose footprint didn't resolve")
    args = ap.parse_args()

    if not kicad_bridge.is_available():
        print("ERROR: KiCad not available — install it or set KICAD_PYTHON_BIN.",
              file=sys.stderr)
        print(f"  probe: {kicad_bridge.probe()}", file=sys.stderr)
        return 1
    print(f"KiCad: {kicad_bridge.probe()}")

    parts = library.all_parts()
    conf = Counter()
    unresolved: list[tuple[str, str]] = []

    # Resolve every part; defer the disk write to one flush at the end.
    for p in parts:
        fp = F._resolve_from_kicad(p, persist=False)
        final = F.for_part(p)
        conf[final.confidence] += 1
        if fp is None and ":" in (p.footprint or ""):
            unresolved.append((p.mpn, p.footprint))

    F._persist_cache()

    resolved = sum(1 for v in F._kicad_fp_cache.values() if v is not None)
    print(f"\nWrote {resolved} verified footprints -> {F._CACHE_PATH}")
    print(f"confidence across {len(parts)} parts: {dict(conf)}")
    print(f"unresolved Lib:Name footprints (data errors, flagged by PCB_R07): "
          f"{len(unresolved)}")

    if args.show_unresolved:
        uni = Counter(fpid for _, fpid in unresolved)
        print("\n-- unresolved footprint ids --")
        for fpid, n in uni.most_common():
            print(f"  {n:3d}x  {fpid}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
