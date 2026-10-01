"""Tables nobody closed are closed automatically, but never one that is still in use."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from jubran.application.floor_service import FloorService
from jubran.domain.enums import TableSessionStatus
from jubran.infrastructure.db.models import CustomerSessionModel, OutboxEventModel, TableSessionModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import USER_EMAIL, USER_PASSWORD, sign_in, start_visit

LATER = timedelta(hours=7)  # more than TABLE_SESSION_IDLE_HOURS (6) after everything below


async def order_tea(guest):
    products = (await guest.get("/api/v1/menu/products")).json()
    await guest.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 1})
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    order = await guest.post("/api/v1/orders", json={
        "confirmation_token": prepared["confirmation_token"], "draft_version": prepared["draft_version"]})
    assert order.status_code in (200, 201), order.text
    return order.json()


async def visit_of(guest):
    return (await guest.get("/api/v1/session/context")).json()["table_session_id"]


async def status_of(db, visit_id):
    db.expire_all()
    return (await db.execute(select(TableSessionModel).where(TableSessionModel.id == visit_id))).scalar_one()


@pytest.mark.asyncio
async def test_abandoned_table_is_closed_and_the_guest_is_told(client, db_session, new_browser):
    await seed_database(db_session)
    admin = new_browser()
    await sign_in(admin)
    await start_visit(client, db_session, "T5")
    visit = await visit_of(client)
    order = await order_tea(client)
    for step in ("start-preparing", "mark-ready", "mark-served"):
        assert (await admin.post(f"/api/v1/admin/orders/{order['order_id']}/{step}")).status_code == 200
    await client.post("/api/v1/service-requests", json={"type": "BILL"})

    # Guests left hours ago and nobody closed the table.
    closed = await FloorService.close_idle_table_sessions(db_session, now=datetime.now(timezone.utc) + LATER)
    assert closed == ["T5"]
    session = await status_of(db_session, visit)
    assert session.status == TableSessionStatus.CLOSED and session.closed_by_admin_id is None
    assert "تلقائياً" in session.closure_note and "طلب الحساب" in session.closure_note
    events = (await db_session.execute(select(OutboxEventModel.event_type))).scalars().all()
    assert "table_session.closed" in events  # the floor screen and the guest's page hear about it

    gone = await client.get("/api/v1/orders")
    assert gone.status_code == 401 and gone.json()["detail"]["error"]["code"] == "SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_tables_in_use_stay_open(client, db_session, new_browser):
    await seed_database(db_session)
    later = datetime.now(timezone.utc) + LATER

    # A guest whose page is still open (it keeps checking in) an hour before "later".
    await start_visit(client, db_session, "T6")
    busy = await visit_of(client)
    await db_session.execute(update(CustomerSessionModel).where(CustomerSessionModel.table_session_id == busy)
                             .values(last_seen_at=later - timedelta(hours=1)))
    await db_session.commit()

    # An order still waiting for the kitchen, however old: staff must decide.
    waiting_guest = new_browser()
    await start_visit(waiting_guest, db_session, "T7")
    waiting = await visit_of(waiting_guest)
    await order_tea(waiting_guest)

    # An order being cooked.
    cooking_guest, admin = new_browser(), new_browser()
    await sign_in(admin)
    await start_visit(cooking_guest, db_session, "T8")
    cooking = await visit_of(cooking_guest)
    order = await order_tea(cooking_guest)
    await admin.post(f"/api/v1/admin/orders/{order['order_id']}/start-preparing")

    assert await FloorService.close_idle_table_sessions(db_session, now=later) == []
    for visit in (busy, waiting, cooking):
        assert (await status_of(db_session, visit)).status == TableSessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_safety_timeout_can_be_turned_off(client, db_session, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T9")
    monkeypatch.setattr(settings, "TABLE_SESSION_IDLE_HOURS", 0)
    assert await FloorService.close_idle_table_sessions(db_session, now=datetime.now(timezone.utc) + LATER) == []


@pytest.mark.asyncio
async def test_logout_removes_the_account_from_the_table_visit(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T10")
    await sign_in(client, USER_EMAIL, USER_PASSWORD)
    context = (await client.get("/api/v1/session/context")).json()
    assert context["is_authenticated"] is True and context["user_email"] == USER_EMAIL

    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    context = (await client.get("/api/v1/session/context")).json()
    assert context["is_authenticated"] is False and context["user_email"] is None
    # The visit itself goes on: the guest can still order as a guest.
    assert (await client.get("/api/v1/draft")).status_code == 200


@pytest.mark.asyncio
async def test_guest_requests_do_not_write_to_the_database_every_time(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T11")
    visit = await visit_of(client)
    guest_id, seen = (await db_session.execute(
        select(CustomerSessionModel.id, CustomerSessionModel.last_seen_at)
        .where(CustomerSessionModel.table_session_id == visit))).one()
    for _ in range(3):
        assert (await client.get("/api/v1/orders")).status_code == 200
    db_session.expire_all()
    assert (await db_session.execute(select(CustomerSessionModel.last_seen_at)
                                     .where(CustomerSessionModel.id == guest_id))).scalar_one() == seen

    # A minute later the next request records the guest as still here.
    await db_session.execute(update(CustomerSessionModel).where(CustomerSessionModel.id == guest_id)
                             .values(last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=2)))
    await db_session.commit()
    assert (await client.get("/api/v1/orders")).status_code == 200
    db_session.expire_all()
    refreshed = (await db_session.execute(select(CustomerSessionModel.last_seen_at)
                                          .where(CustomerSessionModel.id == guest_id))).scalar_one()
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=timezone.utc)
    assert datetime.now(timezone.utc) - refreshed < timedelta(seconds=30)
