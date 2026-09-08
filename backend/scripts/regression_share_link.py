"""Guard: the read-only share link (review #39, Flaw 3).

"The platform provides a button to copy a share link for the project, but this
link is broken for everyone except the original creator." It copied the editor
URL, which is owner-scoped. Now a share is a token on the project row:

  * only the owner can mint or revoke it;
  * a viewer with the token reads the project (and its summary) with NO
    session, and never receives the token back in the payload;
  * no token / a wrong token / a revoked token = 404, not 403 (an id's
    existence must not leak);
  * a routine save (`upsert`) neither leaks nor revokes the share.

Runs against a scratch SQLite file — set DATABASE_URL before the app imports:

    DATABASE_URL=sqlite:///./scratch_share.db python -m scripts.regression_share_link

Offline, no LLM spend.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

if not os.environ.get("DATABASE_URL", "").startswith("sqlite:///./scratch"):
    print("refusing to run against the real database — set "
          "DATABASE_URL=sqlite:///./scratch_share.db")
    sys.exit(2)

from fastapi import HTTPException  # noqa: E402

from app import db  # noqa: E402
from app.api.projects import _readable_project, create_share, revoke_share  # noqa: E402
from app.models_db import UserRow  # noqa: E402
from app.services import store  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def raises_404(fn) -> bool:
    try:
        fn()
    except HTTPException as exc:
        return exc.status_code == 404
    return False


def main() -> int:
    db.init_db()
    with db.SessionLocal() as s:
        for uid, email in (("owner-1", "owner@example.test"), ("other-1", "other@example.test")):
            if s.get(UserRow, uid) is None:
                s.add(UserRow(id=uid, email=email, name=email.split("@")[0],
                              password_hash="x"))
        s.commit()
        owner = s.get(UserRow, "owner-1")
        other = s.get(UserRow, "other-1")

    project = store.create_project("share-guard", user_id="owner-1")
    pid = project.id

    print("\n-- before sharing, nobody but the owner can read it")
    check("owner reads", _readable_project(pid, owner, None).id == pid)
    check("anonymous, no token -> 404", raises_404(lambda: _readable_project(pid, None, None)))
    check("anonymous, made-up token -> 404",
          raises_404(lambda: _readable_project(pid, None, "not-a-token")))
    check("another user -> 404", raises_404(lambda: _readable_project(pid, other, None)))
    check("store has no token", store.get_project(pid).shareToken is None)

    print("\n-- only the owner can mint")
    check("other user cannot share", raises_404(lambda: create_share(pid, other)))
    share = create_share(pid, owner)
    tok = share.token or ""
    check("owner mints a token", len(tok) >= 20)
    check("path carries id + token", share.path == f"/shared/{pid}?share={tok}")
    again = create_share(pid, owner)
    check("minting again is idempotent (a sent link keeps working)", again.token == tok)

    print("\n-- a viewer with the token reads, and never sees the token")
    viewed = _readable_project(pid, None, tok)
    check("anonymous + token reads the project", viewed.id == pid)
    check("the viewer's copy strips shareToken", viewed.shareToken is None)
    check("another signed-in user + token reads too", _readable_project(pid, other, tok).id == pid)
    check("owner's copy carries the token", _readable_project(pid, owner, None).shareToken == tok)
    check("wrong token still 404", raises_404(lambda: _readable_project(pid, None, tok + "x")))

    print("\n-- a routine save does not touch the share")
    store.upsert(store.get_project(pid))
    check("upsert keeps the token", store.get_project(pid).shareToken == tok)
    check("viewer still reads after a save", _readable_project(pid, None, tok).id == pid)

    print("\n-- revoke")
    check("other user cannot revoke", raises_404(lambda: revoke_share(pid, other)))
    revoke_share(pid, owner)
    check("token gone", store.get_project(pid).shareToken is None)
    check("old link is dead", raises_404(lambda: _readable_project(pid, None, tok)))
    check("owner still reads", _readable_project(pid, owner, None).id == pid)

    store.delete_project(pid, user_id="owner-1")
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("All share-link checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
