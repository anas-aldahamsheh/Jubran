"""Live-update sockets accept only authorised browsers.

Sockets are driven at the ASGI level so they share the test database session
and event loop with the HTTP client.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from jubran.infrastructure.db.models import AuthSessionModel
from jubran.infrastructure.db.seed import seed_database
from jubran.interfaces.websocket.gateway import ws_manager
from jubran.main import app
from jubran.settings import settings
from helpers import ADMIN_EMAIL, ADMIN_PASSWORD, USER_EMAIL, USER_PASSWORD, sign_in, start_visit

SITE = "http://localhost:3001"


class SocketProbe:
    """Minimal ASGI WebSocket client."""

    def __init__(self, path: str, headers: dict[str, str]):
        self.path = path
        self.headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.changed = asyncio.Event()
        self.task = None

    async def _receive(self):
        return await self.incoming.get()

    async def _send(self, message):
        self.sent.append(message)
        self.changed.set()

    async def wait_for(self, message_type: str, timeout: float = 5.0) -> dict:
        async def _wait():
            while True:
                for message in self.sent:
                    if message["type"] == message_type:
                        return message
                self.changed.clear()
                await self.changed.wait()
        return await asyncio.wait_for(_wait(), timeout)

    async def open(self) -> str:
        scope = {
            "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "http_version": "1.1",
            "path": self.path, "raw_path": self.path.encode(), "query_string": b"", "root_path": "",
            "headers": self.headers, "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
            "subprotocols": [],
        }
        await self.incoming.put({"type": "websocket.connect"})
        self.task = asyncio.create_task(app(scope, self._receive, self._send))

        async def _first():
            while not self.sent:
                self.changed.clear()
                await self.changed.wait()
            return self.sent[0]["type"]
        return await asyncio.wait_for(_first(), 5)

    async def close(self):
        await self.incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self.task, 5)


def admin_cookie(token: str) -> str:
    return f"{settings.ADMIN_SESSION_COOKIE_NAME}={token}"


async def login_token(browser, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD) -> str:
    """Sign in with this browser and return the login value from its HttpOnly cookie."""
    await sign_in(browser, email, password)
    name = settings.ADMIN_SESSION_COOKIE_NAME if email == ADMIN_EMAIL else settings.USER_SESSION_COOKIE_NAME
    return browser.cookies.get(name)


@pytest.mark.asyncio
async def test_admin_feed_requires_admin_login(client, db_session, new_browser):
    await seed_database(db_session)
    admin_token = await login_token(client)
    user_token = await login_token(new_browser(), USER_EMAIL, USER_PASSWORD)

    # No login at all.
    anonymous = SocketProbe("/ws/admin", {"origin": SITE})
    assert await anonymous.open() == "websocket.close"
    await anonymous.task

    # A signed-in customer account is not an administrator, in either cookie.
    customer = SocketProbe("/ws/admin", {"origin": SITE, "cookie": f"{settings.USER_SESSION_COOKIE_NAME}={user_token}"})
    assert await customer.open() == "websocket.close"
    customer_as_admin = SocketProbe("/ws/admin", {"origin": SITE, "cookie": admin_cookie(user_token)})
    assert await customer_as_admin.open() == "websocket.close"

    # Login values are accepted only from the HttpOnly cookie, never from headers.
    header_only = SocketProbe("/ws/admin", {"origin": SITE, "authorization": f"Bearer {admin_token}"})
    assert await header_only.open() == "websocket.close"

    # A made-up session value.
    forged = SocketProbe("/ws/admin", {"origin": SITE, "cookie": admin_cookie("not-a-real-session")})
    assert await forged.open() == "websocket.close"

    # Another website cannot reuse the admin's browser session.
    foreign = SocketProbe("/ws/admin", {"origin": "https://evil.example", "cookie": admin_cookie(admin_token)})
    assert await foreign.open() == "websocket.close"

    # The admin, signed in with email and password, receives live events.
    admin = SocketProbe("/ws/admin", {"origin": SITE, "cookie": admin_cookie(admin_token)})
    assert await admin.open() == "websocket.accept"
    await ws_manager.broadcast_to_admin("complaint.created", {"message": "الأكل بارد"})
    event = await admin.wait_for("websocket.send")
    assert json.loads(event["text"])["payload"]["message"] == "الأكل بارد"
    await admin.close()
    assert ws_manager.admin_connections == {}


@pytest.mark.asyncio
async def test_admin_feed_allows_lan_testing_address(client, db_session):
    await seed_database(db_session)
    admin_token = await login_token(client)
    lan = SocketProbe("/ws/admin", {"origin": "http://192.168.1.20:3001", "cookie": admin_cookie(admin_token)})
    assert await lan.open() == "websocket.accept"
    await lan.close()


@pytest.mark.asyncio
async def test_logout_and_expiry_close_the_admin_feed(client, db_session, new_browser):
    await seed_database(db_session)
    first_device, second_device = client, new_browser()
    first_token = await login_token(first_device)
    second_token = await login_token(second_device)

    first = SocketProbe("/ws/admin", {"origin": SITE, "cookie": admin_cookie(first_token)})
    second = SocketProbe("/ws/admin", {"origin": SITE, "cookie": admin_cookie(second_token)})
    assert await first.open() == "websocket.accept"
    assert await second.open() == "websocket.accept"

    # Logging out closes only the socket opened by that login.
    logout = await first_device.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    closed = await first.wait_for("websocket.close")
    assert closed["code"] == 4401
    await first.close()
    assert len(ws_manager.admin_connections) == 1

    # A login session that runs out stops receiving events.
    session = next(iter(ws_manager.admin_connections.values()))
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await ws_manager.broadcast_to_admin("order.created", {"order_id": "x"})
    await second.wait_for("websocket.close")
    assert not any(message["type"] == "websocket.send" for message in second.sent)
    await second.close()
    assert ws_manager.admin_connections == {}

    # A revoked session can no longer open a new socket either.
    revoked = (await db_session.execute(select(AuthSessionModel).where(AuthSessionModel.revoked_at.is_not(None)))).scalars().all()
    assert revoked
    again = SocketProbe("/ws/admin", {"origin": SITE, "cookie": admin_cookie(first_token)})
    assert await again.open() == "websocket.close"


@pytest.mark.asyncio
async def test_customer_feed_is_limited_to_own_table_visit(client, db_session, new_browser):
    await seed_database(db_session)
    t4_phone, t5_phone = client, new_browser()
    await start_visit(t4_phone, db_session, "T4")
    await start_visit(t5_phone, db_session, "T5")
    t4_visit = (await t4_phone.get("/api/v1/session/context")).json()["table_session_id"]
    t5_visit = (await t5_phone.get("/api/v1/session/context")).json()["table_session_id"]
    customer_cookie = f"{settings.SESSION_COOKIE_NAME}={t4_phone.cookies.get(settings.SESSION_COOKIE_NAME)}"

    no_session = SocketProbe(f"/ws/customer/{t4_visit}", {"origin": SITE})
    assert await no_session.open() == "websocket.close"

    other_table = SocketProbe(f"/ws/customer/{t5_visit}", {"origin": SITE, "cookie": customer_cookie})
    assert await other_table.open() == "websocket.close"

    own_table = SocketProbe(f"/ws/customer/{t4_visit}", {"origin": SITE, "cookie": customer_cookie})
    assert await own_table.open() == "websocket.accept"
    await ws_manager.broadcast_to_table(t4_visit, "order.status_changed", {"status": "PREPARING"})
    event = await own_table.wait_for("websocket.send")
    assert json.loads(event["text"])["type"] == "order.status_changed"
    await own_table.close()
    assert t4_visit not in ws_manager.customer_connections
