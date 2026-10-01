"""Authentication and Account Session Service."""
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jubran.infrastructure.db.models import UserModel, AuthSessionModel, CustomerSessionModel
from jubran.domain.enums import UserRole
from jubran.domain.exceptions import UnauthorizedException
from jubran.infrastructure.auth.passwords import hash_password, verify_password
from jubran.settings import settings
from jubran.infrastructure.auth.tokens import hash_token

# A signed-in browser counts as "in use" again at most once a minute (fewer writes).
LAST_SEEN_PRECISION = timedelta(minutes=1)
# Oldest logins beyond this many per account are signed out.
MAX_ACTIVE_LOGINS = 10

_timing_hash: Optional[str] = None



def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _check_password_of_unknown_account(password: str) -> None:
    """Spend the same time as a real check, so response time does not reveal which emails exist."""
    global _timing_hash
    if _timing_hash is None:
        _timing_hash = hash_password(secrets.token_urlsafe(24))
    verify_password(_timing_hash, password)


class AuthService:
    @staticmethod
    async def login(
        db: AsyncSession,
        email: str,
        password: str,
        customer_session_token: Optional[str] = None
    ) -> Tuple[str, UserModel]:
        """Authenticate user by email and password, create session, attach to guest visit if present."""
        email_clean = email.strip().lower()
        stmt = select(UserModel).where(UserModel.email == email_clean, UserModel.is_active == True)
        user = (await db.execute(stmt)).scalar_one_or_none()

        if not user:
            _check_password_of_unknown_account(password)
            raise UnauthorizedException("البريد الإلكتروني أو كلمة المرور غير صحيحة.")
        if not verify_password(user.password_hash, password):
            raise UnauthorizedException("البريد الإلكتروني أو كلمة المرور غير صحيحة.")

        now = datetime.now(timezone.utc)
        raw_auth_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_auth_token)
        expires_at = now + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS)

        auth_session = AuthSessionModel(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=now,
            last_seen_at=now
        )
        db.add(auth_session)
        await db.flush()
        # Keep only the newest logins of this account; older ones are signed out.
        older = (await db.execute(
            select(AuthSessionModel).where(
                AuthSessionModel.user_id == user.id,
                AuthSessionModel.revoked_at.is_(None),
                AuthSessionModel.expires_at > now,
                AuthSessionModel.id != auth_session.id,
            ).order_by(AuthSessionModel.created_at.desc(), AuthSessionModel.id.desc())
        )).scalars().all()
        for stale in older[MAX_ACTIVE_LOGINS - 1:]:
            stale.revoked_at = now

        # If user is logging in during active customer table session, attach user_id without resetting draft
        if customer_session_token:
            cust_token_hash = hash_token(customer_session_token)
            cust_session = (await db.execute(
                select(CustomerSessionModel).where(CustomerSessionModel.token_hash == cust_token_hash)
            )).scalar_one_or_none()
            if cust_session:
                cust_session.authenticated_user_id = user.id

        await db.commit()
        return raw_auth_token, user

    @staticmethod
    async def logout(db: AsyncSession, auth_token: str) -> None:
        """Revoke server-side auth session idempotently."""
        if not auth_token:
            return
        token_hash = hash_token(auth_token)
        stmt = select(AuthSessionModel).where(
            AuthSessionModel.token_hash == token_hash,
            AuthSessionModel.revoked_at.is_(None)
        )
        session = (await db.execute(stmt)).scalar_one_or_none()
        if session:
            session.revoked_at = datetime.now(timezone.utc)
            await db.commit()

    @staticmethod
    async def detach_account_from_visit(db: AsyncSession, customer_session_token: Optional[str]) -> None:
        """After logout the guest's table visit continues without the account identity."""
        if not customer_session_token:
            return
        cust_session = (await db.execute(
            select(CustomerSessionModel).where(CustomerSessionModel.token_hash == hash_token(customer_session_token))
        )).scalar_one_or_none()
        if cust_session and cust_session.authenticated_user_id:
            cust_session.authenticated_user_id = None
            await db.commit()

    @staticmethod
    async def _live_session(db: AsyncSession, auth_token: Optional[str], admin_only: bool = False
                            ) -> Optional[Tuple[AuthSessionModel, UserModel]]:
        """The login behind this token if it is still valid: not signed out, not past its absolute
        lifetime (SESSION_MAX_AGE_SECONDS) and used within LOGIN_IDLE_TIMEOUT_SECONDS."""
        if not auth_token:
            return None
        now = datetime.now(timezone.utc)
        conditions = [
            AuthSessionModel.token_hash == hash_token(auth_token),
            AuthSessionModel.revoked_at.is_(None),
            AuthSessionModel.expires_at > now,
            UserModel.is_active == True,
        ]
        if admin_only:
            conditions.append(UserModel.role == UserRole.ADMIN)
        row = (await db.execute(
            select(AuthSessionModel, UserModel).join(UserModel, UserModel.id == AuthSessionModel.user_id).where(*conditions)
        )).first()
        if not row:
            return None
        session, user = row
        last_seen = _aware(session.last_seen_at) or _aware(session.created_at)
        if last_seen is not None and now - last_seen > timedelta(seconds=settings.LOGIN_IDLE_TIMEOUT_SECONDS):
            return None  # unused for too long: sign in again
        if last_seen is None or now - last_seen >= LAST_SEEN_PRECISION:
            session.last_seen_at = now
            await db.commit()
        return session, user

    @staticmethod
    async def get_user_by_token(db: AsyncSession, auth_token: Optional[str]) -> Optional[UserModel]:
        """Validate token and return active UserModel or None."""
        live = await AuthService._live_session(db, auth_token)
        return live[1] if live else None

    @staticmethod
    async def get_admin_session(db: AsyncSession, auth_token: Optional[str]) -> Optional[AuthSessionModel]:
        """Return the live login session when the token belongs to an active administrator."""
        live = await AuthService._live_session(db, auth_token, admin_only=True)
        return live[0] if live else None
