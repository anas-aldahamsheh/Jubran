"""Authentication HTTP Router.

The login token is delivered only as an HttpOnly cookie; it never appears in a
response body, so page scripts (or an injected script) cannot read it.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Response, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import UserModel
from jubran.application.auth_service import AuthService
from jubran.infrastructure.auth.tokens import hash_token
from jubran.application.events import SESSION_ENDED_EVENT, record_event
from jubran.interfaces.websocket.gateway import ws_manager
from jubran.interfaces.http.cookies import clear_cookie, login_cookie_names, login_tokens, set_http_only_cookie
from jubran.interfaces.http.csrf import issue_token
from jubran.interfaces.http.rate_limits import client_ip
from jubran.application import rate_limiter
from jubran.interfaces.http.dependencies import get_current_user, get_customer_session_token
from jubran.interfaces.http.schemas import CsrfTokenResponse, LoginRequest, LoginResponse, UserResponse
from jubran.domain.enums import UserRole
from jubran.settings import settings

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


async def _end_login_sessions(db: AsyncSession, request: Request, response: Response,
                              keep_cookie: Optional[str] = None) -> None:
    """Revoke every login this browser carries and remove its cookies."""
    for token in login_tokens(request):
        await AuthService.logout(db, token)
        # Stop any live admin feed opened with this login: here at once, and in
        # every other server worker through the event relay.
        await ws_manager.close_admin_session(hash_token(token))
        record_event(db, SESSION_ENDED_EVENT, "auth_session", hash_token(token)[:36],
                     {"session_key": hash_token(token)})
        await db.commit()
    for name in login_cookie_names():
        # On login only stale cookies the browser actually has are removed
        # (keep_cookie is overwritten right after); logout clears both.
        if name != keep_cookie and (keep_cookie is None or request.cookies.get(name)):
            clear_cookie(response, name)


@router.get("/csrf", response_model=CsrfTokenResponse)
async def get_csrf_token(request: Request, response: Response):
    """Token the web app sends back in ``X-CSRF-Token`` on every change request."""
    response.headers["Cache-Control"] = "no-store"
    return CsrfTokenResponse(csrf_token=issue_token(request, response))


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    customer_session_token: Optional[str] = Depends(get_customer_session_token)
):
    # Slow down password guessing: per address, per account from that address,
    # and per account overall.
    address, account = client_ip(request), req.email.strip().lower()
    await rate_limiter.hit(db, "login_ip", address)
    await rate_limiter.hit(db, "login_account", f"{account}|{address}")
    await rate_limiter.hit(db, "login_account_hour", account)
    raw_token, user = await AuthService.login(
        db=db,
        email=req.email,
        password=req.password,
        customer_session_token=customer_session_token
    )

    # One login per browser: sign out whatever this browser was signed in as before.
    cookie_name = settings.ADMIN_SESSION_COOKIE_NAME if user.role == UserRole.ADMIN else settings.USER_SESSION_COOKIE_NAME
    await _end_login_sessions(db, request, response, keep_cookie=cookie_name)
    # "Remember me" keeps the cookie after the browser closes; otherwise it ends with the browser.
    set_http_only_cookie(response, cookie_name, raw_token,
                         settings.SESSION_MAX_AGE_SECONDS if req.remember_me else None)

    return LoginResponse(
        user=UserResponse(
            id=user.id,
            email=user.email,
            role=user.role.value,
            is_active=user.is_active
        )
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    customer_session_token: Optional[str] = Depends(get_customer_session_token)
) -> None:
    await _end_login_sessions(db, request, response)
    # The table visit in this browser goes on as a guest: it no longer carries the account.
    await AuthService.detach_account_from_visit(db, customer_session_token)


@router.get("/me")
async def get_me(user: Optional[UserModel] = Depends(get_current_user)):
    if not user:
        return {"authenticated": False, "role": "GUEST"}
    return {
        "authenticated": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role.value
        }
    }
