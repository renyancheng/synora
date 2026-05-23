from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SessionState, User
from app.security import future_utc, hash_password, mint_token, sha256_text, verify_password


def ensure_bootstrap_user(db: Session) -> User:
    settings = get_settings()
    user = db.scalar(select(User).where(User.email == settings.bootstrap_email))
    if user:
        updated = False
        if user.display_name != settings.bootstrap_display_name:
            user.display_name = settings.bootstrap_display_name
            updated = True
        if not verify_password(settings.bootstrap_password, user.password_hash):
            user.password_hash = hash_password(settings.bootstrap_password)
            updated = True
        if updated:
            db.commit()
            db.refresh(user)
        return user

    user = User(
        email=settings.bootstrap_email,
        display_name=settings.bootstrap_display_name,
        password_hash=hash_password(settings.bootstrap_password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login_user(db: Session, email: str, password: str) -> tuple[User, str, datetime]:
    ensure_bootstrap_user(db)
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if not user or not verify_password(password, user.password_hash):
        raise ValueError("邮箱或密码错误。")

    expires_at = future_utc(get_settings().session_ttl_hours)
    token = mint_token()
    db.execute(delete(SessionState).where(SessionState.expires_at < datetime.now(timezone.utc)))
    db.add(SessionState(user_id=user.id, token_hash=sha256_text(token), expires_at=expires_at))
    db.commit()
    return user, token, expires_at


def resolve_user_by_token(db: Session, access_token: str) -> User | None:
    token_hash = sha256_text(access_token)
    session = db.scalar(
        select(SessionState).where(
            SessionState.token_hash == token_hash,
            SessionState.expires_at > datetime.now(timezone.utc),
        )
    )
    if not session:
        return None
    session.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return db.get(User, session.user_id)
