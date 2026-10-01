"""One suggestion per order, chosen by the model from live menu data (no fixed word lists)."""
import pytest
from sqlalchemy import select

from jubran.application.ai.tools import AssistantToolExecutor
from jubran.infrastructure.db.models import AssistantConversationModel, CustomerSessionModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit
from test_model_agent import model_call, model_response, script_model


async def guest_tools(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    return AssistantToolExecutor(db_session, customer.id, customer.table_session_id, "T3")


async def product(db_session, name):
    return (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == name))).scalar_one()


async def add(tools, db_session, name):
    item = await product(db_session, name)
    assert (await tools.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": item.id, "quantity": 1}]}))["success"]


@pytest.mark.asyncio
async def test_suggestion_material_puts_what_the_basket_lacks_first(client, db_session):
    """No fixed dish list: the model gets the basket and the categories it has nothing from, first."""
    tools = await guest_tools(client, db_session)
    await add(tools, db_session, "حمص")
    result = await tools.execute("recommend_products", {})
    assert [item["name_ar"] for item in result["basket"]] == ["حمص"]
    assert result["options"] and result["options"][0]["category_en"] != "Cold Appetizers"
    assert {"Cold Drinks", "Fresh Juices", "From The Grill"} <= {option["category_en"] for option in result["options"]}
    assert all(option["name_ar"] != "حمص" for option in result["options"])


@pytest.mark.asyncio
async def test_popular_dishes_come_from_real_orders(client, db_session):
    tools = await guest_tools(client, db_session)
    await add(tools, db_session, "شقف")
    prepared = await tools.execute("prepare_order_confirmation", {})
    assert (await tools.execute("submit_confirmed_order", {
        "confirmation_token": prepared["confirmation_token"], "draft_version": prepared["draft_version"]}))["success"]
    result = await tools.execute("recommend_products", {"explicit_request": True})
    assert [dish["name_ar"] for dish in result["popular"]] == ["شقف"]


@pytest.mark.asyncio
async def test_unavailable_dishes_are_never_suggested(client, db_session):
    tools = await guest_tools(client, db_session)
    tea = await product(db_session, "شاي")
    tea.is_available = False
    await db_session.commit()
    await add(tools, db_session, "فول")
    result = await tools.execute("recommend_products", {})
    assert "شاي" not in [option["name_ar"] for option in result["options"] + result["popular"]]


@pytest.mark.asyncio
async def test_a_second_order_in_the_same_visit_can_get_a_suggestion_again(client, db_session, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    customer_id = (await client.get("/api/v1/session/context")).json()["customer_session_id"]
    hummus = await product(db_session, "حمص")
    script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query="حمص", source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": hummus.id, "quantity": 1}])),
         model_response(model_call("recommend_products")),
         model_response(text="بتحب تضيف شاي؟")],
        [model_response(model_call("prepare_order_confirmation", customer_finished=True)),
         model_response(text="هذا الملخص. بتأكد؟")],
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="أكد")), model_response(text="تم.")],
    )
    await client.post("/api/v1/assistant/chat", json={"message": "بدي حمص وخلص"})

    async def suggested():
        db_session.expire_all()
        return (await db_session.execute(select(AssistantConversationModel.upsell_suggested).where(
            AssistantConversationModel.customer_session_id == customer_id))).scalar_one()

    assert await suggested() is True
    await client.post("/api/v1/assistant/chat", json={"message": "لا شكراً"})
    sent = await client.post("/api/v1/assistant/chat", json={"message": "أكد"})
    assert sent.json()["action"]["type"] == "ORDER_SUBMITTED"
    assert await suggested() is False
