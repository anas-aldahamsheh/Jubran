"""End to end through the public API: one table visit from QR scan to closing (spec lifecycle A–J)."""
import contextlib

import pytest

from jubran.infrastructure.db.seed import seed_database
from jubran.interfaces.websocket.relay import EventRelay
from helpers import sign_in, start_visit


class Screens:
    """Stands in for the WebSocket gateway: records what staff and the table would see."""

    def __init__(self):
        self.admin, self.table = [], []

    def has_listeners(self):
        return True

    async def broadcast_to_admin(self, event_type, payload):
        self.admin.append(event_type)

    async def broadcast_to_table(self, table_session_id, event_type, payload):
        self.table.append(event_type)

    async def close_admin_session(self, key):
        pass


@pytest.mark.asyncio
async def test_a_whole_visit(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin = client, new_browser()
    screens = Screens()

    @contextlib.asynccontextmanager
    async def same_session():
        yield db_session
    relay = EventRelay(same_session, screens)
    await relay.poll_once()

    # A/B. The table is empty until a guest scans its QR code.
    await sign_in(admin)
    floor = (await admin.get("/api/v1/admin/floor")).json()
    assert next(t for t in floor["tables"] if t["table_number"] == "T7")["base_state"] == "VACANT"
    await start_visit(guest, db_session, "T7")
    floor = (await admin.get("/api/v1/admin/floor")).json()
    assert next(t for t in floor["tables"] if t["table_number"] == "T7")["base_state"] == "OCCUPIED_IDLE"

    # C/D. Menu -> basket -> explicit confirmation -> order.
    products = {p["name_ar"]: p for p in (await guest.get("/api/v1/menu/products")).json()}
    await guest.post("/api/v1/draft/items", json={"product_id": products["فتوش"]["id"], "quantity": 1})
    await guest.post("/api/v1/draft/items", json={"product_id": products["شاي"]["id"], "quantity": 2, "note": "بالنعناع"})
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    order = (await guest.post("/api/v1/orders", json={"confirmation_token": prepared["confirmation_token"],
                                                      "draft_version": prepared["draft_version"]})).json()
    await relay.poll_once()
    assert "order.created" in screens.admin and "orders.changed" in screens.table
    floor = (await admin.get("/api/v1/admin/floor")).json()
    assert next(t for t in floor["tables"] if t["table_number"] == "T7")["base_state"] == "ORDER_PENDING"

    # E/F/G. Kitchen steps; the guest sees exactly three states.
    seen = []
    for step in ("start-preparing", "mark-ready", "mark-served"):
        assert (await admin.post(f"/api/v1/admin/orders/{order['order_id']}/{step}")).status_code == 200
        seen.append((await guest.get("/api/v1/orders")).json()[0]["status"])
    assert seen == ["PREPARING", "READY", "READY"]  # "served" is a staff marker, not a fourth state

    # I. Bill, then an optional rating.
    assert (await guest.post("/api/v1/service-requests", json={"type": "BILL"})).json()["type"] == "BILL"
    assert (await guest.post("/api/v1/feedback", json={"rating": 5, "comment": "ممتاز"})).status_code in (200, 201)
    await relay.poll_once()
    assert "service_request.created" in screens.admin

    # J. Staff close the table: it is free again and the guest's page is told.
    visit = (await guest.get("/api/v1/session/context")).json()["table_session_id"]
    assert (await admin.post(f"/api/v1/admin/table-sessions/{visit}/close")).status_code == 200
    await relay.poll_once()
    assert "visit.closed" in screens.table
    floor = (await admin.get("/api/v1/admin/floor")).json()
    assert next(t for t in floor["tables"] if t["table_number"] == "T7")["base_state"] == "VACANT"
    ended = await guest.get("/api/v1/orders")
    assert ended.status_code == 401 and ended.json()["detail"]["error"]["code"] == "SESSION_EXPIRED"
