"""Abuse protection: repeated requests get HTTP 429 instead of reaching the logic."""
import pytest
from sqlalchemy import select

from jubran.application import rate_limiter
from jubran.application.qr_service import QrService
from jubran.application.rate_limiter import LIMITS, RateLimitExceeded
from jubran.infrastructure.db.models import RateLimitCounterModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import ADMIN_EMAIL, ADMIN_PASSWORD, start_visit


def assert_rate_limited(response):
    assert response.status_code == 429, response.text
    assert int(response.headers["retry-after"]) >= 1
    assert response.json()["detail"]["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_password_guessing_is_slowed_down(client, db_session):
    await seed_database(db_session)
    for _ in range(LIMITS["login_account"].hits):
        wrong = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "guess-123"})
        assert wrong.status_code == 401
    # Even the right password is refused until the window passes.
    blocked = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert_rate_limited(blocked)
    # Counters are stored in the database, so every server worker sees them.
    assert (await db_session.execute(select(RateLimitCounterModel))).scalars().first() is not None


@pytest.mark.asyncio
async def test_assistant_messages_are_limited_per_guest(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    for _ in range(LIMITS["assistant_customer"].hits):
        response = await client.post("/api/v1/assistant/chat", json={"message": "مرحبا"})
        assert response.status_code == 503  # no model configured in tests, but the request got through
    assert_rate_limited(await client.post("/api/v1/assistant/chat", json={"message": "مرحبا"}))


@pytest.mark.asyncio
async def test_whole_table_cannot_bypass_the_limit_with_new_guests(client, db_session, new_browser, monkeypatch):
    monkeypatch.setitem(LIMITS, "assistant_table", rate_limiter.Limit(3, 300))
    await seed_database(db_session)
    await start_visit(client, db_session, "T5")
    context = (await client.get("/api/v1/session/context")).json()
    for _ in range(3):
        await client.post("/api/v1/assistant/chat", json={"message": "مرحبا"})
    # A second phone joining the same table visit shares the table's allowance.
    qr = (await QrService.create_table_qr(db_session, context["table_id"]))["qr_token"]
    second = new_browser()
    assert (await second.post("/api/v1/table-sessions/start", json={"qr_token": qr})).status_code == 200
    assert (await second.get("/api/v1/session/context")).json()["table_session_id"] == context["table_session_id"]
    assert_rate_limited(await second.post("/api/v1/assistant/chat", json={"message": "مرحبا"}))


@pytest.mark.asyncio
async def test_order_submission_and_service_calls_are_limited(client, db_session, monkeypatch):
    monkeypatch.setitem(LIMITS, "customer_action", rate_limiter.Limit(2, 60))
    monkeypatch.setitem(LIMITS, "order_submit", rate_limiter.Limit(1, 60))
    await seed_database(db_session)
    await start_visit(client, db_session, "T6")
    assert (await client.post("/api/v1/service-requests", json={"type": "TISSUES"})).status_code == 200
    assert (await client.post("/api/v1/service-requests", json={"type": "BILL"})).status_code == 200
    assert_rate_limited(await client.post("/api/v1/service-requests", json={"type": "STAFF"}))
    body = {"confirmation_token": "x" * 20, "draft_version": 1}
    assert (await client.post("/api/v1/orders", json=body)).status_code != 429
    assert_rate_limited(await client.post("/api/v1/orders", json=body))


@pytest.mark.asyncio
async def test_qr_scanning_from_one_address_is_limited(client, db_session, monkeypatch):
    monkeypatch.setitem(LIMITS, "session_start_ip", rate_limiter.Limit(2, 60))
    await seed_database(db_session)
    for _ in range(2):
        assert (await client.post("/api/v1/table-sessions/start", json={"qr_token": "not-a-real-code"})).status_code == 400
    assert_rate_limited(await client.post("/api/v1/table-sessions/start", json={"qr_token": "not-a-real-code"}))


@pytest.mark.asyncio
async def test_sliding_window_does_not_reset_all_at_once(db_session):
    limit = LIMITS["order_submit"]
    start = 1_000_000 * limit.window_seconds
    for i in range(limit.hits):
        await rate_limiter.hit(db_session, "order_submit", "guest", now=start + 1 + i)
    with pytest.raises(RateLimitExceeded):
        await rate_limiter.hit(db_session, "order_submit", "guest", now=start + 10)
    # Just after the window boundary most of the previous window still counts.
    with pytest.raises(RateLimitExceeded):
        await rate_limiter.hit(db_session, "order_submit", "guest", now=start + limit.window_seconds + 1)
    # A full window later the guest may continue.
    await rate_limiter.hit(db_session, "order_submit", "guest", now=start + 2 * limit.window_seconds + 1)


@pytest.mark.asyncio
async def test_limits_can_be_switched_off(db_session, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMITS_ENABLED", False)
    for _ in range(LIMITS["order_submit"].hits * 3):
        await rate_limiter.hit(db_session, "order_submit", "guest")
