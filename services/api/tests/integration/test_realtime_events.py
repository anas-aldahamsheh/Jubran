"""Every committed change reaches the live screens: staff get details, guests a refresh signal."""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from jubran.infrastructure.db.models import OutboxEventModel
from jubran.infrastructure.db.seed import seed_database
from jubran.interfaces.websocket.relay import EventRelay
from helpers import sign_in, start_visit


class FakeSockets:
    def __init__(self):
        self.listening = True
        self.admin, self.table, self.closed_logins = [], [], []

    def has_listeners(self):
        return self.listening

    async def broadcast_to_admin(self, event_type, payload):
        self.admin.append((event_type, payload))

    async def broadcast_to_table(self, table_session_id, event_type, payload):
        self.table.append((table_session_id, event_type, payload))

    async def close_admin_session(self, key):
        self.closed_logins.append(key)


def relay_for(db_session, sockets):
    @contextlib.asynccontextmanager
    async def same_session():
        yield db_session
    return EventRelay(same_session, sockets)


async def after_last_event(db_session):
    """Wait until the clock is past the newest event (Windows clocks tick only every ~16 ms)."""
    newest = (await db_session.execute(select(func.max(OutboxEventModel.created_at)))).scalar_one()
    newest = newest if newest.tzinfo else newest.replace(tzinfo=timezone.utc)
    while datetime.now(timezone.utc) <= newest:
        await asyncio.sleep(0.005)


async def order_tea(guest):
    products = (await guest.get("/api/v1/menu/products")).json()
    await guest.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 1})
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    order = await guest.post("/api/v1/orders", json={
        "confirmation_token": prepared["confirmation_token"], "draft_version": prepared["draft_version"]})
    return order.json()


@pytest.mark.asyncio
async def test_orders_services_and_closing_are_pushed_live(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    await start_visit(guest, db_session, "T2")
    visit = (await guest.get("/api/v1/session/context")).json()["table_session_id"]
    await sign_in(admin)
    sockets = FakeSockets()
    relay = relay_for(db_session, sockets)
    await relay.poll_once()  # starts listening from now

    order = await order_tea(guest)
    assert await relay.poll_once() == 1
    assert sockets.admin[-1][0] == "order.created" and sockets.admin[-1][1]["order_number"] == order["order_number"]
    assert sockets.table[-1] == (visit, "orders.changed", {})  # guests get no details, only "refresh"
    assert await relay.poll_once() == 0  # never delivered twice

    await admin.post(f"/api/v1/admin/orders/{order['order_id']}/start-preparing")
    await guest.post("/api/v1/service-requests", json={"type": "TISSUES"})
    await guest.post("/api/v1/complaints", json={"message": "الأكل تأخر كثير"})
    assert await relay.poll_once() == 3
    assert [event for event, _ in sockets.admin[-3:]] == ["order.status_changed", "service_request.created", "complaint.created"]
    assert [signal for _, signal, _ in sockets.table[-3:]] == ["orders.changed", "service_requests.changed", "service_requests.changed"]

    await admin.post(f"/api/v1/admin/orders/{order['order_id']}/mark-ready")
    await admin.post(f"/api/v1/admin/orders/{order['order_id']}/mark-served")
    closed = await admin.post(f"/api/v1/admin/table-sessions/{visit}/close")
    assert closed.status_code == 200, closed.text
    await relay.poll_once()
    assert (visit, "visit.closed", {}) in sockets.table
    assert "table_session.closed" in [event for event, _ in sockets.admin]


@pytest.mark.asyncio
async def test_nothing_is_replayed_to_screens_that_connect_later(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    sockets = FakeSockets()
    sockets.listening = False
    relay = relay_for(db_session, sockets)
    await order_tea(client)
    assert await relay.poll_once() == 0  # nobody connected: nothing read at all
    await after_last_event(db_session)

    sockets.listening = True  # a page opens later (and loads a fresh snapshot itself)
    assert await relay.poll_once() == 0
    await client.post("/api/v1/service-requests", json={"type": "BILL"})
    assert await relay.poll_once() == 1


@pytest.mark.asyncio
async def test_logout_closes_live_feeds_on_every_worker(client, db_session):
    await seed_database(db_session)
    sockets = FakeSockets()
    relay = relay_for(db_session, sockets)
    await relay.poll_once()
    await sign_in(client)
    assert (await client.post("/api/v1/auth/logout")).status_code in (200, 204)
    await relay.poll_once()
    assert len(sockets.closed_logins) == 1 and len(sockets.closed_logins[0]) == 64
    assert sockets.admin == []  # the login key itself is never broadcast


@pytest.mark.asyncio
async def test_old_events_are_purged(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    await client.post("/api/v1/service-requests", json={"type": "STAFF"})
    await db_session.execute(update(OutboxEventModel).values(created_at=datetime.now(timezone.utc) - timedelta(days=1)))
    await db_session.commit()
    await relay_for(db_session, FakeSockets()).purge_old()
    assert (await db_session.execute(select(OutboxEventModel))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_changed_conversation_tells_only_the_guests_page(client, db_session):
    from jubran.application.ai.conversation_store import ConversationStore
    await seed_database(db_session)
    await start_visit(client, db_session, "T5")
    context = (await client.get("/api/v1/session/context")).json()
    sockets = FakeSockets()
    relay = relay_for(db_session, sockets)
    await relay.poll_once()

    state = await ConversationStore.load(db_session, context["customer_session_id"])
    state.append("user", "مرحبا")
    await ConversationStore.save(db_session, state)  # e.g. after a voice conversation
    assert await relay.poll_once() == 1
    assert sockets.table[-1] == (context["table_session_id"], "conversation.changed", {})
    assert sockets.admin == []  # the floor screen is not refreshed for chat messages
