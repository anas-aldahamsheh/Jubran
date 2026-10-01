"""The floor screen's data stays small no matter how long the restaurant has been running."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from jubran.application.floor_service import FloorService
from jubran.domain.enums import ComplaintStatus, OrderStatus, ServiceRequestStatus, ServiceRequestType, TableSessionStatus
from jubran.infrastructure.db.models import (
    ComplaintModel, CustomerSessionModel, OrderItemModel, OrderModel, PhysicalTableModel,
    ServiceRequestModel, TableSessionModel,
)
from jubran.infrastructure.db.seed import seed_database


async def old_visit(db, table, when, *, served=True, open_complaint=False, unserved=False, number=[1000]):
    visit = TableSessionModel(physical_table_id=table.id, status=TableSessionStatus.CLOSED, started_at=when, closed_at=when)
    db.add(visit)
    await db.flush()
    guest = CustomerSessionModel(table_session_id=visit.id, token_hash=f"h{number[0]}", started_at=when,
                                 expires_at=when + timedelta(hours=3), last_seen_at=when)
    db.add(guest)
    await db.flush()
    number[0] += 1
    order = OrderModel(table_session_id=visit.id, customer_session_id=guest.id, order_number=f"JB-{number[0]}",
                       status=OrderStatus.READY, served_at=None if unserved else when, created_at=when, updated_at=when)
    db.add(order)
    await db.flush()
    db.add(OrderItemModel(order_id=order.id, product_id=None, product_name_snapshot_ar="حمص", product_name_snapshot_en="Hummus",
                          unit_price_minor_snapshot=3450, quantity=2, line_total_minor=6900))
    db.add(ServiceRequestModel(table_session_id=visit.id, customer_session_id=guest.id, type=ServiceRequestType.BILL,
                               status=ServiceRequestStatus.RESOLVED, created_at=when, resolved_at=when))
    if open_complaint:
        db.add(ComplaintModel(table_session_id=visit.id, customer_session_id=guest.id, message="الأكل كان بارد",
                              status=ComplaintStatus.OPEN, created_at=when))
    await db.flush()
    return order


@pytest.mark.asyncio
async def test_old_history_is_left_out_but_open_items_stay(db_session):
    await seed_database(db_session)
    table = (await db_session.execute(select(PhysicalTableModel).where(PhysicalTableModel.table_number == "T1"))).scalar_one()
    empty_size = len(json.dumps(await FloorService.get_floor_snapshot(db_session), default=str))

    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    for day in range(40):  # a month and more of finished visits
        await old_visit(db_session, table, long_ago - timedelta(days=day))
    complaint_visit_order = await old_visit(db_session, table, long_ago, open_complaint=True)
    forgotten = await old_visit(db_session, table, long_ago, unserved=True)
    yesterday = await old_visit(db_session, table, datetime.now(timezone.utc) - timedelta(hours=3))
    await db_session.commit()

    snapshot = await FloorService.get_floor_snapshot(db_session)
    t1 = next(t for t in snapshot["tables"] if t["table_number"] == "T1")
    assert t1["base_state"] == "VACANT" and t1["orders"] == [] and t1["overlays"]["service_requests"] == []

    # Still on screen: work that isn't finished, plus the last day's history.
    assert [o["order_id"] for o in snapshot["active_orders"]] == [forgotten.id]
    assert [o["order_id"] for o in snapshot["completed_orders"]] == [yesterday.id]
    open_items = [item for item in snapshot["service_queue"] if item["status"] == "OPEN"]
    assert [item["kind"] for item in open_items] == ["COMPLAINT"]
    assert complaint_visit_order.id not in json.dumps(snapshot)

    # 40+ old visits add almost nothing to what the screen downloads.
    size = len(json.dumps(snapshot, default=str))
    assert size - empty_size < 4000, (empty_size, size)
