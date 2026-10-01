"""Initial Jubran data for an empty database.

The restaurant, its branch and the dine-in menu come from the restaurant's own site
(jubran.com): names, prices and only the details its menus give (no invented
ingredients, since the assistant answers allergy questions from them). The dishes'
photos are in ``menu_photos`` (see its CREDITS.md).
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jubran.infrastructure.db.menu_data import MENU
from jubran.infrastructure.db.seed_photos import add_missing_menu_photos
from jubran.infrastructure.db.models import (
    RestaurantModel, BranchModel, OpeningHourModel, MenuCategoryModel,
    ProductModel, PhysicalTableModel, UserModel
)
from jubran.domain.enums import UserRole, TableShape
from jubran.domain.money import jod_to_fils
from jubran.infrastructure.auth.passwords import hash_password
from jubran.settings import settings

RESTAURANT = {
    "name_ar": "جبران",
    "name_en": "Jubran",
    "about_ar": (
        "مطعم جبران على سطح بوليفارد العبدلي في عمّان، سُمّي تيمّناً بالشاعر جبران خليل جبران. "
        "مطبخ شامي (أردني ولبناني وسوري وأرمني) وأطباق عالمية، بأكثر من 100 طبق، "
        "مع فطور صباحي وقهوة مختصة وكوكتيلات طازجة بدون كحول وأرجيلة. جلسات داخلية وخارجية وخدمة صف السيارات."
    ),
    "about_en": (
        "Jubran is a rooftop restaurant at Abdali Boulevard, Amman, named after the poet Gibran Khalil Gibran. "
        "Levantine cooking (Jordanian, Lebanese, Syrian and Armenian) and international dishes, over 100 of them, "
        "with breakfast, specialty coffee, fresh non-alcoholic mocktails and shisha. Indoor and outdoor seating and valet parking."
    ),
    "phone": "+962 777 123 456",
}

# Every day 09:00 to 01:30 (past midnight). (day, opens, closes, note in Arabic, note in English)
OPENING_HOURS = tuple((day, "09:00", "01:30", None, None) for day in range(7))

# The first one has the tables (the branch the guests order from); any others are for
# guests who ask about the restaurant's other locations. Jubran has one branch.
BRANCHES = (
    {
        "name_ar": "بوليفارد العبدلي",
        "name_en": "Abdali Boulevard",
        "address_ar": "بوليفارد العبدلي، مدخل 2، الطابق السابع (الروف)، شارع رفيق الحريري، عمّان، الأردن",
        "address_en": "Abdali Boulevard, Entrance 2, 7th floor (rooftop), Rafiq Hariri Avenue, Amman, Jordan",
        "phone": "+962 777 123 456",
        "is_demo_branch": True,
    },
)


# Local demo sign-ins. Their passwords are published in this repository, so they
# exist only in development/test databases; production never creates them and
# disables them at startup if an older database still has them.
DEMO_ACCOUNTS = (
    ("admin@jubran.jo", "admin12345", UserRole.ADMIN),
    ("customer@jubran.jo", "user12345", UserRole.USER),
)


async def seed_demo_accounts(session: AsyncSession) -> None:
    if settings.is_production:
        return
    for email, password, role in DEMO_ACCOUNTS:
        existing = (await session.execute(select(UserModel).where(UserModel.email == email))).scalar_one_or_none()
        if not existing:
            session.add(UserModel(email=email, password_hash=hash_password(password), role=role, is_active=True))


def add_opening_hours(session: AsyncSession, branch: BranchModel) -> None:
    for day, opens, closes, notes_ar, notes_en in OPENING_HOURS:
        session.add(OpeningHourModel(branch_id=branch.id, day_of_week=day, opens_at=opens, closes_at=closes,
                                     notes_ar=notes_ar, notes_en=notes_en))


async def seed_database(session: AsyncSession, *, with_photos: bool = False) -> None:
    """Create the initial Jubran data on an empty database.

    Only adds what is missing; it never changes rows that already exist, so the
    administrator's edits (menu, hours, tables) survive every restart. With
    ``with_photos`` (the server and init_db), a newly created menu gets its dishes'
    photos too.
    """
    # 1. Demo sign-in accounts (never in production)
    await seed_demo_accounts(session)

    # 2. Restaurant
    restaurant = (await session.execute(select(RestaurantModel))).scalar_one_or_none()
    if not restaurant:
        restaurant = RestaurantModel(**RESTAURANT)
        session.add(restaurant)
        await session.flush()

    # 3. Branches, each with its opening hours
    branches = (await session.execute(select(BranchModel).where(BranchModel.restaurant_id == restaurant.id))).scalars().all()
    if not branches:
        for details in BRANCHES:
            new_branch = BranchModel(restaurant_id=restaurant.id, **details)
            session.add(new_branch)
            await session.flush()
            add_opening_hours(session, new_branch)
        branches = (await session.execute(select(BranchModel).where(BranchModel.restaurant_id == restaurant.id))).scalars().all()
    # The tables belong to the branch the guests order from.
    branch = next((b for b in branches if b.is_demo_branch), branches[0])

    # 4. Dine-in menu: categories, dishes and (with_photos) their photos
    existing_cats = (await session.execute(select(MenuCategoryModel).where(MenuCategoryModel.restaurant_id == restaurant.id))).scalars().all()
    if not existing_cats:
        for category_order, (category_ar, category_en, dishes) in enumerate(MENU, start=1):
            category = MenuCategoryModel(restaurant_id=restaurant.id, name_ar=category_ar, name_en=category_en,
                                         sort_order=category_order)
            session.add(category)
            await session.flush()
            for dish_order, (name_ar, name_en, desc_ar, desc_en, price) in enumerate(dishes, start=1):
                session.add(ProductModel(
                    category_id=category.id,
                    name_ar=name_ar,
                    name_en=name_en,
                    description_ar=desc_ar,
                    description_en=desc_en,
                    price_minor=jod_to_fils(price),
                    currency="JOD",
                    is_available=True,
                    sort_order=dish_order,
                ))
        if with_photos:
            await session.flush()
            await add_missing_menu_photos(session)

    # 5. Demo tables (T1 to T12). QR codes are never seeded: an administrator
    # creates an unguessable QR for each table from the Tables & QR page.
    existing_tables = (await session.execute(select(PhysicalTableModel).where(PhysicalTableModel.branch_id == branch.id))).scalars().all()
    if not existing_tables:
        table_configs = [
            ("T1", 14.55, 67.20, 0.0),
            ("T2", 17.87, 51.30, 0.0),
            ("T3", 18.95, 32.16, 0.0),
            ("T4", 28.22, 14.06, 0.0),
            ("T5", 43.95, 14.45, 0.0),
            ("T6", 35.64, 33.20, 0.0),
            ("T7", 34.67, 49.87, 0.0),
            ("T8", 61.13, 35.03, 45.0),
            ("T9", 62.30, 50.65, 45.0),
            ("T10", 81.25, 36.07, 0.0),
            ("T11", 83.40, 51.82, 0.0),
            ("T12", 65.43, 67.06, 0.0),
        ]
        for t_num, x, y, rot in table_configs:
            tbl = PhysicalTableModel(
                branch_id=branch.id,
                table_number=t_num,
                shape=TableShape.ROUND,
                seat_count=4,
                x_percent=x,
                y_percent=y,
                rotation_deg=rot,
                is_active=True
            )
            session.add(tbl)

    await session.commit()
