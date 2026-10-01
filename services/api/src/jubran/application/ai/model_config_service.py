"""Administrator-managed AI settings and the protection of provider keys.

The assistant uses AI for four separate jobs, each with its own section in the admin
settings (its own provider, model and key):
- chat: the waiter that talks with guests and uses the tools,
- embedding: menu search by meaning,
- transcription: a guest's recorded speech turned into text (dictation, turn-by-turn voice),
- voice: live spoken conversation (OpenAI GPT-Live, which hands each request to the chat),
  off until the administrator switches it on.

A section remembers one setting per provider, so switching provider and back keeps what
was entered, and uses one of them. A section never saved runs on the server's defaults
and environment keys.
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.auth.secret_box import decrypt_secret, encrypt_secret
from jubran.infrastructure.db.models import AiModelConfigModel
from jubran.settings import settings

# The providers each purpose can use, the usual one first.
PROVIDERS = {
    "chat": ("openai", "gemini"),
    "embedding": ("gemini", "openai"),
    "transcription": ("openai", "gemini"),
    "voice": ("openai",),  # GPT-Live only
}
PURPOSES = tuple(PROVIDERS)


def encrypt_key(raw: str) -> str:
    return encrypt_secret(raw)


def decrypt_key(ciphertext: str) -> str:
    try:
        return decrypt_secret(ciphertext)
    except ValueError as exc:
        raise ValueError("MODEL_KEY_DECRYPTION_FAILED") from exc


def environment_key(provider: str) -> Optional[str]:
    return settings.OPENAI_API_KEY if provider == "openai" else settings.GEMINI_API_KEY


def default_model(purpose: str, provider: str) -> str:
    """The model a section suggests for a provider, and uses before it is saved."""
    return {
        ("chat", "openai"): settings.OPENAI_TEXT_MODEL,
        ("chat", "gemini"): settings.GEMINI_TEXT_MODEL,
        ("embedding", "openai"): settings.OPENAI_EMBEDDING_MODEL,
        ("embedding", "gemini"): settings.GEMINI_EMBEDDING_MODEL,
        ("transcription", "openai"): settings.OPENAI_TRANSCRIBE_MODEL,
        ("transcription", "gemini"): settings.GEMINI_TRANSCRIBE_MODEL,
        ("voice", "openai"): settings.OPENAI_LIVE_MODEL,
    }[(purpose, provider)]


def default_provider(purpose: str) -> Optional[str]:
    """The provider a section uses before it is saved: one the server has a key for."""
    if purpose == "voice":
        return None  # live voice starts when the administrator switches it on
    if purpose == "embedding" and settings.EMBEDDING_PROVIDER != "auto":
        return settings.EMBEDDING_PROVIDER
    return next((provider for provider in ("gemini", "openai") if environment_key(provider)), None)


@dataclass(frozen=True)
class RuntimeModelConfig:
    provider: str
    model_id: str
    api_key: str
    reasoning_effort: Optional[str] = None
    reasoning_mode: Optional[str] = None
    thinking_level: Optional[str] = None
    thinking_budget: Optional[int] = None


class ModelConfigService:
    # A setting for a provider the section no longer offers (e.g. Gemini for live voice) is
    # never shown or used, even before the migration that removes it has run.
    @staticmethod
    async def saved(db: AsyncSession, purpose: str) -> list[AiModelConfigModel]:
        """What a section remembers: at most one setting per provider."""
        return list((await db.execute(
            select(AiModelConfigModel)
            .where(AiModelConfigModel.purpose == purpose, AiModelConfigModel.provider.in_(PROVIDERS[purpose]))
            .order_by(AiModelConfigModel.provider)
        )).scalars().all())

    @staticmethod
    async def in_use(db: AsyncSession, purpose: str) -> Optional[AiModelConfigModel]:
        return (await db.execute(
            select(AiModelConfigModel)
            .where(AiModelConfigModel.purpose == purpose, AiModelConfigModel.is_active.is_(True),
                   AiModelConfigModel.provider.in_(PROVIDERS[purpose]))
        )).scalars().first()

    @staticmethod
    def runtime_for(model: AiModelConfigModel) -> RuntimeModelConfig:
        key = decrypt_key(model.api_key_ciphertext) if model.api_key_ciphertext else environment_key(model.provider)
        if not key:
            raise ValueError("AI_PROVIDER_NOT_CONFIGURED")
        return RuntimeModelConfig(model.provider, model.model_id, key, model.reasoning_effort,
                                  model.reasoning_mode, model.thinking_level, model.thinking_budget)

    @classmethod
    async def runtime(cls, db: AsyncSession, purpose: str) -> RuntimeModelConfig:
        """The model, options and key a purpose runs on now; ValueError(code) when it can't run."""
        model = await cls.in_use(db, purpose)
        if model is not None:
            return cls.runtime_for(model)
        provider = default_provider(purpose)
        if provider is None:
            raise ValueError("LIVE_VOICE_OFF" if purpose == "voice" else "AI_PROVIDER_NOT_CONFIGURED")
        key = environment_key(provider)
        if not key:
            raise ValueError("AI_PROVIDER_NOT_CONFIGURED")
        return RuntimeModelConfig(provider, default_model(purpose, provider), key)

    @staticmethod
    async def saved_key(db: AsyncSession, purpose: str, provider: str) -> Optional[str]:
        """The key a section saved for a provider (never sent to the browser)."""
        ciphertext = (await db.execute(
            select(AiModelConfigModel.api_key_ciphertext)
            .where(AiModelConfigModel.purpose == purpose, AiModelConfigModel.provider == provider)
        )).scalar_one_or_none()
        return decrypt_key(ciphertext) if ciphertext else None

    @staticmethod
    async def save(db: AsyncSession, purpose: str, provider: str, model_id: str, api_key: Optional[str] = None,
                   **options) -> AiModelConfigModel:
        """Save a section's setting for one provider and use it. Without a new key, the key the
        section saved for that provider stays; with none at all, the server's key is used."""
        model = (await db.execute(
            select(AiModelConfigModel)
            .where(AiModelConfigModel.purpose == purpose, AiModelConfigModel.provider == provider)
        )).scalar_one_or_none()
        ciphertext = encrypt_key(api_key) if api_key else model.api_key_ciphertext if model else None
        if not ciphertext and not environment_key(provider):
            raise ValueError("API_KEY_REQUIRED")
        if model is None:
            model = AiModelConfigModel(purpose=purpose, provider=provider)
            db.add(model)
        model.model_id = model_id
        model.api_key_ciphertext = ciphertext
        model.reasoning_effort = options.get("reasoning_effort")
        model.reasoning_mode = options.get("reasoning_mode")
        model.thinking_level = options.get("thinking_level")
        model.thinking_budget = options.get("thinking_budget")
        # One setting in use per purpose: the others step aside first.
        await db.execute(update(AiModelConfigModel).where(AiModelConfigModel.purpose == purpose)
                         .values(is_active=False))
        model.is_active = True
        await db.commit()
        await db.refresh(model)
        return model

    @staticmethod
    async def switch_off(db: AsyncSession, purpose: str) -> None:
        """Stop using a section's settings (they stay saved for switching on again)."""
        await db.execute(update(AiModelConfigModel).where(AiModelConfigModel.purpose == purpose)
                         .values(is_active=False))
        await db.commit()
