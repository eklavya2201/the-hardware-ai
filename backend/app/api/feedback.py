"""User feedback endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.db import SessionLocal
from app.models_db import FeedbackRow, UserRow
from app.services.admin import require_admin
from app.services.auth import current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    product_rating: int = Field(..., ge=1, le=10)
    ui_rating: int = Field(..., ge=1, le=10)
    schematic_rating: int = Field(..., ge=1, le=10)
    pcb_rating: int = Field(..., ge=1, le=10)
    ai_rating: int = Field(..., ge=1, le=10)
    performance_rating: int = Field(..., ge=1, le=10)
    review: str | None = Field(default=None, max_length=4000)


class FeedbackOut(BaseModel):
    id: str
    user_id: str
    user_name: str
    user_email: str
    product_rating: int
    ui_rating: int
    schematic_rating: int
    pcb_rating: int
    ai_rating: int
    performance_rating: int
    review: str | None
    created_at: str | None


def _feedback_out(row: FeedbackRow, user: UserRow) -> FeedbackOut:
    return FeedbackOut(
        id=row.id,
        user_id=row.user_id,
        user_name=user.name,
        user_email=user.email,
        product_rating=row.product_rating,
        ui_rating=row.ui_rating,
        schematic_rating=row.schematic_rating,
        pcb_rating=row.pcb_rating,
        ai_rating=row.ai_rating,
        performance_rating=row.performance_rating,
        review=row.review,
        created_at=row.created_at.isoformat() if isinstance(row.created_at, datetime) else None,
    )


@router.post("", response_model=FeedbackOut)
def submit_feedback(
    body: FeedbackRequest,
    user: UserRow = Depends(current_user),
) -> FeedbackOut:
    review = (body.review or "").strip() or None
    with SessionLocal() as db:
        row = FeedbackRow(
            id=uuid.uuid4().hex,
            user_id=user.id,
            product_rating=body.product_rating,
            ui_rating=body.ui_rating,
            schematic_rating=body.schematic_rating,
            pcb_rating=body.pcb_rating,
            ai_rating=body.ai_rating,
            performance_rating=body.performance_rating,
            review=review,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        fresh_user = db.get(UserRow, user.id) or user
        return _feedback_out(row, fresh_user)


@router.get("/admin", response_model=list[FeedbackOut])
def list_feedback(_: UserRow = Depends(require_admin)) -> list[FeedbackOut]:
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(FeedbackRow, UserRow)
                .join(UserRow, FeedbackRow.user_id == UserRow.id)
                .order_by(desc(FeedbackRow.created_at))
                .limit(500)
            )
            .all()
        )
        return [_feedback_out(feedback, user) for feedback, user in rows]
