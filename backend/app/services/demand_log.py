"""Demand log — what the agent looked for and did not find.

The library grew to 23,941 parts mostly by catalogue sweep, which is why it
holds thousands of capacitor variants and still misses the relay module a user
actually asked for. The fix is to grow it by DEMAND, and demand has to be
measured: every `search_parts` call is appended here with its result count, so
a zero-result search is evidence rather than a shrug.

Append-only JSONL, one line per search, no user text beyond the query the agent
itself composed. `scripts/demand_report.py` reads it. Best-effort throughout: a
logging failure must never break a build.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_PATH = Path(
    os.environ.get(
        "SYNQIT_DEMAND_LOG",
        str(Path(__file__).resolve().parent.parent / "library" / "demand_log.jsonl"),
    )
)
MAX_BYTES = 8 * 1024 * 1024  # roll at 8 MB; the report only needs recent history


def record(query: str | None, category: str | None, count: int,
           hits: list[str] | None = None, project_id: str | None = None) -> None:
    """One search, its result count, and the top MPNs it did return."""
    try:
        row = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "query": (query or "")[:200],
            "category": (category or None),
            "count": int(count),
            "hits": (hits or [])[:5],
            "project": project_id,
        }
        with _LOCK:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            if _PATH.exists() and _PATH.stat().st_size > MAX_BYTES:
                _PATH.replace(_PATH.with_suffix(".jsonl.1"))
            with _PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — telemetry must never break a build
        pass


def read(limit: int | None = None) -> list[dict]:
    """Every logged search, oldest first. Missing file = no history, not an error."""
    rows: list[dict] = []
    for path in (_PATH.with_suffix(".jsonl.1"), _PATH):
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return rows[-limit:] if limit else rows
