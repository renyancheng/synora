# Agent Loop 标准方案与 Synora 调度优化

> 调研时间：2026-04。范围：agent loop 主流模式、框架执行模型、生产化最佳实践，以及针对 Synora 现有 LangGraph 实现的调度优化方案。
> 结论先行：**当前实现的方向正确（LangGraph + 显式循环 + 护栏 + 审计），主要短板在“调度粒度”**——工具串行执行、固定轮次预算、无 token/时间预算、无并发控制、无运行级可观测性。优化优先级见文末路线图。

## 落地状态（2026-04 更新）

| 项 | 状态 | 说明 |
|---|---|---|
| P0-紧急：工具轮后强制回答轮 | ✅ 已修复 | `reflect_step` 分支重排：工具轮确定性 continue（不再误判预告文本为最终回答）；部分失败也续跑、仅全部失败收口（`tool_failed_all`）；`assistant_output` 限定为本轮 act 输出（不再用累积 text_content 兜底）。`test_conversation_service.py` 54 个测试 + `test_agent_service.py` 全部通过 |
| P0-4：reflect 降频 | ✅ 已覆盖 | 工具轮 0 次 LLM 评估（确定性续跑）；无工具轮规则短路；LLM 评估降为防御性兜底 |
| P0-2：运行级预算 | 进行中 | 总时长 / 每轮 max_tokens / 总 token 预算声明 |
| P1-1：web_search 缓存 | 进行中 | 进程内 TTL 缓存 |
| P1-3：可观测性模型列 | 进行中 | AgentRun/AgentToolCallAudit 新列 + 写入接线 |
| P0-1 / P0-3 / P1-2 | 待办 | 工具并行 / 上下文记账压缩 / 并发优先级 |

---

## 1. Agent Loop 的最新标准方案（调研结论）

### 1.1 模式谱系：从 Workflow 到 Agent

业界（Anthropic、OpenAI、AWS 等）已形成共识：先区分 **Workflow（固定链路）** 与 **Agent（模型自主决策的循环）**，并遵循“**能用 workflow 就不用 agent，能用单 agent 就不用多 agent**”的最小化原则：

| 模式 | 循环形态 | 适用场景 | 代表实现 |
|---|---|---|---|
| ReAct | thought → action → observation → 再思考，逐轮串行 | 简单问答、单工具链 | 各框架内置 `create_agent` |
| Plan-and-Execute | 先出计划 → 逐步执行 → 按需重规划 | 多步骤任务 | LangGraph planner/executor |
| **LLMCompiler** | LLM 一次性产出 **DAG**（含依赖关系），调度器按拓扑序**并行**执行无依赖任务，仅在有依赖处重新调用 LLM | 工具多、可并行、延迟敏感 | 论文 arXiv:2312.04511 |
| Reflexion | 执行后自我评估，失败注入反馈重跑（带次数上限） | 代码/推理类可重试任务 | 论文 arXiv:2303.11366 |
| 多 Agent 协调 | 见 1.3 五种模式 | 研究、并行子任务 | Anthropic / OpenAI SDK |

Synora 当前 general_chat 的 `plan → act → observe → reflect` 是 **Plan-and-Execute + Reflexion 的混合**，符合主流，无需推翻重做。

### 1.2 主流框架的 Loop 执行模型对比

| 框架 | Loop 模型 | 调度能力 | 备注 |
|---|---|---|---|
| OpenAI Agents SDK | `run` 内部 ReAct 循环 + `handoffs` 移交 | 单 agent 内工具可并行（`tool_use_behavior`）；handoff 做多 agent 路由 | 轻量，无图持久化 |
| **LangGraph** | StateGraph + 条件边 + Checkpointer（状态持久化/恢复） | 图级调度，节点天然可并行（`Send` API / 扇出）；断点续跑 | Synora 已采用，事实标准之一 |
| Claude Agent SDK | 裸 loop + context management（compaction、tool 清理） | 单 agent，上下文工程最完善 | Anthropic 推荐“先单 agent” |

对比结论（Langfuse / Turion / agentmarketcap 等 2025-2026 对比）：**多步、需持久化与审计的任务选 LangGraph；纯对话式单 agent 可用轻量 SDK；两者不应混用**。Synora 选型无需变更。

### 1.3 多 Agent 协调模式（Anthropic 官方五种）

1. **Orchestrator-Workers**：主 agent 拆任务、worker 并行执行、结果汇总——适用于研究类（Anthropic 研究系统借此将效率提升约 90%）。
2. **Routing**：按意图/领域分发给专用 agent（Synora 的 `route_intent` 已是此模式，但 worker 是固定节点而非独立 agent）。
3. **Evaluator-Optimizer**：生成 + 评估两 agent 交替（Synora 的 `reflect` 即轻量版）。
4. **Parallelization**：同一任务多路并行（多路搜索、多路草稿）。
5. **Handoffs**：上下文整体移交（OpenAI Agents SDK 风格）。

**对 Synora 的结论**：个人助理场景下，单 agent + 工具循环是第一优先；只有“研究型任务”（多路 web_search、多源资料汇总）才值得引入 Orchestrator-Workers 或 Parallelization，且应做成**任务级子循环**而不是全量改造。

### 1.4 生产化标准要素（“可上生产的 loop”）

综合 freeCodeCamp《Production-Safe Agent Loop》、Shopify Sidekick、AWS Serverless Agentic 与 Anthropic Context Engineering：

1. **硬性退出条件**：轮次上限 + 总 token 预算 + 总时长预算，三者取最先触发；不能只靠“模型说自己做完了”。
2. **护栏（Guardrails）**：空输出重试、重复回答检测、承诺话术拦截、未知工具拦截、失败工具不再重调——Synora 已全部具备（`reflect_step` 中的 anti-empty / anti-repeat / anti-commitment / tool_failed）。
3. **审计与可追溯**：每次工具调用留痕（request/response/status/error）、运行级状态机。Synora 已有 `AgentRun` + `AgentToolCallAudit`，但缺少**耗时与 token 计量**。
4. **上下文工程**：窗口裁剪、工具结果截断、compaction（长对话摘要化）、tool 清理——Synora 有 DB 窗口（12 条/LLM 8 条）与摘要裁剪，缺**运行内 token 记账与压缩**。
5. **可观测性**：OTel GenAI Semantic Conventions 的 span 追踪（LLM 调用、工具调用、循环轮次），或至少结构化日志 + 指标。
6. **缓存**：语义缓存（相似 query 命中直接复用），显著降延迟与成本。
7. **评估（Eval）**：固定评测集做回归，防止 prompt/模型升级劣化。
8. **降级路径**：每一步 LLM 调用失败都有确定性兜底——Synora 已做到（plan/reflect 降级日志 `agent_step_degraded`）。

---

## 2. Synora 现状诊断（基于代码走读）

### 2.1 已具备的强项

- LangGraph StateGraph + Checkpointer（`app/agent/graph.py`、`state.py`），状态可持久化、节点从稳定 ID 重载资源，不把 ORM 会话塞进 checkpoint。
- 显式循环 + 条件边（`reflect` → `act`/`finalize`），`agent_max_loop_iterations=4`。
- 完整护栏（空回答、重复、承诺话术、未知工具、失败收口）。
- SSE 实时流、取消传播（`raise_if_stream_cancelled`）、文本缓冲强制 flush。
- 工具白名单注入（general_chat 仅 `get_current_time` / `web_search`），防“伪草稿”。
- 确定性 plan 短路，减少无谓 LLM 调用。

### 2.2 调度层面的短板（本次优化重点）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| S1 | **工具串行执行**：`for call in tool_calls` 逐个 `await`，即使多个工具无依赖 | `observe_step`（agent_service.py:499） | 多工具轮次延迟 = 各工具延迟之和；LLMCompiler 思路下应为 DAG 并行 |
| S2 | **固定轮次上限**：4 轮一刀切，无自适应 | `config.agent_max_loop_iterations`、`_route_loop` | 简单任务浪费、复杂任务不够；且轮次与 token 无联动 |
| S3 | **无运行级预算**：只有单次 LLM 90s 超时，无总时长/总 token 上限 | `llm_timeout_seconds` | 极端 case 可长时间占用 worker 与用户等待 |
| S4 | **上下文重复重建 + 每轮记忆检索**：每轮 `act_step` 都重查 DB 最近消息并调用 `MemoryService.retrieve_context`（向量检索） | `build_general_chat_messages`（agent_service.py:212-247） | 每轮多一次向量检索与 DB 查询；多轮后 `agent_messages` 全量重发，token 膨胀无记账 |
| S5 | **reflect 固定一次 LLM 调用**：启发式短路只在少数分支命中，其余每轮都做一次结构化 LLM 评估 | `reflect_step`（agent_service.py:601-603） | 每轮 +1 次 LLM 延迟与成本（约 1~2s） |
| S6 | **无并发与速率控制**：同一用户连发多消息、多用户同时触发时无调度约束 | 流运行时（消费方） | 峰值期 LLM/搜索 API 被打爆，体验无保障 |
| S7 | **无可观测性指标**：审计有 request/response，但无每轮耗时、token 用量、成本、缓存命中率 | `AgentRun`/`AgentToolCallAudit` | 无法定位慢轮、无法做预算与容量规划 |
| S8 | **无缓存**：相同的问法/工具结果每次重算 | 全链路 | 高重复性个人助理场景浪费明显 |
| S9 | **intake 与 general_chat 无调度区分**：两者同抢一个流消费通道 | stream runtime | 卡片流程（确定性高）应优先于推理循环 |

---

## 3. Agent 调度优化方案（按优先级）

### P0-1：工具并行调度（解决 S1）——收益最大、改动最小

**方案**：`observe_step` 从串行 `for` 改为 `asyncio.gather` 并发执行，并对工具结果**按 call 顺序**回填 `ToolMessage`（顺序错位会破坏模型理解）；增加“同类工具限并发”信号量（如 web_search 最多 2 路）。

```python
# agent_service.py observe_step 改造要点
from asyncio import gather, Semaphore

async def _run_one_tool(sem, ...):
    async with sem:
        return await tool.ainvoke(args)

results = await gather(*[_run_one_tool(sem, call) for call in tool_calls],
                       return_exceptions=True)
# 按 tool_calls 原始顺序组装 tool_messages / summaries / 审计
```

**进阶（LLMCompiler 化）**：当前 act 已能从模型流式捕获**多个** tool_calls，说明模型具备多工具意图。短期无需做完整 DAG；只需：
- 同轮工具全部并行执行（无依赖场景已覆盖 90% 收益）；
- 若未来出现“第二个工具依赖第一个工具的输出”，再引入 `web_search → 汇总` 的显式子任务 DAG（LLMCompiler 模式），而不是让模型自己多轮往返。

**验收**：2 个独立 web_search 的轮次延迟接近最慢单个工具，而非两者之和；`AgentRun` 记录 `parallel_tool_rounds`。

### P0-2：运行级预算（解决 S2、S3）

在 `Settings` 增加并接入：

```python
agent_max_loop_iterations: int = 4        # 保留：轮次硬上限
agent_max_run_seconds: int = 120          # 新增：单 run 总时长预算（含流式）
agent_max_run_tokens: int = 60000         # 新增：单 run 总 token 预算（输入+输出）
```

- **时长**：`act_step` 流式循环内做 wall-clock 检查（与 `raise_if_stream_cancelled` 同位置），超时发 `run_timeout` SSE 事件并收口（复用 finalize 兜底文案）。
- **Token**：在 `llm.py` 的 `create_chat_model` 外层封装“调用记账”包装器（`astream` 每 chunk 累加 usage，dashscope 兼容模式在 final chunk 提供 `usage`），每轮结束写回 `AgentRun.step_metrics`（JSON：`{iteration, prompt_tokens, completion_tokens, latency_ms}`）。
- **自适应轮次**：`_route_loop` 增加两档：`iteration >= max_iter` 硬收口不变；token 超预算时提前收口（即使还有轮次额度），避免“回答越写越长越跑越多”。

### P0-3：上下文记账与压缩（解决 S4）

1. **每轮记忆只检索一次**：把 `memory_context` / `history_text` 结果放进 `AgentState`（如 `context["memory_payload"]`），`build_general_chat_messages` 优先用 state 缓存；首轮之外不再触发向量检索。`context` 字段已存在于 state，改动为“写入一次、读取复用”。
2. **运行内 token 记账**（承接 P0-2）：每轮 act 前预估 `agent_messages` + 窗口消息的 token 数；超过 `context_budget`（如 50k）时：
   - 先裁剪最老的 `agent_messages`（工具轮次对最终回答贡献递减）；
   - 再对超长 `ToolMessage` 内容截断（已有 `[:1200]` 式摘要，提升为统一 `summarize_tool_result`）。
3. **工具结果摘要化**：web_search 返回大 JSON 时，observe 阶段即压缩为“每源标题+摘要 2 行”再进上下文（参考 Anthropic context engineering 的 tool result 清理）。

### P0-4：reflect 降频与退出策略（解决 S5）

保留现有启发式短路，新增两条**零 LLM** 的收口规则，把 LLM 评估降为“最后一轮”才用：

1. **工具成功且已有回答文本 → 直接 done**：当前代码在 `assistant_output` 非空时已短路（agent_service.py:598-599），保持；再把“回答文本非空 + 本轮无工具调用”的判定提到最前（已是）。
2. **连续 N 轮无新信息 → 直接 done**：`iteration >= 2` 且本轮 `observation` 与上轮相同/为空时，视为无进展，跳过 LLM 评估直接收口。
3. **LLM 评估仅保留一种情形**：工具调用过、有回答文本、但模型意图继续（需要证据判断是否值得下一轮）——即当前 `else` 分支；并给评估结果加 **TTL 缓存**（同 `(user_goal, plan)` 前缀命中直接复用，见 P1-1）。

预期：general_chat 一轮问答（无工具）的路径变为 `plan → act → reflect(规则短路) → finalize`，**全程 0 次额外 LLM 调用**（现状是 1 次 reflect 评估）。

### P1-1：语义缓存（解决 S8）

- **工具结果缓存**：`web_search` 以 `(query 归一化, top_k)` 为键缓存 5~10 分钟（Redis，`ttl` 可配）；命中时 observe 直接回填，0 延迟。
- **意图路由缓存**：`aroute_conversation_intent` 对完全相同的 `text_content` 前缀（如“搜索 X”类高频模板）做短 TTL 缓存。
- **注意**：缓存键只做归一化（去空白/小写），**不做 embedding 语义匹配**（个人助理场景误命中风险 > 收益），避免引入新基础设施。

### P1-2：并发与优先级调度（解决 S6、S9）

在流运行时（`consume_stream` 消费入口）加**应用层调度**（Celery 之外的第二道闸）：

1. **每用户并发 1**：同一 `conversation_id` 已有 pending 时，新消息进等待队列（前端已展示 pending 态），防止同会话多 run 交错写同一 assistant_message。
2. **全局信号量**：`asyncio.Semaphore` 限制同时运行 agent run 数（如 8，可配），超出进 FIFO；防止 LLM/搜索 API 峰值打满。
3. **优先级**：`schedule_intake` / `quick_note_intake`（确定性卡片流程，秒级完成）> general_chat 推理循环；简单队列实现：intake 消息插队。

> 若未来 run 变长（研究型任务），再评估把“运行调度”上移给 Celery 队列（任务编排），保留 LangGraph 只管“单 run 内部调度”的边界——与 Temporal/Dagster/LangGraph 长任务编排的分层一致。

### P1-3：可观测性（解决 S7）

- **结构化日志**：每轮一次 `agent_step` 日志（`run_id, iteration, node, latency_ms, prompt_tokens, completion_tokens, tool_names`），沿用现有 `logger.warning` 的 key=value 风格。
- **AgentRun 扩展**：`step_metrics` JSON（轮次明细）+ `total_tokens` + `total_latency_ms` + `cache_hits` 计数；`AgentToolCallAudit` 增加 `latency_ms`。
- **（可选）OTel**：引入 `opentelemetry` + GenAI semantic conventions，把 LLM/工具/节点映射为 span；不加时上面的结构化日志已覆盖 80% 排障需求。

### P1-4：intake 与 general_chat 的资源边界（延续 S9）

`GENERAL_CHAT_EXCLUDED_TOOLS` 已做工具级隔离；调度层面 intake 节点**禁用工具并行**（P0-1 的 gather 仅 general_chat 分支启用），保持卡片流程确定性与审计顺序。

### P2：评估与多 Agent 演进路线

1. **Eval 基线**：沉淀 20~30 条覆盖“日程/速记 intake、单轮问答、搜索类、护栏触发（空回答/重复/承诺话术）”的回归用例（`services/api/tests` 已有大量可复用 fixture），每次改 loop/换模型跑一遍；记录 P0 指标（见下）。
2. **多 Agent 演进（仅在需求出现时）**：
   - 阶段一（现在）：Routing 已有 + Parallelization 增强（P0-1 多工具并行）；
   - 阶段二（研究型任务需求时）：Orchestrator-Workers 子图——`research` 意图进入子图，主 agent 拆 2~4 路搜索 worker（并行），汇总 agent 合成回答；worker 复用现有 `observe_step` 工具执行逻辑；
   - 阶段三（外部生态）：保持 MCP 单向暴露（已有），对外部 agent 的调用不进入本循环调度。
3. **灰度**：`agent_backend: "langgraph" | "legacy"` 已是开关；新调度参数（预算、并行开关）全部走 Settings 环境变量，按用户/比例灰度。

---

## 4. 落地路线图与度量指标

| 阶段 | 内容 | 预估改动面 | 度量 |
|---|---|---|---|
| 第 1 周 | P0-1 工具并行 + P0-2 预算（时长/token/自适应轮次） | `agent_service.py`、`config.py`、`graph.py`、`llm.py` 记账包装 | 多工具轮延迟 ↓50%；超时 run=0；平均轮数 ↓ |
| 第 2 周 | P0-3 上下文记账压缩 + P0-4 reflect 降频 | `state.py`、`agent_service.py`、`nodes.py` | 无工具问答全程 LLM 调用数 3→2（plan+act）；p95 延迟 ↓1~2s；单 run token ↓30% |
| 第 3 周 | P1-1 语义缓存 + P1-2 并发优先级 | `web_search.py`、`stream_runtime.py`、`config.py` | 搜索工具缓存命中率 ≥30%；同会话并发 run=0 |
| 第 4 周 | P1-3 可观测性 + P1-4 边界 + Eval 基线 | `models.py`、`audit`、`tests/` | 每 run 有完整 step_metrics；回归通过率 100% |
| 后续 | P2 多 Agent 子图（按需） | `graph.py` 新增子图 | 研究类任务完成率/时长 |

**北极星指标**：p50/p95 首字延迟、平均 run 耗时、单 run token 成本、工具成功率、护栏触发率（空回答/重复/承诺话术应趋近 0）。

---

## 5. 参考资料

- Anthropic《Multi-agent coordination patterns: Five approaches》— https://claude.com/blog/multi-agent-coordination-patterns
- Anthropic Orchestrator-Workers 模式 Cookbook — https://platform.claude.com/cookbook/patterns-agents-orchestrator-workers
- Anthropic《Building Production Multi-Agent Research Systems》— https://www.zenml.io/llmops-database/building-production-multi-agent-research-systems-with-claude
- Anthropic Context Engineering（memory / compaction / tool clearing）— https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
- LLMCompiler（DAG 并行工具调度，论文 arXiv:2312.04511）— https://github.com/crazyyanchao/llmcompiler
- freeCodeCamp《How to Build a Production-Safe Agent Loop: From Exit Conditions to Audit Trails》— https://www.freecodecamp.org/news/how-to-build-a-production-safe-agent-loop-from-exit-conditions-to-audit-trails/
- Shopify《Building production-ready agentic systems: Lessons from Shopify Sidekick》— https://shopify.engineering/building-production-ready-agentic-systems
- AWS re:Invent《Build, deploy, and operate agentic architectures on AWS Serverless》— https://repost.aws/articles/AR9fNVsR75Q_KYE19tA9E2HQ
- Langfuse《Comparing Open-Source AI Agent Frameworks》— https://langfuse.com/blog/2025-03-19-ai-agent-comparison
- Turion《LangGraph vs OpenAI and Claude Agent SDKs Compared》— https://turion.ai/blog/langgraph-vs-openai-claude-agent-sdk-2026/
- OTel GenAI Tracing 实践 — https://futureagi.com/blog/what-is-llm-tracing-2026/
- Temporal/Dagster/LangGraph 长任务编排模式 — https://www.kinde.com/learn/ai-for-software-engineering/ai-devops/orchestrating-multi-step-agents-temporal-dagster-langgraph-patterns-for-long-running-work/
