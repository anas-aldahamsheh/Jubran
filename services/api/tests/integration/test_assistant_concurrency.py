"""Chats never queue behind each other: no global re-indexing per search, no open transaction during model calls."""
import pytest
from sqlalchemy import select

from jubran.application.ai.agent_service import AssistantService
from jubran.application.ai.semantic_retrieval import SemanticKnowledgeService
from jubran.application.ai.tools import AssistantToolExecutor
from jubran.infrastructure.db.models import CustomerSessionModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import sign_in, start_visit
from test_model_agent import model_call, model_response


def count_index_builds(monkeypatch):
    builds = []
    original = SemanticKnowledgeService.sync.__func__

    async def counted(cls, db):
        builds.append(1)
        await original(cls, db)

    monkeypatch.setattr(SemanticKnowledgeService, "sync", classmethod(counted))
    return builds


@pytest.mark.asyncio
async def test_searches_reuse_the_index_until_the_admin_changes_something(client, db_session, new_browser, monkeypatch):
    builds = count_index_builds(monkeypatch)
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    tools = AssistantToolExecutor(db_session, customer.id, customer.table_session_id, "T3")

    version_before = (await client.get("/api/v1/assistant/history")).json()["content_version"]
    for query in ("حمص", "شاي", "أوقات الدوام"):
        assert (await tools.execute("search_knowledge", {"query": query}))["success"]
    assert len(builds) == 1  # built once, then reused

    admin = new_browser()
    await sign_in(admin)
    tea = (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == "شاي"))).scalar_one()
    renamed = await admin.patch(f"/api/v1/admin/menu/products/{tea.id}", json={"description_ar": "شاي بالقرنفل"})
    assert renamed.status_code == 200
    assert len(builds) == 2  # re-indexed right after the change, in the admin's request

    found = await tools.execute("search_knowledge", {"query": "قرنفل", "source_types": ["product"]})
    assert any(m["product"]["id"] == tea.id for m in found["matches"])
    assert len(builds) == 2  # guests' searches did no indexing work
    version_after = (await client.get("/api/v1/assistant/history")).json()["content_version"]
    assert version_after != version_before and version_after.isdigit()


@pytest.mark.asyncio
async def test_no_transaction_or_lock_is_held_while_the_model_thinks(client, db_session, monkeypatch):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    tea = (await db_session.execute(select(ProductModel).where(ProductModel.name_ar == "شاي"))).scalar_one()
    replies = iter([
        model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
        model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": tea.id, "quantity": 1}])),
        model_response(model_call("get_current_draft")),
        model_response(text="أضفت الشاي."),
    ])
    open_during_model_calls = []

    class WatchingChat:
        async def send_message(self, message, context=None):
            open_during_model_calls.append(db_session.in_transaction())
            return next(replies)

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-test-key")
    monkeypatch.setattr(AssistantService, "_create_chat", classmethod(lambda cls, *args, **kwargs: WatchingChat()))
    response = await client.post("/api/v1/assistant/chat", json={"message": "بدي شاي"})
    assert response.status_code == 200, response.text
    assert response.json()["draft"]["item_count"] == 1
    assert open_during_model_calls == [False, False, False, False]
