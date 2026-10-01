"""Database seeding integration test."""
import pytest
from sqlalchemy import select
from jubran.infrastructure.db.menu_data import MENU
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.db.models import (
    RestaurantModel, BranchModel, OpeningHourModel, MenuCategoryModel,
    ProductModel, PhysicalTableModel, TableQrTokenModel, UserModel
)


@pytest.mark.asyncio
async def test_seed_database_integration(db_session):
    await seed_database(db_session)

    # Verify Restaurant and Branch
    restaurant = (await db_session.execute(select(RestaurantModel))).scalar_one()
    assert restaurant.name_ar == "جبران"
    assert restaurant.name_en == "Jubran"

    branch = (await db_session.execute(select(BranchModel))).scalar_one()
    assert "العبدلي" in branch.name_ar

    # Verify Opening hours
    hours = (await db_session.execute(select(OpeningHourModel))).scalars().all()
    assert len(hours) == 7
    assert {(h.opens_at, h.closes_at) for h in hours} == {("09:00", "01:30")}

    # Verify Categories
    categories = (await db_session.execute(select(MenuCategoryModel))).scalars().all()
    assert len(categories) == len(MENU)

    # Verify Products
    products = (await db_session.execute(select(ProductModel))).scalars().all()
    assert len(products) == sum(len(dishes) for _, _, dishes in MENU)

    # Verify Tables (T1..T12)
    tables = (await db_session.execute(select(PhysicalTableModel))).scalars().all()
    assert len(tables) == 12

    # QR codes are never seeded; an administrator creates unguessable ones per table.
    tokens = (await db_session.execute(select(TableQrTokenModel))).scalars().all()
    assert tokens == []

    # Verify Demo accounts
    users = (await db_session.execute(select(UserModel))).scalars().all()
    assert len(users) == 2
    emails = {u.email for u in users}
    assert "admin@jubran.jo" in emails
    assert "customer@jubran.jo" in emails
