"""Integration tests for Menu, Draft, Confirmation, and Ordering."""
import pytest
from sqlalchemy import select
from jubran.infrastructure.db.menu_data import MENU
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit
from jubran.infrastructure.db.models import ProductModel
from jubran.domain.enums import OrderStatus
from jubran.application.ordering_service import OrderingService


@pytest.mark.asyncio
async def test_full_ordering_flow_and_invariants(client, db_session):
    await seed_database(db_session)

    # 1. Start QR Table Session (T3)
    await start_visit(client, db_session, "T3")

    # 2. Browse Menu Categories and Products
    cats_res = await client.get("/api/v1/menu/categories")
    assert cats_res.status_code == 200
    cats = cats_res.json()
    assert len(cats) == len(MENU)

    prods_res = await client.get("/api/v1/menu/products")
    assert prods_res.status_code == 200
    products = prods_res.json()
    assert len(products) == sum(len(dishes) for _, _, dishes in MENU)

    hummus = next(p for p in products if p["name_ar"] == "حمص")
    tea = next(p for p in products if p["name_ar"] == "شاي")

    # 3. Add Hummus with sanitized custom note (REQ-005, REQ-022)
    custom_note = "<script>alert('hack')</script>بدون شطة مع زيت زيادة"
    add1_res = await client.post(
        "/api/v1/draft/items",
        json={"product_id": hummus["id"], "quantity": 2, "note": custom_note}
    )
    assert add1_res.status_code == 200
    draft1 = add1_res.json()
    assert draft1["version"] == 2
    assert draft1["total_minor"] == 4200 * 2  # 8400 fils
    # Verify note sanitized (<script> removed)
    assert "<script>" not in draft1["items"][0]["note"]
    assert "بدون شطة مع زيت زيادة" in draft1["items"][0]["note"]

    # 4. Add Tea
    add2_res = await client.post(
        "/api/v1/draft/items",
        json={"product_id": tea["id"], "quantity": 1, "note": "مع نعناع"}
    )
    assert add2_res.status_code == 200
    draft2 = add2_res.json()
    assert draft2["version"] == 3
    assert draft2["total_minor"] == (4200 * 2) + 3750  # 12150 fils

    # 5. Unavailable product blocking test (REQ-003)
    falafel = next(p for p in products if p["name_ar"] == "كبة حماتي")
    # Temporarily set unavailable in DB
    falafel_db = (await db_session.execute(select(ProductModel).where(ProductModel.id == falafel["id"]))).scalar_one()
    falafel_db.is_available = False
    await db_session.commit()

    unavail_add = await client.post(
        "/api/v1/draft/items",
        json={"product_id": falafel["id"], "quantity": 5}
    )
    assert unavail_add.status_code == 400
    assert unavail_add.json()["detail"]["error"]["code"] == "PRODUCT_UNAVAILABLE"

    # 6. Prepare confirmation challenge (REQ-006)
    prep_res = await client.post("/api/v1/draft/prepare-confirmation")
    assert prep_res.status_code == 200
    prep_data = prep_res.json()
    conf_token = prep_data["confirmation_token"]
    draft_version = prep_data["draft_version"]
    assert draft_version == 3

    # 7. Modify draft after confirmation token issued -> should invalidate confirmation version (REQ-006 invariant)
    await client.patch(
        f"/api/v1/draft/items/{draft2['items'][1]['line_id']}",
        json={"quantity": 2}
    )
    # Draft is now version 4. Attempting submit with version 3 must fail with 409 Conflict!
    stale_submit = await client.post(
        "/api/v1/orders",
        json={"confirmation_token": conf_token, "draft_version": draft_version}
    )
    assert stale_submit.status_code == 409
    assert stale_submit.json()["detail"]["error"]["code"] == "DRAFT_VERSION_CONFLICT"

    # 8. Re-prepare confirmation for current version
    prep2_res = await client.post("/api/v1/draft/prepare-confirmation")
    assert prep2_res.status_code == 200
    new_conf_token = prep2_res.json()["confirmation_token"]
    new_version = prep2_res.json()["draft_version"]

    # 9. Submit order with idempotency key
    idemp_key = "unique-order-key-12345"
    submit_res = await client.post(
        "/api/v1/orders",
        json={"confirmation_token": new_conf_token, "draft_version": new_version},
        headers={"Idempotency-Key": idemp_key}
    )
    assert submit_res.status_code == 200
    order_data = submit_res.json()
    assert order_data["status"] == "PENDING_APPROVAL"
    assert order_data["order_number"].startswith("JB-")
    first_order_id = order_data["order_id"]

    # 10. Repeated submit with same Idempotency-Key returns identical response (REQ-006)
    replay_submit = await client.post(
        "/api/v1/orders",
        json={"confirmation_token": new_conf_token, "draft_version": new_version},
        headers={"Idempotency-Key": idemp_key}
    )
    assert replay_submit.status_code == 200
    assert replay_submit.json()["order_id"] == first_order_id

    # 11. Add to existing visit: creating a 2nd order during same table visit (REQ-011)
    # Draft is now clear; add a burger
    burger = next(p for p in products if p["name_ar"] == "نشمي برغر")
    await client.post(
        "/api/v1/draft/items",
        json={"product_id": burger["id"], "quantity": 1}
    )
    prep3 = await client.post("/api/v1/draft/prepare-confirmation")
    submit2 = await client.post(
        "/api/v1/orders",
        json={"confirmation_token": prep3.json()["confirmation_token"], "draft_version": prep3.json()["draft_version"]},
        headers={"Idempotency-Key": "second-order-key-54321"}
    )
    assert submit2.status_code == 200
    second_order_id = submit2.json()["order_id"]
    assert second_order_id != first_order_id

    # 12. Check customer orders list reflects both separate orders under same table visit
    orders_res = await client.get("/api/v1/orders")
    assert orders_res.status_code == 200
    orders_list = orders_res.json()
    assert len(orders_list) == 2
    order_ids = {o["order_id"] for o in orders_list}
    assert first_order_id in order_ids
    assert second_order_id in order_ids

    # 13. State Transition validation (REQ-007)
    # PENDING_APPROVAL -> PREPARING
    order_obj = await OrderingService.update_order_status(db_session, first_order_id, OrderStatus.PREPARING)
    assert order_obj.status == OrderStatus.PREPARING

    # PREPARING -> READY
    order_obj2 = await OrderingService.update_order_status(db_session, first_order_id, OrderStatus.READY)
    assert order_obj2.status == OrderStatus.READY

    # Illegal transition: READY -> PREPARING (must raise exception)
    with pytest.raises(Exception):
        await OrderingService.update_order_status(db_session, first_order_id, OrderStatus.PREPARING)
