"""Admin AI settings: one independent section per purpose, key secrecy, checks and provider protocol."""
import contextlib
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from jubran.application.ai import transcription_service
from jubran.application.ai.model_clients import OpenAIAgentChat
from jubran.application.ai.model_config_service import ModelConfigService, RuntimeModelConfig
from jubran.application.ai.semantic_retrieval import EmbeddingService
from jubran.infrastructure.db.models import EMBEDDING_DIMENSIONS, AiModelConfigModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit

API = "/api/v1/admin/ai-models"


async def sign_in_admin(client, db_session):
    await seed_database(db_session)
    await sign_in(client)


def section(settings, purpose):
    return next(item for item in settings["sections"] if item["purpose"] == purpose)


@pytest.mark.asyncio
async def test_each_purpose_has_its_own_settings_and_keys_stay_secret(client, db_session):
    assert (await client.get(API)).status_code == 401
    await sign_in_admin(client, db_session)
    saved = await client.put(f"{API}/chat", json={
        "provider": "gemini", "model_id": "gemini-test", "api_key": "gemini-chat-secret", "thinking_level": "high",
    })
    assert saved.status_code == 200 and saved.json()["in_use"] == {
        "source": "saved", "provider": "gemini", "model_id": "gemini-test", "key": "saved"}
    assert "gemini-chat-secret" not in saved.text
    stored = (await db_session.execute(select(AiModelConfigModel.api_key_ciphertext))).scalar_one()
    assert "gemini-chat-secret" not in stored
    chat = await ModelConfigService.runtime(db_session, "chat")
    assert (chat.model_id, chat.api_key, chat.thinking_level) == ("gemini-test", "gemini-chat-secret", "high")

    # Switching the chat to OpenAI keeps the Gemini setting (and its key) for switching back.
    await client.put(f"{API}/chat", json={"provider": "openai", "model_id": "gpt-test", "api_key": "openai-secret",
                                          "reasoning_effort": "low"})
    assert (await ModelConfigService.runtime(db_session, "chat")).api_key == "openai-secret"
    back = await client.put(f"{API}/chat", json={"provider": "gemini", "model_id": "gemini-test-2"})
    assert back.json()["saved"]["gemini"]["has_api_key"] and back.json()["saved"]["openai"]["has_api_key"]
    assert (await ModelConfigService.runtime(db_session, "chat")).api_key == "gemini-chat-secret"

    # The other sections don't borrow the chat's keys: each gets its own.
    listed = (await client.get(API)).json()
    assert [item["purpose"] for item in listed["sections"]] == ["chat", "embedding", "transcription", "voice"]
    assert section(listed, "transcription")["in_use"]["source"] == "none"
    assert section(listed, "voice")["in_use"]["source"] == "none"
    assert "secret" not in json.dumps(listed)
    with pytest.raises(ValueError):
        await ModelConfigService.runtime(db_session, "transcription")
    await client.put(f"{API}/transcription", json={"provider": "openai", "model_id": "whisper-1",
                                                   "api_key": "stt-secret"})
    stt = await ModelConfigService.runtime(db_session, "transcription")
    assert (stt.provider, stt.model_id, stt.api_key) == ("openai", "whisper-1", "stt-secret")
    assert (await ModelConfigService.runtime(db_session, "chat")).api_key == "gemini-chat-secret"


@pytest.mark.asyncio
async def test_live_voice_is_off_until_switched_on(client, db_session, new_browser):
    admin = new_browser()
    await sign_in_admin(admin, db_session)
    await admin.put(f"{API}/chat", json={"provider": "gemini", "model_id": "gemini-chat", "api_key": "k"})
    await start_visit(client, db_session, "T4")
    voice = (await client.post("/api/v1/assistant/voice/session")).json()
    assert (voice["mode"], voice["reason"]) == ("turns", "LIVE_VOICE_OFF")  # a Gemini chat no longer turns it on

    on = await admin.put(f"{API}/voice", json={"provider": "openai", "model_id": "gpt-live-1", "api_key": "v"})
    assert on.json()["in_use"]["source"] == "saved"
    assert (await client.post("/api/v1/assistant/voice/session")).json()["mode"] == "live"
    off = await admin.delete(f"{API}/voice")
    assert off.json()["in_use"]["source"] == "none" and off.json()["saved"]["openai"]["has_api_key"]
    assert (await client.post("/api/v1/assistant/voice/session")).json()["mode"] == "turns"
    assert (await admin.delete(f"{API}/chat")).status_code == 422  # only live voice can be switched off


@pytest.mark.asyncio
async def test_an_old_gemini_live_voice_setting_is_ignored_and_gpt_live_is_offered(client, db_session):
    await sign_in_admin(client, db_session)
    # Saved (and switched on) back when live voice ran on Gemini.
    db_session.add(AiModelConfigModel(purpose="voice", provider="gemini", model_id="gemini-3.8-live", is_active=True))
    await db_session.commit()
    voice = section((await client.get(API)).json(), "voice")
    assert voice["in_use"]["source"] == "none" and voice["providers"] == ["openai"]
    assert voice["saved"] == {} and voice["suggested_models"] == {"openai": "gpt-live-1"}
    with pytest.raises(ValueError):
        await ModelConfigService.runtime(db_session, "voice")  # it never runs: the call stays step by step

    on = await client.put(f"{API}/voice", json={"provider": "openai", "model_id": "gpt-live-1", "api_key": "live-key"})
    assert on.status_code == 200 and on.json()["in_use"]["source"] == "saved"
    runtime = await ModelConfigService.runtime(db_session, "voice")
    assert (runtime.provider, runtime.model_id, runtime.api_key) == ("openai", "gpt-live-1", "live-key")


@pytest.mark.asyncio
async def test_saving_keeps_the_saved_key_and_checks_the_options(client, db_session):
    await sign_in_admin(client, db_session)
    missing = await client.put(f"{API}/chat", json={"provider": "openai", "model_id": "gpt-a"})
    assert missing.status_code == 400 and missing.json()["detail"]["error"]["code"] == "API_KEY_REQUIRED"
    await client.put(f"{API}/chat", json={"provider": "openai", "model_id": "gpt-a", "api_key": "stored-secret"})
    edited = await client.put(f"{API}/chat", json={"provider": "openai", "model_id": "gpt-b",
                                                   "reasoning_effort": "medium"})
    assert edited.status_code == 200
    chat = await ModelConfigService.runtime(db_session, "chat")
    assert (chat.model_id, chat.api_key, chat.reasoning_effort) == ("gpt-b", "stored-secret", "medium")
    assert len((await db_session.execute(select(AiModelConfigModel))).scalars().all()) == 1

    wrong = [
        ("chat", {"provider": "gemini", "model_id": "g", "reasoning_effort": "high", "api_key": "k"}),
        ("voice", {"provider": "gemini", "model_id": "gemini-live", "api_key": "k"}),  # live voice is GPT-Live only
        ("embedding", {"provider": "gemini", "model_id": "gemini-embedding-001", "thinking_level": "low",
                       "api_key": "k"}),
    ]
    for purpose, form in wrong:
        assert (await client.put(f"{API}/{purpose}", json=form)).status_code == 422
    assert (await client.put(f"{API}/usage", json={"provider": "openai", "model_id": "x"})).status_code == 422


@pytest.mark.asyncio
async def test_connection_checks_are_admin_only_and_use_the_right_model(client, db_session, monkeypatch, new_browser):
    await sign_in_admin(client, db_session)
    await client.put(f"{API}/chat", json={"provider": "openai", "model_id": "gpt-saved", "api_key": "saved-secret"})
    anonymous = new_browser()
    assert (await anonymous.post(f"{API}/chat/test", json={"provider": "openai", "model_id": "x"})).status_code == 401

    class FakeChat:
        def __init__(self, config, history, instruction):
            assert (config.model_id, config.api_key) == ("gpt-check", "saved-secret")  # the saved key is used

        async def send_message(self, message, context=None):
            return SimpleNamespace(text="", function_calls=[SimpleNamespace(name="submit_order")])

        async def close(self):
            return None

    monkeypatch.setattr("jubran.interfaces.http.routers.ai_models.OpenAIAgentChat", FakeChat)
    checked = await client.post(f"{API}/chat/test", json={"provider": "openai", "model_id": "gpt-check"})
    assert checked.status_code == 200 and checked.json()["success"] is True
    assert (await ModelConfigService.runtime(db_session, "chat")).model_id == "gpt-saved"  # a check saves nothing

    vectors = {"size": EMBEDDING_DIMENSIONS}

    async def gemini_vectors(api_key, model, texts, task_type):
        assert (api_key, model) == ("embed-key", "gemini-embedding-001")
        return [[0.1] * vectors["size"] for _ in texts]

    monkeypatch.setattr(EmbeddingService, "_gemini_vectors", staticmethod(gemini_vectors))
    form = {"provider": "gemini", "model_id": "gemini-embedding-001", "api_key": "embed-key"}
    assert (await client.post(f"{API}/embedding/test", json=form)).status_code == 200
    vectors["size"] = 3072
    wrong_size = await client.post(f"{API}/embedding/test", json=form)
    assert wrong_size.status_code == 502 and wrong_size.json()["detail"]["error"]["code"] == "WRONG_DIMENSIONS"

    async def rejected(api_key, model, audio, mime_type):
        request = httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")
        raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))

    monkeypatch.setattr(transcription_service, "_openai_transcript", rejected)
    failed = await client.post(f"{API}/transcription/test",
                               json={"provider": "openai", "model_id": "whisper-1", "api_key": "bad-key"})
    error = failed.json()["detail"]["error"]
    assert failed.status_code == 502 and error["code"] == "AUTH_FAILED" and error["details"]["message_en"]

    connected = []

    class FakeLive:
        def __init__(self, api_key, model):
            connected.append((api_key, model))

        @contextlib.asynccontextmanager
        async def connect(self, instruction, history):
            yield None

    monkeypatch.setattr("jubran.interfaces.http.routers.ai_models.GptLiveProvider", FakeLive)
    live = await client.post(f"{API}/voice/test", json={"provider": "openai", "model_id": "gpt-live-1",
                                                        "api_key": "live-key"})
    assert live.status_code == 200 and connected == [("live-key", "gpt-live-1")]


@pytest.mark.asyncio
async def test_openai_responses_tool_round_uses_selected_reasoning():
    requests = []

    def responder(request: httpx.Request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.headers["authorization"] == "Bearer test-key"
        if len(requests) == 1:
            return httpx.Response(200, json={"output": [{"type": "function_call", "id": "fc-1",
                "call_id": "call-1", "name": "search_knowledge", "arguments": "{\"query\":\"menu categories\"}"}]})
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "هذه أقسام القائمة."}]}]})

    chat = OpenAIAgentChat(RuntimeModelConfig("openai", "gpt-test", "test-key",
                                                reasoning_effort="high"), [], "system instructions")
    await chat._client.aclose()
    chat._client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    try:
        first = await chat.send_message("شو عندكم؟")
        assert first.function_calls[0].name == "search_knowledge"
        final = await chat.send_message([{"name": "search_knowledge", "call_id": "call-1",
                                          "response": {"matches": ["الأطباق"]}}])
        assert final.text == "هذه أقسام القائمة."
        assert requests[0]["model"] == "gpt-test"
        assert requests[0]["reasoning"] == {"effort": "high"}
        assert any(tool["name"] == "submit_order" for tool in requests[0]["tools"])
        assert requests[1]["input"][-1]["call_id"] == "call-1"
    finally:
        await chat.close()
