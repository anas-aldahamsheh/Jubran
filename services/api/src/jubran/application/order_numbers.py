"""Race-free order numbers: JB-101, JB-102, … (JB for Jubran)

The next number comes from one atomic ``UPDATE … RETURNING`` on a counter row,
so two guests submitting at the same moment can never receive the same number
(the database serialises the two updates).
"""
import re

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.db.models import CounterModel, OrderModel

ORDER_COUNTER = "order_number"
FIRST_ORDER_NUMBER = 101
_NUMBER = re.compile(r"^JB-(\d+)$")


async def _increment(db: AsyncSession):
    stmt = (
        update(CounterModel)
        .where(CounterModel.name == ORDER_COUNTER)
        .values(value=CounterModel.value + 1)
        .returning(CounterModel.value)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def next_order_number(db: AsyncSession) -> str:
    value = await _increment(db)
    if value is None:
        # First order on this database (or first since the upgrade): continue
        # after the highest number already used. Concurrent first calls are
        # safe: only one insert wins, and both then increment the same row.
        used = [int(m.group(1)) for (number,) in (await db.execute(select(OrderModel.order_number)))
                if (m := _NUMBER.match(number or ""))]
        start = max(used, default=FIRST_ORDER_NUMBER - 1)
        dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        await db.execute(insert(CounterModel).values(name=ORDER_COUNTER, value=start).on_conflict_do_nothing())
        value = await _increment(db)
    return f"JB-{value}"
