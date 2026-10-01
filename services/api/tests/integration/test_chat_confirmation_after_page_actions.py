"""A "yes" in the chat after the guest acted on the orders page never breaks the assistant."""
import pytest
from sqlalchemy import select

from jubran.infrastructure.db.models import AssistantConversationModel, OrderModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit
from test_model_agent import model_call, model_response, script_model, summary_calls


async def seated_with_summary(client, db_session, monkeypatch, *later_turns):
    """The assistant shows a summary for one tea; returns the guest's id."""
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == "شاي"))).scalar_one()
    script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
         *summary_calls(),
         model_response(text="هذا ملخص طلبك. هل تؤكد؟")],
        *later_turns,
    )
    shown = await client.post("/api/v1/assistant/chat", json={"message": "بدي شاي وخلص"})
    assert shown.json()["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"
    return (await client.get("/api/v1/session/context")).json()["customer_session_id"]


async def pending_in_db(db_session, customer_id):
    db_session.expire_all()
    row = (await db_session.execute(select(AssistantConversationModel).where(
        AssistantConversationModel.customer_session_id == customer_id))).scalar_one()
    return row.pending_token_ciphertext is not None


def confirm_turn(reply="حسناً."):
    return [model_response(model_call("submit_order", confirmed_summary=True, guest_words="أكد")),
            model_response(text=reply)]


@pytest.mark.asyncio
async def test_yes_after_sending_from_the_orders_page(client, db_session, monkeypatch):
    customer_id = await seated_with_summary(client, db_session, monkeypatch,
                                            confirm_turn("طلبك انبعت من صفحة الطلبات."), confirm_turn())

    # The guest sends the same basket from the orders page instead.
    prepared = (await client.post("/api/v1/draft/prepare-confirmation")).json()
    sent = await client.post("/api/v1/orders", json={"confirmation_token": prepared["confirmation_token"],
                                                     "draft_version": prepared["draft_version"]})
    assert sent.status_code == 200

    reply = await client.post("/api/v1/assistant/chat", json={"message": "تمام أكد"})
    assert reply.status_code == 200, reply.text
    assert reply.json()["tool_calls"][0]["result"]["error_code"] == "ORDER_ALREADY_SUBMITTED"
    assert len((await db_session.execute(select(OrderModel))).scalars().all()) == 1
    assert not await pending_in_db(db_session, customer_id)

    # And the next "yes" is simply answered, not an error loop.
    again = await client.post("/api/v1/assistant/chat", json={"message": "أكد"})
    assert again.status_code == 200
    assert again.json()["tool_calls"][0]["result"]["error_code"] == "NOTHING_TO_SEND"


@pytest.mark.asyncio
async def test_yes_after_emptying_the_basket(client, db_session, monkeypatch):
    customer_id = await seated_with_summary(client, db_session, monkeypatch,
                                            confirm_turn("سلتك صارت فاضية."), confirm_turn())
    line_id = (await client.get("/api/v1/draft")).json()["items"][0]["line_id"]
    assert (await client.delete(f"/api/v1/draft/items/{line_id}")).status_code == 200

    reply = await client.post("/api/v1/assistant/chat", json={"message": "أكد"})
    assert reply.status_code == 200, reply.text  # used to be a 503 "model failed"
    result = reply.json()["tool_calls"][0]["result"]
    assert result["success"] is False and result["error_code"] == "EMPTY_DRAFT"
    assert reply.json()["action"] is None
    assert not await pending_in_db(db_session, customer_id)
    assert (await db_session.execute(select(OrderModel))).scalars().all() == []

    again = await client.post("/api/v1/assistant/chat", json={"message": "أكد"})
    assert again.status_code == 200
    assert again.json()["tool_calls"][0]["result"]["error_code"] == "NOTHING_TO_SEND"
