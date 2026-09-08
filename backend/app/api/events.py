"""Client telemetry ingest.

`POST /events/track` accepts a batch of touchpoint events from the browser
(page views, clicks, named actions). Auth is optional — anonymous (pre-login)
events are kept and stitched to an account later via `anon_id`. The user's
email is always taken from the session, never from the request body.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.models_db import UserRow
from app.services import analytics
from app.services.auth import current_user_optional

router = APIRouter(prefix="/events", tags=["events"])


class ClientEvent(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=60)
    path: str | None = Field(default=None, max_length=500)
    props: dict[str, Any] = Field(default_factory=dict)
    # Client-side epoch milliseconds when the event occurred. Optional; the
    # server falls back to receipt time when absent.
    ts: int | None = None


class TrackRequest(BaseModel):
    anon_id: str | None = Field(default=None, max_length=64)
    events: list[ClientEvent] = Field(default_factory=list)


@router.post("/track")
def track(
    body: TrackRequest,
    request: Request,
    user: Optional[UserRow] = Depends(current_user_optional),
) -> dict:
    stored = analytics.log_events(
        [e.model_dump() for e in body.events],
        user_email=(user.email if user else None),
        anon_id=body.anon_id,
        referrer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
    )
    return {"stored": stored}
