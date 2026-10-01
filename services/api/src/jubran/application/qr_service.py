"""Table QR management: create, replace and disable unguessable table codes.

Each physical table has at most one active QR. The raw token is random, looked
up by its SHA-256 hash, and kept encrypted so an administrator can download the
printable QR again at any time. Replacing or disabling a QR makes any printed
copy of the previous one stop working immediately.
"""
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.domain.exceptions import DomainException, EntityNotFoundException
from jubran.infrastructure.auth.secret_box import decrypt_secret, encrypt_secret
from jubran.infrastructure.db.models import PhysicalTableModel, TableQrTokenModel
from jubran.infrastructure.auth.tokens import hash_token

# 24 random bytes = 192 bits, encoded as 32 URL-safe characters.
QR_TOKEN_BYTES = 24



class QrUpdateConflictException(DomainException):
    http_status = 409

    def __init__(self):
        super().__init__(
            code="QR_UPDATE_CONFLICT",
            message="تم تعديل رمز هذه الطاولة للتو. حدّث الصفحة وحاول مرة أخرى.",
        )


class QrService:
    @staticmethod
    def _serialize(table: PhysicalTableModel, qr: Optional[TableQrTokenModel]) -> Dict[str, Any]:
        token = None
        if qr is not None and qr.token_ciphertext:
            try:
                token = decrypt_secret(qr.token_ciphertext)
            except ValueError:
                # No configured DATA_ENCRYPTION_KEYS key can read it (an old key was removed); the admin can replace it.
                token = None
        return {
            "id": table.id,
            "table_number": table.table_number,
            "shape": table.shape.value,
            "seat_count": table.seat_count,
            "x_percent": table.x_percent,
            "y_percent": table.y_percent,
            "rotation_deg": table.rotation_deg,
            "is_active": table.is_active,
            "has_active_qr": qr is not None,
            "qr_token_id": qr.id if qr else None,
            "qr_created_at": qr.created_at.isoformat() if qr else None,
            "qr_token": token,
        }

    @staticmethod
    async def _lock_table(db: AsyncSession, table_id: str) -> PhysicalTableModel:
        table = (await db.execute(
            select(PhysicalTableModel).where(PhysicalTableModel.id == table_id).with_for_update()
        )).scalar_one_or_none()
        if not table:
            raise EntityNotFoundException("PhysicalTable", table_id)
        return table

    @staticmethod
    async def _revoke_active(db: AsyncSession, table_id: str, now: datetime) -> None:
        active_tokens = (await db.execute(
            select(TableQrTokenModel).where(
                TableQrTokenModel.physical_table_id == table_id,
                TableQrTokenModel.is_active == True,
            )
        )).scalars().all()
        for token in active_tokens:
            token.is_active = False
            token.revoked_at = now
        # Write the revocation before any new QR so the one-active-QR rule holds.
        await db.flush()

    @classmethod
    async def list_tables(cls, db: AsyncSession) -> List[Dict[str, Any]]:
        """List all tables with their current QR."""
        stmt = (
            select(PhysicalTableModel, TableQrTokenModel)
            .outerjoin(
                TableQrTokenModel,
                (PhysicalTableModel.id == TableQrTokenModel.physical_table_id) &
                (TableQrTokenModel.is_active == True) &
                (TableQrTokenModel.revoked_at.is_(None))
            )
            .order_by(PhysicalTableModel.table_number)
        )
        rows = (await db.execute(stmt)).all()
        tables = [cls._serialize(table, qr) for table, qr in rows]
        # Natural order: T1, T2, ... T10 instead of T1, T10, T11, T2.
        tables.sort(key=lambda item: (len(item["table_number"]), item["table_number"]))
        return tables

    @classmethod
    async def create_table_qr(cls, db: AsyncSession, table_id: str) -> Dict[str, Any]:
        """Create a new QR for a table, replacing (and invalidating) any current one."""
        table = await cls._lock_table(db, table_id)
        now = datetime.now(timezone.utc)
        await cls._revoke_active(db, table.id, now)

        raw_token = secrets.token_urlsafe(QR_TOKEN_BYTES)
        new_qr = TableQrTokenModel(
            physical_table_id=table.id,
            token_hash=hash_token(raw_token),
            token_ciphertext=encrypt_secret(raw_token),
            is_active=True,
            created_at=now,
        )
        db.add(new_qr)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise QrUpdateConflictException() from exc
        return cls._serialize(table, new_qr)

    @classmethod
    async def deactivate_table_qr(cls, db: AsyncSession, table_id: str) -> Dict[str, Any]:
        """Disable the table's QR so printed copies stop working, without issuing a new one."""
        table = await cls._lock_table(db, table_id)
        await cls._revoke_active(db, table.id, datetime.now(timezone.utc))
        await db.commit()
        return cls._serialize(table, None)
