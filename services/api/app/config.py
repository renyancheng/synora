import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.agent.external_mcp import McpServerSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="SYNORA_",
    )

    app_name: str = "Synora API"
    env: str = "development"
    api_prefix: str = ""
    default_timezone: str = "Asia/Shanghai"

    database_url: str = "sqlite:///./synora.db"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    session_ttl_hours: int = 168
    approval_ttl_hours: int = 72

    bootstrap_email: str = "han.teacher@example.com"
    bootstrap_password: str = "SynoraMVP123!"
    bootstrap_display_name: str = "韩老师"

    # Firebase（FCM）可选：指向 service account JSON；为空时 FCM 推送静默跳过。
    firebase_service_account_path: str = ""

    agent_backend: str = "langgraph"  # "langgraph" | "legacy"
    agent_max_loop_iterations: int = 4
    # 运行级预算：单 run 总 wall-clock 时长（秒，0 关闭）。
    agent_max_run_seconds: int = 120
    # 单轮 token 上限（0 不限制）：create_chat_model 未显式传 max_tokens 时注入。
    agent_max_tokens_per_round: int = 0
    # 单 run 总 token 上限（0 关闭）：当前仅由 _route_loop 读取，usage 记账由后续任务实现。
    agent_max_run_tokens: int = 0
    # --- P1-2 Agent 运行并发与优先级调度 ---
    # 全局并发闸门：进程内同时执行的 agent run 数上限（0=不限制）。
    agent_max_concurrent_runs: int = 8
    # intake（schedule_intake / quick_note_intake）并发上限（0=等于全局上限）。
    agent_max_intake_concurrent_runs: int = 0
    # general_chat 并发上限（0=由全局上限与保留槽位推导：全局上限 - agent_intake_reserved_slots）。
    agent_max_general_chat_concurrent_runs: int = 0
    # intake 保留槽位：general_chat 最多占用（全局上限 - 保留槽位）个并发槽位，
    # 保证日程/速记这类秒级确定性卡片流程在 general_chat 占满闸门时仍能进入执行。
    agent_intake_reserved_slots: int = 2
    pending_draft_timeout_hours: int = 6
    pending_nudge_max: int = 2
    pending_nudge_cooldown_hours: int = 24
    mcp_servers: list[McpServerSettings] = []  # SYNORA_MCP_SERVERS JSON 数组
    langgraph_checkpoint_backend: str = "sqlite"  # "sqlite" | "postgres"
    langgraph_checkpoint_db_url: str = ""
    langgraph_checkpoint_sqlite_path: str = "langgraph_checkpoints.db"
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.6-flash"
    llm_enable_thinking: bool = False
    llm_timeout_seconds: int = 90
    llm_max_pdf_pages: int = 6
    llm_max_image_side: int = 1568
    # 联网搜索（智谱 bigmodel 网络搜索 API，模型 search_std）。
    # Key 通过环境变量 SYNORA_ZHIPU_WEB_SEARCH_API_KEY 配置；为空时工具返回未配置提示。
    zhipu_web_search_api_key: str = ""
    zhipu_web_search_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_web_search_model: str = "search_std"
    memory_enabled: bool = True
    memory_embedding_model: str = "text-embedding-v4"
    memory_top_k: int = 6
    memory_writeback_queue: str = "memory"
    memory_vector_schema: str = "public"
    memory_vector_table: str = "synora_memory_vectors"
    memory_embedding_dimensions: int = 1536
    semantic_search_enabled: bool = True
    quick_note_vector_schema: str = "public"
    quick_note_vector_table: str = "synora_quick_note_vectors"
    schedule_vector_schema: str = "public"
    schedule_vector_table: str = "synora_schedule_vectors"
    conversation_history_vector_schema: str = "public"
    conversation_history_vector_table: str = "synora_conversation_history_vectors"
    conversation_history_top_k: int = 6
    conversation_history_candidate_limit: int = 18

    mcp_bearer_token: str = ""
    mcp_mount_path: str = "/mcp"
    cors_allowed_origins: str = (
        "http://localhost,"
        "http://localhost:3000,"
        "http://localhost:5000,"
        "http://localhost:8000,"
        "http://101.43.52.61,"
        "http://127.0.0.1,"
        "http://127.0.0.1:3000,"
        "http://127.0.0.1:5000,"
        "http://127.0.0.1:8000"
    )
    cors_allow_origin_regex: str = (
        r"https?://"
        r"(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)"
        r"(:\d+)?$"
    )

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "synora"
    minio_secret_key: str = "synora123"
    minio_bucket: str = "synora-attachments"
    minio_secure: bool = False
    minio_region: str = "us-east-1"

    attachment_max_size_bytes: int = 8_000_000

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        raw = self.cors_allowed_origins.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
