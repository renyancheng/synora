"""LangGraph 图状态定义。

状态字段均为可 JSON 序列化的基本类型，便于 checkpointer 持久化。
节点仅通过状态中的稳定 ID 重载数据库资源；checkpoint 和 config 均不保存
活跃 ORM Session/实例。
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
    anti_repeat_used: bool  # 防粘滞护栏：重复回答已触发过一次重跑
    anti_empty_retries: int  # 空回答护栏：已重跑次数（最多 2 次，随后兜底收口）
    anti_commitment_used: bool  # 承诺话术护栏：只承诺未执行工具已触发过一次
    # 跨迭代累积的消息（AIMessage / ToolMessage 序列化 dict），用 add reducer 追加
    agent_messages: Annotated[list[dict[str, Any]], add]
    pending_tool_calls: list[dict[str, Any]]
    observation: str
    tool_failed: bool
    reflection: str
    current_aimessage: dict[str, Any] | None
    # 推理轨迹步骤（[{seq, step_type, label, content, status, iteration}]），add reducer 追加
    reasoning_steps: Annotated[list[dict[str, Any]], add]

    # 输出（finalize 归一化）
    assistant_text: str
    created_message_ids: list[int]
    requires_approval: dict[str, Any] | None
