"""Claude (Anthropic) as the chat's model: chosen in Admin > Settings next to OpenAI and Gemini,
for the chat only, speaking Anthropic's Messages API with the same tools and turn interface."""
import json
from types import SimpleNamespace

import httpx
import pytest

from jubran.application.ai.model_clients import AnthropicAgentChat, agent_chat, close_shared_http_client
from jubran.application.ai.model_config_service import ModelConfigService, RuntimeModelConfig
from jubran.application.ai.provider_errors import classify_provider_failure
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in

API = "/api/v1/admin/ai-models"
CLAUDE = RuntimeModelConfig("anthropic", "claude-haiku-5-5", "test-key")
CLAUDE_THINKING_HARD = RuntimeModelConfig("anthropic", "claude-haiku-5-5", "test-key", reasoning_effort="high")


def claude_chat(responder, history=()):
    return AnthropicAgentChat(CLAUDE, list(history), "system instructions",
                              client=httpx.AsyncClient(transport=httpx.MockTransport(responder)))


def sse(*events):
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


@pytest.mark.asyncio
async def test_the_chat_section_offers_claude_and_only_the_chat(client, db_session, monkeypatch):
    await seed_database(db_session)
    await sign_in(client)
    listed = (await client.get(API)).json()
    chat = next(item for item in listed["sections"] if item["purpose"] == "chat")
    assert chat["providers"] == ["openai", "gemini", "anthropic"]
    assert chat["suggested_models"]["anthropic"] == "claude-haiku-5-5"
    assert listed["server_keys"]["anthropic"] is False
    assert all("anthropic" not in item["providers"] for item in listed["sections"] if item["purpose"] != "chat")

    saved = await client.put(f"{API}/chat", json={"provider": "anthropic", "model_id": "claude-sonnet-5-5",
                                                  "api_key": "sk-ant-secret"})
    assert saved.status_code == 200 and saved.json()["in_use"] == {
        "source": "saved", "provider": "anthropic", "model_id": "claude-sonnet-5-5", "key": "saved"}
    assert "sk-ant-secret" not in saved.text
    runtime = await ModelConfigService.runtime(db_session, "chat")
    assert (runtime.provider, runtime.model_id, runtime.api_key) == ("anthropic", "claude-sonnet-5-5", "sk-ant-secret")
    assert isinstance(agent_chat(runtime, [], "instructions"), AnthropicAgentChat)

    # Claude has no menu search, speech to text or live voice; its one thinking option is the effort level.
    for purpose in ("embedding", "transcription", "voice"):
        refused = await client.put(f"{API}/{purpose}", json={"provider": "anthropic", "model_id": "claude-haiku-5-5",
                                                             "api_key": "k"})
        assert refused.status_code == 422
    for wrong in ({"reasoning_effort": "xhigh"}, {"thinking_level": "high"}, {"reasoning_mode": "pro"}):
        refused = await client.put(f"{API}/chat", json={"provider": "anthropic", "model_id": "claude-haiku-5-5", **wrong})
        assert refused.status_code == 422
    effort = await client.put(f"{API}/chat", json={"provider": "anthropic", "model_id": "claude-sonnet-5-5",
                                                   "reasoning_effort": "high"})
    assert effort.status_code == 200 and effort.json()["saved"]["anthropic"]["reasoning_effort"] == "high"
    assert (await ModelConfigService.runtime(db_session, "chat")).reasoning_effort == "high"

    class FakeChat:
        def __init__(self, config, history, instruction):
            assert (config.provider, config.model_id, config.api_key) == ("anthropic", "claude-check", "sk-ant-secret")

        async def send_message(self, message, context=None):
            return SimpleNamespace(text="أهلاً", function_calls=[])

        async def close(self):
            return None

    monkeypatch.setattr("jubran.interfaces.http.routers.ai_models.AnthropicAgentChat", FakeChat)
    checked = await client.post(f"{API}/chat/test", json={"provider": "anthropic", "model_id": "claude-check"})
    assert checked.status_code == 200 and checked.json()["success"] is True


@pytest.mark.asyncio
async def test_a_claude_tool_round_keeps_the_conversation_and_caches_it():
    requests = []

    def responder(request: httpx.Request):
        assert request.url == httpx.URL(AnthropicAgentChat.URL)
        assert request.headers["x-api-key"] == "test-key" and request.headers["anthropic-version"] == "2023-06-01"
        payload = json.loads(request.content)
        requests.append(payload)
        if "temperature" in payload:
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "temperature is not supported for this model"}})
        if len(requests) == 2:
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "لحظة بشوف."},
                            {"type": "tool_use", "id": "toolu_1", "name": "search_knowledge",
                             "input": {"query": "حمص"}}],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 50, "cache_creation_input_tokens": 3000, "output_tokens": 20}})
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "عنا حمص بـ 2 دينار."}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 40, "cache_read_input_tokens": 3050, "output_tokens": 10}})

    history = [{"role": "assistant", "content": "أهلاً! كيف بقدر أساعدك؟"},
               {"role": "user", "content": "مرحبا"}, {"role": "assistant", "content": "أهلين "}]
    chat = claude_chat(responder, history)
    try:
        first = await chat.send_message("شو عندكم حمص؟", context={"basket": []})
        assert [(call.name, call.args, call.call_id) for call in first.function_calls] == [
            ("search_knowledge", {"query": "حمص"}, "toolu_1")]
        final = await chat.send_message([{"name": "search_knowledge", "call_id": "toolu_1",
                                          "response": {"matches": ["حمص"]}}])
        assert final.text == "عنا حمص بـ 2 دينار." and final.function_calls == []
    finally:
        await chat.close()

    # A model that refuses temperature is asked again without it, and never gets it again.
    assert "temperature" in requests[0] and all("temperature" not in payload for payload in requests[1:])
    first_request, tool_round = requests[1], requests[2]
    assert first_request["model"] == "claude-haiku-5-5" and first_request["max_tokens"] > 0
    assert first_request["system"] == [{"type": "text", "text": "system instructions",
                                        "cache_control": {"type": "ephemeral"}}]
    assert any(tool["name"] == "submit_order" and tool["input_schema"]["type"] == "object"
               for tool in first_request["tools"])
    # The conversation opens with the guest; their message comes after the turn's facts.
    messages = first_request["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == [{"type": "text", "text": "أهلين"}]
    facts, words = messages[2]["content"]
    assert facts["text"].startswith("[Turn context") and json.loads(facts["text"].split("\n", 1)[1]) == {"basket": []}
    assert words == {"type": "text", "text": "شو عندكم حمص؟", "cache_control": {"type": "ephemeral"}}
    # The tool's answer follows Claude's turn as it came; only the newest block carries the cache mark.
    assert tool_round["messages"][3]["content"][1]["type"] == "tool_use"
    result = tool_round["messages"][4]["content"][0]
    assert (result["type"], result["tool_use_id"]) == ("tool_result", "toolu_1")
    assert json.loads(result["content"]) == {"matches": ["حمص"]} and "cache_control" in result
    marks = json.dumps(tool_round["messages"]).count("cache_control")
    assert marks == 1
    # Cached tokens count as input, at their own (cached) rate.
    assert (chat.usage.input_tokens, chat.usage.cached_input_tokens, chat.usage.output_tokens) == (
        50 + 3000 + 40 + 3050, 3050, 30)


@pytest.mark.asyncio
async def test_claude_text_is_passed_on_while_it_is_written_and_a_busy_moment_is_retried(monkeypatch):
    monkeypatch.setattr(AnthropicAgentChat, "RETRY_DELAYS", (0, 0))
    answers = iter([
        httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
        sse({"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "بس "}},
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
        sse({"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "بضيف "}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "الحمص."}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1,
             "content_block": {"type": "tool_use", "id": "toolu_2", "name": "update_basket", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"op"}},
            {"type": "content_block_delta", "index": 1,
             "delta": {"type": "input_json_delta", "partial_json": "\": \"add\"}"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 15}},
            {"type": "message_stop"}),
    ])
    sent = []

    def responder(request: httpx.Request):
        sent.append(json.loads(request.content))
        return next(answers)

    chat = claude_chat(responder)
    pieces = []
    try:
        reply = await chat.send_message("ضيف حمص", on_text=pieces.append)
    finally:
        await chat.close()
    assert all(payload["stream"] is True for payload in sent) and len(sent) == 3
    # The interrupted attempt's words are taken back (None) before the reply is written again.
    assert pieces == [None, "بس ", None, "بضيف ", "الحمص."]
    assert reply.text == "بضيف الحمص."
    assert [(call.name, call.args, call.call_id) for call in reply.function_calls] == [
        ("update_basket", {"op": "add"}, "toolu_2")]
    assert chat.usage.output_tokens == 15


@pytest.mark.asyncio
async def test_claude_shares_the_pool_of_open_connections():
    first, second = AnthropicAgentChat(CLAUDE, [], "instructions"), AnthropicAgentChat(CLAUDE, [], "instructions")
    try:
        assert first._client is second._client
    finally:
        await close_shared_http_client()


def test_claude_failures_read_like_the_others():
    def failure(status, body):
        request = httpx.Request("POST", AnthropicAgentChat.URL)
        return httpx.HTTPStatusError(str(status), request=request,
                                     response=httpx.Response(status, request=request, json=body))

    overloaded = failure(529, {"error": {"type": "overloaded_error", "message": "Overloaded"}})
    no_credit = failure(400, {"error": {"type": "invalid_request_error",
                                        "message": "Your credit balance is too low to access the Anthropic API."}})
    bad_key = failure(401, {"error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    assert classify_provider_failure(overloaded, "chat") == "AI_RATE_LIMITED"
    assert classify_provider_failure(no_credit, "chat") == "AI_QUOTA_EXCEEDED"
    assert classify_provider_failure(bad_key, "chat") == "AI_AUTH_FAILED"


@pytest.mark.asyncio
async def test_the_chosen_thinking_level_is_sent_and_dropped_if_the_model_refuses_it():
    sent = []

    def responder(request: httpx.Request):
        payload = json.loads(request.content)
        sent.append(payload)
        if "output_config" in payload:
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "This model does not support the effort parameter."}})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "أهلاً"}], "usage": {}})

    chat = AnthropicAgentChat(CLAUDE_THINKING_HARD, [], "instructions",
                              client=httpx.AsyncClient(transport=httpx.MockTransport(responder)))
    try:
        assert (await chat.send_message("مرحبا")).text == "أهلاً"
        await chat.send_message("كمان")
    finally:
        await chat.close()
    assert sent[0]["output_config"] == {"effort": "high"}
    assert [("output_config" in payload) for payload in sent] == [True, False, False]
