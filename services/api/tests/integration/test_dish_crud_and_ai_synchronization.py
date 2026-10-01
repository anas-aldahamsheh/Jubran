"""The model's menu tool always reads current admin-managed data."""
import pytest
from sqlalchemy import select

from jubran.application.ai.tools import AssistantToolExecutor
from jubran.infrastructure.db.models import CustomerSessionModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit


@pytest.mark.asyncio
async def test_menu_tool_reflects_admin_changes(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    categories = await client.get("/api/v1/menu/categories")
    category_id = categories.json()[0]["id"]
    created = await client.post("/api/v1/admin/menu/products", json={
        "category_id": category_id, "name_ar": "قلاية بندورة بلدي باللحمة",
        "name_en": "Local Tomato Pan with Meat", "description_ar": "بندورة ولحمة",
        "description_en": "Tomato and meat", "price_minor": 2750, "is_available": True,
    })
    assert created.status_code == 201
    product_id = created.json()["id"]
    await start_visit(client, db_session, "T3")
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    executor = AssistantToolExecutor(db_session, customer.id, customer.table_session_id, "T3")

    found = await executor.execute("search_knowledge", {
        "query": "قلاية بندورة", "source_types": ["product"]
    })
    assert next(m["product"] for m in found["matches"] if m["product"]["id"] == product_id)["price_minor"] == 2750

    updated = await client.patch(f"/api/v1/admin/menu/products/{product_id}",
                                 json={"price_minor": 3500})
    assert updated.status_code == 200
    details = await executor.execute("get_product_details", {"product_id": product_id})
    assert details["product"]["price_minor"] == 3500

    hidden = await client.patch(f"/api/v1/admin/menu/products/{product_id}/availability",
                                json={"is_available": False})
    assert hidden.status_code == 200
    # A hidden dish is still recognised, with its status, so the assistant can say
    # "it's on the menu but not available right now" instead of "we don't have it".
    shown = await executor.execute("search_knowledge", {
        "query": "قلاية بندورة", "source_types": ["product"]
    })
    hidden_match = next(m["product"] for m in shown["matches"] if m["product"]["id"] == product_id)
    assert hidden_match["is_available"] is False
    available = await executor.execute("search_knowledge", {
        "query": "قلاية بندورة", "source_types": ["product"], "available_only": True
    })
    assert product_id not in [m["product"]["id"] for m in available["matches"]]
