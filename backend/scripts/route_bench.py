"""Route the stored sweep designs and report where nets actually fail.

A real corpus of agent-built boards beats a synthetic one: the USB-C THT-row
complaint only ever appeared on congested designs, and a clean two-part test
board routes 0-rat at every grid pitch.

    BENCH_START=0 BENCH_N=12 python route_bench.py
"""

import glob
import json
import os
import sys
import time

sys.path.insert(0, r"C:\Users\pande\Cursor-for-hardware\backend")

from app.models import Project          # noqa: E402
from app.services import placement, routing  # noqa: E402

ALL = sorted(
    f for f in glob.glob(
        r"C:\Users\pande\Cursor-for-hardware\docs\sweeps\2026-08-19\*.json")
    if not f.endswith(".rec.json")
)
START = int(os.environ.get("BENCH_START", "0"))
N = int(os.environ.get("BENCH_N", "12"))
files = ALL[START:START + N]
print(f"routing boards {START}..{START + len(files) - 1} of {len(ALL)}", flush=True)

total_rats = 0
by_net: dict[str, int] = {}
worst: list[tuple[int, str, list[str]]] = []
t0 = time.time()
done = 0

for f in files:
    name = os.path.basename(f)
    try:
        p = Project(**json.load(open(f, encoding="utf-8")))
        if not p.ir.components:
            continue
        p.board = placement.auto_place(p)
        t1 = time.time()
        b = routing.auto_route(p)
        dt = time.time() - t1
    except Exception as e:  # noqa: BLE001
        print(f"  [skip] {name}: {type(e).__name__}: {e}", flush=True)
        continue
    done += 1
    n = len(b.rats)
    total_rats += n
    for r in b.rats:
        by_net[r.net] = by_net.get(r.net, 0) + 1
    if n:
        worst.append((n, name, sorted({r.net for r in b.rats})))
    print(f"  {name:34s} {len(p.ir.components):3d} parts  "
          f"{p.board.width:5.1f}x{p.board.height:5.1f}  {n:3d} rats  {dt:5.1f}s", flush=True)

worst.sort(reverse=True)
print(f"\n{done} boards routed in {time.time() - t0:.0f}s — {total_rats} rats total")
print(f"{len(worst)} boards with rats, {done - len(worst)} clean")
if worst:
    print("\nworst boards:")
    for n, name, nets in worst[:12]:
        print(f"  {n:3d} rats  {name:34s} {', '.join(nets[:6])}")
if by_net:
    print("\nnets that fail most:")
    for net, n in sorted(by_net.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {n:3d}  {net}")
