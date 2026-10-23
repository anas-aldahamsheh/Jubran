"""Notes and changes to a line, in the basket and in a sent order.

- Any wish about how a dish is prepared or served is its note, set, changed or removed at any time.
- Part of a line can get its own note ("two of the five teas without sugar").
- A field a change can't apply is refused with a clear error, never silently dropped.
- Staff see a new note on a sent order as a change, and a part given its own note was ordered with the rest.
"""
import pytest
from sqlalchemy import select

from jubran.application.ai.tools import AssistantToolExecutor
from jubran.application.ordering_service import OrderingService
from jubran.infrastructure.db.models import CustomerSessionModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit
from test_order_amendments import floor_order, my_order, place_order
from test_serving_units import boxed_hummus


async def seated(client, db_session, table="T4"):
    await seed_database(db_session, with_photos=True)  # the edit dialog sends the photo link back
    await start_visit(client, db_session, table)
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    products = {p.name_ar: p for p in (await db_session.execute(select(ProductModel))).scalars().all()}
    return AssistantToolExecutor(db_session, customer.id, customer.table_session_id, table), customer, products


def lines(draft):
    return [(line["name_ar"], line["quantity"], line.get("note")) for line in draft["items"]]


async def tea_line(executor, products, quantity):
    added = await executor.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": products["شاي"].id, "quantity": quantity}]})
    return added["draft"]["items"][0]["line_id"]


@pytest.mark.asyncio
async def test_a_basket_line_gets_changes_and_loses_its_note(client, db_session):
    executor, _, products = await seated(client, db_session)
    line_id = await tea_line(executor, products, 2)

    noted = await executor.execute("update_draft_order", {"operations": [
        {"op": "update", "line_id": line_id, "note": "بدون سكر"}]})
    assert noted["success"] and lines(noted["draft"]) == [("شاي", 2, "بدون سكر")]
    changed = await executor.execute("update_draft_order", {"operations": [
        {"op": "update", "line_id": line_id, "product_id": products["شاي"].id, "note": "سكر خفيف", "quantity": 3}]})
    assert lines(changed["draft"]) == [("شاي", 3, "سكر خفيف")]
    cleared = await executor.execute("update_draft_order", {"operations": [
        {"op": "update", "line_id": line_id, "note": ""}]})
    assert lines(cleared["draft"]) == [("شاي", 3, None)]
    # The older name of the change still works, and a field sent as None is not given.
    older = await executor.execute("update_draft_order", {"operations": [
        {"op": "set_quantity", "line_id": line_id, "quantity": 4, "note": None, "unit": None}]})
    assert lines(older["draft"]) == [("شاي", 4, None)]


@pytest.mark.asyncio
async def test_part_of_a_basket_line_gets_its_own_note(client, db_session):
    executor, _, products = await seated(client, db_session)
    line_id = await tea_line(executor, products, 5)

    split = await executor.execute("update_draft_order", {"operations": [
        {"op": "split", "line_id": line_id, "quantity": 2, "note": "بدون سكر"}]})
    assert split["success"] and lines(split["draft"]) == [("شاي", 3, None), ("شاي", 2, "بدون سكر")]
    assert split["draft"]["items"][0]["line_id"] == line_id  # the rest stays the line it was
    # The rest given the same note: one line again, as adding the same dish twice would be.
    same = await executor.execute("update_draft_order", {"operations": [
        {"op": "update", "line_id": line_id, "note": "بدون سكر"}]})
    assert lines(same["draft"]) == [("شاي", 5, "بدون سكر")]
    # All of a line "split" is the whole line changing.
    whole = await executor.execute("update_draft_order", {"operations": [
        {"op": "split", "line_id": line_id, "quantity": 5, "note": "سكر زيادة"}]})
    assert lines(whole["draft"]) == [("شاي", 5, "سكر زيادة")]


@pytest.mark.asyncio
async def test_a_basket_line_changes_how_it_is_served(client, db_session, new_browser):
    admin = new_browser()
    executor, _, products = await seated(client, db_session)
    await sign_in(admin)
    await boxed_hummus(admin)
    added = await executor.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": products["حمص"].id, "quantity": 3, "unit": "صحن"}]})
    line_id = added["draft"]["items"][0]["line_id"]

    boxed = await executor.execute("update_draft_order", {"operations": [
        {"op": "split", "line_id": line_id, "quantity": 1, "unit": "box"}]})
    assert sorted((line["unit"], line["quantity"]) for line in boxed["draft"]["items"]) == [("صحن", 2), ("علبة", 1)]
    refused = await executor.execute("update_draft_order", {"operations": [
        {"op": "update", "line_id": line_id, "unit": "كاسة"}]})
    assert (refused["error_code"], refused["units"]) == ("UNIT_NOT_OFFERED", ["صحن", "علبة"])


@pytest.mark.asyncio
@pytest.mark.parametrize("change, code", [
    ({"op": "remove", "note": "بدون سكر"}, "UNSUPPORTED_FIELD"),
    ({"op": "update", "quantity": 3, "extra_cheese": True}, "UNSUPPORTED_FIELD"),
    ({"op": "update"}, "NOTHING_TO_CHANGE"),
    ({"op": "update", "unit": "صحن"}, "UNIT_NOT_OFFERED"),
    ({"op": "split", "quantity": 1}, "SPLIT_WITHOUT_CHANGE"),
    ({"op": "split", "quantity": 3, "note": "بدون سكر"}, "INVALID_QUANTITY"),  # more than the line has
    ({"op": "update", "note": "بدون سكر", "product_id": "another-dish"}, "LINE_PRODUCT_MISMATCH"),
])
async def test_a_change_a_line_cant_take_changes_nothing(client, db_session, change, code):
    executor, customer, products = await seated(client, db_session)
    line_id = await tea_line(executor, products, 2)
    result = await executor.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": products["حمص"].id, "quantity": 1}, {**change, "line_id": line_id}]})
    assert (result["success"], result["error_code"], result["failed_operation_index"]) == (False, code, 1)
    if code == "UNSUPPORTED_FIELD":
        assert result["fields"] and "line_id" in result["allowed"]
    draft = await OrderingService.get_draft_summary(db_session, customer.id, customer.table_session_id)
    assert lines(draft) == [("شاي", 2, None)]  # nothing saved, not even the valid add


@pytest.mark.asyncio
async def test_a_note_on_a_sent_order_reaches_the_staff_as_a_change(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    await sign_in(admin)
    await start_visit(guest, db_session, "T2")
    order, _ = await place_order(guest, [("شاي", 5)])
    view = await my_order(guest, order["order_id"])
    tea = view["items"][0]
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    executor = AssistantToolExecutor(db_session, customer.id, customer.table_session_id, "T2")
    await admin.post(f"/api/v1/admin/orders/{order['order_id']}/start-preparing")

    split = await executor.execute("apply_order_amendment", {"order_id": order["order_id"], "operations": [
        {"op": "split", "item_id": tea["item_id"], "quantity": 2, "note": "بدون سكر"}]})
    assert split["success"], split
    changes = split["amendment"]["changes"]
    assert [(c["type"], c["quantity_before"], c["quantity_after"], c.get("note_after")) for c in changes] == [
        ("note_changed", 2, 2, "بدون سكر")]  # 2 of the 5 teas, not a dish "added"
    view = await my_order(guest, order["order_id"])
    assert sorted((i["quantity"], i["note"], i["added_later"]) for i in view["items"]) == [
        (2, "بدون سكر", False), (3, None, False)]  # ordered with the rest, not added later

    second = next(i for i in view["items"] if i["note"] is None)
    noted = await executor.execute("apply_order_amendment", {"order_id": order["order_id"], "operations": [
        {"op": "update", "item_id": second["item_id"], "note": "سكر خفيف"}]})
    assert noted["success"]
    [change] = noted["amendment"]["changes"]
    assert (change["type"], change["note_before"], change["note_after"]) == ("note_changed", None, "سكر خفيف")
    seen = await floor_order(admin, order["order_id"])
    assert seen["needs_attention"] is True  # the kitchen had started: staff are told
    recorded = [change["type"] for amendment in seen["amendments"] for change in amendment["changes"]]
    assert recorded.count("note_changed") == 2


@pytest.mark.asyncio
async def test_a_sent_order_takes_only_fields_a_change_can_apply(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T2")
    order, _ = await place_order(client, [("شاي", 2)])
    item = (await my_order(client, order["order_id"]))["items"][0]
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    executor = AssistantToolExecutor(db_session, customer.id, customer.table_session_id, "T2")
    for change, code in (({"op": "remove", "item_id": item["item_id"], "note": "x"}, "UNSUPPORTED_FIELD"),
                         ({"op": "update", "item_id": item["item_id"], "unit": "صحن"}, "UNIT_NOT_OFFERED"),
                         ({"op": "update", "item_id": item["item_id"]}, "NOTHING_TO_CHANGE")):
        result = await executor.execute("preview_order_amendment", {"order_id": order["order_id"],
                                                                     "operations": [change]})
        assert (result["success"], result["error_code"]) == (False, code)
    assert (await my_order(client, order["order_id"]))["items"][0]["quantity"] == 2
