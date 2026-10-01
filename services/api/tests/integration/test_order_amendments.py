"""Guests change orders they already sent, like telling the waiter "one more tea, and drop the hummus".

- Waiting for the restaurant: the change applies quietly.
- Being prepared: it applies and stays highlighted for staff until acknowledged.
- Ready or served: locked (more dishes become a new order).
- Only the guest who sent the order can change it.
"""
import pytest
from sqlalchemy import select, update

from jubran.infrastructure.db.models import ProductModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit


async def place_order(guest, dishes):
    products = {p["name_ar"]: p for p in (await guest.get("/api/v1/menu/products")).json()}
    for name, quantity in dishes:
        response = await guest.post("/api/v1/draft/items", json={"product_id": products[name]["id"], "quantity": quantity})
        assert response.status_code == 200, response.text
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    order = (await guest.post("/api/v1/orders", json={"confirmation_token": prepared["confirmation_token"],
                                                      "draft_version": prepared["draft_version"]})).json()
    return order, products


async def my_order(guest, order_id):
    return next(o for o in (await guest.get("/api/v1/orders")).json() if o["order_id"] == order_id)


async def floor_order(admin, order_id):
    floor = (await admin.get("/api/v1/admin/floor")).json()
    return next(o for o in floor["active_orders"] + floor["completed_orders"] if o["order_id"] == order_id)


@pytest.mark.asyncio
async def test_pending_order_changes_apply_quietly(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    await sign_in(admin)
    await start_visit(guest, db_session, "T2")
    order, products = await place_order(guest, [("حمص", 1), ("شاي", 2)])

    view = await my_order(guest, order["order_id"])
    assert view["mine"] and view["editable"] and view["locked_reason"] is None
    tea = next(item for item in view["items"] if item["name_ar"] == "شاي")
    hummus = next(item for item in view["items"] if item["name_ar"] == "حمص")

    response = await guest.post(f"/api/v1/orders/{order['order_id']}/amend", json={
        "expected_version": view["version"],
        "operations": [{"op": "set_quantity", "item_id": tea["item_id"], "quantity": 3},
                       {"op": "remove", "item_id": hummus["item_id"]},
                       {"op": "add", "product_id": products["كبة حماتي"]["id"], "quantity": 6}],
    })
    assert response.status_code == 200, response.text
    change = response.json()
    assert change["applied"] and change["order_status"] == "PENDING_APPROVAL"
    kinds = {(c["name_ar"], c["type"]) for c in change["changes"]}
    assert kinds == {("شاي", "quantity_changed"), ("حمص", "removed"), ("كبة حماتي", "added")}

    view = await my_order(guest, order["order_id"])
    assert {(i["name_ar"], i["quantity"], i["added_later"]) for i in view["items"]} == {
        ("شاي", 3, False), ("كبة حماتي", 6, True)}
    assert view["amended"] and view["version"] == change["order_version"]

    seen = await floor_order(admin, order["order_id"])
    assert len(seen["amendments"]) == 1 and seen["needs_attention"] is False  # quiet: not being cooked yet
    assert seen["total_minor"] == 3 * products["شاي"]["price_minor"] + 6 * products["كبة حماتي"]["price_minor"]

    # Looking at an older version of the order: review again instead of guessing.
    stale = await guest.post(f"/api/v1/orders/{order['order_id']}/amend", json={
        "expected_version": view["version"] - 1, "operations": [{"op": "remove", "item_id": tea["item_id"]}]})
    assert stale.status_code == 409 and stale.json()["detail"]["error"]["code"] == "ORDER_CHANGED"


@pytest.mark.asyncio
async def test_change_while_cooking_is_highlighted_until_staff_acknowledge(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    await sign_in(admin)
    await start_visit(guest, db_session, "T3")
    order, products = await place_order(guest, [("فول", 1)])
    assert (await admin.post(f"/api/v1/admin/orders/{order['order_id']}/start-preparing")).status_code == 200

    response = await guest.post(f"/api/v1/orders/{order['order_id']}/amend", json={
        "operations": [{"op": "add", "product_id": products["شاي"]["id"], "quantity": 1}]})
    assert response.status_code == 200, response.text
    assert response.json()["kitchen_already_preparing"] is True

    seen = await floor_order(admin, order["order_id"])
    assert seen["needs_attention"] is True and seen["status"] == "PREPARING"
    assert seen["amendments"][0]["changes"][0]["type"] == "added"
    assert any(item["added_later"] for item in seen["items"])

    assert (await admin.post(f"/api/v1/admin/orders/{order['order_id']}/acknowledge-changes")).json()["acknowledged"] == 1
    seen = await floor_order(admin, order["order_id"])
    assert seen["needs_attention"] is False and seen["amendments"][0]["acknowledged"] is True


@pytest.mark.asyncio
async def test_ready_and_served_orders_are_locked(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    await sign_in(admin)
    await start_visit(guest, db_session, "T6")
    order, products = await place_order(guest, [("حمص", 1)])
    item_id = (await my_order(guest, order["order_id"]))["items"][0]["item_id"]
    for step, reason in (("start-preparing", None), ("mark-ready", "READY"), ("mark-served", "SERVED")):
        assert (await admin.post(f"/api/v1/admin/orders/{order['order_id']}/{step}")).status_code == 200
        view = await my_order(guest, order["order_id"])
        assert view["locked_reason"] == reason and view["editable"] is (reason is None)
        if reason:
            refused = await guest.post(f"/api/v1/orders/{order['order_id']}/amend", json={
                "operations": [{"op": "set_quantity", "item_id": item_id, "quantity": 2}]})
            assert refused.status_code == 409
            error = refused.json()["detail"]["error"]
            assert error["code"] == "ORDER_LOCKED" and error["details"]["reason"] == reason


@pytest.mark.asyncio
async def test_only_the_guest_who_ordered_can_change_it(client, db_session, new_browser):
    await seed_database(db_session)
    first, second = client, new_browser()
    await start_visit(first, db_session, "T8")
    order, products = await place_order(first, [("فتوش", 1)])
    await start_visit(second, db_session, "T8")  # a friend joins the same table

    seen_by_friend = await my_order(second, order["order_id"])
    assert seen_by_friend["mine"] is False and seen_by_friend["editable"] is False
    refused = await second.post(f"/api/v1/orders/{order['order_id']}/amend", json={
        "operations": [{"op": "remove", "item_id": seen_by_friend["items"][0]["item_id"]}]})
    assert refused.status_code == 403 and refused.json()["detail"]["error"]["code"] == "ORDER_NOT_YOURS"


@pytest.mark.asyncio
async def test_removing_everything_cancels_the_order_by_the_guest(client, db_session):
    await seed_database(db_session)
    guest = client
    await start_visit(guest, db_session, "T9")
    order, _ = await place_order(guest, [("شقف", 1)])
    item_id = (await my_order(guest, order["order_id"]))["items"][0]["item_id"]
    response = await guest.post(f"/api/v1/orders/{order['order_id']}/amend",
                                json={"operations": [{"op": "remove", "item_id": item_id}]})
    assert response.status_code == 200 and response.json()["cancels_order"] is True
    view = await my_order(guest, order["order_id"])
    assert view["status"] == "CANCELLED" and view["cancelled_by_guest"] and not view["cancelled_with_visit"]


@pytest.mark.asyncio
async def test_more_of_a_dish_needs_it_available_and_uses_todays_price(client, db_session):
    await seed_database(db_session)
    guest = client
    await start_visit(guest, db_session, "T10")
    order, products = await place_order(guest, [("شاي", 2)])
    item_id = (await my_order(guest, order["order_id"]))["items"][0]["item_id"]
    tea_id = products["شاي"]["id"]

    await db_session.execute(update(ProductModel).where(ProductModel.id == tea_id).values(is_available=False))
    await db_session.commit()
    more = await guest.post(f"/api/v1/orders/{order['order_id']}/amend",
                            json={"operations": [{"op": "set_quantity", "item_id": item_id, "quantity": 3}]})
    assert more.status_code == 422 and more.json()["detail"]["error"]["code"] == "PRODUCT_UNAVAILABLE"
    fewer = await guest.post(f"/api/v1/orders/{order['order_id']}/amend",
                             json={"operations": [{"op": "set_quantity", "item_id": item_id, "quantity": 1}]})
    assert fewer.status_code == 200, fewer.text  # fewer is always fine

    old_price = products["شاي"]["price_minor"]
    await db_session.execute(update(ProductModel).where(ProductModel.id == tea_id)
                             .values(is_available=True, price_minor=old_price + 250))
    await db_session.commit()
    more = await guest.post(f"/api/v1/orders/{order['order_id']}/amend",
                            json={"operations": [{"op": "set_quantity", "item_id": item_id, "quantity": 2}]})
    assert more.status_code == 200, more.text
    lines = sorted((i["quantity"], i["unit_price_display"]) for i in (await my_order(guest, order["order_id"]))["items"])
    assert len(lines) == 2  # the extra tea is a new line at today's price; the first keeps its price
    assert (await db_session.execute(select(ProductModel.price_minor).where(ProductModel.id == tea_id))).scalar_one() == old_price + 250
