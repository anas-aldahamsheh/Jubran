"""Every failure reaches the web app in one readable shape: {"detail": {"error": {"code", "message"}}}."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from jubran.domain.enums import ServiceRequestStatus, ServiceRequestType
from jubran.infrastructure.db.models import CustomerSessionModel, ServiceRequestModel
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.db.session import get_db_session
from jubran.main import app
from helpers import sign_in, start_visit


def error_of(response):
    return response.json()["detail"]["error"]


@pytest.mark.asyncio
async def test_invalid_input_gets_a_readable_arabic_message(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    product_id = (await client.get("/api/v1/menu/products")).json()[0]["id"]

    too_many = await client.post("/api/v1/draft/items", json={"product_id": product_id, "quantity": 51})
    assert too_many.status_code == 422
    error = error_of(too_many)
    assert error["code"] == "VALIDATION_ERROR"
    assert "الكمية" in error["message"] and "50" in error["message"]
    assert error["details"]["fields"] == ["الكمية: يجب ألا يزيد عن 50"]


@pytest.mark.asyncio
async def test_business_rule_failures_are_not_server_errors(client, db_session, new_browser):
    await seed_database(db_session)
    await start_visit(client, db_session, "T5")

    # Reviewing / submitting an empty basket is a normal refusal, not a crash.
    empty = await client.post("/api/v1/draft/prepare-confirmation")
    assert empty.status_code == 400 and error_of(empty)["code"] == "EMPTY_DRAFT"
    bogus = await client.post("/api/v1/orders", json={"confirmation_token": "x" * 20, "draft_version": 1})
    assert 400 <= bogus.status_code < 500 and error_of(bogus)["code"]

    # Unknown ids in the admin area come back in the same shape.
    admin = new_browser()
    await sign_in(admin)
    history = await admin.get("/api/v1/admin/customer-sessions/does-not-exist/history")
    assert history.status_code == 404 and error_of(history)["code"] == "CUSTOMER_NOT_FOUND"
    closing = await admin.post("/api/v1/admin/table-sessions/does-not-exist/close")
    assert closing.status_code == 404 and error_of(closing)["code"]

    # Even framework errors (unknown address) keep the shape.
    missing = await client.get("/api/v1/no-such-endpoint")
    assert missing.status_code == 404 and error_of(missing)["code"] == "HTTP_404"


@pytest.mark.asyncio
async def test_duplicate_open_requests_left_by_an_old_race_do_not_crash(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T6")
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    now = datetime.now(timezone.utc)
    for _ in range(2):
        db_session.add(ServiceRequestModel(table_session_id=customer.table_session_id, customer_session_id=customer.id,
                                           type=ServiceRequestType.STAFF, status=ServiceRequestStatus.OPEN,
                                           created_at=now))
    await db_session.commit()

    again = await client.post("/api/v1/service-requests", json={"type": "STAFF"})
    assert again.status_code == 200, again.text
    assert again.json()["is_duplicate"] is True


@pytest.mark.asyncio
async def test_unexpected_errors_are_json_with_a_reference_the_web_app_can_read(client):
    class BrokenSession:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("internal detail: table menu_categories on db.internal")

    async def broken_db():
        yield BrokenSession()

    previous = app.dependency_overrides[get_db_session]
    app.dependency_overrides[get_db_session] = broken_db
    try:
        response = await client.get("/api/v1/menu/categories", headers={"Origin": "http://localhost:3001"})
    finally:
        app.dependency_overrides[get_db_session] = previous

    assert response.status_code == 500
    error = error_of(response)
    assert error["code"] == "INTERNAL_ERROR"
    assert error["details"]["request_id"] == response.headers["x-request-id"]
    assert "db.internal" not in response.text and "RuntimeError" not in response.text
    # Readable by the web app (CORS) and still hardened.
    assert response.headers["access-control-allow-origin"] == "http://localhost:3001"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_request_ids(client):
    given = await client.get("/health", headers={"X-Request-ID": "trace-1234abcd"})
    assert given.headers["x-request-id"] == "trace-1234abcd"
    # Anything odd is replaced, so logs can't be polluted through this header.
    odd = await client.get("/health", headers={"X-Request-ID": "bad id\\nwith spaces"})
    assert odd.headers["x-request-id"] != "bad id\\nwith spaces" and len(odd.headers["x-request-id"]) == 32
