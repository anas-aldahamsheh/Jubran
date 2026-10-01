"""Changing an order the guest already sent, the way a waiter would.

- Waiting for the restaurant (PENDING_APPROVAL): the change simply applies.
- Being prepared (PREPARING): it applies too, and staff see the order highlighted
  as changed (with exactly what changed) until someone acknowledges it.
- Ready, served, cancelled or closed: the order is locked. More dishes become a
  new order from the same guest.

Only the guest who sent an order can change it. Every change is checked and
applied under a row lock, all-or-nothing, and recorded as an amendment.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jubran.application.events import record_event
from jubran.application.ordering_service import MAX_LINE_QUANTITY, sanitize_note
from jubran.domain.enums import OrderStatus, TableSessionStatus
from jubran.domain.exceptions import BusinessRuleError, TableSessionClosedException
from jubran.domain.money import format_jod
from jubran.infrastructure.db.models import (
    OrderAmendmentModel, OrderItemModel, OrderModel, ProductModel, TableSessionModel,
)

GUEST_CANCEL_NOTE = "ألغاه الزبون قبل التسليم."

LOCK_MESSAGES = {
    "READY": ("الطلب جاهز، فما بنقدر نعدّل عليه. أي أصناف إضافية بتنبعت كطلب جديد.",
              "This order is ready, so it can't be changed. Anything extra goes in a new order."),
    "SERVED": ("الطلب تسلّم، فما بنقدر نعدّل عليه. أي أصناف إضافية بتنبعت كطلب جديد.",
               "This order was already served, so it can't be changed. Anything extra goes in a new order."),
    "CANCELLED": ("هاد الطلب ملغي.", "This order was cancelled."),
    "CLOSED": ("هاد الطلب مغلق.", "This order is closed."),
}


class AmendmentOperationError(Exception):
    """One change in the batch was refused; nothing was saved."""

    def __init__(self, code: str, index: int):
        super().__init__(code)
        self.code = code
        self.index = index


def lock_reason(order: OrderModel) -> Optional[str]:
    """Why an order can no longer be changed, or None while it still can."""
    if order.status == OrderStatus.CANCELLED:
        return "CANCELLED"
    if order.status == OrderStatus.CLOSED:
        return "CLOSED"
    if order.served_at is not None:
        return "SERVED"
    if order.status == OrderStatus.READY:
        return "READY"
    return None


def order_locked(reason: str) -> BusinessRuleError:
    message_ar, message_en = LOCK_MESSAGES.get(reason, LOCK_MESSAGES["CLOSED"])
    return BusinessRuleError(message_ar, "ORDER_LOCKED", 409, {"reason": reason, "message_en": message_en})


def _line(item: OrderItemModel) -> Dict[str, Any]:
    return {"item_id": item.id, "product_id": item.product_id, "name_ar": item.product_name_snapshot_ar,
            "name_en": item.product_name_snapshot_en, "unit_price_minor": item.unit_price_minor_snapshot,
            "quantity": item.quantity, "note": item.note}


def describe_changes(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What changed, line by line: added dishes, removed dishes and new quantities."""
    old = {line["item_id"]: line for line in before}
    changes: List[Dict[str, Any]] = []
    for line in after:
        previous = old.pop(line["item_id"], None) if line.get("item_id") else None
        base = {"name_ar": line["name_ar"], "name_en": line["name_en"], "note": line.get("note"),
                "unit_price_minor": line["unit_price_minor"]}
        if previous is None:
            changes.append({"type": "added", "quantity_before": 0, "quantity_after": line["quantity"], **base})
        elif previous["quantity"] != line["quantity"]:
            changes.append({"type": "quantity_changed", "quantity_before": previous["quantity"],
                            "quantity_after": line["quantity"], **base})
    for line in old.values():
        changes.append({"type": "removed", "quantity_before": line["quantity"], "quantity_after": 0,
                        "name_ar": line["name_ar"], "name_en": line["name_en"], "note": line.get("note"),
                        "unit_price_minor": line["unit_price_minor"]})
    return changes


def _total(lines: List[Dict[str, Any]]) -> int:
    return sum(line["unit_price_minor"] * line["quantity"] for line in lines)


def _money(fils: int) -> Dict[str, str]:
    return {"ar": format_jod(fils, "ar"), "en": format_jod(fils, "en")}


class OrderAmendmentService:
    @staticmethod
    async def _load(db: AsyncSession, order_id: str, customer_session_id: str, table_session_id: str,
                    lock: bool) -> OrderModel:
        visit_status = (await db.execute(select(TableSessionModel.status).where(
            TableSessionModel.id == table_session_id))).scalar_one_or_none()
        if visit_status != TableSessionStatus.ACTIVE:
            raise TableSessionClosedException()
        stmt = (select(OrderModel)
                .where(OrderModel.id == order_id, OrderModel.table_session_id == table_session_id)
                .options(selectinload(OrderModel.items))
                .execution_options(populate_existing=True))
        if lock:
            stmt = stmt.with_for_update()
        order = (await db.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise BusinessRuleError("ما لقيت هاد الطلب على طاولتك.", "ORDER_NOT_FOUND", 404)
        if order.customer_session_id != customer_session_id:
            raise BusinessRuleError("هاد الطلب لضيف ثاني على الطاولة، وبس صاحبه بقدر يعدّل عليه.",
                                    "ORDER_NOT_YOURS", 403)
        return order

    @staticmethod
    async def _plan(db: AsyncSession, order: OrderModel, operations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The order's lines after the changes (checked, nothing written). Raises AmendmentOperationError."""
        if not operations:
            raise AmendmentOperationError("EMPTY_OPERATIONS", 0)

        def valid_quantity(value: Any, minimum: int) -> bool:
            return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= MAX_LINE_QUANTITY

        products: Dict[str, Optional[ProductModel]] = {}

        async def product(product_id: Optional[str]) -> Optional[ProductModel]:
            if not product_id:
                return None
            if product_id not in products:
                products[product_id] = (await db.execute(
                    select(ProductModel).where(ProductModel.id == product_id))).scalar_one_or_none()
            return products[product_id]

        lines = [_line(item) for item in order.items]
        by_id = {line["item_id"]: line for line in lines}
        for index, operation in enumerate(operations):
            kind = operation.get("op")
            if kind == "add":
                quantity = operation.get("quantity", 1)
                dish = await product(operation.get("product_id"))
                if dish is None or not valid_quantity(quantity, 1):
                    raise AmendmentOperationError("INVALID_ADD", index)
                if not dish.is_available:
                    raise AmendmentOperationError("PRODUCT_UNAVAILABLE", index)
                note = sanitize_note(operation.get("note"))
                same = next((line for line in lines if line["product_id"] == dish.id and line["note"] == note
                             and line["unit_price_minor"] == dish.price_minor), None)
                if same is not None:
                    if same["quantity"] + quantity > MAX_LINE_QUANTITY:
                        raise AmendmentOperationError("QUANTITY_LIMIT", index)
                    same["quantity"] += quantity
                else:
                    lines.append({"item_id": None, "product_id": dish.id, "name_ar": dish.name_ar,
                                  "name_en": dish.name_en, "unit_price_minor": dish.price_minor,
                                  "quantity": quantity, "note": note})
            elif kind in ("set_quantity", "remove"):
                line = by_id.get(operation.get("item_id") or "")
                if line is None or line not in lines:
                    raise AmendmentOperationError("ORDER_ITEM_NOT_FOUND", index)
                quantity = 0 if kind == "remove" else operation.get("quantity")
                if not valid_quantity(quantity, 0):
                    raise AmendmentOperationError("INVALID_QUANTITY", index)
                if quantity == 0:
                    lines.remove(line)
                elif quantity <= line["quantity"]:
                    line["quantity"] = quantity
                else:
                    # More of a dish is a new request: it must still be available, at today's price.
                    dish = await product(line["product_id"])
                    if dish is None or not dish.is_available:
                        raise AmendmentOperationError("PRODUCT_UNAVAILABLE", index)
                    extra = quantity - line["quantity"]
                    if dish.price_minor == line["unit_price_minor"]:
                        line["quantity"] = quantity
                    else:
                        lines.append({"item_id": None, "product_id": dish.id, "name_ar": dish.name_ar,
                                      "name_en": dish.name_en, "unit_price_minor": dish.price_minor,
                                      "quantity": extra, "note": line["note"]})
            else:
                raise AmendmentOperationError("INVALID_OPERATION", index)
        return lines

    @classmethod
    def _summary(cls, order: OrderModel, before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> Dict[str, Any]:
        changes = describe_changes(before, after)
        total_before, total_after = _total(before), _total(after)
        return {
            "order_id": order.id,
            "order_number": order.order_number,
            "order_status": order.status.value,
            "order_version": order.version,
            "changes": changes,
            "items_after": [{key: line[key] for key in ("name_ar", "name_en", "quantity", "note")} | {
                "line_total_display_ar": format_jod(line["unit_price_minor"] * line["quantity"], "ar"),
                "line_total_display_en": format_jod(line["unit_price_minor"] * line["quantity"], "en"),
            } for line in after],
            "total_before_minor": total_before,
            "total_after_minor": total_after,
            "total_before_display_ar": _money(total_before)["ar"],
            "total_before_display_en": _money(total_before)["en"],
            "total_after_display_ar": _money(total_after)["ar"],
            "total_after_display_en": _money(total_after)["en"],
            "cancels_order": not after,
            "kitchen_already_preparing": order.status == OrderStatus.PREPARING,
        }

    @classmethod
    async def preview(cls, db: AsyncSession, customer_session_id: str, table_session_id: str,
                      order_id: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """What the order would look like after the changes; nothing is saved."""
        order = await cls._load(db, order_id, customer_session_id, table_session_id, lock=False)
        reason = lock_reason(order)
        if reason:
            raise order_locked(reason)
        before = [_line(item) for item in order.items]
        after = await cls._plan(db, order, operations)
        summary = cls._summary(order, before, after)
        if not summary["changes"]:
            raise AmendmentOperationError("NO_CHANGE", 0)
        return summary

    @classmethod
    async def apply(cls, db: AsyncSession, customer_session_id: str, table_session_id: str, order_id: str,
                    operations: List[Dict[str, Any]], expected_version: Optional[int] = None) -> Dict[str, Any]:
        """Apply the changes all-or-nothing under the order's row lock; returns the change summary."""
        order = await cls._load(db, order_id, customer_session_id, table_session_id, lock=True)
        reason = lock_reason(order)
        if reason:
            await db.rollback()
            raise order_locked(reason)
        if expected_version is not None and order.version != expected_version:
            current = order.version
            await db.rollback()
            raise BusinessRuleError("الطلب تغيّر من شوي. راجع الطلب مرة ثانية.", "ORDER_CHANGED", 409,
                                    {"current_version": current})
        before = [_line(item) for item in order.items]
        try:
            after = await cls._plan(db, order, operations)
        except AmendmentOperationError:
            await db.rollback()
            raise
        summary = cls._summary(order, before, after)
        if not summary["changes"]:
            await db.rollback()
            raise AmendmentOperationError("NO_CHANGE", 0)

        now = datetime.now(timezone.utc)
        status_at_change = order.status
        kept = {line["item_id"]: line for line in after if line.get("item_id")}
        for item in list(order.items):
            line = kept.get(item.id)
            if line is None:
                order.items.remove(item)
                await db.delete(item)
            elif line["quantity"] != item.quantity:
                item.quantity = line["quantity"]
                item.line_total_minor = item.unit_price_minor_snapshot * line["quantity"]
        for line in after:
            if not line.get("item_id"):
                order.items.append(OrderItemModel(
                    order_id=order.id, product_id=line["product_id"],
                    product_name_snapshot_ar=line["name_ar"], product_name_snapshot_en=line["name_en"],
                    unit_price_minor_snapshot=line["unit_price_minor"], quantity=line["quantity"],
                    note=line["note"], line_total_minor=line["unit_price_minor"] * line["quantity"],
                    added_at=now,
                ))
        if not after:
            order.status = OrderStatus.CANCELLED
            order.closure_note = GUEST_CANCEL_NOTE
        order.version += 1
        order.updated_at = now
        amendment = OrderAmendmentModel(
            order_id=order.id, customer_session_id=customer_session_id, order_status=status_at_change.value,
            changes_json=json.dumps(summary["changes"], ensure_ascii=False),
            total_before_minor=summary["total_before_minor"], total_after_minor=summary["total_after_minor"],
            created_at=now,
        )
        db.add(amendment)
        record_event(db, "order.amended", "order", order.id, {
            "order_id": order.id, "order_number": order.order_number, "table_session_id": table_session_id,
            "status": order.status.value, "order_status_at_change": status_at_change.value,
            "needs_attention": status_at_change == OrderStatus.PREPARING, "cancelled": not after,
        })
        await db.commit()
        summary.update({"order_status": order.status.value, "order_version": order.version,
                        "amendment_id": amendment.id, "applied": True})
        return summary

    @staticmethod
    async def acknowledge(db: AsyncSession, order_id: str, user_id: Optional[str]) -> int:
        """Staff saw the changes made while the order was being prepared; returns how many were cleared."""
        order = (await db.execute(select(OrderModel).where(OrderModel.id == order_id)
                                  .options(selectinload(OrderModel.amendments)))).scalar_one_or_none()
        if order is None:
            raise BusinessRuleError("الطلب غير موجود.", "ORDER_NOT_FOUND", 404)
        now = datetime.now(timezone.utc)
        cleared = 0
        for amendment in order.amendments:
            if amendment.acknowledged_at is None:
                amendment.acknowledged_at = now
                amendment.acknowledged_by_user_id = user_id
                cleared += 1
        if cleared:
            record_event(db, "order.amendment_acknowledged", "order", order.id, {
                "order_id": order.id, "order_number": order.order_number,
                "table_session_id": order.table_session_id})
        await db.commit()
        return cleared


def amendment_payload(amendment: OrderAmendmentModel) -> Dict[str, Any]:
    """How staff and guests see one recorded change."""
    try:
        changes = json.loads(amendment.changes_json or "[]")
    except ValueError:
        changes = []
    return {
        "id": amendment.id,
        "created_at": amendment.created_at.isoformat(),
        "order_status": amendment.order_status,
        "changes": changes,
        "total_before_display": format_jod(amendment.total_before_minor, "ar"),
        "total_before_display_en": format_jod(amendment.total_before_minor, "en"),
        "total_after_display": format_jod(amendment.total_after_minor, "ar"),
        "total_after_display_en": format_jod(amendment.total_after_minor, "en"),
        "acknowledged": amendment.acknowledged_at is not None,
        "needs_attention": amendment.order_status == OrderStatus.PREPARING.value and amendment.acknowledged_at is None,
    }
