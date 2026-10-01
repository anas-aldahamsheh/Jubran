"""Assistant conversation state kept in the database (not in server memory).

Survives restarts and works with several server workers: any worker can pick up
the guest's next message, including a "yes, send it" to a summary shown earlier.
"""
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.domain.enums import TableSessionStatus
from jubran.domain.exceptions import BusinessRuleError
from jubran.application.events import record_event
from jubran.infrastructure.auth.secret_box import decrypt_secret, encrypt_secret
from jubran.infrastructure.db.models import (
    AssistantConversationModel, CustomerSessionModel, TableSessionModel,
)

MAX_MESSAGES = 40
# Dishes the assistant showed stay "known" for this many guest turns (then it looks them up again).
KNOWN_PRODUCT_TURNS = 4
MAX_KNOWN_PRODUCTS = 40
# Longest a single assistant turn may keep the guest's conversation busy. If a
# worker dies mid-turn, the conversation frees itself after this time.
LEASE_SECONDS = 120
WAIT_FOR_PREVIOUS_TURN_SECONDS = 25.0
_POLL_SECONDS = 0.25


@dataclass
class PendingConfirmation:
    token: str
    version: int
    summary: Dict[str, Any]


@dataclass
class PendingAmendment:
    """A change to a sent order, shown to the guest and waiting for their "yes"."""
    order_id: str
    order_version: int
    operations: List[Dict[str, Any]]
    summary: Dict[str, Any]


@dataclass
class ConversationState:
    customer_session_id: str
    history: List[Dict[str, str]] = field(default_factory=list)
    pending: Optional[PendingConfirmation] = None
    upsell_suggested: bool = False
    pending_amendment: Optional[PendingAmendment] = None
    # product id -> {"name_ar", "name_en", "price_minor", "turn"}; see remember_products.
    known_products: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    turn: int = 0

    def append(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        self.history = self.history[-MAX_MESSAGES:]

    def start_turn(self) -> None:
        """A new guest turn: forget dishes shown too long ago."""
        self.turn += 1
        oldest = self.turn - KNOWN_PRODUCT_TURNS
        self.known_products = {pid: info for pid, info in self.known_products.items() if info.get("turn", 0) > oldest}

    def remember_products(self, products: List[Dict[str, Any]]) -> None:
        """Dishes shown to the guest this turn (from search, details or suggestions)."""
        for product in products:
            product_id = product.get("id")
            if product_id:
                self.known_products[product_id] = {
                    "name_ar": product.get("name_ar"), "name_en": product.get("name_en"),
                    "price_minor": product.get("price_minor"), "turn": self.turn}
        if len(self.known_products) > MAX_KNOWN_PRODUCTS:
            newest = sorted(self.known_products.items(), key=lambda item: item[1].get("turn", 0), reverse=True)
            self.known_products = dict(newest[:MAX_KNOWN_PRODUCTS])


def assistant_busy() -> BusinessRuleError:
    return BusinessRuleError("المساعد ما زال يرد على رسالتك السابقة. انتظر لحظة ثم حاول مرة أخرى.",
                             "ASSISTANT_BUSY", 409)


class ConversationStore:
    @staticmethod
    async def _ensure_row(db: AsyncSession, customer_session_id: str) -> None:
        dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        await db.execute(insert(AssistantConversationModel)
                         .values(customer_session_id=customer_session_id, messages_json="[]", upsell_suggested=False)
                         .on_conflict_do_nothing(index_elements=[AssistantConversationModel.customer_session_id]))

    @classmethod
    async def acquire(cls, db: AsyncSession, customer_session_id: str,
                      wait_seconds: float = WAIT_FOR_PREVIOUS_TURN_SECONDS) -> str:
        """Take the guest's conversation for one turn; waits for a turn already running."""
        lease = uuid.uuid4().hex
        deadline = time.monotonic() + wait_seconds
        await cls._ensure_row(db, customer_session_id)
        await db.commit()
        while True:
            now = datetime.now(timezone.utc)
            claimed = await db.execute(
                update(AssistantConversationModel)
                .where(AssistantConversationModel.customer_session_id == customer_session_id,
                       or_(AssistantConversationModel.busy_until.is_(None),
                           AssistantConversationModel.busy_until < now))
                .values(busy_until=now + timedelta(seconds=LEASE_SECONDS), busy_lease=lease)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            if claimed.rowcount == 1:
                return lease
            if time.monotonic() >= deadline:
                raise assistant_busy()
            await asyncio.sleep(_POLL_SECONDS)

    @staticmethod
    async def renew(db: AsyncSession, customer_session_id: str, lease: str) -> None:
        """Keep a long turn (a voice call) holding the conversation."""
        await db.execute(
            update(AssistantConversationModel)
            .where(AssistantConversationModel.customer_session_id == customer_session_id,
                   AssistantConversationModel.busy_lease == lease)
            .values(busy_until=datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS))
            .execution_options(synchronize_session=False)
        )
        await db.commit()

    @staticmethod
    async def release(db: AsyncSession, customer_session_id: str, lease: str) -> None:
        await db.execute(
            update(AssistantConversationModel)
            .where(AssistantConversationModel.customer_session_id == customer_session_id,
                   AssistantConversationModel.busy_lease == lease)
            .values(busy_until=None, busy_lease=None)
            .execution_options(synchronize_session=False)
        )
        await db.commit()

    @staticmethod
    async def load(db: AsyncSession, customer_session_id: str) -> ConversationState:
        row = (await db.execute(select(AssistantConversationModel).where(
            AssistantConversationModel.customer_session_id == customer_session_id))).scalar_one_or_none()
        state = ConversationState(customer_session_id=customer_session_id)
        if row is None:
            return state
        try:
            state.history = [m for m in json.loads(row.messages_json or "[]") if isinstance(m, dict)]
        except ValueError:
            state.history = []
        state.upsell_suggested = bool(row.upsell_suggested)
        try:
            known = json.loads(row.known_products_json or "{}")
            state.turn = int(known.get("turn", 0))
            state.known_products = {pid: info for pid, info in (known.get("products") or {}).items()
                                    if isinstance(info, dict)}
        except (ValueError, TypeError, AttributeError):
            state.turn, state.known_products = 0, {}
        try:
            amendment = json.loads(row.pending_amendment_json) if row.pending_amendment_json else None
            if amendment:
                state.pending_amendment = PendingAmendment(
                    order_id=amendment["order_id"], order_version=int(amendment["order_version"]),
                    operations=list(amendment["operations"]), summary=dict(amendment["summary"]))
        except (ValueError, TypeError, KeyError):
            state.pending_amendment = None
        if row.pending_token_ciphertext and row.pending_summary_json and row.pending_draft_version is not None:
            try:
                state.pending = PendingConfirmation(token=decrypt_secret(row.pending_token_ciphertext),
                                                    version=row.pending_draft_version,
                                                    summary=json.loads(row.pending_summary_json))
            except ValueError:
                state.pending = None  # unreadable (e.g. SECRET_KEY changed): the guest simply reviews again
        return state

    @classmethod
    async def save(cls, db: AsyncSession, state: ConversationState) -> None:
        await cls._ensure_row(db, state.customer_session_id)
        pending = state.pending
        await db.execute(
            update(AssistantConversationModel)
            .where(AssistantConversationModel.customer_session_id == state.customer_session_id)
            .values(
                messages_json=json.dumps(state.history[-MAX_MESSAGES:], ensure_ascii=False),
                pending_token_ciphertext=encrypt_secret(pending.token) if pending else None,
                pending_draft_version=pending.version if pending else None,
                pending_summary_json=json.dumps(pending.summary, ensure_ascii=False, default=str) if pending else None,
                upsell_suggested=state.upsell_suggested,
                known_products_json=json.dumps({"turn": state.turn, "products": state.known_products},
                                               ensure_ascii=False),
                pending_amendment_json=json.dumps({
                    "order_id": state.pending_amendment.order_id,
                    "order_version": state.pending_amendment.order_version,
                    "operations": state.pending_amendment.operations,
                    "summary": state.pending_amendment.summary,
                }, ensure_ascii=False, default=str) if state.pending_amendment else None,
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )
        await cls._announce_change(db, state.customer_session_id)
        await db.commit()

    @staticmethod
    async def _announce_change(db: AsyncSession, customer_session_id: str) -> None:
        """Open chat panels of this guest (another tab, after voice mode) reload the conversation."""
        table_session_id = (await db.execute(
            select(CustomerSessionModel.table_session_id).where(CustomerSessionModel.id == customer_session_id)
        )).scalar_one_or_none()
        if table_session_id:
            record_event(db, "assistant.conversation_changed", "assistant_conversation", customer_session_id,
                         {"table_session_id": table_session_id})

    @classmethod
    async def clear(cls, db: AsyncSession, customer_session_id: str) -> None:
        """Forget the conversation (messages, pending summary, suggestion) but not a running turn's lease."""
        await db.execute(
            update(AssistantConversationModel)
            .where(AssistantConversationModel.customer_session_id == customer_session_id)
            .values(messages_json="[]", pending_token_ciphertext=None, pending_draft_version=None,
                    pending_summary_json=None, upsell_suggested=False, known_products_json=None,
                    pending_amendment_json=None, updated_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        await cls._announce_change(db, customer_session_id)
        await db.commit()

    @staticmethod
    async def delete_for_visits(db: AsyncSession, table_session_ids: List[str]) -> None:
        """Called inside the table-closing transaction (the caller commits)."""
        if not table_session_ids:
            return
        guests = select(CustomerSessionModel.id).where(CustomerSessionModel.table_session_id.in_(table_session_ids))
        await db.execute(delete(AssistantConversationModel)
                         .where(AssistantConversationModel.customer_session_id.in_(guests))
                         .execution_options(synchronize_session=False))

    @staticmethod
    async def purge_finished(db: AsyncSession) -> None:
        """Drop conversations of visits that ended or guest sessions that expired."""
        now = datetime.now(timezone.utc)
        finished = (select(CustomerSessionModel.id)
                    .join(TableSessionModel, TableSessionModel.id == CustomerSessionModel.table_session_id)
                    .where(or_(TableSessionModel.status != TableSessionStatus.ACTIVE,
                               CustomerSessionModel.expires_at <= now)))
        await db.execute(delete(AssistantConversationModel)
                         .where(AssistantConversationModel.customer_session_id.in_(finished))
                         .execution_options(synchronize_session=False))
        await db.commit()
