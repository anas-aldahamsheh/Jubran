"""Public demo: anyone can try the guest side, without a table's printed QR code.

Only when PUBLIC_DEMO is on (a copy open to everyone on the internet):
- "Try as a guest" seats each visitor at a free table of their own (guests at one table
  share its orders). When every table is taken, a table nobody used for a while is closed
  and handed to the new visitor; tables still in use are never taken away.
- A pretend kitchen moves each order along by itself (preparing, ready, served), so the
  visitor follows the whole journey with no staff watching.
- The assistant answers a limited number of messages a day in total, because every
  visitor shares the server's AI key (bucket "assistant_demo_day").
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.floor_service import FloorService
from jubran.application.ordering_service import OrderingService
from jubran.application.session_service import SessionService
from jubran.domain.enums import OrderStatus, TableSessionStatus
from jubran.domain.exceptions import BusinessRuleError, DomainException
from jubran.infrastructure.db.models import CustomerSessionModel, OrderModel, PhysicalTableModel, TableSessionModel

logger = logging.getLogger("jubran.public_demo")

# A taken table goes to the next visitor after this long without any activity.
DEMO_TABLE_IDLE = timedelta(minutes=10)
# The pretend kitchen: accepts an order, cooks it, then brings it to the table.
KITCHEN_ACCEPTS_AFTER = timedelta(seconds=15)
KITCHEN_COOKS_FOR = timedelta(seconds=60)
KITCHEN_SERVES_AFTER = timedelta(seconds=30)
KITCHEN_INTERVAL_SECONDS = 10


class DemoTablesBusy(BusinessRuleError):
    def __init__(self):
        super().__init__(
            "كل الطاولات مشغولة الآن. جرّب مرة ثانية بعد دقائق.", "DEMO_TABLES_BUSY", 503,
            {"message_en": "Every table is taken right now. Please try again in a few minutes."},
        )


def _aware(moment: datetime) -> datetime:
    # SQLite hands back naive datetimes; they are stored in UTC.
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


async def _seat_at_free_table(db: AsyncSession, table_id: str, now: datetime) -> Optional[Tuple[str, CustomerSessionModel, PhysicalTableModel]]:
    """Start a new visit at this table, unless someone took it meanwhile (checked under the table lock)."""
    table = (await db.execute(
        select(PhysicalTableModel).where(PhysicalTableModel.id == table_id, PhysicalTableModel.is_active == True)  # noqa: E712
        .with_for_update()
    )).scalar_one_or_none()
    taken = (await db.execute(
        select(TableSessionModel.id).where(TableSessionModel.physical_table_id == table_id,
                                           TableSessionModel.status == TableSessionStatus.ACTIVE).limit(1)
    )).first()
    if table is None or taken is not None:
        await db.rollback()
        return None
    visit = TableSessionModel(physical_table_id=table.id, started_at=now, status=TableSessionStatus.ACTIVE)
    db.add(visit)
    await db.flush()
    raw_token, guest = SessionService.add_guest(db, visit, now)
    await db.commit()
    await db.refresh(guest)
    return raw_token, guest, table


async def _free_up_an_idle_table(db: AsyncSession, now: datetime) -> bool:
    """Close the oldest visit nobody used for DEMO_TABLE_IDLE (with nothing still cooking); False if none."""
    visits = (await db.execute(
        select(TableSessionModel.id).where(TableSessionModel.status == TableSessionStatus.ACTIVE)
        .order_by(TableSessionModel.started_at)
    )).scalars().all()
    await db.rollback()
    minutes = int(DEMO_TABLE_IDLE.total_seconds() // 60)
    for visit_id in visits:
        try:
            await FloorService.close_table_session(
                db, visit_id, None, idle_since=now - DEMO_TABLE_IDLE,
                automatic_reason=f"زيارة تجريبية انتهت بعد {minutes} دقائق بدون نشاط")
        except BusinessRuleError:
            await db.rollback()  # still in use: someone is at this table
            continue
        return True
    return False


async def seat_demo_guest(db: AsyncSession, existing_session_token: Optional[str],
                          now: Optional[datetime] = None) -> Tuple[str, CustomerSessionModel, PhysicalTableModel]:
    """The visitor's own open visit, or a new one at a free table. Raises DemoTablesBusy."""
    if existing_session_token:
        current = await SessionService.get_customer_session_by_token(db, existing_session_token)
        if current is not None:
            guest, _visit, table = current
            return existing_session_token, guest, table

    now = now or datetime.now(timezone.utc)
    for attempt in range(2):
        if attempt == 1 and not await _free_up_an_idle_table(db, now):
            break
        free = (await db.execute(
            select(PhysicalTableModel.id).where(
                PhysicalTableModel.is_active == True,  # noqa: E712
                ~select(TableSessionModel.id).where(
                    TableSessionModel.physical_table_id == PhysicalTableModel.id,
                    TableSessionModel.status == TableSessionStatus.ACTIVE,
                ).exists(),
            )
        )).scalars().all()
        await db.rollback()
        random.shuffle(free)  # visitors arriving together rarely reach for the same table
        for table_id in free:
            seated = await _seat_at_free_table(db, table_id, now)
            if seated is not None:
                return seated
    raise DemoTablesBusy()


async def run_demo_kitchen(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Move each order one step once it has waited long enough; returns how many moved."""
    now = now or datetime.now(timezone.utc)
    waiting = (await db.execute(
        select(OrderModel.id, OrderModel.status, OrderModel.updated_at, OrderModel.created_at).where(
            OrderModel.served_at.is_(None),
            OrderModel.status.in_([OrderStatus.PENDING_APPROVAL, OrderStatus.PREPARING, OrderStatus.READY]),
        )
    )).all()
    await db.rollback()

    moved = 0
    for order_id, status, updated_at, created_at in waiting:
        waited = now - _aware(updated_at or created_at)
        try:
            if status == OrderStatus.PENDING_APPROVAL and waited >= KITCHEN_ACCEPTS_AFTER:
                await OrderingService.update_order_status(db, order_id, OrderStatus.PREPARING)
            elif status == OrderStatus.PREPARING and waited >= KITCHEN_COOKS_FOR:
                await OrderingService.update_order_status(db, order_id, OrderStatus.READY)
            elif status == OrderStatus.READY and waited >= KITCHEN_SERVES_AFTER:
                await FloorService.mark_order_served(db, order_id)
            else:
                continue
        except DomainException:
            await db.rollback()  # the guest changed or cancelled it meanwhile; next round
            continue
        moved += 1
    return moved


async def demo_kitchen_loop(session_factory, interval: float = KITCHEN_INTERVAL_SECONDS) -> None:
    """Run the pretend kitchen every ``interval`` seconds until cancelled."""
    while True:
        try:
            async with session_factory() as db:
                await run_demo_kitchen(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # a failed round must never stop the next one
            logger.exception("Demo kitchen round failed")
        await asyncio.sleep(interval)
