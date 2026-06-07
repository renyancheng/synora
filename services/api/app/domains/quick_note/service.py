from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.memory.service import MemoryService
from app.domains.search.service import SemanticSearchService
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


def normalize_tags(tags: list[str]) -> list[str]:
    unique: list[str] = []
    for raw in tags:
        tag = raw.strip()
        if tag and tag not in unique:
            unique.append(tag)
    return unique


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
    preview_tags = normalize_tags(list(result["preview_tags"]))
    evidence_digest = list(result.get("evidence_digest", []))
    draft_hash = build_note_hash(normalized_content, preview_tags, payload.attachment_ids)
    approval_scope = str(payload.context.get("approval_scope") or "").strip() or None
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
        approval_scope=approval_scope,
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
    normalized_tags = normalize_tags(tags)
    draft_hash = build_note_hash(content, normalized_tags, attachment_ids)
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
        tags_csv=",".join(normalized_tags),
        source_text=content.strip(),
        source_type="mixed",
        source_attachment_ids=attachment_ids,
        topic_tags_json=normalized_tags,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    SemanticSearchService().upsert_quick_note(note)
    return note


def list_notes(db: Session, user_id: int, *, tag: str | None = None, query: str | None = None) -> list[QuickNote]:
    normalized_tag = (tag or "").strip()
    cleaned_query = (query or "").strip()
    if cleaned_query:
        rows = _search_notes_semantic(db, user_id=user_id, query=cleaned_query, tag=normalized_tag)
        if rows:
            return rows
        return _search_notes_fallback(db, user_id=user_id, query=cleaned_query, tag=normalized_tag)

    rows = db.scalars(select(QuickNote).where(QuickNote.user_id == user_id).order_by(QuickNote.created_at.desc())).all()
    if not normalized_tag:
        return rows
    return [row for row in rows if normalized_tag in list(row.topic_tags_json or [])]


def list_note_tags(db: Session, user_id: int) -> list[dict[str, int | str]]:
    rows = db.scalars(select(QuickNote).where(QuickNote.user_id == user_id)).all()
    counter: Counter[str] = Counter()
    for row in rows:
        counter.update(normalize_tags(list(row.topic_tags_json or [])))
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


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
    normalized_tags = normalize_tags(tags)
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
    SemanticSearchService().upsert_quick_note(note)
    return note


def delete_note(db: Session, user_id: int, note_id: int) -> None:
    note = db.scalar(select(QuickNote).where(QuickNote.id == note_id, QuickNote.user_id == user_id))
    if not note:
        raise ValueError("速记不存在或已被删除。")
    db.delete(note)
    db.commit()
    SemanticSearchService().delete_quick_note(user_id=user_id, note_id=note_id)


def _search_notes_semantic(db: Session, *, user_id: int, query: str, tag: str | None = None) -> list[QuickNote]:
    note_ids = SemanticSearchService().search_quick_note_ids(user_id=user_id, query_text=query)
    if not note_ids:
        return []
    rows = db.scalars(select(QuickNote).where(QuickNote.user_id == user_id, QuickNote.id.in_(note_ids))).all()
    by_id = {row.id: row for row in rows}
    ordered_rows = [by_id[note_id] for note_id in note_ids if note_id in by_id]
    normalized_tag = (tag or "").strip()
    if normalized_tag:
        ordered_rows = [row for row in ordered_rows if normalized_tag in list(row.topic_tags_json or [])]
    return ordered_rows


def _search_notes_fallback(db: Session, *, user_id: int, query: str, tag: str | None = None) -> list[QuickNote]:
    normalized_tag = (tag or "").strip()
    rows = db.scalars(select(QuickNote).where(QuickNote.user_id == user_id)).all()
    ranked: list[tuple[int, QuickNote]] = []
    for row in rows:
        if normalized_tag and normalized_tag not in list(row.topic_tags_json or []):
            continue
        score = _note_keyword_score(row, query)
        if score > 0:
            ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], item[1].created_at, item[1].id), reverse=False)
    return [item[1] for item in ranked]


def _note_keyword_score(note: QuickNote, query: str) -> int:
    terms = _query_terms(query)
    if not terms:
        return 0
    score = 0
    fields = [
        (note.content or "", 6),
        (" ".join(list(note.topic_tags_json or [])), 4),
        (note.source_text or "", 2),
    ]
    for term in terms:
        for raw_value, weight in fields:
            value = raw_value.lower()
            if term in value:
                score += max(1, value.count(term)) * weight
    return score


def _query_terms(query: str) -> list[str]:
    cleaned = query.strip().lower()
    if not cleaned:
        return []
    terms = [cleaned]
    for part in cleaned.split():
        if part and part not in terms:
            terms.append(part)
    return terms
