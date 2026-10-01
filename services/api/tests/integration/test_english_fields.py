"""The English screens get English dish names and prices, not the Arabic ones."""
import pytest

from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit


@pytest.mark.asyncio
async def test_basket_orders_and_floor_have_english_names_and_prices(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    await start_visit(guest, db_session, "T3")

    products = {p["name_ar"]: p for p in (await guest.get("/api/v1/menu/products")).json()}
    tea = products["شاي"]
    draft = (await guest.post("/api/v1/draft/items", json={"product_id": tea["id"], "quantity": 2})).json()
    line = draft["items"][0]
    assert line["name_en"] == tea["name_en"]
    assert line["unit_price_display_en"].endswith("JOD") and line["line_total_display_en"].endswith("JOD")
    assert draft["total_display_en"].endswith("JOD")

    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    submitted = (await guest.post("/api/v1/orders", json={"confirmation_token": prepared["confirmation_token"],
                                                          "draft_version": prepared["draft_version"]})).json()
    assert submitted["status_display_en"] == "Awaiting confirmation"
    assert submitted["total_display_en"].endswith("JOD")
    assert submitted["items"][0]["name_en"] == tea["name_en"]

    order = (await guest.get("/api/v1/orders")).json()[0]
    assert order["total_display_en"].endswith("JOD")
    assert order["items"][0]["name_en"] == tea["name_en"]
    assert order["items"][0]["line_total_display_en"].endswith("JOD")

    await sign_in(admin)
    floor = (await admin.get("/api/v1/admin/floor")).json()
    floor_order = next(o for o in floor["active_orders"] if o["order_id"] == submitted["order_id"])
    assert floor_order["total_display_en"].endswith("JOD")
    assert floor_order["items"][0]["name_en"] == tea["name_en"]
    assert floor_order["items"][0]["unit_price_display_en"].endswith("JOD")
