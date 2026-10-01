"""Kitchen workflow per the spec: pending -> preparing -> ready, then an optional "served" marker."""
import pytest

from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit


async def submitted_order(guest, db_session, table="T8"):
    await start_visit(guest, db_session, table)
    products = (await guest.get("/api/v1/menu/products")).json()
    await guest.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 1})
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    order = await guest.post("/api/v1/orders", json={
        "confirmation_token": prepared["confirmation_token"], "draft_version": prepared["draft_version"]})
    return order.json()["order_id"]


def table_state(floor, number):
    return next(t for t in floor["tables"] if t["table_number"] == number)["base_state"]


@pytest.mark.asyncio
async def test_steps_cannot_be_skipped_and_guest_sees_three_states(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    order_id = await submitted_order(guest, db_session)
    await sign_in(admin)

    # Not ready before cooking starts, not served before it is ready.
    assert (await admin.post(f"/api/v1/admin/orders/{order_id}/mark-ready")).status_code == 400
    assert (await admin.post(f"/api/v1/admin/orders/{order_id}/mark-served")).status_code == 400

    labels = []
    for step in ("start-preparing", "mark-ready"):
        assert (await admin.post(f"/api/v1/admin/orders/{order_id}/{step}")).status_code == 200
        labels.append((await guest.get("/api/v1/orders")).json()[0]["status_display_ar"])
    assert labels == ["قيد التحضير", "جاهز"]
    assert table_state((await admin.get("/api/v1/admin/floor")).json(), "T8") == "ORDER_READY"
    assert (await admin.post(f"/api/v1/admin/orders/{order_id}/start-preparing")).status_code == 400

    # Serving clears the green table marker, but the guest still sees "ready".
    assert (await admin.post(f"/api/v1/admin/orders/{order_id}/mark-served")).status_code == 200
    assert table_state((await admin.get("/api/v1/admin/floor")).json(), "T8") == "OCCUPIED_IDLE"
    order = (await guest.get("/api/v1/orders")).json()[0]
    assert (order["status"], order["status_display_ar"], order["status_display_en"]) == ("READY", "جاهز", "Ready")
    assert (await admin.post(f"/api/v1/admin/orders/{order_id}/mark-served")).status_code == 400
