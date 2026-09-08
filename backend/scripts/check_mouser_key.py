"""Is this Mouser key the RIGHT key? One command, one honest answer.

Mouser hands out two different keys and only one of them works here:

  * **Search API** key — mouser.com/en/api-search/ -> "Sign Up" (free, instant).
    This is the one Synqit needs: `pricing.py` calls
    `api.mouser.com/api/v1/search/partnumber`.
  * **Order API** key — a different product behind a business review. Requesting
    it by mistake has cost this project two rounds already; it fails here with
    "Invalid unique identifier for this request", which reads like a typo'd key
    rather than the wrong PRODUCT. That is the whole reason this script exists.

Usage (from backend/, key either in the env or on the command line):

    python -m scripts.check_mouser_key
    python -m scripts.check_mouser_key --key <GUID> --mpn ESP32-WROOM-32

Exit codes: 0 the key works · 1 it does not (the reason is printed).
Network I/O: one request, only when you run this.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

from app.services import pricing  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a Mouser SEARCH API key.")
    ap.add_argument("--key", help="the key to test (default: MOUSER_API_KEY from "
                                  "the environment / backend/.env)")
    ap.add_argument("--mpn", default="ESP32-WROOM-32",
                    help="a real MPN to price (default: ESP32-WROOM-32)")
    args = ap.parse_args()

    load_dotenv(_BACKEND_ROOT / ".env")
    key = args.key or os.environ.get("MOUSER_API_KEY", "")
    if not key:
        print("No key. Pass --key, or set MOUSER_API_KEY in backend/.env.")
        print("Get one (free, instant) at https://www.mouser.com/en/api-search/")
        return 1
    os.environ["MOUSER_API_KEY"] = key
    print(f"Key ...{key[-6:]}  ->  {pricing.MOUSER_URL.split('?')[0]}")
    print(f"Asking for {args.mpn} ...")

    # Bypass the 24 h cache — we are testing the KEY, not the cache.
    price = pricing._fetch_mouser(args.mpn, key)  # noqa: SLF001
    if price is not None:
        print("\n[OK] The key works — this is a Search API key.")
        print(f"     {args.mpn}: {price.price} {price.currency} "
              f"· stock {price.stock} · vendor {price.vendor}")
        print("     Set MOUSER_API_KEY in backend/.env, restart the backend, then")
        print("     POST /projects/{id}/bom/refresh-prices — BOM lines should read")
        print("     priceSource='live' instead of '~est.'.")
        return 0

    err = pricing.LAST_ERROR or "no price came back (and no API error was reported)"
    print(f"\n[FAIL] {err}")
    low = err.lower()
    if "invalid unique identifier" in low or "not authorized" in low:
        print("       That is the signature of the WRONG PRODUCT, not a typo:")
        print("       an Order API key cannot call the Search API. Request the")
        print("       Search API key at https://www.mouser.com/en/api-search/")
    elif "not found" in low or "no parts" in low:
        print(f"       The key was accepted but Mouser has no '{args.mpn}'.")
        print("       Retry with --mpn on a part Mouser definitely stocks.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
