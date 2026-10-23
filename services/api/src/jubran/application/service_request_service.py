"""Service Requests, Complaints, and Feedback Application Service."""
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jubran.infrastructure.db.models import (
    ServiceRequestModel, ComplaintModel, FeedbackModel, OrderModel, TableSessionModel, UserModel
)
from jubran.domain.enums import (
    ServiceRequestType, ServiceRequestStatus, ComplaintStatus, TableSessionStatus
)
from jubran.domain.exceptions import (
    BusinessRuleError, EntityNotFoundException, TableSessionClosedException
)
from jubran.application.events import record_event
from jubran.application.opening_hours import RESTAURANT_TZ

MAX_COMPLAINT_PART = 500
# One complaint per guest per visit: what they add later becomes timed lines on it.
MAX_COMPLAINT_LENGTH = 4000
MAX_FEEDBACK_COMMENT = 500
# A guest can cancel their own service request until staff mark it done.
CANCELLABLE_STATES = (ServiceRequestStatus.OPEN, ServiceRequestStatus.IN_PROGRESS)
_TIMED_LINE = re.compile(r"^\[\d{1,2}:\d{2}\] ")
_EDGE = " \t\n,.،؛;:!?؟-–—"


def _plain(text: str) -> str:
    """A text for comparing: single spaces, no case (letter by letter, so positions match)."""
    return "".join(letter.lower() if len(letter.lower()) == 1 else letter for letter in " ".join((text or "").split()))


def new_part(earlier: str, message: str) -> str:
    """What ``message`` adds to ``earlier``: nothing when it only repeats it, and without the
    repeated part when it repeats it and goes on ("the tea was cold" + "the tea was cold, I
    want a discount" adds "I want a discount")."""
    message = " ".join((message or "").split())
    said = [_TIMED_LINE.sub("", line).strip() for line in (earlier or "").split("\n")]
    said = [line for line in said if line] + [" ".join((earlier or "").split())]
    if any(_plain(message) in _plain(line) for line in said):
        return ""
    for line in sorted(said, key=len, reverse=True):
        start = _plain(message).find(_plain(line)) if line else -1
        if start >= 0:  # positions match: the plain form only lowers the case of single letters
            message = message[:start] + " " + message[start + len(_plain(line)):]
            message = " ".join(message.split())
    message = message.strip(_EDGE)
    return message if len(re.findall(r"\w", message)) >= 2 else ""


def _cancelled_meanwhile(req: ServiceRequestModel) -> BusinessRuleError:
    """Staff acted on a request that was cancelled meanwhile: by the guest, or when the visit ended."""
    message = "أُلغي هذا الطلب مع إنهاء الجلسة" if req.closure_note else "الزبون ألغى هذا الطلب"
    return BusinessRuleError(message, "SERVICE_REQUEST_CANCELLED", 409)


def merged_comment(earlier: Optional[str], comment: Optional[str]) -> Optional[str]:
    """A rating's comment after a new one: the earlier comment stays, and the new one is added
    (unless one already says the other)."""
    if not comment:
        return earlier
    if not earlier or _plain(earlier) in _plain(comment):
        return comment[:MAX_FEEDBACK_COMMENT]
    if _plain(comment) in _plain(earlier):
        return earlier
    return f"{earlier}\n{comment}"[:MAX_FEEDBACK_COMMENT]


class CustomerServiceManager:
    @staticmethod
    async def get_service_requests_for_customer(
        db: AsyncSession,
        table_session_id: str,
        customer_session_id: str,
    ) -> List[Dict[str, Any]]:
        """Return service requests for the customer's current table session."""
        stmt = (
            select(ServiceRequestModel)
            .where(ServiceRequestModel.table_session_id == table_session_id)
            .order_by(ServiceRequestModel.created_at.desc())
        )
        requests = (await db.execute(stmt)).scalars().all()
        return [
            {
                "request_id": req.id,
                "type": req.type.value,
                "status": req.status.value,
                "created_at": req.created_at.isoformat(),
                "can_cancel": req.customer_session_id == customer_session_id and req.status in CANCELLABLE_STATES,
                # Staff notes stay in the admin panel; guests only see that the visit ended it.
                "cancelled_with_visit": req.status == ServiceRequestStatus.CANCELLED and req.closure_note is not None,
            }
            for req in requests
        ]

    @staticmethod
    async def create_service_request(
        db: AsyncSession,
        table_session_id: str,
        customer_session_id: str,
        request_type: ServiceRequestType
    ) -> Dict[str, Any]:
        """Keep at most one unfinished request of each type per active table session."""
        # Serialize requests for this table session on databases that support row locks.
        session = (await db.execute(
            select(TableSessionModel)
            .where(TableSessionModel.id == table_session_id)
            .with_for_update()
        )).scalar_one_or_none()
        if not session or session.status != TableSessionStatus.ACTIVE:
            raise TableSessionClosedException()

        now = datetime.now(timezone.utc)
        # A request stays exclusive until staff resolves it or the customer cancels it.
        stmt = (
            select(ServiceRequestModel)
            .where(
                ServiceRequestModel.table_session_id == table_session_id,
                ServiceRequestModel.type == request_type,
                ServiceRequestModel.status.in_((ServiceRequestStatus.OPEN, ServiceRequestStatus.IN_PROGRESS)),
            )
        )
        # .first(): even if an old race left two unfinished requests, never crash.
        existing = (await db.execute(stmt.order_by(ServiceRequestModel.created_at))).scalars().first()
        if existing:
            return {
                "request_id": existing.id,
                "type": existing.type.value,
                "status": existing.status.value,
                "is_duplicate": True,
                "created_at": existing.created_at.isoformat()
            }

        req = ServiceRequestModel(
            table_session_id=table_session_id,
            customer_session_id=customer_session_id,
            type=request_type,
            status=ServiceRequestStatus.OPEN,
            created_at=now
        )
        db.add(req)
        await db.flush()  # assigns the id used by the event
        record_event(db, "service_request.created", "service_request", req.id, {
            "request_id": req.id,
            "table_session_id": table_session_id,
            "type": req.type.value
        })
        await db.commit()

        return {
            "request_id": req.id,
            "type": req.type.value,
            "status": req.status.value,
            "is_duplicate": False,
            "created_at": req.created_at.isoformat()
        }

    @staticmethod
    async def start_service_request(
        db: AsyncSession, request_id: str
    ) -> ServiceRequestModel:
        req = (await db.execute(select(ServiceRequestModel).where(ServiceRequestModel.id == request_id))).scalar_one_or_none()
        if not req:
            raise EntityNotFoundException("ServiceRequest", request_id)
        if req.status == ServiceRequestStatus.CANCELLED:
            raise _cancelled_meanwhile(req)
        if req.status != ServiceRequestStatus.OPEN:
            raise BusinessRuleError("طلب الخدمة لم يعد بانتظار التأكيد", "INVALID_SERVICE_STATE", 409)
        req.status = ServiceRequestStatus.IN_PROGRESS
        record_event(db, "service_request.started", "service_request", req.id, {
            "request_id": req.id, "table_session_id": req.table_session_id
        })
        await db.commit()
        return req

    @staticmethod
    async def resolve_service_request(
        db: AsyncSession,
        request_id: str,
        admin_user: UserModel
    ) -> ServiceRequestModel:
        """Complete a service request after it has entered progress."""
        req = (await db.execute(select(ServiceRequestModel).where(ServiceRequestModel.id == request_id))).scalar_one_or_none()
        if not req:
            raise EntityNotFoundException("ServiceRequest", request_id)
        if req.status == ServiceRequestStatus.CANCELLED:
            raise _cancelled_meanwhile(req)
        if req.status != ServiceRequestStatus.IN_PROGRESS:
            raise BusinessRuleError("يجب بدء معالجة طلب الخدمة قبل إتمامه", "REQUEST_NOT_OPEN", 409)

        now = datetime.now(timezone.utc)
        req.status = ServiceRequestStatus.RESOLVED
        req.resolved_at = now
        req.resolved_by_admin_id = admin_user.id
        record_event(db, "service_request.resolved", "service_request", req.id, {
            "request_id": req.id,
            "table_session_id": req.table_session_id
        })
        await db.commit()
        return req

    @staticmethod
    async def cancel_service_request(
        db: AsyncSession,
        request_id: str,
        table_session_id: str,
        customer_session_id: str,
    ) -> ServiceRequestModel:
        """Cancel a service request this customer made in this table session, until staff mark it done."""
        req = (await db.execute(
            select(ServiceRequestModel).where(
                ServiceRequestModel.id == request_id,
                ServiceRequestModel.table_session_id == table_session_id,
                ServiceRequestModel.customer_session_id == customer_session_id,
            )
        )).scalar_one_or_none()
        if not req:
            raise EntityNotFoundException("ServiceRequest", request_id)
        if req.status not in CANCELLABLE_STATES:
            raise BusinessRuleError("لا يمكن إلغاء هذا الطلب لأنه انتهى", "REQUEST_NOT_CANCELLABLE", 409)

        req.status = ServiceRequestStatus.CANCELLED
        record_event(db, "service_request.cancelled", "service_request", req.id, {
            "request_id": req.id,
            "table_session_id": req.table_session_id,
            "type": req.type.value,
        })
        await db.commit()
        return req

    @staticmethod
    async def submit_complaint(
        db: AsyncSession,
        table_session_id: str,
        customer_session_id: str,
        message: str,
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """Record a guest's complaint: one per guest per visit.

        A later complaint is added to the earlier one as a timed line (only what it adds, when
        it repeats what was said), and a resolved complaint opens again with it.
        """
        session = (await db.execute(select(TableSessionModel).where(TableSessionModel.id == table_session_id))).scalar_one_or_none()
        if not session or session.status != TableSessionStatus.ACTIVE:
            raise TableSessionClosedException()

        clean_message = (message or "").strip()[:MAX_COMPLAINT_PART]
        # Same rule for the page and the assistant: a complaint needs actual content.
        if len(clean_message) < 3:
            raise BusinessRuleError("اكتب تفاصيل الشكوى (3 أحرف على الأقل).", "VALIDATION_ERROR", 422)
        now = datetime.now(timezone.utc)

        earlier = (await db.execute(
            select(ComplaintModel).where(ComplaintModel.table_session_id == table_session_id,
                                         ComplaintModel.customer_session_id == customer_session_id)
            .order_by(ComplaintModel.created_at.desc()).limit(1).with_for_update()
        )).scalar_one_or_none()
        if earlier is not None:
            room = MAX_COMPLAINT_LENGTH - len(earlier.message) - len("\n[00:00] ")
            added = new_part(earlier.message, clean_message)[:max(room, 0)]
            reopened = False
            if added:
                stamp = now.astimezone(RESTAURANT_TZ).strftime("%H:%M")
                earlier.message = f"{earlier.message}\n[{stamp}] {added}"
                if earlier.status == ComplaintStatus.RESOLVED:
                    earlier.status, earlier.resolved_at, reopened = ComplaintStatus.OPEN, None, True
                record_event(db, "complaint.updated", "complaint", earlier.id, {
                    "complaint_id": earlier.id,
                    "table_session_id": table_session_id,
                    "message": added,
                    "reopened": reopened,
                })
            await db.commit()  # with nothing new, the complaint simply stays as it is
            return {
                "complaint_id": earlier.id,
                "status": earlier.status.value,
                "created_at": earlier.created_at.isoformat(),
                "added_to_earlier": True,
                "nothing_new": not added,
                "reopened": reopened,
            }

        complaint = ComplaintModel(
            table_session_id=table_session_id,
            customer_session_id=customer_session_id,
            message=clean_message,
            category=category[:50] if category else None,
            status=ComplaintStatus.OPEN,
            created_at=now
        )
        db.add(complaint)
        await db.flush()  # assigns the id used by the event
        record_event(db, "complaint.created", "complaint", complaint.id, {
            "complaint_id": complaint.id,
            "table_session_id": table_session_id,
            "message": clean_message
        })
        await db.commit()

        return {
            "complaint_id": complaint.id,
            "status": complaint.status.value,
            "created_at": complaint.created_at.isoformat(),
            "added_to_earlier": False,
        }

    @staticmethod
    async def start_complaint(db: AsyncSession, complaint_id: str) -> ComplaintModel:
        complaint = (await db.execute(select(ComplaintModel).where(ComplaintModel.id == complaint_id))).scalar_one_or_none()
        if not complaint:
            raise EntityNotFoundException("Complaint", complaint_id)
        if complaint.status != ComplaintStatus.OPEN:
            raise BusinessRuleError("الشكوى لم تعد بانتظار التأكيد", "INVALID_COMPLAINT_STATE", 409)
        complaint.status = ComplaintStatus.IN_PROGRESS
        record_event(db, "complaint.started", "complaint", complaint.id, {
            "complaint_id": complaint.id, "table_session_id": complaint.table_session_id
        })
        await db.commit()
        return complaint

    @staticmethod
    async def resolve_complaint(
        db: AsyncSession,
        complaint_id: str
    ) -> ComplaintModel:
        """Resolve a complaint alert."""
        complaint = (await db.execute(select(ComplaintModel).where(ComplaintModel.id == complaint_id))).scalar_one_or_none()
        if not complaint:
            raise EntityNotFoundException("Complaint", complaint_id)
        if complaint.status != ComplaintStatus.IN_PROGRESS:
            raise BusinessRuleError("يجب بدء معالجة الشكوى قبل إتمامها", "INVALID_COMPLAINT_STATE", 409)

        complaint.status = ComplaintStatus.RESOLVED
        complaint.resolved_at = datetime.now(timezone.utc)
        record_event(db, "complaint.resolved", "complaint", complaint.id, {
            "complaint_id": complaint.id,
            "table_session_id": complaint.table_session_id
        })
        await db.commit()
        return complaint

    @staticmethod
    async def submit_feedback(
        db: AsyncSession,
        table_session_id: str,
        customer_session_id: str,
        rating: int,
        comment: Optional[str] = None
    ) -> Dict[str, Any]:
        """Record the guest's rating of this visit (page or assistant).

        Only once something was ordered at the table (there is a meal to rate), and
        one rating per guest per visit: rating again replaces the stars, and a new
        comment is added to the earlier one (which is never lost).
        """
        if not isinstance(rating, int) or rating < 1 or rating > 5:
            raise BusinessRuleError("يجب أن يكون التقييم بين 1 و 5 نجوم.", "VALIDATION_ERROR", 422)
        session = (await db.execute(select(TableSessionModel).where(TableSessionModel.id == table_session_id))).scalar_one_or_none()
        if not session or session.status != TableSessionStatus.ACTIVE:
            raise TableSessionClosedException()
        ordered = (await db.execute(
            select(OrderModel.id).where(OrderModel.table_session_id == table_session_id).limit(1)
        )).scalar_one_or_none()
        if ordered is None:
            raise BusinessRuleError("يمكنك تقييم الزيارة بعد أن تطلب من المطعم.", "FEEDBACK_TOO_EARLY", 409)

        clean_comment = comment.strip()[:MAX_FEEDBACK_COMMENT] if comment and comment.strip() else None
        now = datetime.now(timezone.utc)

        fb = (await db.execute(
            select(FeedbackModel).where(FeedbackModel.table_session_id == table_session_id,
                                        FeedbackModel.customer_session_id == customer_session_id)
            .order_by(FeedbackModel.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        updated = fb is not None
        if fb is None:
            fb = FeedbackModel(table_session_id=table_session_id, customer_session_id=customer_session_id)
            db.add(fb)
        fb.rating = rating
        # New stars replace the earlier ones; the earlier comment stays and a new one is added to it.
        fb.comment = merged_comment(fb.comment, clean_comment)
        fb.created_at = now
        await db.commit()

        return {
            "feedback_id": fb.id,
            "rating": fb.rating,
            "comment": fb.comment,
            "updated": updated,
            "created_at": fb.created_at.isoformat()
        }
