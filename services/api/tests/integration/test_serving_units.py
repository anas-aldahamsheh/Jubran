"""Serving units: how each dish is counted (plate, glass, box...) and the way each line is served.

- A dish without units gets one automatically; a dish may be served several ways, at one price.
- A dish served several ways needs the guest's choice, and each way is its own line: in the
  basket, the order the restaurant receives, changes to it and every staff screen.
- The assistant holds the whole menu with its units, and serves each dish the way it comes.
"""
import pytest

from jubran.application.ai.agent_service import AssistantService, instruction_with
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import sign_in, start_visit
from test_model_agent import ScriptedChat, model_call, model_response
from test_waiter_logic import chat


async def boxed_hummus(admin) -> dict:
    """The admin says hummus also comes in a box (at the same price), saving the edit dialog as it
    is sent: every field, including the link to the dish's photo."""
    products = (await admin.get("/api/v1/menu/products")).json()
    hummus = next(product for product in products if product["name_ar"] == "حمص")
    assert hummus["image_asset_url"]
    form = {key: hummus[key] or "" for key in ("category_id", "name_ar", "name_en", "description_ar",
                                                "description_en", "image_asset_url")}
    response = await admin.patch(f"/api/v1/admin/menu/products/{hummus['id']}", json={
        **form, "price_minor": hummus["price_minor"], "is_available": True, "units": ["صحن", "علبة"]})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_units_are_automatic_until_the_admin_sets_them(client, db_session, new_browser):
    await seed_database(db_session, with_photos=True)  # the edit dialog sends the photo link back
    admin = new_browser()
    await sign_in(admin)
    products = {product["name_en"]: product for product in (await admin.get("/api/v1/menu/products")).json()}
    assert (products["Hummus"]["units"], products["Hummus"]["units_automatic"]) == (["صحن"], True)
    assert products["Chicken Shawarma Skewer"]["units"] == ["سيخ"]
    assert products["Still Mineral Water"]["units"] == ["قنينة"]
    assert (products["Tea Selection"]["units_display_en"], products["Soft Drinks"]["units_display_en"]) == (["glass"], ["can"])

    hummus = await boxed_hummus(admin)
    assert (hummus["units"], hummus["units_automatic"], hummus["units_display_en"]) == (
        ["صحن", "علبة"], False, ["plate", "box"])
    automatic = (await admin.patch(f"/api/v1/admin/menu/products/{hummus['id']}", json={"units": []})).json()
    assert (automatic["units"], automatic["units_automatic"]) == (["صحن"], True)
    created = await admin.post("/api/v1/admin/menu/products", json={
        "category_id": hummus["category_id"], "name_ar": "متبل", "name_en": "Mutabbal", "price_minor": 2000,
        "units": [" زبدية ", "bowl", "علبة"]})  # "bowl" is «زبدية» again
    assert created.status_code == 201 and created.json()["units"] == ["زبدية", "علبة"]


@pytest.mark.asyncio
async def test_a_dish_served_two_ways_is_two_lines_for_the_guest_and_the_staff(client, db_session, new_browser):
    await seed_database(db_session, with_photos=True)  # the edit dialog sends the photo link back
    admin = new_browser()
    await sign_in(admin)
    hummus = await boxed_hummus(admin)
    await start_visit(client, db_session, "T2")

    unclear = await client.post("/api/v1/draft/items", json={"product_id": hummus["id"], "quantity": 1})
    error = unclear.json()["detail"]["error"]
    assert unclear.status_code == 422 and error["code"] == "UNIT_REQUIRED"
    assert error["details"]["units"] == ["صحن", "علبة"]
    for unit in ("صحن", "علبة", "box"):  # "box" is the same way as «علبة»
        added = await client.post("/api/v1/draft/items", json={"product_id": hummus["id"], "quantity": 1, "unit": unit})
        assert added.status_code == 200, added.text
    lines = {line["unit"]: line for line in added.json()["items"]}
    assert {unit: line["quantity"] for unit, line in lines.items()} == {"صحن": 1, "علبة": 2}
    assert lines["علبة"]["unit_display_en"] == "box"
    assert lines["علبة"]["unit_price_minor"] == lines["صحن"]["unit_price_minor"]

    prepared = (await client.post("/api/v1/draft/prepare-confirmation")).json()
    order = (await client.post("/api/v1/orders", json={"confirmation_token": prepared["confirmation_token"],
                                                       "draft_version": prepared["draft_version"]})).json()
    mine = next(o for o in (await client.get("/api/v1/orders")).json() if o["order_id"] == order["order_id"])
    assert sorted((item["unit"], item["quantity"]) for item in mine["items"]) == [("صحن", 1), ("علبة", 2)]
    floor = (await admin.get("/api/v1/admin/floor")).json()
    seen = next(o for o in floor["active_orders"] if o["order_id"] == order["order_id"])
    assert sorted((item["unit_display_ar"], item["quantity"]) for item in seen["items"]) == [("صحن", 1), ("علبة", 2)]

    # A change to the sent order needs the way too, and adds to the line served that way.
    change = {"op": "add", "product_id": hummus["id"], "quantity": 1}
    refused = await client.post(f"/api/v1/orders/{order['order_id']}/amend",
                                json={"expected_version": mine["version"], "operations": [change]})
    assert refused.status_code == 422 and refused.json()["detail"]["error"]["details"]["units"] == ["صحن", "علبة"]
    amended = await client.post(f"/api/v1/orders/{order['order_id']}/amend",
                                json={"expected_version": mine["version"], "operations": [{**change, "unit": "علبة"}]})
    assert amended.status_code == 200, amended.text
    [line] = amended.json()["changes"]
    assert (line["type"], line["unit"], line["quantity_before"], line["quantity_after"]) == (
        "quantity_changed", "علبة", 2, 3)


@pytest.mark.asyncio
async def test_the_assistant_holds_the_menu_and_asks_which_way_only_when_there_are_several(
        client, db_session, new_browser, monkeypatch):
    await seed_database(db_session, with_photos=True)  # the edit dialog sends the photo link back
    admin = new_browser()
    await sign_in(admin)
    hummus = await boxed_hummus(admin)
    await start_visit(client, db_session, "T3")
    monkeypatch.setattr(settings, "DEBUG", True)  # tool results are returned for inspection
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-test-key")
    tea = next(p for p in (await client.get("/api/v1/menu/products")).json() if p["name_ar"] == "شاي")
    add = {"op": "add", "product_id": hummus["id"], "quantity": 1, "guest_gave_quantity": True}
    chats = [ScriptedChat([model_response(model_call("update_draft_order", still_asking=True, operations=[
                 add, {"op": "add", "product_id": tea["id"], "quantity": 2, "guest_gave_quantity": True}])),
                 model_response(text="ضفت كاستين شاي. بدك الحمص صحن ولا علبة؟")]),
             ScriptedChat([model_response(model_call("update_draft_order", operations=[
                 {**add, "unit": "صحن"}, {**add, "quantity": 2, "unit": "box"}])),
                 model_response(text="تم، ضفت صحن حمص وعلبتين سفري.")])]
    menus = []

    def create_chat(cls, history, table, config, context, language=None, menu=None):
        menus.append(menu)
        return chats[len(menus) - 1]

    monkeypatch.setattr(AssistantService, "_create_chat", classmethod(create_chat))
    asked = await chat(client, "بدي حمص واحد و٢ شاي")
    instruction = instruction_with(menus[0])
    assert f"{hummus['id']} | حمص | Hummus | 4.2 | صحن or علبة" in instruction
    assert f"{tea['id']} | شاي | Tea Selection | 3.75 | كاسة" in instruction
    assert "recently_shown_dishes" not in chats[0].context  # nothing to look up: the whole menu is there
    result = asked["tool_calls"][0]["result"]
    assert result["partial_success"] and result["blocked_operations"][0]["error_code"] == "UNIT_REQUIRED"
    assert result["blocked_operations"][0]["units"] == ["صحن", "علبة"] and "which one" in result["hint"]
    assert [(line["name_ar"], line["unit"]) for line in result["basket"]["items"]] == [("شاي", "كاسة")]

    served = await chat(client, "صحن هون وعلبتين سفري")
    lines = {(item["name_ar"], item["unit"]): item["quantity"] for item in served["draft"]["items"]}
    assert lines == {("شاي", "كاسة"): 2, ("حمص", "صحن"): 1, ("حمص", "علبة"): 2}
