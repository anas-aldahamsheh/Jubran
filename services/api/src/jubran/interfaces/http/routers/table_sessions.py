"""Table Session and QR Resolution Router."""
from typing import Optional, Tuple
from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import CustomerSessionModel, TableSessionModel, PhysicalTableModel, BranchModel, RestaurantModel, UserModel
from jubran.application.session_service import SessionService
from jubran.interfaces.http.cookies import set_http_only_cookie
from jubran.interfaces.http.rate_limits import per_address
from jubran.interfaces.http.dependencies import get_customer_session_token, get_required_customer_context
from jubran.interfaces.http.schemas import StartSessionRequest, StartSessionResponse, SessionContextResponse
from jubran.settings import settings

router = APIRouter(prefix="/api/v1", tags=["Table Sessions"])


@router.post("/table-sessions/start", response_model=StartSessionResponse,
             dependencies=[per_address("session_start_ip")])
async def start_session(
    req: StartSessionRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    existing_token: Optional[str] = Depends(get_customer_session_token)
):
    raw_cust_token, cust_session, table = await SessionService.start_or_resume_session(
        db=db,
        qr_token=req.qr_token,
        existing_session_token=existing_token
    )

    # The visit token lives only in this HttpOnly cookie (never in the response body).
    set_http_only_cookie(response, settings.SESSION_COOKIE_NAME, raw_cust_token, settings.SESSION_MAX_AGE_SECONDS)

    # Load branch and restaurant names for bootstrap
    branch = (await db.execute(select(BranchModel).where(BranchModel.id == table.branch_id))).scalar_one()
    restaurant = (await db.execute(select(RestaurantModel).where(RestaurantModel.id == branch.restaurant_id))).scalar_one()

    return StartSessionResponse(
        customer_id=cust_session.id,
        table_id=table.id,
        table_number=table.table_number,
        branch_name_ar=branch.name_ar,
        branch_name_en=branch.name_en,
        restaurant_name_ar=restaurant.name_ar,
        restaurant_name_en=restaurant.name_en
    )


@router.get("/session/context", response_model=SessionContextResponse)
async def get_session_context(
    ctx: Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel] = Depends(get_required_customer_context),
    db: AsyncSession = Depends(get_db_session)
):
    cust_session, table_session, table = ctx
    branch = (await db.execute(select(BranchModel).where(BranchModel.id == table.branch_id))).scalar_one()

    user_email = None
    if cust_session.authenticated_user_id:
        user = (await db.execute(select(UserModel).where(UserModel.id == cust_session.authenticated_user_id))).scalar_one_or_none()
        if user:
            user_email = user.email

    return SessionContextResponse(
        customer_session_id=cust_session.id,
        customer_id=cust_session.id,
        table_session_id=table_session.id,
        table_id=table.id,
        table_number=table.table_number,
        branch_name_ar=branch.name_ar,
        branch_name_en=branch.name_en,
        is_authenticated=cust_session.authenticated_user_id is not None,
        user_email=user_email
    )
