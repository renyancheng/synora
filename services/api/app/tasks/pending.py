"""主动推进：草稿超时追问 + 跨天意图唤醒。

与 ``tasks/notifications.py`` 相同的 collect → delay → handle 模式：
- ``scan_pending_draft_timeouts``（beat 每 5 分钟）：扫描长期未确认的挂起草稿，
  为每个超时草稿派发 ``handle_draft_timeout`` 生成口语化追问并写系统通知。
- ``scan_pending_intents``（beat 每 1 分钟）：扫描已到唤醒时间的跨天意图，
  派发 ``handle_cross_day_intent`` 主动跟进（单次触发）。

防打扰：nudge 次数上限与冷却（``meta_json.nudge_count`` / ``last_nudge_at``），
由 handle 任务执行前判断；用户确认/取消走 ``_clear_pending_state`` 删除行。

核心逻辑抽成 ``*_core`` 函数接收显式 db（便于测试注入内存库），celery task 包装器
负责 ``SessionLocal`` 生命周期。service 层的 ``_append_message`` /
``queue_notification_audit`` 在函数内延迟 import，避免
``conversation.service → tasks.memory → celery_app → pending → service`` 循环依赖。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.models import ConversationPendingState, NotificationAudit
from app.tasks.celery_app import celery_app

_NUDGE_SYSTEM_PROMPT = (
    "你是 Synora 日程助手的主动提醒。用户有一份日程草稿长期未确认。"
    "生成一句口语化的追问（不超过60字），提及草稿的标题与时间，"
    "询问用户是继续确认还是先放着。只输出追问本身，不要任何解释、前缀或引号。"
)
_CROSS_DAY_SYSTEM_PROMPT = (
    "你是 Synora 日程助手。用户昨天说好改天再处理一份日程草稿，现在时间到了。"
    "生成一句口语化的跟进（不超过60字），自然唤起对话，只提标题，"
    "询问是否继续安排。只输出跟进本身，不要任何解释、前缀或引号。"
)


def _nudge_allowed(meta_json: dict) -> tuple[bool, int]:
    """防打扰判断：nudge 次数上限 + 冷却。返回 (是否允许, 本次应写入的计数)。"""
    settings = get_settings()
    count = int(meta_json.get("nudge_count", 0) or 0)
    if count >= settings.pending_nudge_max:
        return False, count
    last_raw = meta_json.get("last_nudge_at")
    if last_raw:
        try:
            last_dt = datetime.fromisoformat(str(last_raw))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last_dt < timedelta(hours=settings.pending_nudge_cooldown_hours):
                return False, count
        except ValueError:
            pass
    return True, count + 1


def _write_system_notification(db: Session, *, user_id: int, subject: str, body: str) -> NotificationAudit:
    """写一条已送达的 system 审计（前端轮询消费 + FCM 可选增强）。"""
    from app.domains.notification.service import queue_notification_audit

    audit = queue_notification_audit(
        db,
        user_id=user_id,
        reminder_job_id=None,
        channel="system",
        provider="system",
        subject=subject,
        recipient="system",
        payload={"title": subject, "body": body},
    )
    audit.status = "delivered"
    audit.delivered_at = datetime.now(timezone.utc)
    audit.external_id = f"system-{audit.id}"
    db.commit()
    db.refresh(audit)
    return audit


def _fallback_followup(title: str, *, cross_day: bool) -> str:
    if title:
        prefix = "昨天说的" if cross_day else "还记得"
        return f"{prefix}「{title}」吗？要确认安排或先放着，都可以告诉我。"
    return "上次的日程草稿还没确认，要现在安排吗？"


def _generate_followup(*, title: str, start: str, cross_day: bool) -> str:
    from app.agent.llm import invoke_text

    settings = get_settings()
    try:
        text = invoke_text(
            settings,
            system_prompt=_CROSS_DAY_SYSTEM_PROMPT if cross_day else _NUDGE_SYSTEM_PROMPT,
            user_text=f"草稿标题：{title or '（未命名）'}\n草稿时间：{start or '（未指定）'}",
            operation="pending_cross_day" if cross_day else "pending_nudge",
        ).strip()
    except Exception:  # noqa: BLE001 - LLM 不可用时降级为模板文案
        return _fallback_followup(title, cross_day=cross_day)
    if not text:
        return _fallback_followup(title, cross_day=cross_day)
    if len(text) > 60:
        text = f"{text[:60]}…"
    return text


# --------------------------------------------------------------------------- #
# Core（接收显式 db，便于测试）
# --------------------------------------------------------------------------- #


def collect_stale_draft_timeouts(db: Session) -> list[ConversationPendingState]:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.pending_draft_timeout_hours)
    return db.scalars(
        select(ConversationPendingState).where(
            ConversationPendingState.stage.in_(["needs_input", "approval_pending"]),
            ConversationPendingState.updated_at < cutoff,
        )
    ).all()


def collect_due_cross_day_intents(db: Session) -> list[ConversationPendingState]:
    now = datetime.now(timezone.utc)
    return db.scalars(
        select(ConversationPendingState).where(
            ConversationPendingState.intent_type == "cross_day",
            ConversationPendingState.planned_at.is_not(None),
            ConversationPendingState.planned_at <= now,
        )
    ).all()


def handle_draft_timeout_core(db: Session, pending_id: int) -> str:
    from app.domains.conversation.service import _append_message

    pending = db.get(ConversationPendingState, pending_id)
    if not pending:
        return "missing"
    thread = pending.conversation
    if not thread:
        return "no-thread"
    allowed, next_count = _nudge_allowed(dict(pending.meta_json or {}))
    if not allowed:
        return "snoozed"

    draft = dict(pending.payload_json or {})
    title = str(draft.get("title") or draft.get("event_title") or "").strip()
    start = str(draft.get("start_at") or draft.get("scheduled_at") or "").strip()
    nudge = _generate_followup(title=title, start=start, cross_day=False)

    _append_message(
        db,
        thread,
        role="assistant",
        message_type="text",
        status="completed",
        text_content=nudge,
        structured_payload={"source": "pending_nudge", "pending_id": pending.id},
    )
    _write_system_notification(db, user_id=pending.user_id, subject="待确认的日程草稿", body=nudge)

    meta = dict(pending.meta_json or {})
    meta["nudge_count"] = next_count
    meta["last_nudge_at"] = datetime.now(timezone.utc).isoformat()
    pending.meta_json = meta
    db.commit()
    return "ok"


def handle_cross_day_intent_core(db: Session, pending_id: int) -> str:
    from app.domains.conversation.service import _append_message

    pending = db.get(ConversationPendingState, pending_id)
    if not pending:
        return "missing"
    meta = dict(pending.meta_json or {})
    if meta.get("intent_triggered") is True:
        return "already-triggered"
    thread = pending.conversation
    if not thread:
        return "no-thread"

    draft = dict(pending.payload_json or {})
    title = str(draft.get("title") or draft.get("event_title") or "").strip()
    start = str(draft.get("start_at") or draft.get("scheduled_at") or "").strip()
    follow = _generate_followup(title=title, start=start, cross_day=True)

    _append_message(
        db,
        thread,
        role="assistant",
        message_type="text",
        status="completed",
        text_content=follow,
        structured_payload={"source": "cross_day_followup", "pending_id": pending.id},
    )
    _write_system_notification(db, user_id=pending.user_id, subject="昨天的安排还没完成", body=follow)

    meta["intent_triggered"] = True
    pending.meta_json = meta
    db.commit()
    return "ok"


# --------------------------------------------------------------------------- #
# Celery tasks（SessionLocal 生命周期）
# --------------------------------------------------------------------------- #


@celery_app.task(name="app.tasks.pending.scan_pending_draft_timeouts")
def scan_pending_draft_timeouts() -> int:
    db = SessionLocal()
    try:
        rows = collect_stale_draft_timeouts(db)
        for pending in rows:
            handle_draft_timeout.delay(pending.id)
        return len(rows)
    finally:
        db.close()


@celery_app.task(name="app.tasks.pending.handle_draft_timeout")
def handle_draft_timeout(pending_id: int) -> str:
    db = SessionLocal()
    try:
        return handle_draft_timeout_core(db, pending_id)
    except Exception as exc:  # noqa: BLE001 - 后台任务兜底
        return f"failed:{exc}"
    finally:
        db.close()


@celery_app.task(name="app.tasks.pending.scan_pending_intents")
def scan_pending_intents() -> int:
    db = SessionLocal()
    try:
        rows = collect_due_cross_day_intents(db)
        triggered = 0
        for pending in rows:
            meta = dict(pending.meta_json or {})
            if meta.get("intent_triggered") is True:
                continue
            handle_cross_day_intent.delay(pending.id)
            triggered += 1
        return triggered
    finally:
        db.close()


@celery_app.task(name="app.tasks.pending.handle_cross_day_intent")
def handle_cross_day_intent(pending_id: int) -> str:
    db = SessionLocal()
    try:
        return handle_cross_day_intent_core(db, pending_id)
    except Exception as exc:  # noqa: BLE001 - 后台任务兜底
        return f"failed:{exc}"
    finally:
        db.close()
