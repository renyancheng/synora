from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models import User
from app.schemas.user_preferences import UserPreferencesResponse, UserPreferencesUpdateRequest

router = APIRouter(prefix="/users/me", tags=["users"])


@router.get("/preferences", response_model=UserPreferencesResponse)
def get_preferences(
    current_user: User = Depends(get_current_user),
) -> UserPreferencesResponse:
    # 通知已收敛为仅 system，无用户可配置的通知偏好。
    return UserPreferencesResponse()


@router.patch("/preferences", response_model=UserPreferencesResponse)
def update_preferences(
    payload: UserPreferencesUpdateRequest,
    current_user: User = Depends(get_current_user),
) -> UserPreferencesResponse:
    return UserPreferencesResponse()
