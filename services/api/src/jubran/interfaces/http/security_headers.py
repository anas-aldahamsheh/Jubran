"""Security headers on every API response (pure ASGI, so streaming and sockets are untouched)."""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jubran.settings import settings

# The API returns JSON and images only, so it may load and embed nothing.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_DOCS_PATHS = ("/docs", "/openapi.json")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                # Swagger UI (debug only) loads its own scripts; everything else gets a lock-down policy.
                if not (settings.DEBUG and path.startswith(_DOCS_PATHS)):
                    headers.setdefault("Content-Security-Policy", _API_CSP)
                if settings.SECURE_COOKIES:
                    headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
                # Personal data (orders, sessions) must never sit in shared caches.
                if path.startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)
