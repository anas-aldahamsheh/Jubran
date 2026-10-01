"""Small transaction helpers shared by long-running flows (assistant turns, indexing)."""
from sqlalchemy.ext.asyncio import AsyncSession


async def end_transaction(db: AsyncSession) -> None:
    """Finish the current transaction, releasing read snapshots and row/advisory locks.

    Changes are committed by the services that make them, so this normally only
    ends a read-only transaction. If the transaction already failed, roll it back.
    """
    try:
        await db.commit()
    except Exception:
        await db.rollback()
