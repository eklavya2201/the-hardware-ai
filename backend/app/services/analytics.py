"""Product-analytics event capture and aggregation.

Mirrors the best-effort philosophy of search_log: ingesting an event must never
break the request that produced it, and the user's email is always stamped
server-side from the session — never trusted from the client payload.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models_db import EventRow

# Cap how much free-form context we store per event so a malicious or buggy
# client can't bloat the table.
_MAX_PROPS_BYTES = 4000
_MAX_EVENTS_PER_BATCH = 50


def _iso(dt: datetime | None) -> str | None:
    """Serialize a stored datetime as an explicit-UTC ISO string. The DB columns
    are naive but hold UTC wall-clock values; without the +00:00 offset the
    browser would parse them as *local* time and render the wrong hour."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _event_time(ts_ms: Any) -> datetime:
    """Resolve an event's timestamp from the client-supplied epoch-ms value,
    falling back to now. Guards against garbage/out-of-range values so a bad
    client clock can't write an absurd date."""
    now = datetime.now(timezone.utc)
    if not isinstance(ts_ms, (int, float)) or ts_ms <= 0:
        return now
    try:
        when = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return now
    # Reject implausible clocks (before 2020 or more than a day in the future).
    if when.year < 2020 or when > now + timedelta(days=1):
        return now
    return when


def log_events(
    events: list[dict[str, Any]],
    *,
    user_email: str | None,
    anon_id: str | None,
    referrer: str | None,
    user_agent: str | None,
) -> int:
    """Persist a batch of client events. Returns the number stored. Best-effort:
    swallows errors so analytics can never take down the calling request."""
    if not events:
        return 0
    rows: list[EventRow] = []
    for ev in events[:_MAX_EVENTS_PER_BATCH]:
        event_type = str(ev.get("event_type") or "").strip()[:60]
        if not event_type:
            continue
        props = ev.get("props")
        if not isinstance(props, dict):
            props = {}
        # Trim oversized props rather than rejecting the event outright.
        if len(str(props)) > _MAX_PROPS_BYTES:
            props = {"_truncated": True}
        path = ev.get("path")
        rows.append(
            EventRow(
                id=f"ev-{uuid.uuid4().hex[:16]}",
                user_email=user_email,
                anon_id=(anon_id or None),
                event_type=event_type,
                path=(str(path)[:500] if path else None),
                props=props,
                referrer=(str(referrer)[:2000] if referrer else None),
                user_agent=(str(user_agent)[:500] if user_agent else None),
                created_at=_event_time(ev.get("ts")),
            )
        )
    if not rows:
        return 0
    try:
        with SessionLocal() as db:
            db.add_all(rows)
            db.commit()
        return len(rows)
    except Exception:  # noqa: BLE001 — analytics must never break the caller
        return 0


def get_analytics(limit: int = 50, recent_limit: int = 100) -> dict[str, Any]:
    """Aggregate view of all captured events for the admin portal."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    with SessionLocal() as db:
        rows = (
            db.execute(select(EventRow).order_by(EventRow.created_at.desc()))
            .scalars()
            .all()
        )

    total = len(rows)
    by_type = dict(Counter(r.event_type for r in rows).most_common())

    # Top viewed pages (page_view events only).
    page_views = [r for r in rows if r.event_type == "page_view" and r.path]
    top_pages = [
        {"path": p, "count": c}
        for p, c in Counter(r.path for r in page_views).most_common(limit)
    ]

    # Per-user activity, identified vs anonymous.
    identified = [r for r in rows if r.user_email]
    by_user = [
        {"email": email, "count": c}
        for email, c in Counter(r.user_email for r in identified).most_common(limit)
    ]

    def _active_since(cutoff: datetime) -> int:
        emails = {
            r.user_email
            for r in identified
            if r.created_at and r.created_at.replace(tzinfo=timezone.utc) >= cutoff
        }
        return len(emails)

    recent = [
        {
            "email": r.user_email,
            "anon_id": r.anon_id,
            "event_type": r.event_type,
            "path": r.path,
            "props": r.props,
            "created_at": _iso(r.created_at),
        }
        for r in rows[:recent_limit]
    ]

    return {
        "total_events": total,
        "unique_users": len({r.user_email for r in identified}),
        "active_24h": _active_since(day_ago),
        "active_7d": _active_since(week_ago),
        "by_type": by_type,
        "top_pages": top_pages,
        "by_user": by_user,
        "recent": recent,
    }


def list_users(limit: int = 500) -> list[dict[str, Any]]:
    """One row per identified user with their activity totals — the directory
    the admin picks from before drilling into a single user's events."""
    with SessionLocal() as db:
        rows = db.execute(
            select(
                EventRow.user_email,
                func.count(EventRow.id),
                func.max(EventRow.created_at),
                func.min(EventRow.created_at),
            )
            .where(EventRow.user_email.is_not(None))
            .group_by(EventRow.user_email)
        ).all()

    users = [
        {
            "email": email,
            "event_count": count,
            "last_seen": _iso(last),
            "first_seen": _iso(first),
        }
        for email, count, last, first in rows
    ]
    # Most recently active first.
    users.sort(key=lambda u: u["last_seen"] or "", reverse=True)
    return users[:limit]


def get_user_events(email: str, limit: int = 300) -> dict[str, Any]:
    """Full event timeline for one user (newest first) plus a per-type summary.
    Email is matched case-insensitively against the stored (already lowercased)
    value."""
    email = (email or "").strip().lower()
    if not email:
        return {"email": email, "total": 0, "by_type": {}, "events": []}

    with SessionLocal() as db:
        rows = (
            db.execute(
                select(EventRow)
                .where(func.lower(EventRow.user_email) == email)
                .order_by(EventRow.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

    events = [
        {
            "event_type": r.event_type,
            "path": r.path,
            "props": r.props,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]
    return {
        "email": email,
        "total": len(events),
        "by_type": dict(Counter(e["event_type"] for e in events)),
        "events": events,
    }
