"""Voice mode: live streaming when the provider supports it, otherwise turn-by-turn.

Both use the exact model, tools and guards of text chat. Turn-by-turn voice sends
one recorded utterance (or typed text) per request; the transcript is produced
by the configured model on the server, never by the browser.
"""
from typing import Any, Dict, Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.ai.agent_service import AssistantService, interface_language
from jubran.application.ai.live_voice import LiveVoiceUnavailable, live_voice_provider
from jubran.application.ai.transcription_service import SpeechTranscriptionService
from jubran.domain.exceptions import BusinessRuleError

LIVE_SOCKET_PATH = "/ws/assistant/voice"


class VoiceAssistantService:
    @classmethod
    async def voice_capabilities(cls, db: AsyncSession, table_number: str) -> Dict[str, Any]:
        """Which voice mode this restaurant can offer right now (no provider details are exposed)."""
        try:
            await live_voice_provider(db)
            mode, reason = "live", None
        except LiveVoiceUnavailable as exc:
            mode, reason = "turns", str(exc) or None
        return {"mode": mode, "reason": reason, "table_number": table_number,
                "live_socket_path": LIVE_SOCKET_PATH if mode == "live" else None}

    @classmethod
    async def interact_speech_turn(cls, db: AsyncSession, customer_session_id: str,
                                   table_session_id: str, table_number: str,
                                   text_message: Optional[str] = None, audio_file: Optional[UploadFile] = None,
                                   language: Optional[str] = None) -> Dict[str, Any]:
        language = interface_language(language)
        user_text = (text_message or "").strip()
        if audio_file is not None and not user_text:
            user_text = await SpeechTranscriptionService.transcribe_audio(audio_file, db, language=language)
        if not user_text:
            raise BusinessRuleError("لم يتم التقاط أي صوت أو نص.", "EMPTY_VOICE_INPUT", 400)
        result = await AssistantService.chat(db, customer_session_id, table_session_id,
                                             table_number, user_text, language)
        return {"spoken_text": result["response"], "user_text": user_text,
                "language": language or "ar", "tool_calls": result["tool_calls"],
                "draft": result["draft"], "action": result["action"],
                "table_number": table_number}
