from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.quick_note.service import create_quick_note_draft
from app.domains.schedule.service import create_schedule_draft
from app.models import User
from app.runtime.errors import LLMServiceError
from app.runtime.model_adapter import ModelAdapter
from app.schemas.agent import AgentSessionIntakeRequest, AgentSessionIntakeResponse
from app.schemas.quick_note import QuickNoteDraftRequest
from app.schemas.schedule import ScheduleDraftInput

router = APIRouter(prefix="/agent/sessions", tags=["agent_sessions"])


@router.post("/intake", response_model=AgentSessionIntakeResponse)
def intake(
    payload: AgentSessionIntakeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentSessionIntakeResponse:
    payload_data = payload.model_dump(mode="json")
    try:
        workflow = ModelAdapter().route_workflow(payload_data)
        if workflow == "schedule_intake":
            draft, draft_hash, missing_fields, ambiguity_flags, evidence_digest, parse_confidence = create_schedule_draft(
                db,
                current_user.id,
                ScheduleDraftInput(
                    text_content=payload.text_content,
                    attachment_ids=payload.attachment_ids,
                    context=payload.context,
                ),
            )
            result = {
                "draft": draft.model_dump(mode="json", by_alias=True),
                "draft_hash": draft_hash,
                "missing_fields": missing_fields,
                "ambiguity_flags": ambiguity_flags,
                "evidence_digest": evidence_digest,
                "parse_confidence": parse_confidence,
            }
        else:
            normalized_content, preview_tags, approval_token, evidence_digest, approval = create_quick_note_draft(
                db,
                current_user.id,
                QuickNoteDraftRequest(
                    content=payload.text_content,
                    tags=[],
                    attachment_ids=payload.attachment_ids,
                    context=payload.context,
                ),
            )
            result = {
                "normalized_content": normalized_content,
                "preview_tags": preview_tags,
                "attachment_ids": payload.attachment_ids,
                "evidence_digest": evidence_digest,
                "approval": {
                    "approval_token": approval_token,
                    "action": approval.action,
                    "expires_at": approval.expires_at.isoformat(),
                    "draft_hash": approval.draft_hash,
                },
            }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message) from exc
    return AgentSessionIntakeResponse(workflow=workflow, result=result)
