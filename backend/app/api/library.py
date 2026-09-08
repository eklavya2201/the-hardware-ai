"""Library endpoints — search, ingest from PDF, commit, delete user parts.

Ingestion is two-step: ingest returns a *proposal* the user reviews, then they
POST it to /library/parts to actually commit. This is deliberate — PRD §10
flags hallucinated parts as a top-3 risk, so we never auto-commit LLM output.
"""

from __future__ import annotations

import gzip
import json

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from app.models import LibraryPart
from app.models_db import UserRow
from app.services import datasheet, library, offers
from app.services.auth import current_user

router = APIRouter()


@router.get("/library", response_model=list[LibraryPart])
def list_library() -> list[LibraryPart]:
    # Attach derived buy-links at the API boundary (on copies, so the shared
    # parts the agent reads stay untouched).
    return [p.model_copy(update={"offers": offers.derive_offers(p)}) for p in library.all_parts()]


_INDEX_CACHE: tuple[float, int, bytes] | None = None


@router.get("/library/index")
def library_index(request: Request) -> Response:
    """Card-sized rows for the Library page grid.

    The full /library payload carries every pin table and application note —
    ~77 MB at 19k parts — which the grid never reads. This returns only what a
    card renders: pin COUNT (not pins) and the first 3 buy-links (the card
    shows at most 3; the rest were pure search-URL templates of the MPN).
    Serialized once and cached in-process, invalidated when the user-parts
    layer changes on disk."""
    global _INDEX_CACHE
    try:
        mtime = library._USER_PARTS_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    parts = library.all_parts()
    key = (mtime, len(parts))
    if _INDEX_CACHE is None or _INDEX_CACHE[:2] != key:
        rows = []
        for p in parts:
            offer_rows = []
            for o in offers.derive_offers(p)[:3]:
                row = o.model_dump(exclude_none=True)
                if row.get("price") is None:
                    row.pop("currency", None)  # currency renders only with a price
                offer_rows.append(row)
            # offers.sourcing exists on the 3d-reset lane only; main has no
            # per-part catalogue audit surface yet — degrade to no verdict.
            _sourcing = getattr(offers, "sourcing", None)
            s = _sourcing(p) if _sourcing else None
            rows.append({
                "mpn": p.mpn,
                "manufacturer": p.manufacturer,
                "category": p.category,
                "package": p.package,
                "description": p.description,
                "interfaces": p.interfaces,
                "pinCount": len(p.pins),
                "unitCost": p.unitCost,
                "datasheetUrl": p.datasheetUrl,
                "imageUrl": p.imageUrl,
                "offers": offer_rows,
                "sourcing": s.model_dump(exclude_none=True) if s else None,
            })
        # Search URLs repeat the same templates 19k times — gzip is ~10:1 here.
        # Compressed once at build, not per request (no global GZip middleware
        # on purpose: it would buffer the SSE chat stream).
        _INDEX_CACHE = (mtime, len(parts),
                        gzip.compress(json.dumps(rows).encode("utf-8"), 6))
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(content=_INDEX_CACHE[2], media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(content=gzip.decompress(_INDEX_CACHE[2]),
                    media_type="application/json")


@router.get("/library/user-parts", response_model=list[LibraryPart])
def list_user_parts() -> list[LibraryPart]:
    return library.list_user_parts()


@router.get("/library/footprint/families")
def footprint_families() -> dict:
    """Parametric footprint-generator families + their form schemas (item #4 —
    the most-requested feature in the market research)."""
    from app.services import footprint_gen

    return {"families": footprint_gen.FAMILIES}


@router.post("/library/footprint/generate")
def generate_footprint(
    body: dict,
    user: UserRow = Depends(current_user),
) -> dict:
    """Generate an IPC-nominal .kicad_mod from {family, params}. Returns the
    file text + pad list (for the client-side preview) + honesty warnings."""
    from app.services import footprint_gen

    family = str(body.get("family") or "")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(422, "params must be an object")
    try:
        return footprint_gen.generate(family, params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/library/ingest")
async def ingest_datasheet(
    file: UploadFile = File(...),
    user: UserRow = Depends(current_user),
) -> dict:
    """Upload a datasheet PDF, get back a proposed LibraryPart for review.
    Does NOT commit — call POST /library/parts to actually save."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Expected a PDF file (.pdf extension).")

    blob = await file.read()
    if len(blob) < 200:
        raise HTTPException(400, "File is too small to be a real datasheet.")
    if len(blob) > 30 * 1024 * 1024:  # 30 MB cap
        raise HTTPException(400, "File too large (>30 MB). Trim to the relevant pages.")

    result = datasheet.ingest(blob)
    return {
        "success": result.success,
        "proposed": result.proposed.model_dump(exclude_none=True) if result.proposed else None,
        "confidence": result.confidence,
        "notes": result.notes,
        "raw_text_sample": result.raw_text_sample,
        "error": result.error,
        "filename": file.filename,
    }


@router.post("/library/parts", response_model=LibraryPart)
def commit_user_part(
    part: LibraryPart,
    user: UserRow = Depends(current_user),
) -> LibraryPart:
    """Save a reviewed/edited part into user_parts.json. Returns it back."""
    if not part.mpn or not part.mpn.strip():
        raise HTTPException(400, "MPN is required.")
    return library.add_user_part(part)


@router.delete("/library/parts/{mpn}")
def delete_user_part(
    mpn: str,
    user: UserRow = Depends(current_user),
) -> dict:
    deleted = library.delete_user_part(mpn)
    if not deleted:
        raise HTTPException(404, f"User part {mpn!r} not found.")
    return {"deleted": mpn}
