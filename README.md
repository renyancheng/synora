# Synora MVP

Synora 是一个面向单用户场景的生活备忘助手 MVP，当前仓库包含：

- `D:\Projects\aaa\synora\app\synora`：Flutter 客户端
- `D:\Projects\aaa\synora\services\api`：FastAPI 模块化单体后端
- `D:\Projects\aaa\synora\deploy`：Docker Compose、本地环境样例与启动方式

## 当前能力

- 单用户登录与聊天式 Agent 主界面
- 文本与多模态附件输入（图片、PDF、常见文本文件）
- `qwen3.6-plus` 驱动的日程草稿解析与速记整理
- 日程冲突检测、审批确认与提醒创建
- 速记预览、审批确认与正式保存
- SMTP 邮件提醒与企业微信机器人提醒审计
- 标准 MCP Server（Streamable HTTP、tools-only、Bearer Token 认证）

## 启动方式

1. 复制 `D:\Projects\aaa\synora\deploy\.env.example` 为 `D:\Projects\aaa\synora\deploy\.env`
2. 至少补充以下环境变量：
   - `SYNORA_LLM_API_KEY`
   - `SYNORA_MCP_BEARER_TOKEN`
   - 如需企业微信提醒：`SYNORA_WECOM_ROBOT_WEBHOOK`
   - 如需真实邮件发送：配置你自己的 SMTP 参数
3. 启动后端：

```powershell
cd D:\Projects\aaa\synora\deploy
docker compose up --build
```

4. 启动 Flutter：

```powershell
cd D:\Projects\aaa\synora\app\synora
flutter pub get
flutter run --dart-define=SYNORA_API_BASE_URL=http://10.0.2.2:8000
```

如果在 Windows 浏览器调试 Flutter Web，请把 `SYNORA_API_BASE_URL` 改成 `http://localhost:8000`。

## MCP 接入

- 端点：`http://localhost:8000/mcp`
- 传输：Streamable HTTP
- 认证：`Authorization: Bearer <SYNORA_MCP_BEARER_TOKEN>`
- 当前工具：
  - `parse_schedule_draft`
  - `detect_schedule_conflicts`
  - `create_schedule_after_approval`
  - `record_quick_note`
  - `dispatch_notification`
  - `get_notification_status`

### Inspector / 远程 MCP 客户端

1. 添加一个远程 MCP Server
2. URL 填：`http://localhost:8000/mcp`
3. Header 添加：`Authorization: Bearer <你的 MCP Token>`
4. 连接成功后即可通过 `tools/list` 查看 Synora 工具列表
