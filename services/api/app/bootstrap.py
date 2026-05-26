from __future__ import annotations

from datetime import timedelta

from sqlalchemy import inspect, text

from app.db import Base, SessionLocal, engine
from app.domains.auth.service import ensure_bootstrap_user
from app.models import Schedule
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
    if not str(engine.url).startswith("sqlite"):
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as exc:
            print(f"Synora startup warning: pgvector unavailable, memory retrieval will be degraded. ({exc})")

    _ensure_column("schedules", "start_at", "TIMESTAMP NULL")
    _ensure_column("schedules", "end_at", "TIMESTAMP NULL")
    _ensure_column("schedules", "time_zone", "VARCHAR(80) NOT NULL DEFAULT 'Asia/Shanghai'")
    _ensure_column("schedules", "is_all_day", "BOOLEAN NOT NULL DEFAULT FALSE")
    _ensure_column("schedules", "recurrence_rules_json", "JSON NOT NULL DEFAULT '[]'")
    _ensure_column("schedules", "reminder_offsets_minutes_json", "JSON NOT NULL DEFAULT '[]'")
    _ensure_column("schedules", "source_attachment_ids", "JSON NOT NULL DEFAULT '[]'")
    _ensure_column("schedules", "parse_confidence", "DOUBLE PRECISION NOT NULL DEFAULT 0.0")
    _ensure_column("schedules", "source_type", "VARCHAR(40) NOT NULL DEFAULT 'attachment'")

    _ensure_column("quick_notes", "source_attachment_ids", "JSON NOT NULL DEFAULT '[]'")
    _ensure_column("quick_notes", "topic_tags_json", "JSON NOT NULL DEFAULT '[]'")
    _ensure_column("quick_notes", "source_type", "VARCHAR(40) NOT NULL DEFAULT 'attachment'")

    _ensure_column("approval_requests", "normalized_payload_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column("approval_requests", "evidence_digest_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column("approval_requests", "approval_scope", "VARCHAR(120)")

    _ensure_column("notification_audits", "provider", "VARCHAR(80) NOT NULL DEFAULT ''")
    _ensure_column("notification_audits", "retry_count", "INTEGER NOT NULL DEFAULT 0")

    _ensure_column("conversation_messages", "status", "VARCHAR(30) NOT NULL DEFAULT 'completed'")
    _ensure_column("conversation_messages", "action_group_id", "VARCHAR(64)")
    _ensure_column("conversation_messages", "revision", "INTEGER NOT NULL DEFAULT 1")

    _ensure_column("agent_runs", "conversation_id", "INTEGER")
    _ensure_column("agent_runs", "user_message_id", "INTEGER")
    _ensure_column("agent_runs", "assistant_message_id", "INTEGER")
    _ensure_column("agent_runs", "stream_token", "VARCHAR(64)")
    _ensure_column("agent_runs", "stream_status", "VARCHAR(40) NOT NULL DEFAULT 'pending'")

    db = SessionLocal()
    try:
        rows = db.query(Schedule).all()
        for row in rows:
            if row.start_at is None and row.scheduled_at is not None:
                row.start_at = row.scheduled_at
            if row.end_at is None and row.start_at is not None:
                row.end_at = row.start_at + timedelta(minutes=row.duration_minutes or 60)

            offsets = [int(item) for item in (row.reminder_offsets_minutes_json or [])]
            if not offsets and row.start_at and row.reminder_at:
                delta = row.reminder_at - row.start_at
                offsets = [int(delta.total_seconds() // 60)]
            if not offsets:
                offsets = [-1440]
            row.reminder_offsets_minutes_json = offsets
        db.commit()
    finally:
        db.close()


def init_db() -> None:
    _reconcile_legacy_schema()
    try:
        get_object_storage().ensure_bucket_exists()
    except Exception as exc:
        print(f"Synora startup warning: object storage unavailable, attachments may fail until MinIO is ready. ({exc})")
    db = SessionLocal()
    try:
        ensure_bootstrap_user(db)
    finally:
        db.close()
