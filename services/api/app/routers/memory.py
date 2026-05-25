from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.domains.memory.service import MemoryService
from app.models import User
from app.schemas.common import ApiEnvelope
from app.schemas.memory import MemoryItem, MemoryListResponse

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=MemoryListResponse)
def list_memory(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MemoryListResponse:
    summary, items = MemoryService().list_records(db, user_id=current_user.id)
    return MemoryListResponse(
        summary=summary,
        items=[
            MemoryItem(
                id=item.id,
                memory_type=item.memory_type,
                title=item.title,
                content=item.content,
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
                is_active=item.is_active,
                updated_at=item.updated_at,
            )
            for item in items
        ],
    )


@router.delete("/{memory_id}", response_model=ApiEnvelope)
def delete_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiEnvelope:
    try:
        MemoryService().delete_record(db, user_id=current_user.id, memory_id=memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ApiEnvelope(message="记忆已删除。")


@router.post("/clear", response_model=ApiEnvelope)
def clear_memory(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiEnvelope:
    MemoryService().clear_user_memory(db, user_id=current_user.id)
    return ApiEnvelope(message="长期记忆已清空。")

