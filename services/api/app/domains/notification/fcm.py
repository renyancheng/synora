"""Firebase Cloud Messaging（FCM）推送：可选的系统通知增强通道。

未配置 service account（firebase_service_account_path）时，_ensure_firebase 返回
False，send_system_push 静默跳过 —— 系统通知仍通过「前端轮询 + 本地通知」工作，
FCM 只负责「应用彻底关闭时」也能收到推送。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DeviceToken

logger = logging.getLogger(__name__)

_initialized = False
_available = False


def _ensure_firebase() -> bool:
    """懒加载 Firebase Admin SDK；结果缓存在模块级，避免重复初始化。"""
    global _initialized, _available
    if _initialized:
        return _available
    _initialized = True

    path = get_settings().firebase_service_account_path.strip()
    if not path:
        logger.info("未配置 firebase_service_account_path，FCM 推送已禁用（降级为前端轮询）。")
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(path)
        firebase_admin.initialize_app(cred)
        _available = True
    except Exception as exc:  # noqa: BLE001 - 初始化失败只降级，不让通知主流程崩溃
        logger.error("Firebase 初始化失败，FCM 推送已禁用：%s", exc)
    return _available


def send_system_push(
    db: Session,
    *,
    user_id: int,
    title: str,
    body: str,
    audit_id: int,
) -> str | None:
    """向该用户所有已注册设备推送 FCM 通知。

    返回可读错误信息；未配置 Firebase / 无设备令牌 / 发送成功均返回 None。
    """
    if not _ensure_firebase():
        return None
    tokens = db.scalars(
        select(DeviceToken.token).where(DeviceToken.user_id == user_id)
    ).all()
    if not tokens:
        return None

    try:
        from firebase_admin import messaging
    except ImportError:
        return None

    failures: list[str] = []
    for token in tokens:
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={"notification_audit_id": str(audit_id)},
                token=token,
            )
            messaging.send(message)
        except Exception as exc:  # noqa: BLE001 - 单设备失败不影响其他设备
            failures.append(f"{token[:12]}…:{exc}")
    if not failures:
        return None
    return "; ".join(failures)[:500]
