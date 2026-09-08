"""DigiKey Product Information API v4 — the second real source (B6).

One distributor means one quota, one currency and one opinion about whether a
part exists. This client mirrors `pricing._fetch_mouser` exactly: stdlib only,
keyless no-op, never raises, `LAST_ERROR` carries the API's own words.

Auth is OAuth2 client-credentials (a "production app" on
developer.digikey.com): `DIGIKEY_CLIENT_ID` + `DIGIKEY_CLIENT_SECRET`, and
`DIGIKEY_SANDBOX=1` to hit the sandbox host while production access is still
pending approval. Optional `DIGIKEY_SITE` (US, IN, ...) / `DIGIKEY_CURRENCY`
(USD, INR, ...) select the locale the prices come back in — the answer's
currency is recorded on every price, never assumed.

The token is held in memory for its lifetime (~10 min); a 401 refreshes it once.

NOTE: written against the v4 reference; the live shape is guarded by
`regression_pricing` on a recorded fixture, and production has not been
exercised until an approved app exists — see docs/BOM_LANE.md B6.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from app.services.pricing import LivePrice, _num, _parse_breaks

LAST_ERROR: str | None = None
_token: dict = {"value": None, "expires": 0.0}


def available() -> bool:
    return bool(os.environ.get("DIGIKEY_CLIENT_ID") and os.environ.get("DIGIKEY_CLIENT_SECRET"))


def _host() -> str:
    return "https://sandbox-api.digikey.com" if os.environ.get("DIGIKEY_SANDBOX") else "https://api.digikey.com"


def _get_token() -> str | None:
    global LAST_ERROR
    if _token["value"] and time.time() < _token["expires"] - 30:
        return _token["value"]
    body = urllib.parse.urlencode({
        "client_id": os.environ["DIGIKEY_CLIENT_ID"],
        "client_secret": os.environ["DIGIKEY_CLIENT_SECRET"],
        "grant_type": "client_credentials",
    }).encode()
    req = urllib.request.Request(
        f"{_host()}/v1/oauth2/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        LAST_ERROR = f"DigiKey token request failed: {exc}"
        return None
    tok = data.get("access_token")
    if not tok:
        LAST_ERROR = f"DigiKey token error: {data.get('error_description') or data.get('error') or 'no token'}"
        return None
    _token["value"] = tok
    _token["expires"] = time.time() + float(data.get("expires_in") or 600)
    return tok


def _search_raw(mpn: str) -> dict | None:
    global LAST_ERROR
    LAST_ERROR = None
    tok = _get_token()
    if not tok:
        return None
    body = json.dumps({"Keywords": mpn, "Limit": 10, "Offset": 0}).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {tok}",
        "X-DIGIKEY-Client-Id": os.environ["DIGIKEY_CLIENT_ID"],
        "X-DIGIKEY-Locale-Site": os.environ.get("DIGIKEY_SITE", "US"),
        "X-DIGIKEY-Locale-Currency": os.environ.get("DIGIKEY_CURRENCY", "USD"),
        "X-DIGIKEY-Locale-Language": "en",
    }
    req = urllib.request.Request(f"{_host()}/products/v4/search/keyword", data=body,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        if exc.code == 401 and _token["value"]:
            _token["value"] = None  # expired early — one retry with a new token
            tok = _get_token()
            if tok:
                req.headers["Authorization"] = f"Bearer {tok}"
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as exc2:  # noqa: BLE001
                    LAST_ERROR = f"DigiKey request failed: {exc2}"
                    return None
        LAST_ERROR = f"DigiKey request failed: HTTP {exc.code}"
        return None
    except Exception as exc:  # noqa: BLE001
        LAST_ERROR = f"DigiKey request failed: {exc}"
        return None


def parse(mpn: str, data: dict, *, now: float | None = None) -> LivePrice | None:
    """v4 keyword-search response -> LivePrice. Exact manufacturer part number
    only (B3) — DigiKey's keyword search is even looser than Mouser's."""
    # v4 returns `ExactMatches` beside the fuzzy `Products` list (confirmed
    # live 2026-08-27). Prefer it; still insist on the MPN matching, because an
    # "exact match" on a keyword search can be an exact DigiKey part number.
    candidates = list(data.get("ExactMatches") or []) + list(data.get("Products") or [])
    hit = next((p for p in candidates
                if (p.get("ManufacturerProductNumber") or "").upper() == mpn.upper()), None)
    if hit is None:
        return None
    # One product has several variations (cut tape / reel / tube); take the
    # one with the smallest MOQ that has pricing — that is what a student buys.
    variations = [v for v in (hit.get("ProductVariations") or []) if v.get("StandardPricing")]
    variations.sort(key=lambda v: v.get("MinimumOrderQuantity") or 1)
    var = variations[0] if variations else None
    breaks = _parse_breaks((var or {}).get("StandardPricing") or [], "BreakQuantity", "UnitPrice")
    if not breaks:
        return None
    stock = (var or {}).get("QuantityAvailableforPackageType")
    if stock is None:
        stock = hit.get("QuantityAvailable")
    lead = hit.get("ManufacturerLeadWeeks")
    status = (hit.get("ProductStatus") or {})
    lifecycle = status.get("Status") if isinstance(status, dict) else (status or None)
    # The booleans are the authoritative EOL signal; ProductStatus.Status can
    # still read "Active" on a part with a last-time-buy date.
    if hit.get("Discontinued"):
        lifecycle = f"Discontinued ({lifecycle})" if lifecycle else "Discontinued"
    elif hit.get("EndOfLife"):
        lifecycle = f"End of life ({lifecycle})" if lifecycle else "End of life"
    return LivePrice(
        mpn=mpn,
        price=breaks[0]["price"],
        currency=str(data.get("SearchLocaleUsed", {}).get("Currency")
                     or os.environ.get("DIGIKEY_CURRENCY", "USD")),
        stock=int(stock) if stock is not None else None,
        vendor="DigiKey",
        fetchedAt=now if now is not None else time.time(),
        breaks=breaks,
        sku=(var or {}).get("DigiKeyProductNumber") or None,
        moq=(var or {}).get("MinimumOrderQuantity"),
        lifecycle=lifecycle,
        leadDays=(_num(lead) or 0) * 7 if _num(lead) else None,
        datasheetUrl=hit.get("DatasheetUrl") or None,
    )


def fetch(mpn: str) -> LivePrice | None:
    if not available():
        return None
    data = _search_raw(mpn)
    if data is None:
        return None
    return parse(mpn, data)
