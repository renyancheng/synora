from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models import NotificationAudit, User
from app.schemas.notification import NotificationItem

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationItem])
def list_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[NotificationItem]:
    rows = db.scalars(
        select(NotificationAudit).where(NotificationAudit.user_id == current_user.id).order_by(NotificationAudit.created_at.desc())
    ).all()
    return [
        NotificationItem(
            id=row.id,
            channel=row.channel,
            provider=row.provider,
            recipient=row.recipient,
            subject=row.subject,
            status=row.status,
            error_message=row.error_message,
            retry_count=row.retry_count,
            created_at=row.created_at,
            delivered_at=row.delivered_at,
        )
        for row in rows
    ]
