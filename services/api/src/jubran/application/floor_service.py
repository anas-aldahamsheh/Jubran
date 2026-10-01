"""Floor Management and State Projection Service."""
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload
from jubran.infrastructure.db.models import (
    ComplaintModel, CustomerSessionModel, DraftOrderModel, OrderModel, OutboxEventModel, PhysicalTableModel,
    ServiceRequestModel, TableSessionModel, UserModel,
)
from jubran.domain.enums import (
    TableBaseState, TableSessionStatus, OrderStatus,
    ServiceRequestStatus, ServiceRequestType, ComplaintStatus, DraftStatus
)
from jubran.domain.exceptions import (
    BusinessRuleError, EntityNotFoundException, InvalidStateTransitionException
)
from jubran.domain.money import format_jod
from jubran.application.ai.conversation_store import ConversationStore
from jubran.application.order_amendment_service import GUEST_CANCEL_NOTE, amendment_payload
from jubran.application.events import record_event
from jubran.settings import settings

# The floor screen shows handled orders/requests from this recent window only.
RECENT_HISTORY = timedelta(hours=24)
RECENT_ORDERS_LIMIT = 100
RECENT_QUEUE_LIMIT = 200

# Closing a table with orders that are cooking or ready: what happened to them?
KITCHEN_DELIVERED = "delivered"
KITCHEN_CANCEL = "cancel"


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    # SQLite hands back naive datetimes; they are stored in UTC.
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _last_activity(visit: TableSessionModel) -> datetime:
    """The most recent sign of life at this visit (guest requests, orders, services, drafts)."""
    moments = [visit.started_at]
    for guest in visit.customer_sessions:
        moments += [guest.last_seen_at] + [draft.updated_at for draft in guest.draft_orders]
    moments += [moment for order in visit.orders for moment in (order.created_at, order.updated_at, order.served_at)]
    moments += [moment for request in visit.service_requests for moment in (request.created_at, request.resolved_at)]
    moments += [complaint.created_at for complaint in visit.complaints]
    return max(_aware(moment) for moment in moments if moment is not None)


def _in_progress(visit: TableSessionModel) -> bool:
    """An order the kitchen or staff have not finished with."""
    return any(order.served_at is None and order.status not in (OrderStatus.CLOSED, OrderStatus.CANCELLED)
               for order in visit.orders)


def _in_order_of_adding(order: OrderModel) -> list:
    """The dishes sent with the order first, then those the guest added later."""
    return sorted(order.items, key=lambda item: (item.added_at is not None, _aware(item.added_at) or order.created_at, item.id))


class FloorService:
    @staticmethod
    async def get_floor_snapshot(db: AsyncSession, branch_id: Optional[str] = None) -> Dict[str, Any]:
        """What staff need on the floor right now; size does not grow with the restaurant's history.

        - Tables with only their *active* visits (orders, services, complaints).
        - Still-actionable items from any visit (unserved orders, open requests and complaints).
        - A short recent history: completed orders and handled requests of the last day.
        Older history is available per guest through the customer-history lookup.
        """
        now = datetime.now(timezone.utc)
        recent = now - RECENT_HISTORY
        active_only = TableSessionModel.status == TableSessionStatus.ACTIVE
        stmt = (
            select(PhysicalTableModel)
            .order_by(PhysicalTableModel.table_number)
            .options(
                selectinload(PhysicalTableModel.sessions.and_(active_only))
                .selectinload(TableSessionModel.orders).selectinload(OrderModel.items),
                selectinload(PhysicalTableModel.sessions.and_(active_only))
                .selectinload(TableSessionModel.orders).selectinload(OrderModel.amendments),
                selectinload(PhysicalTableModel.sessions.and_(active_only))
                .selectinload(TableSessionModel.service_requests),
                selectinload(PhysicalTableModel.sessions.and_(active_only))
                .selectinload(TableSessionModel.complaints),
            )
        )
        if branch_id:
            stmt = stmt.where(PhysicalTableModel.branch_id == branch_id)
        tables = (await db.execute(stmt)).scalars().all()
        table_by_id = {table.id: table for table in tables}

        def summarize_order(order: OrderModel, table: PhysicalTableModel) -> Dict[str, Any]:
            total_minor = sum(item.line_total_minor for item in order.items)
            amendments = [amendment_payload(amendment) for amendment in order.amendments]
            return {
                "order_id": order.id,
                "order_number": order.order_number,
                "table_id": table.id,
                "table_number": table.table_number,
                "table_session_id": order.table_session_id,
                "customer_id": order.customer_session_id,
                "status": order.status.value,
                "is_served": order.served_at is not None,
                "total_minor": total_minor,
                "total_display": format_jod(total_minor, "ar"),
                "total_display_en": format_jod(total_minor, "en"),
                # Changes the guest made after sending; one made while cooking needs a staff "seen".
                "amendments": amendments,
                "needs_attention": any(item["needs_attention"] for item in amendments),
                "cancelled_by_guest": order.status == OrderStatus.CANCELLED and order.closure_note == GUEST_CANCEL_NOTE,
                "items": [
                    {
                        "item_id": item.id,
                        "added_later": item.added_at is not None,
                        "name_ar": item.product_name_snapshot_ar,
                        "name_en": item.product_name_snapshot_en or item.product_name_snapshot_ar,
                        "quantity": item.quantity,
                        "note": item.note,
                        "unit_price_display": format_jod(item.unit_price_minor_snapshot, "ar"),
                        "unit_price_display_en": format_jod(item.unit_price_minor_snapshot, "en"),
                        "line_total_display": format_jod(item.line_total_minor, "ar"),
                        "line_total_display_en": format_jod(item.line_total_minor, "en"),
                    }
                    for item in _in_order_of_adding(order)
                ],
                "created_at": order.created_at.isoformat(),
                "served_at": order.served_at.isoformat() if order.served_at else None,
                "closure_note": order.closure_note,
            }

        def queue_item(item, table: PhysicalTableModel, kind: str) -> Dict[str, Any]:
            entry = {
                "id": item.id,
                "kind": kind,
                "type": item.type.value if kind == "SERVICE" else "COMPLAINT",
                "status": item.status.value,
                "table_id": table.id,
                "table_number": table.table_number,
                "table_session_id": item.table_session_id,
                "customer_id": item.customer_session_id,
                "created_at": item.created_at.isoformat(),
                "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
                "closure_note": item.closure_note,
            }
            if kind == "COMPLAINT":
                entry["message"] = item.message
            return entry

        def is_done(order: OrderModel) -> bool:
            return order.served_at is not None or order.status in (OrderStatus.CLOSED, OrderStatus.CANCELLED)

        projected_tables, all_active_orders, completed_orders, service_queue = [], [], [], []
        seen_orders, seen_queue = set(), set()

        for table in tables:
            active_sessions = list(table.sessions)  # only ACTIVE visits were loaded
            active_session = max(active_sessions, key=lambda s: (s.started_at, s.id), default=None)
            services = sorted((r for s in active_sessions for r in s.service_requests),
                              key=lambda r: r.created_at, reverse=True)
            complaints = sorted((c for s in active_sessions for c in s.complaints),
                                key=lambda c: c.created_at, reverse=True)
            orders = [o for s in active_sessions for o in s.orders]

            base_state = TableBaseState.VACANT
            service_requested = bill_requested = has_complaint = False
            if active_session:
                open_services = [r for r in services if r.status in (ServiceRequestStatus.OPEN, ServiceRequestStatus.IN_PROGRESS)]
                service_requested = any(r.type in (ServiceRequestType.STAFF, ServiceRequestType.TISSUES, ServiceRequestType.CLEAN_TABLE) for r in open_services)
                bill_requested = any(r.type == ServiceRequestType.BILL for r in open_services)
                has_complaint = any(c.status in (ComplaintStatus.OPEN, ComplaintStatus.IN_PROGRESS) for c in complaints)
                # Spec rule: the most urgent state wins: pending > preparing > ready (unserved) > occupied.
                if any(o.status == OrderStatus.PENDING_APPROVAL for o in orders):
                    base_state = TableBaseState.ORDER_PENDING
                elif any(o.status == OrderStatus.PREPARING for o in orders):
                    base_state = TableBaseState.ORDER_PREPARING
                elif any(o.status == OrderStatus.READY and o.served_at is None for o in orders):
                    base_state = TableBaseState.ORDER_READY
                else:
                    base_state = TableBaseState.OCCUPIED_IDLE

            table_orders_payload = []
            for order in sorted(orders, key=lambda o: o.created_at, reverse=True):
                summary = summarize_order(order, table)
                table_orders_payload.append(summary)
                seen_orders.add(order.id)
                (completed_orders if is_done(order) else all_active_orders).append(summary)
            for request in services:
                seen_queue.add(request.id)
                service_queue.append(queue_item(request, table, "SERVICE"))
            for complaint in complaints:
                seen_queue.add(complaint.id)
                service_queue.append(queue_item(complaint, table, "COMPLAINT"))

            projected_tables.append({
                "table_id": table.id,
                "table_number": table.table_number,
                "shape": table.shape.value,
                "seat_count": table.seat_count,
                "x_percent": table.x_percent,
                "y_percent": table.y_percent,
                "rotation_deg": table.rotation_deg,
                "base_state": base_state.value,
                "active_session_id": active_session.id if active_session else None,
                "active_session_ids": [s.id for s in active_sessions],
                "overlays": {
                    "service_requested": service_requested,
                    "bill_requested": bill_requested,
                    "has_complaint": has_complaint,
                    "service_requests": [
                        {"id": r.id, "type": r.type.value, "status": r.status.value, "created_at": r.created_at.isoformat(), "table_session_id": r.table_session_id, "customer_id": r.customer_session_id, "closure_note": r.closure_note}
                        for r in services
                    ],
                    "complaints": [
                        {"id": c.id, "message": c.message, "status": c.status.value, "created_at": c.created_at.isoformat(), "table_session_id": c.table_session_id, "customer_id": c.customer_session_id, "closure_note": c.closure_note}
                        for c in complaints
                    ],
                },
                "orders": table_orders_payload,
            })

        # Items outside the active visits: still-actionable ones, and a short recent history.
        closed_visit_orders = (await db.execute(
            select(OrderModel)
            .join(TableSessionModel, TableSessionModel.id == OrderModel.table_session_id)
            .where(TableSessionModel.status != TableSessionStatus.ACTIVE,
                   or_(and_(OrderModel.served_at.is_(None),
                            OrderModel.status.not_in([OrderStatus.CLOSED, OrderStatus.CANCELLED])),
                       OrderModel.updated_at >= recent))
            .options(selectinload(OrderModel.items), selectinload(OrderModel.amendments),
                     selectinload(OrderModel.table_session))
            .order_by(OrderModel.updated_at.desc())
            .limit(RECENT_ORDERS_LIMIT)
        )).scalars().all()
        for order in closed_visit_orders:
            table = table_by_id.get(order.table_session.physical_table_id)
            if table is None or order.id in seen_orders:
                continue
            summary = summarize_order(order, table)
            # An unserved order remains actionable even if its visit was closed.
            (completed_orders if is_done(order) else all_active_orders).append(summary)

        for model, kind, open_states in ((ServiceRequestModel, "SERVICE", (ServiceRequestStatus.OPEN, ServiceRequestStatus.IN_PROGRESS)),
                                         (ComplaintModel, "COMPLAINT", (ComplaintStatus.OPEN, ComplaintStatus.IN_PROGRESS))):
            rows = (await db.execute(
                select(model)
                .join(TableSessionModel, TableSessionModel.id == model.table_session_id)
                .where(TableSessionModel.status != TableSessionStatus.ACTIVE,
                       or_(model.status.in_(open_states), model.created_at >= recent))
                .options(selectinload(model.table_session))
                .order_by(model.created_at.desc())
                .limit(RECENT_QUEUE_LIMIT)
            )).scalars().all()
            for item in rows:
                table = table_by_id.get(item.table_session.physical_table_id)
                if table is not None and item.id not in seen_queue:
                    service_queue.append(queue_item(item, table, kind))

        all_active_orders.sort(key=lambda x: x["created_at"])
        completed_orders.sort(key=lambda x: x["served_at"] or x["created_at"], reverse=True)
        service_queue.sort(key=lambda x: x["created_at"], reverse=True)
        active_services = [item for item in service_queue if item["status"] in ("OPEN", "IN_PROGRESS")]
        service_history = [item for item in service_queue if item["status"] not in ("OPEN", "IN_PROGRESS")][:RECENT_QUEUE_LIMIT]

        return {
            "tables": projected_tables,
            "active_orders": all_active_orders,
            "completed_orders": completed_orders[:RECENT_ORDERS_LIMIT],
            "service_queue": active_services + service_history,
            "timestamp": now.isoformat()
        }

    @staticmethod
    async def mark_order_served(db: AsyncSession, order_id: str) -> OrderModel:
        """Table marker after delivery: only for READY orders; the order stays READY for the guest."""
        order = (await db.execute(select(OrderModel).where(OrderModel.id == order_id).with_for_update()
                                  .execution_options(populate_existing=True))).scalar_one_or_none()
        if not order:
            raise EntityNotFoundException("Order", order_id)

        if order.status != OrderStatus.READY or order.served_at is not None:
            raise InvalidStateTransitionException(order.status.value, "SERVED")

        now = datetime.now(timezone.utc)
        order.served_at = now
        order.updated_at = now
        order.version += 1
        db.add(OutboxEventModel(
            event_type="order.status_changed",
            aggregate_type="order",
            aggregate_id=order.id,
            payload_json=json.dumps({
                "order_id": order.id,
                "order_number": order.order_number,
                "status": order.status.value,
                "is_served": True,
                "table_session_id": order.table_session_id,
            }),
        ))
        await db.commit()
        return order

    @staticmethod
    async def close_table_session(db: AsyncSession, table_session_id: str, admin_user: Optional[UserModel],
                                  kitchen_orders: Optional[str] = None,
                                  automatic_reason: Optional[str] = None,
                                  idle_since: Optional[datetime] = None) -> TableSessionModel:
        """Close every active visit for a table so duplicate sessions cannot leave it occupied.

        ``admin_user`` is None when the system closes an abandoned visit itself
        (``automatic_reason`` then explains why, in the closure note); with
        ``idle_since`` the table is closed only if it is still idle since then
        (checked under the table lock, so a guest acting at that moment wins).

        - Orders the kitchen has not started (pending) are cancelled.
        - Orders already cooking or ready but not yet served need an explicit
          decision (``kitchen_orders``): "delivered" or "cancel". Without it
          nothing is closed and KITCHEN_ORDERS_DECISION_REQUIRED is returned.
        - Open table services (tissues, bill, ...) are cancelled.
        - Complaints stay open: they are follow-ups for management, not table services.
        """
        session = (await db.execute(select(TableSessionModel).where(TableSessionModel.id == table_session_id))).scalar_one_or_none()
        if not session:
            raise EntityNotFoundException("TableSession", table_session_id)
        if kitchen_orders not in (None, KITCHEN_DELIVERED, KITCHEN_CANCEL):
            raise BusinessRuleError("قرار غير معروف للطلبات قيد التحضير.", "VALIDATION_ERROR", 422)

        table = (await db.execute(select(PhysicalTableModel).where(PhysicalTableModel.id == session.physical_table_id).with_for_update())).scalar_one()
        active_sessions = (await db.execute(
            select(TableSessionModel).where(
                TableSessionModel.physical_table_id == session.physical_table_id,
                TableSessionModel.status == TableSessionStatus.ACTIVE,
            ).options(
                selectinload(TableSessionModel.orders).selectinload(OrderModel.items),
                selectinload(TableSessionModel.service_requests),
                selectinload(TableSessionModel.complaints),
                selectinload(TableSessionModel.customer_sessions).selectinload(CustomerSessionModel.draft_orders),
            ).with_for_update()
        )).scalars().all()
        if idle_since is not None and any(_in_progress(visit) or _last_activity(visit) > idle_since
                                          for visit in active_sessions):
            raise BusinessRuleError("الطاولة مستخدمة حالياً.", "TABLE_IN_USE", 409)
        now = datetime.now(timezone.utc)
        unfinished = [o for s in active_sessions for o in s.orders if o.served_at is None and o.status not in (OrderStatus.CLOSED, OrderStatus.CANCELLED)]
        pending = [o for o in unfinished if o.status == OrderStatus.PENDING_APPROVAL]
        in_kitchen = [o for o in unfinished if o.status in (OrderStatus.PREPARING, OrderStatus.READY)]
        if in_kitchen and kitchen_orders is None:
            raise BusinessRuleError(
                "على الطاولة طلبات قيد التحضير أو جاهزة لم تُسلَّم بعد. حدّد هل سُلِّمت للزبون أم أُلغيت.",
                "KITCHEN_ORDERS_DECISION_REQUIRED", 409,
                {"orders": [{"order_id": o.id, "order_number": o.order_number, "status": o.status.value} for o in in_kitchen]},
            )
        cancelled_orders = pending + (in_kitchen if kitchen_orders == KITCHEN_CANCEL else [])
        delivered_orders = in_kitchen if kitchen_orders == KITCHEN_DELIVERED else []
        services = [r for s in active_sessions for r in s.service_requests if r.status in (ServiceRequestStatus.OPEN, ServiceRequestStatus.IN_PROGRESS)]
        complaints = [c for s in active_sessions for c in s.complaints if c.status in (ComplaintStatus.OPEN, ComplaintStatus.IN_PROGRESS)]
        drafts = [draft for s in active_sessions for customer in s.customer_sessions for draft in customer.draft_orders if draft.status == DraftStatus.OPEN]
        service_names = {ServiceRequestType.STAFF: "طلب موظف", ServiceRequestType.TISSUES: "مناديل إضافية", ServiceRequestType.CLEAN_TABLE: "تنظيف الطاولة", ServiceRequestType.BILL: "طلب الحساب"}

        def describe(orders):
            return "، ".join(f"{o.order_number} ({', '.join(f'{i.quantity}× {i.product_name_snapshot_ar}' for i in o.items)})" for o in orders)

        note_parts = []
        if cancelled_orders:
            note_parts.append("طلبات أُلغيت: " + describe(cancelled_orders))
        if delivered_orders:
            note_parts.append("طلبات سُجّلت مُسلَّمة: " + describe(delivered_orders))
        if services:
            note_parts.append("خدمات أُلغيت: " + "، ".join(service_names.get(r.type, "طلب خدمة") for r in services))
        if complaints:
            note_parts.append("شكاوى بقيت مفتوحة للمتابعة: " + "، ".join(c.message for c in complaints))
        if automatic_reason:
            note_parts.insert(0, automatic_reason)
        closure_note = (f"إنهاء جلسة طاولة {table.table_number}: " + "؛ ".join(note_parts)) if note_parts else None
        admin_id = admin_user.id if admin_user else None

        def order_event(order):
            db.add(OutboxEventModel(
                event_type="order.status_changed", aggregate_type="order", aggregate_id=order.id,
                payload_json=json.dumps({"order_id": order.id, "order_number": order.order_number, "status": order.status.value, "is_served": order.served_at is not None, "table_session_id": order.table_session_id, "customer_id": order.customer_session_id, "closure_note": order.closure_note}),
            ))

        for order in cancelled_orders:
            order.status = OrderStatus.CANCELLED
            order.closure_note = f"أُلغي الطلب بسبب إنهاء جلسة طاولة {table.table_number}."
            order.updated_at = now
            order.version += 1
            order_event(order)
        for order in delivered_orders:
            # The guest had the food: it counts as ready and served, never as cancelled.
            order.status = OrderStatus.READY
            order.served_at = now
            order.closure_note = f"سُجّل الطلب مُسلَّماً عند إنهاء جلسة طاولة {table.table_number}."
            order.updated_at = now
            order.version += 1
            order_event(order)
        for request in services:
            request.status = ServiceRequestStatus.CANCELLED
            request.resolved_at = now
            request.resolved_by_admin_id = admin_id
            request.closure_note = f"أُلغي طلب الخدمة بسبب إنهاء جلسة طاولة {table.table_number}."
        for draft in drafts:
            draft.status = DraftStatus.ABANDONED
            draft.updated_at = now
        # The assistant's memory of these guests ends with the visit.
        await ConversationStore.delete_for_visits(db, [s.id for s in active_sessions])
        for active_session in active_sessions:
            # Staff screens update, and guests still at the table learn their visit ended.
            record_event(db, "table_session.closed", "table_session", active_session.id,
                         {"table_session_id": active_session.id, "table_id": table.id,
                          "table_number": table.table_number})
            active_session.closure_note = closure_note
            active_session.status = TableSessionStatus.CLOSED
            active_session.closed_at = now
            active_session.closed_by_admin_id = admin_id
        await db.commit()
        return session

    @staticmethod
    async def get_customer_history(db: AsyncSession, customer_id: str) -> Dict[str, Any]:
        """Everything one guest did during their visit: orders, services, complaints, baskets."""
        customer = (await db.execute(
            select(CustomerSessionModel)
            .where(CustomerSessionModel.id == customer_id)
            .options(selectinload(CustomerSessionModel.table_session).selectinload(TableSessionModel.physical_table))
        )).scalar_one_or_none()
        if not customer:
            raise BusinessRuleError("معرّف العميل غير موجود.", "CUSTOMER_NOT_FOUND", 404)

        orders = (await db.execute(
            select(OrderModel).where(OrderModel.customer_session_id == customer.id)
            .options(selectinload(OrderModel.items), selectinload(OrderModel.amendments))
            .order_by(OrderModel.created_at.desc())
        )).scalars().all()
        services = (await db.execute(
            select(ServiceRequestModel).where(ServiceRequestModel.customer_session_id == customer.id)
            .order_by(ServiceRequestModel.created_at.desc())
        )).scalars().all()
        complaints = (await db.execute(
            select(ComplaintModel).where(ComplaintModel.customer_session_id == customer.id)
            .order_by(ComplaintModel.created_at.desc())
        )).scalars().all()
        drafts = (await db.execute(
            select(DraftOrderModel).where(DraftOrderModel.customer_session_id == customer.id)
            .order_by(DraftOrderModel.created_at.desc())
        )).scalars().all()
        visit = customer.table_session

        return {
            "customer_id": customer.id,
            "table_number": visit.physical_table.table_number,
            "table_session_id": visit.id,
            "session_status": visit.status.value,
            "session_started_at": visit.started_at.isoformat(),
            "session_closed_at": visit.closed_at.isoformat() if visit.closed_at else None,
            "orders": [{
                "order_id": order.id,
                "order_number": order.order_number,
                "status": order.status.value,
                "is_served": order.served_at is not None,
                "created_at": order.created_at.isoformat(),
                "total_display": format_jod(sum(item.line_total_minor for item in order.items), "ar"),
                "total_display_en": format_jod(sum(item.line_total_minor for item in order.items), "en"),
                "closure_note": order.closure_note,
                "amendments": [amendment_payload(amendment) for amendment in order.amendments],
                "items": [{"name_ar": item.product_name_snapshot_ar, "added_later": item.added_at is not None,
                           "name_en": item.product_name_snapshot_en or item.product_name_snapshot_ar,
                           "quantity": item.quantity} for item in order.items],
            } for order in orders],
            "services": [{"id": item.id, "type": item.type.value, "status": item.status.value, "created_at": item.created_at.isoformat(), "closure_note": item.closure_note} for item in services],
            "complaints": [{"id": item.id, "message": item.message, "status": item.status.value, "created_at": item.created_at.isoformat(), "closure_note": item.closure_note} for item in complaints],
            "drafts": [{"id": item.id, "status": item.status.value, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()} for item in drafts],
        }

    @staticmethod
    async def close_idle_table_sessions(db: AsyncSession, now: Optional[datetime] = None) -> list:
        """Close visits nobody closed: no activity for TABLE_SESSION_IDLE_HOURS and nothing in progress.

        Conservative on purpose: any guest page still open (it keeps the guest's
        session "seen"), any order or request in that time, or any order the
        kitchen has not finished keeps the table open for staff to decide.
        Returns the table numbers that were closed.
        """
        idle_hours = settings.TABLE_SESSION_IDLE_HOURS
        if idle_hours <= 0:
            return []
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=idle_hours)
        active = (await db.execute(
            select(TableSessionModel).where(TableSessionModel.status == TableSessionStatus.ACTIVE).options(
                selectinload(TableSessionModel.physical_table),
                selectinload(TableSessionModel.orders),
                selectinload(TableSessionModel.service_requests),
                selectinload(TableSessionModel.complaints),
                selectinload(TableSessionModel.customer_sessions).selectinload(CustomerSessionModel.draft_orders),
            )
        )).scalars().all()

        by_table: Dict[str, list] = {}
        for visit in active:
            by_table.setdefault(visit.physical_table_id, []).append(visit)
        idle_tables = [(visits[0].id, visits[0].physical_table.table_number) for visits in by_table.values()
                       if not any(_in_progress(visit) or _last_activity(visit) > cutoff for visit in visits)]
        await db.rollback()  # release the read before closing each table under its own lock

        closed = []
        reason = f"أُغلقت تلقائياً بعد {idle_hours:g} ساعات بدون أي نشاط"
        for visit_id, table_number in idle_tables:
            try:
                await FloorService.close_table_session(db, visit_id, None, automatic_reason=reason, idle_since=cutoff)
            except BusinessRuleError:
                await db.rollback()  # a guest came back meanwhile: leave the table open
                continue
            closed.append(table_number)
        return closed
