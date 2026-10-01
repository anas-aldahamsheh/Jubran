"""Browser credential cookies.

Login and table-visit tokens live only in HttpOnly cookies: page scripts can
never read them, and the API never returns them in a response body. Every
cookie is written and cleared with the same attributes, otherwise browsers
keep the old one.
"""
from typing import Optional

from starlette.requests import HTTPConnection
from starlette.responses import Response

from jubran.settings import settings


def credential_cookie_names() -> tuple[str, ...]:
    return (settings.ADMIN_SESSION_COOKIE_NAME, settings.USER_SESSION_COOKIE_NAME, settings.SESSION_COOKIE_NAME)


def login_cookie_names() -> tuple[str, str]:
    return (settings.ADMIN_SESSION_COOKIE_NAME, settings.USER_SESSION_COOKIE_NAME)


def set_http_only_cookie(response: Response, name: str, value: str, max_age: Optional[int]) -> None:
    """``max_age=None`` makes a browser-session cookie (gone when the browser closes)."""
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        path="/",
        domain=settings.COOKIE_DOMAIN,
        secure=bool(settings.SECURE_COOKIES),
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
    )


def clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(
        key=name,
        path="/",
        domain=settings.COOKIE_DOMAIN,
        secure=bool(settings.SECURE_COOKIES),
        httponly=True,
        samesite=settings.COOKIE_SAMESITE,
    )


def login_tokens(connection: HTTPConnection) -> list[str]:
    """All account-login tokens this browser carries (admin first)."""
    return [token for name in login_cookie_names() if (token := connection.cookies.get(name))]
