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

## 默认账号

- 邮箱：`han.teacher@example.com`
- 密码：`SynoraMVP123!`

可通过这些环境变量覆盖：

- `SYNORA_BOOTSTRAP_EMAIL`
- `SYNORA_BOOTSTRAP_PASSWORD`
- `SYNORA_BOOTSTRAP_DISPLAY_NAME`
