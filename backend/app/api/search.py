"""Search-analytics endpoints.

`/search-events/analytics` is a cross-user view, so it's admin-gated (DB
`is_admin` flag, see services/admin.py). `/search-events/mine` lets any user
read their own history.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models_db import UserRow
from app.services import search_log
from app.services.admin import require_admin
from app.services.auth import current_user

router = APIRouter()


@router.get("/search-events/analytics")
def analytics(_: UserRow = Depends(require_admin)) -> dict:
    """Aggregate of all collected searches — top queries, per-user breakdown,
    recent events, and zero-result searches. Admin-only."""
    return search_log.get_analytics()


@router.get("/search-events/mine")
def my_history(user: UserRow = Depends(current_user)) -> list[dict]:
    """The signed-in user's own search history."""
    return search_log.get_user_history(user.email)
