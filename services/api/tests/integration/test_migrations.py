"""Database migrations: fresh installs, databases from before migrations, and restarts that keep admin edits.

Runs on SQLite; set MIGRATION_TEST_DATABASE_URL to an *empty scratch* PostgreSQL
database (it is wiped) to run the same checks on PostgreSQL.
"""
import os

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

alembic = pytest.importorskip("alembic")
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402

from jubran.domain.enums import TableShape  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402

from jubran.infrastructure.db.migrations.runner import alembic_config, migrate_database  # noqa: E402
from jubran.infrastructure.db.models import (  # noqa: E402
    MenuCategoryModel, OpeningHourModel, PhysicalTableModel,
)
from jubran.infrastructure.db.seed import seed_database  # noqa: E402
from jubran.infrastructure.db.session import Base  # noqa: E402

HEAD = ScriptDirectory.from_config(alembic_config()).get_current_head()
TARGETS = ["sqlite"] + (["postgresql"] if os.getenv("MIGRATION_TEST_DATABASE_URL") else [])


@pytest_asyncio.fixture(params=TARGETS)
async def engine(request, tmp_path):
    if request.param == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}")
    else:
        engine = create_async_engine(os.environ["MIGRATION_TEST_DATABASE_URL"])
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            for enum in ("userrole", "tableshape", "tablesessionstatus", "complaintstatus", "draftstatus",
                         "orderstatus", "servicerequesttype", "servicerequeststatus"):
                await connection.execute(text(f"DROP TYPE IF EXISTS {enum}"))
    yield engine
    await engine.dispose()


async def schema_differences(engine):
    def diff(sync_connection):
        context = MigrationContext.configure(sync_connection, opts={"compare_type": False})
        return compare_metadata(context, Base.metadata)
    async with engine.connect() as connection:
        return await connection.run_sync(diff)


async def version(engine):
    async with engine.connect() as connection:
        return (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()


def sessions(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_fresh_database_gets_the_whole_schema(engine):
    assert await migrate_database(engine) == "empty"
    assert await version(engine) == HEAD
    assert await schema_differences(engine) == []
    assert await migrate_database(engine) == "versioned"  # nothing left to do the second time


@pytest.mark.asyncio
async def test_database_from_before_migrations_is_bridged_without_losing_edits(engine):
    # A database built by the old start-up routine, missing what was added later.
    async with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("DROP TABLE assistant_conversations"))
    async with sessions(engine)() as db:
        await seed_database(db)
        await db.execute(update(PhysicalTableModel).where(PhysicalTableModel.table_number == "T1")
                         .values(shape=TableShape.SQUARE, seat_count=6, x_percent=50.0, y_percent=40.0))
        await db.commit()

    assert await migrate_database(engine) == "legacy"
    assert await version(engine) == HEAD
    assert await schema_differences(engine) == []
    async with sessions(engine)() as db:
        t1 = (await db.execute(select(PhysicalTableModel).where(PhysicalTableModel.table_number == "T1"))).scalar_one()
    # The old start-up reset every table to round/4 seats and moved T1 back; not any more.
    assert (t1.shape, t1.seat_count, t1.x_percent, t1.y_percent) == (TableShape.SQUARE, 6, 50.0, 40.0)


@pytest.mark.asyncio
async def test_restarts_keep_every_admin_edit(engine):
    await migrate_database(engine)
    async with sessions(engine)() as db:
        await seed_database(db)
        await db.execute(update(PhysicalTableModel).where(PhysicalTableModel.table_number == "T1")
                         .values(shape=TableShape.RECTANGLE, seat_count=8, x_percent=20.0))
        await db.execute(update(MenuCategoryModel).where(MenuCategoryModel.sort_order == 3).values(is_active=False))
        await db.execute(update(OpeningHourModel).where(OpeningHourModel.day_of_week == 5)
                         .values(opens_at="12:00", notes_ar="الجمعة بعد الصلاة"))
        await db.commit()

    for _ in range(2):  # two restarts
        assert await migrate_database(engine) == "versioned"
        async with sessions(engine)() as db:
            await seed_database(db)

    async with sessions(engine)() as db:
        t1 = (await db.execute(select(PhysicalTableModel).where(PhysicalTableModel.table_number == "T1"))).scalar_one()
        drinks = (await db.execute(select(MenuCategoryModel).where(MenuCategoryModel.sort_order == 3))).scalar_one()
        fridays = (await db.execute(select(OpeningHourModel).where(OpeningHourModel.day_of_week == 5))).scalars().all()
    assert (t1.shape, t1.seat_count, t1.x_percent) == (TableShape.RECTANGLE, 8, 20.0)
    assert drinks.is_active is False
    assert (friday.opens_at, friday.notes_ar) == ("12:00", "الجمعة بعد الصلاة")


@pytest.mark.asyncio
async def test_old_gemini_live_voice_settings_are_removed(engine):
    from jubran.infrastructure.db.models import AiModelConfigModel
    await migrate_database(engine)
    async with sessions(engine)() as db:
        db.add_all([AiModelConfigModel(purpose="voice", provider="gemini", model_id="gemini-3.8-live", is_active=False),
                    AiModelConfigModel(purpose="chat", provider="gemini", model_id="gemini-chat", is_active=True)])
        await db.commit()
    async with engine.begin() as connection:  # as if the database were one migration behind
        await connection.execute(text("UPDATE alembic_version SET version_num = '0004_ai_purposes'"))
    assert await migrate_database(engine) == "versioned"
    async with sessions(engine)() as db:
        left = (await db.execute(select(AiModelConfigModel.purpose, AiModelConfigModel.provider))).all()
    assert left == [("chat", "gemini")]
