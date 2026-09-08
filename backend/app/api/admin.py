"""Admin portal endpoints — all gated by services.admin.require_admin.

Surfaces the analytics overview (events + searches) and admin-user management
(list / grant / revoke).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from app.models_db import UserRow
from app.services import admin as admin_service
from app.services import analytics, search_log

router = APIRouter(prefix="/admin", tags=["admin"])


class GrantAdminRequest(BaseModel):
    email: EmailStr


class CreateAccountRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=200)
    # Optional — omit for a Google/passwordless account.
    password: Optional[str] = Field(default=None, max_length=200)
    is_admin: bool = False


@router.get("/overview")
def overview(_: UserRow = Depends(admin_service.require_admin)) -> dict:
    """Everything the admin dashboard needs in one call: event analytics plus
    the existing search analytics."""
    return {
        "events": analytics.get_analytics(),
        "searches": search_log.get_analytics(),
    }


@router.get("/users")
def users(_: UserRow = Depends(admin_service.require_admin)) -> list[dict]:
    """Directory of users that have any tracked activity, with totals."""
    return analytics.list_users()


@router.get("/users/{email}/events")
def user_events(
    email: str,
    _: UserRow = Depends(admin_service.require_admin),
) -> dict:
    """A single user's full event timeline."""
    return analytics.get_user_events(email)


@router.get("/admins")
def list_admins(_: UserRow = Depends(admin_service.require_admin)) -> list[dict]:
    return admin_service.list_admins()


@router.post("/admins")
def grant_admin(
    body: GrantAdminRequest,
    _: UserRow = Depends(admin_service.require_admin),
) -> dict:
    return admin_service.grant_admin(str(body.email))


@router.delete("/admins/{email}")
def revoke_admin(
    email: str,
    acting: UserRow = Depends(admin_service.require_admin),
) -> dict:
    return admin_service.revoke_admin(email, acting)


# --- Account management ----------------------------------------------------

@router.get("/accounts")
def list_accounts(_: UserRow = Depends(admin_service.require_admin)) -> list[dict]:
    """Every account (not just active ones)."""
    return admin_service.list_accounts()


@router.post("/accounts")
def create_account(
    body: CreateAccountRequest,
    _: UserRow = Depends(admin_service.require_admin),
) -> dict:
    return admin_service.create_account(
        str(body.email), body.name, body.password, body.is_admin
    )


@router.delete("/accounts/{email}")
def delete_account(
    email: str,
    acting: UserRow = Depends(admin_service.require_admin),
) -> dict:
    return admin_service.delete_account(email, acting)


