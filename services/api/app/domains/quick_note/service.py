from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import QuickNote
from app.runtime import get_runtime_executor
from app.runtime.approval_gate import ApprovalGate
from app.schemas.quick_note import QuickNoteDraftRequest
from app.security import sha256_text


def build_note_hash(content: str, tags: list[str], source_type: str, attachment_ids: list[int]) -> str:
    normalized = {
        "content": content.strip(),
        "tags": sorted(tags),
        "source_type": source_type,
        "attachment_ids": sorted(attachment_ids),
    }
    return sha256_text(str(normalized))


def create_quick_note_draft(
    db: Session,
    user_id: int,
    payload: QuickNoteDraftRequest,
) -> tuple[str, list[str], str, list[str], object]:
    result = get_runtime_executor().execute_workflow(
        db,
        user_id=user_id,
        workflow="quick_note_intake",
        payload={
            "source_type": payload.source_type,
            "text_content": payload.content,
            "content": payload.content,
            "tags": payload.tags,
            "attachment_ids": payload.attachment_ids,
            "context": payload.context,
        },
    )
    normalized_content = str(result["normalized_content"]).strip()
    preview_tags = list(result["preview_tags"])
    evidence_digest = list(result.get("evidence_digest", []))
    draft_hash = build_note_hash(normalized_content, preview_tags, payload.source_type, payload.attachment_ids)
    approval, token = ApprovalGate().create(
        db,
        user_id=user_id,
        action="record_quick_note",
        draft_hash=draft_hash,
        payload={"content": normalized_content, "tags": preview_tags},
        normalized_payload={
            "content": normalized_content,
            "tags": preview_tags,
            "source_type": payload.source_type,
            "attachment_ids": payload.attachment_ids,
        },
        evidence_digest=evidence_digest,
    )
    return normalized_content, preview_tags, token, evidence_digest, approval


def save_note_after_approval(
    db: Session,
    user_id: int,
    *,
    content: str,
    tags: list[str],
    source_type: str,
    attachment_ids: list[int],
    approval_token: str,
) -> QuickNote:
    draft_hash = build_note_hash(content, tags, source_type, attachment_ids)
    ApprovalGate().consume(
        db,
        user_id=user_id,
        action="record_quick_note",
        approval_token=approval_token,
        draft_hash=draft_hash,
    )
    note = QuickNote(
        user_id=user_id,
        content=content.strip(),
        tags_csv=",".join(tags),
        source_text=content.strip(),
        source_type=source_type,
        source_attachment_ids=attachment_ids,
        topic_tags_json=tags,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def list_notes(db: Session, user_id: int) -> list[QuickNote]:
    return db.scalars(select(QuickNote).where(QuickNote.user_id == user_id).order_by(QuickNote.created_at.desc())).all()
