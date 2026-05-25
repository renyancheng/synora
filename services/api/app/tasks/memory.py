from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.domains.memory.service import MemoryService
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.memory.write_user_memory")
def write_user_memory(
    *,
    user_id: int,
    source_kind: str,
    source_ref_id: str | None,
    text: str,
    summary: str = "",
) -> int:
    db: Session = SessionLocal()
    try:
        service = MemoryService()
        entries = service.extract_memory_facts(text=text, summary=summary)
        records = service.upsert_memory_records(
            db,
            user_id=user_id,
            source_kind=source_kind,
            source_ref_id=source_ref_id,
            entries=entries,
        )
        return len(records)
    finally:
        db.close()

