"""Application service for Table and Customer sessions."""
import secrets
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jubran.infrastructure.db.models import TableQrTokenModel, PhysicalTableModel, TableSessionModel, CustomerSessionModel
from jubran.domain.enums import TableSessionStatus
from jubran.domain.exceptions import InvalidQRTokenException
from jubran.settings import settings
from jubran.infrastructure.auth.tokens import hash_token


LAST_SEEN_PRECISION = timedelta(minutes=1)



class SessionService:
    @staticmethod
    async def start_or_resume_session(
        db: AsyncSession,
        qr_token: str,
        existing_session_token: Optional[str] = None
    ) -> Tuple[str, CustomerSessionModel, PhysicalTableModel]:
        """Resolve QR token to table, activate table session, create/resume customer session."""
        token_hash = hash_token(qr_token)
        
        # 1. Authoritative QR token lookup
        stmt = (
            select(TableQrTokenModel)
            .where(
                TableQrTokenModel.token_hash == token_hash,
                TableQrTokenModel.is_active == True,
                TableQrTokenModel.revoked_at.is_(None)
            )
        )
        qr_record = (await db.execute(stmt)).scalar_one_or_none()
        if not qr_record:
            raise InvalidQRTokenException("رمز الطاولة غير صالح أو ملغي.")

        # 2. Authoritative table lookup
        table = (await db.execute(
            select(PhysicalTableModel).where(
                PhysicalTableModel.id == qr_record.physical_table_id,
                PhysicalTableModel.is_active == True
            ).with_for_update()
        )).scalar_one_or_none()
        if not table:
            raise InvalidQRTokenException("الطاولة المرتبطة بهذا الرمز غير نشطة.")

        # 3. Find or create active table session (one active session per physical table)
        active_table_sessions = (await db.execute(
            select(TableSessionModel).where(
                TableSessionModel.physical_table_id == table.id,
                TableSessionModel.status == TableSessionStatus.ACTIVE
            ).order_by(TableSessionModel.started_at.desc(), TableSessionModel.id.desc())
        )).scalars().all()

        now = datetime.now(timezone.utc)
        # Preserve the customer's own active visit when an older race left more
        # than one active session attached to the same physical table.
        if existing_session_token and active_table_sessions:
            cust_token_hash = hash_token(existing_session_token)
            existing_cust_session = (await db.execute(
                select(CustomerSessionModel).where(
                    CustomerSessionModel.token_hash == cust_token_hash,
                    CustomerSessionModel.table_session_id.in_([s.id for s in active_table_sessions]),
                    CustomerSessionModel.expires_at > now
                )
            )).scalar_one_or_none()
            if existing_cust_session:
                existing_cust_session.last_seen_at = now
                await db.commit()
                return existing_session_token, existing_cust_session, table

        active_table_session = active_table_sessions[0] if active_table_sessions else None
        if not active_table_session:
            active_table_session = TableSessionModel(
                physical_table_id=table.id,
                started_at=now,
                status=TableSessionStatus.ACTIVE
            )
            db.add(active_table_session)
            await db.flush()

        # 4. Create new Customer Session
        raw_cust_token, cust_session = SessionService.add_guest(db, active_table_session, now)
        await db.commit()
        await db.refresh(cust_session)

        return raw_cust_token, cust_session, table

    @staticmethod
    def add_guest(db: AsyncSession, table_session: TableSessionModel, now: datetime) -> Tuple[str, CustomerSessionModel]:
        """A new guest at this table visit: their raw visit token (for the cookie) and session. The caller commits."""
        raw_cust_token = secrets.token_urlsafe(32)
        cust_session = CustomerSessionModel(
            table_session_id=table_session.id,
            token_hash=hash_token(raw_cust_token),
            started_at=now,
            expires_at=now + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS),
            last_seen_at=now
        )
        db.add(cust_session)
        return raw_cust_token, cust_session

    @staticmethod
    async def get_customer_session_by_token(
        db: AsyncSession,
        session_token: str
    ) -> Optional[Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel]]:
        """Validate customer session token and retrieve associated table session and table."""
        if not session_token:
            return None
        now = datetime.now(timezone.utc)
        token_hash = hash_token(session_token)
        stmt = (
            select(CustomerSessionModel, TableSessionModel, PhysicalTableModel)
            .join(TableSessionModel, CustomerSessionModel.table_session_id == TableSessionModel.id)
            .join(PhysicalTableModel, TableSessionModel.physical_table_id == PhysicalTableModel.id)
            .where(
                CustomerSessionModel.token_hash == token_hash,
                CustomerSessionModel.expires_at > now,
                TableSessionModel.status == TableSessionStatus.ACTIVE
            )
        )
        result = (await db.execute(stmt)).first()
        if not result:
            return None
        
        cust_session, table_session, physical_table = result
        # "Last seen" feeds the idle-table safety timeout; minute precision is plenty,
        # so most requests (and page polling) do not write to the database at all.
        last_seen = cust_session.last_seen_at
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if last_seen is None or now - last_seen >= LAST_SEEN_PRECISION:
            cust_session.last_seen_at = now
            await db.commit()
        return cust_session, table_session, physical_table
