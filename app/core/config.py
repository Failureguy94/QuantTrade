from decimal import Decimal
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    database_url: str = "postgresql+asyncpg://quant:quant@localhost:5433/quant"
    jwt_secret: str = "development-only-change-me"
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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str, info):
        # Validate that in production environment, a secure custom secret is set
        app_env = info.data.get("app_env", "development")
        if app_env == "production":
            if value == "development-only-change-me" or len(value) < 32:
                raise ValueError(
                    "In production, JWT_SECRET must be at least 32 characters long "
                    "and not use the default."
                )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
