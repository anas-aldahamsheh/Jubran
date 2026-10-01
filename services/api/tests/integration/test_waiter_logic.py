"""The assistant as a waiter: changing sent orders, remembering dishes, honest outcomes, costs.

The model is scripted (offline); what is checked is the server side: guards,
state kept between turns, the facts given to the model, and what reaches the database.
"""
import json

import httpx
import pytest
from sqlalchemy import select

from jubran.application.ai.agent_service import AssistantService
from jubran.application.ai.model_clients import OpenAIAgentChat
from jubran.application.ai.model_config_service import RuntimeModelConfig
from jubran.application.ai.tools import AssistantToolExecutor
from jubran.application.ai.usage import Usage, estimated_cost_microusd
from jubran.infrastructure.db.models import AssistantUsageModel, CustomerSessionModel, OrderModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import sign_in, start_visit
from test_model_agent import ScriptedChat, model_call, model_response, script_model, summary_calls


async def dish(db_session, name):
    return (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == name))).scalar_one()


async def send_order(client, db_session, name, quantity=1):
    product = await dish(db_session, name)
    await client.post("/api/v1/draft/items", json={"product_id": product.id, "quantity": quantity})
    prepared = (await client.post("/api/v1/draft/prepare-confirmation")).json()
    return (await client.post("/api/v1/orders", json={"confirmation_token": prepared["confirmation_token"],
                                                      "draft_version": prepared["draft_version"]})).json()


async def chat(client, message):
    response = await client.post("/api/v1/assistant/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def debug_mode(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", True)  # tool results are returned for inspection


@pytest.mark.asyncio
async def test_changing_a_sent_order_needs_the_guests_yes_in_a_later_message(client, db_session, monkeypatch, debug_mode):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    order = await send_order(client, db_session, "شاي", 1)
    tea_item = (await client.get("/api/v1/orders")).json()[0]["items"][0]["item_id"]
    change = {"order_id": order["order_id"],
              "operations": [{"op": "set_quantity", "item_id": tea_item, "quantity": 3}]}
    chats = script_model(monkeypatch,
        [model_response(model_call("prepare_order_amendment", **change)),
         model_response(model_call("confirm_order_amendment", confirmed=True)),  # same turn: refused
         model_response(text="بصير عندك 3 شاي بالطلب، المجموع الجديد 4.65 د.أ. بأكّد التعديل؟")],
        [model_response(model_call("confirm_order_amendment", confirmed=True, guest_words="آه أكد")),
         model_response(text="تم.")],
    )

    shown = await chat(client, "خلي الشاي 3 بالطلب اللي بعتته")
    assert shown["action"]["type"] == "AWAITING_AMENDMENT_CONFIRMATION"
    assert shown["tool_calls"][0]["result"]["amendment"]["changes"][0]["quantity_after"] == 3
    assert shown["tool_calls"][1]["result"]["error_code"] == "PRIOR_CONFIRMATION_REQUIRED"
    assert (await client.get("/api/v1/orders")).json()[0]["items"][0]["quantity"] == 1
    # The next turn's facts include the change waiting for a yes.
    confirmed = await chat(client, "آه أكد")
    assert "order_change_awaiting_confirmation" in chats[1].context
    assert confirmed["action"]["type"] == "ORDER_AMENDED"
    view = (await client.get("/api/v1/orders")).json()[0]
    assert view["items"][0]["quantity"] == 3 and view["amended"] is True


@pytest.mark.asyncio
async def test_the_confirm_button_applies_the_change_shown(client, db_session, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T5")
    order = await send_order(client, db_session, "حمص", 1)
    tea = await dish(db_session, "شاي")
    script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
         model_response(model_call("prepare_order_amendment", order_id=order["order_id"],
                                   operations=[{"op": "add", "product_id": tea.id, "quantity": 2}])),
         model_response(text="بضيف 2 شاي على طلبك. بأكد؟")],
    )
    await chat(client, "ضيف 2 شاي على طلبي")
    pressed = await client.post("/api/v1/assistant/confirm-amendment", json={"language": "ar"})
    assert pressed.status_code == 200 and pressed.json()["success"] is True
    assert pressed.json()["response"].startswith("تم تعديل الطلب رقم")
    names = {item["name_ar"]: item for item in (await client.get("/api/v1/orders")).json()[0]["items"]}
    assert names["شاي"]["quantity"] == 2 and names["شاي"]["added_later"] is True


@pytest.mark.asyncio
async def test_ready_orders_cannot_be_changed_from_the_chat(client, db_session, new_browser, monkeypatch, debug_mode):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    await sign_in(admin)
    await start_visit(guest, db_session, "T6")
    order = await send_order(guest, db_session, "فول", 1)
    for step in ("start-preparing", "mark-ready"):
        await admin.post(f"/api/v1/admin/orders/{order['order_id']}/{step}")
    item = (await guest.get("/api/v1/orders")).json()[0]["items"][0]["item_id"]
    script_model(monkeypatch,
        [model_response(model_call("prepare_order_amendment", order_id=order["order_id"],
                                   operations=[{"op": "remove", "item_id": item}])),
         model_response(text="الطلب جاهز فما بقدر أعدّل عليه.")],
    )
    reply = await chat(guest, "شيل الفول")
    result = reply["tool_calls"][0]["result"]
    assert result["success"] is False and result["error_code"] == "ORDER_LOCKED"
    assert result["details"]["reason"] == "READY"


@pytest.mark.asyncio
async def test_dishes_shown_recently_can_be_added_without_searching_again(client, db_session, monkeypatch, debug_mode):
    await seed_database(db_session)
    await start_visit(client, db_session, "T7")
    tea = await dish(db_session, "شاي")
    add_tea = model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 2}])
    chats = script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
         model_response(text="عنا شاي بالنعناع أو الميرمية بـ 1.55 د.أ. كم كاسة؟")],
        [model_response(add_tea), model_response(text="ضفت 2 شاي.")],
        *[[model_response(text="تمام")] for _ in range(4)],
        [model_response(add_tea), model_response(text="لازم أدور أول.")],
    )
    await chat(client, "عندكم شاي؟")
    added = await chat(client, "2")
    assert added["tool_calls"][0]["result"]["success"] is True  # no second search needed
    assert tea.id in {d["id"] for d in chats[1].context["recently_shown_dishes"]}
    for _ in range(4):
        await chat(client, "تمام")
    basket = chats[2].context["basket_not_sent"]
    assert basket["items"][0]["n"] == 1 and basket["items"][0]["quantity"] == 2
    assert "recently_shown_dishes" not in chats[6 - 1].context or tea.id not in {
        d["id"] for d in chats[5].context.get("recently_shown_dishes", [])}
    late = await chat(client, "زيد كمان 2 شاي")  # shown too long ago: look it up again
    # The tea is in the basket, so adding more of it is still allowed.
    assert late["tool_calls"][0]["result"]["success"] is True


@pytest.mark.asyncio
async def test_a_turn_that_fails_after_sending_still_reports_the_order(client, db_session, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T8")
    tea = await dish(db_session, "شاي")

    class BreaksAfterSubmit(ScriptedChat):
        async def send_message(self, message, context=None):
            if isinstance(message, list) and message and message[0]["name"] == "submit_order":
                raise RuntimeError("provider connection dropped")
            return await super().send_message(message, context)

    turns = iter([
        ScriptedChat([model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
                      model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
                      *summary_calls(),
                      model_response(text="شاي واحد، 1.55 د.أ. بأكد؟")]),
        BreaksAfterSubmit([model_response(model_call("submit_order", confirmed_summary=True, guest_words="أكد"))]),
    ])
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-test-key")
    monkeypatch.setattr(AssistantService, "_create_chat", classmethod(lambda cls, *args, **kwargs: next(turns)))
    await chat(client, "بدي شاي وبس")
    reply = await chat(client, "أكد")
    assert reply["response"].startswith("تم إرسال طلبك رقم")  # not an error: the order really went out
    assert len((await db_session.execute(select(OrderModel))).scalars().all()) == 1
    history = (await client.get("/api/v1/assistant/history")).json()["history"]
    assert history[-2]["content"] == "أكد"


@pytest.mark.asyncio
async def test_order_status_shows_the_table_without_revealing_guests(client, db_session, new_browser):
    await seed_database(db_session)
    first, second = client, new_browser()
    await start_visit(first, db_session, "T9")
    await send_order(first, db_session, "فتوش", 1)
    await start_visit(second, db_session, "T9")
    await send_order(second, db_session, "شاي", 2)
    guests = (await db_session.execute(select(CustomerSessionModel))).scalars().all()
    first_id = (await first.get("/api/v1/session/context")).json()["customer_session_id"]
    second_guest = next(g for g in guests if g.id != first_id)
    status = await AssistantToolExecutor(db_session, second_guest.id, second_guest.table_session_id, "T9").execute(
        "get_order_status", {})
    mine = [o for o in status["orders"] if o["mine"]]
    others = [o for o in status["orders"] if not o["mine"]]
    assert len(mine) == 1 and len(others) == 1
    assert mine[0]["editable"] and "item_id" in mine[0]["items"][0]
    assert not others[0]["editable"] and "item_id" not in others[0]["items"][0]
    assert not any(guest.id in json.dumps(status) for guest in guests)


def test_cost_estimate_uses_cached_tokens():
    usage = Usage()
    usage.add(input_tokens=10_000, cached_input_tokens=8_000, output_tokens=500)
    # 2,000 new input at $0.10/M + 8,000 cached at $0.01/M + 500 output at $0.50/M = $0.00053
    assert estimated_cost_microusd("gpt-6-luna", usage) == 530
    assert estimated_cost_microusd("some-unpriced-model", usage) is None


@pytest.mark.asyncio
async def test_openai_client_counts_tokens_and_drops_fields_a_model_refuses():
    requests = []

    def responder(request: httpx.Request):
        payload = json.loads(request.content)
        requests.append(payload)
        if "include" in payload:
            return httpx.Response(400, json={"error": {"param": "include", "message": "Unsupported parameter: include"}})
        return httpx.Response(200, json={
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "أهلاً!"}]}],
            "usage": {"input_tokens": 3000, "input_tokens_details": {"cached_tokens": 2048},
                      "output_tokens": 40, "output_tokens_details": {"reasoning_tokens": 12}}})

    chat_client = OpenAIAgentChat(RuntimeModelConfig("openai", "gpt-6-luna", "test-key", reasoning_effort="low"),
                                  [], "static instructions")
    await chat_client._client.aclose()
    chat_client._client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    try:
        reply = await chat_client.send_message("مرحبا", context={"table": "T1"})
        assert reply.text == "أهلاً!"
        assert "include" in requests[0] and "include" not in requests[1]  # retried without it
        assert requests[1]["instructions"] == "static instructions"  # facts are not in the instructions
        assert requests[1]["input"][-2]["role"] == "developer" and "T1" in requests[1]["input"][-2]["content"]
        assert requests[1]["prompt_cache_key"] == "jubran-assistant"
        usage = chat_client.usage
        assert (usage.calls, usage.input_tokens, usage.cached_input_tokens, usage.output_tokens,
                usage.reasoning_tokens) == (1, 3000, 2048, 40, 12)
    finally:
        await chat_client.close()


@pytest.mark.asyncio
async def test_usage_is_recorded_per_turn_and_summarized_for_the_admin(client, db_session, new_browser, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T10")

    class CountingChat(ScriptedChat):
        def __init__(self, responses):
            super().__init__(responses)
            self.usage = Usage()

        async def send_message(self, message, context=None):
            self.usage.add(input_tokens=5000, cached_input_tokens=4000, output_tokens=100)
            return await super().send_message(message, context)

    async def gpt_luna(db, purpose):
        return RuntimeModelConfig("openai", "gpt-6-luna", "key")

    monkeypatch.setattr("jubran.application.ai.agent_service.ModelConfigService.runtime", gpt_luna)
    monkeypatch.setattr(AssistantService, "_create_chat", classmethod(
        lambda cls, *args, **kwargs: CountingChat([model_response(text="أهلاً وسهلاً!")])))
    await chat(client, "مرحبا")
    row = (await db_session.execute(select(AssistantUsageModel))).scalar_one()
    assert (row.model_id, row.model_calls, row.input_tokens, row.succeeded) == ("gpt-6-luna", 1, 5000, True)
    assert row.cost_microusd == 190  # 1000*0.10 + 4000*0.01 + 100*0.50 micro-dollars

    admin = new_browser()
    await sign_in(admin)
    summary = (await admin.get("/api/v1/admin/ai-models/usage")).json()
    assert summary["models"][0]["turns"] == 1 and summary["models"][0]["cost_usd"] == 0.0002


def order_script(tea, *later_turns):
    """Turn 1: the guest orders tea and finishes; the summary is shown."""
    return [[model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
             model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
             *summary_calls(),
             model_response(text="شاي واحد، 1.55 د.أ. بتحب أبعته للمطبخ؟")], *later_turns]


@pytest.mark.asyncio
async def test_an_order_is_sent_only_on_the_guests_own_yes(client, db_session, monkeypatch, debug_mode):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = await dish(db_session, "شاي")
    script_model(monkeypatch, *order_script(
        tea,
        # "No, nothing else" is not a yes: whatever the model quotes, nothing is sent.
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="آه أكد")),
         model_response(model_call("submit_order", confirmed_summary=True)),
         model_response(text="بتحب أبعت الطلب؟")],
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="تمام ابعته")),
         model_response(text="تم.")],
    ))
    await chat(client, "بدي شاي وبس")
    refused = await chat(client, "لا ما بدي إشي ثاني")
    assert [call["result"]["error_code"] for call in refused["tool_calls"]] == ["CONFIRMATION_WORDS_REQUIRED"] * 2
    assert (await db_session.execute(select(OrderModel))).scalars().all() == []
    sent = await chat(client, "تمام ابعته")
    assert sent["action"]["type"] == "ORDER_SUBMITTED"


@pytest.mark.asyncio
async def test_no_summary_in_the_same_reply_as_a_suggestion(client, db_session, monkeypatch, debug_mode):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = await dish(db_session, "شاي")
    script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
         model_response(model_call("recommend_products")),
         model_response(model_call("prepare_order_confirmation", customer_finished=True)),
         model_response(text="بتحب فلافل مع الشاي؟")],
        [model_response(model_call("prepare_order_confirmation", customer_finished=True)),
         model_response(text="شاي واحد. بتحب أبعته؟")],
    )
    suggested = await chat(client, "بدي شاي وخلص")
    assert suggested["tool_calls"][-1]["result"]["error_code"] == "SUGGESTION_FIRST"
    assert suggested["action"] is None
    summary = await chat(client, "لا شكراً")
    assert summary["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"


@pytest.mark.asyncio
async def test_nothing_fits_goes_straight_to_the_summary(client, db_session, monkeypatch, debug_mode):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = await dish(db_session, "شاي")
    script_model(monkeypatch, [
        model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
        model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
        model_response(model_call("recommend_products")),
        model_response(model_call("prepare_order_confirmation", customer_finished=True, no_fitting_suggestion=True)),
        model_response(text="شاي واحد. بتحب أبعته؟")])
    shown = await chat(client, "بدي شاي وخلص")
    assert shown["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"


@pytest.mark.asyncio
async def test_the_sent_message_follows_the_language_the_guest_writes(client, db_session, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = await dish(db_session, "شاي")
    script_model(monkeypatch, *order_script(
        tea, [model_response(model_call("submit_order", confirmed_summary=True, guest_words="yes, send it")),
              model_response(text="Done, order JB-999 is on its way!")]))
    await chat(client, "one tea, that's all")
    # The page is in Arabic, but the guest writes English: the server's own sentence (used here
    # because the model named a wrong number) is English too.
    sent = await client.post("/api/v1/assistant/chat", json={"message": "yes, send it", "language": "ar"})
    assert sent.json()["response"].startswith("Your order JB-")


@pytest.mark.asyncio
async def test_lines_added_together_keep_the_order_they_were_named_in(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    customer = (await client.get("/api/v1/session/context")).json()["customer_session_id"]
    visit = (await client.get("/api/v1/session/context")).json()["table_session_id"]
    tools = AssistantToolExecutor(db_session, customer, visit, "T4")
    names = ["حمص", "شاي", "فول", "فتوش", "مياه معدنية"]
    dishes = [await dish(db_session, name) for name in names]
    added = await tools.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": product.id, "quantity": 1} for product in dishes]})
    # "The last one" is the dish named last, even on clocks that tick coarsely (Windows).
    assert [item["name_ar"] for item in added["draft"]["items"]] == names


@pytest.mark.asyncio
async def test_a_brief_provider_hiccup_is_retried(monkeypatch):
    answers = iter([httpx.Response(503, json={"error": {"message": "overloaded"}}),
                    httpx.Response(429, json={"error": {"message": "Rate limit reached, try again"}}),
                    httpx.Response(200, json={"output": [{"type": "message", "content": [
                        {"type": "output_text", "text": "أهلاً!"}]}], "usage": {}})])
    monkeypatch.setattr(OpenAIAgentChat, "RETRY_DELAYS", (0, 0))
    chat_client = OpenAIAgentChat(RuntimeModelConfig("openai", "gpt-6-luna", "test-key"), [], "static")
    await chat_client._client.aclose()
    chat_client._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(answers)))
    try:
        assert (await chat_client.send_message("مرحبا")).text == "أهلاً!"
    finally:
        await chat_client.close()


@pytest.mark.asyncio
async def test_a_used_up_quota_is_not_retried(monkeypatch):
    calls = []

    def quota(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "quota"}})

    monkeypatch.setattr(OpenAIAgentChat, "RETRY_DELAYS", (0, 0))
    chat_client = OpenAIAgentChat(RuntimeModelConfig("openai", "gpt-6-luna", "test-key"), [], "static")
    await chat_client._client.aclose()
    chat_client._client = httpx.AsyncClient(transport=httpx.MockTransport(quota))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await chat_client.send_message("مرحبا")
        assert len(calls) == 1
    finally:
        await chat_client.close()


@pytest.mark.asyncio
async def test_more_of_a_dish_already_sent_needs_no_new_search(client, db_session, monkeypatch, debug_mode):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = await dish(db_session, "شاي")
    order = await send_order(client, db_session, "شاي", 1)
    script_model(monkeypatch, [
        model_response(model_call("prepare_order_amendment", order_id=order["order_id"],
                                  operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
        model_response(text="بصير عندك 2 شاي. بأكد؟")])
    shown = await chat(client, "زيد على طلبي شاي كمان")
    assert shown["action"]["type"] == "AWAITING_AMENDMENT_CONFIRMATION"  # the order's own dish is known


@pytest.mark.asyncio
async def test_a_named_dish_brings_its_variations(db_session, monkeypatch):
    from jubran.application.ai.semantic_retrieval import EmbeddingService, SemanticKnowledgeService, \
        SemanticRetrievalError
    await seed_database(db_session)

    async def provider_down(cls, db, texts, *, task_type):
        raise SemanticRetrievalError("EMBEDDING_QUOTA_EXCEEDED")

    monkeypatch.setattr(EmbeddingService, "embed", classmethod(provider_down))
    found = await SemanticKnowledgeService.search(db_session, "بدي حمص", source_types=["product"])
    names = [match["product"]["name_ar"] for match in found["matches"]]
    assert names[0] == "حمص"
    assert {"حمص بالجوز والريحان", "حمص بيروتي", "حمص باللحم"} <= set(names)


@pytest.mark.asyncio
async def test_an_order_gets_a_suggestion_considered_before_its_summary(client, db_session, monkeypatch, debug_mode):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = await dish(db_session, "شاي")
    script_model(monkeypatch, [
        model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
        model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
        model_response(model_call("prepare_order_confirmation", customer_finished=True)),
        model_response(model_call("recommend_products")),
        model_response(text="بتحب فلافل مع الشاي؟")])
    reply = await chat(client, "بدي شاي وبس")
    assert reply["tool_calls"][2]["result"]["error_code"] == "SUGGESTION_NOT_CONSIDERED"
    assert reply["action"] is None  # no summary yet: the suggestion comes first


@pytest.mark.asyncio
async def test_every_dish_of_a_kind_is_grouped_and_kept(db_session, monkeypatch):
    from jubran.application.ai.semantic_retrieval import SemanticKnowledgeService
    from jubran.application.ai.tool_views import model_view
    await seed_database(db_session)
    found = await SemanticKnowledgeService.search(db_session, "بدي أرجيلة عنب", source_types=["product"], limit=2)
    names = {match["product"]["name_ar"] for match in found["matches"]}
    grape_shishas = {"أرجيلة عنب", "أرجيلة عنب وتوت", "أرجيلة عنب ونعنع"}
    assert grape_shishas <= names  # a small limit never cuts a kind short
    view = model_view("search_knowledge", found)
    assert set(view["same_kind"][0]) == grape_shishas


@pytest.mark.asyncio
async def test_a_change_after_the_summary_shows_the_updated_summary(client, db_session, monkeypatch, debug_mode):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = await dish(db_session, "شاي")
    script_model(monkeypatch, *order_script(tea))
    await chat(client, "بدي شاي وبس")
    line = (await client.get("/api/v1/draft")).json()["items"][0]["line_id"]
    script_model(monkeypatch,
        [model_response(model_call("update_draft_order", operations=[{"op": "set_quantity", "line_id": line, "quantity": 2}])),
         model_response(text="صاروا شايين، 3.10 د.أ. أبعته؟")],
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="آه ابعته")),
         model_response(text="تم.")])
    changed = await chat(client, "لحظة، خليهم 2")
    assert changed["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"  # the updated summary, at once
    assert changed["action"]["summary"]["items"][0]["quantity"] == 2
    sent = await chat(client, "آه ابعته")
    assert sent["action"]["type"] == "ORDER_SUBMITTED"
    assert (await client.get("/api/v1/orders")).json()[0]["items"][0]["quantity"] == 2


@pytest.mark.asyncio
async def test_nothing_on_the_menu_brings_real_alternatives(client, db_session, monkeypatch):
    from jubran.application.ai.assistant_turn import AssistantTurn
    from jubran.application.ai.conversation_store import ConversationState
    from jubran.application.ai.semantic_retrieval import SemanticKnowledgeService
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    context = (await client.get("/api/v1/session/context")).json()
    tools = AssistantToolExecutor(db_session, context["customer_session_id"], context["table_session_id"], "T4")

    async def nothing_found(cls, db, **kwargs):
        return {"success": True, "matches": [], "no_reliable_match": True}

    monkeypatch.setattr(SemanticKnowledgeService, "search", classmethod(nothing_found))
    turn = AssistantTurn(ConversationState(customer_session_id=context["customer_session_id"]))
    view = await turn.run_tool(tools, "search_knowledge", {"query": "كنافة"})
    offered = view["alternatives"]
    assert len(offered) == 3 and all(dish["price"] for dish in offered)  # real dishes with prices
    assert {dish["id"] for dish in offered} <= turn.seen_products  # the guest can take one right away
