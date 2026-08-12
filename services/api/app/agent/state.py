"""LangGraph 图状态定义。

状态字段均为可 JSON 序列化的基本类型，便于 checkpointer 持久化。
ORM 对象（db / thread / agent_run / assistant_message）通过
``get_config()["configurable"]`` 传递，不进入图状态。
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # 输入（本轮不可变）
    user_id: int
    conversation_id: int
    agent_run_id: int
    assistant_message_id: int
    stream_id: str
    user_message: str
    attachment_ids: list[int]
    attachment_parts: list[dict]
    context: dict[str, Any]
    selected_tool: str | None
    conversation_history_lines: list[str]

    # 路由结果
    intent: str  # general_chat | schedule_intake | quick_note_intake

    # 输出（finalize 归一化）
    assistant_text: str
    created_message_ids: list[int]
    requires_approval: dict[str, Any] | None
