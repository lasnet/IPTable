from functools import lru_cache

from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

from app.services.i18n import normalize_language


class Settings(BaseSettings):
    app_name: str = "IPtable"
    app_env: str = "local"
    secret_key: str = Field(default="", min_length=0)
    session_cookie_secure: bool | None = None
    session_idle_timeout_seconds: int = Field(default=86400, ge=60)
    login_rate_limit_attempts: int = Field(default=5, ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(default=300, ge=60)
    login_rate_limit_lockout_seconds: int = Field(default=900, ge=60)
    integration_api_token: str = Field(default="", min_length=0)
    interface_language: str = Field(default="RU", min_length=0, max_length=8)
    initial_admin_username: str = Field(default="admin", min_length=3, max_length=80)
    initial_admin_password: str = Field(default="", min_length=0)
    database_url: str = ""
    postgres_host: str = ""
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "iptable"
    postgres_password: str = ""
    postgres_db: str = "iptable"
    ping_interval_seconds: int = Field(default=3600, ge=60)
    ping_timeout_seconds: int = Field(default=2, ge=1)
    ping_concurrency: int = Field(default=8, ge=1, le=128)
    ping_batch_size: int = Field(default=32, ge=1, le=512)
    ping_batch_pause_seconds: float = Field(default=1.0, ge=0)
    ping_project_pause_seconds: float = Field(default=5.0, ge=0)
    ping_queue_poll_seconds: float = Field(default=5.0, ge=1)
    ping_running_job_timeout_seconds: int = Field(default=7200, ge=300)
    project_table_default_page_size: int = Field(default=25, ge=10, le=250)
    max_project_addresses: int = Field(default=4096, ge=1)
    csv_import_max_bytes: int = Field(default=2_097_152, ge=1024)
    enable_ping_worker: bool = True

    # The shared dotenv also contains Docker Compose variables (ports and PostgreSQL).
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        self.app_env = self.app_env.strip().lower()
        if self.app_env not in {"local", "production"}:
            raise ValueError("APP_ENV must be local or production")
        if not self.database_url:
            if self.postgres_host:
                if not self.postgres_password.strip():
                    raise ValueError("Set POSTGRES_PASSWORD in docker-compose.yml or .env")
                self.database_url = URL.create(
                    "postgresql+psycopg", username=self.postgres_user,
                    password=self.postgres_password, host=self.postgres_host,
                    port=self.postgres_port, database=self.postgres_db,
                ).render_as_string(hide_password=False)
            else:
                self.database_url = "sqlite:///./data/iptable.sqlite3"
        self.interface_language = normalize_language(self.interface_language)
        if self.app_env == "production":
            if len(self.secret_key.strip()) < 32 or self.secret_key.strip().startswith("replace-with-"):
                raise ValueError("Production SECRET_KEY must be at least 32 characters and not a placeholder")
            if len(self.initial_admin_password.strip()) < 12 or self.initial_admin_password.strip().startswith("replace-with-"):
                raise ValueError("Production INITIAL_ADMIN_PASSWORD must be at least 12 characters and not a placeholder")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
