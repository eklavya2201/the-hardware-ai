"""Search/query analytics. Records what users search for (currently AI build
prompts) keyed by their email, and aggregates it for the analytics endpoint.

Email is always stamped server-side from the authenticated session — callers
pass it from `current_user.email`, never from client input. Logging is
best-effort: a failure here must never break the request that triggered it."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any


def _iso(dt: datetime | None) -> str | None:
    """UTC-aware ISO string. Stored datetimes are naive UTC; without the offset
    the browser renders them in the wrong timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models_db import SearchEventRow


def log_search(
    user_email: str,
    query: str,
    source: str = "build_prompt",
    result_count: int | None = None,
) -> None:
    """Persist one search event. Best-effort — swallows errors so a logging
    failure can't take down the chat/search request that called it."""
    query = (query or "").strip()
    if not query or not user_email:
        return
    try:
        with SessionLocal() as db:
            db.add(
                SearchEventRow(
                    id=f"se-{uuid.uuid4().hex[:12]}",
                    user_email=user_email,
                    query=query[:4000],
                    source=source,
                    result_count=result_count,
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001 — analytics must never break the caller
        pass


def get_analytics(limit: int = 50) -> dict[str, Any]:
    """Aggregate view of collected searches: totals, top queries, recent
    events, per-user breakdown, and zero-result searches (for sources that
    record a result_count, e.g. future part search)."""
    with SessionLocal() as db:
        rows = (
            db.execute(select(SearchEventRow).order_by(SearchEventRow.created_at.desc()))
            .scalars()
            .all()
        )

    total = len(rows)
    by_source = dict(Counter(r.source for r in rows))

    # Case-insensitive top queries.
    q_counter = Counter(r.query.strip().lower() for r in rows)
    top_queries = [
        {"query": q, "count": c} for q, c in q_counter.most_common(limit)
    ]

    by_user = [
        {"email": email, "count": c}
        for email, c in Counter(r.user_email for r in rows).most_common(limit)
    ]

    recent = [
        {
            "email": r.user_email,
            "query": r.query,
            "source": r.source,
            "result_count": r.result_count,
            "created_at": _iso(r.created_at),
        }
        for r in rows[:limit]
    ]

    zero_result = [
        {"email": r.user_email, "query": r.query, "source": r.source}
        for r in rows
        if r.result_count == 0
    ][:limit]

    return {
        "total": total,
        "by_source": by_source,
        "top_queries": top_queries,
        "by_user": by_user,
        "recent": recent,
        "zero_result": zero_result,
    }


def get_user_history(user_email: str, limit: int = 100) -> list[dict[str, Any]]:
    """A single user's own search history (newest first)."""
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(SearchEventRow)
                .where(SearchEventRow.user_email == user_email)
                .order_by(SearchEventRow.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    return [
        {
            "query": r.query,
            "source": r.source,
            "result_count": r.result_count,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]
