"""Cap the size of request bodies *before* they are read into memory or onto disk.

Uploads are parsed (and spooled) before a route handler runs, so a size check
inside the handler comes too late. This middleware refuses an oversized body
from its Content-Length header, and counts the bytes of bodies sent without one,
answering 413 as soon as the limit is passed.
"""
import json
import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_AUDIO_UPLOAD_BYTES = 5 * 1024 * 1024       # a spoken turn or dictation (the app records at most a minute)
MAX_PRODUCT_IMAGES_BYTES = 5 * 8 * 1024 * 1024  # five product photos of up to 8 MB each
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024        # every JSON request
_FORM_OVERHEAD = 64 * 1024                      # multipart boundaries and headers

_LIMITS = (
    (re.compile(r"^/api/v1/assistant/(dictation|voice/audio-turn)$"), MAX_AUDIO_UPLOAD_BYTES + _FORM_OVERHEAD),
    (re.compile(r"^/api/v1/admin/menu/products/[^/]+/images$"), MAX_PRODUCT_IMAGES_BYTES + _FORM_OVERHEAD),
)


def body_limit(path: str) -> int:
    for pattern, limit in _LIMITS:
        if pattern.match(path):
            return limit
    return DEFAULT_MAX_BODY_BYTES


def _too_large_body(limit: int) -> bytes:
    megabytes = limit / (1024 * 1024)
    return json.dumps({"detail": {"error": {
        "code": "PAYLOAD_TOO_LARGE",
        "message": f"حجم البيانات المرسلة أكبر من المسموح ({megabytes:.0f} ميغابايت).",
        "details": {"max_bytes": limit},
    }}}, ensure_ascii=False).encode("utf-8")


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        limit = body_limit(scope["path"])

        async def refuse() -> None:
            body = _too_large_body(limit)
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode()),
                                    (b"connection", b"close")]})
            await send({"type": "http.response.body", "body": body})

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await refuse()
            return

        received = 0
        exceeded = False
        answered = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    return {"type": "http.disconnect"}  # stop reading; the app gives up on the body
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal answered
            if exceeded:
                # Whatever the app answers about the cut-off body, the reason is the size.
                if not answered:
                    answered = True
                    await refuse()
                return
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not answered:
            await refuse()
