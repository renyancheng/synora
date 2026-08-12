"""Agent 运行时遗留模块。

历史自研编排（Planner / Executor / ToolRegistry / PolicyGuard / AgentRuntimeStub）
已由 LangGraph 编排层（app.agent）替代并删除。当前仅保留被领域服务直接引用的
LLM 封装、工具实现、上下文拼装、输出规范化与审批门等组件。
"""
