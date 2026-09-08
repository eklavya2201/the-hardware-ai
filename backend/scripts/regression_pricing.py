"""Regression guard for honest BOM sourcing (no network, no key needed).

Asserts:
1. Mouser response parsing — exact-MPN preference, price/stock extraction,
   no-price and no-match cases return None (we never fabricate).
2. Cache TTL — a fresh entry resolves, an expired one doesn't, a corrupt
   cache file degrades to empty.
3. Keyless degrade — refresh() with no MOUSER_API_KEY returns (0, 0) and
   never touches the network.
4. recompute_bom provenance — a live-cached MPN labels "live" with stock,
   a library part labels "reference", an unknown/zero-cost part "none".

Run: python -m scripts.regression_pricing
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Component, Project, SchematicIR
from app.services import pricing, tools


def check(name: str, ok: bool) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return ok


MOUSER_FIXTURE = {
    "SearchResults": {
        "Parts": [
            {
                "ManufacturerPartNumber": "OTHER-PART",
                "Availability": "5 In Stock",
                "PriceBreaks": [{"Quantity": 1, "Price": "$9.99", "Currency": "USD"}],
            },
            {
                "ManufacturerPartNumber": "ESP32-WROOM-32",
                "Availability": "2,553 In Stock",
                "PriceBreaks": [
                    {"Quantity": 10, "Price": "$2.95", "Currency": "USD"},
                    {"Quantity": 1, "Price": "$3.10", "Currency": "USD"},
                ],
            },
        ]
    }
}


def main() -> int:
    results: list[bool] = []

    # --- 1. Parsing --------------------------------------------------------
    lp = pricing._parse_mouser("ESP32-WROOM-32", MOUSER_FIXTURE, now=1000.0)
    results.append(check("exact MPN preferred over first hit", lp is not None and lp.price == 3.10))
    results.append(check("qty-1 break chosen", lp is not None and lp.price == 3.10))
    results.append(check("stock parsed with thousands separator", lp is not None and lp.stock == 2553))
    results.append(check("no match -> None", pricing._parse_mouser("X", {"SearchResults": {"Parts": []}}) is None))
    results.append(check(
        "no price breaks -> None",
        pricing._parse_mouser("A", {"SearchResults": {"Parts": [{"ManufacturerPartNumber": "A"}]}}) is None,
    ))

    # --- 2 + 3 + 4 need a scratch cache + clean env ------------------------
    real_cache = pricing.CACHE_PATH
    scratch = Path(__file__).resolve().parent / "_price_cache_test.json"
    pricing.CACHE_PATH = scratch
    old_key = os.environ.pop("MOUSER_API_KEY", None)
    try:
        now = time.time()
        scratch.write_text(json.dumps({
            "FRESH-1": {"mpn": "FRESH-1", "price": 1.5, "currency": "USD", "stock": 42,
                        "vendor": "Mouser", "fetchedAt": now - 60},
            "STALE-1": {"mpn": "STALE-1", "price": 2.5, "currency": "USD", "stock": 7,
                        "vendor": "Mouser", "fetchedAt": now - pricing.CACHE_TTL_S - 60},
        }), encoding="utf-8")

        results.append(check("fresh cache entry resolves", pricing.cached_price("FRESH-1") is not None))
        results.append(check("expired cache entry rejected", pricing.cached_price("STALE-1") is None))

        scratch.write_text("not json", encoding="utf-8")
        results.append(check("corrupt cache degrades to empty", pricing.cached_price("FRESH-1") is None))

        results.append(check("keyless refresh -> (0, 0), no network", pricing.refresh(["ESP32-WROOM-32"]) == (0, 0)))
        results.append(check("keyless available() is False", not pricing.available()))

        # --- 3b. quota discipline (Mouser publishes 30/min, 1000/day) -------
        # Every call is paced and counted, with NO network: _fetch_mouser is
        # replaced and time.sleep recorded, so the guard measures the loop's
        # behaviour rather than trusting the comment above it.
        results.append(check("pacing honours the published 30 calls/minute",
                             pricing.MIN_CALL_INTERVAL_S >= 60.0 / 30))
        scratch.write_text("{}", encoding="utf-8")
        os.environ["MOUSER_API_KEY"] = "test-key"
        real_fetch, real_sleep = pricing._fetch_mouser, pricing.time.sleep
        try:
            calls: list[str] = []
            naps: list[float] = []
            pricing.time.sleep = lambda s: naps.append(s)  # type: ignore[assignment]

            # (a) a part that simply has no price must NOT stop the run,
            #     and the pause happens whether or not the call succeeded.
            pricing._fetch_mouser = lambda mpn, key: (  # type: ignore[assignment]
                calls.append(mpn) or setattr(pricing, "LAST_ERROR", "Not found") or None)
            pricing.refresh(["A", "B", "C"])
            results.append(check("a per-part miss does not abort the run", len(calls) == 3))
            results.append(check("every call after the first is paced",
                                 len(naps) == 2 and all(n >= pricing.MIN_CALL_INTERVAL_S
                                                        for n in naps)))

            # (b) a key/quota failure repeats for every part — stop at once.
            calls.clear()
            pricing._fetch_mouser = lambda mpn, key: (  # type: ignore[assignment]
                calls.append(mpn) or setattr(
                    pricing, "LAST_ERROR",
                    "Mouser API error: Invalid unique identifier. (API Key)") or None)
            pricing.refresh(["A", "B", "C", "D"])
            results.append(check("a bad key stops after ONE call", len(calls) == 1))

            # (c) one project can never spend the whole daily allowance.
            calls.clear()
            pricing._fetch_mouser = lambda mpn, key: (  # type: ignore[assignment]
                calls.append(mpn) or setattr(pricing, "LAST_ERROR", None) or None)
            pricing.refresh([f"P{i}" for i in range(pricing.MAX_CALLS_PER_REFRESH + 25)])
            results.append(check("a single refresh is capped",
                                 len(calls) == pricing.MAX_CALLS_PER_REFRESH
                                 and "quota" in (pricing.LAST_ERROR or "")))
        finally:
            pricing._fetch_mouser, pricing.time.sleep = real_fetch, real_sleep
            os.environ.pop("MOUSER_API_KEY", None)
            pricing.LAST_ERROR = None

        # --- 4. recompute_bom provenance -----------------------------------
        scratch.write_text(json.dumps({
            "ESP32-WROOM-32": {"mpn": "ESP32-WROOM-32", "price": 3.10, "currency": "USD",
                               "stock": 2553, "vendor": "Mouser", "fetchedAt": now},
        }), encoding="utf-8")
        project = Project(
            id="t", name="t", path="t", kind="hardware",
            ir=SchematicIR(components=[
                Component(refdes="U1", mpn="ESP32-WROOM-32", x=0, y=0),
                Component(refdes="U2", mpn="AP2112K-3.3TRG1", x=0, y=0),
                Component(refdes="X9", mpn="NOT-A-REAL-PART", x=0, y=0),
            ], nets=[]),
        )
        lines = {l.mpn: l for l in tools.recompute_bom(project)}
        live = lines["ESP32-WROOM-32"]
        results.append(check("live-cached MPN -> live + stock + vendor",
                             live.priceSource == "live" and live.unitCost == 3.10
                             and live.stock == 2553 and live.priceVendor == "Mouser"))

        # A key issued on a non-US Mouser site quotes ITS currency (the real
        # key issued 2026-08-22 answers INR). The BOM must carry that through:
        # rendering INR behind a "$" overstated every live price ~85x.
        results.append(check("live line carries the quoted currency",
                             live.priceCurrency == "USD"))
        results.append(check("library reference line is USD",
                             lines["AP2112K-3.3TRG1"].priceCurrency == "USD"))
        results.append(check("an unpriced line claims no currency",
                             lines["NOT-A-REAL-PART"].priceCurrency is None))
        scratch.write_text(json.dumps({
            "ESP32-WROOM-32": {"mpn": "ESP32-WROOM-32", "price": 485.47, "currency": "INR",
                               "stock": 11517, "vendor": "Mouser", "fetchedAt": now},
        }), encoding="utf-8")
        inr = {l.mpn: l for l in tools.recompute_bom(project)}["ESP32-WROOM-32"]
        results.append(check("an INR quote is reported as INR, not dollars",
                             inr.priceCurrency == "INR" and inr.unitCost == 485.47))
        ref = lines["AP2112K-3.3TRG1"]
        results.append(check("library part -> reference", ref.priceSource == "reference" and ref.unitCost > 0))
        none = lines["NOT-A-REAL-PART"]
        results.append(check("unknown part -> none, cost 0", none.priceSource == "none" and none.unitCost == 0.0))
    finally:
        pricing.CACHE_PATH = real_cache
        if old_key is not None:
            os.environ["MOUSER_API_KEY"] = old_key
        scratch.unlink(missing_ok=True)
        # B7: a corrupt cache is set aside as .corrupt.json, not swallowed.
        scratch.with_suffix(".corrupt.json").unlink(missing_ok=True)

    if all(results):
        print("\nAll pricing regressions pass.")
        return 0
    print("\nFAILURES present.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
