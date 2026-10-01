"""Integration tests for 2.5D floor snapshot, status projections, and admin operations."""
import pytest
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit


@pytest.mark.asyncio
async def test_floor_snapshot_and_state_projections(client, db_session, new_browser):
    await seed_database(db_session)

    # Admin signs in on one device; the guest uses their own phone.
    admin = client
    await sign_in(admin)
    guest = new_browser()

    # 1. Initial snapshot: all 12 tables are VACANT
    res = await admin.get("/api/v1/admin/floor")
    assert res.status_code == 200
    data = res.json()
    assert len(data["tables"]) == 12
    t4 = next(t for t in data["tables"] if t["table_number"] == "T4")
    assert t4["base_state"] == "VACANT"

    # 2. Customer scans T4 -> starts session -> T4 becomes OCCUPIED_IDLE
    await start_visit(guest, db_session, "T4")

    res2 = await admin.get("/api/v1/admin/floor")
    t4_after_scan = next(t for t in res2.json()["tables"] if t["table_number"] == "T4")
    assert t4_after_scan["base_state"] == "OCCUPIED_IDLE"
    table_session_id = t4_after_scan["active_session_id"]
    assert table_session_id is not None

    # 3. Customer submits an order on T4 -> T4 becomes ORDER_PENDING
    # Get a product to order
    prods = (await client.get("/api/v1/menu/products")).json()
    foul = next(p for p in prods if p["name_ar"] == "فول")

    await guest.post("/api/v1/draft/items", json={"product_id": foul["id"], "quantity": 1})
    prep = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    submit_res = await guest.post(
        "/api/v1/orders",
        json={"confirmation_token": prep["confirmation_token"], "draft_version": prep["draft_version"]}
    )
    order_id = submit_res.json()["order_id"]

    res3 = await admin.get("/api/v1/admin/floor")
    t4_after_order = next(t for t in res3.json()["tables"] if t["table_number"] == "T4")
    assert t4_after_order["base_state"] == "ORDER_PENDING"
    assert len(res3.json()["active_orders"]) >= 1

    # 4. Admin starts preparing order -> T4 becomes ORDER_PREPARING
    prep_order_res = await admin.post(f"/api/v1/admin/orders/{order_id}/start-preparing")
    assert prep_order_res.status_code == 200

    res4 = await admin.get("/api/v1/admin/floor")
    t4_preparing = next(t for t in res4.json()["tables"] if t["table_number"] == "T4")
    assert t4_preparing["base_state"] == "ORDER_PREPARING"

    # 5. Admin marks order ready -> T4 becomes ORDER_READY
    ready_res = await admin.post(f"/api/v1/admin/orders/{order_id}/mark-ready")
    assert ready_res.status_code == 200

    res5 = await admin.get("/api/v1/admin/floor")
    t4_ready = next(t for t in res5.json()["tables"] if t["table_number"] == "T4")
    assert t4_ready["base_state"] == "ORDER_READY"

    # 6. Admin marks served -> food delivered to table -> T4 returns to OCCUPIED_IDLE
    served_res = await admin.post(f"/api/v1/admin/orders/{order_id}/mark-served")
    assert served_res.status_code == 200

    res6 = await admin.get("/api/v1/admin/floor")
    t4_served = next(t for t in res6.json()["tables"] if t["table_number"] == "T4")
    assert t4_served["base_state"] == "OCCUPIED_IDLE"

    # Verify customer still sees order status as READY (mark served is an operational marker, not a 4th customer state)
    cust_orders = (await guest.get("/api/v1/orders")).json()
    assert cust_orders[0]["status"] == "READY"
    assert cust_orders[0]["is_served"] is True

    # 7. Admin closes table session -> T4 returns to VACANT
    close_res = await admin.post(f"/api/v1/admin/table-sessions/{table_session_id}/close")
    assert close_res.status_code == 200

    res7 = await admin.get("/api/v1/admin/floor")
    t4_closed = next(t for t in res7.json()["tables"] if t["table_number"] == "T4")
    assert t4_closed["base_state"] == "VACANT"
    assert t4_closed["active_session_id"] is None
