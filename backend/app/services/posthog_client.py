"""Server-side PostHog capture.

Mirrors the best-effort philosophy of `analytics` / `search_log`: capturing an
event must never break the request that produced it. This owns the handful of
conversion-critical events (`signup`, `login`, `ai_prompt`) server-side so they
land in PostHog even when the browser SDK is blocked by an ad-blocker or the
user hasn't consented to client-side analytics.

Dormant until `POSTHOG_API_KEY` is set — with no key, every call is a cheap
no-op, so the app runs identically in dev / self-hosted setups. Use the SAME
project key here as the frontend's `VITE_PUBLIC_POSTHOG_KEY`.
"""

from __future__ import annotations

import atexit
import os
from typing import Any

_client: Any | None = None
_initialised = False


def _get_client() -> Any | None:
    """Lazily construct the PostHog client from env. Cached; returns None when
    unconfigured or when the SDK isn't installed, so callers stay no-ops."""
    global _client, _initialised
    if _initialised:
        return _client
    _initialised = True

    api_key = os.environ.get("POSTHOG_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from posthog import Posthog

        host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com").strip()
        _client = Posthog(project_api_key=api_key, host=host)
        atexit.register(shutdown)  # flush any buffered events on process exit
    except Exception:  # noqa: BLE001 — analytics must never break startup
        _client = None
    return _client


def capture(
    distinct_id: str | None,
    event: str,
    properties: dict[str, Any] | None = None,
) -> None:
    """Record one event for a user. `distinct_id` should be the user's email so
    it matches the frontend `posthog.identify(email)` call. Best-effort."""
    client = _get_client()
    if client is None or not distinct_id:
        return
    try:
        client.capture(
            distinct_id=distinct_id,
            event=event,
            properties=properties or {},
        )
    except Exception:  # noqa: BLE001 — never propagate telemetry failures
        pass


def shutdown() -> None:
    """Flush buffered events. Called on app shutdown and at process exit."""
    if _client is None:
        return
    try:
        _client.flush()
    except Exception:  # noqa: BLE001
        pass
