"""FastAPI glue for the rate limiter: per-guest, per-table, per-address and per-admin limits."""
from typing import Tuple

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application import rate_limiter
from jubran.application.rate_limiter import RateLimitExceeded
from jubran.infrastructure.db.models import CustomerSessionModel, PhysicalTableModel, TableSessionModel, UserModel
from jubran.infrastructure.db.session import get_db_session
from jubran.interfaces.http.dependencies import get_required_customer_context, require_admin

CustomerContext = Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel]


def client_ip(request: Request) -> str:
    # With uvicorn --proxy-headers this is the visitor's address, not the proxy's.
    return request.client.host if request.client else "unknown"


def per_guest(*buckets: str):
    """Limit by the guest's own visit (and optionally the whole table: bucket 'assistant_table')."""
    async def dependency(ctx: CustomerContext = Depends(get_required_customer_context),
                         db: AsyncSession = Depends(get_db_session)) -> None:
        customer, table_session, _ = ctx
        for bucket in buckets:
            subject = table_session.id if bucket == "assistant_table" else customer.id
            await rate_limiter.hit(db, bucket, subject)
    return Depends(dependency)


def per_address(bucket: str):
    async def dependency(request: Request, db: AsyncSession = Depends(get_db_session)) -> None:
        await rate_limiter.hit(db, bucket, client_ip(request))
    return Depends(dependency)


def per_admin(bucket: str):
    async def dependency(admin: UserModel = Depends(require_admin),
                         db: AsyncSession = Depends(get_db_session)) -> None:
        await rate_limiter.hit(db, bucket, admin.id)
    return Depends(dependency)


async def rate_limited_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(exc.retry_after)},
        content={"detail": {"error": {
            "code": "RATE_LIMITED",
            "message": f"طلبات كثيرة خلال وقت قصير. حاول مرة أخرى بعد {exc.retry_after} ثانية.",
            "details": {"retry_after_seconds": exc.retry_after},
        }}},
    )
