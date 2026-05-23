from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.approval.service import consume_approval_request, create_approval_request
from app.models import QuickNote
from app.runtime.agent_runtime_stub import suggest_note_tags
from app.security import sha256_text


def build_note_hash(content: str, tags: list[str]) -> str:
    normalized = f"{content.strip()}|{'|'.join(sorted(tags))}"
    return sha256_text(normalized)


def preview_note_save(db: Session, user_id: int, content: str, tags: list[str]) -> tuple[list[str], str, datetime, str]:
    preview_tags = suggest_note_tags(content, tags)
    draft_hash = build_note_hash(content, preview_tags)
    approval, token = create_approval_request(
        db,
        user_id,
        "record_quick_note",
        {"content": content, "tags": preview_tags},
        draft_hash,
    )
    return preview_tags, token, approval.expires_at, draft_hash


def save_note_after_approval(db: Session, user_id: int, content: str, tags: list[str], approval_token: str) -> QuickNote:
    preview_tags = suggest_note_tags(content, tags)
    draft_hash = build_note_hash(content, preview_tags)
    consume_approval_request(db, user_id, "record_quick_note", approval_token, draft_hash)
    note = QuickNote(
        user_id=user_id,
        content=content.strip(),
        tags_csv=",".join(preview_tags),
        source_text=content.strip(),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def list_notes(db: Session, user_id: int) -> list[QuickNote]:
    return db.scalars(select(QuickNote).where(QuickNote.user_id == user_id).order_by(QuickNote.created_at.desc())).all()
