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


@lru_cache
def get_settings() -> Settings:
    return Settings()
