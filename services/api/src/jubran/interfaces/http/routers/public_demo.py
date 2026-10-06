"""Public demo entry: "Try as a guest" without a table's QR code (only when PUBLIC_DEMO is on)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.public_demo import seat_demo_guest
from jubran.infrastructure.db.models import BranchModel, RestaurantModel
from jubran.infrastructure.db.session import get_db_session
from jubran.interfaces.http.cookies import set_http_only_cookie
from jubran.interfaces.http.dependencies import get_customer_session_token
from jubran.interfaces.http.rate_limits import per_address
from jubran.interfaces.http.schemas import StartSessionResponse
from jubran.settings import settings

router = APIRouter(prefix="/api/v1/demo", tags=["Public demo"])


def demo_only() -> None:
    if not settings.PUBLIC_DEMO:
        raise HTTPException(status_code=404, detail="Not Found")


@router.post("/visit", response_model=StartSessionResponse,
             dependencies=[Depends(demo_only), per_address("demo_visit_ip")])
async def start_demo_visit(
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    existing_token: Optional[str] = Depends(get_customer_session_token),
):
    """Seat this visitor at a table of their own (or bring them back to their open visit)."""
    raw_token, guest, table = await seat_demo_guest(db, existing_token)
    set_http_only_cookie(response, settings.SESSION_COOKIE_NAME, raw_token, settings.SESSION_MAX_AGE_SECONDS)

    branch = (await db.execute(select(BranchModel).where(BranchModel.id == table.branch_id))).scalar_one()
    restaurant = (await db.execute(select(RestaurantModel).where(RestaurantModel.id == branch.restaurant_id))).scalar_one()
    return StartSessionResponse(
        customer_id=guest.id,
        table_id=table.id,
        table_number=table.table_number,
        branch_name_ar=branch.name_ar,
        branch_name_en=branch.name_en,
        restaurant_name_ar=restaurant.name_ar,
        restaurant_name_en=restaurant.name_en,
    )
