"""Curated parts library — see PRD §7.3.

The agent is hard-constrained to MPNs in this library — no part may exist in
the LLM's output without a corresponding entry. This eliminates hallucination.

Two layers:
  - parts.json: hand-curated, ships with the app, never modified at runtime.
  - user_parts.json: parts ingested from datasheet PDFs at runtime. Sits on
    top of the curated layer; user parts can shadow curated entries.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.models import LibraryPart

_LIBRARY_PATH = Path(__file__).resolve().parent.parent / "library" / "parts.json"
_USER_PARTS_PATH = Path(__file__).resolve().parent.parent / "library" / "user_parts.json"

_lock = threading.RLock()
_curated_cache: list[LibraryPart] | None = None
_user_cache: list[LibraryPart] | None = None


def _load_curated() -> list[LibraryPart]:
    global _curated_cache
    if _curated_cache is None:
        raw = json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))
        _curated_cache = [LibraryPart.model_validate(p) for p in raw]
    return _curated_cache


def _load_user_parts() -> list[LibraryPart]:
    global _user_cache
    if _user_cache is None:
        if _USER_PARTS_PATH.exists():
            raw = json.loads(_USER_PARTS_PATH.read_text(encoding="utf-8"))
            _user_cache = [LibraryPart.model_validate(p) for p in raw]
        else:
            _user_cache = []
    return _user_cache


def _persist_user_parts(parts: list[LibraryPart]) -> None:
    """Atomic-ish write to user_parts.json. Called under _lock."""
    payload = json.dumps([p.model_dump(exclude_none=True) for p in parts], indent=2)
    tmp = _USER_PARTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(_USER_PARTS_PATH)
    global _user_cache
    _user_cache = list(parts)


def all_parts() -> list[LibraryPart]:
    """Return curated + user parts, with user entries overriding any curated
    entry sharing the same MPN."""
    curated = _load_curated()
    user = _load_user_parts()
    user_mpns = {p.mpn for p in user}
    return user + [p for p in curated if p.mpn not in user_mpns]


def core_parts() -> list[LibraryPart]:
    """The parts worth listing in the agent's system prompt: the hand-curated
    base layer plus every part that carries a `typicalApplication` (i.e. was
    curated by a person, not discovered by the catalogue loop). Everything
    else — 23k+ discovered passives/MCUs/regulators — is reachable through
    `search_parts`. Listing the whole library in the prompt was ~900k tokens at
    23,941 parts and broke every build (2026-09-06)."""
    base = {p.mpn for p in _load_curated()}
    return [p for p in all_parts() if p.mpn in base or p.typicalApplication]


def get_part(mpn: str) -> LibraryPart | None:
    for p in _load_user_parts():
        if p.mpn == mpn:
            return p
    for p in _load_curated():
        if p.mpn == mpn:
            return p
    return None


# ---- User parts mutations ------------------------------------------------


def add_user_part(part: LibraryPart) -> LibraryPart:
    with _lock:
        parts = list(_load_user_parts())
        # Replace if MPN already exists.
        parts = [p for p in parts if p.mpn != part.mpn]
        parts.append(part)
        _persist_user_parts(parts)
    return part


def delete_user_part(mpn: str) -> bool:
    with _lock:
        parts = list(_load_user_parts())
        new_parts = [p for p in parts if p.mpn != mpn]
        if len(new_parts) == len(parts):
            return False
        _persist_user_parts(new_parts)
        return True


def list_user_parts() -> list[LibraryPart]:
    return list(_load_user_parts())


def search(query: str | None = None, category: str | None = None) -> list[LibraryPart]:
    """Token search over MPN, description, alternatives and typical application.

    The query is split into words and each word is matched independently, so a
    phrase like "small speaker" or "doorbell chime" finds a part that carries
    ANY of the words — ranked by how many words matched, then by where they
    matched (MPN beats description beats application notes). The old
    whole-query substring match returned NOTHING for any multi-word phrase,
    which the agent then honestly reported as "no such part in the library"
    while the part sat one word away (review #38's missing speaker).
    """
    parts = all_parts()
    if category:
        parts = [p for p in parts if p.category == category]
    if not query:
        return parts
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return parts
    matches: list[tuple[int, int, LibraryPart]] = []
    for p in parts:
        mpn = p.mpn.lower()
        desc = p.description.lower()
        alts = [alt.lower() for alt in p.alternatives]
        app = (p.typicalApplication or "").lower()
        hit_count = 0
        score = 0
        for t in tokens:
            best = 0
            if t in mpn:
                best = 10
            elif t in desc:
                best = 5
            elif any(t in alt for alt in alts):
                best = 2
            elif t in app:
                best = 1
            if best:
                hit_count += 1
                score += best
        if hit_count:
            matches.append((hit_count, score, p))
    matches.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [p for _, _, p in matches]
