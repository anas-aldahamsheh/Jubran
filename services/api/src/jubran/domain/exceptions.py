"""Domain Exceptions."""
from typing import Optional, Dict, Any


class DomainException(Exception):
    """Expected business outcome; the HTTP layer turns it into a uniform error response."""
    http_status: int = 400

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class BusinessRuleError(DomainException, ValueError):
    """Invalid input or an action not allowed in the current state (still a ValueError)."""

    def __init__(self, message: str, code: str = "INVALID_REQUEST", http_status: int = 400,
                 details: Optional[Dict[str, Any]] = None):
        super().__init__(code=code, message=message, details=details)
        self.http_status = http_status


class EntityNotFoundException(DomainException):
    http_status = 404

    def __init__(self, entity_name: str, entity_id: str):
        super().__init__(
            code="ENTITY_NOT_FOUND",
            message=f"{entity_name} with identifier {entity_id} was not found.",
            details={"entity": entity_name, "id": entity_id}
        )


class UnauthorizedException(DomainException):
    http_status = 401

    def __init__(self, message: str = "غير مصرح بالدخول."):
        super().__init__(code="UNAUTHORIZED", message=message)


class ForbiddenException(DomainException):
    http_status = 403

    def __init__(self, message: str = "ليس لديك الصلاحية لتنفيذ هذا الإجراء."):
        super().__init__(code="FORBIDDEN", message=message)


class InvalidQRTokenException(DomainException):
    def __init__(self, message: str = "رمز الطاولة غير صالح أو ملغي."):
        super().__init__(code="INVALID_QR_TOKEN", message=message)


class TableSessionClosedException(DomainException):
    def __init__(self, message: str = "جلسة الطاولة مغلقة حالياً."):
        super().__init__(code="TABLE_SESSION_CLOSED", message=message)


class ProductUnavailableException(DomainException):
    def __init__(self, product_name: str):
        super().__init__(
            code="PRODUCT_UNAVAILABLE",
            message=f"الصنف '{product_name}' غير متوفر حالياً.",
            details={"product_name": product_name}
        )


class DraftVersionConflictException(DomainException):
    http_status = 409

    def __init__(self, current_version: int):
        super().__init__(
            code="DRAFT_VERSION_CONFLICT",
            message="تم تعديل محتويات السلة. يرجى مراجعة وتأكيد الطلب المحدث.",
            details={"current_version": current_version}
        )


class ConfirmedOrderChangedException(DraftVersionConflictException):
    """Prices, items or availability changed after the guest reviewed the order."""

    def __init__(self, current_version: int, summary: Dict[str, Any]):
        super().__init__(current_version)
        self.message = "تغيّرت أسعار أو أصناف طلبك بعد ما راجعته. راجع الطلب المحدّث وأكّده من جديد."
        self.args = (self.message,)
        self.details = {"current_version": current_version, "reason": "SUMMARY_CHANGED", "summary": summary}


class InvalidConfirmationTokenException(DomainException):
    def __init__(self, message: str = "رمز تأكيد الطلب غير صالح أو منتهي الصلاحية."):
        super().__init__(code="INVALID_CONFIRMATION_TOKEN", message=message)


class InvalidStateTransitionException(DomainException):
    def __init__(self, from_state: str, to_state: str):
        super().__init__(
            code="INVALID_STATE_TRANSITION",
            message=f"لا يمكن الانتقال من الحالة '{from_state}' إلى '{to_state}'.",
            details={"from_state": from_state, "to_state": to_state}
        )
