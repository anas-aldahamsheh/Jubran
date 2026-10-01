"""Restaurant Information Application Service."""
from typing import Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from jubran.infrastructure.db.models import RestaurantModel, BranchModel, OpeningHourModel
from jubran.domain.exceptions import BusinessRuleError, EntityNotFoundException
from jubran.application.ai.content_state import bump_content_version
from jubran.application.opening_hours import open_status


def _hours(branch: BranchModel) -> List[Dict[str, Any]]:
    return [{
        "day_of_week": oh.day_of_week,
        "opens_at": oh.opens_at,
        "closes_at": oh.closes_at,
        "notes_ar": oh.notes_ar,
        "notes_en": oh.notes_en,
    } for oh in sorted(branch.opening_hours, key=lambda x: x.day_of_week)]


def _branch_view(branch: BranchModel) -> Dict[str, Any]:
    return {
        "id": branch.id,
        "name_ar": branch.name_ar,
        "name_en": branch.name_en,
        "address_ar": branch.address_ar,
        "address_en": branch.address_en,
        "phone": branch.phone,
        "opening_hours": _hours(branch),
    }


class RestaurantService:
    @staticmethod
    async def get_restaurant_info(db: AsyncSession, branch_id: Optional[str] = None) -> Dict[str, Any]:
        """The restaurant, the branch in question (the guest's, else the one with the tables)
        with its opening hours, and the restaurant's other branches."""
        stmt = (
            select(RestaurantModel)
            .options(
                selectinload(RestaurantModel.branches).selectinload(BranchModel.opening_hours)
            )
        )
        restaurant = (await db.execute(stmt)).scalars().first()
        if not restaurant:
            raise EntityNotFoundException("Restaurant", "default")

        demo_branch = next((b for b in restaurant.branches if b.id == branch_id), None) if branch_id else None
        if not demo_branch:
            demo_branch = next((b for b in restaurant.branches if b.is_demo_branch), None)
        if not demo_branch and restaurant.branches:
            demo_branch = restaurant.branches[0]

        hours_list = _hours(demo_branch) if demo_branch else []
        others = sorted((b for b in restaurant.branches if demo_branch is None or b.id != demo_branch.id),
                        key=lambda b: (b.created_at is None, b.created_at, b.name_en))

        return {
            "id": restaurant.id,
            "name_ar": restaurant.name_ar,
            "name_en": restaurant.name_en,
            "about_ar": restaurant.about_ar,
            "about_en": restaurant.about_en,
            "phone": restaurant.phone,
            # Worked out here, so nobody (and no AI model) has to reason about hours past midnight.
            "open_now": open_status(hours_list) if hours_list else None,
            "branch": _branch_view(demo_branch) if demo_branch else None,
            "other_branches": [_branch_view(branch) for branch in others],
        }

    @staticmethod
    async def update_restaurant_info(
        db: AsyncSession,
        name_ar: Optional[str] = None,
        name_en: Optional[str] = None,
        about_ar: Optional[str] = None,
        about_en: Optional[str] = None,
        phone: Optional[str] = None,
        branch_address_ar: Optional[str] = None,
        branch_address_en: Optional[str] = None,
        branch_phone: Optional[str] = None,
        branch_name_ar: Optional[str] = None,
        branch_name_en: Optional[str] = None,
        opening_hours: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Update restaurant profile details."""
        stmt = (
            select(RestaurantModel)
            .options(
                selectinload(RestaurantModel.branches).selectinload(BranchModel.opening_hours)
            )
        )
        restaurant = (await db.execute(stmt)).scalars().first()
        if not restaurant:
            raise EntityNotFoundException("Restaurant", "default")

        required = {"name_ar": name_ar, "name_en": name_en, "about_ar": about_ar,
                    "about_en": about_en, "phone": phone,
                    "branch_name_ar": branch_name_ar, "branch_name_en": branch_name_en}
        if any(value is not None and not value.strip() for value in required.values()):
            raise BusinessRuleError("الاسم والنبذة والهاتف لا يمكن أن تكون فارغة.", "VALIDATION_ERROR", 422)

        if name_ar is not None and restaurant.name_ar != name_ar.strip():
            restaurant.name_ar = name_ar.strip()
        if name_en is not None and restaurant.name_en != name_en.strip():
            restaurant.name_en = name_en.strip()
        if about_ar is not None and restaurant.about_ar != about_ar.strip():
            restaurant.about_ar = about_ar.strip()
        if about_en is not None and restaurant.about_en != about_en.strip():
            restaurant.about_en = about_en.strip()
        if phone is not None and restaurant.phone != phone.strip():
            restaurant.phone = phone.strip()

        demo_branch = next((b for b in restaurant.branches if b.is_demo_branch), None)
        if demo_branch:
            if branch_name_ar is not None and demo_branch.name_ar != branch_name_ar.strip():
                demo_branch.name_ar = branch_name_ar.strip()
            if branch_name_en is not None and demo_branch.name_en != branch_name_en.strip():
                demo_branch.name_en = branch_name_en.strip()
            if branch_address_ar is not None and demo_branch.address_ar != branch_address_ar.strip():
                demo_branch.address_ar = branch_address_ar.strip()
            if branch_address_en is not None and demo_branch.address_en != branch_address_en.strip():
                demo_branch.address_en = branch_address_en.strip()
            if branch_phone is not None and demo_branch.phone != branch_phone.strip():
                demo_branch.phone = branch_phone.strip()
            if opening_hours is not None:
                if len(opening_hours) != 7 or {day["day_of_week"] for day in opening_hours} != set(range(7)):
                    raise BusinessRuleError("يجب إدخال أوقات العمل للأيام السبعة مرة واحدة لكل يوم.", "VALIDATION_ERROR", 422)
                existing = {day.day_of_week: day for day in demo_branch.opening_hours}
                for day in opening_hours:
                    record = existing.get(day["day_of_week"])
                    if record is None:
                        record = OpeningHourModel(branch_id=demo_branch.id, day_of_week=day["day_of_week"])
                        db.add(record)
                    for field in ("opens_at", "closes_at"):
                        value = day.get(field)
                        if getattr(record, field) != value:
                            setattr(record, field, value)
                    # A day's note changes only when it is sent (empty clears it).
                    for field in ("notes_ar", "notes_en"):
                        if field in day:
                            value = (day[field] or "").strip() or None
                            if getattr(record, field) != value:
                                setattr(record, field, value)

        await bump_content_version(db)
        await db.commit()
        return await RestaurantService.get_restaurant_info(db)
