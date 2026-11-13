"""Settings validation: what the application refuses to start with in production."""

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_DATABASE_URL, Settings, describe_errors, get_settings

SAFE_PRODUCTION = {
    "app_env": "production",
    "debug": False,
    "jwt_secret": "s" * 40,
    "database_url": "postgresql+asyncpg://app:secret@db.internal:5432/quant",
    "cors_origins": ["https://app.example.com"],
    "frontend_base_url": "https://app.example.com",
    "require_email_verification": False,
    "email_backend": "console",
    "smtp_host": "",
}


def build(**overrides) -> Settings:
    # Explicit values win over the environment and no .env file is read, so these
    # tests behave the same on a laptop and in CI.
    return Settings(_env_file=None, **{**SAFE_PRODUCTION, **overrides})


def test_safe_production_settings_are_accepted():
    settings = build()
    assert settings.is_production
    assert settings.resolved_log_format == "json"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"debug": True}, "DEBUG must be false"),
        ({"database_url": DEFAULT_DATABASE_URL}, "DATABASE_URL must not be the development"),
        ({"jwt_secret": "development-only-change-me"}, "JWT_SECRET must be at least 32"),
        ({"jwt_secret": "short"}, "JWT_SECRET must be at least 32"),
        ({"cors_origins": ["http://localhost:3000"]}, "must not include a local origin"),
        ({"cors_origins": ["https://app.example.com", "http://127.0.0.1:5173"]}, "local origin"),
        ({"cors_origins": ["*"]}, "explicit origins"),
        ({"frontend_base_url": "http://localhost:3000"}, "FRONTEND_BASE_URL"),
        ({"require_email_verification": True}, "needs a real EMAIL_BACKEND"),
    ],
)
def test_unsafe_production_settings_are_refused(overrides, message):
    with pytest.raises(ValidationError, match=message):
        build(**overrides)


def test_every_production_problem_is_reported_at_once():
    with pytest.raises(ValidationError) as caught:
        build(debug=True, cors_origins=["*"], frontend_base_url="http://localhost:3000")
    text = str(caught.value)
    assert "DEBUG" in text and "explicit origins" in text and "FRONTEND_BASE_URL" in text


def test_the_same_settings_are_fine_in_development():
    settings = build(
        app_env="development",
        debug=True,
        database_url=DEFAULT_DATABASE_URL,
        jwt_secret="development-only-change-me",
        cors_origins=["http://localhost:3000"],
        frontend_base_url="http://localhost:3000",
    )
    assert not settings.is_production
    assert settings.resolved_log_format == "text"


def test_email_verification_is_allowed_with_smtp():
    settings = build(
        require_email_verification=True, email_backend="smtp", smtp_host="smtp.example.com"
    )
    assert settings.require_email_verification


def test_smtp_backend_needs_a_host_in_any_environment():
    with pytest.raises(ValidationError, match="requires SMTP_HOST"):
        build(app_env="development", email_backend="smtp", smtp_host="")


def test_unknown_email_backend_is_refused():
    with pytest.raises(ValidationError, match="EMAIL_BACKEND"):
        build(email_backend="carrier-pigeon")


@pytest.mark.parametrize(
    "url",
    ["postgres://app:secret@db.internal/quant", "postgresql://app:secret@db.internal/quant"],
)
def test_plain_postgres_urls_select_the_async_driver(url):
    assert build(database_url=url).database_url == (
        "postgresql+asyncpg://app:secret@db.internal/quant"
    )


def test_cors_origins_accept_a_comma_separated_string():
    settings = build(cors_origins="https://a.example.com, https://b.example.com")
    assert settings.cors_origins == ["https://a.example.com", "https://b.example.com"]


def test_cors_origins_from_the_environment(monkeypatch):
    """Hosting dashboards make a plain comma-separated value the natural thing to type."""
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com/,https://b.example.com")
    settings = Settings(_env_file=None, **{k: v for k, v in SAFE_PRODUCTION.items()
                                           if k != "cors_origins"})
    assert settings.cors_origins == ["https://a.example.com", "https://b.example.com"]

    monkeypatch.setenv("CORS_ORIGINS", '["https://c.example.com"]')
    settings = Settings(_env_file=None, **{k: v for k, v in SAFE_PRODUCTION.items()
                                           if k != "cors_origins"})
    assert settings.cors_origins == ["https://c.example.com"]


def test_configuration_errors_are_reported_without_secret_values(monkeypatch):
    secret = "s3cr3t-" * 6
    with pytest.raises(ValidationError) as caught:
        build(jwt_secret=secret, debug=True, smtp_password="hunter2-hunter2")
    assert secret[:12] in str(caught.value)  # what pydantic would print by default
    described = describe_errors(caught.value)
    assert "DEBUG must be false" in described
    assert "s3cr3t" not in described and "hunter2" not in described


def test_startup_exits_with_a_clean_message_on_bad_configuration(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "p" * 40)
    monkeypatch.setenv("DEBUG", "true")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as caught:
            get_settings()
    finally:
        get_settings.cache_clear()
    message = str(caught.value)
    assert message.startswith("Invalid configuration:")
    assert "DEBUG must be false" in message
    assert "pppp" not in message
