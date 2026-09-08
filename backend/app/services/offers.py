"""Derive buy-links for a library part from the distributor SKUs it already
carries — no external API needed. 248 of the curated parts have a DigiKey,
Mouser, or LCSC/JLC part number; each maps to a canonical vendor URL.

Affiliate tags are applied from env, so links go live as plain (still useful)
product links today and become affiliate links the moment a program is
configured — no code change. Two env forms per vendor (VENDOR in DIGIKEY /
MOUSER / LCSC):

    AFFILIATE_<VENDOR>_PARAM = "ref=synqit-20"     # appended as a query param
    AFFILIATE_<VENDOR>_WRAP  = "https://net/r?u="  # redirect wrapper; URL is
                                                    # url-encoded and appended

WRAP wins if both are set (redirect networks like Impact/Partnerize can't be
expressed as a plain query param). When neither is set the link is plain and
Offer.affiliate=False, so the UI makes no affiliate claim.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, quote_plus

from app.models import LibraryPart, Offer, PartSourcing

_AMAZON_OFFERS_PATH = (
    Path(__file__).resolve().parent.parent / "library" / "amazon_offers.json"
)


@lru_cache(maxsize=1)
def _amazon_offers() -> dict[str, dict]:
    """Hand-curated MPN -> Amazon entry map (affiliate Phase 3). Only listed
    hobbyist modules get an Amazon link — bare ICs stay distributor-only.
    Entries carry an honest `search` phrase and an optional exact `asin`
    (hand-verified, never guessed); asin wins when present."""
    try:
        data = json.loads(_AMAZON_OFFERS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _apply_affiliate(url: str, vendor_key: str) -> tuple[str, bool]:
    wrap = os.environ.get(f"AFFILIATE_{vendor_key}_WRAP")
    if wrap:
        return f"{wrap}{quote(url, safe='')}", True
    param = os.environ.get(f"AFFILIATE_{vendor_key}_PARAM")
    if param:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{param}", True
    return url, False


def fab_order_links() -> list[dict]:
    """PCB-fab quote pages for the "Order PCB" flow (affiliate Phase 2). Same
    env convention as parts vendors: AFFILIATE_JLCPCB_* / AFFILIATE_PCBWAY_*
    turn these into referral links with no code change; plain links until then.
    The user uploads the exported Gerber zip on the fab's page — both fabs
    quote from a browser upload, no API key needed.

    Third env form, for referral programs that hand out an opaque short link
    with no destination/param support (PCBWay's pcbway.com/g/<code>):
    AFFILIATE_<VENDOR>_URL replaces the target wholesale — the button lands on
    the referral URL (attribution cookie set), the user proceeds to the quote
    page from there. _URL wins over _WRAP/_PARAM."""
    fabs = [
        ("JLCPCB", "JLCPCB", "https://cart.jlcpcb.com/quote"),
        ("PCBWay", "PCBWAY", "https://www.pcbway.com/orderonline.aspx"),
    ]
    out: list[dict] = []
    for name, key, url in fabs:
        override = os.environ.get(f"AFFILIATE_{key}_URL")
        if override:
            out.append({"vendor": name, "url": override, "affiliate": True})
            continue
        final_url, affiliate = _apply_affiliate(url, key)
        out.append({"vendor": name, "url": final_url, "affiliate": affiliate})
    return out


def sourcing(part: LibraryPart) -> PartSourcing:
    """Read the audit's verdict off the part — never guess one.

    `audit_mpns.py` stamps provenance with "confirmed"/"NOT carried"/"no exact
    listing"; catalogue-driven discovery stamps its own, because a part that
    came BACK from a distributor's catalogue is proven to be in it. Anything
    else has simply never been checked, which is a third state and must not be
    shown as either of the other two."""
    prov = getattr(part, "provenance", None)
    ver = ((getattr(prov, "extractorVersion", "") or "") if prov else "")
    on = ((getattr(prov, "extractedAt", None) or None) if prov else None)

    # A vendor SKU in hand outranks any audit: someone recorded that vendor's
    # own number for this part, which is stronger than a keyword lookup.
    if part.lcscPart or part.jlcPart or part.digikeyPart or part.mouserPart:
        return PartSourcing(checked=True, carried=True, checkedOn=on,
                            note="A distributor part number is on file.")
    if "NOT carried" in ver:
        # Name the catalogue we actually asked. Mouser is one distributor, and
        # plenty of real parts are sold only by DigiKey, LCSC or Adafruit —
        # saying "no distributor" turns a single-source answer into a claim we
        # never checked.
        return PartSourcing(checked=True, carried=False, checkedOn=on,
                            note="Mouser does not list this part number. "
                                 "Other distributors were not asked.")
    if "no exact listing" in ver:
        return PartSourcing(checked=True, carried=False, checkedOn=on,
                            note="Mouser returned similar parts but nothing "
                                 "with this exact part number.")
    if ("confirmed" in ver or "orderable" in ver
            or "expand_passives" in ver or "discover_parts" in ver):
        return PartSourcing(checked=True, carried=True, checkedOn=on,
                            note="Confirmed in a distributor catalogue.")
    return PartSourcing(checked=False, carried=False, checkedOn=on,
                        note="No distributor has been asked about this part "
                             "number yet.")


def derive_offers(part: LibraryPart) -> list[Offer]:
    """Buy-links for a part. Every part gets DigiKey + Mouser (an exact product
    link when we have that vendor's SKU, else a reliable search-by-MPN), plus
    LCSC when the part carries an LCSC/JLC number. So every card has a buy
    option, precise where we can be. `sku` is None for search-based offers.

    We deliberately don't set a `price` — unitCost is a reference cost, not the
    live vendor price, and we won't fabricate one without an API."""
    offers: list[Offer] = []

    def add(vendor: str, vendor_key: str, sku: str | None, url: str) -> None:
        final_url, affiliate = _apply_affiliate(url, vendor_key)
        offers.append(Offer(vendor=vendor, url=final_url, sku=sku, affiliate=affiliate))

    # A BUY LINK IS A PROMISE, AND WE ONLY MAKE IT ON EVIDENCE.
    #
    # Two rounds of this bug on 2026-09-04: first "No results for Fiducial_1mm"
    # (a generic part nobody sells under one number), then "No results for
    # TS-1187A-B-A-B" — which looks exactly like a real MPN and is simply not
    # carried. No string heuristic can tell those apart, because the question
    # is not "is this shaped like a part number" but "does a distributor sell
    # it", and only a distributor can answer that. A 40-part sample of the
    # never-audited KiCad-symbol harvest came back 48% NOT carried, so ~6,600
    # cards were offering a search that lands on an empty page.
    #
    # So: link only where something checked. A vendor SKU we hold is proof.
    # An audit that confirmed the part is proof. Everything else gets no
    # distributor link and an honest "not confirmed" line on the card, until
    # `scripts.audit_mpns` promotes it.
    # Evidence is PER VENDOR. An LCSC number proves LCSC carries the part and
    # says nothing about Mouser — TS-1187A-B-A-B has an LCSC SKU and Mouser
    # answers "No results" for it, which is the second dead link reported on
    # 2026-09-04. What the Mouser-based audit does establish is that the MPN is
    # a real catalogued part number, which makes a DigiKey search sound: both
    # are broad-line distributors carrying the same manufacturers.
    prov = getattr(part, "provenance", None)
    ver = ((getattr(prov, "extractorVersion", "") or "") if prov else "")
    at_mouser = ("_" not in part.mpn) and (
        "confirmed" in ver or "orderable" in ver
        or "expand_passives" in ver or "discover_parts" in ver)

    # DigiKey — exact SKU when we hold one; else only for an MPN a catalogue
    # has actually answered for.
    if part.digikeyPart or at_mouser:
        dk_key = part.digikeyPart or part.mpn
        add(
            "DigiKey",
            "DIGIKEY",
            part.digikeyPart,
            f"https://www.digikey.com/en/products/result?keywords={quote_plus(dk_key)}",
        )

    # Mouser — its own SKU, or its own catalogue's confirmation. Nothing else.
    if part.mouserPart or at_mouser:
        mo_key = part.mouserPart or part.mpn
        add(
            "Mouser",
            "MOUSER",
            part.mouserPart,
            f"https://www.mouser.com/c/?q={quote_plus(mo_key)}",
        )

    # LCSC — only with a real LCSC/JLC part number; their search is unreliable
    # for western MPNs, so we don't fall back to an MPN search here.
    lcsc = part.lcscPart or part.jlcPart
    if lcsc:
        add("LCSC", "LCSC", lcsc, f"https://www.lcsc.com/product-detail/{quote(lcsc)}.html")

    # Amazon (Phase 3) — hand-curated hobbyist modules only. Exact product
    # link when a verified ASIN is stored, else a search link (Amazon permits
    # tagged search links, so affiliate still applies).
    entry = _amazon_offers().get(part.mpn)
    if entry:
        domain = os.environ.get("AMAZON_DOMAIN", "www.amazon.com")
        asin = entry.get("asin")
        url = (
            f"https://{domain}/dp/{quote(asin)}"
            if asin
            else f"https://{domain}/s?k={quote_plus(entry['search'])}"
        )
        add("Amazon", "AMAZON", asin, url)

    return offers
