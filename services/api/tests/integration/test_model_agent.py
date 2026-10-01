"""Offline integration checks for model-directed tool orchestration."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from jubran.application.ai.agent_service import AssistantService
from jubran.infrastructure.db.models import ProductModel, OrderModel
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit
from jubran.settings import settings


def model_call(name, **args):
    return SimpleNamespace(name=name, args=args)


def model_response(*calls, text=""):
    return SimpleNamespace(function_calls=list(calls), text=text)


class ScriptedChat:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    async def send_message(self, message, context=None):
        self.messages.append(message)
        if context is not None:
            self.context = context
        return next(self.responses)

    async def send_note(self, note):
        return await self.send_message(note)


def summary_calls():
    """The model finishing an order: a suggestion considered, nothing fits, then the summary."""
    return [model_response(model_call("recommend_products")),
            model_response(model_call("prepare_order_confirmation", customer_finished=True,
                                      no_fitting_suggestion=True))]


def script_model(monkeypatch, *turns):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-test-key")
    chats = [ScriptedChat(turn) for turn in turns]
    iterator = iter(chats)
    monkeypatch.setattr(AssistantService, "_create_chat", classmethod(lambda cls, history, table, config, draft, language=None: next(iterator)))
    return chats


async def seat_customer(client, db_session):
    """Seed and scan table T4 with this browser (the visit lives in its cookie)."""
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")


@pytest.mark.asyncio
async def test_menu_paraphrase_uses_model_selected_live_tool(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    chats = script_model(monkeypatch, [
        model_response(model_call("search_knowledge", query="تصنيفات الطعام الموجودة", source_types=["category"])),
        model_response(text="هذه أقسام القائمة المتاحة."),
    ])
    response = await client.post("/api/v1/assistant/chat", json={"message": "شو فيش عندكم منتجات؟"})
    assert response.status_code == 200
    assert response.json()["tool_calls"][0]["tool"] == "search_knowledge"
    assert response.json()["tool_calls"][0]["result"]["matches"]
    assert response.json()["response"] == "هذه أقسام القائمة المتاحة."
    assert len(chats[0].messages) == 2


@pytest.mark.asyncio
async def test_order_needs_prior_summary_and_server_owned_token(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    product = (await db_session.execute(select(ProductModel).where(ProductModel.is_available.is_(True)))).scalars().first()
    assert product is not None
    script_model(monkeypatch,
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="أرسل الطلب")),
         model_response(text="التأكيد مطلوب أولاً.")],
        [model_response(model_call("search_knowledge", query=product.name_ar, source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": product.id, "quantity": 2}])),
         *summary_calls(),
         model_response(text="هذه سلة الطلب ومجموعها. هل تؤكد إرسالها؟")],
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="نعم، أكد")),
         model_response(text="تم إرسال طلبك.")],
    )
    rejected = await client.post("/api/v1/assistant/chat", json={"message": "أرسل الطلب"})
    assert rejected.status_code == 200
    assert rejected.json()["tool_calls"][0]["result"]["error_code"] == "NOTHING_TO_SEND"  # nothing in the basket

    prepared = await client.post("/api/v1/assistant/chat", json={"message": f"بدي 2 {product.name_ar} وخلص"})
    assert prepared.status_code == 200
    assert prepared.json()["draft"]["item_count"] == 2
    assert prepared.json()["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"
    assert "confirmation_token" not in str(prepared.json())

    submitted = await client.post("/api/v1/assistant/chat", json={"message": "نعم، أكد هذا الطلب"})
    assert submitted.status_code == 200
    assert submitted.json()["action"]["type"] == "ORDER_SUBMITTED"
    assert submitted.json()["tool_calls"][0]["result"]["success"] is True


@pytest.mark.asyncio
async def test_voice_turn_uses_identical_model_tool_loop(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    script_model(monkeypatch, [model_response(model_call("search_knowledge", query="أوقات دوام الفرع", source_types=["branch"])),
                               model_response(text="هذه أوقات العمل الحالية.")])
    result = await client.post("/api/v1/assistant/voice/turn", json={"message": "متى بتفتحوا؟"})
    assert result.status_code == 200
    assert result.json()["spoken_text"] == "هذه أوقات العمل الحالية."
    assert result.json()["tool_calls"][0]["tool"] == "search_knowledge"


@pytest.mark.asyncio
async def test_unconfigured_model_returns_service_error(client, db_session):
    await seat_customer(client, db_session)
    response = await client.post("/api/v1/assistant/chat", json={"message": "مرحبا"})
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "AI_PROVIDER_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_unverified_product_and_same_turn_submit_are_blocked(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    product = (await db_session.execute(select(ProductModel).where(ProductModel.is_available.is_(True)))).scalars().first()
    script_model(monkeypatch,
        [model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": product.id, "quantity": 1}])),
         model_response(text="يجب البحث عن الصنف أولاً.")],
        [model_response(model_call("search_knowledge", query=product.name_ar, source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": product.id, "quantity": 1}])),
         *summary_calls(),
         model_response(model_call("submit_order", confirmed_summary=True)),
         model_response(text="هذه السلة؛ أحتاج تأكيدك في رسالة لاحقة.")],
    )
    blocked = await client.post("/api/v1/assistant/chat", json={"message": "أضف الصنف"})
    assert blocked.json()["tool_calls"][0]["result"]["error_code"] == "LOOKUP_REQUIRED"
    assert blocked.json()["draft"] is None

    prepared = await client.post("/api/v1/assistant/chat", json={"message": "أضف الصنف وأرسل"})
    assert prepared.status_code == 200
    assert prepared.json()["tool_calls"][-1]["result"]["error_code"] == "PRIOR_CONFIRMATION_REQUIRED"
    assert prepared.json()["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"


@pytest.mark.asyncio
async def test_price_change_requires_fresh_confirmation_before_submit(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    product = (await db_session.execute(select(ProductModel).where(ProductModel.is_available.is_(True)))).scalars().first()
    original_price = product.price_minor
    script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query=product.name_ar, source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": product.id, "quantity": 1}])),
         *summary_calls(),
         model_response(text="هذا ملخص الطلب. هل تؤكد الإرسال؟")],
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="نعم")),
         model_response(text="تغيّر السعر، لذلك أحتاج تأكيدك على الملخص الجديد.")],
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="أوافق")),
         model_response(text="تم إرسال الطلب.")],
    )

    prepared = await client.post("/api/v1/assistant/chat", json={"message": f"أضف {product.name_ar} وخلص"})
    assert prepared.json()["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"

    product.price_minor = original_price + 300
    await db_session.commit()
    changed = await client.post("/api/v1/assistant/chat", json={"message": "نعم"})
    submit_result = changed.json()["tool_calls"][0]["result"]
    assert submit_result["error_code"] == "LIVE_DRAFT_CHANGED"
    assert submit_result["changes"][0]["type"] == "PRICE_CHANGED"
    assert changed.json()["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"
    assert (await db_session.execute(select(OrderModel))).scalars().all() == []

    submitted = await client.post("/api/v1/assistant/chat", json={"message": "أوافق على السعر الجديد"})
    assert submitted.json()["action"]["type"] == "ORDER_SUBMITTED"


@pytest.mark.asyncio
async def test_unavailable_product_blocks_previously_confirmed_order(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    product = (await db_session.execute(select(ProductModel).where(ProductModel.is_available.is_(True)))).scalars().first()
    script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query=product.name_ar, source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": product.id, "quantity": 1}])),
         *summary_calls(),
         model_response(text="هل تؤكد إرسال هذا الطلب؟")],
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="نعم")),
         model_response(text="الصنف أصبح غير متوفر، لذلك لم أرسل الطلب.")],
    )

    prepared = await client.post("/api/v1/assistant/chat", json={"message": f"أضف {product.name_ar} وخلص"})
    assert prepared.json()["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"
    product.is_available = False
    await db_session.commit()

    blocked = await client.post("/api/v1/assistant/chat", json={"message": "نعم"})
    result = blocked.json()["tool_calls"][0]["result"]
    assert result["error_code"] == "PRODUCT_UNAVAILABLE_BEFORE_SUBMIT"
    assert any(change["type"] == "PRODUCT_UNAVAILABLE" for change in result["changes"])
    assert (await db_session.execute(select(OrderModel))).scalars().all() == []


@pytest.mark.asyncio
async def test_model_selects_clear_item_and_asks_about_ambiguous_item(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    product = (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == "حمص"))).scalar_one()
    script_model(monkeypatch, [
        model_response(model_call("search_knowledge", query="وجبة حمص وفلافل", source_types=["product"])),
        model_response(model_call("update_draft_order", operations=[
            {"op": "add", "product_id": product.id, "quantity": 1},
        ])),
        model_response(text="أضفت الحمص. هل تقصد فلافل أم كبة حماتي؟"),
    ])
    response = await client.post("/api/v1/assistant/chat", json={"message": "بدي حمص وفلافل"})
    assert response.status_code == 200
    assert [item["name_ar"] for item in response.json()["draft"]["items"]] == ["حمص"]
    assert response.json()["tool_calls"][-1]["result"]["success"] is True


@pytest.mark.asyncio
async def test_read_only_model_answer_never_mutates_draft(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    script_model(monkeypatch, [model_response(text="أي نوع تقصد؟")])
    response = await client.post("/api/v1/assistant/chat", json={"message": "بدي شيء من المنيو"})
    assert response.status_code == 200
    assert response.json()["draft"] is None
    assert response.json()["tool_calls"] == []


@pytest.mark.asyncio
async def test_model_can_decline_confirmation_after_interpreting_context(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    product = (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == "شاي"))).scalar_one()
    script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query=product.name_ar, source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[
             {"op": "add", "product_id": product.id, "quantity": 1}
         ])), model_response(text="أضفت الشاي. هل تريد شيئًا آخر؟")],
        [model_response(model_call("prepare_order_confirmation", customer_finished=False)),
         model_response(text="أخبرني ماذا تريد أن تضيف.")],
    )
    await client.post("/api/v1/assistant/chat", json={"message": "بدي شاي"})
    response = await client.post("/api/v1/assistant/chat", json={"message": "نعم"})
    assert response.status_code == 200
    assert response.json()["tool_calls"][0]["result"]["error_code"] == "CUSTOMER_STILL_ORDERING"
    assert response.json()["action"] is None


@pytest.mark.asyncio
async def test_production_answers_do_not_carry_internal_search_data(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    script_model(monkeypatch, [
        model_response(model_call("search_knowledge", query="حمص", source_types=["product"])),
        model_response(text="عندنا حمص."),
    ])
    monkeypatch.setattr(settings, "DEBUG", False)
    body = (await client.post("/api/v1/assistant/chat", json={"message": "في حمص؟"})).json()
    assert body["tool_calls"] == [{"tool": "search_knowledge"}]


class NotedChat(ScriptedChat):
    """A scripted chat that also accepts the server's notes (e.g. "rewrite that reply")."""

    async def send_note(self, note):
        self.messages.append(note)
        return next(self.responses)


def script_noted_model(monkeypatch, responses):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-test-key")
    chat = NotedChat(responses)
    monkeypatch.setattr(AssistantService, "_create_chat",
                        classmethod(lambda cls, history, table, config, draft, language=None: chat))
    return chat


@pytest.mark.asyncio
async def test_a_word_from_another_writing_system_is_rewritten(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    chat = script_noted_model(monkeypatch, [model_response(text="عنا فتة بـ4.25 د.أ. ומה الكمية؟"),
                                            model_response(text="عنا فتة بـ4.25 د.أ. قديش بدك؟")])
    response = await client.post("/api/v1/assistant/chat", json={"message": "في عندكم فتة؟"})
    assert response.json()["response"] == "عنا فتة بـ4.25 د.أ. قديش بدك؟"
    assert "[System check]" in chat.messages[-1]


@pytest.mark.asyncio
async def test_a_failed_rewrite_drops_the_stray_words(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    script_noted_model(monkeypatch, [model_response(text="عنا فتة. ומה الكمية؟"),
                                     model_response(text="עדיין עברית")])
    response = await client.post("/api/v1/assistant/chat", json={"message": "في عندكم فتة؟"})
    assert response.json()["response"] == "عنا فتة. الكمية؟"


@pytest.mark.asyncio
async def test_the_guests_own_writing_system_is_kept(client, db_session, monkeypatch):
    await seat_customer(client, db_session)
    chat = script_noted_model(monkeypatch, [model_response(text="Здравствуйте! Чем могу помочь?")])
    response = await client.post("/api/v1/assistant/chat", json={"message": "Привет"})
    assert response.json()["response"] == "Здравствуйте! Чем могу помочь?"
    assert len(chat.messages) == 1  # no rewrite was asked for
