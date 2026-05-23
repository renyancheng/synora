from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.quick_note.service import list_notes, preview_note_save, save_note_after_approval
from app.models import User
from app.schemas.quick_note import QuickNoteItem, QuickNotePreviewResponse, QuickNoteRequest, QuickNoteSavedResponse

router = APIRouter(prefix="/quick-notes", tags=["quick_notes"])


@router.post("", response_model=QuickNotePreviewResponse | QuickNoteSavedResponse)
def create_or_confirm_quick_note(
    payload: QuickNoteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.approval_token:
        try:
            note = save_note_after_approval(db, current_user.id, payload.content, payload.tags, payload.approval_token)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return QuickNoteSavedResponse(note_id=note.id, topic_tags=[tag for tag in note.tags_csv.split(",") if tag])

    preview_tags, token, expires_at, draft_hash = preview_note_save(db, current_user.id, payload.content, payload.tags)
    return QuickNotePreviewResponse(
        preview_tags=preview_tags,
        approval={
            "approval_token": token,
            "action": "record_quick_note",
            "expires_at": expires_at,
            "draft_hash": draft_hash,
        },
    )


@router.get("", response_model=list[QuickNoteItem])
def get_quick_notes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[QuickNoteItem]:
    rows = list_notes(db, current_user.id)
    return [QuickNoteItem(id=row.id, content=row.content, tags=[tag for tag in row.tags_csv.split(",") if tag], created_at=row.created_at) for row in rows]
