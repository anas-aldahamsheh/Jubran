"""The waiter adds a dish only when the guest gave its quantity (judged by the model from the
whole conversation), suggests only with dishes just added (never after "no thanks"), the chat
shows its steps and its reply as they happen, and model calls reuse open connections."""
import json

import httpx
import pytest

from jubran.application.ai.model_clients import OpenAIAgentChat, close_shared_http_client
from jubran.application.ai.model_config_service import RuntimeModelConfig
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import start_visit
from test_model_agent import ScriptedChat, model_call, model_response, script_model
from test_waiter_logic import chat, dish, send_order


@pytest.fixture
def debug_mode(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", True)  # tool results are returned for inspection


async def seated(client, db_session, table="T4"):
    await seed_database(db_session)
    await start_visit(client, db_session, table)


def add(product, quantity, gave=None, **extra):
    """The model adding a dish; ``gave`` is its own judgement that the guest gave this quantity."""
    operation = {"op": "add", "product_id": product.id, "quantity": quantity}
    if gave is not None:
        operation["guest_gave_quantity"] = gave
    return model_call("update_draft_order", operations=[operation], **extra)


def codes(reply):
    return [call["result"].get("error_code") for call in reply["tool_calls"]]


@pytest.mark.asyncio
async def test_a_dish_is_added_only_when_the_model_says_the_guest_gave_its_quantity(
        client, db_session, monkeypatch, debug_mode):
    await seated(client, db_session)
    juice = await dish(db_session, "عصير برتقال")
    script_model(monkeypatch,
        # No search first: the whole menu is in the model's instructions.
        [model_response(add(juice, 1)),  # assumed one
         model_response(add(juice, 1, gave=False)),
         model_response(text="أكيد! كم كاسة عصير برتقال بدك؟")],
        [model_response(add(juice, 2, gave=True)), model_response(text="تم، ضفت كاستين عصير.")],
    )
    asked = await chat(client, "بدي عصير")
    assert codes(asked) == ["QUANTITY_NOT_GIVEN"] * 2
    assert "how many" in asked["tool_calls"][0]["result"]["hint"]
    assert asked["draft"] is None
    added = await chat(client, "٢")  # the answer, in Arabic digits
    assert added["tool_calls"][0]["result"]["success"] is True
    assert added["draft"]["items"][0]["quantity"] == 2


@pytest.mark.asyncio
async def test_a_quantity_said_messages_ago_is_used_without_asking_again(
        client, db_session, monkeypatch, debug_mode):
    """The case from the chat: «٣ شاورما» said early on, then other talk, then the shawarma is added."""
    await seated(client, db_session)
    shawarma = await dish(db_session, "سيخ شاورما دجاج")
    hummus = await dish(db_session, "حمص")
    script_model(monkeypatch,
        [model_response(add(hummus, 2, gave=True, still_asking=True)),
         model_response(text="تم، ضفت صحنين حمص. والشاورما بدك الدجاج؟")],
        [model_response(text="السيخ بـ9.50 د.أ.")],
        [model_response(add(shawarma, 3, gave=True)), model_response(text="تم، ضفت 3 أسياخ شاورما دجاج.")],
    )
    first = await chat(client, "بدي ٢ حمص و٣ شاورما")
    assert "suggestion_ideas" not in first["tool_calls"][0]["result"]  # still asking about the shawarma
    await chat(client, "قديش سعرها؟")
    added = await chat(client, "اه")
    assert added["tool_calls"][0]["result"]["success"] is True
    lines = {item["product_id"]: item for item in added["draft"]["items"]}
    assert lines[shawarma.id]["quantity"] == 3 and lines[shawarma.id]["unit"] == "سيخ"
    assert lines[hummus.id]["unit"] == "صحن"
    assert added["tool_calls"][0]["result"]["suggestion_ideas"]  # the order is clear: the suggestion's turn


@pytest.mark.asyncio
async def test_dishes_added_to_a_sent_order_need_the_quantity_too(client, db_session, monkeypatch, debug_mode):
    await seated(client, db_session, "T5")
    order = await send_order(client, db_session, "حمص", 1)
    tea = await dish(db_session, "شاي")
    script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
         model_response(model_call("change_sent_order", order_id=order["order_id"], guest_words="ضيف شاي",
                                   operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
         model_response(text="كم كاسة شاي بدك أضيف؟")],
    )
    reply = await chat(client, "ضيف شاي على طلبي")
    assert codes(reply)[1] == "QUANTITY_NOT_GIVEN"
    assert reply["action"] is None


@pytest.mark.asyncio
async def test_no_suggestion_once_the_guest_says_no_thanks(client, db_session, monkeypatch, debug_mode):
    """The case from the chat: "بتحب شي ثاني؟" -> "لا شكرا" must bring the summary, not an offer."""
    await seated(client, db_session)
    juice = await dish(db_session, "عصير برتقال")
    script_model(monkeypatch,
        [model_response(add(juice, 2, gave=True)),
         model_response(text="تم، ضفت كاستين عصير برتقال. بتحب شي ثاني؟")],
        [model_response(model_call("recommend_products")),
         model_response(model_call("prepare_order_confirmation", customer_finished=True)),
         model_response(text="تمام، طلبك: كاستين عصير برتقال. أبعثه للمطبخ؟")],
    )
    await chat(client, "عصير بدي 2")
    summary = await chat(client, "لا شكرا")
    assert codes(summary)[0] == "SUGGESTION_ALREADY_MADE"
    assert summary["tool_calls"][1]["result"]["success"] is True
    assert summary["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"


@pytest.mark.asyncio
async def test_the_one_suggestion_comes_with_the_dishes_just_added(client, db_session, monkeypatch, debug_mode):
    await seated(client, db_session)
    hummus = await dish(db_session, "حمص")
    script_model(monkeypatch,
        [model_response(add(hummus, 2, gave=True)),
         model_response(text="تم، ضفت صحنين حمص. بتحب معهم شاي؟ الكاسة بـ3.75 د.أ")],
        [model_response(model_call("prepare_order_confirmation", customer_finished=True)),
         model_response(text="طلبك: صحنين حمص. أبعثه؟")],
    )
    offered = await chat(client, "بدي 2 حمص")
    added = offered["tool_calls"][0]["result"]
    assert "suggestion_ideas" in added["next_step"]
    ideas = added["suggestion_ideas"]
    assert ideas and hummus.id not in {idea["id"] for idea in ideas}
    assert all(idea["units"] and idea["price"] for idea in ideas)
    summary = await chat(client, "لا بس هيك")
    assert summary["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"


class WritingChat(ScriptedChat):
    """A scripted model that writes its text in pieces, like a streaming provider."""

    async def send_message(self, message, context=None, on_text=None):
        response = await super().send_message(message, context)
        if on_text is not None:
            on_text(None)
            for word in response.text.split(" "):
                on_text(word + " ")
        return response


def events(body: str):
    found = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        found.append((lines["event"], json.loads(lines["data"])))
    return found


@pytest.mark.asyncio
async def test_the_reply_is_streamed_with_the_steps_the_assistant_takes(client, db_session, monkeypatch):
    await seated(client, db_session)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-test-key")
    from jubran.application.ai.agent_service import AssistantService
    writer = WritingChat([model_response(model_call("search_knowledge", query="حمص", source_types=["product"])),
                          model_response(text="عنا حمص بـ 4.20 د.أ، كم صحن بدك؟")])
    monkeypatch.setattr(AssistantService, "_create_chat",
                        classmethod(lambda cls, history, table, config, draft, language=None, menu=None: writer))
    response = await client.post("/api/v1/assistant/chat/stream", json={"message": "في حمص؟"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    found = events(response.text)
    assert [data["step"] for kind, data in found if kind == "step"] == ["thinking", "menu", "thinking"]
    assert "".join(data["delta"] for kind, data in found if kind == "text").strip() == "عنا حمص بـ 4.20 د.أ، كم صحن بدك؟"
    kind, done = found[-1]
    assert kind == "done" and done["response"] == "عنا حمص بـ 4.20 د.أ، كم صحن بدك؟"
    history = (await client.get("/api/v1/assistant/history")).json()["history"]
    assert [m["content"] for m in history] == ["في حمص؟", "عنا حمص بـ 4.20 د.أ، كم صحن بدك؟"]


@pytest.mark.asyncio
async def test_a_streamed_turn_reports_a_failure_as_an_event(client, db_session):
    await seated(client, db_session)
    response = await client.post("/api/v1/assistant/chat/stream", json={"message": "مرحبا"})
    kind, error = events(response.text)[-1]
    assert kind == "error" and error["code"] == "AI_PROVIDER_NOT_CONFIGURED"
    assert error["message"] and error["details"]["message_en"]


@pytest.mark.asyncio
async def test_openai_text_is_passed_on_while_it_is_written():
    stream = "".join(f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n" for event in [
        {"type": "response.created", "response": {}},
        {"type": "response.output_text.delta", "delta": "أهلاً "},
        {"type": "response.output_text.delta", "delta": "وسهلاً!"},
        {"type": "response.completed", "response": {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "أهلاً وسهلاً!"}]}],
            "usage": {"input_tokens": 100, "output_tokens": 5}}},
    ])
    sent = []

    def responder(request: httpx.Request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream.encode())

    chat_client = OpenAIAgentChat(RuntimeModelConfig("openai", "gpt-6-luna", "test-key"), [], "instructions",
                                  client=httpx.AsyncClient(transport=httpx.MockTransport(responder)))
    pieces = []
    try:
        reply = await chat_client.send_message("مرحبا", on_text=pieces.append)
    finally:
        await chat_client.close()
    assert sent[0]["stream"] is True
    assert pieces == [None, "أهلاً ", "وسهلاً!"]
    assert reply.text == "أهلاً وسهلاً!" and reply.function_calls == []
    assert chat_client.usage.input_tokens == 100


@pytest.mark.asyncio
async def test_model_calls_share_one_pool_of_open_connections():
    config = RuntimeModelConfig("openai", "gpt-6-luna", "test-key")
    first, second = OpenAIAgentChat(config, [], "instructions"), OpenAIAgentChat(config, [], "instructions")
    try:
        assert first._client is second._client
        await first.close()  # one guest's turn ends: the connections stay open for the next message
        assert not second._client.is_closed
    finally:
        await close_shared_http_client()
