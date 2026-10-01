"""The "confirm" button on the assistant's summary submits exactly that summary (the model
only words the confirmation afterwards)."""
import pytest
from sqlalchemy import select

from jubran.infrastructure.db.models import OrderModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit
from test_model_agent import model_call, model_response, script_model, summary_calls


async def summary_shown(client, db_session, monkeypatch, *later_turns):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == "شاي"))).scalar_one()
    script_model(monkeypatch, [
        model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
        model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 2}])),
        *summary_calls(),
        model_response(text="هذا ملخص طلبك. هل تؤكد؟")], *later_turns)
    shown = await client.post("/api/v1/assistant/chat", json={"message": "بدي 2 شاي وخلص"})
    assert shown.json()["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"
    return tea


@pytest.mark.asyncio
async def test_button_sends_the_reviewed_summary_without_the_model(client, db_session, monkeypatch):
    # The model only words the confirmation, after the server has sent the order.
    await summary_shown(client, db_session, monkeypatch, [model_response(text="Done! The kitchen has your order.")])
    confirmed = await client.post("/api/v1/assistant/confirm-order", json={"language": "en"})
    body = confirmed.json()
    assert confirmed.status_code == 200 and body["success"] is True
    assert body["action"]["type"] == "ORDER_SUBMITTED"
    assert body["response"] == "Done! The kitchen has your order."
    orders = (await db_session.execute(select(OrderModel))).scalars().all()
    assert len(orders) == 1

    # The conversation remembers it, and a second press never sends twice.
    history = (await client.get("/api/v1/assistant/history")).json()["history"]
    assert history[-1]["content"] == body["response"]
    again = (await client.post("/api/v1/assistant/confirm-order", json={})).json()
    assert again["success"] is False and again["error_code"] == "NOTHING_TO_SEND"
    assert len((await db_session.execute(select(OrderModel))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_button_after_a_price_change_asks_for_a_new_review(client, db_session, monkeypatch):
    tea = await summary_shown(client, db_session, monkeypatch)
    tea.price_minor += 250
    await db_session.commit()

    refused = (await client.post("/api/v1/assistant/confirm-order", json={})).json()
    assert refused["success"] is False and refused["error_code"] == "LIVE_DRAFT_CHANGED"
    assert refused["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"
    assert refused["action"]["summary"]["items"][0]["unit_price_minor"] == tea.price_minor
    assert (await db_session.execute(select(OrderModel))).scalars().all() == []

    # Pressing again after seeing the new price sends it.
    accepted = (await client.post("/api/v1/assistant/confirm-order", json={})).json()
    assert accepted["action"]["type"] == "ORDER_SUBMITTED"


@pytest.mark.asyncio
async def test_button_confirmation_never_shows_a_wrong_order_number(client, db_session, monkeypatch):
    # The model names another number: the server's sentence, with the real one, is used instead.
    await summary_shown(client, db_session, monkeypatch, [model_response(text="Your order JB-999 is in!")])
    body = (await client.post("/api/v1/assistant/confirm-order", json={"language": "en"})).json()
    number = body["action"]["order_number"]
    assert number != "JB-999" and body["response"].startswith(f"Your order {number}")


@pytest.mark.asyncio
async def test_button_confirmation_still_answers_without_the_model(client, db_session, monkeypatch):
    # No model reply (unavailable): the order is sent and the server's sentence says so.
    await summary_shown(client, db_session, monkeypatch)
    body = (await client.post("/api/v1/assistant/confirm-order", json={"language": "en"})).json()
    assert body["action"]["type"] == "ORDER_SUBMITTED" and body["response"].startswith("Your order JB-")
