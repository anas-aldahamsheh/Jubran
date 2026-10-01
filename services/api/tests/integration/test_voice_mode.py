"""Voice bootstrap: which mode the web app uses, without exposing provider details."""
import pytest

from jubran.application.ai.model_config_service import encrypt_key
from jubran.infrastructure.db.models import AiModelConfigModel
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit


async def use(db_session, purpose, provider):
    db_session.add(AiModelConfigModel(purpose=purpose, provider=provider, model_id=f"{provider}-{purpose}-model",
                                      api_key_ciphertext=encrypt_key("key"), is_active=True))
    await db_session.commit()


@pytest.mark.asyncio
async def test_voice_bootstrap_requires_customer_session(client):
    client.cookies.clear()
    response = await client.post("/api/v1/assistant/voice/session")
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("settings_saved, mode, reason", [
    ([], "turns", "LIVE_VOICE_OFF"),
    ([("chat", "gemini")], "turns", "LIVE_VOICE_OFF"),  # a Gemini chat model doesn't switch live voice on
    ([("chat", "gemini"), ("voice", "openai")], "live", None),
])
async def test_voice_mode_follows_the_live_voice_section(client, db_session, settings_saved, mode, reason):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    for purpose, provider in settings_saved:
        await use(db_session, purpose, provider)
    data = (await client.post("/api/v1/assistant/voice/session")).json()
    assert (data["mode"], data["reason"], data["table_number"]) == (mode, reason, "T4")
    assert data["live_socket_path"] == ("/ws/assistant/voice" if mode == "live" else None)
    # No keys, tokens or model names reach the browser.
    assert not {"ephemeral_token", "provider", "model", "api_key"} & set(data)


@pytest.mark.asyncio
async def test_no_public_endpoint_reveals_the_ai_provider(client):
    # The old unauthenticated /voice/status told anyone which provider and model were in use.
    assert (await client.get("/api/v1/assistant/voice/status")).status_code in (404, 405)
