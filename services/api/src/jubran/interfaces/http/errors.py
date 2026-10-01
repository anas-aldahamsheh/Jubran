"""One error format for the whole API: {"detail": {"error": {"code", "message", "details"?}}}.

Also gives every request an id (X-Request-ID) so an unexpected 500 shown to a
guest can be matched with the server log, without exposing internals.
"""
import logging
import re
import uuid
from typing import Any, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jubran.application.ai.agent_service import AssistantUnavailableError
from jubran.application.ai.provider_errors import user_error_message
from jubran.domain.exceptions import DomainException

logger = logging.getLogger("jubran.errors")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def error_response(status: int, code: str, message: str, details: Optional[dict] = None,
                   headers: Optional[dict] = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status, content={"detail": {"error": error}}, headers=headers)


async def domain_error_handler(request: Request, exc: DomainException) -> JSONResponse:
    return error_response(exc.http_status, exc.code, exc.message, exc.details or None)


async def assistant_unavailable_handler(request: Request, exc: AssistantUnavailableError) -> JSONResponse:
    # Arabic message plus an English one the web app shows when the guest chose English.
    return error_response(503, str(exc), user_error_message(str(exc)),
                          {"message_en": user_error_message(str(exc), "en")})


FIELD_LABELS = {
    "quantity": "الكمية", "note": "الملاحظة", "email": "البريد الإلكتروني", "password": "كلمة المرور",
    "qr_token": "رمز الطاولة", "message": "الرسالة", "rating": "التقييم", "price_minor": "السعر",
    "name_ar": "الاسم بالعربية", "name_en": "الاسم بالإنجليزية", "description_ar": "الوصف بالعربية",
    "description_en": "الوصف بالإنجليزية", "phone": "رقم الهاتف", "draft_version": "نسخة السلة",
    "confirmation_token": "رمز التأكيد", "product_id": "الصنف", "category_id": "القسم",
    "image_asset_url": "رابط الصورة",
}


def _reason(error: dict) -> str:
    """A short Arabic reason for the common validation failures; the original text otherwise."""
    kind, ctx = error.get("type", ""), error.get("ctx") or {}
    reasons = {
        "missing": "مطلوب",
        "less_than_equal": f"يجب ألا يزيد عن {ctx.get('le')}",
        "less_than": f"يجب أن يكون أقل من {ctx.get('lt')}",
        "greater_than_equal": f"يجب ألا يقل عن {ctx.get('ge')}",
        "greater_than": f"يجب أن يكون أكبر من {ctx.get('gt')}",
        "string_too_short": f"قصير جداً (الحد الأدنى {ctx.get('min_length')})",
        "string_too_long": f"طويل جداً (الحد الأقصى {ctx.get('max_length')} حرفاً)",
        "int_parsing": "يجب أن يكون رقماً صحيحاً", "int_type": "يجب أن يكون رقماً صحيحاً",
        "int_from_float": "يجب أن يكون رقماً صحيحاً", "bool_parsing": "يجب أن يكون نعم أو لا",
        "json_invalid": "صيغة البيانات غير صحيحة", "enum": "قيمة غير مسموحة", "literal_error": "قيمة غير مسموحة",
    }
    if kind == "string_pattern_mismatch" and error.get("loc", ())[-1:] == ("image_asset_url",):
        return "يجب أن يكون مسار صورة من الموقع نفسه، مثل /images/hummus.png"
    if kind == "string_pattern_mismatch":
        return "الصيغة غير صحيحة"
    if kind in reasons:
        return reasons[kind]
    return str(error.get("msg", "")).removeprefix("Value error, ")


def _field_message(error: dict) -> str:
    parts = [str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path", "header", "cookie")]
    label = FIELD_LABELS.get(parts[-1], ".".join(parts)) if parts else ""
    reason = _reason(error)
    return f"{label}: {reason}" if label else reason


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [_field_message(error) for error in exc.errors()]
    return error_response(422, "VALIDATION_ERROR", "البيانات المرسلة غير صحيحة. " + (fields[0] if fields else ""),
                          {"fields": fields})


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Keep errors already in the standard shape; wrap plain-text ones."""
    headers = getattr(exc, "headers", None)
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=headers)
    return error_response(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail), headers=headers)


class RequestContextMiddleware:
    """Request ids, and a JSON 500 (inside CORS, so the web app can read it) for unexpected errors."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception("Unhandled error (request %s %s, id %s)", scope.get("method"), scope.get("path"), request_id)
            if started:
                raise
            response = error_response(500, "INTERNAL_ERROR", "حدث خطأ غير متوقع. حاول مرة أخرى.",
                                      {"request_id": request_id}, headers={"X-Request-ID": request_id})
            await response(scope, receive, send)


def register_error_handlers(app) -> None:
    app.add_exception_handler(DomainException, domain_error_handler)
    app.add_exception_handler(AssistantUnavailableError, assistant_unavailable_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
