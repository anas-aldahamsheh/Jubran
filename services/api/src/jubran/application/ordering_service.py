"""Ordering, Draft, and Lifecycle Application Service."""
import secrets
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from jubran.infrastructure.db.models import (
    DraftOrderModel, DraftItemModel, DraftConfirmationModel,
    OrderModel, OrderItemModel, ProductModel, TableSessionModel,
    CustomerSessionModel, IdempotencyRecordModel, OutboxEventModel
)
from jubran.domain.enums import DraftStatus, OrderStatus, TableSessionStatus
from jubran.domain.exceptions import (
    EntityNotFoundException, ProductUnavailableException,
    DraftVersionConflictException, InvalidConfirmationTokenException,
    InvalidStateTransitionException, TableSessionClosedException, ConfirmedOrderChangedException,
    BusinessRuleError,
)
from jubran.domain.money import format_jod, fils_to_jod
from jubran.application.order_numbers import next_order_number
from jubran.infrastructure.auth.tokens import hash_token

SUBMIT_ORDER_OPERATION = "submit_order"

# Guests see exactly three order states (the spec); serving does not add a fourth.
CUSTOMER_STATUS_LABELS = {
    OrderStatus.PENDING_APPROVAL: ("بانتظار التأكيد", "Awaiting confirmation"),
    OrderStatus.PREPARING: ("قيد التحضير", "Preparing"),
    OrderStatus.READY: ("جاهز", "Ready"),
    OrderStatus.CANCELLED: ("أُلغي", "Cancelled"),
    OrderStatus.CLOSED: ("أُغلق", "Closed"),
}


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def sanitize_note(note: Optional[str]) -> Optional[str]:
    """Clean a guest's note: no control or text-direction characters, no angle brackets,
    single spaces, at most 200 characters."""
    if not note:
        return None
    cleaned = _CONTROL_CHARACTERS.sub("", note).replace("\n", " ").replace("\t", " ")
    cleaned = re.sub(r"[<>]", "", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return cleaned[:200] if cleaned else None



def summary_fingerprint(items: List[Dict[str, Any]]) -> str:
    """Hash of everything the guest agrees to when confirming (order of lines is irrelevant)."""
    lines = sorted(
        (item["line_id"], item["product_id"], item["quantity"], item["note"] or "",
         item["unit_price_minor"], bool(item["is_available"]))
        for item in items
    )
    return hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode("utf-8")).hexdigest()


MAX_LINE_QUANTITY = 50


def new_line_times(draft: DraftOrderModel, count: int) -> List[datetime]:
    """Creation times for new basket lines: after every existing line, in the order given.

    Lines are numbered by creation time ("the last one" = the one added last). A coarse
    clock (Windows ticks every ~16 ms) would give lines added together the same time.
    """
    stamp = datetime.now(timezone.utc)
    latest = max((item.created_at for item in draft.items if item.created_at is not None), default=None)
    if latest is not None and stamp <= latest:
        stamp = latest + timedelta(microseconds=1)
    return [stamp + timedelta(microseconds=index) for index in range(count)]


class DraftOperationError(Exception):
    """A batch of basket changes was refused; nothing in the batch was saved."""

    def __init__(self, code: str, index: int):
        super().__init__(code)
        self.code = code
        self.index = index


class OrderingService:
    @staticmethod
    async def get_or_create_open_draft(
        db: AsyncSession,
        customer_session_id: str,
        table_session_id: str
    ) -> DraftOrderModel:
        """Serialize draft access, recover duplicate open drafts, or create one."""
        customer_session = (await db.execute(
            select(CustomerSessionModel).where(
                CustomerSessionModel.id == customer_session_id,
                CustomerSessionModel.table_session_id == table_session_id,
            ).with_for_update()
        )).scalar_one_or_none()
        if not customer_session:
            raise EntityNotFoundException("CustomerSession", customer_session_id)

        stmt = (
            select(DraftOrderModel)
            .where(
                DraftOrderModel.customer_session_id == customer_session_id,
                DraftOrderModel.table_session_id == table_session_id,
                DraftOrderModel.status == DraftStatus.OPEN
            )
            .options(selectinload(DraftOrderModel.items).selectinload(DraftItemModel.product))
            .order_by(DraftOrderModel.created_at, DraftOrderModel.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        open_drafts = (await db.execute(stmt)).scalars().unique().all()
        if open_drafts:
            draft = open_drafts[0]
            if len(open_drafts) > 1:
                now = datetime.now(timezone.utc)
                matching_items = {(item.product_id, item.note): item for item in draft.items}
                for duplicate in open_drafts[1:]:
                    for item in duplicate.items:
                        key = (item.product_id, item.note)
                        existing_item = matching_items.get(key)
                        if existing_item:
                            existing_item.quantity = min(50, existing_item.quantity + item.quantity)
                            await db.delete(item)
                        else:
                            item.draft_order = draft
                            matching_items[key] = item
                    duplicate.status = DraftStatus.ABANDONED
                    duplicate.updated_at = now
                    duplicate.version += 1
                draft.updated_at = now
                draft.version += 1
                await db.commit()
                refresh_stmt = select(DraftOrderModel).where(DraftOrderModel.id == draft.id).options(
                    selectinload(DraftOrderModel.items).selectinload(DraftItemModel.product)
                )
                draft = (await db.execute(refresh_stmt)).scalar_one()
            return draft

        draft = DraftOrderModel(
                customer_session_id=customer_session_id,
                table_session_id=table_session_id,
                version=1,
                status=DraftStatus.OPEN
            )
        db.add(draft)
        await db.commit()
        # Reload with relationships
        stmt = select(DraftOrderModel).where(DraftOrderModel.id == draft.id).options(
            selectinload(DraftOrderModel.items).selectinload(DraftItemModel.product)
        )
        draft = (await db.execute(stmt)).scalar_one()
        return draft

    @classmethod
    async def get_draft_summary(cls, db: AsyncSession, customer_session_id: str, table_session_id: str) -> Dict[str, Any]:
        """Return authoritative draft summary with calculated totals."""
        draft = await cls.get_or_create_open_draft(db, customer_session_id, table_session_id)
        
        items_summary = []
        total_minor = 0

        for item in sorted(draft.items, key=lambda row: (row.created_at, row.id)):
            product = item.product
            line_total_minor = product.price_minor * item.quantity
            total_minor += line_total_minor
            items_summary.append({
                "line_id": item.id,
                "created_at": item.created_at.isoformat(),
                "product_id": product.id,
                "name_ar": product.name_ar,
                "name_en": product.name_en,
                "unit_price_minor": product.price_minor,
                "unit_price_display_ar": format_jod(product.price_minor, "ar"),
                "unit_price_display_en": format_jod(product.price_minor, "en"),
                "quantity": item.quantity,
                "note": item.note,
                "line_total_minor": line_total_minor,
                "line_total_display_ar": format_jod(line_total_minor, "ar"),
                "line_total_display_en": format_jod(line_total_minor, "en"),
                "is_available": product.is_available
            })

        return {
            "draft_id": draft.id,
            "version": draft.version,
            "status": draft.status.value,
            "items": items_summary,
            "total_minor": total_minor,
            "total_jod": str(fils_to_jod(total_minor)),
            "total_display_ar": format_jod(total_minor, "ar"),
            "total_display_en": format_jod(total_minor, "en"),
            "item_count": sum(i["quantity"] for i in items_summary)
        }

    @classmethod
    async def add_item_to_draft(
        cls,
        db: AsyncSession,
        customer_session_id: str,
        table_session_id: str,
        product_id: str,
        quantity: int = 1,
        note: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add item to draft or increment quantity if matching note exists."""
        if quantity < 1 or quantity > 50:
            raise BusinessRuleError("يجب أن تكون الكمية بين 1 و 50.", "INVALID_QUANTITY", 422)

        product = (await db.execute(select(ProductModel).where(ProductModel.id == product_id))).scalar_one_or_none()
        if not product:
            raise EntityNotFoundException("Product", product_id)
        if not product.is_available:
            raise ProductUnavailableException(product.name_ar)

        clean_note = sanitize_note(note)
        draft = await cls.get_or_create_open_draft(db, customer_session_id, table_session_id)

        # Look for existing item with identical note
        existing_item = next((i for i in draft.items if i.product_id == product_id and i.note == clean_note), None)
        if existing_item:
            existing_item.quantity += quantity
            if existing_item.quantity > 50:
                existing_item.quantity = 50
        else:
            new_item = DraftItemModel(
                draft_order_id=draft.id,
                product_id=product.id,
                quantity=quantity,
                note=clean_note,
                created_at=new_line_times(draft, 1)[0],
            )
            db.add(new_item)
            draft.items.append(new_item)

        draft.version += 1
        draft.updated_at = datetime.now(timezone.utc)
        await db.commit()

        return await cls.get_draft_summary(db, customer_session_id, table_session_id)

    @classmethod
    async def update_item_in_draft(
        cls,
        db: AsyncSession,
        customer_session_id: str,
        table_session_id: str,
        line_id: str,
        quantity: Optional[int] = None,
        note: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update draft line quantity or note."""
        draft = await cls.get_or_create_open_draft(db, customer_session_id, table_session_id)
        item = next((i for i in draft.items if i.id == line_id), None)
        if not item:
            raise EntityNotFoundException("DraftItem", line_id)

        if quantity is not None and quantity < 1:
            # Quantity zero removes the line; a note sent with it has nothing left to apply to.
            await db.delete(item)
        else:
            if quantity is not None:
                item.quantity = min(quantity, 50)
            if note is not None:
                item.note = sanitize_note(note)

        draft.version += 1
        draft.updated_at = datetime.now(timezone.utc)
        await db.commit()

        return await cls.get_draft_summary(db, customer_session_id, table_session_id)

    @classmethod
    async def remove_item_from_draft(
        cls,
        db: AsyncSession,
        customer_session_id: str,
        table_session_id: str,
        line_id: str
    ) -> Dict[str, Any]:
        """Remove item from draft."""
        draft = await cls.get_or_create_open_draft(db, customer_session_id, table_session_id)
        item = next((i for i in draft.items if i.id == line_id), None)
        if item:
            await db.delete(item)
            draft.version += 1
            draft.updated_at = datetime.now(timezone.utc)
            await db.commit()

        return await cls.get_draft_summary(db, customer_session_id, table_session_id)

    @classmethod
    async def apply_draft_operations(
        cls,
        db: AsyncSession,
        customer_session_id: str,
        table_session_id: str,
        operations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply several basket changes as one all-or-nothing change.

        Pass 1 works out the resulting basket on plain data and checks every
        operation (unknown line, unavailable dish, quantity outside 1-50…)
        without touching the database objects. Only if all are valid does
        pass 2 write the result, committed once. On any problem nothing is
        saved and DraftOperationError tells which operation failed.
        """
        if not operations:
            raise DraftOperationError("EMPTY_OPERATIONS", 0)
        draft = await cls.get_or_create_open_draft(db, customer_session_id, table_session_id)

        def valid_quantity(value: Any, minimum: int) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= MAX_LINE_QUANTITY

        # Pass 1: simulate. Keys are existing line ids or "new:<n>" for added lines.
        planned: Dict[str, Dict[str, Any]] = {
            item.id: {"product_id": item.product_id, "note": item.note, "quantity": item.quantity}
            for item in draft.items
        }
        for index, operation in enumerate(operations):
            kind = operation.get("op")
            if kind == "add":
                product_id, quantity = operation.get("product_id"), operation.get("quantity", 1)
                if not product_id or not valid_quantity(quantity, 1):
                    raise DraftOperationError("INVALID_ADD", index)
                product = (await db.execute(select(ProductModel).where(ProductModel.id == product_id))).scalar_one_or_none()
                if not product or not product.is_available:
                    raise DraftOperationError("PRODUCT_UNAVAILABLE", index)
                note = sanitize_note(operation.get("note"))
                same = next((line for line in planned.values()
                             if line["product_id"] == product_id and line["note"] == note), None)
                if same is not None:
                    if same["quantity"] + quantity > MAX_LINE_QUANTITY:
                        raise DraftOperationError("QUANTITY_LIMIT", index)
                    same["quantity"] += quantity
                else:
                    planned[f"new:{index}"] = {"product_id": product_id, "note": note, "quantity": quantity}
            elif kind in ("set_quantity", "remove"):
                line_id = operation.get("line_id") or ""
                if line_id not in planned or line_id.startswith("new:"):
                    raise DraftOperationError("DRAFT_LINE_NOT_FOUND", index)
                if kind == "set_quantity":
                    quantity = operation.get("quantity")
                    if not valid_quantity(quantity, 0):
                        raise DraftOperationError("INVALID_QUANTITY", index)
                    if quantity > 0:
                        planned[line_id]["quantity"] = quantity
                        continue
                del planned[line_id]
            else:
                raise DraftOperationError("INVALID_OPERATION", index)

        # Pass 2: write the planned basket in one commit.
        added = [key for key in planned if key.startswith("new:")]
        times = dict(zip(added, new_line_times(draft, len(added))))
        for item in list(draft.items):
            if item.id not in planned:
                draft.items.remove(item)
                await db.delete(item)
            else:
                item.quantity = planned[item.id]["quantity"]
        for key, line in planned.items():
            if key.startswith("new:"):
                new_line = DraftItemModel(draft_order_id=draft.id, product_id=line["product_id"],
                                          quantity=line["quantity"], note=line["note"], created_at=times[key])
                db.add(new_line)
                draft.items.append(new_line)
        draft.version += 1
        draft.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return await cls.get_draft_summary(db, customer_session_id, table_session_id)

    @classmethod
    async def prepare_confirmation(
        cls,
        db: AsyncSession,
        customer_session_id: str,
        table_session_id: str
    ) -> Dict[str, Any]:
        """Validate all items, ensure table session is open, issue short-lived confirmation challenge token."""
        # 1. Check table session status
        table_session = (await db.execute(select(TableSessionModel).where(TableSessionModel.id == table_session_id))).scalar_one_or_none()
        if not table_session or table_session.status != TableSessionStatus.ACTIVE:
            raise TableSessionClosedException()

        # 2. Get authoritative draft summary
        summary = await cls.get_draft_summary(db, customer_session_id, table_session_id)
        if not summary["items"]:
            raise BusinessRuleError("السلة فارغة. يرجى إضافة أصناف أولاً.", "EMPTY_DRAFT")

        # 3. Verify availability of every item
        for item in summary["items"]:
            if not item["is_available"]:
                raise ProductUnavailableException(item["name_ar"])

        # 4. Generate confirmation challenge token bound to customer session and draft version
        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)

        confirmation = DraftConfirmationModel(
            customer_session_id=customer_session_id,
            draft_order_id=summary["draft_id"],
            draft_version=summary["version"],
            confirmation_token_hash=token_hash,
            summary_fingerprint=summary_fingerprint(summary["items"]),
            confirmed_at=datetime.now(timezone.utc)
        )
        db.add(confirmation)
        await db.commit()

        return {
            "draft_version": summary["version"],
            "confirmation_token": raw_token,
            "summary": summary
        }

    @staticmethod
    async def _replayed_order(db: AsyncSession, idemp_hash: Optional[str]) -> Optional[Dict[str, Any]]:
        """The stored response of an earlier submit with the same Idempotency-Key, if any."""
        if not idemp_hash:
            return None
        record = (await db.execute(
            select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.key_hash == idemp_hash,
                IdempotencyRecordModel.operation_type == SUBMIT_ORDER_OPERATION,
                IdempotencyRecordModel.expires_at > datetime.now(timezone.utc),
            )
        )).scalar_one_or_none()
        return json.loads(record.response_payload) if record else None

    @classmethod
    async def submit_order(
        cls,
        db: AsyncSession,
        customer_session_id: str,
        table_session_id: str,
        confirmation_token: str,
        draft_version: int,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Submit a confirmed draft as one immutable order.

        Safe under concurrency: the confirmation and the draft are claimed with
        conditional updates (only one request can win), order numbers come from
        an atomic counter, and a repeated Idempotency-Key returns the first result.
        """
        now = datetime.now(timezone.utc)
        idemp_hash = (hashlib.sha256(f"{customer_session_id}:{idempotency_key}".encode()).hexdigest()
                      if idempotency_key else None)
        replay = await cls._replayed_order(db, idemp_hash)
        if replay is not None:
            return replay

        # 1. Validate confirmation token and draft version
        conf = (await db.execute(
            select(DraftConfirmationModel).where(
                DraftConfirmationModel.confirmation_token_hash == hash_token(confirmation_token),
                DraftConfirmationModel.customer_session_id == customer_session_id,
                DraftConfirmationModel.consumed_at.is_(None)
            )
        )).scalar_one_or_none()
        if not conf:
            replay = await cls._replayed_order(db, idemp_hash)
            if replay is not None:
                return replay
            raise InvalidConfirmationTokenException()

        if conf.draft_version != draft_version:
            raise DraftVersionConflictException(conf.draft_version)

        # 2. Load draft with items and products
        draft = (await db.execute(
            select(DraftOrderModel)
            .where(
                DraftOrderModel.id == conf.draft_order_id,
                DraftOrderModel.customer_session_id == customer_session_id,
                DraftOrderModel.table_session_id == table_session_id,
                DraftOrderModel.status == DraftStatus.OPEN
            )
            .options(selectinload(DraftOrderModel.items).selectinload(DraftItemModel.product))
        )).scalar_one_or_none()

        if not draft or not draft.items:
            raise BusinessRuleError("هذا الطلب أُرسل مسبقاً أو أن السلة فارغة. حدّث الصفحة وراجع طلبك.", "DRAFT_NOT_SUBMITTABLE", 409)

        if draft.version != draft_version:
            raise DraftVersionConflictException(draft.version)

        # The order must be exactly what the guest reviewed: a price change, a
        # removed dish or an item that ran out since then needs a fresh review.
        current_items = [
            {"line_id": item.id, "product_id": item.product_id, "quantity": item.quantity, "note": item.note,
             "unit_price_minor": item.product.price_minor, "is_available": item.product.is_available}
            for item in draft.items
        ]
        if conf.summary_fingerprint != summary_fingerprint(current_items):
            summary = await cls.get_draft_summary(db, customer_session_id, table_session_id)
            raise ConfirmedOrderChangedException(draft.version, summary)
        for item in draft.items:
            if not item.product.is_available:
                raise ProductUnavailableException(item.product.name_ar)

        # 3. Re-verify table session is active
        table_session = (await db.execute(select(TableSessionModel).where(TableSessionModel.id == table_session_id))).scalar_one()
        if table_session.status != TableSessionStatus.ACTIVE:
            raise TableSessionClosedException()

        # 4. Claim the confirmation and the draft. Each conditional update succeeds
        # for exactly one request; a concurrent double submit loses here.
        claimed = await db.execute(
            update(DraftConfirmationModel)
            .where(DraftConfirmationModel.id == conf.id, DraftConfirmationModel.consumed_at.is_(None))
            .values(consumed_at=now)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await db.rollback()
            replay = await cls._replayed_order(db, idemp_hash)
            if replay is not None:
                return replay
            raise InvalidConfirmationTokenException("تم إرسال هذا الطلب مسبقاً.")
        submitted = await db.execute(
            update(DraftOrderModel)
            .where(DraftOrderModel.id == draft.id, DraftOrderModel.status == DraftStatus.OPEN,
                   DraftOrderModel.version == draft_version)
            .values(status=DraftStatus.SUBMITTED, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if submitted.rowcount != 1:
            await db.rollback()
            current_version = (await db.execute(
                select(DraftOrderModel.version).where(DraftOrderModel.id == draft.id)
            )).scalar_one_or_none()
            raise DraftVersionConflictException(current_version if current_version is not None else draft_version)

        # 5. Create the order with a race-free number
        order = OrderModel(
            table_session_id=table_session_id,
            customer_session_id=customer_session_id,
            order_number=await next_order_number(db),
            status=OrderStatus.PENDING_APPROVAL,
            version=1,
            created_at=now,
            updated_at=now
        )
        db.add(order)
        await db.flush()

        # 6. Snapshot order items with authoritative prices
        total_minor = 0
        order_items_payload = []
        for item in draft.items:
            prod = item.product
            line_total = prod.price_minor * item.quantity
            total_minor += line_total
            db.add(OrderItemModel(
                order_id=order.id,
                product_id=prod.id,
                product_name_snapshot_ar=prod.name_ar,
                product_name_snapshot_en=prod.name_en,
                unit_price_minor_snapshot=prod.price_minor,
                quantity=item.quantity,
                note=item.note,
                line_total_minor=line_total
            ))
            order_items_payload.append({
                "product_id": prod.id,
                "name_ar": prod.name_ar,
                "name_en": prod.name_en,
                "quantity": item.quantity,
                "unit_price_display": format_jod(prod.price_minor, "ar"),
                "unit_price_display_en": format_jod(prod.price_minor, "en"),
                "line_total_display": format_jod(line_total, "ar"),
                "line_total_display_en": format_jod(line_total, "en"),
                "note": item.note
            })

        # 7. Record Outbox Event for Realtime
        db.add(OutboxEventModel(
            event_type="order.created",
            aggregate_type="order",
            aggregate_id=order.id,
            payload_json=json.dumps({
                "order_id": order.id,
                "order_number": order.order_number,
                "table_session_id": table_session_id,
                "status": order.status.value,
                "items": order_items_payload,
                "total_display": format_jod(total_minor, "ar"),
                "total_display_en": format_jod(total_minor, "en"),
                "created_at": now.isoformat()
            })
        ))

        response_data = {
            "order_id": order.id,
            "order_number": order.order_number,
            "status": order.status.value,
            "status_display_ar": CUSTOMER_STATUS_LABELS[OrderStatus.PENDING_APPROVAL][0],
            "status_display_en": CUSTOMER_STATUS_LABELS[OrderStatus.PENDING_APPROVAL][1],
            "items": order_items_payload,
            "total_minor": total_minor,
            "total_display_ar": format_jod(total_minor, "ar"),
            "total_display_en": format_jod(total_minor, "en"),
            "created_at": now.isoformat()
        }

        # 8. Remember the result for retries with the same Idempotency-Key
        if idemp_hash:
            # An expired record must not block reusing the key.
            await db.execute(delete(IdempotencyRecordModel).where(
                IdempotencyRecordModel.key_hash == idemp_hash,
                IdempotencyRecordModel.operation_type == SUBMIT_ORDER_OPERATION,
                IdempotencyRecordModel.expires_at <= now,
            ))
            db.add(IdempotencyRecordModel(
                key_hash=idemp_hash,
                operation_type=SUBMIT_ORDER_OPERATION,
                scope_id=customer_session_id,
                response_payload=json.dumps(response_data),
                expires_at=now + timedelta(hours=24)
            ))

        try:
            await db.commit()
        except IntegrityError:
            # A concurrent request with the same key finished first: return its order.
            await db.rollback()
            replay = await cls._replayed_order(db, idemp_hash)
            if replay is not None:
                return replay
            raise
        return response_data

    @staticmethod
    async def get_orders_for_customer(db: AsyncSession, table_session_id: str,
                                      customer_session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Orders of the current visit (all guests at the table), with exactly 3 customer-visible states.

        ``mine`` marks the asking guest's own orders; only those can be changed
        (``editable``), and only until they are ready or served.
        """
        from jubran.application.order_amendment_service import GUEST_CANCEL_NOTE, lock_reason

        stmt = (
            select(OrderModel)
            .where(OrderModel.table_session_id == table_session_id)
            .options(selectinload(OrderModel.items), selectinload(OrderModel.amendments))
            .order_by(OrderModel.created_at.desc())
        )
        orders = (await db.execute(stmt)).scalars().all()

        result = []
        for o in orders:
            mine = customer_session_id is not None and o.customer_session_id == customer_session_id
            locked = lock_reason(o)
            items_list = [
                {
                    "item_id": it.id,
                    "name_ar": it.product_name_snapshot_ar,
                    "name_en": it.product_name_snapshot_en or it.product_name_snapshot_ar,
                    "quantity": it.quantity,
                    "unit_price_display": format_jod(it.unit_price_minor_snapshot, "ar"),
                    "unit_price_display_en": format_jod(it.unit_price_minor_snapshot, "en"),
                    "line_total_display": format_jod(it.line_total_minor, "ar"),
                    "line_total_display_en": format_jod(it.line_total_minor, "en"),
                    "note": it.note,
                    "added_later": it.added_at is not None,
                }
                for it in sorted(o.items, key=lambda row: (row.added_at is not None, row.added_at or o.created_at, row.id))
            ]
            total_minor = sum(it.line_total_minor for it in o.items)
            result.append({
                "order_id": o.id,
                "order_number": o.order_number,
                "version": o.version,
                "mine": mine,
                "editable": mine and locked is None,
                "locked_reason": locked,
                "amended": bool(o.amendments),
                "cancelled_by_guest": o.status == OrderStatus.CANCELLED and o.closure_note == GUEST_CANCEL_NOTE,
                "status": o.status.value,
                "status_display_ar": CUSTOMER_STATUS_LABELS.get(o.status, ("", ""))[0] or o.status.value,
                "status_display_en": CUSTOMER_STATUS_LABELS.get(o.status, ("", ""))[1] or o.status.value,
                "is_served": o.served_at is not None,
                "total_minor": total_minor,
                "total_display_ar": format_jod(total_minor, "ar"),
                "total_display_en": format_jod(total_minor, "en"),
                "items": items_list,
                "created_at": o.created_at.isoformat(),
                # Guests only learn that the restaurant ended the visit; the staff
                # closure note (with other guests' complaints) stays in the admin panel.
                "cancelled_with_visit": (o.status == OrderStatus.CANCELLED and o.closure_note is not None
                                         and o.closure_note != GUEST_CANCEL_NOTE),
            })
        return result

    @staticmethod
    async def update_order_status(db: AsyncSession, order_id: str, new_status: OrderStatus) -> OrderModel:
        """Only approval starts preparation; delivery is recorded atomically by FloorService."""
        # Locked, so a guest's change to the same order is applied before or after, never in between.
        order = (await db.execute(select(OrderModel).where(OrderModel.id == order_id).with_for_update()
                                  .execution_options(populate_existing=True))).scalar_one_or_none()
        if not order:
            raise EntityNotFoundException("Order", order_id)

        # Kitchen workflow: approve -> preparing -> ready. Serving is a separate
        # table marker (FloorService.mark_order_served), not a status.
        valid_transitions = {
            OrderStatus.PENDING_APPROVAL: [OrderStatus.PREPARING],
            OrderStatus.PREPARING: [OrderStatus.READY],
            OrderStatus.READY: [],
            OrderStatus.CLOSED: [],
            OrderStatus.CANCELLED: [],
        }

        if new_status not in valid_transitions.get(order.status, []):
            raise InvalidStateTransitionException(order.status.value, new_status.value)

        order.status = new_status
        order.version += 1
        order.updated_at = datetime.now(timezone.utc)

        # Record Outbox Event
        outbox_event = OutboxEventModel(
            event_type="order.status_changed",
            aggregate_type="order",
            aggregate_id=order.id,
            payload_json=json.dumps({
                "order_id": order.id,
                "order_number": order.order_number,
                "status": new_status.value,
                "table_session_id": order.table_session_id
            })
        )
        db.add(outbox_event)
        await db.commit()
        return order
