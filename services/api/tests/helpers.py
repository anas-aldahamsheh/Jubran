"""Shared test helpers."""
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.qr_service import QrService
from jubran.infrastructure.db.models import PhysicalTableModel
from jubran.settings import settings

# Local demo accounts (seeded only outside production).
ADMIN_EMAIL, ADMIN_PASSWORD = "admin@jubran.jo", "admin12345"
USER_EMAIL, USER_PASSWORD = "customer@jubran.jo", "user12345"

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class BrowserClient(httpx.AsyncClient):
    """Talks to the API exactly like the web app does.

    Credentials exist only as HttpOnly cookies in this client's own cookie jar
    (one jar = one browser). Change requests carry the CSRF token fetched from
    ``/api/v1/auth/csrf``, just like ``apiFetch`` in the frontend.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._csrf: tuple[str, str] | None = None  # (csrf cookie, token)

    async def csrf_token(self) -> str:
        cookie = self.cookies.get(settings.CSRF_COOKIE_NAME)
        if self._csrf is None or cookie is None or self._csrf[0] != cookie:
            response = await self.get("/api/v1/auth/csrf")
            assert response.status_code == 200, response.text
            self._csrf = (self.cookies.get(settings.CSRF_COOKIE_NAME), response.json()["csrf_token"])
        return self._csrf[1]

    async def request(self, method, url, **kwargs):
        if method.upper() in _UNSAFE_METHODS:
            headers = httpx.Headers(kwargs.pop("headers", None) or {})
            if "x-csrf-token" not in headers:
                headers["X-CSRF-Token"] = await self.csrf_token()
            kwargs["headers"] = headers
        return await super().request(method, url, **kwargs)


async def sign_in(browser: httpx.AsyncClient, email: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD,
                  **extra) -> httpx.Response:
    response = await browser.post("/api/v1/auth/login", json={"email": email, "password": password, **extra})
    assert response.status_code == 200, response.text
    return response


async def issue_table_qr(db_session: AsyncSession, table_number: str) -> str:
    """Create a real QR for a seeded table, exactly like the admin does, and return its token."""
    table = (await db_session.execute(
        select(PhysicalTableModel).where(PhysicalTableModel.table_number == table_number)
    )).scalar_one()
    return (await QrService.create_table_qr(db_session, table.id))["qr_token"]


async def start_visit(browser: httpx.AsyncClient, db_session: AsyncSession, table_number: str = "T4") -> httpx.Response:
    """Scan a freshly issued QR for ``table_number`` with this browser."""
    response = await browser.post("/api/v1/table-sessions/start",
                                  json={"qr_token": await issue_table_qr(db_session, table_number)})
    assert response.status_code == 200, response.text
    return response
