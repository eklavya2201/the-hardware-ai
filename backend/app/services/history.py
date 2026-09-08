"""Project version history.

Every mutation creates a `ProjectCommitRow` snapshot. The list/get/revert API
sits on top. Storage strategy: full project snapshot per commit (simpler than
per-field diffs; the 50-component projects we target produce ~50KB JSON each,
trivial for SQLite).

Revert semantics are git-checkout-with-followup-commit: clicking Revert
restores the project state to that commit AND inserts a new commit ('Reverted
to <X>') so the previous head is preserved and you can undo the revert itself.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Project
from app.models_db import ProjectCommitRow, ProjectRow

_lock = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:32]


def _latest_commit_id(session, project_id: str) -> str | None:
    row = (
        session.execute(
            select(ProjectCommitRow.id)
            .where(ProjectCommitRow.project_id == project_id)
            .order_by(ProjectCommitRow.created_at.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    return row


def commit(
    project: Project,
    message: str,
    author: str = "user",
) -> str:
    """Snapshot the given project state and return the new commit id."""
    snapshot = project.model_dump()
    commit_id = _new_id()
    with _lock, SessionLocal() as session:
        parent_id = _latest_commit_id(session, project.id)
        row = ProjectCommitRow(
            id=commit_id,
            project_id=project.id,
            parent_id=parent_id,
            message=message[:500],
            author=author,
            created_at=_now(),
            snapshot=snapshot,
        )
        session.add(row)
        session.commit()
    return commit_id


def list_commits(project_id: str, limit: int = 100) -> list[dict]:
    """Return commit metadata (without the snapshot — keeps the response small)
    newest first."""
    with SessionLocal() as session:
        rows = (
            session.execute(
                select(ProjectCommitRow)
                .where(ProjectCommitRow.project_id == project_id)
                .order_by(ProjectCommitRow.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": row.id,
                "parent_id": row.parent_id,
                "message": row.message,
                "author": row.author,
                "created_at": row.created_at.isoformat() if row.created_at else _now().isoformat(),
                # Light summary so the panel can show counts without fetching the snapshot.
                "component_count": len((row.snapshot or {}).get("ir", {}).get("components", []) or []),
                "net_count": len((row.snapshot or {}).get("ir", {}).get("nets", []) or []),
            }
            for row in rows
        ]


def get_commit(commit_id: str) -> dict | None:
    with SessionLocal() as session:
        row = session.get(ProjectCommitRow, commit_id)
        if not row:
            return None
        return {
            "id": row.id,
            "parent_id": row.parent_id,
            "project_id": row.project_id,
            "message": row.message,
            "author": row.author,
            "created_at": row.created_at.isoformat() if row.created_at else _now().isoformat(),
            "snapshot": row.snapshot,
        }


def revert_to(project_id: str, commit_id: str) -> Project:
    """Restore the project state to the snapshot at `commit_id` and write a
    new commit recording the revert. Returns the restored Project."""
    record = get_commit(commit_id)
    if not record or record.get("project_id") != project_id:
        raise KeyError(commit_id)

    snapshot = record["snapshot"]
    project = Project.model_validate(snapshot)
    project.updatedAt = _now().isoformat()

    # Persist the restored project state.
    from app.services import store as store_module  # avoid circular import

    store_module.upsert(project)

    # Record a follow-up commit so the previous head isn't lost.
    commit(
        project,
        message=f"Reverted to commit {commit_id[:8]} ({record.get('message', '')[:80]})",
        author="user",
    )
    return project


def cleanup_for_project(project_id: str) -> int:
    """Delete all commits for a project. Used by `delete_project` — not exposed
    to the API yet (no delete endpoint), but ready for it."""
    with _lock, SessionLocal() as session:
        deleted = (
            session.query(ProjectCommitRow)
            .filter(ProjectCommitRow.project_id == project_id)
            .delete(synchronize_session=False)
        )
        session.commit()
        return deleted
