"""SQLAlchemy ORM models. Mirrors the Pydantic models in app/models.py.

Whole-project storage with JSON columns for the nested structures. We're
prioritizing simplicity over normalized schema — the schematic IR, BOM, issues,
and chat log are read/written as a unit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Nullable: OAuth-only accounts (Google) have no password. Email/password
    # accounts always set this. verify_password() treats a null/empty hash as
    # a guaranteed mismatch, so a Google user can't be logged into via password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Google's stable subject identifier (`sub` claim). Set when an account is
    # created or linked via Google sign-in; null for password-only accounts.
    google_sub: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    avatar_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    policy_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    privacy_policy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    terms_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Grants access to the admin portal. DB-managed: admins can promote/demote
    # other users at runtime. A bootstrap set (see db._seed_admins) guarantees
    # the founding admin is always True even before this column existed.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Bring-your-own-model (Prompt to Product, 2026-09). provider "synqit" (or
    # NULL) = the server's own key/model. The key is Fernet-encrypted with a
    # SESSION_SECRET-derived key (services/llm_config.py) and never sent back
    # to the browser. All nullable: db._add_missing_columns adds them on boot.
    llm_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    llm_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    llm_api_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    llm_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Nullable so the self-healing migration can add the column to existing
    # rows. Backfilled to a placeholder user on startup; new projects always
    # get a real user_id from the API layer.
    user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(30), default="hardware")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    thumbnail_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    layers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    component_count: Mapped[int] = mapped_column(Integer, default=0)
    drc_error_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Read-only share link (review #39, Flaw 3). NULL = not shared. The token
    # is the only thing that grants a non-owner read access; it is never
    # written by `upsert`, only by `set_share_token`, so a routine save can
    # neither leak nor revoke a share.
    share_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # JSON-encoded structures. SQLAlchemy needs a fresh list/dict assignment to
    # detect mutations on JSON columns — never mutate in-place; always reassign.
    ir: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    bom: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    chat: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    board: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    firmware: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    # Mechanical IR (Week 3 / task 5.1) — enclosure, outline, holes, cutouts.
    # Auto-added on boot by db._add_missing_columns (ADD COLUMN-only migration).
    mechanical: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    # Build guide (Prompt to Product, 2026-09) — same ADD COLUMN-only migration.
    build_guide: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class SearchEventRow(Base):
    """A single user search/query, for product analytics. Currently captures AI
    build prompts; `source` lets us add other surfaces (part search, etc.) later.
    `user_email` is stamped server-side from the session, never sent by the
    client. `result_count` is null when not applicable (e.g. build prompts)."""

    __tablename__ = "search_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_email: Mapped[str] = mapped_column(String(320), index=True)
    query: Mapped[str] = mapped_column(String(4000))
    source: Mapped[str] = mapped_column(String(40), index=True, default="build_prompt")
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_search_user_created", "user_email", "created_at"),
    )


class FeedbackRow(Base):
    """A product feedback submission from one authenticated user."""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("users.id"), nullable=False, index=True
    )
    product_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    ui_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    schematic_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    pcb_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    performance_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    review: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_feedback_user_created", "user_id", "created_at"),
    )


class EventRow(Base):
    """A single product-analytics event — one user touchpoint. Captures page
    views, clicks on interactive elements, and named product actions (login,
    project create, AI prompt, …).

    `user_email` is stamped server-side from the session and is null for
    anonymous (pre-login) events; `anon_id` is a client-generated id that lets
    us stitch an anonymous session to the account it later logs into. `props`
    is free-form JSON so the client can attach whatever context an event needs
    without a schema migration."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    anon_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # e.g. "page_view" | "click" | "login" | "project_create" | "ai_prompt"
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    props: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    referrer: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_events_type_created", "event_type", "created_at"),
        Index("ix_events_user_created", "user_email", "created_at"),
    )


class ProjectCommitRow(Base):
    """Versioned snapshot of a project. Every mutation (agent edit, accepted
    diff, property change, auto-place/route) creates one of these. Foundation
    for undo, sharing, and branching."""

    __tablename__ = "project_commits"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"))
    parent_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("project_commits.id"), nullable=True
    )
    message: Mapped[str] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(40))  # "agent" | "user" | "system"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Full project snapshot at this commit (Project.model_dump()).
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_commits_project_created", "project_id", "created_at"),
    )


# --- The studio's own data (2026-09) ------------------------------------------
# The studio used to keep its conversations, messages and uploads in a hosted
# Supabase project — a second database, a second account system and a second
# place for a board's history to live. These three tables replace it, so the
# whole product runs on one box with one database.


class StudioConversationRow(Base):
    """One chat in the studio. `settings` carries suggestions and anything else
    the UI keeps per conversation; `current_message_leaf_id` is the tip of the
    branch being viewed, since messages form a tree rather than a list."""

    __tablename__ = "studio_conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="New Conversation")
    type: Mapped[str] = mapped_column(String(32), default="parametric")
    privacy: Mapped[str] = mapped_column(String(16), default="private")
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    current_message_leaf_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("ix_studio_conv_user_updated", "user_id", "updated_at"),)


class StudioMessageRow(Base):
    """One turn. `parts` is the AI SDK's message-parts array — text, tool calls,
    tool output — stored whole, because that is how it is read back."""

    __tablename__ = "studio_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("studio_conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))
    parts: Mapped[list[Any]] = mapped_column(JSON, default=list)
    content: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    parent_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rating: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (Index("ix_studio_msg_conv_created", "conversation_id", "created_at"),)


class StudioFileRow(Base):
    """An upload or a generated artifact — an attached image, a mesh, a preview
    render. The bytes sit on the engine's data volume; this row is the index.

    Not S3: the volume is EBS-backed and already the home of every project, and
    a bucket would mean new credentials on the box for no durability gain at
    this size. Worth revisiting when the total gets large — the only thing that
    would change is `storage_path`."""

    __tablename__ = "studio_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="image")  # image | mesh | preview
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(500))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
