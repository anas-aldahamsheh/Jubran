"""The assistant's memory lives in the database: restarts and extra workers lose nothing."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jubran.application.ai import agent_service
from jubran.application.ai.conversation_store import ConversationStore
from jubran.application.maintenance import purge_expired_records
from jubran.domain.exceptions import BusinessRuleError
from jubran.infrastructure.db.models import (
    AssistantConversationModel, CustomerSessionModel, OrderModel, ProductModel,
)
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.db.session import Base
from helpers import sign_in, start_visit
from test_model_agent import model_call, model_response, script_model, summary_calls


async def product_named(db_session, name="شاي"):
    return (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == name))).scalar_one()


def prepare_then_confirm_script(monkeypatch, product):
    return script_model(monkeypatch,
        [model_response(model_call("search_knowledge", query=product.name_ar, source_types=["product"])),
         model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": product.id, "quantity": 1}])),
         *summary_calls(),
         model_response(text="هذا ملخص طلبك. هل تؤكد؟")],
        [model_response(model_call("submit_order", confirmed_summary=True, guest_words="نعم أكد")),
         model_response(text="تم.")],
    )


async def conversation_row(db_session, customer_id):
    db_session.expire_all()
    return (await db_session.execute(select(AssistantConversationModel).where(
        AssistantConversationModel.customer_session_id == customer_id))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_pending_summary_is_stored_safely_and_confirmed_by_any_worker(client, db_session, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    customer_id = (await client.get("/api/v1/session/context")).json()["customer_session_id"]
    product = await product_named(db_session)
    prepare_then_confirm_script(monkeypatch, product)

    prepared = await client.post("/api/v1/assistant/chat", json={"message": "بدي شاي وخلص"})
    assert prepared.json()["action"]["type"] == "AWAITING_ORDER_CONFIRMATION"

    # Nothing is kept in the server process any more...
    assert not any(hasattr(agent_service, name) for name in ("_PENDING", "_SESSION_MEMORY", "_SUGGESTED", "_LOCKS"))
    # ...the database holds it, with the order token encrypted.
    row = await conversation_row(db_session, customer_id)
    assert row.pending_token_ciphertext and row.pending_draft_version and row.busy_lease is None
    assert "شاي" in row.messages_json

    confirmed = await client.post("/api/v1/assistant/chat", json={"message": "نعم أكد"})
    assert confirmed.json()["action"]["type"] == "ORDER_SUBMITTED"
    assert len((await db_session.execute(select(OrderModel))).scalars().all()) == 1
    assert (await conversation_row(db_session, customer_id)).pending_token_ciphertext is None


@pytest.mark.asyncio
async def test_history_clearing_and_end_of_visit(client, db_session, new_browser, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    context = (await client.get("/api/v1/session/context")).json()
    script_model(monkeypatch, [model_response(text="أهلاً! كيف بقدر أساعدك؟")])
    await client.post("/api/v1/assistant/chat", json={"message": "مرحبا"})

    history = (await client.get("/api/v1/assistant/history")).json()["history"]
    assert [m["role"] for m in history] == ["user", "assistant"]

    assert (await client.post("/api/v1/assistant/clear-history")).status_code == 200
    assert (await client.get("/api/v1/assistant/history")).json()["history"] == []

    # Closing the table forgets the conversation entirely.
    await ConversationStore.save(db_session, (await ConversationStore.load(db_session, context["customer_session_id"])))
    admin = new_browser()
    await sign_in(admin)
    assert (await admin.post(f"/api/v1/admin/table-sessions/{context['table_session_id']}/close")).status_code == 200
    assert await conversation_row(db_session, context["customer_session_id"]) is None


@pytest.mark.asyncio
async def test_leftover_conversations_are_purged(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    customer_id = (await client.get("/api/v1/session/context")).json()["customer_session_id"]
    await ConversationStore.save(db_session, await ConversationStore.load(db_session, customer_id))

    await purge_expired_records(db_session)
    assert await conversation_row(db_session, customer_id) is not None  # visit still going

    await db_session.execute(update(CustomerSessionModel).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    await db_session.commit()
    await purge_expired_records(db_session)
    assert await conversation_row(db_session, customer_id) is None


@pytest_asyncio.fixture
async def two_workers(tmp_path):
    """Two independent database connections, like two server workers."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'workers.db'}", connect_args={"timeout": 30})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await seed_database(db)
        from helpers import issue_table_qr
        from jubran.application.session_service import SessionService
        _, customer, _ = await SessionService.start_or_resume_session(db, await issue_table_qr(db, "T2"))
        customer_id = customer.id
    async with factory() as first, factory() as second:
        yield first, second, customer_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_one_turn_at_a_time_per_guest_across_workers(two_workers):
    first, second, customer_id = two_workers
    lease = await ConversationStore.acquire(first, customer_id)

    with pytest.raises(BusinessRuleError) as busy:
        await ConversationStore.acquire(second, customer_id, wait_seconds=0.3)
    assert busy.value.code == "ASSISTANT_BUSY" and busy.value.http_status == 409

    # A waiting message goes through as soon as the previous turn ends.
    async def finish_first_turn_soon():
        await asyncio.sleep(0.3)
        await ConversationStore.release(first, customer_id, lease)

    releasing = asyncio.create_task(finish_first_turn_soon())
    second_lease = await ConversationStore.acquire(second, customer_id, wait_seconds=5)
    await releasing
    assert second_lease != lease

    # A worker that died mid-turn does not block the guest forever.
    await second.execute(update(AssistantConversationModel).values(
        busy_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await second.commit()
    assert await ConversationStore.acquire(first, customer_id, wait_seconds=0)
