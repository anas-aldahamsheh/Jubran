"""Cross-site request forgery protection for cookie-authenticated requests.

Because sign-in now lives only in cookies, the browser attaches them to every
request, including ones another website triggers. Every state-changing API
request (POST/PUT/PATCH/DELETE) is therefore checked twice:

1. Origin: when the browser says which page sent the request (``Origin``, or
   ``Referer`` as a fallback), it must be this site's own frontend.
2. Token: when the request carries a login or table-visit cookie it must also
   send ``X-CSRF-Token``. The value is an HMAC (keyed with ``CSRF_SECRET``) of
   a random per-browser HttpOnly cookie, handed out by ``GET /api/v1/auth/csrf``.
   Another site can neither read that response nor forge the HMAC.
"""
import base64
import hashlib
import hmac
import secrets
from typing import Optional
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from jubran.interfaces.http.cookies import credential_cookie_names
from jubran.interfaces.http.origins import is_allowed_origin
from jubran.settings import settings

CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_COOKIE_BYTES = 32

ORIGIN_REJECTED = "CSRF_ORIGIN_REJECTED"
TOKEN_INVALID = "CSRF_TOKEN_INVALID"
_MESSAGES = {
    ORIGIN_REJECTED: "تم رفض الطلب لأنه صادر من موقع غير مصرح له.",
    TOKEN_INVALID: "انتهت صلاحية الصفحة. أعد المحاولة.",
}


def token_for(cookie_value: str) -> str:
    digest = hmac.new(settings.CSRF_SECRET.encode("utf-8"), f"csrf:{cookie_value}".encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _usable_cookie(value: Optional[str]) -> bool:
    return bool(value) and 32 <= len(value) <= 128


def issue_token(request: Request, response: Response) -> str:
    """Return this browser's CSRF token, creating its cookie on first use."""
    cookie_value = request.cookies.get(settings.CSRF_COOKIE_NAME)
    if not _usable_cookie(cookie_value):
        cookie_value = secrets.token_urlsafe(_COOKIE_BYTES)
    # Re-sent every time so the expiry keeps sliding for active browsers.
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=cookie_value,
        max_age=settings.CSRF_MAX_AGE_SECONDS,
        path="/",
        domain=settings.COOKIE_DOMAIN,
        secure=bool(settings.SECURE_COOKIES),
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
    )
    return token_for(cookie_value)


def _origin_of(url: str) -> Optional[str]:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _trusted_origin(origin: Optional[str], host: Optional[str]) -> bool:
    if not origin or origin == "null":
        return False
    if is_allowed_origin(origin):
        return True
    # Same-origin pages served by the API itself (for example /docs).
    return bool(host) and urlsplit(origin).netloc == host


def rejection_reason(request: Request) -> Optional[str]:
    """None when a state-changing request may proceed, else an error code."""
    host = request.headers.get("host")
    origin = request.headers.get("origin")
    if origin is not None:
        if not _trusted_origin(origin, host):
            return ORIGIN_REJECTED
    else:
        referer = request.headers.get("referer")
        if referer and not _trusted_origin(_origin_of(referer), host):
            return ORIGIN_REJECTED

    if not any(request.cookies.get(name) for name in credential_cookie_names()):
        return None  # No ambient credentials, nothing to forge.

    cookie_value = request.cookies.get(settings.CSRF_COOKIE_NAME)
    sent = request.headers.get(CSRF_HEADER)
    if not sent or not _usable_cookie(cookie_value) or not hmac.compare_digest(sent, token_for(cookie_value)):
        return TOKEN_INVALID
    return None


class CSRFMiddleware:
    """Pure ASGI middleware (keeps streaming and WebSockets untouched)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        reason = rejection_reason(Request(scope))
        if reason is None:
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            status_code=403,
            content={"detail": {"error": {"code": reason, "message": _MESSAGES[reason]}}},
        )
        await response(scope, receive, send)
