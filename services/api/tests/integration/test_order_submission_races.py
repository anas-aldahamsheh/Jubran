"""Two submits of the same order can never both reach the kitchen.

A second request is made to finish *inside* the first one, right between the
first request reading the confirmation and claiming it: exactly the window a
real race hits. Each request uses its own database connection (file database).
"""
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jubran.application.order_numbers import next_order_number
from jubran.application.ordering_service import OrderingService
from jubran.application.qr_service import QrService
from jubran.application.session_service import SessionService
from jubran.domain.exceptions import InvalidConfirmationTokenException
from jubran.infrastructure.db.models import OrderModel, PhysicalTableModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.db.session import Base


@pytest_asyncio.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}", connect_args={"timeout": 30})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await seed_database(db)
    yield factory
    await engine.dispose()


async def confirmed_basket(factory, table_number="T3"):
    async with factory() as db:
        table = (await db.execute(select(PhysicalTableModel).where(PhysicalTableModel.table_number == table_number))).scalar_one()
        qr = (await QrService.create_table_qr(db, table.id))["qr_token"]
        _, customer, _ = await SessionService.start_or_resume_session(db, qr)
        product = (await db.execute(select(ProductModel).where(ProductModel.is_available == True))).scalars().first()  # noqa: E712
        await OrderingService.add_item_to_draft(db, customer.id, customer.table_session_id, product.id, 2)
        prepared = await OrderingService.prepare_confirmation(db, customer.id, customer.table_session_id)
        return customer.id, customer.table_session_id, prepared


def rival_runs_before_claim(db, rival):
    """Make `rival` finish right before this session claims the confirmation."""
    original_execute = db.execute
    state = {"done": False}

    async def execute(statement, *args, **kwargs):
        if not state["done"] and str(statement).startswith("UPDATE draft_confirmations"):
            state["done"] = True
            await rival()
        return await original_execute(statement, *args, **kwargs)

    db.execute = execute


async def count_orders(factory):
    async with factory() as db:
        return (await db.execute(select(func.count(OrderModel.id)))).scalar()


@pytest.mark.asyncio
async def test_double_submit_of_one_confirmation_creates_one_order(sessions):
    customer_id, visit_id, prepared = await confirmed_basket(sessions)
    submit = dict(customer_session_id=customer_id, table_session_id=visit_id,
                  confirmation_token=prepared["confirmation_token"], draft_version=prepared["draft_version"])
    rival_result = {}

    async def rival():
        async with sessions() as rival_db:
            rival_result["order"] = await OrderingService.submit_order(rival_db, **submit)

    async with sessions() as db:
        rival_runs_before_claim(db, rival)
        with pytest.raises(InvalidConfirmationTokenException):
            await OrderingService.submit_order(db, **submit)

    assert rival_result["order"]["order_number"] == "JB-101"
    assert await count_orders(sessions) == 1


@pytest.mark.asyncio
async def test_retry_with_the_same_key_returns_the_first_order(sessions):
    customer_id, visit_id, prepared = await confirmed_basket(sessions)
    submit = dict(customer_session_id=customer_id, table_session_id=visit_id,
                  confirmation_token=prepared["confirmation_token"], draft_version=prepared["draft_version"],
                  idempotency_key="tap-twice")
    rival_result = {}

    async def rival():
        async with sessions() as rival_db:
            rival_result["order"] = await OrderingService.submit_order(rival_db, **submit)

    async with sessions() as db:
        rival_runs_before_claim(db, rival)
        first = await OrderingService.submit_order(db, **submit)

    assert first == rival_result["order"]
    assert await count_orders(sessions) == 1


@pytest.mark.asyncio
async def test_order_numbers_are_unique_and_continue_after_existing_ones(sessions):
    numbers = []
    for table in ("T1", "T2", "T4"):
        customer_id, visit_id, prepared = await confirmed_basket(sessions, table)
        async with sessions() as db:
            order = await OrderingService.submit_order(db, customer_id, visit_id,
                                                       prepared["confirmation_token"], prepared["draft_version"])
            numbers.append(order["order_number"])
    assert numbers == ["JB-101", "JB-102", "JB-103"]


@pytest.mark.asyncio
async def test_counter_starts_after_orders_created_before_the_upgrade(sessions):
    customer_id, visit_id, prepared = await confirmed_basket(sessions)
    async with sessions() as db:
        db.add(OrderModel(table_session_id=visit_id, customer_session_id=customer_id, order_number="JB-157"))
        await db.commit()
        assert await next_order_number(db) == "JB-158"
        assert await next_order_number(db) == "JB-159"


@pytest.mark.asyncio
async def test_expired_retry_keys_are_cleaned_up(sessions):
    from datetime import datetime, timedelta, timezone
    from jubran.application.maintenance import purge_expired_records
    from jubran.infrastructure.db.models import IdempotencyRecordModel

    async with sessions() as db:
        now = datetime.now(timezone.utc)
        db.add_all([
            IdempotencyRecordModel(key_hash="old", operation_type="submit_order", scope_id="x",
                                   response_payload="{}", expires_at=now - timedelta(minutes=1)),
            IdempotencyRecordModel(key_hash="fresh", operation_type="submit_order", scope_id="x",
                                   response_payload="{}", expires_at=now + timedelta(hours=1)),
        ])
        await db.commit()
        await purge_expired_records(db)
        keys = (await db.execute(select(IdempotencyRecordModel.key_hash))).scalars().all()
    assert keys == ["fresh"]
