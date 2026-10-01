"""FastAPI HTTP Request Dependencies for Auth and Sessions."""
from typing import Optional, Tuple
from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import UserModel, CustomerSessionModel, TableSessionModel, PhysicalTableModel
from jubran.application.auth_service import AuthService
from jubran.application.session_service import SessionService
from jubran.domain.enums import UserRole
from jubran.interfaces.http.cookies import login_tokens
from jubran.settings import settings


async def get_current_auth_token(request: Request) -> Optional[str]:
    # Sign-in travels only in HttpOnly cookies (never in headers page scripts could set).
    tokens = login_tokens(request)
    return tokens[0] if tokens else None


async def get_current_user(
    token: Optional[str] = Depends(get_current_auth_token),
    db: AsyncSession = Depends(get_db_session)
) -> Optional[UserModel]:
    if not token:
        return None
    return await AuthService.get_user_by_token(db, token)


async def require_admin(
    user: Optional[UserModel] = Depends(get_current_user)
) -> UserModel:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHENTICATED", "message": "يجب تسجيل الدخول كمسؤول أولاً."}}
        )
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": "غير مصرح لك بالوصول إلى لوحة الإدارة."}}
        )
    return user


async def get_customer_session_token(request: Request) -> Optional[str]:
    # The table visit travels only in its HttpOnly cookie.
    return request.cookies.get(settings.SESSION_COOKIE_NAME)


async def get_required_customer_context(
    token: Optional[str] = Depends(get_customer_session_token),
    db: AsyncSession = Depends(get_db_session)
) -> Tuple[CustomerSessionModel, TableSessionModel, PhysicalTableModel]:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "NO_ACTIVE_SESSION", "message": "يرجى مسح رمز الطاولة لبدء الجلسة."}}
        )
    ctx = await SessionService.get_customer_session_by_token(db, token)
    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "SESSION_EXPIRED", "message": "انتهت صلاحية جلسة الطاولة. يرجى مسح الرمز مجدداً."}}
        )
    return ctx
