"""The studio's own data: conversations, messages and uploaded files.

This replaces a hosted Supabase project. Everything the studio keeps now lives
in the same database as the boards it designs, behind the same session, so
there is one account system and one place a piece of work exists.

Authorisation is ownership: every row carries `user_id` and every query filters
on the caller. Someone else's row is "not found" rather than "forbidden" — a
403 would confirm the id exists.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models_db import (
    StudioConversationRow,
    StudioFileRow,
    StudioMessageRow,
    UserRow,
)
from app.services.auth import current_user

router = APIRouter(prefix="/studio", tags=["studio"])

# Uploads live beside the database on the data volume.
FILES_ROOT = Path(os.environ.get("STUDIO_FILES_DIR", "/data/studio-files"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# A file id becomes a path segment, so it must not be able to escape the
# directory it is written into.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,120}")


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


# --- conversations -----------------------------------------------------------


class ConversationOut(BaseModel):
    id: str
    user_id: str
    title: str
    type: str
    privacy: str
    settings: dict
    current_message_leaf_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def of(cls, row: StudioConversationRow) -> "ConversationOut":
        return cls(
            id=row.id,
            user_id=row.user_id,
            title=row.title,
            type=row.type,
            privacy=row.privacy,
            settings=row.settings or {},
            current_message_leaf_id=row.current_message_leaf_id,
            created_at=_iso(row.created_at),
            updated_at=_iso(row.updated_at),
        )


class ConversationCreate(BaseModel):
    id: Optional[str] = None
    title: str = "New Conversation"
    type: str = "parametric"
    privacy: str = "private"
    settings: dict = Field(default_factory=dict)


class ConversationPatch(BaseModel):
    title: Optional[str] = None
    privacy: Optional[str] = None
    settings: Optional[dict] = None
    current_message_leaf_id: Optional[str] = None


def _owned(db, conversation_id: str, user: UserRow) -> StudioConversationRow:
    row = db.get(StudioConversationRow, conversation_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "No such conversation")
    return row


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    user: UserRow = Depends(current_user),
):
    with SessionLocal() as db:
        rows = (
            db.execute(
                select(StudioConversationRow)
                .where(StudioConversationRow.user_id == user.id)
                .order_by(StudioConversationRow.updated_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [ConversationOut.of(r) for r in rows]


@router.post("/conversations", response_model=ConversationOut)
def create_conversation(body: ConversationCreate, user: UserRow = Depends(current_user)):
    with SessionLocal() as db:
        # The client mints the id so it can stream into a conversation before
        # the round trip finishes; creating it twice is not an error.
        if body.id:
            existing = db.get(StudioConversationRow, body.id)
            if existing is not None:
                if existing.user_id != user.id:
                    raise HTTPException(404, "No such conversation")
                return ConversationOut.of(existing)
        row = StudioConversationRow(
            id=body.id or uuid.uuid4().hex,
            user_id=user.id,
            title=body.title,
            type=body.type,
            privacy=body.privacy,
            settings=body.settings,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return ConversationOut.of(row)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: str, user: UserRow = Depends(current_user)):
    with SessionLocal() as db:
        return ConversationOut.of(_owned(db, conversation_id, user))


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def patch_conversation(
    conversation_id: str, body: ConversationPatch, user: UserRow = Depends(current_user)
):
    with SessionLocal() as db:
        row = _owned(db, conversation_id, user)
        if body.title is not None:
            row.title = body.title
        if body.privacy is not None:
            row.privacy = body.privacy
        if body.settings is not None:
            row.settings = body.settings
        if body.current_message_leaf_id is not None:
            row.current_message_leaf_id = body.current_message_leaf_id
        db.commit()
        db.refresh(row)
        return ConversationOut.of(row)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user: UserRow = Depends(current_user)):
    with SessionLocal() as db:
        row = _owned(db, conversation_id, user)
        db.execute(delete(StudioMessageRow).where(StudioMessageRow.conversation_id == row.id))
        db.delete(row)
        db.commit()
        return {"deleted": conversation_id}


# --- messages ----------------------------------------------------------------


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    user_id: str
    role: str
    parts: list
    content: Optional[dict] = None
    metadata: dict
    parent_message_id: Optional[str] = None
    rating: int
    created_at: Optional[str] = None

    @classmethod
    def of(cls, row: StudioMessageRow) -> "MessageOut":
        return cls(
            id=row.id,
            conversation_id=row.conversation_id,
            user_id=row.user_id,
            role=row.role,
            parts=row.parts or [],
            content=row.content,
            metadata=row.metadata_json or {},
            parent_message_id=row.parent_message_id,
            rating=row.rating,
            created_at=_iso(row.created_at),
        )


class MessageUpsert(BaseModel):
    id: Optional[str] = None
    role: str
    parts: list = Field(default_factory=list)
    content: Optional[dict] = None
    metadata: dict = Field(default_factory=dict)
    parent_message_id: Optional[str] = None
    rating: Optional[int] = None
    advance_leaf: bool = True


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(conversation_id: str, user: UserRow = Depends(current_user)):
    with SessionLocal() as db:
        _owned(db, conversation_id, user)
        rows = (
            db.execute(
                select(StudioMessageRow)
                .where(StudioMessageRow.conversation_id == conversation_id)
                .order_by(StudioMessageRow.created_at.asc())
            )
            .scalars()
            .all()
        )
        return [MessageOut.of(r) for r in rows]


@router.put("/conversations/{conversation_id}/messages", response_model=MessageOut)
def upsert_message(
    conversation_id: str, body: MessageUpsert, user: UserRow = Depends(current_user)
):
    """Upsert, because the studio writes a message and then rewrites its parts
    as tool calls settle. The same id means the same row."""
    with SessionLocal() as db:
        _owned(db, conversation_id, user)
        message_id = body.id or uuid.uuid4().hex
        row = db.get(StudioMessageRow, message_id)
        created = row is None
        if row is None:
            row = StudioMessageRow(
                id=message_id,
                conversation_id=conversation_id,
                user_id=user.id,
                role=body.role,
            )
            db.add(row)
        elif row.user_id != user.id:
            raise HTTPException(404, "No such message")
        row.parts = body.parts
        row.content = body.content
        row.metadata_json = body.metadata
        row.parent_message_id = body.parent_message_id
        if body.rating is not None:
            row.rating = body.rating
        if created and body.advance_leaf:
            # Replaces the database trigger the old store had: a new message is
            # the tip of the branch, and the chat handler walks parents back
            # from that tip to build the request. Without this the branch is
            # stuck on whatever the leaf was before.
            conversation = db.get(StudioConversationRow, conversation_id)
            if conversation is not None:
                conversation.current_message_leaf_id = row.id
        db.commit()
        db.refresh(row)
        return MessageOut.of(row)


class MessagePatch(BaseModel):
    parts: Optional[list] = None
    metadata: Optional[dict] = None
    rating: Optional[int] = None


@router.patch(
    "/conversations/{conversation_id}/messages/{message_id}", response_model=MessageOut
)
def patch_message(
    conversation_id: str,
    message_id: str,
    body: MessagePatch,
    user: UserRow = Depends(current_user),
):
    """Update an existing message, or 404.

    Deliberately not an upsert. The studio rewrites an assistant row after it
    finishes a tool call locally, and that row is written by the server when
    the stream closes — the two can race. Creating the row here would hide the
    race behind a phantom message with no place in the branch; a 404 lets the
    caller retry until the real row lands.
    """
    with SessionLocal() as db:
        _owned(db, conversation_id, user)
        row = db.get(StudioMessageRow, message_id)
        if row is None or row.user_id != user.id or row.conversation_id != conversation_id:
            raise HTTPException(404, "No such message")
        if body.parts is not None:
            row.parts = body.parts
        if body.metadata is not None:
            row.metadata_json = body.metadata
        if body.rating is not None:
            row.rating = body.rating
        db.commit()
        db.refresh(row)
        return MessageOut.of(row)


@router.delete("/conversations/{conversation_id}/messages/{message_id}")
def delete_message(conversation_id: str, message_id: str, user: UserRow = Depends(current_user)):
    with SessionLocal() as db:
        _owned(db, conversation_id, user)
        row = db.get(StudioMessageRow, message_id)
        if row is not None and row.user_id == user.id:
            db.delete(row)
            db.commit()
        return {"deleted": message_id}


# --- files -------------------------------------------------------------------


class FileOut(BaseModel):
    id: str
    kind: str
    content_type: str
    size_bytes: int
    url: str
    created_at: Optional[str] = None


@router.post("/files", response_model=FileOut)
async def upload_file(
    file: UploadFile = File(...),
    kind: str = Form(default="image"),
    conversation_id: Optional[str] = Form(default=None),
    file_id: Optional[str] = Form(default=None),
    user: UserRow = Depends(current_user),
):
    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Files are limited to 25 MB.")

    # The caller may name the file. Renders that the server has to find again
    # are keyed on the tool call that produced them, so both sides can derive
    # the same id without a round trip. Re-uploading the same id replaces it.
    if file_id:
        if not _SAFE_ID.fullmatch(file_id):
            raise HTTPException(400, "file_id may contain letters, digits, dashes and underscores.")
    else:
        file_id = uuid.uuid4().hex
    # One directory per user: a listing cannot cross accounts, and no single
    # directory grows without bound.
    folder = FILES_ROOT / user.id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_id
    path.write_bytes(payload)

    with SessionLocal() as db:
        existing = db.get(StudioFileRow, file_id)
        if existing is not None and existing.user_id != user.id:
            raise HTTPException(409, "That file id is taken.")
        if existing is not None:
            db.delete(existing)
            db.commit()
        row = StudioFileRow(
            id=file_id,
            user_id=user.id,
            conversation_id=conversation_id,
            kind=kind,
            content_type=file.content_type or "application/octet-stream",
            size_bytes=len(payload),
            storage_path=str(path),
            meta={"filename": file.filename or ""},
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return FileOut(
            id=row.id,
            kind=row.kind,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            url="/studio/files/" + row.id,
            created_at=_iso(row.created_at),
        )


@router.get("/files/{file_id}")
def read_file(file_id: str, user: UserRow = Depends(current_user)):
    with SessionLocal() as db:
        row = db.get(StudioFileRow, file_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(404, "No such file")
        path = Path(row.storage_path)
        if not path.exists():
            raise HTTPException(404, "The file is missing from storage")
        return FileResponse(
            path,
            media_type=row.content_type,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )


@router.delete("/files/{file_id}")
def delete_file(file_id: str, user: UserRow = Depends(current_user)):
    with SessionLocal() as db:
        row = db.get(StudioFileRow, file_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(404, "No such file")
        Path(row.storage_path).unlink(missing_ok=True)
        db.delete(row)
        db.commit()
        return {"deleted": file_id}


# --- profile -----------------------------------------------------------------


@router.get("/profile")
def profile(user: UserRow = Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.name,
        "avatar_url": user.avatar_url,
        "is_admin": bool(user.is_admin),
    }
