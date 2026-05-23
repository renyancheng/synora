from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="SYNORA_")

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

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_timeout_seconds: int = 45

    ocr_space_api_key: str = ""
    ocr_space_base_url: str = "https://api.ocr.space/parse/image"
    ocr_space_language: str = "chs"
    ocr_space_engine: int = 2
    ocr_max_file_size_bytes: int = 1_000_000
    ocr_max_pdf_pages: int = 3

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "synora"
    minio_secret_key: str = "synora123"
    minio_bucket: str = "synora-attachments"
    minio_secure: bool = False
    minio_region: str = "us-east-1"

    attachment_max_size_bytes: int = 1_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
