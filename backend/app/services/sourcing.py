"""Can this BOM actually be bought today? (docs/BOM_LANE.md B5)

`manufacturability_report` gated on footprints, board sync, R43 and
completeness — zero sourcing inputs — so Synqit would call a design ready to
manufacture with a third of it unbuyable. This is the BOM-level verdict a
student can trust, computed from the same cached distributor data the BOM
shows (never the network; refresh is the user's click).

Per line, in severity order:
- eol           lifecycle says obsolete / discontinued / end of life  -> blocks
- out_of_stock  distributor stock is 0                                -> blocks
- short_stock   0 < stock < quantity needed                           -> blocks
- nrnd          not recommended for new designs                       -> warns
- moq           must buy more than needed (MOQ > qty)                 -> warns
- stale         quote older than the cache TTL                        -> warns
- unverified    no distributor data at all                            -> NOT a
                blocker: a keyless install must still be able to export, and
                "we did not look" is not "it cannot be bought". It is reported
                honestly as unverified, never as OK.
- ok            live, in stock for the quantity, active lifecycle

Where a line fails, the library's `alternatives` (until now used only for R43
matching) are proposed with THEIR cached status, so the second source is one
click, not a search.

Same honest-surfaces discipline as the rules: the verdict must fire on an
unbuyable BOM and stay silent on a clean one — `regression_sourcing.py`.
"""

from __future__ import annotations

import re

from app.models import Project
from app.services import library, pricing

_EOL_RE = re.compile(r"obsolete|discontinu|end.of.life|\bEOL\b|last.time.buy", re.I)
_NRND_RE = re.compile(r"not.recommended|\bNRND\b", re.I)

BLOCKING = {"eol", "out_of_stock", "short_stock"}


def _lifecycle_status(text: str | None) -> str | None:
    if not text:
        return None
    if _EOL_RE.search(text):
        return "eol"
    if _NRND_RE.search(text):
        return "nrnd"
    return None


def assess_line(mpn: str, qty_needed: int, *, now: float | None = None) -> dict:
    lp, stale = pricing.cached_price_any(mpn, now=now)
    part = library.get_part(mpn)
    reasons: list[str] = []
    status = "ok"
    if lp is None:
        status = "unverified"
        reasons.append("not checked against a distributor yet — use Live prices")
    else:
        life = _lifecycle_status(lp.lifecycle)
        if life == "eol":
            status = "eol"
            reasons.append(f"{lp.vendor} lists it as {lp.lifecycle} — do not design it in")
        elif lp.stock is not None and lp.stock <= 0:
            status = "out_of_stock"
            lead = f", factory lead {lp.leadDays // 7} weeks" if lp.leadDays else ""
            reasons.append(f"0 in stock at {lp.vendor}{lead}")
        elif lp.stock is not None and lp.stock < qty_needed:
            status = "short_stock"
            reasons.append(f"only {lp.stock} in stock at {lp.vendor}, {qty_needed} needed")
        elif life == "nrnd":
            status = "nrnd"
            reasons.append(f"{lp.vendor} marks it {lp.lifecycle} — fine for a prototype, risky for production")
        if lp.moq and lp.moq > qty_needed and status in {"ok", "nrnd"}:
            if status == "ok":
                status = "moq"
            reasons.append(f"minimum order {lp.moq} at {lp.vendor} — you buy {lp.moq}, not {qty_needed}")
        if stale:
            if status == "ok":
                status = "stale"
            reasons.append("quote is older than a day — refresh before ordering")
    alternatives: list[dict] = []
    if status in BLOCKING and part is not None:
        for alt in part.alternatives:
            a_lp, a_stale = pricing.cached_price_any(alt, now=now)
            a_status = "unverified"
            if a_lp is not None:
                a_status = assess_line(alt, qty_needed, now=now)["status"]
            alternatives.append({"mpn": alt, "status": a_status,
                                 "stock": a_lp.stock if a_lp else None,
                                 "vendor": a_lp.vendor if a_lp else None})
    return {
        "mpn": mpn,
        "qtyNeeded": qty_needed,
        "status": status,
        "blocking": status in BLOCKING,
        "reasons": reasons,
        "vendor": lp.vendor if lp else None,
        "sku": lp.sku if lp else None,
        "stock": lp.stock if lp else None,
        "moq": lp.moq if lp else None,
        "lifecycle": lp.lifecycle if lp else None,
        "stale": stale,
        "alternatives": alternatives,
    }


def assess(project: Project, *, boards: int = 1, now: float | None = None) -> dict:
    """The BOM verdict. `boards` multiplies every quantity — an order is for a
    run, not a board, and a part with 30 in stock is fine for 1 board and
    short for 50."""
    boards = max(1, int(boards or 1))
    by_mpn: dict[str, list[str]] = {}
    for c in project.ir.components:
        by_mpn.setdefault(c.mpn, []).append(c.refdes)
    lines = []
    for mpn, refs in by_mpn.items():
        row = assess_line(mpn, len(refs) * boards, now=now)
        row["refdes"] = refs[0]
        row["qtyPerBoard"] = len(refs)
        lines.append(row)
    lines.sort(key=lambda r: r["refdes"])
    blockers = [l for l in lines if l["blocking"]]
    warnings = [l for l in lines if not l["blocking"] and l["status"] not in {"ok", "unverified"}]
    unverified = [l for l in lines if l["status"] == "unverified"]
    if blockers:
        verdict = "blocked"
    elif len(unverified) == len(lines) and lines:
        verdict = "unverified"
    elif unverified:
        verdict = "partial"
    else:
        verdict = "ready"
    return {
        "verdict": verdict,
        "boards": boards,
        "lines": lines,
        "blockers": len(blockers),
        "warnings": len(warnings),
        "unverified": len(unverified),
        "checked": len(lines) - len(unverified),
        "total": len(lines),
        "vendors": pricing.vendors_configured(),
    }
