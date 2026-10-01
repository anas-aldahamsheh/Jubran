"""Authoritative Domain Enums."""
from enum import Enum


class UserRole(str, Enum):
    """Exactly two roles in this release."""
    USER = "USER"
    ADMIN = "ADMIN"


class TableShape(str, Enum):
    SQUARE = "SQUARE"
    ROUND = "ROUND"
    RECTANGLE = "RECTANGLE"


class TableSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class DraftStatus(str, Enum):
    OPEN = "OPEN"
    SUBMITTED = "SUBMITTED"
    ABANDONED = "ABANDONED"


class OrderStatus(str, Enum):
    """Order workflow and terminal states."""
    PENDING_APPROVAL = "PENDING_APPROVAL"
    PREPARING = "PREPARING"
    READY = "READY"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class TableBaseState(str, Enum):
    """Operational table state projected onto 2.5D map."""
    VACANT = "VACANT"
    OCCUPIED_IDLE = "OCCUPIED_IDLE"
    ORDER_PENDING = "ORDER_PENDING"
    ORDER_PREPARING = "ORDER_PREPARING"
    ORDER_READY = "ORDER_READY"


class ServiceRequestType(str, Enum):
    """Approved customer service request types."""
    STAFF = "STAFF"
    TISSUES = "TISSUES"
    CLEAN_TABLE = "CLEAN_TABLE"
    BILL = "BILL"


class ServiceRequestStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class ComplaintStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
