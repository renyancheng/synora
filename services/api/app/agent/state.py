"""LangGraph 图状态定义。

状态字段均为可 JSON 序列化的基本类型，便于 checkpointer 持久化。
ORM 对象（db / thread / agent_run / assistant_message）通过
``get_config()["configurable"]`` 传递，不进入图状态。
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict


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

    # 推理主循环（general_chat 专用：plan → act → observe → reflect）
    plan: str
    iteration_count: int
    max_iterations: int
    loop_decision: str  # "continue" | "done"
    follow_up_prompt: str | None
    # 跨迭代累积的消息（AIMessage / ToolMessage 序列化 dict），用 add reducer 追加
    agent_messages: Annotated[list[dict[str, Any]], add]
    pending_tool_calls: list[dict[str, Any]]
    observation: str
    reflection: str
    current_aimessage: dict[str, Any] | None
    # 推理轨迹步骤（[{seq, step_type, label, content, status, iteration}]），add reducer 追加
    reasoning_steps: Annotated[list[dict[str, Any]], add]

    # 输出（finalize 归一化）
    assistant_text: str
    created_message_ids: list[int]
    requires_approval: dict[str, Any] | None
