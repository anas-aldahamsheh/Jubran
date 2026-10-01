"""Safe provider failure classification for user-facing assistant errors."""
from typing import Any


def _status_code(error: Exception) -> int | None:
    candidates: list[Any] = [
        getattr(error, "code", None),
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ]
    for candidate in candidates:
        try:
            status = int(candidate)
        except (TypeError, ValueError):
            continue
        if 100 <= status <= 599:
            return status
    return None


def classify_provider_failure(error: Exception, component: str) -> str:
    """Return a stable error code without exposing provider response details."""
    prefix = "EMBEDDING" if component == "embedding" else "AI"
    details = str(error).casefold()
    response = getattr(error, "response", None)
    if response is not None:
        try:
            details += " " + str(response.text).casefold()
        except Exception:
            pass

    status = _status_code(error)
    if any(term in details for term in ("invalid api key", "api key not valid", "unauthorized", "invalid_api_key")):
        return f"{prefix}_AUTH_FAILED"
    if status == 429:
        if any(term in details for term in ("quota", "insufficient_quota", "billing_hard_limit")):
            return f"{prefix}_QUOTA_EXCEEDED"
        return f"{prefix}_RATE_LIMITED"
    if status == 401:
        return f"{prefix}_AUTH_FAILED"
    if status == 403:
        return f"{prefix}_ACCESS_DENIED"
    if status == 404:
        return f"{prefix}_MODEL_NOT_FOUND"
    if status in {408, 500, 502, 503, 504} or any(
        term in details for term in ("timed out", "timeout", "connection refused", "network is unreachable")
    ):
        return f"{prefix}_PROVIDER_UNAVAILABLE"
    # Live (WebSocket) connections end with a close code instead of an HTTP status.
    if "not found" in details or "not supported" in details:
        return f"{prefix}_MODEL_NOT_FOUND"
    if status is not None and 400 <= status < 500:
        return f"{prefix}_REQUEST_REJECTED"
    if "quota" in details:
        return f"{prefix}_QUOTA_EXCEEDED"
    return f"{prefix}_FAILED"


# What the guest reads when the assistant can't answer: plain words and a way forward,
# never technical details (models, providers, quotas, keys). The precise code stays in
# the response and the server log for staff.
_UNAVAILABLE = ("المساعد مش متاح هلأ. فيك تطلب من المنيو مباشرة، أو تطلب موظف من زر الخدمة.",
                "The assistant isn't available right now. You can still order from the menu or call a staff member.")
_BUSY = ("المساعد مشغول شوي هلأ. جرّب كمان لحظة، أو اطلب من المنيو مباشرة.",
         "The assistant is busy right now. Please try again in a moment, or order from the menu.")
_UNFINISHED = ("ما قدرت أكمل الرد هالمرة. ابعت رسالتك مرة ثانية، أو اطلب من المنيو مباشرة.",
               "I couldn't finish that reply. Please send your message again, or order from the menu.")
_MESSAGES = {
    "AI_RATE_LIMITED": _BUSY,
    "EMBEDDING_RATE_LIMITED": _BUSY,
    "EMPTY_MODEL_RESPONSE": _UNFINISHED,
    "TOOL_ROUND_LIMIT": _UNFINISHED,
    "AI_FAILED": _UNFINISHED,
    "COMPLAINT_SAVE_FAILED": ("ما قدرت أسجّل الشكوى. جرّب مرة ثانية، أو اطلب موظف من زر الخدمة.",
                              "I couldn't record your complaint. Please try again, or call a staff member."),
}


def user_error_message(code: str, language: str = "ar") -> str:
    arabic, english = _MESSAGES.get(code, _UNAVAILABLE)
    return english if language == "en" else arabic
