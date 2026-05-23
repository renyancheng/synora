from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models import ApprovalRequest, User
from app.schemas.approval import ApprovalItem

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalItem])
def list_approvals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ApprovalItem]:
    rows = db.scalars(
        select(ApprovalRequest).where(ApprovalRequest.user_id == current_user.id).order_by(ApprovalRequest.created_at.desc())
    ).all()
    return [
        ApprovalItem(
            id=row.id,
            action=row.action,
            draft_hash=row.draft_hash,
            status=row.status,
            expires_at=row.expires_at,
            created_at=row.created_at,
            confirmed_at=row.confirmed_at,
        )
        for row in rows
    ]
