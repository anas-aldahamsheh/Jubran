"""Audio transcription with the speech-to-text model of the admin settings; never fabricate a transcript."""
import logging
from typing import Optional

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.ai.model_config_service import ModelConfigService
from jubran.settings import settings

logger = logging.getLogger(__name__)

from jubran.interfaces.http.body_limits import MAX_AUDIO_UPLOAD_BYTES

# Restaurant words the recogniser should expect (spec: 03_AI/07_VOICE_AND_DICTATION.md).
VOCABULARY_HINT = "جبران، حمص بيروتي، متبل، فتة، فتوش، شنكليش، كبة حماتي، مسخن، صاجية جبران، شيش طاووق، شقف، كباب خشخاش، كنافة، عثملية، أم علي، كرك، آيس تي، موهيتو، أرجيلة"


def transcription_prompt(language: Optional[str]) -> str:
    spoken = "English" if language == "en" else "Arabic (Jordanian dialect), possibly mixed with English"
    return (f"Transcribe this restaurant guest's speech exactly as spoken. It is mostly {spoken}. "
            f"Menu words that may occur: {VOCABULARY_HINT}. Return only the spoken words, no notes.")


def detect_audio_type(data: bytes) -> Optional[str]:
    """The recording's real format from its first bytes (what browsers record), or None."""
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"              # WebM / Matroska (Chrome, Firefox, Edge)
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[4:8] == b"ftyp":
        return "audio/mp4"               # MP4 / M4A (Safari)
    if data.startswith(b"ID3") or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return None


def _audio_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


async def _openai_transcript(api_key: str, model: str, audio_bytes: bytes, mime_type: str) -> str:
    import httpx
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (f"audio.{mime_type.split('/')[1]}", audio_bytes, mime_type)},
            # No forced language: guests often mix Arabic and English.
            data={"model": model, "prompt": VOCABULARY_HINT},
        )
        response.raise_for_status()
        return (response.json().get("text") or "").strip()


async def _gemini_transcript(api_key: str, model: str, audio_bytes: bytes, mime_type: str,
                             language: Optional[str]) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=audio_bytes, mime_type=mime_type), transcription_prompt(language)],
        )
        return (response.text or "").strip()
    finally:
        await client.aio.aclose()
        client.close()


async def transcript_from(provider: str, api_key: str, model: str, audio_bytes: bytes, mime_type: str,
                          language: Optional[str] = None) -> str:
    if provider == "openai":
        return await _openai_transcript(api_key, model, audio_bytes, mime_type)
    return await _gemini_transcript(api_key, model, audio_bytes, mime_type, language)


def standard_models(provider: str) -> list[str]:
    """Long-standing models of a provider, tried when the chosen one is rejected."""
    if provider == "openai":
        return [settings.OPENAI_TRANSCRIBE_MODEL, "whisper-1"]
    return [settings.GEMINI_TRANSCRIBE_MODEL, settings.GEMINI_TEXT_MODEL]


class SpeechTranscriptionService:
    @staticmethod
    async def transcribe_audio(file: UploadFile, db: AsyncSession, language: Optional[str] = None) -> str:
        """``language`` is the guest's interface language ("ar" / "en"); speech may still be mixed."""
        # Never more than the limit in memory (the request size is also capped before parsing).
        audio_bytes = await file.read(MAX_AUDIO_UPLOAD_BYTES + 1)
        if not audio_bytes:
            raise _audio_error(status.HTTP_400_BAD_REQUEST, "EMPTY_AUDIO", "ملف الصوت فارغ.")
        if len(audio_bytes) > MAX_AUDIO_UPLOAD_BYTES:
            raise _audio_error(status.HTTP_413_CONTENT_TOO_LARGE, "AUDIO_TOO_LARGE",
                               f"التسجيل الصوتي أطول من المسموح (حتى {MAX_AUDIO_UPLOAD_BYTES // (1024 * 1024)} ميغابايت).")
        # The bytes decide the format, not the name or declared type the browser sent.
        mime_type = detect_audio_type(audio_bytes)
        if mime_type is None:
            raise _audio_error(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "UNSUPPORTED_AUDIO",
                               "صيغة التسجيل غير مدعومة (المدعوم: WebM وOGG وMP4/M4A وWAV وMP3).")
        try:
            config = await ModelConfigService.runtime(db, "transcription")
        except ValueError as exc:
            raise HTTPException(status_code=503,
                                detail={"error": {"code": "AI_PROVIDER_NOT_CONFIGURED", "message": "خدمة تحويل الصوت غير متاحة."}}) from exc

        # The model chosen in the settings first; if the provider rejects it (for example a
        # model name that does not exist on this account), a long-standing model of the same
        # provider, so dictation keeps working and the log says what to fix.
        candidates = list(dict.fromkeys([config.model_id, *standard_models(config.provider)]))

        try:
            transcript = ""
            last_error: Optional[Exception] = None
            for model in candidates:
                try:
                    transcript = await transcript_from(config.provider, config.api_key, model, audio_bytes,
                                                       mime_type, language)
                    break
                except Exception as exc:  # try the next model, keep the reason
                    last_error = exc
                    logger.warning("Transcription with model %r failed (%s); check the speech-to-text "
                                   "section in Admin > Settings.", model, type(exc).__name__)
            else:
                raise last_error or ValueError("No transcription model")
            if not transcript:
                raise ValueError("Empty transcription")
            return transcript
        except Exception as exc:
            logger.exception("Speech transcription failed")
            raise HTTPException(status_code=503,
                                detail={"error": {"code": "TRANSCRIPTION_FAILED", "message": "تعذر تحويل التسجيل إلى نص."}}) from exc
