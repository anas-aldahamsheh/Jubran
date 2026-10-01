"""Integration tests for QR table access, sessions, and auth."""
import pytest
from sqlalchemy import select
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.db.models import TableQrTokenModel
from jubran.settings import settings
from helpers import USER_EMAIL, USER_PASSWORD, issue_table_qr, sign_in


@pytest.mark.asyncio
async def test_qr_table_access_and_session_lifecycle(client, db_session):
    await seed_database(db_session)

    # 1. Test invalid QR token rejection
    bad_res = await client.post("/api/v1/table-sessions/start", json={"qr_token": "non-existent-token"})
    assert bad_res.status_code == 400
    assert bad_res.json()["detail"]["error"]["code"] == "INVALID_QR_TOKEN"

    # 2. The old predictable seed code no longer opens a table
    legacy_res = await client.post("/api/v1/table-sessions/start", json={"qr_token": "qr-t1-jubran"})
    assert legacy_res.status_code == 400

    # 3. Test valid QR token entry (T1)
    res = await client.post("/api/v1/table-sessions/start", json={"qr_token": await issue_table_qr(db_session, "T1")})
    assert res.status_code == 200
    data = res.json()
    assert data["table_number"] == "T1"
    assert "العبدلي" in data["branch_name_ar"]
    assert "جبران" in data["restaurant_name_ar"]
    # The visit token is only in the HttpOnly cookie, never in the body.
    assert "customer_session_token" not in data
    assert client.cookies.get(settings.SESSION_COOKIE_NAME)

    # 3. Test session context retrieval with the customer session cookie
    ctx_res = await client.get("/api/v1/session/context")
    assert ctx_res.status_code == 200
    ctx_data = ctx_res.json()
    assert ctx_data["table_number"] == "T1"
    assert ctx_data["is_authenticated"] is False

    # 4. Test optional customer login during active table session
    login_res = await sign_in(client, USER_EMAIL, USER_PASSWORD)
    assert login_res.json()["user"]["role"] == "USER"
    assert "token" not in login_res.json()

    # 5. Check session context now reflects authenticated user while preserving table T1
    ctx_res2 = await client.get("/api/v1/session/context")
    assert ctx_res2.status_code == 200
    assert ctx_res2.json()["table_number"] == "T1"
    assert ctx_res2.json()["is_authenticated"] is True
    assert ctx_res2.json()["user_email"] == "customer@jubran.jo"


@pytest.mark.asyncio
async def test_admin_auth_and_qr_rotation(client, db_session, new_browser):
    await seed_database(db_session)
    admin, member, guest = client, new_browser(), new_browser()

    # 1. Unauthenticated request to admin endpoint is rejected
    unauth_res = await guest.get("/api/v1/admin/tables")
    assert unauth_res.status_code == 401

    # 2. Non-admin user request to admin endpoint is rejected (403)
    await sign_in(member, USER_EMAIL, USER_PASSWORD)
    forbidden_res = await member.get("/api/v1/admin/tables")
    assert forbidden_res.status_code == 403

    # 3. Admin login succeeds
    admin_login = await sign_in(admin)
    assert admin_login.json()["user"]["role"] == "ADMIN"

    # 4. Admin accesses tables list
    tables_res = await admin.get("/api/v1/admin/tables")
    assert tables_res.status_code == 200
    tables = tables_res.json()
    assert len(tables) == 12
    t2 = next(t for t in tables if t["table_number"] == "T2")
    assert t2["has_active_qr"] is False and t2["qr_token"] is None

    # 5. Non-admins cannot create QR codes
    assert (await guest.post(f"/api/v1/admin/tables/{t2['id']}/qr")).status_code == 401
    assert (await member.post(f"/api/v1/admin/tables/{t2['id']}/qr")).status_code == 403

    # 6. Admin creates a QR for T2: random, opaque and recoverable for printing
    created = await admin.post(f"/api/v1/admin/tables/{t2['id']}/qr")
    assert created.status_code == 200
    first_token = created.json()["qr_token"]
    assert len(first_token) >= 32
    # Not derived from the table (the old codes looked like "qr-t2-jubran").
    assert not first_token.lower().startswith("qr-") and "jubran" not in first_token.lower()
    listed = next(t for t in (await admin.get("/api/v1/admin/tables")).json() if t["id"] == t2["id"])
    assert listed["has_active_qr"] is True and listed["qr_token"] == first_token

    first_scan = await guest.post("/api/v1/table-sessions/start", json={"qr_token": first_token})
    assert first_scan.status_code == 200
    assert first_scan.json()["table_number"] == "T2"

    # 7. Replacing the QR invalidates the printed copy of the previous one
    replaced = await admin.post(f"/api/v1/admin/tables/{t2['id']}/qr")
    assert replaced.status_code == 200
    new_token = replaced.json()["qr_token"]
    assert new_token != first_token
    old_scan = await guest.post("/api/v1/table-sessions/start", json={"qr_token": first_token})
    assert old_scan.status_code == 400
    assert old_scan.json()["detail"]["error"]["code"] == "INVALID_QR_TOKEN"
    new_scan = await guest.post("/api/v1/table-sessions/start", json={"qr_token": new_token})
    assert new_scan.status_code == 200
    assert new_scan.json()["table_number"] == "T2"
    active = (await db_session.execute(select(TableQrTokenModel).where(
        TableQrTokenModel.physical_table_id == t2["id"], TableQrTokenModel.is_active == True
    ))).scalars().all()
    assert len(active) == 1
    assert active[0].token_ciphertext and new_token not in active[0].token_ciphertext

    # 8. Disabling the QR stops it without issuing a new one
    disabled = await admin.delete(f"/api/v1/admin/tables/{t2['id']}/qr")
    assert disabled.status_code == 200
    assert disabled.json()["has_active_qr"] is False and disabled.json()["qr_token"] is None
    assert (await guest.post("/api/v1/table-sessions/start", json={"qr_token": new_token})).status_code == 400

    # 9. Unknown tables are reported, and the floor snapshot never exposes QR codes
    assert (await admin.post("/api/v1/admin/tables/missing/qr")).status_code == 404
    floor = (await admin.get("/api/v1/admin/floor")).json()
    assert all("qr_token" not in table for table in floor["tables"])
