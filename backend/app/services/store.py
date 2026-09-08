"""Project store backed by SQLite via SQLAlchemy.

Public surface stays identical to the previous in-memory version:
  list_projects, get_project, create_project, upsert, append_message,
  seed_demo_projects.

Concurrency: a process-wide RLock guards multi-step operations (create,
upsert, append) so the read → mutate → write sequence is atomic against the
DB. SQLite is fine for single-process dev; swap the engine in app/db.py to
Postgres when going hosted (no other code changes required).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Board,
    BuildGuide,
    ChatMessage,
    Firmware,
    MechanicalIR,
    Project,
    SchematicIR,
)
from app.models_db import ProjectRow

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    return (
        "".join(c if c.isalnum() else "-" for c in name.lower())
        .strip("-")
        .replace("--", "-")
    )


# ---- conversion -----------------------------------------------------------


def _hydrate_descriptions(ir: SchematicIR) -> None:
    """Backfill each component's description from the parts library by MPN.

    Done on read so existing projects (created before the field existed) get
    descriptions without re-running the agent. Imported lazily to avoid a
    circular import at module load.
    """
    from app.services.library import get_part

    for comp in ir.components:
        if comp.description:
            continue
        part = get_part(comp.mpn)
        if part and getattr(part, "description", None):
            comp.description = part.description


def _row_to_project(row: ProjectRow) -> Project:
    ir = SchematicIR.model_validate(row.ir or {})
    _hydrate_descriptions(ir)
    return Project(
        id=row.id,
        name=row.name,
        path=row.path,
        kind=getattr(row, "kind", "hardware") or "hardware",  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        thumbnailUrl=row.thumbnail_url,
        layers=row.layers,
        componentCount=row.component_count,
        drcErrorCount=row.drc_error_count,
        updatedAt=row.updated_at.isoformat() if row.updated_at else _now_iso(),
        ir=ir,
        bom=row.bom or [],
        issues=row.issues or [],
        chat=row.chat or [],
        board=Board.model_validate(row.board) if row.board else None,
        firmware=Firmware.model_validate(getattr(row, "firmware", None))
        if getattr(row, "firmware", None)
        else None,
        shareToken=getattr(row, "share_token", None),
        mechanical=MechanicalIR.model_validate(getattr(row, "mechanical", None))
        if getattr(row, "mechanical", None)
        else None,
        buildGuide=BuildGuide.model_validate(getattr(row, "build_guide", None))
        if getattr(row, "build_guide", None)
        else None,
    )


def _project_kwargs(project: Project, user_id: str | None = None) -> dict:
    kwargs = {
        "id": project.id,
        "name": project.name,
        "path": project.path,
        "kind": project.kind,
        "status": project.status,
        "thumbnail_url": project.thumbnailUrl,
        "layers": project.layers,
        "component_count": len(project.ir.components),
        "drc_error_count": project.drcErrorCount,
        "ir": project.ir.model_dump(),
        "bom": [b.model_dump() if hasattr(b, "model_dump") else b for b in project.bom],
        "issues": [
            i.model_dump() if hasattr(i, "model_dump") else i for i in project.issues
        ],
        "chat": [
            c.model_dump() if hasattr(c, "model_dump") else c for c in project.chat
        ],
        "board": project.board.model_dump() if project.board else None,
        "firmware": project.firmware.model_dump() if project.firmware else None,
        "mechanical": project.mechanical.model_dump() if project.mechanical else None,
        "build_guide": project.buildGuide.model_dump() if project.buildGuide else None,
        "updated_at": datetime.now(timezone.utc),
    }
    if user_id is not None:
        kwargs["user_id"] = user_id
    return kwargs


# ---- public API -----------------------------------------------------------


def list_projects(user_id: str | None = None) -> list[Project]:
    with SessionLocal() as session:
        stmt = select(ProjectRow).order_by(ProjectRow.updated_at.desc())
        if user_id is not None:
            stmt = stmt.where(ProjectRow.user_id == user_id)
        rows = session.execute(stmt).scalars().all()
        return [_row_to_project(r) for r in rows]


def get_project(project_id: str, user_id: str | None = None) -> Project | None:
    with SessionLocal() as session:
        row = session.get(ProjectRow, project_id)
        if row is None:
            return None
        if user_id is not None and row.user_id != user_id:
            # Hide projects owned by other users — present as not found rather
            # than a 403 to avoid leaking ID existence.
            return None
        return _row_to_project(row)


def create_project(name: str, kind: str = "hardware", user_id: str | None = None) -> Project:
    base_id = _slugify(name) or uuid.uuid4().hex[:8]
    with _lock, SessionLocal() as session:
        project_id = base_id
        if session.get(ProjectRow, project_id) is not None:
            project_id = f"{base_id}-{uuid.uuid4().hex[:4]}"

        project = Project(
            id=project_id,
            name=name,
            kind=kind,  # type: ignore[arg-type]
            path=f"/usr/projects/{project_id}",
            status="draft",
            componentCount=0,
            ir=SchematicIR(),
            updatedAt=_now_iso(),
        )
        session.add(ProjectRow(**_project_kwargs(project, user_id=user_id)))
        session.commit()
        return project


def upsert(project: Project) -> Project:
    project.componentCount = len(project.ir.components)
    project.updatedAt = _now_iso()
    kwargs = _project_kwargs(project)
    with _lock, SessionLocal() as session:
        row = session.get(ProjectRow, project.id)
        if row is None:
            session.add(ProjectRow(**kwargs))
        else:
            for key, value in kwargs.items():
                setattr(row, key, value)
        session.commit()
        return project


def set_share_token(project_id: str, user_id: str, token: str | None) -> Project | None:
    """Create (token) or revoke (None) the read-only share link. Owner only —
    a mismatched user reads as "not found", the same as get_project."""
    with _lock, SessionLocal() as session:
        row = session.get(ProjectRow, project_id)
        if row is None or row.user_id != user_id:
            return None
        row.share_token = token
        session.commit()
        return _row_to_project(row)


def get_shared_project(project_id: str, token: str) -> Project | None:
    """The project IF `token` is its live share token. Review #39 (Flaw 3):
    the Share button copied the editor URL, which is owner-scoped, so every
    recipient got a blank page. This is the read path a recipient uses."""
    if not token:
        return None
    with SessionLocal() as session:
        row = session.get(ProjectRow, project_id)
        if row is None or not row.share_token or row.share_token != token:
            return None
        project = _row_to_project(row)
        project.shareToken = None  # never hand the token itself to a viewer
        return project


def delete_project(project_id: str, user_id: str | None = None) -> bool:
    """Permanently delete a project and its commit history.

    A 2026-08-20 reviewer found there was no way to remove a project at all —
    every test build they made was permanent, with no statement of what happens
    to the design data. Ownership is enforced here as well as at the endpoint
    (a mismatched user_id reads as "not found", the same as `get_project`), so
    no caller can delete someone else's board by id.
    """
    from app.models_db import ProjectCommitRow

    with _lock, SessionLocal() as session:
        row = session.get(ProjectRow, project_id)
        if row is None:
            return False
        if user_id is not None and row.user_id != user_id:
            return False
        # Commits are a separate table with no cascade — delete them, or the
        # design's whole revision history outlives the "deleted" project.
        for commit in session.scalars(
            select(ProjectCommitRow).where(ProjectCommitRow.project_id == project_id)
        ):
            session.delete(commit)
        session.delete(row)
        session.commit()
        return True


def append_message(project_id: str, message: ChatMessage) -> None:
    with _lock, SessionLocal() as session:
        row = session.get(ProjectRow, project_id)
        if row is None:
            raise KeyError(project_id)
        # JSON columns require a fresh list assignment for change detection.
        chat = list(row.chat or [])
        chat.append(message.model_dump())
        row.chat = chat
        row.updated_at = datetime.now(timezone.utc)
        session.commit()


def seed_demo_projects() -> None:
    """Seed the four demo projects on first launch. No-op if any project exists."""
    with SessionLocal() as session:
        existing = session.execute(select(ProjectRow).limit(1)).scalar_one_or_none()
        if existing is not None:
            return
    for name in ("Apollo-11 Core", "Smart Home Hub", "Power Supply v2", "Sensor Array MK4"):
        create_project(name)
