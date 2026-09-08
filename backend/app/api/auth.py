"""Auth endpoints: signup, login, logout, me.

Sessions are HttpOnly signed cookies (see services/auth.py).
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

import os
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.db import SessionLocal
from app.models_db import UserRow
from app.services import auth as auth_service
from app.services import posthog_client

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    # The ID token (JWT) returned by Google Identity Services on the client.
    credential: str = Field(..., min_length=1)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    is_admin: bool = False
    policies_accepted: bool = False

    @classmethod
    def from_row(cls, row: UserRow) -> "UserOut":
        from app.services.admin import is_admin

        return cls(
            id=row.id,
            email=row.email,
            name=row.name,
            is_admin=is_admin(row),
            policies_accepted=row.policy_accepted_at is not None,
        )


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _password_strong_enough(password: str) -> bool:
    # Mirrors the helper text on the signup form: 8+ chars, at least one digit.
    return len(password) >= 8 and bool(re.search(r"\d", password))


@router.post("/signup", response_model=UserOut)
def signup(body: SignupRequest, response: Response) -> UserOut:
    if not _password_strong_enough(body.password):
        raise HTTPException(
            400, "Password must be at least 8 characters with one number."
        )
    email = _normalize_email(body.email)

    with SessionLocal() as db:
        existing = db.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(409, "An account with that email already exists.")

        user = UserRow(
            id=uuid.uuid4().hex,
            email=email,
            name=body.name.strip(),
            password_hash=auth_service.hash_password(body.password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        auth_service.issue_session_cookie(response, user.id)
        posthog_client.capture(user.email, "signup", {"method": "password"})
        return UserOut.from_row(user)


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, response: Response) -> UserOut:
    email = _normalize_email(body.email)
    with SessionLocal() as db:
        user = db.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        if user is None or not auth_service.verify_password(
            body.password, user.password_hash
        ):
            # Same error for both cases — don't leak which one matched.
            raise HTTPException(401, "Invalid email or password.")
        auth_service.issue_session_cookie(response, user.id)
        posthog_client.capture(user.email, "login", {"method": "password"})
        return UserOut.from_row(user)


@router.post("/google", response_model=UserOut)
def google_auth(body: GoogleAuthRequest, response: Response) -> UserOut:
    """Sign in (or sign up) with a Google ID token from GIS.

    Account resolution order:
      1. Match on google_sub  → returning Google user.
      2. Match on email       → existing password account; link it to Google.
      3. Otherwise            → create a new passwordless account.
    """
    try:
        claims = auth_service.verify_google_id_token(body.credential)
    except RuntimeError:
        # GOOGLE_CLIENT_ID unset — feature not configured on this deployment.
        raise HTTPException(503, "Google sign-in is not configured.")
    except Exception:
        raise HTTPException(401, "Could not verify Google sign-in. Please try again.")

    if not claims.get("email") or not claims.get("email_verified"):
        raise HTTPException(401, "Your Google account has no verified email.")

    sub = str(claims["sub"])
    email = _normalize_email(str(claims["email"]))
    name = (claims.get("name") or "").strip() or email.split("@")[0]
    avatar = claims.get("picture")

    created = False
    with SessionLocal() as db:
        user = db.execute(
            select(UserRow).where(UserRow.google_sub == sub)
        ).scalar_one_or_none()

        if user is None:
            # No Google-linked account yet — fall back to email so we link an
            # existing password account rather than creating a duplicate.
            user = db.execute(
                select(UserRow).where(UserRow.email == email)
            ).scalar_one_or_none()
            if user is not None:
                user.google_sub = sub

        if user is None:
            user = UserRow(
                id=uuid.uuid4().hex,
                email=email,
                name=name,
                password_hash=None,
                google_sub=sub,
                avatar_url=avatar,
            )
            db.add(user)
            created = True
        elif avatar and not user.avatar_url:
            user.avatar_url = avatar

        db.commit()
        db.refresh(user)
        auth_service.issue_session_cookie(response, user.id)
        # A brand-new Google account is a signup as well as a login.
        if created:
            posthog_client.capture(user.email, "signup", {"method": "google"})
        posthog_client.capture(user.email, "login", {"method": "google"})
        return UserOut.from_row(user)


# --- Synqit CAD handoff (2026-09) -------------------------------------------
# GET /auth/handoff?return_to=<cad app /sso url>
#   signed in  -> 302 to return_to?token=<5-minute handoff token>
#   signed out -> 302 to the Synqit login page with ?next= pointing back here
# POST /auth/handoff/verify {token} -> UserOut   (called by the CAD server)
# return_to must be an origin listed in CAD_APP_ORIGINS (comma-separated;
# default http://localhost:3000) — never an open redirect.


class HandoffVerifyRequest(BaseModel):
    token: str = Field(..., min_length=1)


def _cad_app_origins() -> list[str]:
    raw = os.environ.get("CAD_APP_ORIGINS", "http://localhost:3000")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def _synqit_app_url() -> str:
    return os.environ.get("SYNQIT_APP_URL", "http://localhost:5173").rstrip("/")


@router.get("/handoff")
def handoff(
    return_to: str,
    user: Optional[UserRow] = Depends(auth_service.current_user_optional),
):
    parsed = urlparse(return_to)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme not in ("http", "https") or origin not in _cad_app_origins():
        raise HTTPException(400, "return_to is not an allowed CAD app origin")
    if user is None:
        here = f"/api/auth/handoff?return_to={quote(return_to, safe='')}"
        return RedirectResponse(
            f"{_synqit_app_url()}/login?next={quote(here, safe='')}",
            status_code=302,
        )
    token = auth_service.handoff_token(user.id)
    sep = "&" if parsed.query else "?"
    return RedirectResponse(f"{return_to}{sep}token={token}", status_code=302)


@router.post("/handoff/verify", response_model=UserOut)
def handoff_verify(body: HandoffVerifyRequest) -> UserOut:
    user_id = auth_service.decode_handoff_token(body.token)
    if not user_id:
        raise HTTPException(401, "Invalid or expired handoff token")
    with SessionLocal() as db:
        user = db.get(UserRow, user_id)
        if user is None:
            raise HTTPException(401, "Unknown user")
        return UserOut.from_row(user)


# --- Studio service session (2026-09) ---------------------------------------
# The studio is the product's front door and owns sign-in; the engine owns
# projects, the library and the agent. Rather than ask a person to hold two
# accounts, the studio's *server* presents this endpoint with a shared secret
# and the email of the person it has already authenticated, and gets back an
# engine session for them. The engine account is an implementation detail
# nobody has to know about.
#
# The secret never reaches a browser. If STUDIO_SERVICE_SECRET is unset the
# endpoint refuses outright rather than defaulting to open -- an unconfigured
# deployment must not be a passwordless login for any email.


class ServiceSessionRequest(BaseModel):
    email: EmailStr
    name: Optional[str] = None


@router.post("/service/session", response_model=UserOut)
def service_session(
    body: ServiceSessionRequest,
    response: Response,
    x_service_secret: Optional[str] = Header(default=None),
) -> UserOut:
    expected = os.environ.get("STUDIO_SERVICE_SECRET", "")
    if not expected:
        raise HTTPException(503, "Service sessions are not configured.")
    if not x_service_secret or not secrets.compare_digest(x_service_secret, expected):
        raise HTTPException(401, "Bad service secret.")

    email = _normalize_email(str(body.email))
    name = (body.name or "").strip() or email.split("@")[0]

    with SessionLocal() as db:
        user = db.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalar_one_or_none()
        created = user is None
        if user is None:
            # Passwordless, like a Google account: the studio vouches for them.
            user = UserRow(
                id=uuid.uuid4().hex,
                email=email,
                name=name,
                password_hash=None,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        auth_service.issue_session_cookie(response, user.id)
        if created:
            posthog_client.capture(user.email, "signup", {"method": "studio"})
        posthog_client.capture(user.email, "login", {"method": "studio"})
        return UserOut.from_row(user)


@router.post("/accept-policies", response_model=UserOut)
def accept_policies(user: UserRow = Depends(auth_service.current_user)) -> UserOut:
    with SessionLocal() as db:
        row = db.get(UserRow, user.id)
        if row is None:
            raise HTTPException(401, "Not authenticated")
        row.policy_accepted_at = datetime.now(timezone.utc)
        row.privacy_policy_version = "2026-06-28"
        row.terms_version = "2026-06-28"
        db.commit()
        db.refresh(row)
        return UserOut.from_row(row)


@router.post("/logout")
def logout(response: Response) -> dict:
    auth_service.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=Optional[UserOut])
def me(
    user: Optional[UserRow] = Depends(auth_service.current_user_optional),
) -> Optional[UserOut]:
    if user is None:
        return None
    return UserOut.from_row(user)


# ---- Bring your own model (Prompt to Product, 2026-09) ---------------------
# The key never leaves the server: PUT stores it encrypted, GET returns only
# "set" + the last four characters, and /test makes one tiny completion so a
# wrong key, endpoint or model name is caught here — not mid-build.

from app.services import llm_config  # noqa: E402


@router.get("/me/llm", response_model=llm_config.LlmSettingsOut)
def get_llm_settings(user: UserRow = Depends(auth_service.current_user)) -> llm_config.LlmSettingsOut:
    return llm_config.settings_out(user)


@router.put("/me/llm", response_model=llm_config.LlmSettingsOut)
def put_llm_settings(
    body: llm_config.LlmSettingsIn, user: UserRow = Depends(auth_service.current_user)
) -> llm_config.LlmSettingsOut:
    with SessionLocal() as db:
        row = db.get(UserRow, user.id)
        if row is None:
            raise HTTPException(404, "User not found")
        row.llm_provider = body.provider
        row.llm_model = (body.model or "").strip() or None
        row.llm_base_url = (body.base_url or "").strip() or None
        row.llm_api_version = (body.api_version or "").strip() or None
        if body.provider == "synqit":
            row.llm_api_key_enc = None          # no key is kept for the subscription path
            row.llm_base_url = None
            row.llm_api_version = None
        elif body.api_key is not None:
            key = body.api_key.strip()
            row.llm_api_key_enc = llm_config.encrypt_key(key) if key else None
        db.commit()
        db.refresh(row)
        return llm_config.settings_out(row)


@router.post("/me/llm/test")
def test_llm_settings(
    body: llm_config.LlmSettingsIn, user: UserRow = Depends(auth_service.current_user)
) -> dict:
    """Try the settings in the request (a key typed but not yet saved wins;
    otherwise the stored one). Returns {ok, message}; never raises for a bad key."""
    if body.provider == "synqit":
        rt = llm_config.runtime_for_user(None)
        ok, msg = (True, f"Synqit's own model: {rt.model}") if rt.api_key else (False, "Server has no OPENAI_API_KEY.")
        return {"ok": ok, "message": msg}
    key = (body.api_key or "").strip() or llm_config.decrypt_key(getattr(user, "llm_api_key_enc", None))
    if not key:
        return {"ok": False, "message": "Enter an API key first."}
    if not (body.model or "").strip():
        return {"ok": False, "message": "Enter a model name."}
    rt = llm_config.LlmRuntime(
        provider=body.provider,
        api_key=key,
        base_url=(body.base_url or "").strip() or llm_config.PRESET_BASE_URL.get(body.provider),
        model=body.model.strip(),
        api_version=(body.api_version or "").strip() or None,
    )
    ok, msg = llm_config.test_runtime(rt)
    return {"ok": ok, "message": msg}
