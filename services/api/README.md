# Synora API

FastAPI 后端负责：
- 登录会话
- 附件上传与 MinIO 存储
- OCR.space 附件解析
- DeepSeek 受控 Agent Runtime
- 日程审批创建与冲突检测
- 速记审批保存
- Celery 提醒调度
- SMTP / 企业微信群机器人通知审计

## 本地开发
推荐直接使用 `D:\Projects\aaa\synora\deploy\docker-compose.yml`。
如果要手动运行：

1. 创建 Python 3.12 虚拟环境
2. 安装 `requirements.txt`
3. 复制 `deploy/.env.example` 为 `deploy/.env`，再设置其中的环境变量
4. 启动 API：
```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 默认账号

- 邮箱：`han.teacher@example.com`
- 密码：`SynoraMVP123!`

可通过环境变量覆盖：
- `SYNORA_BOOTSTRAP_EMAIL`
- `SYNORA_BOOTSTRAP_PASSWORD`
- `SYNORA_BOOTSTRAP_DISPLAY_NAME`
