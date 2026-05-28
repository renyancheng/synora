import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    notification_from_email: str = "synora@example.com"
    notification_to_email: str = "han.teacher@example.com"
    wecom_robot_webhook: str = ""

    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.6-flash"
    llm_enable_thinking: bool = False
    llm_timeout_seconds: int = 90
    llm_max_pdf_pages: int = 6
    llm_max_image_side: int = 1568
    memory_enabled: bool = True
    memory_embedding_model: str = "text-embedding-v4"
    memory_top_k: int = 6
    memory_writeback_queue: str = "memory"
    memory_vector_schema: str = "public"
    memory_vector_table: str = "synora_memory_vectors"
    memory_embedding_dimensions: int = 1536

    mcp_bearer_token: str = ""
    mcp_mount_path: str = "/mcp"
    cors_allowed_origins: str = (
        "http://localhost,"
        "http://localhost:3000,"
        "http://localhost:5000,"
        "http://localhost:8000,"
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
