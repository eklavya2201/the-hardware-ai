"""SQLite persistence layer.

Single file at backend/voltron.db. SQLAlchemy 2.x with the typed Mapped[T]
pattern. Migrations: simple `Base.metadata.create_all` on startup — when we
need to evolve the schema in production, swap to Alembic.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Default to a file at backend/voltron.db. Overridable via env so tests can
# point at an in-memory DB.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "voltron.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create tables if they don't exist; add new columns if missing.
    Idempotent. For schema changes beyond ADD COLUMN, switch to Alembic."""
    # Import models so SQLAlchemy registers them on Base.metadata.
    from app import models_db  # noqa: F401
    from sqlalchemy import inspect

    Base.metadata.create_all(engine)

    # Self-healing migration: add any column present on the model but missing
    # from the live table. Runs for every table that can gain columns over
    # time. Same code works on SQLite (dev) and Postgres (Neon, prod).
    inspector = inspect(engine)
    _add_missing_columns(inspector, "projects", models_db.ProjectRow.__table__)
    _add_missing_columns(inspector, "users", models_db.UserRow.__table__)

    # The Google OAuth work relaxed users.password_hash to nullable. ADD COLUMN
    # can't express that for the pre-existing column, so drop the constraint
    # explicitly (Postgres only — SQLite doesn't enforce it the same way and
    # has no ALTER COLUMN).
    _drop_not_null(inspector, "users", "password_hash")

    # Promote the founding admin(s) so the admin portal is reachable on first
    # boot, before anyone can grant access through the UI.
    _seed_admins()

    # Wipe any pre-auth projects (and their commits). New users start clean.
    _delete_orphan_projects()


# Emails that are always admins. The founder is hard-coded so the portal can
# never be locked out; ADMIN_EMAILS (comma-separated) can add more at deploy
# time. These can't be demoted through the UI (see services/admin.py).
BOOTSTRAP_ADMIN_EMAILS = {"krishanttanti@gmail.com"}


def bootstrap_admin_emails() -> set[str]:
    extra = os.environ.get("ADMIN_EMAILS", "")
    emails = {e.strip().lower() for e in extra.split(",") if e.strip()}
    return emails | BOOTSTRAP_ADMIN_EMAILS


def _seed_admins() -> None:
    """Set is_admin=True for every bootstrap admin that already has an account.
    Idempotent; runs on every startup so a newly-added ADMIN_EMAILS entry takes
    effect the next time that user exists."""
    from app.models_db import UserRow
    from sqlalchemy import select

    targets = bootstrap_admin_emails()
    if not targets:
        return
    with SessionLocal() as session:
        users = (
            session.execute(
                select(UserRow).where(UserRow.email.in_(targets))
            )
            .scalars()
            .all()
        )
        changed = False
        for user in users:
            if not user.is_admin:
                user.is_admin = True
                changed = True
        if changed:
            session.commit()


def _add_missing_columns(inspector, table_name: str, table) -> None:
    from sqlalchemy import text

    if table_name not in inspector.get_table_names():
        return
    existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
    with engine.begin() as conn:
        for col in table.columns:
            if col.name in existing_columns:
                continue
            col_type = col.type.compile(dialect=engine.dialect)
            null_clause = "" if col.nullable else " NOT NULL"
            default_clause = ""
            if col.default is not None and col.default.arg is not None:
                arg = col.default.arg
                if not callable(arg):
                    default_clause = f" DEFAULT {arg!r}"
            # Quote the identifier — Postgres folds unquoted names to lowercase,
            # which is fine for our snake_case columns, but quoting is harmless
            # and consistent.
            conn.execute(
                text(
                    f'ALTER TABLE {table_name} ADD COLUMN "{col.name}" '
                    f"{col_type}{null_clause}{default_clause}"
                )
            )


def _drop_not_null(inspector, table_name: str, column_name: str) -> None:
    from sqlalchemy import text

    if engine.dialect.name != "postgresql":
        return
    if table_name not in inspector.get_table_names():
        return
    cols = {c["name"]: c for c in inspector.get_columns(table_name)}
    col = cols.get(column_name)
    if col is None or col.get("nullable", True):
        return
    with engine.begin() as conn:
        conn.execute(
            text(f'ALTER TABLE {table_name} ALTER COLUMN "{column_name}" DROP NOT NULL')
        )


def _delete_orphan_projects() -> None:
    from app.models_db import ProjectCommitRow, ProjectRow
    from sqlalchemy import delete, select

    with SessionLocal() as session:
        orphan_ids = (
            session.execute(
                select(ProjectRow.id).where(ProjectRow.user_id.is_(None))
            )
            .scalars()
            .all()
        )
        if not orphan_ids:
            return
        session.execute(
            delete(ProjectCommitRow).where(
                ProjectCommitRow.project_id.in_(orphan_ids)
            )
        )
        session.execute(delete(ProjectRow).where(ProjectRow.user_id.is_(None)))
        session.commit()
