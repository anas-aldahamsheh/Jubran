"""Customer Service HTTP Router for Service Requests, Complaints, and Feedback."""
from typing import Tuple, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import CustomerSessionModel, TableSessionModel, PhysicalTableModel, UserModel
from jubran.application.restaurant_service import RestaurantService
from jubran.application.service_request_service import CustomerServiceManager
from jubran.interfaces.http.dependencies import get_required_customer_context, require_admin
from jubran.interfaces.http.rate_limits import per_guest
from jubran.domain.enums import ServiceRequestType

router = APIRouter(prefix="/api/v1", tags=["Service Requests & Feedback"])


class ServiceRequestInput(BaseModel):
    type: ServiceRequestType


class ComplaintInput(BaseModel):
    message: str = Field(min_length=3, max_length=500)
    category: Optional[str] = Field(default=None, max_length=50)


class FeedbackInput(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=500)


@router.get("/service-requests")
async def get_service_requests(
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    customer_session, table_session, _ = ctx
    return await CustomerServiceManager.get_service_requests_for_customer(
        db, table_session.id, customer_session.id
    )


@router.post("/service-requests", dependencies=[per_guest("customer_action")])
async def create_service_request(
    req: ServiceRequestInput,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await CustomerServiceManager.create_service_request(
        db=db,
        table_session_id=table_session.id,
        customer_session_id=cust_session.id,
        request_type=req.type
    )


@router.post("/service-requests/{request_id}/cancel", dependencies=[per_guest("customer_action")])
async def cancel_service_request(
    request_id: str,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session),
):
    customer_session, table_session, _ = ctx
    req = await CustomerServiceManager.cancel_service_request(
        db, request_id, table_session.id, customer_session.id
    )
    return {"request_id": req.id, "status": req.status.value}


@router.post("/complaints", dependencies=[per_guest("customer_action")])
async def submit_complaint(
    req: ComplaintInput,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await CustomerServiceManager.submit_complaint(
        db=db,
        table_session_id=table_session.id,
        customer_session_id=cust_session.id,
        message=req.message,
        category=req.category
    )


@router.post("/feedback", dependencies=[per_guest("customer_action")])
async def submit_feedback(
    req: FeedbackInput,
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, _ = ctx
    return await CustomerServiceManager.submit_feedback(
        db=db,
        table_session_id=table_session.id,
        customer_session_id=cust_session.id,
        rating=req.rating,
        comment=req.comment
    )


@router.post("/admin/service-requests/{request_id}/start")
async def start_service_request(
    request_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    req = await CustomerServiceManager.start_service_request(db, request_id)
    return {"request_id": req.id, "status": req.status.value}


@router.post("/admin/service-requests/{request_id}/resolve")
async def resolve_service_request(
    request_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    req = await CustomerServiceManager.resolve_service_request(db, request_id, admin)
    return {"request_id": req.id, "status": req.status.value}


@router.post("/admin/complaints/{complaint_id}/start")
async def start_complaint(
    complaint_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    complaint = await CustomerServiceManager.start_complaint(db, complaint_id)
    return {"complaint_id": complaint.id, "status": complaint.status.value}


@router.post("/admin/complaints/{complaint_id}/resolve")
async def resolve_complaint(
    complaint_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    c = await CustomerServiceManager.resolve_complaint(db, complaint_id)
    return {"complaint_id": c.id, "status": c.status.value}


@router.get("/restaurant")
async def get_public_restaurant_profile(db: AsyncSession = Depends(get_db_session)):
    """Public restaurant profile and branch information."""
    return await RestaurantService.get_restaurant_info(db)
