"""Administrator accounts: first-run bootstrap and production safety at startup.

Rules
- Production never keeps an account that still signs in with a password
  published in this repository (the demo accounts): it is disabled and its
  sessions are revoked.
- ``ADMIN_EMAIL`` + ``ADMIN_PASSWORD`` create the first administrator. An
  existing, active administrator with its own password is never overwritten.
- Production refuses to start while no active administrator exists.
"""
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.domain.enums import UserRole
from jubran.infrastructure.auth.passwords import hash_password, verify_password
from jubran.infrastructure.db.models import AuthSessionModel, UserModel
from jubran.infrastructure.db.seed import DEMO_ACCOUNTS
from jubran.settings import DEMO_PASSWORDS, settings

AdminSetupResult = Literal["created", "updated", "unchanged"]


class AdminSetupError(RuntimeError):
    """The server cannot run safely until an administrator is configured."""


async def revoke_user_sessions(db: AsyncSession, user_id: str) -> list[str]:
    """Revoke every live login of a user; returns their token hashes."""
    now = datetime.now(timezone.utc)
    sessions = (await db.execute(
        select(AuthSessionModel).where(AuthSessionModel.user_id == user_id, AuthSessionModel.revoked_at.is_(None))
    )).scalars().all()
    for session in sessions:
        session.revoked_at = now
    return [session.token_hash for session in sessions]


def _uses_demo_password(user: UserModel) -> bool:
    return any(verify_password(user.password_hash, password) for password in DEMO_PASSWORDS)


async def retire_published_demo_accounts(db: AsyncSession) -> list[str]:
    """Disable demo accounts that still use their published passwords."""
    retired = []
    for email, _password, _role in DEMO_ACCOUNTS:
        user = (await db.execute(select(UserModel).where(UserModel.email == email))).scalar_one_or_none()
        if user and user.is_active and _uses_demo_password(user):
            user.is_active = False
            await revoke_user_sessions(db, user.id)
            retired.append(email)
    return retired


async def set_admin_account(db: AsyncSession, email: str, password: str, *, replace_password: bool) -> AdminSetupResult:
    """Create ``email`` as an active administrator, or repair it.

    With ``replace_password`` False an active administrator that already has its
    own (non-demo) password is left untouched; anything else (disabled, not an
    administrator, or a published password) gets ``password`` and admin rights.
    """
    email = email.strip().lower()
    user = (await db.execute(select(UserModel).where(UserModel.email == email))).scalar_one_or_none()
    if user is None:
        db.add(UserModel(email=email, password_hash=hash_password(password), role=UserRole.ADMIN, is_active=True))
        return "created"

    healthy_admin = user.is_active and user.role == UserRole.ADMIN and not _uses_demo_password(user)
    if healthy_admin and not replace_password:
        return "unchanged"

    user.password_hash = hash_password(password)
    user.role = UserRole.ADMIN
    user.is_active = True
    await revoke_user_sessions(db, user.id)
    return "updated"


async def has_active_admin(db: AsyncSession) -> bool:
    admin = (await db.execute(
        select(UserModel.id).where(UserModel.role == UserRole.ADMIN, UserModel.is_active == True).limit(1)  # noqa: E712
    )).first()
    return admin is not None


async def secure_accounts_on_startup(db: AsyncSession) -> None:
    if settings.is_production:
        retired = await retire_published_demo_accounts(db)
        for email in retired:
            print(f"[jubran] Disabled demo account {email}: its password is public.")

    if settings.ADMIN_EMAIL and settings.ADMIN_PASSWORD is not None:
        result = await set_admin_account(
            db, settings.ADMIN_EMAIL, settings.ADMIN_PASSWORD.get_secret_value(), replace_password=False
        )
        if result != "unchanged":
            print(f"[jubran] Administrator {settings.ADMIN_EMAIL} {result} from ADMIN_EMAIL/ADMIN_PASSWORD.")

    await db.commit()

    if settings.is_production and not await has_active_admin(db):
        raise AdminSetupError(
            "No active administrator account. Set ADMIN_EMAIL and ADMIN_PASSWORD (at least 12 characters) "
            "in the server environment, or run: python -m jubran.manage create-admin --email you@example.com"
        )
