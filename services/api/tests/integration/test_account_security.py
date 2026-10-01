"""No published password ever opens a production admin panel."""
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from jubran import manage
from jubran.application.account_security import (
    AdminSetupError, secure_accounts_on_startup, set_admin_account,
)
from jubran.domain.enums import UserRole
from jubran.infrastructure.db.models import AuthSessionModel, UserModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import ADMIN_EMAIL, ADMIN_PASSWORD, USER_EMAIL, USER_PASSWORD, sign_in

OWNER_EMAIL = "owner@jubran-restaurant.jo"
OWNER_PASSWORD = "olive-oil-and-zaatar-2026"


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ADMIN_EMAIL", None)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", None)

    def configure_admin(email, password):
        monkeypatch.setattr(settings, "ADMIN_EMAIL", email)
        monkeypatch.setattr(settings, "ADMIN_PASSWORD", SecretStr(password))
    return configure_admin


async def user(db_session, email):
    return (await db_session.execute(select(UserModel).where(UserModel.email == email))).scalar_one_or_none()


async def login_status(client, email, password):
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return response.status_code


@pytest.mark.asyncio
async def test_production_database_never_gets_demo_accounts(db_session, production):
    await seed_database(db_session)
    await db_session.commit()
    assert (await db_session.execute(select(UserModel))).scalars().all() == []


@pytest.mark.asyncio
async def test_production_refuses_to_run_without_an_administrator(db_session, production):
    await seed_database(db_session)
    with pytest.raises(AdminSetupError) as error:
        await secure_accounts_on_startup(db_session)
    assert "ADMIN_EMAIL" in str(error.value) and "create-admin" in str(error.value)


@pytest.mark.asyncio
async def test_old_database_demo_logins_are_disabled_in_production(client, db_session, new_browser, production):
    # A database created before this fix still has the published demo accounts.
    settings.ENVIRONMENT = "test"
    await seed_database(db_session)
    await db_session.commit()
    old_admin_browser = new_browser()
    await sign_in(old_admin_browser)
    settings.ENVIRONMENT = "production"

    production(OWNER_EMAIL, OWNER_PASSWORD)
    await secure_accounts_on_startup(db_session)

    assert (await user(db_session, ADMIN_EMAIL)).is_active is False
    assert (await user(db_session, USER_EMAIL)).is_active is False
    # Every session of the demo admin was revoked, so an open panel is signed out.
    assert (await old_admin_browser.get("/api/v1/admin/tables")).status_code == 401
    assert await login_status(client, ADMIN_EMAIL, ADMIN_PASSWORD) == 401
    assert await login_status(client, USER_EMAIL, USER_PASSWORD) == 401

    # The configured owner is the administrator now.
    owner = await user(db_session, OWNER_EMAIL)
    assert owner.role == UserRole.ADMIN and owner.is_active
    await sign_in(client, OWNER_EMAIL, OWNER_PASSWORD)
    assert (await client.get("/api/v1/admin/tables")).status_code == 200


@pytest.mark.asyncio
async def test_demo_admin_email_can_be_kept_with_a_new_private_password(client, db_session, production):
    settings.ENVIRONMENT = "test"
    await seed_database(db_session)
    await db_session.commit()
    settings.ENVIRONMENT = "production"

    production(ADMIN_EMAIL, OWNER_PASSWORD)
    await secure_accounts_on_startup(db_session)

    assert (await user(db_session, ADMIN_EMAIL)).is_active is True
    assert await login_status(client, ADMIN_EMAIL, ADMIN_PASSWORD) == 401
    assert await login_status(client, ADMIN_EMAIL, OWNER_PASSWORD) == 200


@pytest.mark.asyncio
async def test_environment_password_never_overwrites_an_existing_admin(client, db_session, production):
    assert await set_admin_account(db_session, OWNER_EMAIL, OWNER_PASSWORD, replace_password=False) == "created"
    await db_session.commit()

    production(OWNER_EMAIL, "a-different-password-from-env")
    await secure_accounts_on_startup(db_session)

    assert await login_status(client, OWNER_EMAIL, OWNER_PASSWORD) == 200
    assert await login_status(client, OWNER_EMAIL, "a-different-password-from-env") == 401


@pytest.mark.asyncio
async def test_reset_command_replaces_password_and_signs_the_admin_out(client, db_session):
    await set_admin_account(db_session, OWNER_EMAIL, OWNER_PASSWORD, replace_password=False)
    await db_session.commit()
    await sign_in(client, OWNER_EMAIL, OWNER_PASSWORD)

    assert await set_admin_account(db_session, OWNER_EMAIL, "brand-new-owner-passphrase", replace_password=True) == "updated"
    await db_session.commit()

    sessions = (await db_session.execute(select(AuthSessionModel))).scalars().all()
    assert sessions and all(session.revoked_at is not None for session in sessions)
    assert (await client.get("/api/v1/admin/tables")).status_code == 401
    assert await login_status(client, OWNER_EMAIL, "brand-new-owner-passphrase") == 200


def test_reset_command_checks_the_typed_password(monkeypatch, capsys):
    created = []

    async def fake_create_admin(email, password):
        created.append((email, password))
        return "created"

    monkeypatch.setattr(manage, "create_admin", fake_create_admin)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    typed = iter(["admin12345", "admin12345"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda prompt: next(typed))
    assert manage.main(["create-admin", "--email", OWNER_EMAIL]) == 1
    assert "published demo password" in capsys.readouterr().err

    typed = iter([OWNER_PASSWORD, "something-else-entirely"])
    assert manage.main(["create-admin", "--email", OWNER_EMAIL]) == 1

    typed = iter([OWNER_PASSWORD, OWNER_PASSWORD])
    assert manage.main(["create-admin", "--email", " Owner@Jubran-Restaurant.jo "]) == 0
    assert created == [(OWNER_EMAIL, OWNER_PASSWORD)]
