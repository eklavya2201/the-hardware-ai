"""Every route that CHANGES something must say who is asking.

Darsh found POST /api/library/parts and DELETE /api/library/parts/{mpn}
shipping with no `Depends(current_user)` — an unauthenticated request could add
a part to the library, or delete one, on prod. The two sibling routes in the
same file (`/library/ingest`, `/library/footprint/generate`) DID have it, which
is exactly why nobody noticed: the file looked protected at a glance.

So this guard does not test those two endpoints. It walks EVERY route the app
registers and asserts that anything with a state-changing method resolves
`current_user` somewhere in its dependency tree. A new unprotected write route
fails here the day it is written, whatever file it lands in.

Being public is allowed — it just has to be a decision someone wrote down.
PUBLIC below is that list, and it is deliberately tiny: you cannot log in
through a route that requires you to be logged in.

Offline; no LLM spend, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import create_app  # noqa: E402
from app.services.auth import current_user  # noqa: E402

# (method, path) pairs that are public ON PURPOSE, each with the reason.
PUBLIC: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/signup"): "you cannot have an account before you make one",
    ("POST", "/api/auth/login"): "the point of the route is to get a session",
    ("POST", "/api/auth/google"): "same, via Google",
    ("POST", "/api/auth/logout"): "clearing a cookie must work even with a dead session",
    ("POST", "/api/events/track"): "analytics from the landing page, before signup",
}

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(label)
        print(f"  [FAIL] {label}" + (f"  ({detail})" if detail else ""))


def _requires_user(dependant) -> bool:
    """Does current_user appear anywhere in this route's dependency tree?

    Recursive on purpose: a route protected through a router-level dependency
    or a nested helper is just as protected as one that names it directly."""
    for dep in dependant.dependencies:
        if dep.call is current_user or _requires_user(dep):
            return True
    return False


def test_every_write_route_is_authenticated() -> None:
    app = create_app()
    seen_public: set[tuple[str, str]] = set()
    write_routes = 0

    for route in app.routes:
        methods = set(getattr(route, "methods", None) or ()) & WRITE_METHODS
        path = getattr(route, "path", "")
        for method in sorted(methods):
            write_routes += 1
            key = (method, path)
            protected = _requires_user(route.dependant)
            if key in PUBLIC:
                seen_public.add(key)
                # A route on the allowlist that GAINED auth is not a failure,
                # but the list is now lying — say so.
                check(
                    f"{method} {path} is public by decision",
                    not protected,
                    PUBLIC[key] if not protected else "now requires auth; drop it from PUBLIC",
                )
            else:
                check(f"{method} {path} requires a user", protected)

    check("the app actually registered write routes", write_routes > 20,
          f"{write_routes} found")

    # An allowlist entry for a route that no longer exists hides the next one
    # that takes its place.
    stale = sorted(set(PUBLIC) - seen_public)
    check("no stale PUBLIC entries", not stale, ", ".join(f"{m} {p}" for m, p in stale))


def test_the_two_routes_darsh_found() -> None:
    """Named explicitly so the original finding stays legible in the output."""
    app = create_app()
    by_key = {
        (m, r.path): r
        for r in app.routes
        for m in (getattr(r, "methods", None) or ())
    }
    for key in (("POST", "/api/library/parts"), ("DELETE", "/api/library/parts/{mpn}")):
        route = by_key.get(key)
        check(f"{key[0]} {key[1]} exists", route is not None)
        if route is not None:
            check(f"{key[0]} {key[1]} is closed", _requires_user(route.dependant))


def main() -> int:
    test_every_write_route_is_authenticated()
    test_the_two_routes_darsh_found()

    print(f"\n{PASSED} checks passed, {len(FAILED)} failed.")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("Every state-changing route is authenticated or explicitly public.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
