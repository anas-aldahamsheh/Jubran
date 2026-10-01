"""Closing a table never shows one guest's complaints or internal ids to another guest."""
import json

import pytest
from sqlalchemy import select

from jubran.application.ai.tools import AssistantToolExecutor
from jubran.application.ordering_service import OrderingService
from jubran.application.service_request_service import CustomerServiceManager
from jubran.infrastructure.db.models import CustomerSessionModel, TableSessionModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit

COMPLAINT = "النادل تأخر علينا كثير وكان منزعج"


async def place_order(browser):
    products = (await browser.get("/api/v1/menu/products")).json()
    await browser.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 1})
    prepared = (await browser.post("/api/v1/draft/prepare-confirmation")).json()
    response = await browser.post("/api/v1/orders", json={
        "confirmation_token": prepared["confirmation_token"], "draft_version": prepared["draft_version"]})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_guests_never_see_the_staff_closure_note(client, db_session, new_browser):
    await seed_database(db_session)
    first_guest, second_guest, admin = client, new_browser(), new_browser()
    await start_visit(first_guest, db_session, "T4")
    visit = (await first_guest.get("/api/v1/session/context")).json()
    await start_visit(second_guest, db_session, "T4")
    await place_order(first_guest)
    assert (await second_guest.post("/api/v1/complaints", json={"message": COMPLAINT})).status_code == 200
    assert (await first_guest.post("/api/v1/service-requests", json={"type": "TISSUES"})).status_code == 200

    await sign_in(admin)
    closed = await admin.post(f"/api/v1/admin/table-sessions/{visit['table_session_id']}/close")
    assert closed.status_code == 200

    # The staff note is complete but carries no internal guest ids.
    session = (await db_session.execute(select(TableSessionModel).where(
        TableSessionModel.id == visit["table_session_id"]))).scalar_one()
    assert COMPLAINT in session.closure_note
    customer_ids = [c.id for c in (await db_session.execute(select(CustomerSessionModel))).scalars()]
    assert not any(customer_id in session.closure_note for customer_id in customer_ids)

    # Everything a guest can be shown about the visit.
    orders = await OrderingService.get_orders_for_customer(db_session, visit["table_session_id"])
    services = await CustomerServiceManager.get_service_requests_for_customer(
        db_session, visit["table_session_id"], visit["customer_session_id"])
    tool_view = await AssistantToolExecutor(db_session, visit["customer_session_id"], visit["table_session_id"],
                                            "T4").execute("get_order_status", {})
    guest_payload = json.dumps([orders, services, tool_view], ensure_ascii=False)

    assert COMPLAINT not in guest_payload
    assert "closure_note" not in guest_payload
    assert not any(customer_id in guest_payload for customer_id in customer_ids)
    assert orders[0]["cancelled_with_visit"] is True
    assert services[0]["cancelled_with_visit"] is True
