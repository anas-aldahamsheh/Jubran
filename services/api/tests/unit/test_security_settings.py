"""Production refuses to start with published secrets, debug mode or insecure cookies."""
import secrets

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from jubran import settings as settings_module
from jubran.settings import Settings

STRONG = {
    "SECRET_KEY": secrets.token_urlsafe(48),
    "CSRF_SECRET": secrets.token_urlsafe(48),
    "DATA_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
}


def build(**values) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    # Only the values each test passes explicitly may count.
    for name in ("ENVIRONMENT", "DEBUG", "SECRET_KEY", "CSRF_SECRET", "SECURE_COOKIES",
                 "ADMIN_EMAIL", "ADMIN_PASSWORD", "COOKIE_SAMESITE", "CORS_ORIGINS", "DATA_ENCRYPTION_KEYS"):
        monkeypatch.delenv(name, raising=False)


def reasons(error: ValidationError) -> str:
    return "\n".join(str(item["msg"]) for item in error.errors())


def test_production_is_the_default_and_needs_real_secrets():
    with pytest.raises(ValidationError) as error:
        build()
    text = reasons(error.value)
    assert "SECRET_KEY is not set" in text and "CSRF_SECRET is not set" in text


@pytest.mark.parametrize("value", [
    "jubran-secure-demo-secret-key-change-in-production-2026",  # old code default
    "replace-with-a-long-random-secret",                          # .env.example placeholder
    "short-but-random-Xy7",
    "a" * 64,
])
def test_published_placeholder_or_weak_secrets_are_refused(value):
    with pytest.raises(ValidationError) as error:
        build(ENVIRONMENT="production", SECRET_KEY=value, CSRF_SECRET=STRONG["CSRF_SECRET"],
              DATA_ENCRYPTION_KEYS=STRONG["DATA_ENCRYPTION_KEYS"])
    assert "SECRET_KEY" in reasons(error.value)


def test_production_refuses_debug_plain_http_cookies_and_shared_secret():
    with pytest.raises(ValidationError) as error:
        build(ENVIRONMENT="production", SECRET_KEY=STRONG["SECRET_KEY"], CSRF_SECRET=STRONG["SECRET_KEY"],
              DEBUG=True, SECURE_COOKIES=False, CORS_ORIGINS=["*"])
    text = reasons(error.value)
    assert "must be different" in text
    assert "DEBUG must be off" in text
    assert "SECURE_COOKIES must be true" in text
    assert "CORS_ORIGINS" in text


@pytest.mark.parametrize("password", ["admin12345", "short-pass"])
def test_production_refuses_weak_first_admin_password(password):
    with pytest.raises(ValidationError) as error:
        build(ENVIRONMENT="production", **STRONG, ADMIN_EMAIL="owner@example.com", ADMIN_PASSWORD=password)
    assert "ADMIN_PASSWORD" in reasons(error.value)


def test_startup_error_never_echoes_secret_values():
    leaked = "Leaky-Secret-Value-123"
    with pytest.raises(ValidationError) as error:
        build(ENVIRONMENT="production", SECRET_KEY=leaked, CSRF_SECRET=STRONG["CSRF_SECRET"],
              GEMINI_API_KEY="AIza-real-looking-key")
    assert leaked not in str(error.value) and "AIza-real-looking-key" not in str(error.value)


def test_unsafe_production_start_stops_with_a_readable_reason(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "")  # environment variables win over any local .env file
    monkeypatch.setenv("CSRF_SECRET", "")
    with pytest.raises(SystemExit) as stop:
        settings_module._load_settings()
    message = str(stop.value.code)
    assert "Refusing to start in production" in message
    assert "ENVIRONMENT=development" in message


def test_secure_production_settings_resolve_safe_defaults():
    config = build(ENVIRONMENT="Production", **STRONG, ADMIN_EMAIL=" Owner@Example.com ",
                   ADMIN_PASSWORD="a-long-unique-passphrase")
    assert config.is_production
    assert config.DEBUG is False
    assert config.SECURE_COOKIES is True
    assert config.ADMIN_EMAIL == "owner@example.com"


def test_local_development_keeps_working_without_extra_setup():
    config = build(ENVIRONMENT="development")
    assert config.DEBUG is True
    assert config.SECURE_COOKIES is False
    assert config.SECRET_KEY and config.CSRF_SECRET  # historical dev values keep old local data readable


def test_cross_site_cookies_need_https():
    with pytest.raises(ValidationError):
        build(ENVIRONMENT="development", COOKIE_SAMESITE="none", SECURE_COOKIES=False)


def test_production_needs_its_own_data_encryption_key():
    without = {name: value for name, value in STRONG.items() if name != "DATA_ENCRYPTION_KEYS"}
    with pytest.raises(ValidationError) as error:
        build(ENVIRONMENT="production", **without)
    assert "DATA_ENCRYPTION_KEYS is not set" in reasons(error.value)


def test_malformed_data_encryption_keys_are_refused_with_a_clear_reason():
    with pytest.raises(ValidationError) as error:
        build(ENVIRONMENT="development", DATA_ENCRYPTION_KEYS=f"{Fernet.generate_key().decode()}, not-a-key")
    assert "key #2 is not a valid Fernet key" in reasons(error.value)
    assert "not-a-key" not in reasons(error.value)
