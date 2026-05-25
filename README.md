# Synora MVP

Synora 是一个面向单用户场景的生活助理 MVP，当前仓库包含：

- `app/synora`：Flutter 客户端
- `services/api`：FastAPI 后端
- `deploy/dev`：本地开发用 Docker Compose
- `deploy/prod`：生产部署用 Docker Compose

## 当前能力

- 单用户登录与会话
- 文本、图片、PDF 等附件输入
- 日程草稿解析、冲突检测、审批确认
- 快速笔记预览与保存
- SMTP 邮件提醒与企业微信机器人提醒审计
- 标准 MCP Server（Streamable HTTP + Bearer Token）

## 启动方式

### 本地开发

```powershell
cd D:\Projects\aaa\synora\deploy\dev
Copy-Item .env.example .env
docker compose up --build
```

### 生产部署

```powershell
cd D:\Projects\aaa\synora\deploy\prod
Copy-Item .env.example .env
docker compose up --build
```

至少需要补充这些环境变量：

- `SYNORA_LLM_API_KEY`
- `SYNORA_MCP_BEARER_TOKEN`
- 如需企业微信提醒：`SYNORA_WECOM_ROBOT_WEBHOOK`
- 如需真实邮件发送：配置你自己的 SMTP 参数

## Flutter 联调

Android 模拟器：

```powershell
cd D:\Projects\aaa\synora\app\synora
flutter pub get
flutter run --dart-define=SYNORA_API_BASE_URL=http://10.0.2.2:8000
```

Windows 浏览器调试 Flutter Web：

```powershell
flutter run -d chrome --dart-define=SYNORA_API_BASE_URL=http://localhost:8000
```

Android 真机调试：

```powershell
flutter run --dart-define=SYNORA_API_BASE_URL=http://<你的电脑局域网IP>:8000
```

## MCP 接入

- 端点：`http://localhost:8000/mcp`
- 认证：`Authorization: Bearer <SYNORA_MCP_BEARER_TOKEN>`
- 当前工具：
  - `parse_schedule_draft`
  - `detect_schedule_conflicts`
  - `create_schedule_after_approval`
  - `record_quick_note`
  - `dispatch_notification`
  - `get_notification_status`
