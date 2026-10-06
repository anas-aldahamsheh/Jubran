"""Public demo (PUBLIC_DEMO): "Try as a guest", the pretend kitchen and the assistant's daily total."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from jubran.application import rate_limiter
from jubran.application.public_demo import run_demo_kitchen
from jubran.application.rate_limiter import LIMITS
from jubran.domain.enums import OrderStatus, TableSessionStatus
from jubran.infrastructure.db.models import CustomerSessionModel, OrderModel, PhysicalTableModel, TableSessionModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import start_visit


@pytest.fixture
def public_demo(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_DEMO", True)


async def try_as_guest(browser):
    return await browser.post("/api/v1/demo/visit")


async def order_tea(guest):
    products = (await guest.get("/api/v1/menu/products")).json()
    await guest.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 1})
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    order = await guest.post("/api/v1/orders", json={
        "confirmation_token": prepared["confirmation_token"], "draft_version": prepared["draft_version"]})
    assert order.status_code in (200, 201), order.text
    return order.json()["order_id"]


@pytest.mark.asyncio
async def test_off_unless_the_copy_is_a_public_demo(client, db_session):
    await seed_database(db_session)
    assert (await try_as_guest(client)).status_code == 404


@pytest.mark.asyncio
async def test_each_visitor_gets_a_table_of_their_own(client, db_session, new_browser, public_demo):
    await seed_database(db_session)
    first = await try_as_guest(client)
    assert first.status_code == 200, first.text
    seat = first.json()
    assert seat["table_number"] and seat["restaurant_name_ar"]
    context = (await client.get("/api/v1/session/context")).json()
    assert context["table_id"] == seat["table_id"]

    # The same visitor again (a refresh, a second tab): the same visit, no second table.
    again = (await try_as_guest(client)).json()
    assert (again["table_id"], again["customer_id"]) == (seat["table_id"], seat["customer_id"])

    # Someone else sits elsewhere: guests at one table would share its orders.
    other = new_browser()
    assert (await try_as_guest(other)).json()["table_id"] != seat["table_id"]


@pytest.mark.asyncio
async def test_a_table_left_idle_goes_to_the_next_visitor(client, db_session, new_browser, public_demo, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMITS_ENABLED", False)  # every browser here shares one address
    await seed_database(db_session)
    tables = len((await db_session.execute(select(PhysicalTableModel.id))).scalars().all())
    seated = [client] + [new_browser() for _ in range(tables - 1)]
    numbers = {(await try_as_guest(guest)).json()["table_number"] for guest in seated}
    assert len(numbers) == tables

    # Every table is in use: the next visitor waits rather than taking someone's table.
    latecomer = new_browser()
    busy = await try_as_guest(latecomer)
    assert busy.status_code == 503
    assert busy.json()["detail"]["error"]["code"] == "DEMO_TABLES_BUSY"
    assert "message_en" in busy.json()["detail"]["error"]["details"]

    # The first visitor left a while ago: their table goes to the latecomer.
    gone = (await client.get("/api/v1/session/context")).json()
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=11)
    await db_session.execute(update(TableSessionModel).where(TableSessionModel.id == gone["table_session_id"])
                             .values(started_at=long_ago))
    await db_session.execute(update(CustomerSessionModel)
                             .where(CustomerSessionModel.table_session_id == gone["table_session_id"])
                             .values(started_at=long_ago, last_seen_at=long_ago))
    await db_session.commit()
    seat = await try_as_guest(latecomer)
    assert seat.status_code == 200, seat.text
    assert seat.json()["table_id"] == gone["table_id"]

    db_session.expire_all()
    closed = (await db_session.execute(
        select(TableSessionModel).where(TableSessionModel.id == gone["table_session_id"]))).scalar_one()
    assert closed.status == TableSessionStatus.CLOSED and closed.closed_by_admin_id is None
    assert (await client.get("/api/v1/session/context")).status_code == 401


@pytest.mark.asyncio
async def test_the_pretend_kitchen_takes_an_order_to_the_table(client, db_session, public_demo):
    await seed_database(db_session)
    await try_as_guest(client)
    order_id = await order_tea(client)

    async def order():
        db_session.expire_all()
        return (await db_session.execute(select(OrderModel).where(OrderModel.id == order_id))).scalar_one()

    def later(seconds):
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)

    assert await run_demo_kitchen(db_session, now=later(5)) == 0  # not yet
    assert (await order()).status == OrderStatus.PENDING_APPROVAL
    assert await run_demo_kitchen(db_session, now=later(16)) == 1
    assert (await order()).status == OrderStatus.PREPARING
    assert await run_demo_kitchen(db_session, now=later(61)) == 1
    assert (await order()).status == OrderStatus.READY
    assert await run_demo_kitchen(db_session, now=later(31)) == 1
    served = await order()
    assert served.status == OrderStatus.READY and served.served_at is not None
    assert await run_demo_kitchen(db_session, now=later(3600)) == 0  # nothing left to do

    # The guest follows it on their orders page.
    mine = (await client.get("/api/v1/orders")).json()
    assert [(item["order_id"], item["status"], item["is_served"]) for item in mine] == [(order_id, "READY", True)]


@pytest.mark.asyncio
async def test_the_assistant_has_a_daily_total_for_everyone(client, db_session, new_browser, public_demo, monkeypatch):
    monkeypatch.setitem(LIMITS, "assistant_demo_day", rate_limiter.Limit(2, 86400))
    await seed_database(db_session)
    await try_as_guest(client)
    for _ in range(2):
        response = await client.post("/api/v1/assistant/chat", json={"message": "مرحبا"})
        assert response.status_code == 503  # no model configured in tests, but the request got through
    # Another visitor, at another table, shares the same daily total.
    other = new_browser()
    await try_as_guest(other)
    blocked = await other.post("/api/v1/assistant/chat", json={"message": "hello"})
    assert blocked.status_code == 429
    error = blocked.json()["detail"]["error"]
    assert error["code"] == "DEMO_ASSISTANT_DAILY_LIMIT" and error["details"]["message_en"]


@pytest.mark.asyncio
async def test_no_daily_total_outside_the_demo(client, db_session, monkeypatch):
    monkeypatch.setitem(LIMITS, "assistant_demo_day", rate_limiter.Limit(1, 86400))
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    for _ in range(3):
        assert (await client.post("/api/v1/assistant/chat", json={"message": "مرحبا"})).status_code == 503


@pytest.mark.asyncio
async def test_seats_from_one_address_are_limited(client, db_session, new_browser, public_demo, monkeypatch):
    monkeypatch.setitem(LIMITS, "demo_visit_ip", rate_limiter.Limit(2, 600))
    await seed_database(db_session)
    for _ in range(2):
        assert (await try_as_guest(new_browser())).status_code == 200
    blocked = await try_as_guest(new_browser())
    assert blocked.status_code == 429 and blocked.json()["detail"]["error"]["code"] == "RATE_LIMITED"
