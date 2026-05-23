from __future__ import annotations

from sqlalchemy import inspect, text

from app.db import Base, SessionLocal, engine
from app.domains.auth.service import ensure_bootstrap_user
from app.storage import get_object_storage


def _ensure_column(table_name: str, column_name: str, ddl: str) -> None:
    inspector = inspect(engine)
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in existing:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


def _reconcile_legacy_schema() -> None:
    Base.metadata.create_all(bind=engine)

    _ensure_column("schedules", "source_type", "VARCHAR(40) NOT NULL DEFAULT 'text'")
    _ensure_column("schedules", "source_attachment_ids", "JSON NOT NULL DEFAULT '[]'")
    _ensure_column("schedules", "parse_confidence", "DOUBLE PRECISION NOT NULL DEFAULT 0.0")

    _ensure_column("quick_notes", "source_type", "VARCHAR(40) NOT NULL DEFAULT 'text'")
    _ensure_column("quick_notes", "source_attachment_ids", "JSON NOT NULL DEFAULT '[]'")
    _ensure_column("quick_notes", "topic_tags_json", "JSON NOT NULL DEFAULT '[]'")

    _ensure_column("approval_requests", "normalized_payload_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column("approval_requests", "evidence_digest_json", "TEXT NOT NULL DEFAULT '[]'")

    _ensure_column("notification_audits", "provider", "VARCHAR(80) NOT NULL DEFAULT ''")
    _ensure_column("notification_audits", "retry_count", "INTEGER NOT NULL DEFAULT 0")


def init_db() -> None:
    _reconcile_legacy_schema()
    get_object_storage().ensure_bucket_exists()
    db = SessionLocal()
    try:
        ensure_bootstrap_user(db)
    finally:
        db.close()
