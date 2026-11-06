from functools import lru_cache
from contextvars import ContextVar
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

from app.services.i18n import normalize_language


InterfaceTheme = Literal["blue", "green", "dark"]


class Settings(BaseSettings):
    app_name: str = "IPtable"
    app_env: str = "local"
    app_public_url: str = ""
    https_enabled: bool = False
    secret_key: str = Field(default="", min_length=0)
    session_cookie_secure: bool | None = None
    session_idle_timeout_seconds: int = Field(default=86400, ge=60)
    login_rate_limit_attempts: int = Field(default=5, ge=1, le=100)
    login_rate_limit_window_seconds: int = Field(default=300, ge=60)
    login_rate_limit_lockout_seconds: int = Field(default=900, ge=60)
    integration_api_token: str = Field(default="", min_length=0)
    interface_language: str = Field(default="RU", min_length=0, max_length=8)
    interface_theme: InterfaceTheme = "blue"
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
        self.app_public_url = self.app_public_url.strip().rstrip("/")
        if self.app_public_url:
            try:
                public = urlsplit(self.app_public_url)
                valid_port = public.port
            except ValueError:
                raise ValueError("APP_PUBLIC_URL must be an HTTP(S) origin") from None
            if (public.scheme not in {"http", "https"} or not public.hostname
                    or public.username is not None or public.password is not None
                    or public.path or public.query or public.fragment
                    or any(c.isspace() for c in self.app_public_url)
                    or valid_port == 0):
                raise ValueError("APP_PUBLIC_URL must be an HTTP(S) origin without credentials or a path")
            secure = public.scheme == "https"
            if self.session_cookie_secure is not None and self.session_cookie_secure != secure:
                raise ValueError("SESSION_COOKIE_SECURE must match the scheme of APP_PUBLIC_URL")
            self.session_cookie_secure = secure
        if self.https_enabled and not self.app_public_url.startswith("https://"):
            raise ValueError("HTTPS_ENABLED requires an HTTPS APP_PUBLIC_URL")
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
            if self.session_cookie_secure is False and not self.app_public_url.startswith("http://"):
                raise ValueError("Production requires SESSION_COOKIE_SECURE=true and an HTTPS ingress")
            if len(self.secret_key.strip()) < 32 or self.secret_key.strip().startswith("replace-with-"):
                raise ValueError("Production SECRET_KEY must be at least 32 characters and not a placeholder")
            if len(self.initial_admin_password.strip()) < 12 or self.initial_admin_password.strip().startswith("replace-with-"):
                raise ValueError("Production INITIAL_ADMIN_PASSWORD must be at least 12 characters and not a placeholder")
        return self


class BootstrapSettings(Settings):
    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        # Filter before Pydantic parses values. Invalid old runtime env must not
        # prevent a configured installation from starting after migration.
        names = {
            "app_env", "app_public_url", "https_enabled", "secret_key", "session_cookie_secure",
            "initial_admin_username", "initial_admin_password", "database_url", "postgres_host",
            "postgres_port", "postgres_user", "postgres_password", "postgres_db", "enable_ping_worker",
        }
        def filtered(source):
            return lambda: {key: value for key, value in source().items() if key in names}
        return init_settings, filtered(env_settings), filtered(dotenv_settings), file_secret_settings


@lru_cache
def _bootstrap_settings() -> Settings:
    return BootstrapSettings()


_request_settings: ContextVar[Settings | None] = ContextVar("request_settings", default=None)


def get_settings() -> Settings:
    return _request_settings.get() or _bootstrap_settings()


get_settings.cache_clear = _bootstrap_settings.cache_clear
