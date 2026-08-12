"""设备推送令牌注册：前端拿到 FCM token 后上报，用于 system 通知推送。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models import DeviceToken, User

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegisterRequest(BaseModel):
    token: str
    platform: str = "unknown"


class DeviceUnregisterRequest(BaseModel):
    token: str


class DeviceStatusResponse(BaseModel):
    status: str = "ok"


@router.post("/register", response_model=DeviceStatusResponse)
def register_device(
    payload: DeviceRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeviceStatusResponse:
    token = payload.token.strip()
    if not token:
        return DeviceStatusResponse(status="invalid_token")
    existing = db.scalar(select(DeviceToken).where(DeviceToken.token == token))
    if existing is not None:
        existing.user_id = current_user.id
        existing.platform = payload.platform
        db.commit()
        return DeviceStatusResponse()
    db.add(DeviceToken(user_id=current_user.id, token=token, platform=payload.platform))
    db.commit()
    return DeviceStatusResponse()


@router.post("/unregister", response_model=DeviceStatusResponse)
def unregister_device(
    payload: DeviceUnregisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeviceStatusResponse:
    existing = db.scalar(select(DeviceToken).where(DeviceToken.token == payload.token.strip()))
    if existing is not None and existing.user_id == current_user.id:
        db.delete(existing)
        db.commit()
    return DeviceStatusResponse()
