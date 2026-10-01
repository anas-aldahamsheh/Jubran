"""Realtime events (transactional outbox).

Every change staff or guests should see live is recorded here *in the same
transaction* as the change itself, so an event exists exactly when the change
was committed. The event relay in each server worker then delivers new events
to the admin and table WebSockets it holds (see interfaces/websocket/relay.py).
"""
import json
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.db.models import OutboxEventModel

# A login ended (logout): every worker closes live admin feeds opened with it.
SESSION_ENDED_EVENT = "auth.session_ended"


def record_event(db: AsyncSession, event_type: str, aggregate_type: str, aggregate_id: str,
                 payload: Dict[str, Any]) -> None:
    """Add an event to the current transaction; it is delivered after the caller commits.

    Include ``table_session_id`` in the payload when guests at that table should
    refresh (they only receive a "something changed" signal, never the payload).
    """
    db.add(OutboxEventModel(
        event_type=event_type, aggregate_type=aggregate_type, aggregate_id=str(aggregate_id),
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
    ))
