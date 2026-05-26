from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.memory.service import MemoryService
from app.models import QuickNote
from app.runtime.approval_gate import ApprovalGate
from app.runtime.tool_impls import prepare_quick_note_draft
from app.schemas.quick_note import QuickNoteDraftRequest
from app.security import sha256_text


def build_note_hash(content: str, tags: list[str], attachment_ids: list[int]) -> str:
    normalized = {
        "content": content.strip(),
        "tags": sorted(tags),
        "attachment_ids": sorted(attachment_ids),
    }
    return sha256_text(str(normalized))


def create_quick_note_draft(
    db: Session,
    user_id: int,
    payload: QuickNoteDraftRequest,
) -> tuple[str, list[str], str, list[str], object]:
    result = prepare_quick_note_draft(
        db=db,
        user_id=user_id,
        text_content=payload.content,
        content=payload.content,
        tags=payload.tags,
        attachment_ids=payload.attachment_ids,
        context=payload.context,
    )
    normalized_content = str(result["normalized_content"]).strip()
    preview_tags = list(result["preview_tags"])
    evidence_digest = list(result.get("evidence_digest", []))
    draft_hash = build_note_hash(normalized_content, preview_tags, payload.attachment_ids)
    approval, token = ApprovalGate().create(
        db,
        user_id=user_id,
        action="create_quick_note",
        draft_hash=draft_hash,
        payload={"content": normalized_content, "tags": preview_tags},
        normalized_payload={
            "content": normalized_content,
            "tags": preview_tags,
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
    attachment_ids: list[int],
    approval_token: str,
) -> QuickNote:
    draft_hash = build_note_hash(content, tags, attachment_ids)
    ApprovalGate().consume(
        db,
        user_id=user_id,
        action="create_quick_note",
        approval_token=approval_token,
        draft_hash=draft_hash,
    )
    note = QuickNote(
        user_id=user_id,
        content=content.strip(),
        tags_csv=",".join(tags),
        source_text=content.strip(),
        source_type="mixed",
        source_attachment_ids=attachment_ids,
        topic_tags_json=tags,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def list_notes(db: Session, user_id: int) -> list[QuickNote]:
    return db.scalars(select(QuickNote).where(QuickNote.user_id == user_id).order_by(QuickNote.created_at.desc())).all()


def update_note(
    db: Session,
    user_id: int,
    *,
    note_id: int,
    content: str,
    tags: list[str],
) -> QuickNote:
    note = db.scalar(select(QuickNote).where(QuickNote.id == note_id, QuickNote.user_id == user_id))
    if not note:
        raise ValueError("速记不存在或已被删除。")
    normalized_content = content.strip()
    if not normalized_content:
        raise ValueError("速记内容不能为空。")
    normalized_tags = [tag.strip() for tag in tags if tag.strip()]
    note.content = normalized_content
    note.tags_csv = ",".join(normalized_tags)
    note.topic_tags_json = normalized_tags
    note.source_text = normalized_content
    db.commit()
    db.refresh(note)
    MemoryService().delete_records_by_source(
        db,
        user_id=user_id,
        source_kind="confirmed_quick_note",
        source_ref_id=str(note.id),
    )
    MemoryService().upsert_memory_records(
        db,
        user_id=user_id,
        source_kind="confirmed_quick_note",
        source_ref_id=str(note.id),
        entries=[
            {
                "memory_type": "confirmed_quick_note",
                "title": normalized_content[:60] or "已确认速记",
                "content": normalized_content,
                "summary": "已确认速记",
            }
        ],
    )
    return note


def delete_note(db: Session, user_id: int, note_id: int) -> None:
    note = db.scalar(select(QuickNote).where(QuickNote.id == note_id, QuickNote.user_id == user_id))
    if not note:
        raise ValueError("速记不存在或已被删除。")
    db.delete(note)
    db.commit()
