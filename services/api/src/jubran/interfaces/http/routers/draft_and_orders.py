"""Draft and Orders HTTP Router."""
from typing import Tuple, Optional
from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import CustomerSessionModel, TableSessionModel, PhysicalTableModel
from jubran.application.ordering_service import OrderingService
from jubran.application.order_amendment_service import AmendmentOperationError, OrderAmendmentService
from jubran.domain.exceptions import BusinessRuleError
from jubran.interfaces.http.dependencies import get_required_customer_context
from jubran.interfaces.http.rate_limits import per_guest
from jubran.interfaces.http.schemas import AddItemRequest, AmendOrderRequest, UpdateItemRequest, SubmitOrderRequest

router = APIRouter(prefix="/api/v1", tags=["Draft & Orders"])


@router.get("/draft")
async def get_draft(
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await OrderingService.get_draft_summary(db, cust_session.id, table_session.id)


@router.post("/draft/items")
async def add_draft_item(
    req: AddItemRequest,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await OrderingService.add_item_to_draft(
        db=db,
        customer_session_id=cust_session.id,
        table_session_id=table_session.id,
        product_id=req.product_id,
        quantity=req.quantity,
        note=req.note
    )


@router.patch("/draft/items/{line_id}")
async def update_draft_item(
    line_id: str,
    req: UpdateItemRequest,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await OrderingService.update_item_in_draft(
        db=db,
        customer_session_id=cust_session.id,
        table_session_id=table_session.id,
        line_id=line_id,
        quantity=req.quantity,
        note=req.note
    )


@router.delete("/draft/items/{line_id}")
async def remove_draft_item(
    line_id: str,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await OrderingService.remove_item_from_draft(
        db=db,
        customer_session_id=cust_session.id,
        table_session_id=table_session.id,
        line_id=line_id
    )


@router.post("/draft/prepare-confirmation")
async def prepare_confirmation(
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await OrderingService.prepare_confirmation(
        db=db,
        customer_session_id=cust_session.id,
        table_session_id=table_session.id
    )


@router.post("/orders", dependencies=[per_guest("order_submit")])
async def submit_order(
    req: SubmitOrderRequest,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await OrderingService.submit_order(
        db=db,
        customer_session_id=cust_session.id,
        table_session_id=table_session.id,
        confirmation_token=req.confirmation_token,
        draft_version=req.draft_version,
        idempotency_key=idempotency_key
    )


@router.get("/orders")
async def get_orders(
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await OrderingService.get_orders_for_customer(db, table_session.id, cust_session.id)


AMENDMENT_ERRORS = {
    "PRODUCT_UNAVAILABLE": "هاد الصنف مش متوفر حالياً، فما بنقدر نزيد منه.",
    "QUANTITY_LIMIT": "الحد الأقصى 50 من الصنف الواحد.",
    "ORDER_ITEM_NOT_FOUND": "هاد الصنف مش موجود بالطلب.",
    "NO_CHANGE": "ما في أي تغيير على الطلب.",
}


@router.post("/orders/{order_id}/amend", dependencies=[per_guest("order_amend")])
async def amend_order(
    order_id: str,
    req: AmendOrderRequest,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    """The guest changes an order they already sent (until it is ready or served)."""
    cust_session, table_session, _ = ctx
    try:
        return await OrderAmendmentService.apply(
            db, cust_session.id, table_session.id, order_id,
            [change.model_dump(exclude_none=True) for change in req.operations], req.expected_version)
    except AmendmentOperationError as exc:
        raise BusinessRuleError(AMENDMENT_ERRORS.get(exc.code, "ما قدرنا نطبّق هاد التعديل."), exc.code, 422,
                                {"operation_index": exc.index}) from exc
