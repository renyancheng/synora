from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.user_preferences import UserPreferencesResponse, UserPreferencesUpdateRequest

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("/preferences", response_model=UserPreferencesResponse)
def get_preferences(
    current_user: User = Depends(get_current_user),
) -> UserPreferencesResponse:
    return UserPreferencesResponse(
        wecom_robot_webhook=(current_user.wecom_robot_webhook or "").strip() or None,
    )


@router.patch("/preferences", response_model=UserPreferencesResponse)
def update_preferences(
    payload: UserPreferencesUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserPreferencesResponse:
    webhook = (payload.wecom_robot_webhook or "").strip()
    if webhook and not (webhook.startswith("http://") or webhook.startswith("https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook 必须是有效的 http 或 https 地址。")
    current_user.wecom_robot_webhook = webhook or None
    db.commit()
    db.refresh(current_user)
    return UserPreferencesResponse(
        wecom_robot_webhook=(current_user.wecom_robot_webhook or "").strip() or None,
    )
