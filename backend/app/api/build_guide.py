"""Build guide endpoints (Prompt to Product, 2026-09): read, generate, export.

Mirrors api/firmware.py: generation is user-triggered, persists on the
project, and lands as a revertable history commit.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.models import BuildGuide, Project
from app.models_db import UserRow
from app.services import build_guide as bg_service
from app.services import history, llm_config, store
from app.services.auth import current_user

router = APIRouter()


def _require(project_id: str, user_id: str) -> Project:
    project = store.get_project(project_id, user_id=user_id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    return project


@router.get("/projects/{project_id}/build-guide", response_model=BuildGuide | None)
def get_build_guide(project_id: str, user: UserRow = Depends(current_user)) -> BuildGuide | None:
    return _require(project_id, user.id).buildGuide


@router.post("/projects/{project_id}/build-guide/generate", response_model=Project)
def post_generate(project_id: str, user: UserRow = Depends(current_user)) -> Project:
    project = _require(project_id, user.id)
    if not project.ir.components:
        raise HTTPException(400, "Schematic is empty — design the board first.")
    guide = bg_service.generate(project, llm=llm_config.runtime_for_user(user))
    project.buildGuide = guide
    saved = store.upsert(project)
    history.commit(
        saved,
        message=f"Generated build guide ({len(guide.steps)} steps)",
        author="agent",
    )
    return saved


@router.delete("/projects/{project_id}/build-guide", response_model=Project)
def delete_build_guide(project_id: str, user: UserRow = Depends(current_user)) -> Project:
    project = _require(project_id, user.id)
    project.buildGuide = None
    return store.upsert(project)


@router.get("/projects/{project_id}/export/build-guide")
def export_build_guide(project_id: str, user: UserRow = Depends(current_user)) -> Response:
    project = _require(project_id, user.id)
    if not project.ir.components:
        raise HTTPException(409, "Schematic is empty — nothing to build yet.")
    md = bg_service.to_markdown(project)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in project.name) or "board"
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}-build-guide.md"'},
    )
