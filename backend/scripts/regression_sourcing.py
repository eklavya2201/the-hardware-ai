"""Regression guard for the BOM lane (docs/BOM_LANE.md B1-B7). No network.

Every check asserts BOTH directions where it can: the defect fires on a bad
input AND the surface stays quiet on a clean one — a check that can only pass
is the defect this lane is about.

B1  a quantity is not a price: breaks kept, price picked for the line's qty,
    a board-count multiplier changes the export quantities.
B2  an estimate never leaves dressed as a cost: CSV carries source + currency,
    an unknown price is a BLANK cell, a live price exports with its date.
B3  a near-match is not the part: results with no exact listing -> no price.
B4  a refresh says what it did (fetched vs from cache), a partial quota
    failure is reported, a stale line is flagged stale.
B5  the buyability verdict fires on EOL / out-of-stock / short stock, warns on
    NRND / MOQ / stale, reports unverified honestly, proposes alternatives,
    and readiness goes false WITHOUT blocking the Gerber export.
B6  the DigiKey parser: exact MPN only, cut-tape variation, MOQ, lifecycle.
B7  cache: atomic write, a corrupt file is set aside not swallowed, the daily
    call counter persists across runs.

Run: python -m scripts.regression_sourcing
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Component, Project, SchematicIR
from app.services import digikey, export, pricing, sourcing, tools

RESULTS: list[bool] = []


def check(name: str, ok: bool) -> None:
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


def entry(mpn, price, *, stock=100, breaks=None, age=60, vendor="Mouser", **kw):
    e = {"mpn": mpn, "price": price, "currency": "USD", "stock": stock, "vendor": vendor,
         "fetchedAt": time.time() - age, "breaks": breaks or [{"qty": 1, "price": price}]}
    e.update(kw)
    return e


def project(*mpns: str) -> Project:
    comps = []
    for i, m in enumerate(mpns):
        comps.append(Component(refdes=f"U{i + 1}", mpn=m, x=0, y=0))
    return Project(id="t", name="t", path="t", kind="hardware",
                   ir=SchematicIR(components=comps, nets=[]))


MOUSER_BREAKS = {"SearchResults": {"Parts": [
    {"ManufacturerPartNumber": "A4988SETTR-T", "MouserPartNumber": "250-A4988SETTR-T",
     "Availability": "900 In Stock",
     "PriceBreaks": [{"Quantity": 1, "Price": "$1.00", "Currency": "USD"}]},
    {"ManufacturerPartNumber": "ESP32-WROOM-32", "MouserPartNumber": "356-ESP32-WROOM-32",
     "Availability": "2,553 In Stock", "Min": "1", "LifecycleStatus": "Active",
     "LeadTime": "12 Weeks", "DataSheetUrl": "https://example.com/esp32.pdf",
     "PriceBreaks": [
         {"Quantity": 100, "Price": "$2.50", "Currency": "USD"},
         {"Quantity": 1, "Price": "$3.10", "Currency": "USD"},
         {"Quantity": 10, "Price": "$2.95", "Currency": "USD"},
     ]},
]}}


def main() -> int:
    real_cache = pricing.CACHE_PATH
    scratch = Path(__file__).resolve().parent / "_sourcing_cache_test.json"
    pricing.CACHE_PATH = scratch
    saved_env = {k: os.environ.pop(k, None) for k in
                 ("MOUSER_API_KEY", "DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")}
    real_fetch, real_sleep = pricing._fetch_mouser, pricing.time.sleep
    pricing.time.sleep = lambda s: None  # type: ignore[assignment]
    try:
        # ---------------- B1 / B3: parsing ----------------------------------
        lp = pricing._parse_mouser("ESP32-WROOM-32", MOUSER_BREAKS, now=1000.0)
        check("B1 full break table kept, ascending", lp is not None and
              [b["qty"] for b in lp.breaks] == [1, 10, 100])
        check("B1 qty 1 -> first break", pricing.price_for_qty(lp, 1) == 3.10)
        check("B1 qty 10 -> 10-break", pricing.price_for_qty(lp, 10) == 2.95)
        check("B1 qty 57 -> still the 10-break", pricing.price_for_qty(lp, 57) == 2.95)
        check("B1 qty 100 -> 100-break", pricing.price_for_qty(lp, 100) == 2.50)
        check("B5 inputs parsed: sku / moq / lifecycle / lead / datasheet",
              lp.sku == "356-ESP32-WROOM-32" and lp.moq == 1 and lp.lifecycle == "Active"
              and lp.leadDays == 84 and lp.datasheetUrl)
        check("B3 no exact listing -> NO price (A4988 vs A4988SETTR-T)",
              pricing._parse_mouser("A4988", MOUSER_BREAKS) is None)
        check("B3 packaging suffix still prices (FT232RNL-TUBE is an FT232RNL)",
              pricing._parse_mouser("FT232RNL", {"SearchResults": {"Parts": [
                  {"ManufacturerPartNumber": "FT232RNL-TUBE",
                   "PriceBreaks": [{"Quantity": 1, "Price": "$4", "Currency": "USD"}]}]}}) is not None)

        # ---------------- B4: stale, and what a refresh DID ------------------
        now = time.time()
        scratch.write_text(json.dumps({
            "FRESH": entry("FRESH", 1.0, age=60),
            "STALE": entry("STALE", 2.0, age=pricing.CACHE_TTL_S + 3600),
            "GONE": entry("GONE", 3.0, age=pricing.STALE_MAX_S + 3600),
        }), encoding="utf-8")
        check("B4 fresh is fresh", pricing.cached_price_any("FRESH") == (pricing.cached_price("FRESH"), False))
        s_lp, s_stale = pricing.cached_price_any("STALE")
        check("B4 past TTL is served STALE, not dropped", s_lp is not None and s_stale)
        check("B4 cached_price (fresh-only) still rejects stale", pricing.cached_price("STALE") is None)
        check("B4 past STALE_MAX is gone", pricing.cached_price_any("GONE") == (None, False))
        p = project("FRESH", "STALE")
        lines = {l.mpn: l for l in tools.recompute_bom(p)}
        check("B4 BomLine.priceStale flags the stale line only",
              lines["STALE"].priceStale and not lines["FRESH"].priceStale
              and lines["STALE"].priceSource == "live")

        os.environ["MOUSER_API_KEY"] = "k"
        calls: list[str] = []

        def fake(mpn, key):
            calls.append(mpn)
            if mpn == "QUOTA":
                pricing.LAST_ERROR = "Mouser request failed: HTTP Error 403: Forbidden"
                return None
            if mpn == "MISS":
                pricing.LAST_ERROR = None
                return None
            return pricing.LivePrice(mpn=mpn, price=1.0, currency="USD", stock=5,
                                     vendor="Mouser", fetchedAt=time.time())
        pricing._fetch_mouser = fake  # type: ignore[assignment]
        res = pricing.refresh(["FRESH", "NEW1", "MISS", "NEW2"])
        check("B4 fetched counts only real fetches (3 asked, 2 priced)",
              res.fetched == 2 and res.from_cache == 1 and res.failed == ["MISS"])
        check("B4 priced = fresh now (cache hit + fetched)", res.priced == 3 and res.total == 4)
        check("B4 old tuple contract still holds", tuple(res) == (2, 3) and res == (2, 3))
        calls.clear()
        res = pricing.refresh(["NEW3", "QUOTA", "NEW4", "NEW5"])
        check("B4 quota mid-run: stops, reports the error AND what was skipped",
              res.fetched == 1 and res.error and "403" in res.error
              and res.stopped_early and res.failed == ["QUOTA"]
              and set(res.skipped) == {"NEW4", "NEW5"})
        check("B4 quota stop made no further calls", calls == ["NEW3", "QUOTA"])

        # ---------------- B7: cache discipline ------------------------------
        cache = pricing._load_cache()
        check("B7 daily call counter persisted in the cache file",
              pricing._daily_calls(cache, "Mouser") == 5)
        check("B7 no .tmp left behind (atomic write)", not scratch.with_suffix(".tmp").exists())
        scratch.write_text("{not json", encoding="utf-8")
        check("B7 corrupt cache -> empty AND set aside as evidence",
              pricing._load_cache() == {} and scratch.with_suffix(".corrupt.json").exists()
              and not scratch.exists())
        scratch.with_suffix(".corrupt.json").unlink(missing_ok=True)
        scratch.write_text(json.dumps({"_meta": {"Mouser": {
            "day": time.strftime("%Y-%m-%d", time.gmtime()), "calls": pricing.DAILY_CAP}}}),
            encoding="utf-8")
        calls.clear()
        res = pricing.refresh(["NEW6"])
        check("B7 a spent daily quota makes NO call and says so",
              calls == [] and res.fetched == 0 and "quota" in (res.error or "").lower())
        os.environ.pop("MOUSER_API_KEY", None)

        # ---------------- B2: exports ---------------------------------------
        scratch.write_text(json.dumps({
            "ESP32-WROOM-32": entry("ESP32-WROOM-32", 3.10, stock=2553, sku="356-ESP32",
                                    breaks=[{"qty": 1, "price": 3.10}, {"qty": 10, "price": 2.95}]),
            # Not in the library at all: the ONLY SKU we can know is the live one.
            "LIVE-ONLY-PART": entry("LIVE-ONLY-PART", 0.5, stock=10, sku="999-LIVE-ONLY"),
        }), encoding="utf-8")
        p = project("ESP32-WROOM-32", "AP2112K-3.3TRG1", "NOT-A-REAL-PART", "LIVE-ONLY-PART")
        rows = list(csv.reader(io.StringIO(export.bom_csv(p))))
        head = rows[0]
        by = {r[head.index("Comment")]: r for r in rows[1:]}
        ci = {h: head.index(h) for h in head}
        check("B2 CSV header carries provenance columns",
              {"Unit Price", "Currency", "Price Source", "Price As Of", "Ext Price"} <= set(head)
              and "Unit Cost USD" not in head)
        unknown = next(r for r in rows[1:] if "NOT-A-REAL-PART" in r)
        check("B2 unknown price exports as BLANK, never 0.0000",
              unknown[ci["Unit Price"]] == "" and unknown[ci["Ext Price"]] == ""
              and unknown[ci["Price Source"]] == "none")
        ref = next(r for r in rows[1:] if r[ci["Price Source"]] == "estimate")
        check("B2 reference cost is labelled an estimate, in USD",
              float(ref[ci["Unit Price"]]) > 0 and ref[ci["Currency"]] == "USD")
        live_row = next(r for r in rows[1:] if r[ci["Price Source"]].startswith("live"))
        check("B2 live price exports with vendor + date + currency",
              "Mouser" in live_row[ci["Price Source"]] and live_row[ci["Price As Of"]]
              and live_row[ci["Currency"]] == "USD" and float(live_row[ci["Unit Price"]]) == 3.10)
        rows50 = list(csv.reader(io.StringIO(export.bom_csv(p, boards=50))))
        l50 = next(r for r in rows50[1:] if r[ci["Price Source"]].startswith("live"))
        check("B1 boards=50: qty x50 and the 10-break applies",
              l50[ci["Qty"]] == "50" and float(l50[ci["Unit Price"]]) == 2.95
              and abs(float(l50[ci["Ext Price"]]) - 147.5) < 1e-6)
        mrows = list(csv.reader(io.StringIO(export.mouser_bom_csv(p, boards=3))))
        mline = next(r for r in mrows[1:] if r[1] == "LIVE-ONLY-PART")
        check("B2 Mouser CSV takes the live SKU when the library has none, qty x boards",
              mline[0] == "999-LIVE-ONLY" and mline[2] == "3")
        drows = list(csv.reader(io.StringIO(export.digikey_bom_csv(p))))
        dline = next(r for r in drows[1:] if r[1] == "LIVE-ONLY-PART")
        check("B2 a Mouser SKU never leaks into the DigiKey column", dline[0] == "")

        # ---------------- B5: the verdict -----------------------------------
        scratch.write_text(json.dumps({
            "OK-PART": entry("OK-PART", 1.0, stock=500, lifecycle="Active", moq=1),
            "EOL-PART": entry("EOL-PART", 1.0, stock=500, lifecycle="Obsolete"),
            "OOS-PART": entry("OOS-PART", 1.0, stock=0, leadDays=84),
            "SHORT-PART": entry("SHORT-PART", 1.0, stock=3),
            "NRND-PART": entry("NRND-PART", 1.0, stock=500, lifecycle="Not Recommended for New Designs"),
            "MOQ-PART": entry("MOQ-PART", 0.01, stock=99999, moq=100),
            "STALE-PART": entry("STALE-PART", 1.0, stock=500, age=pricing.CACHE_TTL_S + 60),
            "ALT-PART": entry("ALT-PART", 1.0, stock=500, lifecycle="Active"),
        }), encoding="utf-8")
        st = {m: sourcing.assess_line(m, 5)["status"] for m in
              ("OK-PART", "EOL-PART", "OOS-PART", "SHORT-PART", "NRND-PART", "MOQ-PART",
               "STALE-PART", "NEVER-CHECKED")}
        check("B5 clean line -> ok (silent)", st["OK-PART"] == "ok")
        check("B5 EOL blocks", st["EOL-PART"] == "eol")
        check("B5 out of stock blocks", st["OOS-PART"] == "out_of_stock")
        check("B5 short stock (3 < 5) blocks", st["SHORT-PART"] == "short_stock")
        check("B5 short stock is fine when the run is smaller",
              sourcing.assess_line("SHORT-PART", 2)["status"] == "ok")
        check("B5 NRND warns, does not block", st["NRND-PART"] == "nrnd"
              and not sourcing.assess_line("NRND-PART", 5)["blocking"])
        check("B5 MOQ warns with the number you actually buy",
              st["MOQ-PART"] == "moq" and "100" in sourcing.assess_line("MOQ-PART", 5)["reasons"][0])
        check("B5 stale warns", st["STALE-PART"] == "stale")
        check("B5 never checked -> unverified, NOT blocking, NOT ok",
              st["NEVER-CHECKED"] == "unverified" and not sourcing.assess_line("NEVER-CHECKED", 1)["blocking"])
        oos = sourcing.assess_line("OOS-PART", 1)
        check("B5 out-of-stock reason names the lead time", "12 weeks" in oos["reasons"][0])

        # alternatives ride the library's `alternatives` list
        from app.services import library
        lib_part = next(pt for pt in library.all_parts() if pt.alternatives)
        alt_mpn = lib_part.alternatives[0]
        scratch.write_text(json.dumps({
            lib_part.mpn: entry(lib_part.mpn, 1.0, stock=0),
            alt_mpn: entry(alt_mpn, 1.0, stock=500, lifecycle="Active"),
        }), encoding="utf-8")
        line = sourcing.assess_line(lib_part.mpn, 1)
        check("B5 a blocked line proposes its library alternatives with THEIR status",
              line["blocking"] and any(a["mpn"] == alt_mpn and a["status"] == "ok"
                                       for a in line["alternatives"]))

        # readiness integration
        p_bad = project(lib_part.mpn, "OK-PART")
        scratch.write_text(json.dumps({
            lib_part.mpn: entry(lib_part.mpn, 1.0, stock=0),
            "OK-PART": entry("OK-PART", 1.0, stock=500),
        }), encoding="utf-8")
        rep = export.manufacturability_report(p_bad)
        s_blk = [b for b in rep["blockers"] if b["kind"] == "sourcing"]
        check("B5 readiness: an unbuyable BOM is NOT ready",
              rep["ready"] is False and len(s_blk) == 1 and s_blk[0]["mpn"] == lib_part.mpn)
        check("B5 readiness: sourcing does not stop a Gerber export",
              all(b["kind"] != "sourcing" for b in export.export_blockers(rep)))
        check("B5 readiness carries the verdict summary",
              rep["sourcing"]["verdict"] == "blocked" and rep["sourcing"]["blockers"] == 1)
        scratch.write_text(json.dumps({
            lib_part.mpn: entry(lib_part.mpn, 1.0, stock=500),
            "OK-PART": entry("OK-PART", 1.0, stock=500),
        }), encoding="utf-8")
        rep_ok = export.manufacturability_report(p_bad)
        check("B5 readiness: a buyable BOM adds NO sourcing blocker",
              all(b["kind"] != "sourcing" for b in rep_ok["blockers"])
              and rep_ok["sourcing"]["verdict"] == "ready")
        scratch.write_text("{}", encoding="utf-8")
        rep_un = export.manufacturability_report(p_bad)
        check("B5 readiness: never checked -> 'unverified', no blocker (keyless install exports)",
              rep_un["sourcing"]["verdict"] == "unverified"
              and all(b["kind"] != "sourcing" for b in rep_un["blockers"]))
        v = sourcing.assess(project("OK-PART", "OK-PART", "OK-PART"), boards=10)
        check("B5 boards multiplier: 3 per board x 10 = 30 needed",
              v["lines"][0]["qtyNeeded"] == 30 and v["lines"][0]["qtyPerBoard"] == 3)

        # ---------------- B6: DigiKey parser --------------------------------
        DK = {"SearchLocaleUsed": {"Currency": "USD"}, "Products": [
            {"ManufacturerProductNumber": "ESP32-WROOM-32E", "QuantityAvailable": 9,
             "ProductStatus": {"Status": "Active"}, "DatasheetUrl": "https://x/y.pdf",
             "ManufacturerLeadWeeks": "10",
             "ProductVariations": [
                 {"DigiKeyProductNumber": "1965-ESP32-WROOM-32E-ND", "MinimumOrderQuantity": 1,
                  "QuantityAvailableforPackageType": 1200,
                  "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 3.5},
                                      {"BreakQuantity": 10, "UnitPrice": 3.2}]},
                 {"DigiKeyProductNumber": "1965-ESP32-WROOM-32E-TR", "MinimumOrderQuantity": 1000,
                  "QuantityAvailableforPackageType": 4000,
                  "StandardPricing": [{"BreakQuantity": 1000, "UnitPrice": 2.9}]},
             ]},
            {"ManufacturerProductNumber": "ESP32-WROOM-32", "QuantityAvailable": 0,
             "ProductStatus": {"Status": "Obsolete"},
             "ProductVariations": [{"DigiKeyProductNumber": "1965-OLD-ND", "MinimumOrderQuantity": 1,
                                    "QuantityAvailableforPackageType": 0,
                                    "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 4.0}]}]},
        ]}
        d = digikey.parse("ESP32-WROOM-32E", DK, now=5.0)
        check("B6 exact MPN only (the -32E, not the -32)", d is not None and d.price == 3.5)
        check("B6 smallest-MOQ variation chosen (cut tape, not the reel)",
              d.sku == "1965-ESP32-WROOM-32E-ND" and d.moq == 1 and d.stock == 1200)
        check("B6 breaks / lifecycle / lead / datasheet carried",
              [b["qty"] for b in d.breaks] == [1, 10] and d.lifecycle == "Active"
              and d.leadDays == 70 and d.datasheetUrl and d.vendor == "DigiKey")
        d_old = digikey.parse("ESP32-WROOM-32", DK)
        check("B6 an obsolete listing still parses so the verdict can say EOL",
              d_old is not None and d_old.lifecycle == "Obsolete" and d_old.stock == 0)
        check("B6 no exact -> None", digikey.parse("ESP32", DK) is None)
        # Live shape (2026-08-27): `ExactMatches` beside `Products`, and the
        # EOL booleans beside a ProductStatus that can still say "Active".
        DK2 = {"ExactMatches": [
            {"ManufacturerProductNumber": "OLD-PART", "QuantityAvailable": 3,
             "ProductStatus": {"Id": 0, "Status": "Active"}, "Discontinued": True,
             "ProductVariations": [{"DigiKeyProductNumber": "1-OLD-ND", "MinimumOrderQuantity": 1,
                                    "QuantityAvailableforPackageType": 3,
                                    "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 1.0}]}]}],
            "Products": []}
        d2 = digikey.parse("OLD-PART", DK2)
        check("B6 ExactMatches list is read; Discontinued flag beats an 'Active' status",
              d2 is not None and d2.lifecycle.startswith("Discontinued")
              and sourcing._lifecycle_status(d2.lifecycle) == "eol")
        check("B6 keyless: not available, fetch is a no-op",
              not digikey.available() and digikey.fetch("X") is None)
    finally:
        pricing._fetch_mouser, pricing.time.sleep = real_fetch, real_sleep
        pricing.CACHE_PATH = real_cache
        pricing.LAST_ERROR = None
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
        for suffix in (".json", ".tmp", ".corrupt.json"):
            scratch.with_suffix(suffix).unlink(missing_ok=True)

    print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks pass.")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
