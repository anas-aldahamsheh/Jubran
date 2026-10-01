"""Sign-in lives only in HttpOnly cookies, and other websites cannot use them.

``raw`` clients below are plain HTTP clients (no automatic CSRF header), so the
tests can play the part of a forged request.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from jubran.infrastructure.db.seed import seed_database
from jubran.main import app
from jubran.settings import settings
from helpers import ADMIN_EMAIL, ADMIN_PASSWORD, sign_in, start_visit

SITE = "http://localhost:3001"


def raw_client(**cookies) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    for name, value in cookies.items():
        client.cookies.set(name, value)
    return client


def set_cookie_headers(response, name):
    return [value for value in response.headers.get_list("set-cookie") if value.startswith(f"{name}=")]


def error_code(response):
    return response.json()["detail"]["error"]["code"]


@pytest.mark.asyncio
async def test_login_token_is_only_in_an_http_only_cookie(client, db_session):
    await seed_database(db_session)
    response = await sign_in(client)
    assert set(response.json()) == {"user"}  # no token in the body

    cookie = set_cookie_headers(response, settings.ADMIN_SESSION_COOKIE_NAME)[-1].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    # Without "remember me" the login ends when the browser closes.
    assert "max-age" not in cookie and "expires" not in cookie

    remembered = await sign_in(client, remember_me=True)
    assert f"max-age={settings.SESSION_MAX_AGE_SECONDS}" in set_cookie_headers(
        remembered, settings.ADMIN_SESSION_COOKIE_NAME)[-1].lower()


@pytest.mark.asyncio
async def test_table_visit_token_is_only_in_an_http_only_cookie(client, db_session):
    await seed_database(db_session)
    response = await start_visit(client, db_session, "T2")
    assert "customer_session_token" not in response.json()
    assert "httponly" in set_cookie_headers(response, settings.SESSION_COOKIE_NAME)[-1].lower()
    assert (await client.get("/api/v1/session/context")).json()["table_number"] == "T2"


@pytest.mark.asyncio
async def test_tokens_in_headers_are_ignored(client, db_session):
    """A token copied out of the browser (e.g. by an injected script) cannot be replayed from a header."""
    await seed_database(db_session)
    await sign_in(client)
    await start_visit(client, db_session, "T3")
    admin_token = client.cookies.get(settings.ADMIN_SESSION_COOKIE_NAME)
    visit_token = client.cookies.get(settings.SESSION_COOKIE_NAME)

    async with raw_client() as outsider:
        admin = await outsider.get("/api/v1/admin/tables", headers={"Authorization": f"Bearer {admin_token}"})
        assert admin.status_code == 401
        visit = await outsider.get("/api/v1/session/context", headers={"X-Customer-Session": visit_token})
        assert visit.status_code == 401


@pytest.mark.asyncio
async def test_forged_admin_change_is_rejected(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    tables = (await client.get("/api/v1/admin/tables")).json()
    target = f"/api/v1/admin/tables/{tables[0]['id']}/qr"
    admin_cookie = {settings.ADMIN_SESSION_COOKIE_NAME: client.cookies.get(settings.ADMIN_SESSION_COOKIE_NAME)}

    # The browser sends the login cookie automatically, but not the CSRF token.
    async with raw_client(**admin_cookie) as forged:
        missing = await forged.post(target)
        assert missing.status_code == 403 and error_code(missing) == "CSRF_TOKEN_INVALID"
        wrong = await forged.post(target, headers={"X-CSRF-Token": "guessed"})
        assert wrong.status_code == 403

    # A token that belongs to a different browser does not fit this one.
    async with raw_client() as attacker:
        foreign_token = (await attacker.get("/api/v1/auth/csrf")).json()["csrf_token"]
    async with raw_client(**admin_cookie) as forged:
        assert (await forged.post(target, headers={"X-CSRF-Token": foreign_token})).status_code == 403

    # Another website is refused even with a valid token.
    token = await client.csrf_token()
    evil = await client.post(target, headers={"X-CSRF-Token": token, "Origin": "https://evil.example"})
    assert evil.status_code == 403 and error_code(evil) == "CSRF_ORIGIN_REJECTED"
    evil_referer = await client.post(target, headers={"X-CSRF-Token": token, "Referer": "https://evil.example/page"})
    assert evil_referer.status_code == 403

    # The real admin page: own origin + its token.
    real = await client.post(target, headers={"X-CSRF-Token": token, "Origin": SITE})
    assert real.status_code == 200 and real.json()["has_active_qr"] is True


@pytest.mark.asyncio
async def test_forged_customer_order_is_rejected(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T5")
    visit_cookie = {settings.SESSION_COOKIE_NAME: client.cookies.get(settings.SESSION_COOKIE_NAME)}
    async with raw_client(**visit_cookie) as forged:
        response = await forged.post("/api/v1/service-requests", json={"type": "BILL"})
        assert response.status_code == 403
    assert (await client.post("/api/v1/service-requests", json={"type": "BILL"})).status_code == 200


@pytest.mark.asyncio
async def test_rejection_is_readable_by_the_web_app(client, db_session):
    """CORS headers stay on the 403 so the page can read the code and fetch a fresh token."""
    await seed_database(db_session)
    await sign_in(client)
    admin_cookie = {settings.ADMIN_SESSION_COOKIE_NAME: client.cookies.get(settings.ADMIN_SESSION_COOKIE_NAME)}
    async with raw_client(**admin_cookie) as page:
        response = await page.post("/api/v1/auth/logout", headers={"Origin": SITE})
    assert response.status_code == 403
    assert response.headers["access-control-allow-origin"] == SITE
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_first_login_needs_no_token_but_must_come_from_the_site(client, db_session):
    await seed_database(db_session)
    credentials = {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    async with raw_client() as fresh:
        response = await fresh.post("/api/v1/auth/login", json=credentials, headers={"Origin": SITE})
        assert response.status_code == 200
    async with raw_client() as fresh:
        response = await fresh.post("/api/v1/auth/login", json=credentials, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_csrf_endpoint_reuses_the_browser_cookie(client):
    first = await client.get("/api/v1/auth/csrf")
    second = await client.get("/api/v1/auth/csrf")
    assert first.json()["csrf_token"] == second.json()["csrf_token"]
    assert first.headers["cache-control"] == "no-store"
    assert "httponly" in set_cookie_headers(first, settings.CSRF_COOKIE_NAME)[-1].lower()


@pytest.mark.asyncio
async def test_logout_clears_cookies_and_ends_the_session(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    old_token = client.cookies.get(settings.ADMIN_SESSION_COOKIE_NAME)

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    cleared = set_cookie_headers(logout, settings.ADMIN_SESSION_COOKIE_NAME)
    assert cleared and "max-age=0" in cleared[-1].lower()
    assert client.cookies.get(settings.ADMIN_SESSION_COOKIE_NAME) is None
    assert (await client.get("/api/v1/auth/me")).json()["authenticated"] is False

    # Even a copy of the old cookie no longer works.
    async with raw_client(**{settings.ADMIN_SESSION_COOKIE_NAME: old_token}) as copy:
        assert (await copy.get("/api/v1/admin/tables")).status_code == 401


@pytest.mark.asyncio
async def test_signing_in_again_replaces_the_previous_login(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    first_token = client.cookies.get(settings.ADMIN_SESSION_COOKIE_NAME)
    await sign_in(client)
    assert client.cookies.get(settings.ADMIN_SESSION_COOKIE_NAME) != first_token
    async with raw_client(**{settings.ADMIN_SESSION_COOKIE_NAME: first_token}) as copy:
        assert (await copy.get("/api/v1/admin/tables")).status_code == 401
    assert (await client.get("/api/v1/admin/tables")).status_code == 200
