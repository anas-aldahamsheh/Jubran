"""Closing a table: complaints stay open, cooked food is never silently cancelled."""
import pytest
from sqlalchemy import select

from jubran.domain.enums import ComplaintStatus, OrderStatus, TableSessionStatus
from jubran.infrastructure.db.models import ComplaintModel, OrderModel, TableSessionModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit

COMPLAINT = "الأكل وصل بارد"


async def order_something(guest):
    products = (await guest.get("/api/v1/menu/products")).json()
    await guest.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 1})
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    response = await guest.post("/api/v1/orders", json={
        "confirmation_token": prepared["confirmation_token"], "draft_version": prepared["draft_version"]})
    assert response.status_code == 200, response.text
    return response.json()["order_id"]


async def visit_on(guest, db_session, table):
    await start_visit(guest, db_session, table)
    return (await guest.get("/api/v1/session/context")).json()["table_session_id"]


async def fresh(db_session, model, id_):
    db_session.expire_all()
    return (await db_session.execute(select(model).where(model.id == id_))).scalar_one()


@pytest.mark.asyncio
async def test_complaints_survive_closing_and_can_still_be_handled(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    visit_id = await visit_on(guest, db_session, "T3")
    order_id = await order_something(guest)  # not started by the kitchen
    assert (await guest.post("/api/v1/complaints", json={"message": COMPLAINT})).status_code == 200
    assert (await guest.post("/api/v1/service-requests", json={"type": "TISSUES"})).status_code == 200
    await sign_in(admin)

    closed = await admin.post(f"/api/v1/admin/table-sessions/{visit_id}/close")
    assert closed.status_code == 200, closed.text
    assert (await fresh(db_session, OrderModel, order_id)).status == OrderStatus.CANCELLED

    complaint = (await db_session.execute(select(ComplaintModel))).scalar_one()
    assert complaint.status == ComplaintStatus.OPEN and complaint.closure_note is None
    queue = (await admin.get("/api/v1/admin/floor")).json()["service_queue"]
    open_items = {(item["kind"], item["status"]) for item in queue}
    assert ("COMPLAINT", "OPEN") in open_items
    assert ("SERVICE", "OPEN") not in open_items  # table services end with the visit

    assert (await admin.post(f"/api/v1/admin/complaints/{complaint.id}/start")).status_code == 200
    assert (await admin.post(f"/api/v1/admin/complaints/{complaint.id}/resolve")).status_code == 200
    assert (await fresh(db_session, ComplaintModel, complaint.id)).status == ComplaintStatus.RESOLVED


@pytest.mark.asyncio
async def test_food_being_cooked_needs_an_explicit_decision(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    visit_id = await visit_on(guest, db_session, "T4")
    order_id = await order_something(guest)
    await sign_in(admin)
    assert (await admin.post(f"/api/v1/admin/orders/{order_id}/start-preparing")).status_code == 200

    asked = await admin.post(f"/api/v1/admin/table-sessions/{visit_id}/close")
    assert asked.status_code == 409
    error = asked.json()["detail"]["error"]
    assert error["code"] == "KITCHEN_ORDERS_DECISION_REQUIRED"
    assert [o["order_id"] for o in error["details"]["orders"]] == [order_id]
    # Nothing changed yet.
    assert (await fresh(db_session, TableSessionModel, visit_id)).status == TableSessionStatus.ACTIVE
    assert (await fresh(db_session, OrderModel, order_id)).status == OrderStatus.PREPARING

    bad = await admin.post(f"/api/v1/admin/table-sessions/{visit_id}/close", json={"kitchen_orders": "maybe"})
    assert bad.status_code == 422

    delivered = await admin.post(f"/api/v1/admin/table-sessions/{visit_id}/close", json={"kitchen_orders": "delivered"})
    assert delivered.status_code == 200, delivered.text
    order = await fresh(db_session, OrderModel, order_id)
    assert order.status == OrderStatus.READY and order.served_at is not None
    assert (await fresh(db_session, TableSessionModel, visit_id)).status == TableSessionStatus.CLOSED


@pytest.mark.asyncio
async def test_ready_food_can_be_cancelled_on_purpose(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    visit_id = await visit_on(guest, db_session, "T5")
    order_id = await order_something(guest)
    await sign_in(admin)
    for step in ("start-preparing", "mark-ready"):
        assert (await admin.post(f"/api/v1/admin/orders/{order_id}/{step}")).status_code == 200

    cancelled = await admin.post(f"/api/v1/admin/table-sessions/{visit_id}/close", json={"kitchen_orders": "cancel"})
    assert cancelled.status_code == 200
    order = await fresh(db_session, OrderModel, order_id)
    assert order.status == OrderStatus.CANCELLED and order.served_at is None
