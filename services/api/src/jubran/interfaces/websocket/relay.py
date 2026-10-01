"""Delivers committed outbox events to the WebSockets connected to this worker.

Each worker polls the outbox (cheap indexed range query, only while someone is
listening) and pushes new events to its own sockets, so live updates work the
same with one server process or many.

- Admin sockets receive every event with its payload.
- Guests at the event's table receive only a refresh signal (e.g. "orders.changed");
  the page then reloads its own data through the authorised API.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.events import SESSION_ENDED_EVENT
from jubran.infrastructure.db.models import OutboxEventModel

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.5
# Events are re-read for this long, so one committed a little late is still delivered.
OVERLAP = timedelta(seconds=30)
RETENTION = timedelta(hours=6)
PURGE_EVERY_SECONDS = 600

# What a guest at the table is told for each kind of event.
GUEST_SIGNALS = {
    "order": "orders.changed",
    "service_request": "service_requests.changed",
    "complaint": "service_requests.changed",
    "table_session": "visit.closed",
    "assistant_conversation": "conversation.changed",
}
# Only the guests' own pages care about these; staff screens are not refreshed for them.
GUEST_ONLY = {"assistant_conversation"}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class EventRelay:
    def __init__(self, session_factory: Callable[[], AsyncSession], manager, clock=None):
        self.session_factory = session_factory
        self.manager = manager
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.seen: Dict[str, datetime] = {}
        self.not_before: Optional[datetime] = None
        self._last_purge = self.clock()

    async def poll_once(self) -> int:
        """Deliver events not delivered yet; returns how many were delivered."""
        now = self.clock()
        if not self.manager.has_listeners():
            # Nobody to tell. Pages load a fresh snapshot when they connect, so
            # anything that happens meanwhile does not need replaying.
            self.not_before, self.seen = None, {}
            return 0
        if self.not_before is None:
            self.not_before = now
        since = max(self.not_before, now - OVERLAP)
        async with self.session_factory() as db:
            rows = (await db.execute(
                select(OutboxEventModel).where(OutboxEventModel.created_at >= since - timedelta(seconds=1))
                .order_by(OutboxEventModel.created_at, OutboxEventModel.id)
            )).scalars().all()
        delivered = 0
        for row in rows:
            created = _utc(row.created_at)
            if row.id in self.seen or created < self.not_before:
                continue
            self.seen[row.id] = created
            await self._deliver(row)
            delivered += 1
        cutoff = now - OVERLAP - timedelta(seconds=5)
        self.seen = {key: value for key, value in self.seen.items() if value >= cutoff}
        return delivered

    async def _deliver(self, row: OutboxEventModel) -> None:
        try:
            payload = json.loads(row.payload_json or "{}")
        except ValueError:
            payload = {}
        if row.event_type == SESSION_ENDED_EVENT:
            await self.manager.close_admin_session(payload.get("session_key", ""))
            return
        if row.aggregate_type not in GUEST_ONLY:
            await self.manager.broadcast_to_admin(row.event_type, payload)
        signal = GUEST_SIGNALS.get(row.aggregate_type)
        table_session_id = payload.get("table_session_id")
        if signal and table_session_id:
            await self.manager.broadcast_to_table(table_session_id, signal, {})

    async def purge_old(self) -> None:
        async with self.session_factory() as db:
            await db.execute(delete(OutboxEventModel).where(OutboxEventModel.created_at < self.clock() - RETENTION))
            await db.commit()

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
                if (self.clock() - self._last_purge).total_seconds() > PURGE_EVERY_SECONDS:
                    self._last_purge = self.clock()
                    await self.purge_old()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Event relay poll failed")
            await asyncio.sleep(POLL_SECONDS)
