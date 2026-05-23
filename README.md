# Synora MVP

Synora 是一个面向单用户的生活备忘助手 MVP，当前仓库包含：

- `D:\Projects\aaa\synora\app\synora`：Flutter Android 优先客户端
- `D:\Projects\aaa\synora\services\api`：FastAPI 模块化单体后端
- `D:\Projects\aaa\synora\deploy`：Docker Compose、本地环境样例与启动方式

## 当前能力

- 邮箱登录（单用户引导账号）
- 五类输入：文本、截图、拍照、聊天记录、邮件内容
- OCR.space 附件解析
- DeepSeek 驱动的日程抽取与速记标签整理
- 日程冲突检测与审批确认
- 速记审批确认
- SMTP 邮件提醒与企业微信群机器人 Webhook 审计

## 启动方式

1. 复制 `D:\Projects\aaa\synora\deploy\.env.example` 为 `D:\Projects\aaa\synora\deploy\.env`，再填入：
   - `SYNORA_DEEPSEEK_API_KEY`
   - `SYNORA_OCR_SPACE_API_KEY`
   - 如需真实企业微信提醒，再填 `SYNORA_WECOM_ROBOT_WEBHOOK`
   - 如需真实邮件发送，把 SMTP 改成你的邮件服务
2. 启动后端：

```powershell
cd D:\Projects\aaa\synora\deploy
docker compose up --build
```

3. 启动 Flutter：

```powershell
cd D:\Projects\aaa\synora\app\synora
flutter pub get
flutter run --dart-define=SYNORA_API_BASE_URL=http://10.0.2.2:8000
```

如果在 Windows 浏览器上调试 Flutter Web，请把 `SYNORA_API_BASE_URL` 改成 `http://localhost:8000`。
