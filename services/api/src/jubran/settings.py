"""Application Settings and Configuration.

Secure by default: unless ``ENVIRONMENT`` is explicitly ``development`` or
``test``, the server runs in production mode and refuses to start with
published/weak secrets, debug mode, or cookies that travel over plain HTTP.
"""
from pathlib import Path
from typing import Literal, Optional

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that appeared in this repository (code, .env.example, docs). They are
# public, so they may never protect a production deployment.
_DEV_SECRET_KEY = "jubran-secure-demo-secret-key-change-in-production-2026"
_DEV_CSRF_SECRET = "jubran-csrf-secret-key-change-in-prod-2026"
PUBLISHED_SECRETS = frozenset({
    _DEV_SECRET_KEY,
    _DEV_CSRF_SECRET,
    "replace-with-a-long-random-secret",
    "replace-with-a-different-long-random-secret",
})
_PLACEHOLDER_MARKERS = ("change-in-prod", "changeme", "change-me", "replace-with", "your-secret", "example")

# Passwords of the local demo accounts (development/test only). Published, so
# production never accepts them for any account.
DEMO_PASSWORDS = frozenset({"admin12345", "user12345"})
MIN_SECRET_LENGTH = 32
MIN_PRODUCTION_ADMIN_PASSWORD_LENGTH = 12


def secret_problem(name: str, value: str) -> Optional[str]:
    """Why ``value`` cannot protect production, or None when it is acceptable."""
    if not value:
        return f"{name} is not set."
    lowered = value.lower()
    if value in PUBLISHED_SECRETS or any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return f"{name} is a published/placeholder value."
    if len(value) < MIN_SECRET_LENGTH or len(set(value)) < 12:
        return f"{name} must be a random value of at least {MIN_SECRET_LENGTH} characters."
    return None


def admin_password_problem(password: str, email: Optional[str] = None, label: str = "ADMIN_PASSWORD") -> Optional[str]:
    """Why ``password`` is too weak for a production administrator, or None."""
    if password in DEMO_PASSWORDS:
        return f"{label} is a published demo password."
    if len(password) < MIN_PRODUCTION_ADMIN_PASSWORD_LENGTH:
        return f"{label} must be at least {MIN_PRODUCTION_ADMIN_PASSWORD_LENGTH} characters."
    if email and password.strip().lower() == email.strip().lower():
        return f"{label} must not equal the admin email."
    return None


def encryption_keys_problem(value: str) -> Optional[str]:
    """Why DATA_ENCRYPTION_KEYS is unusable, or None (an empty value is checked separately)."""
    import base64
    import binascii
    for index, key in enumerate(k.strip() for k in (value or "").split(",") if k.strip()):
        try:
            valid = len(base64.urlsafe_b64decode(key.encode("ascii"))) == 32
        except (binascii.Error, ValueError, UnicodeEncodeError):
            valid = False
        if not valid:
            return (f"DATA_ENCRYPTION_KEYS: key #{index + 1} is not a valid Fernet key (44 characters, "
                    "made with Fernet.generate_key()).")
    return None


_HERE = Path(__file__).resolve()
API_DIR = _HERE.parents[2]                                  # services/api
PROJECT_ROOT = _HERE.parents[4] if len(_HERE.parents) > 4 else API_DIR
# Always the same files, wherever the server is started from. The project
# root's .env is the main one; services/api/.env is still read for older
# setups, but where both set a key the root wins (see env_file_conflicts).
ENV_FILES = (API_DIR / ".env", PROJECT_ROOT / ".env")


def _read_env_file(path: Path) -> dict:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip().removeprefix("export ").strip()] = value.strip().strip('"').strip("'")
    return values


def env_file_conflicts() -> list:
    """Setting names given different values in the two .env files (values are never reported)."""
    api_values, root_values = (_read_env_file(path) for path in ENV_FILES)
    if ENV_FILES[0] == ENV_FILES[1]:
        return []
    return sorted(key for key in api_values.keys() & root_values.keys() if api_values[key] != root_values[key])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,  # never echo secrets in a startup error
    )

    # Environment. Anything other than an explicit development/test run is production.
    ENVIRONMENT: Literal["development", "test", "production"] = "production"
    # Unset → on for development/test, off for production (/docs, LAN origins).
    DEBUG: Optional[bool] = None

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://jubran_user:jubran_password@localhost:5433/jubran_db"
    TEST_DATABASE_URL: str = "sqlite+aiosqlite:///:memory:"

    # Security. Production requires both secrets from the environment.
    SECRET_KEY: str = ""
    CSRF_SECRET: str = ""
    # Encrypts stored secrets (AI provider keys, printable QR tokens). Comma-separated
    # Fernet keys: the first encrypts, all of them can decrypt (rotation). Required in
    # production; generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    DATA_ENCRYPTION_KEYS: str = ""
    SESSION_COOKIE_NAME: str = "jubran_session"            # customer table visit
    ADMIN_SESSION_COOKIE_NAME: str = "jubran_admin_session"  # administrator login
    USER_SESSION_COOKIE_NAME: str = "jubran_auth_token"      # optional customer account login
    CSRF_COOKIE_NAME: str = "jubran_csrf"
    # Unset → on in production (cookies only over HTTPS), off for local HTTP.
    SECURE_COOKIES: Optional[bool] = None
    # "none" only when the website and the API live on different sites (needs HTTPS).
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_DOMAIN: Optional[str] = None
    SESSION_MAX_AGE_SECONDS: int = 86400  # 24 hours
    # A login not used for this long is signed out (an open admin screen counts as use).
    LOGIN_IDLE_TIMEOUT_SECONDS: int = 8 * 3600
    # Safety net for tables nobody closed: a visit with no activity at all (no guest
    # page open, no order or request) for this many hours, and no order still being
    # handled, is closed automatically. 0 turns it off.
    TABLE_SESSION_IDLE_HOURS: float = 6.0
    CSRF_MAX_AGE_SECONDS: int = 30 * 86400

    # Uploaded dish photos (files on disk; keep this folder in backups).
    MEDIA_DIR: str = str(API_DIR / "media")

    # Abuse protection (see application/rate_limiter.py for the individual limits).
    # Behind a reverse proxy, run uvicorn with --proxy-headers so limits apply per visitor.
    RATE_LIMITS_ENABLED: bool = True

    # First administrator. Used only when that account does not exist yet; in
    # production it is required while the database has no active administrator.
    ADMIN_EMAIL: Optional[str] = None
    ADMIN_PASSWORD: Optional[SecretStr] = None

    # AI models. Each purpose (chat, menu search, speech to text, live voice) has its own
    # section in Admin > Settings with its own model and key; these are the defaults a
    # section uses (and suggests) before it is saved, with the keys below.
    GEMINI_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_TEXT_MODEL: str = "gemini-2.5-flash"
    OPENAI_TEXT_MODEL: str = "gpt-6-luna"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    # Menu search before its section is saved: "auto" uses a provider this server has a key for.
    EMBEDDING_PROVIDER: Literal["auto", "gemini", "openai"] = "auto"
    SEMANTIC_MIN_SIMILARITY: float = 0.65
    GEMINI_TRANSCRIBE_MODEL: str = "gemini-3.5-transcribe"
    OPENAI_TRANSCRIBE_MODEL: str = "gpt-4o-transcribe"
    # Live voice: OpenAI GPT-Live (it hands every request to the chat assistant above).
    OPENAI_LIVE_MODEL: str = "gpt-live-1"

    # A file that keeps the server's own notes (warnings, and how each live voice call went:
    # start, reconnects, why it ended; never what was said). Unset: printed only.
    LOG_FILE: Optional[str] = None

    # Frontend origins allowed to call the API and open live-update sockets.
    # While DEBUG is on, private-network addresses on port 3001 are also allowed.
    CORS_ORIGINS: list[str] = ["http://localhost:3001", "http://127.0.0.1:3001"]

    # Default Branch
    DEFAULT_BRANCH_ID: str = "00000000-0000-0000-0000-000000000001"

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def _normalize_environment(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @model_validator(mode="after")
    def _secure_defaults(self) -> "Settings":
        if self.DEBUG is None:
            self.DEBUG = not self.is_production
        if self.SECURE_COOKIES is None:
            self.SECURE_COOKIES = self.is_production
        if self.ADMIN_EMAIL:
            self.ADMIN_EMAIL = self.ADMIN_EMAIL.strip().lower()

        if self.is_production:
            problems = self.production_problems()
            if problems:
                raise ValueError(
                    "Refusing to start in production with unsafe settings:\n  - "
                    + "\n  - ".join(problems)
                    + "\nSet the values in the server environment (see .env.example). "
                    "For a local run on your own computer set ENVIRONMENT=development."
                )
        else:
            # Local runs keep working with the historical development secrets.
            if not self.SECRET_KEY:
                self.SECRET_KEY = _DEV_SECRET_KEY
            if not self.CSRF_SECRET:
                self.CSRF_SECRET = _DEV_CSRF_SECRET
        if self.COOKIE_SAMESITE == "none" and not self.SECURE_COOKIES:
            raise ValueError("COOKIE_SAMESITE=none requires SECURE_COOKIES=true (HTTPS).")
        problem = encryption_keys_problem(self.DATA_ENCRYPTION_KEYS)
        if problem:
            raise ValueError(problem)
        return self

    def production_problems(self) -> list[str]:
        problems = []
        for name in ("SECRET_KEY", "CSRF_SECRET"):
            problem = secret_problem(name, getattr(self, name))
            if problem:
                problems.append(problem)
        if self.SECRET_KEY and self.SECRET_KEY == self.CSRF_SECRET:
            problems.append("SECRET_KEY and CSRF_SECRET must be different values.")
        if not self.DATA_ENCRYPTION_KEYS.strip():
            problems.append("DATA_ENCRYPTION_KEYS is not set (it encrypts stored AI keys and QR codes). Generate one with: "
                            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        if self.DEBUG:
            problems.append("DEBUG must be off in production.")
        if not self.SECURE_COOKIES:
            problems.append("SECURE_COOKIES must be true in production (serve the site over HTTPS).")
        if "*" in self.CORS_ORIGINS:
            problems.append("CORS_ORIGINS must list the real website address, not '*'.")
        if self.ADMIN_PASSWORD is not None:
            problem = admin_password_problem(self.ADMIN_PASSWORD.get_secret_value(), self.ADMIN_EMAIL)
            if problem:
                problems.append(problem)
            if not self.ADMIN_EMAIL:
                problems.append("ADMIN_EMAIL must be set together with ADMIN_PASSWORD.")
        return problems


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Stop with a readable reason instead of a traceback.
        reasons = "\n".join(str(error["msg"]).removeprefix("Value error, ") for error in exc.errors())
        raise SystemExit(f"\n[jubran] {reasons}\n") from None


settings = _load_settings()
