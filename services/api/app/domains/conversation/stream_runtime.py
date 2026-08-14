"""会话流共享运行时能力。

为 legacy 与 LangGraph 两条执行路径提供共用的公开服务：
- 流取消（进程内事件 + 数据库持久化状态双轨）
- 工具调用审计（AgentToolCallAudit 生命周期）
- 推理步骤 SSE 事件与摘要
- 消息追加 / 消息载荷序列化
- 流式文本落库（追加语义，SSE 增量即时写出）
- 记忆写回投递

本模块只依赖 models / security / tasks 等基础设施，不反向依赖
conversation 领域内其它模块，避免拆分后形成循环导入。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import AgentRun, AgentToolCallAudit, ConversationMessage, ConversationThread
from app.security import mint_token
from app.tasks.memory import write_user_memory

logger = logging.getLogger(__name__)

# --- 流中断（发送中停止）基础设施 ------------------------------------------
# 进程内注册表：stream_id -> asyncio.Event。POST /streams/{id}/abort set 对应
# Event；各流式检查点轮询该 Event，命中即抛 AgentRunCancelled 让 SSE 以
# run_cancelled 收口。abort 先于流注册到达时 Event 尚不存在，但流启动后会在
# 首个检查点检测到（is_stream_cancelled 幂等判断），无需在 abort 端预创建。
_CANCELLED_STREAMS: dict[str, asyncio.Event] = {}
_CANCELLATION_DB_CHECKED_AT: dict[str, float] = {}
_CANCELLATION_DB_CHECK_INTERVAL_SECONDS = 0.2

# 流式文本批量落库窗口：达到字符数或时间窗口才 commit，SSE 增量不受影响。
# 完成 / 取消 / 失败路径必须调用 flush() 强制落库未提交缓冲。
TEXT_FLUSH_CHARS = 80
TEXT_FLUSH_INTERVAL_SECONDS = 0.5


class MessageTextBuffer:
    """消息文本批量落库缓冲。

    模型每个 token/delta 只更新 ORM 对象内存态，SSE 增量照常即时写出；
    满足字符数或时间窗口条件才 ``db.commit()``，避免逐 token 同步写库阻塞
    事件循环。事务边界：

    - 消息文本：本缓冲按窗口提交，结束时由调用方 flush() 强制刷新；
    - AgentRun 状态：由编排层（consume_stream / finalize）一次性提交；
    - 卡片与审批副作用：由 intake / pending 服务在各自函数内原子提交。
    """

    def __init__(
        self,
        db: Session,
        assistant_message: ConversationMessage,
        *,
        flush_chars: int = TEXT_FLUSH_CHARS,
        flush_interval_seconds: float = TEXT_FLUSH_INTERVAL_SECONDS,
    ) -> None:
        if flush_chars <= 0:
            raise ValueError("flush_chars 必须为正数")
        self._db = db
        self._message = assistant_message
        self._flush_chars = flush_chars
        self._flush_interval_seconds = flush_interval_seconds
        self._pending_chars = 0
        self._last_flush_monotonic = time.monotonic()
        self._dirty = False
        self._force_flush = False

    def append(self, delta: str) -> None:
        self._message.text_content = (self._message.text_content or "") + delta
        self._pending_chars += len(delta)
        self._dirty = True

    def set_text(self, text: str) -> None:
        """整体覆盖文本（例如 astream_events 的 on_chain_end 全量尾文本），强制下次检查即落库。"""
        self._message.text_content = text
        self._dirty = True
        self._force_flush = True

    def needs_flush(self) -> bool:
        if not self._dirty:
            return False
        if self._force_flush:
            return True
        return self._pending_chars >= self._flush_chars or (
            time.monotonic() - self._last_flush_monotonic >= self._flush_interval_seconds
        )

    def flush(self) -> None:
        if not self._dirty:
            return
        self._db.commit()
        self._pending_chars = 0
        self._last_flush_monotonic = time.monotonic()
        self._dirty = False
        self._force_flush = False

    @property
    def text(self) -> str:
        return self._message.text_content or ""

    @property
    def pending_chars(self) -> int:
        return self._pending_chars

    @property
    def dirty(self) -> bool:
        return self._dirty


class AgentRunCancelled(Exception):
    """用户主动中断当前 agent 运行（发送中停止）。"""

    def __init__(self, stream_id: str) -> None:
        super().__init__(f"stream {stream_id} cancelled by user")
        self.stream_id = stream_id


def is_stream_cancelled(stream_id: str | None) -> bool:
    return bool(stream_id) and stream_id in _CANCELLED_STREAMS and _CANCELLED_STREAMS[stream_id].is_set()


def clear_stream_cancelled(stream_id: str | None) -> None:
    if stream_id:
        _CANCELLED_STREAMS.pop(stream_id, None)
        _CANCELLATION_DB_CHECKED_AT.pop(stream_id, None)


def is_stream_cancellation_requested(db: Session, stream_id: str | None) -> bool:
    """查询持久化取消状态，避免复用流 Session 中已缓存的 AgentRun。"""
    if not stream_id:
        return False
    return db.scalar(select(AgentRun.stream_status).where(AgentRun.stream_token == stream_id)) == "cancelling"


def raise_if_stream_cancelled(
    db: Session,
    stream_id: str | None,
    *,
    force_database_check: bool = False,
) -> None:
    """本地事件为快路径，数据库状态为跨实例和重启后的事实来源。"""
    if not stream_id:
        return
    if is_stream_cancelled(stream_id):
        raise AgentRunCancelled(stream_id)
    now = time.monotonic()
    last_checked = _CANCELLATION_DB_CHECKED_AT.get(stream_id, 0.0)
    if force_database_check or now - last_checked >= _CANCELLATION_DB_CHECK_INTERVAL_SECONDS:
        _CANCELLATION_DB_CHECKED_AT[stream_id] = now
        if is_stream_cancellation_requested(db, stream_id):
            raise AgentRunCancelled(stream_id)


def abort_stream(db: Session, *, user_id: int, conversation_id: int, stream_id: str) -> bool:
    """持久化取消请求并通知本 worker；返回是否命中当前用户的可取消流。"""
    result = db.execute(
        update(AgentRun)
        .where(
            AgentRun.user_id == user_id,
            AgentRun.conversation_id == conversation_id,
            AgentRun.stream_token == stream_id,
            AgentRun.stream_status.in_(("pending", "active")),
        )
        .values(stream_status="cancelling")
    )
    db.commit()
    if result.rowcount != 1:
        return False
    _CANCELLED_STREAMS.setdefault(stream_id, asyncio.Event()).set()
    return True


def enqueue_memory_writeback(
    *,
    user_id: int,
    source_kind: str,
    source_ref_id: str | None,
    text: str,
    summary: str = '',
    agent_run_id: int | None = None,
    conversation_id: int | None = None,
) -> None:
    try:
        write_user_memory.delay(
            user_id=user_id,
            source_kind=source_kind,
            source_ref_id=source_ref_id,
            text=text,
            summary=summary,
        )
    except Exception:
        # 降级路径：记忆投递失败不阻断主流程，结构化记录（不含消息正文）。
        logger.warning(
            "memory_writeback_enqueue_failed run_id=%s conversation_id=%s operation=memory_writeback source_kind=%s reason=broker_unreachable",
            agent_run_id,
            conversation_id,
            source_kind,
            exc_info=True,
        )


def build_reasoning_step_event(assistant_message_id: int, step: dict[str, Any]) -> dict[str, Any]:
    """把持久化 step dict 包装成 SSE reasoning_step 事件。"""
    return {"event": "reasoning_step", "data": {"assistant_message_id": assistant_message_id, **step}}


def summarize_reasoning_steps(steps: list[dict[str, Any]]) -> str:
    """把步骤序列压成一行摘要（reasoning_step 卡片的 text_content）。"""
    parts: list[str] = []
    for step in steps:
        label = step.get("label") or step.get("step_type") or ""
        content = str(step.get("content") or "").strip()
        if not content or content in ("（无文本输出）", "本轮无工具调用"):
            continue
        parts.append(f"{label}: {content}" if label else content)
    summary = " → ".join(parts)
    return summary[:160] or "推理过程"


def append_message(
    db: Session,
    thread: ConversationThread,
    *,
    role: str,
    message_type: str,
    status: str,
    text_content: str | None,
    structured_payload: dict,
    action_group_id: str | None = None,
    revision: int = 1,
) -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=thread.id,
        role=role,
        message_type=message_type,
        status=status,
        text_content=text_content,
        structured_payload_json=structured_payload,
        action_group_id=action_group_id,
        revision=revision,
    )
    thread.last_message_at = datetime.now(timezone.utc)
    db.add(message)
    db.commit()
    db.refresh(message)
    db.refresh(thread)
    return message


def message_payload(message: ConversationMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "message_type": message.message_type,
        "status": message.status,
        "text_content": message.text_content,
        "structured_payload": dict(message.structured_payload_json or {}),
        "action_group_id": message.action_group_id,
        "revision": message.revision,
        "created_at": message.created_at.isoformat(),
    }


def start_tool_audit(
    db: Session,
    *,
    agent_run_id: int,
    tool_name: str,
    request_json: dict[str, Any],
) -> AgentToolCallAudit:
    audit = AgentToolCallAudit(
        agent_run_id=agent_run_id,
        tool_name=tool_name,
        request_json=request_json,
        response_json={},
        status="running",
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def finish_tool_audit(
    db: Session,
    audit: AgentToolCallAudit | None,
    *,
    status: str,
    response_json: dict[str, Any],
    error_message: str | None = None,
) -> None:
    if audit is None:
        return
    audit.status = status
    audit.response_json = response_json
    audit.error_message = error_message
    db.commit()


def serialize_any(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): serialize_any(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [serialize_any(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return str(value)


async def emit_text_stream(
    db: Session,
    assistant_message: ConversationMessage,
    text: str,
    *,
    stream_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    # 追加语义：以已有 text_content 为种子，支持 intake 节点先 emit 前置说明文字
    # （感知说明）再输出最终文案，避免覆盖式重算把前置文字丢掉。
    buffer = MessageTextBuffer(db, assistant_message)
    try:
        for index in range(0, len(text), 12):
            raise_if_stream_cancelled(db, stream_id)
            chunk = text[index : index + 12]
            buffer.append(chunk)
            if buffer.needs_flush():
                buffer.flush()
            yield {"event": "message_delta", "data": {"assistant_message_id": assistant_message.id, "delta": chunk}}
    finally:
        # 完成 / 取消 / 失败统一强制刷新，避免 DB 文本落后于客户端已显示文本。
        buffer.flush()
    assistant_message.status = "completed"
    db.commit()
