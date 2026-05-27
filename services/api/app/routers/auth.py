from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_bearer_token, get_current_user
from app.domains.auth.service import login_user, logout_user, register_user, resolve_session_by_token
from app.models import User
from app.schemas.auth import CurrentSessionResponse, LoginRequest, LoginResponse, RegisterRequest
from app.schemas.common import ApiEnvelope

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    try:
        user, token, expires_at = login_user(db, payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user={"id": user.id, "email": user.email, "display_name": user.display_name},
    )


@router.post("/register", response_model=LoginResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> LoginResponse:
    try:
        user, token, expires_at = register_user(
            db,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user={"id": user.id, "email": user.email, "display_name": user.display_name},
    )


@router.get("/me", response_model=CurrentSessionResponse)
def me(
    access_token: str = Depends(get_bearer_token),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentSessionResponse:
    session = resolve_session_by_token(db, access_token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效或已过期。")
    return CurrentSessionResponse(
        expires_at=session.expires_at,
        user={"id": current_user.id, "email": current_user.email, "display_name": current_user.display_name},
    )


@router.post("/logout", response_model=ApiEnvelope)
def logout(
    access_token: str = Depends(get_bearer_token),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    logout_user(db, access_token)
    return ApiEnvelope(message="已退出登录。")
