"""Live distributor pricing — honest sourcing (market-research item #3).

Beta testers flagged wrong BOM prices as the fastest trust-killer, so the BOM
now distinguishes price provenance instead of presenting reference costs as
real quotes:

- "live"      — fetched from a distributor API (Mouser, DigiKey), with stock,
                price breaks, MOQ, lifecycle and a date.
- "reference" — the curated library's unitCost (a ballpark, labelled "est.").
- "none"      — no price known; never invent one.

Keyless-by-design like the affiliate layer: with no distributor key set,
`refresh()` reports unavailable and everything stays labelled "reference" /
"none". With a key, the user-triggered refresh endpoint fetches each BOM MPN
once and caches it (24 h TTL) in `backend/price_cache.json` (git-ignored), so
`recompute_bom` can consult the cache offline — network I/O happens ONLY in
the explicit refresh, mirroring the run-drc / LLM-critic pattern.

The BOM lane's one rule (docs/BOM_LANE.md): **never invent a price.**
- B1: a quantity is not a price — the whole break table is kept and
  `price_for_qty` picks the break the buyer actually lands in.
- B3: a near-match is not the part — no exact listing means no price.
- B4: a refresh reports what it DID (fetched / served from cache / failed), a
  partial quota failure is reported even when some parts priced, and an entry
  past its TTL is served as STALE (flagged) for up to STALE_MAX_S, never as
  fresh and never silently dropped to "est.".
- B7: the cache is written atomically, a corrupt file is set aside rather
  than swallowed, and the daily call count is persisted per vendor so two
  workers sharing one key cannot each spend the day's quota.

Mouser Search API: free key from mouser.com/api-hub, ~30 req/min, 1000/day.
Requests use only the stdlib (urllib) so this adds no dependency.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parents[2] / "price_cache.json"
CACHE_TTL_S = 24 * 3600
# Past the TTL a quote is STALE, not gone: it still says more about the part
# than the library's ballpark does, so it is served with `stale=True` until
# STALE_MAX_S, after which it is dropped.
STALE_MAX_S = 7 * 24 * 3600
MOUSER_URL = "https://api.mouser.com/api/v1/search/partnumber?apiKey={key}"
# Mouser's published Search API quota (mouser.com/en/api-search/): 30 calls per
# minute, 1000 per day. The old 0.15 s gap was ~400/min — 13x over — and it only
# slept after a SUCCESS, so a throttled run hammered hardest exactly when Mouser
# was already refusing it. One project's BOM refresh must not spend the day's
# quota either, hence the per-call cap.
CALLS_PER_MIN = 30
MIN_CALL_INTERVAL_S = 60.0 / CALLS_PER_MIN
MAX_CALLS_PER_REFRESH = 120
DAILY_CAP = 1000
# Seconds to wait on one distributor read. Env-overridable so the unattended
# auditor on the EC2 box can be patient without making a user-facing price
# refresh hang (see the call site).
_REQUEST_TIMEOUT_S = float(os.environ.get("MOUSER_TIMEOUT_S") or 15)
# Cache-file key for bookkeeping that is not a part.
_META_KEY = "_meta"
# An error that will repeat for EVERY part (bad key, quota gone) — keep going on
# a per-part miss, stop dead on these rather than burning the daily allowance.
_FATAL_ERROR_TOKENS = ("invalid unique identifier", "not authoriz", "unauthoriz",
                       "exceed", "limit", "too many", "quota", "suspend",
                       # The daily cap arrives as a bare HTTP 403, and 429 is
                       # the rate limit. Neither says the word "quota", and
                       # missing them cost an audit its integrity.
                       "403", "429", "forbidden", "request failed")


@dataclass
class LivePrice:
    mpn: str
    price: float           # unit price at the SMALLEST break (qty-1 where offered)
    currency: str
    stock: int | None
    vendor: str            # "Mouser" | "DigiKey"
    fetchedAt: float       # epoch seconds
    # B1: the full break table, ascending by qty: [{"qty": 1, "price": 3.10}, ...]
    breaks: list[dict] = field(default_factory=list)
    # B5 inputs. None = the distributor did not say, never a guess.
    sku: str | None = None          # the distributor's own part number
    moq: int | None = None          # minimum order quantity
    lifecycle: str | None = None    # distributor's lifecycle text (Active / NRND / Obsolete ...)
    leadDays: int | None = None     # factory lead time when out of stock
    datasheetUrl: str | None = None


_LP_FIELDS = {f.name for f in fields(LivePrice)}


def _from_entry(entry: dict) -> LivePrice | None:
    """A cache entry from any version of this module → LivePrice, or None."""
    if not isinstance(entry, dict):
        return None
    try:
        return LivePrice(**{k: v for k, v in entry.items() if k in _LP_FIELDS})
    except TypeError:
        return None


def price_for_qty(lp: LivePrice, qty: int) -> float:
    """The unit price a buyer of `qty` actually pays: the largest break whose
    quantity is <= qty. Below the first break (qty 0/negative or a listing that
    starts at 10) the smallest break applies — that is what the cart charges."""
    if qty is None or qty < 1:
        qty = 1
    chosen = None
    for b in sorted(lp.breaks or [], key=lambda b: b.get("qty") or 1):
        if (b.get("qty") or 1) <= qty:
            chosen = b
        else:
            break
    if chosen is None:
        return lp.price
    return float(chosen["price"])


def available() -> bool:
    return bool(os.environ.get("MOUSER_API_KEY")) or _digikey_available()


def _digikey_available() -> bool:
    try:
        from app.services import digikey
        return digikey.available()
    except Exception:  # noqa: BLE001
        return False


def vendors_configured() -> list[str]:
    out = []
    if os.environ.get("MOUSER_API_KEY"):
        out.append("Mouser")
    if _digikey_available():
        out.append("DigiKey")
    return out


# Last API-level error seen by refresh() (e.g. "Invalid API Key"), so the
# endpoint can tell the user WHY zero parts priced instead of failing silently.
LAST_ERROR: str | None = None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cache() -> dict[str, dict]:
    try:
        text = CACHE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        # B7: a truncated file used to be swallowed into {} and every project
        # silently dropped to "est.". Set it aside so the evidence survives.
        try:
            CACHE_PATH.replace(CACHE_PATH.with_suffix(".corrupt.json"))
        except Exception:  # noqa: BLE001
            pass
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    """Atomic: write beside, then replace, so a crash mid-write can never leave
    a half-file behind (B7)."""
    tmp = CACHE_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except Exception:  # noqa: BLE001 — cache is best-effort
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _daily_calls(cache: dict, vendor: str, *, now: float | None = None) -> int:
    day = time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))
    meta = cache.get(_META_KEY) or {}
    rec = meta.get(vendor) or {}
    return int(rec.get("calls") or 0) if rec.get("day") == day else 0


def _count_call(cache: dict, vendor: str, *, now: float | None = None) -> None:
    day = time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))
    meta = cache.setdefault(_META_KEY, {})
    rec = meta.get(vendor) or {}
    calls = int(rec.get("calls") or 0) if rec.get("day") == day else 0
    meta[vendor] = {"day": day, "calls": calls + 1}


def cached_price(mpn: str, *, now: float | None = None) -> LivePrice | None:
    """FRESH cached live price for an MPN, or None. Never touches the network —
    safe to call from recompute_bom on every IR change."""
    lp, stale = cached_price_any(mpn, now=now)
    return None if stale else lp


def cached_price_any(mpn: str, *, now: float | None = None) -> tuple[LivePrice | None, bool]:
    """(price, stale). Fresh within CACHE_TTL_S; stale up to STALE_MAX_S; gone
    after that. A stale quote is still a quote — the caller must SAY it is
    stale, which is the whole point of returning the flag (B4)."""
    entry = _load_cache().get(mpn)
    lp = _from_entry(entry) if entry else None
    if lp is None:
        return None, False
    age = (now if now is not None else time.time()) - lp.fetchedAt
    if age > STALE_MAX_S:
        return None, False
    return lp, age > CACHE_TTL_S


# ---------------------------------------------------------------------------
# Mouser fetch
# ---------------------------------------------------------------------------

def _num(text: object) -> int | None:
    m = re.search(r"[\d,]+", str(text or ""))
    return int(m.group(0).replace(",", "")) if m else None


def _lead_days(text: object) -> int | None:
    """'12 Weeks' / '28 Days' -> days. None when the field is empty."""
    m = re.search(r"(\d+)\s*(week|day)", str(text or ""), re.I)
    if not m:
        return None
    n = int(m.group(1))
    return n * 7 if m.group(2).lower().startswith("week") else n


def _parse_breaks(raw: list[dict], qty_key: str, price_key: str) -> list[dict]:
    out: list[dict] = []
    for b in raw or []:
        m = re.search(r"[\d.,]+", str(b.get(price_key) or ""))
        if not m:
            continue
        try:
            price = float(m.group(0).replace(",", ""))
        except ValueError:
            continue
        out.append({"qty": int(b.get(qty_key) or 1), "price": price})
    return sorted(out, key=lambda b: b["qty"])


def _parse_mouser(mpn: str, data: dict, *, now: float | None = None) -> LivePrice | None:
    """Extract a LivePrice from a Mouser search response.

    B3: ONLY an exact listing (or one differing by a packaging suffix, see
    `exact_part`) may produce a price. Mouser answers a search with everything
    that looked close; the first hit for "A4988" is A4988SETTR-T, a different
    package, and pricing it under our MPN would be a number that belongs to a
    different part. No exact match means no price."""
    part = exact_part(mpn, data)
    if part is None:
        return None

    breaks = _parse_breaks(part.get("PriceBreaks") or [], "Quantity", "Price")
    if not breaks:
        return None
    first = breaks[0]
    currency = str(((part.get("PriceBreaks") or [{}])[0]).get("Currency") or "USD")

    return LivePrice(
        mpn=mpn,
        price=first["price"],
        currency=currency,
        stock=_num(part.get("Availability")),
        vendor="Mouser",
        fetchedAt=now if now is not None else time.time(),
        breaks=breaks,
        sku=(part.get("MouserPartNumber") or None),
        moq=_num(part.get("Min")),
        lifecycle=(part.get("LifecycleStatus") or None),
        leadDays=_lead_days(part.get("LeadTime")),
        datasheetUrl=(part.get("DataSheetUrl") or None),
    )


# Packaging suffixes Mouser folds into ManufacturerPartNumber. Packaging does
# not change the silicon or the land pattern, so FT232RNL-REEL IS an FT232RNL.
# This list is closed on purpose: a PACKAGE suffix (ADS1219IPW vs IPWR vs
# IRTET, A4988 -> A4988SETTR-T) changes the part, and accepting it would be the
# parts[0] bug again — "Mouser has heard of it" mistaken for "we carry it".
# Those stay near-misses for a human to pick in resolve_orderable.
PACKAGING_SUFFIXES = ("-REEL", "-TUBE", "-TR", "-T", "-CT", "-TAPE", "-BULK")


def packaging_suffix(mpn: str, listed: str) -> str | None:
    """The allowlisted suffix by which `listed` extends `mpn`, else None.

    Exact match returns "" (a suffix of nothing). Anything else — a longer
    suffix, a prefix-only overlap, a different part — is None."""
    a, b = mpn.strip().upper(), listed.strip().upper()
    if not a or not b:
        return None
    if a == b:
        return ""
    if b.startswith(a) and b[len(a):] in PACKAGING_SUFFIXES:
        return b[len(a):]
    return None


def exact_part(mpn: str, data: dict) -> dict | None:
    """The record whose manufacturer part number IS `mpn`, or None.

    Mouser answers a part-number search with everything that looked close, so
    "the response had results" is not evidence about the part you asked for.
    Anything deciding whether we CARRY a part — or what it costs — must go
    through here.

    An exact listing wins. Failing that, one that differs from `mpn` only by a
    packaging suffix (see PACKAGING_SUFFIXES) counts, and the record is
    annotated with `_matchedSuffix` so the verdict can say how it matched."""
    parts = (data.get("SearchResults") or {}).get("Parts") or []
    for p in parts:
        if (p.get("ManufacturerPartNumber") or "").upper() == mpn.upper():
            return p
    for p in parts:
        sfx = packaging_suffix(mpn, p.get("ManufacturerPartNumber") or "")
        if sfx:
            return {**p, "_matchedSuffix": sfx}
    return None


def _fetch_mouser_raw(mpn: str, key: str) -> dict | None:
    """The catalogue's answer, unparsed — or None with LAST_ERROR set.

    Split out of _fetch_mouser so a caller that needs more than a price (the
    SKU, the datasheet URL) does not have to re-implement the error discipline
    below, which is the part that has bitten us."""
    body = json.dumps({"SearchByPartRequest": {"mouserPartNumber": mpn}}).encode()
    req = urllib.request.Request(
        MOUSER_URL.format(key=key),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    global LAST_ERROR
    # Clear FIRST. A stale error left by the previous call makes this call's
    # silence look like a failure — or worse, makes a genuine "no such part"
    # indistinguishable from "the server refused us", which is exactly how an
    # audit marked 245 real parts as unorderable when the daily quota ran out
    # (2026-08-23).
    LAST_ERROR = None
    try:
        # 15 s is fine from a dev box and marginal from the EC2 box: on
        # 2026-09-04 the unattended auditor lost whole batches to "read
        # operation timed out" (177 parts, then 17, instead of 850), because a
        # stalled read stops the run by design — an unanswered call must never
        # be recorded as an answer. Raising the ceiling costs nothing when the
        # API is healthy and buys the slow path a chance to finish.
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — network/API failure degrades to no price
        LAST_ERROR = f"Mouser request failed: {exc}"
        return None
    errors = data.get("Errors") or []
    if errors:
        msg = errors[0].get("Message") or "unknown error"
        prop = errors[0].get("PropertyName")
        LAST_ERROR = f"Mouser API error: {msg}{f' ({prop})' if prop else ''}"
        return None
    return data


def _fetch_mouser(mpn: str, key: str) -> LivePrice | None:
    data = _fetch_mouser_raw(mpn, key)
    if data is None:
        return None
    return _parse_mouser(mpn, data)


def _fetch_digikey(mpn: str) -> LivePrice | None:
    """Second source (B6). Sets LAST_ERROR through the same contract."""
    global LAST_ERROR
    from app.services import digikey
    lp = digikey.fetch(mpn)
    if lp is None and digikey.LAST_ERROR:
        LAST_ERROR = digikey.LAST_ERROR
    return lp


def _fatal(error: str | None) -> bool:
    """Is this a key/quota failure that every remaining part would hit too?"""
    low = (error or "").lower()
    return any(tok in low for tok in _FATAL_ERROR_TOKENS)


@dataclass
class RefreshResult:
    """What a refresh actually did — the number the UI shows must be this, not
    "how many are priced now" (B4: a click inside the TTL used to report
    'N/N priced live' having contacted nobody)."""
    fetched: int = 0            # calls that returned a price this run
    from_cache: int = 0         # MPNs already fresh — no call made
    failed: list[str] = field(default_factory=list)   # asked, no price
    skipped: list[str] = field(default_factory=list)  # not asked (cap / fatal stop)
    priced: int = 0             # MPNs with a FRESH price after the run
    total: int = 0
    error: str | None = None    # the last API-level error, even on partial success
    stopped_early: bool = False

    # Backwards-compat with the old (updated, live_now) tuple.
    def __iter__(self):
        yield self.fetched
        yield self.priced

    def __eq__(self, other):  # noqa: D105
        if isinstance(other, tuple):
            return (self.fetched, self.priced) == other
        return super().__eq__(other)


def refresh(mpns: list[str]) -> RefreshResult:
    """Fetch live prices for `mpns` into the cache (skipping fresh entries).
    No key → nothing fetched, no network. Never raises. Mouser first, DigiKey
    for what Mouser could not price (or alone when only DigiKey is set)."""
    global LAST_ERROR
    LAST_ERROR = None
    key = os.environ.get("MOUSER_API_KEY")
    use_dk = _digikey_available()
    wanted = list(dict.fromkeys(m for m in mpns if m))  # de-dup, keep order
    res = RefreshResult(total=len(wanted))
    if not key and not use_dk:
        return res

    cache = _load_cache()
    calls = 0
    stop = False
    for i, mpn in enumerate(wanted):
        if cached_price(mpn) is not None:
            res.from_cache += 1
            continue
        if stop:
            res.skipped.append(mpn)
            continue
        if calls >= MAX_CALLS_PER_REFRESH:
            res.error = (f"stopped after {calls} lookups to stay inside the "
                         "distributor's daily quota — refresh again to continue")
            res.stopped_early = True
            stop = True
            res.skipped.append(mpn)
            continue
        if calls:  # pace EVERY call, not only the ones that succeeded
            time.sleep(MIN_CALL_INTERVAL_S)
        lp: LivePrice | None = None
        if key:
            if _daily_calls(cache, "Mouser") >= DAILY_CAP:
                LAST_ERROR = "Mouser daily quota already spent (1000 calls) — try again tomorrow"
            else:
                lp = _fetch_mouser(mpn, key)
                _count_call(cache, "Mouser")
            if lp is None and _fatal(LAST_ERROR):
                res.error = LAST_ERROR
                if not use_dk:
                    res.stopped_early = True
                    stop = True
                    res.failed.append(mpn)  # asked, refused — the rest are skipped
                    calls += 1
                    continue
                key = None  # Mouser is done for today; DigiKey may still answer
        if lp is None and use_dk:
            if _daily_calls(cache, "DigiKey") >= DAILY_CAP:
                LAST_ERROR = "DigiKey daily quota already spent — try again tomorrow"
            else:
                lp = _fetch_digikey(mpn)
                _count_call(cache, "DigiKey")
            if lp is None and _fatal(LAST_ERROR):
                res.error = LAST_ERROR
                use_dk = False
                if not key:
                    res.stopped_early = True
                    stop = True
                    res.failed.append(mpn)
                    calls += 1
                    continue
        calls += 1
        if lp is not None:
            cache[mpn] = asdict(lp)
            res.fetched += 1
        else:
            res.failed.append(mpn)
            if LAST_ERROR and not res.error:
                res.error = LAST_ERROR
    _save_cache(cache)  # the daily counter must persist even when nothing priced
    res.priced = sum(1 for m in wanted if cached_price(m) is not None)
    if res.error:
        LAST_ERROR = res.error
    return res
