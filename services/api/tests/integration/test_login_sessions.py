"""Logins: no hint about which emails exist, idle logins end, old logins are capped and cleaned up."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from jubran.application import auth_service
from jubran.application.auth_service import MAX_ACTIVE_LOGINS
from jubran.application.maintenance import purge_expired_records
from jubran.infrastructure.db.models import AuthSessionModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import ADMIN_EMAIL, sign_in


@pytest.mark.asyncio
async def test_unknown_email_costs_a_password_check_too(client, db_session, monkeypatch):
    await seed_database(db_session)
    checks = []
    real_verify = auth_service.verify_password
    monkeypatch.setattr(auth_service, "verify_password", lambda hashed, password: checks.append(1) or real_verify(hashed, password))
    unknown = await client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever-123"})
    wrong = await client.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "whatever-123"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()  # same answer
    assert len(checks) == 2               # and the same work for both


@pytest.mark.asyncio
async def test_a_login_left_unused_signs_out(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    assert (await client.get("/api/v1/admin/floor")).status_code == 200
    await db_session.execute(update(AuthSessionModel).values(
        last_seen_at=datetime.now(timezone.utc) - timedelta(hours=9)))
    await db_session.commit()
    assert (await client.get("/api/v1/admin/floor")).status_code == 401
    assert (await client.get("/api/v1/auth/me")).json()["authenticated"] is False


@pytest.mark.asyncio
async def test_old_logins_are_capped_and_cleaned_up(client, db_session, new_browser, monkeypatch):
    await seed_database(db_session)
    monkeypatch.setattr(settings, "RATE_LIMITS_ENABLED", False)  # many sign-ins in a row on purpose
    browsers = [new_browser() for _ in range(MAX_ACTIVE_LOGINS + 2)]
    for browser in browsers:
        await sign_in(browser)
    active = (await db_session.execute(select(func.count()).select_from(AuthSessionModel)
                                       .where(AuthSessionModel.revoked_at.is_(None)))).scalar_one()
    assert active == MAX_ACTIVE_LOGINS
    assert (await browsers[0].get("/api/v1/admin/floor")).status_code == 401   # the oldest was signed out
    assert (await browsers[-1].get("/api/v1/admin/floor")).status_code == 200

    await db_session.execute(update(AuthSessionModel).where(AuthSessionModel.revoked_at.is_not(None))
                             .values(revoked_at=datetime.now(timezone.utc) - timedelta(days=2)))
    await db_session.commit()
    await purge_expired_records(db_session)
    remaining = (await db_session.execute(select(func.count()).select_from(AuthSessionModel))).scalar_one()
    assert remaining == MAX_ACTIVE_LOGINS
