"""Authentication primitives.

- Passwords hashed with bcrypt directly. We pre-hash with SHA-256 + base64 so
  the input to bcrypt is always 44 bytes, well under bcrypt's 72-byte limit.
  passlib (the more common choice) was dropped because passlib 1.7.4 ships
  a broken wrap-bug check that explodes against modern bcrypt (>= 4.0).
- Sessions stored as a signed cookie containing just the user_id. We use
  itsdangerous URLSafeTimedSerializer so the token is tamper-proof and
  expires server-side (max-age check), without needing a sessions table.

Cross-origin cookies (Netlify frontend → Render backend) require
SameSite=None and Secure. Locally (http) we relax to SameSite=Lax.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.db import SessionLocal
from app.models_db import UserRow

SESSION_COOKIE_NAME = "session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _prehash(plaintext: str) -> bytes:
    digest = hashlib.sha256(plaintext.encode("utf-8")).digest()
    return base64.b64encode(digest)  # 44 bytes — fixed, well under 72


def _secret_key() -> str:
    # In dev, fall back to a fixed string so tokens survive restarts.
    # Production MUST set SESSION_SECRET in env.
    return os.environ.get("SESSION_SECRET", "dev-only-do-not-use-in-prod")


def google_client_id() -> str:
    cid = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not cid:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID is not set — Google sign-in is unavailable."
        )
    return cid


# Reuse a single transport so Google's signing certs are cached across requests
# rather than refetched on every verification.
_google_request = None


def verify_google_id_token(credential: str) -> dict:
    """Verify a Google ID token (the GIS `credential` JWT) and return its claims.

    verify_oauth2_token checks the signature against Google's certs, the
    expiry, and that the audience matches our client ID. We additionally pin
    the issuer. Raises ValueError on any invalid token.
    """
    global _google_request
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    if _google_request is None:
        _google_request = google_requests.Request()

    claims = google_id_token.verify_oauth2_token(
        credential, _google_request, google_client_id()
    )
    if claims.get("iss") not in (
        "accounts.google.com",
        "https://accounts.google.com",
    ):
        raise ValueError("Untrusted token issuer")
    return claims


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret_key(), salt="synqit-session")


def _is_secure_cookie() -> bool:
    # Set COOKIE_SECURE=1 in prod (Render). Defaults to off so dev works on http.
    return os.environ.get("COOKIE_SECURE", "0") == "1"


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(_prehash(plaintext), bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(_prehash(plaintext), stored_hash.encode("utf-8"))
    except Exception:
        return False


def issue_session_cookie(response: Response, user_id: str) -> None:
    token = _serializer().dumps(user_id)
    secure = _is_secure_cookie()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        # SameSite=None requires Secure. In local dev we keep it Lax so the
        # cookie still flows on same-site navigations.
        samesite="none" if secure else "lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        # Match the same attributes used when issuing.
        secure=_is_secure_cookie(),
        samesite="none" if _is_secure_cookie() else "lax",
    )


def _decode_token(token: str) -> Optional[str]:
    try:
        return _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


# --- Cross-app handoff (Synqit CAD, 2026-09) -------------------------------
# A signed-in Synqit user opens the CAD app without a second login: the
# backend mints a short-lived token bound to the user id, the CAD app posts it
# back to /auth/handoff/verify server-to-server. Same secret, different salt,
# so a handoff token can never be replayed as a session cookie or vice versa.
HANDOFF_SALT = "cad-handoff"
HANDOFF_MAX_AGE_SECONDS = 5 * 60


def handoff_token(user_id: str) -> str:
    return URLSafeTimedSerializer(_secret_key(), salt=HANDOFF_SALT).dumps(user_id)


def decode_handoff_token(token: str) -> Optional[str]:
    try:
        return URLSafeTimedSerializer(_secret_key(), salt=HANDOFF_SALT).loads(
            token, max_age=HANDOFF_MAX_AGE_SECONDS
        )
    except (BadSignature, SignatureExpired):
        return None


def current_user_optional(
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Optional[UserRow]:
    if not session:
        return None
    user_id = _decode_token(session)
    if not user_id:
        return None
    with SessionLocal() as db:
        return db.get(UserRow, user_id)


def current_user(
    user: Optional[UserRow] = Depends(current_user_optional),
) -> UserRow:
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user
