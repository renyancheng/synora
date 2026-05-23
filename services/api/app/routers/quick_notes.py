from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.quick_note.service import create_quick_note_draft, list_notes, save_note_after_approval
from app.models import User
from app.schemas.quick_note import (
    QuickNoteConfirmRequest,
    QuickNoteDraftRequest,
    QuickNoteDraftResponse,
    QuickNoteItem,
    QuickNoteSavedResponse,
)

router = APIRouter(prefix="/quick-notes", tags=["quick_notes"])


@router.post("/drafts", response_model=QuickNoteDraftResponse)
def create_quick_note_draft_endpoint(
    payload: QuickNoteDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> QuickNoteDraftResponse:
    try:
        normalized_content, preview_tags, token, evidence_digest, approval = create_quick_note_draft(db, current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return QuickNoteDraftResponse(
        normalized_content=normalized_content,
        preview_tags=preview_tags,
        source_type=payload.source_type,
        attachment_ids=payload.attachment_ids,
        evidence_digest=evidence_digest,
        approval={
            "approval_token": token,
            "action": "record_quick_note",
            "expires_at": approval.expires_at,
            "draft_hash": approval.draft_hash,
        },
    )


@router.post("/confirm", response_model=QuickNoteSavedResponse)
def confirm_quick_note(
    payload: QuickNoteConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> QuickNoteSavedResponse:
    try:
        note = save_note_after_approval(
            db,
            current_user.id,
            content=payload.content,
            tags=payload.tags,
            source_type=payload.source_type,
            attachment_ids=payload.attachment_ids,
            approval_token=payload.approval_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return QuickNoteSavedResponse(note_id=note.id, topic_tags=list(note.topic_tags_json))


@router.get("", response_model=list[QuickNoteItem])
def get_quick_notes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[QuickNoteItem]:
    rows = list_notes(db, current_user.id)
    return [
        QuickNoteItem(
            id=row.id,
            content=row.content,
            tags=[tag for tag in row.tags_csv.split(",") if tag],
            created_at=row.created_at,
            source_type=row.source_type,
        )
        for row in rows
    ]
