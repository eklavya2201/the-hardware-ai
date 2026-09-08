"""FreeRouting no-progress watchdog regression.

On an unroutable board (overlapping placement) FreeRouting v2 loops forever —
identical score/unrouted/violations every ~2 s pass, ignoring -mp — until our
hard timeout killed it with a generic message. The StallDetector parses the
per-pass log lines and aborts after STALL_PASSES identical results.

Guards: the pass-line regex against a real v2.1.0 log line; progress resets
the stall counter; a routed board (0 unrouted) never trips the detector even
when scores repeat during optimisation passes.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_freerouting_watchdog
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.routing_freerouting import STALL_PASSES, StallDetector

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        _failures.append(label)


# Verbatim shape of a FreeRouting v2.1.0 log line (from the prod incident).
def _line(pass_no: int, score: float, unrouted: int, violations: int) -> str:
    return (
        f"2026-07-21 17:03:35.245 INFO   [CF269B\\B985F4] Auto-router pass "
        f"#{pass_no} on board 'a458a48e' was completed in 2.13 seconds with "
        f"the score of {score:.2f} ({unrouted} unrouted and {violations} "
        f"violations), using 14865.90 CPU seconds and 84176 MB memory."
    )


def main() -> int:
    print("regression_freerouting_watchdog")

    d = StallDetector()
    check("non-pass lines are ignored", d.feed("INFO Settings were loaded") is None)

    # Stalled board: identical stats every pass -> abort after STALL_PASSES.
    d = StallDetector()
    reasons = [d.feed(_line(i, 212.40, 86, 4)) for i in range(1, STALL_PASSES + 2)]
    check(
        f"stall detected after {STALL_PASSES} identical repeats",
        reasons[STALL_PASSES] is not None and "86 unrouted" in reasons[STALL_PASSES],
        detail=str(reasons[STALL_PASSES]),
    )
    check("no premature abort while counting", all(r is None for r in reasons[:STALL_PASSES]))

    # Progress resets the counter.
    d = StallDetector()
    for i in range(1, STALL_PASSES + 1):
        d.feed(_line(i, 137.03, 92, 4))
    trip = d.feed(_line(STALL_PASSES + 1, 150.00, 60, 2))  # improvement
    check("progress resets the stall counter", trip is None)
    more = [
        d.feed(_line(STALL_PASSES + 1 + i, 150.00, 60, 2))
        for i in range(1, STALL_PASSES + 1)
    ]
    check("re-stall after progress still aborts", more[-1] is not None)

    # Fully routed board: repeated stats with 0 unrouted must never abort —
    # optimisation passes legitimately plateau.
    d = StallDetector()
    verdicts = [d.feed(_line(i, 300.0, 0, 0)) for i in range(1, STALL_PASSES * 3)]
    check("0-unrouted plateau never trips the watchdog", all(v is None for v in verdicts))

    # FreeRouting v2 drops the violations clause once violations hit zero —
    # the exact line format prod produced for 250+ passes on the round wearable
    # (review #22) while the old regex never matched. Must trip.
    d = StallDetector()
    prod_line = ("2026-08-18 19:18:15.131 INFO   [D903B6\\EBE4FA] Auto-router pass #{n} on board "
                 "'1de542b2d0779b6242757a6e410cbc11' was completed in 0.62 seconds with the score of "
                 "948.13 (8 unrouted), using 21519.91 CPU seconds and 73332 MB memory.")
    verdicts = [d.feed(prod_line.format(n=i)) for i in range(250, 250 + STALL_PASSES + 2)]
    check("prod line WITHOUT a violations clause is parsed", d.last_pass is not None and "8 unrouted" in d.last_pass)
    check("prod no-progress loop (8 unrouted, no violations clause) trips the watchdog",
          verdicts[-1] is not None and "8 unrouted" in verdicts[-1], str(verdicts[-1]))

    if _failures:
        print(f"{len(_failures)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
