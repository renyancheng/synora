# Synora API

FastAPI 后端负责：

- 登录与会话
- 附件上传与对象存储
- OCR 解析
- Agent Runtime
- 日程草稿解析、冲突检测、审批创建
- 快速笔记预览与保存
- Celery 提醒调度
- SMTP / 企业微信机器人通知审计

## 本地开发

推荐直接使用 `deploy/dev`：

```powershell
cd D:\Projects\aaa\synora\deploy\dev
Copy-Item .env.example .env
docker compose up --build
```

## 生产部署

生产环境使用 `deploy/prod`：

```powershell
cd D:\Projects\aaa\synora\deploy\prod
Copy-Item .env.example .env
docker compose up --build
```

## 手动运行

如果要不走 Docker 手动运行：

1. 创建 Python 3.12 虚拟环境
2. 安装 `requirements.txt`
3. 准备 `services/api/.env`
4. 启动 API

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Agent 编排（LangGraph）

对话编排基于 LangGraph `StateGraph`（`app/agent/graph.py`）：意图路由 → 分支节点
（general_chat / schedule_intake / quick_note_intake / tool_selection_reminder）→ 统一 finalize。
SSE 事件契约（`run_started / message_delta / tool_call_* / message_completed / card_snapshot / approval_required / run_completed`）
与历史实现逐字节兼容。

可通过环境变量回滚到旧编排：

- `SYNORA_AGENT_BACKEND`：`langgraph`（默认）| `legacy`

## MCP servers

本服务进程内暴露 FastMCP server（`app/runtime/mcp/server.py`），Agent 通过
`langchain-mcp-adapters` 进程内调用。可通过 `SYNORA_MCP_SERVERS` 以 JSON 数组聚合外部 MCP server，
其工具会自动并入 Agent 工具集；单个 server 配置失败仅记日志并跳过，不阻断主流程：

```dotenv
# Streamable HTTP server（如 remote-mcp / npx -y @modelcontextprotocol/server-filesystem）
SYNORA_MCP_SERVERS=[{"name":"files","transport":"streamable_http","url":"http://localhost:9000/mcp","headers":{"Authorization":"Bearer <token>"},"timeout_seconds":30}]

# stdio server（本地进程）
SYNORA_MCP_SERVERS=[{"name":"files","transport":"stdio","command":"python","args":["-m","mcp_server_files"],"env":{"FOO":"bar"}}]
```

## LangGraph checkpointer

每轮对话一个 checkpoint thread（`conv_{conversation_id}_run_{agent_run_id}`），供图内状态恢复；
终态与审批仍由 `AgentRun` 表审计。`rewind_last_turn` / `delete_conversation` 会同步清理孤儿 checkpoint。

- `SYNORA_LANGGRAPH_CHECKPOINT_BACKEND`：`sqlite`（默认）| `postgres`
- `SYNORA_LANGGRAPH_CHECKPOINT_DB_URL`：Postgres DSN（`postgresql+psycopg://...`），backend 为 `postgres` 时使用
- `SYNORA_LANGGRAPH_CHECKPOINT_SQLITE_PATH`：SQLite 路径，默认 `langgraph_checkpoints.db`

## 默认账号

- 邮箱：`han.teacher@example.com`
- 密码：`SynoraMVP123!`

可通过这些环境变量覆盖：

- `SYNORA_BOOTSTRAP_EMAIL`
- `SYNORA_BOOTSTRAP_PASSWORD`
- `SYNORA_BOOTSTRAP_DISPLAY_NAME`
