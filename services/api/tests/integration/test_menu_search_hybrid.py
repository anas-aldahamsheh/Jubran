"""Menu search finds dishes by name as well as by meaning, and never hides a dish's status."""
import pytest
from sqlalchemy import select, update

from jubran.application.ai.semantic_retrieval import EmbeddingService
from jubran.application.ai.tool_views import model_view
from jubran.application.ai.tools import AssistantToolExecutor
from jubran.infrastructure.db.models import CustomerSessionModel, EMBEDDING_DIMENSIONS, ProductModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import start_visit


async def tools_for_guest(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    return AssistantToolExecutor(db_session, customer.id, customer.table_session_id, "T3")


def meaningless_embeddings(monkeypatch):
    """Vectors that relate nothing to anything: only name matching can find a dish."""
    async def flat(cls, db, texts, *, task_type):
        vector = [0.0] * EMBEDDING_DIMENSIONS
        vector[1 if task_type == "RETRIEVAL_DOCUMENT" else 2] = 1.0
        return [list(vector) for _ in texts]

    monkeypatch.setattr(EmbeddingService, "embed", classmethod(flat))
    monkeypatch.setattr(settings, "SEMANTIC_MIN_SIMILARITY", 0.65)


@pytest.mark.asyncio
@pytest.mark.parametrize("question, dish", [
    ("عندكم شقف؟", "شقف"),
    ("بدي صحن ريش", "ريش"),
    ("بدي الحمص", "حمص"),
    ("one hummus please", "حمص"),
])
async def test_short_dish_names_are_always_found(client, db_session, monkeypatch, question, dish):
    tools = await tools_for_guest(client, db_session)
    meaningless_embeddings(monkeypatch)
    result = await tools.execute("search_knowledge", {"query": question, "source_types": ["product"]})
    top = result["matches"][0]
    assert (top["product"]["name_ar"], top["match_type"]) == (dish, "name")


@pytest.mark.asyncio
async def test_unavailable_dish_is_recognised_but_cannot_be_ordered(client, db_session):
    tools = await tools_for_guest(client, db_session)
    await db_session.execute(update(ProductModel).where(ProductModel.name_ar == "شقف").values(is_available=False))
    await db_session.commit()

    found = await tools.execute("search_knowledge", {"query": "شقف", "source_types": ["product"]})
    qudsia = found["matches"][0]["product"]
    assert (qudsia["name_ar"], qudsia["is_available"]) == ("شقف", False)

    added = await tools.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": qudsia["id"], "quantity": 1}]})
    assert added["success"] is False


@pytest.mark.asyncio
async def test_hidden_dishes_do_not_crowd_out_available_ones(client, db_session):
    tools = await tools_for_guest(client, db_session)
    tea = (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == "شاي"))).scalar_one()
    await db_session.execute(update(ProductModel).where(ProductModel.id != tea.id).values(is_available=False))
    await db_session.commit()

    result = await tools.execute("search_knowledge", {
        "query": "طبق شعبي دافئ للفطور", "source_types": ["product"], "available_only": True, "limit": 1})
    assert [m["product"]["id"] for m in result["matches"]] == [tea.id]


@pytest.mark.asyncio
async def test_opening_hours_come_straight_from_the_database(client, db_session):
    tools = await tools_for_guest(client, db_session)
    info = await tools.execute("get_restaurant_info", {})
    assert info["success"] is True
    hours = info["restaurant"]["branch"]["opening_hours"]
    assert len(hours) == 7 and all(day["opens_at"] and day["closes_at"] for day in hours)
    assert info["restaurant"]["phone"]
    # The guest's own branch (the table's); Jubran has no other branches.
    assert "العبدلي" in info["restaurant"]["branch"]["name_ar"]
    assert info["restaurant"]["other_branches"] == []
    view = model_view("get_restaurant_info", info)
    assert view["current_branch"]["name_ar"] == info["restaurant"]["branch"]["name_ar"]
    assert "id" not in view["current_branch"] and not view.get("other_branches")
