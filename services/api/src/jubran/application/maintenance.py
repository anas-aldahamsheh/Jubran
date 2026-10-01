"""Housekeeping: expired records, and tables nobody closed.

Runs when the server starts and then every few minutes in each worker; every
step is safe to run twice at the same time.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application import rate_limiter
from jubran.application.ai.conversation_store import ConversationStore
from jubran.application.ai.usage import purge_old_usage
from jubran.application.floor_service import FloorService
from jubran.infrastructure.db.models import AuthSessionModel, IdempotencyRecordModel

logger = logging.getLogger("jubran.maintenance")

MAINTENANCE_INTERVAL_SECONDS = 10 * 60


async def purge_expired_records(db: AsyncSession) -> None:
    """Drop expired idempotency keys, ended logins, old rate-limit counters and finished assistant conversations."""
    now = datetime.now(timezone.utc)
    await db.execute(delete(IdempotencyRecordModel).where(IdempotencyRecordModel.expires_at <= now))
    # Logins that ended (signed out or expired) more than a day ago.
    day_ago = now - timedelta(days=1)
    await db.execute(delete(AuthSessionModel).where(
        (AuthSessionModel.expires_at <= day_ago) | (AuthSessionModel.revoked_at <= day_ago)))
    await db.commit()
    await rate_limiter.purge_expired(db)
    await ConversationStore.purge_finished(db)
    await purge_old_usage(db)


async def run_maintenance(db: AsyncSession) -> None:
    await purge_expired_records(db)
    closed = await FloorService.close_idle_table_sessions(db)
    if closed:
        logger.info("Closed idle tables automatically: %s", ", ".join(closed))


async def maintenance_loop(session_factory, interval: float = MAINTENANCE_INTERVAL_SECONDS) -> None:
    """Run the housekeeping now and then every ``interval`` seconds until cancelled."""
    while True:
        try:
            async with session_factory() as db:
                await run_maintenance(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # a failed round must never stop the next one
            logger.exception("Maintenance round failed")
        await asyncio.sleep(interval)
