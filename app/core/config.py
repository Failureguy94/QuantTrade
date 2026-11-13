from decimal import Decimal
from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "development-only-change-me"
DEFAULT_DATABASE_URL = "postgresql+asyncpg://quant:quant@localhost:5433/quant"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Quant API"
    app_env: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    database_url: str = DEFAULT_DATABASE_URL
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    cors_origins: list[str] = ["http://localhost:3000"]

    # Paper-trading fill model. Basis points of traded notional.
    paper_commission_bps: Decimal = Decimal("5")
    paper_slippage_bps: Decimal = Decimal("2")
    # Reference quotes: how long a fetched quote is reused, and how old the newest
    # available price may be before orders are rejected for lack of a usable price.
    quote_cache_ttl_seconds: int = 60
    quote_max_age_days: int = 7

    # Sessions: refresh tokens are rotated on use. A token that was just rotated is
    # still accepted for the grace period so concurrent requests do not log a user out.
    refresh_token_days: int = 30
    refresh_token_grace_seconds: int = 30
    # One-time links sent by email.
    email_verification_hours: int = 48
    password_reset_minutes: int = 60
    require_email_verification: bool = False
    # Where the links in emails point (the web application).
    frontend_base_url: str = "http://localhost:3000"

    # Email delivery: "console" logs the message, "smtp" sends it.
    email_backend: str = "console"
    email_from: str = "Quant <no-reply@quant.example>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True

    # Abuse limits. Counters live in process memory (one API process, see ADR-016).
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 20
    rate_limit_backtests_per_minute: int = 12
    rate_limit_risk_per_minute: int = 30
    rate_limit_market_data_per_minute: int = 60
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    max_concurrent_runs_per_user: int = 2
    max_request_body_bytes: int = 1_048_576

    # Background work: queued backtests and resting limit orders. By default the API
    # process runs the worker itself; set WORKER_EMBEDDED=false when running
    # `python -m app.worker` as its own process.
    worker_embedded: bool = True
    worker_poll_seconds: float = 2.0
    matcher_interval_seconds: float = 60.0
    job_timeout_minutes: int = 30
    max_queued_runs_per_user: int = 5

    # Logging: "text" for people, "json" for log collectors. Empty picks by environment.
    log_level: str = "INFO"
    log_format: str = ""
    sentry_dsn: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def resolved_log_format(self) -> str:
        return self.log_format or ("json" if self.is_production else "text")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, value: str) -> str:
        """Accept the plain URLs hosting platforms hand out and select asyncpg."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str, info):
        # Validate that in production environment, a secure custom secret is set
        app_env = info.data.get("app_env", "development")
        if app_env == "production":
            if value == DEFAULT_JWT_SECRET or len(value) < 32:
                raise ValueError(
                    "In production, JWT_SECRET must be at least 32 characters long "
                    "and not use the default."
                )
        return value

    @field_validator("email_backend")
    @classmethod
    def known_email_backend(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"console", "smtp"}:
            raise ValueError("EMAIL_BACKEND must be 'console' or 'smtp'")
        return value

    @model_validator(mode="after")
    def validate_production(self) -> Self:
        """Refuse to start in production with settings that are only safe locally."""
        if self.email_backend == "smtp" and not self.smtp_host:
            raise ValueError("EMAIL_BACKEND=smtp requires SMTP_HOST")
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.debug:
            problems.append("DEBUG must be false")
        if self.database_url == DEFAULT_DATABASE_URL:
            problems.append("DATABASE_URL must not be the development default")
        for origin in self.cors_origins:
            if origin == "*":
                problems.append("CORS_ORIGINS must list explicit origins, not '*'")
            elif (urlsplit(origin).hostname or "") in _LOCAL_HOSTS:
                problems.append(f"CORS_ORIGINS must not include a local origin ({origin})")
        if (urlsplit(self.frontend_base_url).hostname or "") in _LOCAL_HOSTS:
            problems.append("FRONTEND_BASE_URL must be the public web address")
        if self.require_email_verification and self.email_backend == "console":
            problems.append(
                "REQUIRE_EMAIL_VERIFICATION needs a real EMAIL_BACKEND (console only logs)"
            )
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
