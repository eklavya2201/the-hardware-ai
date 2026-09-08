"""Firmware endpoints — PRD §7.7 (Wave 2: backend generator)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models import Firmware, FirmwareFile, Project
from app.models_db import UserRow
from app.services import firmware as fw_service
from app.services import history, llm_config, store
from app.services.auth import current_user

router = APIRouter()


class FirmwareFileUpdate(BaseModel):
    path: str
    content: str


class FirmwareFileRegenerate(BaseModel):
    path: str
    instructions: str = ""


class FirmwareFeatureRequest(BaseModel):
    description: str


@router.get("/projects/{project_id}/firmware", response_model=Firmware | None)
def get_firmware(
    project_id: str, user: UserRow = Depends(current_user)
) -> Firmware | None:
    project = _require(project_id, user.id)
    return project.firmware


@router.post("/projects/{project_id}/firmware/generate", response_model=Project)
def post_generate(
    project_id: str, user: UserRow = Depends(current_user)
) -> Project:
    project = _require(project_id, user.id)
    if project.kind != "hardware-software":
        raise HTTPException(
            400,
            "This project is hardware-only. Create it as 'hardware + software' "
            "to generate firmware.",
        )
    if not project.ir.components:
        raise HTTPException(400, "Schematic is empty — generate components first.")

    try:
        fw = fw_service.generate(project, llm=llm_config.runtime_for_user(user))
    except fw_service.FirmwareGenerationError as exc:
        raise HTTPException(400, str(exc)) from exc

    project.firmware = fw
    saved = store.upsert(project)
    history.commit(
        saved,
        message=f"Generated firmware ({len(fw.files)} files, target {fw.target_mcu})",
        author="agent",
    )
    return saved


@router.put("/projects/{project_id}/firmware/files", response_model=Project)
def update_firmware_file(
    project_id: str,
    body: FirmwareFileUpdate,
    user: UserRow = Depends(current_user),
) -> Project:
    """Save edited content for a single file. Path can be new (adds the file)
    or existing (replaces content). Creates a history commit."""
    project = _require(project_id, user.id)
    if not project.firmware:
        raise HTTPException(400, "Project has no firmware to edit.")

    fw = project.firmware
    found = False
    for f in fw.files:
        if f.path == body.path:
            f.content = body.content
            found = True
            break
    if not found:
        # Path-safety: forbid traversal and absolute paths.
        if ".." in body.path.split("/") or body.path.startswith("/"):
            raise HTTPException(400, "Invalid file path.")
        fw.files.append(FirmwareFile(path=body.path, content=body.content))

    fw.generated_at = datetime.now(timezone.utc).isoformat()
    project.firmware = fw
    saved = store.upsert(project)
    history.commit(
        saved,
        message=f"Edited firmware file: {body.path}",
        author="user",
    )
    return saved


@router.post("/projects/{project_id}/firmware/files/regenerate", response_model=Project)
def regenerate_firmware_file(
    project_id: str,
    body: FirmwareFileRegenerate,
    user: UserRow = Depends(current_user),
) -> Project:
    """Re-generate the content of a single file via the LLM. Other files in
    the project are passed as read-only context."""
    project = _require(project_id, user.id)
    if not project.firmware:
        raise HTTPException(400, "Project has no firmware.")

    try:
        new_content = fw_service.regenerate_file(
            project, body.path, body.instructions, llm=llm_config.runtime_for_user(user)
        )
    except fw_service.FirmwareGenerationError as exc:
        raise HTTPException(400, str(exc)) from exc

    fw = project.firmware
    for f in fw.files:
        if f.path == body.path:
            f.content = new_content
            break
    fw.generated_at = datetime.now(timezone.utc).isoformat()
    project.firmware = fw
    saved = store.upsert(project)
    history.commit(
        saved,
        message=f"Regenerated firmware file: {body.path}",
        author="agent",
    )
    return saved


@router.post(
    "/projects/{project_id}/firmware/feature",
    response_model=Project,
)
def post_firmware_feature(
    project_id: str,
    body: FirmwareFeatureRequest,
    user: UserRow = Depends(current_user),
) -> Project:
    """Add a feature to existing firmware via a single LLM call.

    Different from regenerate-file: scope is the entire project, the LLM may
    modify multiple files OR add new ones (e.g. drop a new feature.cpp/.h
    pair plus update main.cpp's includes). Files not listed in the response
    are left untouched.
    """
    project = _require(project_id, user.id)
    if project.firmware is None:
        raise HTTPException(
            400, "Project has no firmware yet. Generate it first."
        )

    try:
        new_fw = fw_service.add_feature(project, body.description, llm=llm_config.runtime_for_user(user))
    except fw_service.FirmwareGenerationError as exc:
        raise HTTPException(400, str(exc)) from exc

    project.firmware = new_fw
    saved = store.upsert(project)
    history.commit(
        saved,
        message=f"Firmware feature: {body.description[:80]}",
        author="agent",
    )
    return saved


@router.delete("/projects/{project_id}/firmware", response_model=Project)
def delete_firmware(
    project_id: str, user: UserRow = Depends(current_user)
) -> Project:
    project = _require(project_id, user.id)
    project.firmware = None
    saved = store.upsert(project)
    history.commit(saved, message="Cleared firmware", author="user")
    return saved


def _require(project_id: str, user_id: str) -> Project:
    project = store.get_project(project_id, user_id=user_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return project
