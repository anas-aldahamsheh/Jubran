"""Admin Live Floor and Order Operations HTTP Router."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import UserModel
from jubran.application.floor_service import FloorService
from jubran.application.ordering_service import OrderingService
from jubran.application.order_amendment_service import OrderAmendmentService
from jubran.interfaces.http.dependencies import require_admin
from jubran.domain.enums import OrderStatus

router = APIRouter(prefix="/api/v1/admin", tags=["Admin Floor & Operations"])


@router.get("/floor")
async def get_floor_snapshot(
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Retrieve authoritative 2.5D restaurant floor snapshot."""
    return await FloorService.get_floor_snapshot(db)


@router.get("/customer-sessions/{customer_id}/history")
async def get_customer_history(
    customer_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Return the complete audit trail for one anonymous customer visit ID."""
    return await FloorService.get_customer_history(db, customer_id)


@router.post("/orders/{order_id}/start-preparing")
async def start_preparing_order(
    order_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Move order from PENDING_APPROVAL to PREPARING."""
    order = await OrderingService.update_order_status(db, order_id, OrderStatus.PREPARING)
    return {"order_id": order.id, "status": order.status.value}


@router.post("/orders/{order_id}/mark-ready")
async def mark_order_ready(
    order_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Kitchen finished: PREPARING -> READY (the guest now sees "ready")."""
    order = await OrderingService.update_order_status(db, order_id, OrderStatus.READY)
    return {"order_id": order.id, "status": order.status.value}


@router.post("/orders/{order_id}/mark-served")
async def mark_order_served(
    order_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Operational marker after delivering a READY order; the guest still sees READY."""
    order = await FloorService.mark_order_served(db, order_id)
    return {"order_id": order.id, "status": order.status.value, "served": True}


@router.post("/orders/{order_id}/acknowledge-changes")
async def acknowledge_order_changes(
    order_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Staff saw what the guest changed while the order was being prepared."""
    cleared = await OrderAmendmentService.acknowledge(db, order_id, admin.id)
    return {"order_id": order_id, "acknowledged": cleared}


class CloseTableRequest(BaseModel):
    # Only needed when orders are cooking or ready but not served yet.
    kitchen_orders: Optional[Literal["delivered", "cancel"]] = None


@router.post("/table-sessions/{table_session_id}/close")
async def close_table_session(
    table_session_id: str,
    body: Optional[CloseTableRequest] = None,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Controlled close of active table session."""
    session = await FloorService.close_table_session(db, table_session_id, admin,
                                                     body.kitchen_orders if body else None)
    return {"table_session_id": session.id, "status": session.status.value, "closure_note": session.closure_note}
