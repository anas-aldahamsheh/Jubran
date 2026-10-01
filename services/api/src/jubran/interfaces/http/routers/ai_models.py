"""Administrator-only AI settings: one section per purpose (chat, menu search, speech to text, live voice)."""
import io
import logging
import re
import wave
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.ai import transcription_service
from jubran.application.ai.live_voice import GptLiveProvider
from jubran.application.ai.model_clients import GeminiAgentChat, OpenAIAgentChat
from jubran.application.ai.model_config_service import (
    PROVIDERS, PURPOSES, ModelConfigService, RuntimeModelConfig, default_model, default_provider, environment_key,
)
from jubran.application.ai.provider_errors import classify_provider_failure
from jubran.application.ai.semantic_retrieval import (
    EmbeddingService, SemanticKnowledgeService, SemanticRetrievalError,
)
from jubran.application.ai.usage import usage_summary
from jubran.infrastructure.db.models import EMBEDDING_DIMENSIONS, AiModelConfigModel, UserModel
from jubran.infrastructure.db.session import get_db_session
from jubran.interfaces.http.dependencies import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/ai-models", tags=["Admin AI Models"])

Purpose = Literal["chat", "embedding", "transcription", "voice"]


class SectionInput(BaseModel):
    """A section's form: provider, model and (optionally) a new key; thinking options are for chat."""
    provider: Literal["openai", "gemini"]
    model_id: str = Field(min_length=1, max_length=150)
    api_key: Optional[str] = Field(default=None, max_length=512)
    reasoning_effort: Optional[Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]] = None
    reasoning_mode: Optional[Literal["standard", "pro"]] = None
    thinking_level: Optional[Literal["minimal", "low", "medium", "high"]] = None
    thinking_budget: Optional[int] = Field(default=None, ge=-1, le=32768)

    @model_validator(mode="after")
    def check_provider_fields(self):
        self.model_id = self.model_id.strip()
        if not self.model_id:
            raise ValueError("Model ID is required")
        if self.api_key is not None:
            self.api_key = self.api_key.strip() or None
        if self.provider == "openai" and (self.thinking_level is not None or self.thinking_budget is not None):
            raise ValueError("Gemini thinking fields cannot be used with OpenAI")
        if self.provider == "gemini" and (self.reasoning_effort is not None or self.reasoning_mode is not None):
            raise ValueError("OpenAI reasoning fields cannot be used with Gemini")
        if self.thinking_level is not None and self.thinking_budget is not None:
            raise ValueError("Choose Gemini thinking level or token budget, not both")
        return self

    def options(self) -> dict:
        return {"reasoning_effort": self.reasoning_effort, "reasoning_mode": self.reasoning_mode,
                "thinking_level": self.thinking_level, "thinking_budget": self.thinking_budget}


def _error(status: int, code: str, arabic: str, english: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": arabic,
                                                               "details": {"message_en": english}}})


def _missing_key() -> HTTPException:
    return _error(400, "API_KEY_REQUIRED", "أدخل مفتاح API لهذا المزوّد (لا يوجد مفتاح محفوظ ولا مفتاح على الخادم).",
                  "Enter an API key for this provider (none is saved and the server has none).")


def _checked(purpose: str, req: SectionInput) -> SectionInput:
    if req.provider not in PROVIDERS[purpose]:
        raise _error(422, "PROVIDER_NOT_SUPPORTED", "هذا المزوّد غير متاح لهذا القسم.",
                     "This provider isn't available for this section.")
    if purpose != "chat" and any(value is not None for value in req.options().values()):
        raise _error(422, "OPTIONS_NOT_SUPPORTED", "خيارات التفكير خاصة بقسم المحادثة.",
                     "Thinking options belong to the chat section.")
    return req


def _saved_view(model: AiModelConfigModel) -> dict:
    return {"model_id": model.model_id, "has_api_key": bool(model.api_key_ciphertext), "is_active": model.is_active,
            "reasoning_effort": model.reasoning_effort, "reasoning_mode": model.reasoning_mode,
            "thinking_level": model.thinking_level, "thinking_budget": model.thinking_budget,
            "updated_at": model.updated_at}


async def _section(db: AsyncSession, purpose: str) -> dict:
    """One section as the settings page shows it (keys are never included)."""
    saved = await ModelConfigService.saved(db, purpose)
    active = next((model for model in saved if model.is_active), None)
    if active is not None:
        source, provider, model_id = "saved", active.provider, active.model_id
        key = "saved" if active.api_key_ciphertext else "server" if environment_key(provider) else None
    else:
        provider = default_provider(purpose)
        source = "default" if provider else "none"
        model_id = default_model(purpose, provider) if provider else None
        key = "server" if provider and environment_key(provider) else None
    view = {
        "purpose": purpose,
        "providers": list(PROVIDERS[purpose]),
        "suggested_models": {name: default_model(purpose, name) for name in PROVIDERS[purpose]},
        "in_use": {"source": source, "provider": provider, "model_id": model_id, "key": key},
        "saved": {model.provider: _saved_view(model) for model in saved},
    }
    if purpose == "embedding":
        view["indexed_documents"] = await SemanticKnowledgeService.indexed_count(db)
    return view


@router.get("")
async def ai_settings(admin: UserModel = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    return {"sections": [await _section(db, purpose) for purpose in PURPOSES],
            "server_keys": {provider: bool(environment_key(provider)) for provider in ("openai", "gemini")}}


@router.get("/usage")
async def assistant_usage(days: int = 7, admin: UserModel = Depends(require_admin),
                          db: AsyncSession = Depends(get_db_session)):
    """Tokens and estimated cost of the assistant per model over the last days."""
    return await usage_summary(db, max(1, min(days, 90)))


@router.put("/{purpose}")
async def save_section(purpose: Purpose, req: SectionInput, admin: UserModel = Depends(require_admin),
                       db: AsyncSession = Depends(get_db_session)):
    """Save a section's setting and use it (a key left empty keeps the one saved for that provider)."""
    _checked(purpose, req)
    try:
        await ModelConfigService.save(db, purpose, req.provider, req.model_id, req.api_key, **req.options())
    except ValueError as exc:
        raise _missing_key() from exc
    if purpose == "embedding":
        # Re-index the menu for the new provider or model now; if it is unreachable, the next search retries.
        await SemanticKnowledgeService.refresh_after_admin_change(db)
    return await _section(db, purpose)


@router.delete("/{purpose}")
async def switch_off(purpose: Literal["voice"], admin: UserModel = Depends(require_admin),
                     db: AsyncSession = Depends(get_db_session)):
    """Live voice off: the voice button then works message by message (speech to text, then chat)."""
    await ModelConfigService.switch_off(db, purpose)
    return await _section(db, purpose)


# ---------------------------------------------------------------------------
# Connection checks: the form's values against the provider, before or after saving
# ---------------------------------------------------------------------------

CHECK_INSTRUCTION = "Reply briefly to the administrator's connection check."

# What the administrator reads when a check fails: the likely cause in plain words
# (the provider's own message goes to the server log).
CHECK_FAILURES = {
    "AUTH_FAILED": ("المفتاح غير صحيح أو غير مفعّل.", "The API key is wrong or inactive."),
    "ACCESS_DENIED": ("هذا المفتاح لا يملك صلاحية على هذا الموديل.", "This key has no access to this model."),
    "MODEL_NOT_FOUND": ("اسم الموديل غير موجود على هذا الحساب، أو لا يدعم هذا الاستخدام.",
                        "This model doesn't exist on this account, or doesn't support this use."),
    "QUOTA_EXCEEDED": ("انتهت حصة الاستخدام لهذا المفتاح (أو يحتاج الحساب إلى تفعيل الدفع).",
                       "This key is out of quota (or the account needs billing)."),
    "RATE_LIMITED": ("المزوّد مزدحم الآن. حاول بعد دقيقة.", "The provider is rate limiting. Try again in a minute."),
    "PROVIDER_UNAVAILABLE": ("المزوّد غير متاح الآن، أو لا يوجد اتصال بالإنترنت.",
                             "The provider is unavailable, or there's no internet connection."),
    "REQUEST_REJECTED": ("رفض المزوّد الطلب. تأكد من اسم الموديل وخياراته.",
                         "The provider rejected the request. Check the model name and its options."),
    "WRONG_DIMENSIONS": (f"هذا الموديل لا يعطي {EMBEDDING_DIMENSIONS} رقماً لكل نص، والبحث في المنيو يحتاجها.",
                         f"This model doesn't return {EMBEDDING_DIMENSIONS} numbers per text, which menu search needs."),
    "EMPTY_RESPONSE": ("لم يُرجع الموديل أي رد.", "The model returned nothing."),
    "FAILED": ("فشل الفحص. السبب الدقيق في سجل الخادم.", "The check failed; the exact reason is in the server log."),
}
_SECRETS = re.compile(r"(key=)[^&\s]+|sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}")


def _silence(seconds: float = 0.5, rate: int = 16000) -> bytes:
    """A short silent WAV recording, enough for a speech-to-text model to answer."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


async def _check_chat(config: RuntimeModelConfig) -> None:
    # Tools the model may ask for are never run here.
    chat = (OpenAIAgentChat if config.provider == "openai" else GeminiAgentChat)(config, [], CHECK_INSTRUCTION)
    try:
        response = await chat.send_message("Connection check. Reply briefly.")
        if not (response.text or response.function_calls):
            raise ValueError("EMPTY_RESPONSE")
    finally:
        await chat.close()


async def _check_embedding(config: RuntimeModelConfig) -> None:
    await EmbeddingService.provider_vectors(config.provider, config.api_key, config.model_id, ["حمص"],
                                            "RETRIEVAL_QUERY")


async def _check_transcription(config: RuntimeModelConfig) -> None:
    # Silence may come back as an empty transcript; the model answering is what counts.
    await transcription_service.transcript_from(config.provider, config.api_key, config.model_id, _silence(),
                                                "audio/wav")


async def _check_voice(config: RuntimeModelConfig) -> None:
    # Starting a GPT-Live session proves the key, the model and the account's access.
    async with GptLiveProvider(config.api_key, config.model_id).connect(CHECK_INSTRUCTION, []):
        pass


CHECKS = {"chat": _check_chat, "embedding": _check_embedding,
          "transcription": _check_transcription, "voice": _check_voice}


def _failure_code(exc: Exception) -> str:
    if str(exc) == "EMPTY_RESPONSE":
        return "EMPTY_RESPONSE"
    code = exc.code if isinstance(exc, SemanticRetrievalError) else classify_provider_failure(exc, "chat")
    if code == "INVALID_EMBEDDING_RESPONSE":
        return "WRONG_DIMENSIONS"
    return code.split("_", 1)[1] if code.startswith(("AI_", "EMBEDDING_")) else code


@router.post("/{purpose}/test")
async def test_section(purpose: Purpose, req: SectionInput, admin: UserModel = Depends(require_admin),
                       db: AsyncSession = Depends(get_db_session)):
    """Check the form's provider, model and key (a key left empty: the saved one, else the server's)."""
    _checked(purpose, req)
    try:
        key = req.api_key or await ModelConfigService.saved_key(db, purpose, req.provider)
    except ValueError:
        key = None  # a saved key that can no longer be decrypted: a new one is needed
    key = key or environment_key(req.provider)
    if not key:
        raise _missing_key()
    config = RuntimeModelConfig(req.provider, req.model_id, key, **req.options())
    try:
        await CHECKS[purpose](config)
    except Exception as exc:
        code = _failure_code(exc)
        logger.warning("AI settings check failed (%s, %s %s): %s %s", purpose, config.provider, config.model_id,
                       code, _SECRETS.sub(lambda match: (match.group(1) or "") + "***", str(exc))[:500])
        arabic, english = CHECK_FAILURES.get(code, CHECK_FAILURES["FAILED"])
        raise _error(502, code, arabic, english) from exc
    return {"success": True, "provider": config.provider, "model_id": config.model_id}
