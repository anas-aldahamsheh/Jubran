"""Dictation keeps working when the speech-to-text model name in the settings is wrong."""
import io

import pytest

from jubran.application.ai import transcription_service
from jubran.application.ai.model_config_service import encrypt_key
from jubran.infrastructure.db.models import AiModelConfigModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import start_visit

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


async def gemini_transcribes(db_session, model="gemini-transcribe-model"):
    db_session.add(AiModelConfigModel(purpose="transcription", provider="gemini", model_id=model,
                                      api_key_ciphertext=encrypt_key("key"), is_active=True))
    await db_session.commit()


@pytest.mark.asyncio
async def test_an_unknown_model_falls_back_to_the_providers_standard_model(client, db_session, monkeypatch):
    await seed_database(db_session)
    await gemini_transcribes(db_session, model="gemini-does-not-exist")
    await start_visit(client, db_session, "T2")
    tried = []

    async def fake(api_key, model, audio, mime_type, language):
        tried.append((model, mime_type))
        if model == "gemini-does-not-exist":
            raise RuntimeError("404 model not found")
        return "بدي حمص"

    monkeypatch.setattr(transcription_service, "_gemini_transcript", fake)
    response = await client.post("/api/v1/assistant/dictation", files={"audio": ("a.webm", io.BytesIO(WEBM), "audio/webm")})
    assert response.status_code == 200 and response.json()["transcript"] == "بدي حمص"
    assert tried == [("gemini-does-not-exist", "audio/webm"), (settings.GEMINI_TRANSCRIBE_MODEL, "audio/webm")]


@pytest.mark.asyncio
async def test_when_every_model_fails_the_guest_gets_a_clear_error(client, db_session, monkeypatch):
    await seed_database(db_session)
    await gemini_transcribes(db_session)
    await start_visit(client, db_session, "T2")

    async def broken(*args):
        raise RuntimeError("provider down")

    monkeypatch.setattr(transcription_service, "_gemini_transcript", broken)
    response = await client.post("/api/v1/assistant/dictation", files={"audio": ("a.webm", io.BytesIO(WEBM), "audio/webm")})
    assert response.status_code == 503 and response.json()["detail"]["error"]["code"] == "TRANSCRIPTION_FAILED"
