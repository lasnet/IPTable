from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "IPtable"
    app_env: str = "local"
    secret_key: str = Field(default="", min_length=0)
    session_idle_timeout_seconds: int = Field(default=86400, ge=60)
    initial_admin_username: str = Field(default="admin", min_length=3, max_length=80)
    initial_admin_password: str = Field(default="", min_length=0)
    database_url: str = "sqlite:///./data/iptable.sqlite3"
    ping_interval_seconds: int = Field(default=3600, ge=60)
    ping_timeout_seconds: int = Field(default=2, ge=1)
    ping_concurrency: int = Field(default=8, ge=1, le=128)
    ping_batch_size: int = Field(default=32, ge=1, le=512)
    ping_batch_pause_seconds: float = Field(default=1.0, ge=0)
    ping_project_pause_seconds: float = Field(default=5.0, ge=0)
    ping_queue_poll_seconds: float = Field(default=5.0, ge=1)
    max_project_addresses: int = Field(default=4096, ge=1)
    csv_import_max_bytes: int = Field(default=2_097_152, ge=1024)
    enable_ping_worker: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
