"""Admin authorization and admin-user management.

A user is an admin if their `users.is_admin` flag is set OR their email is in
the bootstrap set (founder + ADMIN_EMAILS). Bootstrap admins are immutable —
they can't be demoted through the API, so the portal can never be locked out.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, HTTPException
from sqlalchemy import delete, select

from app.db import SessionLocal, bootstrap_admin_emails
from app.models_db import ProjectCommitRow, ProjectRow, UserRow
from app.services import auth as auth_service
from app.services.auth import current_user


def is_admin(user: UserRow) -> bool:
    return bool(user.is_admin) or user.email.lower() in bootstrap_admin_emails()


def is_protected(email: str) -> bool:
    """Bootstrap admins can't be demoted."""
    return email.lower() in bootstrap_admin_emails()


def require_admin(user: UserRow = Depends(current_user)) -> UserRow:
    if not is_admin(user):
        raise HTTPException(403, "Admin access required.")
    return user


def list_admins() -> list[dict[str, Any]]:
    """Every admin account, newest first. Includes bootstrap admins that have
    signed up (the flag is seeded for them on startup)."""
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(UserRow)
                .where(UserRow.is_admin.is_(True))
                .order_by(UserRow.created_at.desc())
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "email": r.email,
            "name": r.name,
            "protected": is_protected(r.email),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def grant_admin(email: str) -> dict[str, Any]:
    """Promote an existing user to admin. They must already have an account —
    we don't pre-create rows for emails that have never signed in."""
    email = email.strip().lower()
    if not email:
        raise HTTPException(400, "Email is required.")
    with SessionLocal() as db:
        user = db.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(
                404,
                "No account with that email yet. Ask them to sign in once, then "
                "grant admin access.",
            )
        user.is_admin = True
        db.commit()
        db.refresh(user)
        return {"id": user.id, "email": user.email, "name": user.name}


def revoke_admin(email: str, acting_user: UserRow) -> dict[str, Any]:
    """Demote an admin. Bootstrap admins and your own account are protected so
    you can't lock yourself (or the founder) out."""
    email = email.strip().lower()
    if is_protected(email):
        raise HTTPException(400, "This admin is protected and can't be removed.")
    if email == acting_user.email.lower():
        raise HTTPException(400, "You can't remove your own admin access.")
    with SessionLocal() as db:
        user = db.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        if user is None or not user.is_admin:
            raise HTTPException(404, "That user isn't an admin.")
        user.is_admin = False
        db.commit()
        return {"email": email}


# --- Account management (admin user directory) -----------------------------

def _account_dict(user: UserRow) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_admin": is_admin(user),
        "protected": is_protected(user.email),
        "auth": "google" if user.google_sub else "password",
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def list_accounts(limit: int = 1000) -> list[dict[str, Any]]:
    """Every account, newest first — the admin user-management directory.
    (Distinct from analytics.list_users, which only lists users with activity.)"""
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(UserRow).order_by(UserRow.created_at.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
    return [_account_dict(r) for r in rows]


def create_account(
    email: str,
    name: str,
    password: str | None = None,
    make_admin: bool = False,
) -> dict[str, Any]:
    """Admin-create a user. Password is optional — leave it blank for an account
    that signs in with Google (or has a password set later)."""
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if not email:
        raise HTTPException(400, "Email is required.")
    if not name:
        raise HTTPException(400, "Name is required.")
    if password is not None and password != "" and len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    with SessionLocal() as db:
        existing = db.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(409, "An account with that email already exists.")
        user = UserRow(
            id=uuid.uuid4().hex,
            email=email,
            name=name,
            password_hash=(
                auth_service.hash_password(password) if password else None
            ),
            is_admin=bool(make_admin),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return _account_dict(user)


def delete_account(email: str, acting_user: UserRow) -> dict[str, Any]:
    """Permanently delete a user and all their projects/commits. Bootstrap
    admins and your own account are protected."""
    email = (email or "").strip().lower()
    if is_protected(email):
        raise HTTPException(400, "This account is protected and can't be deleted.")
    if email == acting_user.email.lower():
        raise HTTPException(400, "You can't delete your own account here.")
    with SessionLocal() as db:
        user = db.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(404, "No account with that email.")

        # Cascade by hand — there's no ON DELETE CASCADE on these FKs.
        project_ids = (
            db.execute(select(ProjectRow.id).where(ProjectRow.user_id == user.id))
            .scalars()
            .all()
        )
        if project_ids:
            db.execute(
                delete(ProjectCommitRow).where(
                    ProjectCommitRow.project_id.in_(project_ids)
                )
            )
            db.execute(delete(ProjectRow).where(ProjectRow.id.in_(project_ids)))
        db.delete(user)
        db.commit()
        return {"email": email, "deleted_projects": len(project_ids)}

