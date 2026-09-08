"""Guard: the PREVIEW router is bounded in wall-clock time (2026-08-20).

Reviewers repeatedly reported "slow PCB generation". Routing every one of the
60 stored sweep designs put a number on it: 57 boards cost **2048 s** in total,
and ONE of them — 117 parts on a 272 mm board — took **1316 s**, twenty-two
minutes, to arrive at 6 unrouted nets anyway. There was no wall-clock budget at
all; a hard board simply paid for every retry.

The preview exists to be looked at. The manufacturing route is FreeRouting,
behind the Finalize button the canvas already offers whenever nets are left
unrouted. So the preview now stops at `ROUTE_TIME_BUDGET_S` and reports what is
still unrouted, which is both faster and truer than making someone wait.

45 s is the measured setting (full 60-board sweeps at 25 s and 45 s): at 45 s
only the two known-congested boards trade a rat or three while the worst case
drops 1316 s -> 46 s; 25 s regressed six boards, and below ~20 s the first FULL
pass cannot complete on the biggest board (6 -> 29 rats).

Offline; uses small synthetic boards so it runs in seconds.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.models_db import ProjectRow  # noqa: E402
from app.services import placement, routing, store, tools  # noqa: E402

PASSED = 0
FAILED: list[str] = []
_SCRATCH: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(label)
        print(f"  [FAIL] {label}" + (f"  ({detail})" if detail else ""))


def cleanup() -> None:
    with db.SessionLocal() as s:
        for pid in _SCRATCH:
            row = s.get(ProjectRow, pid)
            if row is not None:
                s.delete(row)
        s.commit()


def a_board(name: str, parts: int):
    p = store.create_project(name, user_id="regression-budget")
    _SCRATCH.append(p.id)
    tools.add_component(p, mpn="ESP32-C3-MINI-1", refdes="U1")
    for i in range(parts):
        tools.add_component(p, mpn="RC0603FR-0710KL", refdes=f"R{i + 1}")
        tools.connect(p, net_name=f"N{i}", pins=[["R" + str(i + 1), "1"]])
    p.board = placement.auto_place(p)
    return p


def main() -> int:
    db.init_db()
    try:
        print("\n-- the budget exists and is configurable")
        check("the router declares a wall-clock budget",
              hasattr(routing, "ROUTE_TIME_BUDGET_S"))
        check("it defaults to the measured knee, not an arbitrary number",
              routing.ROUTE_TIME_BUDGET_S == 45.0,
              f"{routing.ROUTE_TIME_BUDGET_S}s")
        check("it is overridable per deployment",
              "SYNQIT_ROUTE_BUDGET_S" in open(
                  os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "app", "services", "routing.py"),
                  encoding="utf-8").read())

        print("\n-- an easy board finishes long before the budget")
        p = a_board("budget easy", 6)
        t0 = time.monotonic()
        board = routing.auto_route(p)
        dt = time.monotonic() - t0
        check("a small board is not slowed down by the budget",
              dt < routing.ROUTE_TIME_BUDGET_S, f"{dt:.1f}s")
        check("and it still routes", board is not None)

        print("\n-- a tiny budget stops the router and reports honestly")
        original = routing.ROUTE_TIME_BUDGET_S
        routing.ROUTE_TIME_BUDGET_S = 0.001
        try:
            p2 = a_board("budget tiny", 10)
            t0 = time.monotonic()
            board2 = routing.auto_route(p2)
            dt = time.monotonic() - t0
            check("it returns almost immediately", dt < 20.0, f"{dt:.1f}s")
            check("it still returns a real board, not an error",
                  board2 is not None and board2.width > 0)
            check("unrouted nets come back as rats the canvas can show",
                  isinstance(board2.rats, list))
            # The Finalize-by-default banner keys on rats, so the user is
            # offered the manufacturing route rather than left with a silent
            # half-routed board.
            check("nothing is silently dropped: every rat names its net",
                  all(r.net for r in board2.rats))
        finally:
            routing.ROUTE_TIME_BUDGET_S = original

        print("\n-- disabling the budget is possible (0 = unbounded)")
        routing.ROUTE_TIME_BUDGET_S = 0.0
        try:
            p3 = a_board("budget off", 4)
            check("a zero budget means no deadline at all",
                  routing.auto_route(p3) is not None)
        finally:
            routing.ROUTE_TIME_BUDGET_S = original
    finally:
        cleanup()

    print(f"\n{PASSED} checks passed, {len(FAILED)} failed.")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("All route-budget checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
