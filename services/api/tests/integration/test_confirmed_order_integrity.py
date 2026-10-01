"""A guest is only ever charged for exactly what they reviewed and confirmed."""
import pytest
from sqlalchemy import select, update

from jubran.infrastructure.db.models import DraftConfirmationModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit


async def basket_with(browser, *product_ids):
    for product_id in product_ids:
        added = await browser.post("/api/v1/draft/items", json={"product_id": product_id, "quantity": 1})
        assert added.status_code == 200, added.text
    prepared = await browser.post("/api/v1/draft/prepare-confirmation")
    assert prepared.status_code == 200, prepared.text
    return prepared.json()


async def submit(browser, prepared):
    return await browser.post("/api/v1/orders", json={
        "confirmation_token": prepared["confirmation_token"], "draft_version": prepared["draft_version"]})


async def two_products(db_session):
    products = (await db_session.execute(select(ProductModel).order_by(ProductModel.name_ar))).scalars().all()
    return products[0], products[1]


@pytest.mark.asyncio
async def test_price_change_after_review_needs_a_new_confirmation(client, db_session, new_browser):
    await seed_database(db_session)
    dish, _ = await two_products(db_session)
    new_price = dish.price_minor + 500
    await start_visit(client, db_session, "T2")
    prepared = await basket_with(client, dish.id)

    admin = new_browser()
    await sign_in(admin)
    changed = await admin.patch(f"/api/v1/admin/menu/products/{dish.id}", json={"price_minor": new_price})
    assert changed.status_code == 200

    refused = await submit(client, prepared)
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"]["code"] == "DRAFT_VERSION_CONFLICT"

    # After reviewing the new price the guest can order, and pays what they saw.
    again = (await client.post("/api/v1/draft/prepare-confirmation")).json()
    assert again["summary"]["items"][0]["unit_price_minor"] == new_price
    ordered = await submit(client, again)
    assert ordered.status_code == 200
    assert ordered.json()["total_minor"] == new_price


@pytest.mark.asyncio
async def test_any_change_to_reviewed_lines_is_detected(client, db_session):
    """Even a change made outside the admin screens (no basket version bump) is caught."""
    await seed_database(db_session)
    dish, _ = await two_products(db_session)
    await start_visit(client, db_session, "T3")
    prepared = await basket_with(client, dish.id)
    await db_session.execute(update(ProductModel).where(ProductModel.id == dish.id).values(price_minor=1))
    await db_session.commit()

    refused = await submit(client, prepared)
    assert refused.status_code == 409
    details = refused.json()["detail"]["error"]["details"]
    assert details["reason"] == "SUMMARY_CHANGED"
    assert details["summary"]["items"][0]["unit_price_minor"] == 1


@pytest.mark.asyncio
async def test_deleted_dish_never_silently_drops_from_a_confirmed_order(client, db_session, new_browser):
    await seed_database(db_session)
    first, second = await two_products(db_session)
    await start_visit(client, db_session, "T5")
    prepared = await basket_with(client, first.id, second.id)

    admin = new_browser()
    await sign_in(admin)
    assert (await admin.delete(f"/api/v1/admin/menu/products/{second.id}")).status_code == 200

    assert (await submit(client, prepared)).status_code == 409
    remaining = (await client.post("/api/v1/draft/prepare-confirmation")).json()
    assert [item["product_id"] for item in remaining["summary"]["items"]] == [first.id]


@pytest.mark.asyncio
async def test_unchanged_basket_is_ordered_normally(client, db_session):
    await seed_database(db_session)
    dish, _ = await two_products(db_session)
    await start_visit(client, db_session, "T6")
    prepared = await basket_with(client, dish.id)
    ordered = await submit(client, prepared)
    assert ordered.status_code == 200
    assert ordered.json()["total_minor"] == dish.price_minor


@pytest.mark.asyncio
async def test_confirmations_from_before_the_upgrade_must_be_reviewed_again(client, db_session):
    await seed_database(db_session)
    dish, _ = await two_products(db_session)
    await start_visit(client, db_session, "T7")
    prepared = await basket_with(client, dish.id)
    await db_session.execute(update(DraftConfirmationModel).values(summary_fingerprint=None))
    await db_session.commit()
    assert (await submit(client, prepared)).status_code == 409
