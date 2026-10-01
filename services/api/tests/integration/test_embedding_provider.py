"""Menu search has its own settings section: provider, model and key, independent of the chat."""
import pytest
from sqlalchemy import select, update

from jubran.application.ai.model_config_service import ModelConfigService
from jubran.application.ai.semantic_retrieval import EmbeddingService, SemanticKnowledgeService
from jubran.application.ai.tool_views import model_view
from jubran.infrastructure.db.models import EMBEDDING_DIMENSIONS, KnowledgeDocumentModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings
from helpers import sign_in

REAL_EMBED = EmbeddingService.__dict__["embed"]  # captured before the offline test stub replaces it


def unit_vector(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[sum(map(ord, text)) % EMBEDDING_DIMENSIONS] = 1.0
    return vector


def fake_providers(monkeypatch):
    calls = []

    async def openai(api_key, model, texts):
        calls.append(("openai", api_key, model))
        return [unit_vector(t) for t in texts]

    async def gemini(api_key, model, texts, task_type):
        calls.append(("gemini", api_key, model))
        return [unit_vector(t) for t in texts]

    monkeypatch.setattr(EmbeddingService, "embed", REAL_EMBED)
    monkeypatch.setattr(EmbeddingService, "_openai_vectors", staticmethod(openai))
    monkeypatch.setattr(EmbeddingService, "_gemini_vectors", staticmethod(gemini))
    return calls


async def menu_search(db_session, provider, key, model=None):
    """Save the menu-search section, as the administrator does on the settings page."""
    model = model or (settings.OPENAI_EMBEDDING_MODEL if provider == "openai" else settings.GEMINI_EMBEDDING_MODEL)
    await ModelConfigService.save(db_session, "embedding", provider, model, key)


@pytest.mark.asyncio
async def test_openai_only_setup_can_search_the_menu(db_session, monkeypatch):
    await seed_database(db_session)
    calls = fake_providers(monkeypatch)
    await menu_search(db_session, "openai", "sk-admin")  # no Gemini key anywhere

    result = await SemanticKnowledgeService.search(db_session, "حمص", source_types=["product"])
    assert result["success"] and result["matches"]
    assert {call[:2] for call in calls} == {("openai", "sk-admin")}
    assert result["embedding_model"] == f"openai:{settings.OPENAI_EMBEDDING_MODEL}"


@pytest.mark.asyncio
async def test_the_saved_key_wins_over_the_server_key(db_session, monkeypatch):
    await seed_database(db_session)
    calls = fake_providers(monkeypatch)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "server-env-key")
    await menu_search(db_session, "gemini", "admin-saved-key")

    await SemanticKnowledgeService.search(db_session, "فول", source_types=["product"])
    assert {call[:2] for call in calls} == {("gemini", "admin-saved-key")}


@pytest.mark.asyncio
async def test_menu_search_does_not_borrow_the_chat_key(db_session, monkeypatch):
    await seed_database(db_session)
    calls = fake_providers(monkeypatch)
    await ModelConfigService.save(db_session, "chat", "gemini", "gemini-chat", "chat-key")

    found = await SemanticKnowledgeService.search(db_session, "بدي شقف", source_types=["product"])
    assert calls == [] and found["retrieval"] == "name_only"  # named dishes are still found
    assert [m["product"]["name_ar"] for m in found["matches"]] == ["شقف"]


@pytest.mark.asyncio
async def test_switching_provider_or_model_rebuilds_the_index(db_session, monkeypatch):
    await seed_database(db_session)
    fake_providers(monkeypatch)
    await menu_search(db_session, "gemini", "g-key")
    await SemanticKnowledgeService.search(db_session, "شاي", source_types=["product"])

    await menu_search(db_session, "openai", "o-key")  # the admin switches provider
    await SemanticKnowledgeService.search(db_session, "شاي", source_types=["product"])
    models = set((await db_session.execute(select(KnowledgeDocumentModel.embedding_model))).scalars())
    assert models == {f"openai:{settings.OPENAI_EMBEDDING_MODEL}"}

    await menu_search(db_session, "openai", None, model="text-embedding-3-large")  # same key, new model
    await SemanticKnowledgeService.search(db_session, "شاي", source_types=["product"])
    models = set((await db_session.execute(select(KnowledgeDocumentModel.embedding_model))).scalars())
    assert models == {"openai:text-embedding-3-large"}
    assert await SemanticKnowledgeService.indexed_count(db_session) > 0


@pytest.mark.asyncio
async def test_settings_page_shows_what_menu_search_runs_on(client, db_session, monkeypatch):
    await seed_database(db_session)
    await sign_in(client)
    embedding = (await client.get("/api/v1/admin/ai-models")).json()["sections"][1]
    assert embedding["purpose"] == "embedding" and embedding["in_use"]["source"] == "none"  # no key anywhere

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "server-env-key")
    embedding = (await client.get("/api/v1/admin/ai-models")).json()["sections"][1]
    assert embedding["in_use"] == {"source": "default", "provider": "gemini",
                                   "model_id": settings.GEMINI_EMBEDDING_MODEL, "key": "server"}

    fake_providers(monkeypatch)
    saved = await client.put("/api/v1/admin/ai-models/embedding",
                             json={"provider": "openai", "model_id": "text-embedding-3-small", "api_key": "sk-admin"})
    assert saved.json()["in_use"] == {"source": "saved", "provider": "openai",
                                      "model_id": "text-embedding-3-small", "key": "saved"}
    assert saved.json()["indexed_documents"] > 0  # saving indexes the menu right away


@pytest.mark.asyncio
async def test_named_dishes_are_still_found_when_the_provider_is_down(db_session, monkeypatch):
    from jubran.application.ai.semantic_retrieval import SemanticRetrievalError
    await seed_database(db_session)

    async def provider_down(cls, db, texts, *, task_type):
        raise SemanticRetrievalError("EMBEDDING_PROVIDER_UNAVAILABLE")

    monkeypatch.setattr(EmbeddingService, "embed", classmethod(provider_down))
    found = await SemanticKnowledgeService.search(db_session, "بدي شقف", source_types=["product"])
    assert found["retrieval"] == "name_only"
    assert [m["product"]["name_ar"] for m in found["matches"]] == ["شقف"]

    # Without a name to go on, the guest's turn still goes on: the menu is listed (live, in menu
    # order) for the model to choose from, and the model is told the list is not ranked.
    await db_session.execute(update(ProductModel).where(ProductModel.name_ar == "فلافل").values(is_available=False))
    await db_session.commit()
    listed = await SemanticKnowledgeService.search(db_session, "شي خفيف للفطور", source_types=["product"],
                                                   available_only=True)
    names = [m["product"]["name_ar"] for m in listed["matches"]]
    assert listed["retrieval"] == "listing" and "فول" in names and "فلافل" not in names
    assert "not ranked" in model_view("search_knowledge", listed)["note"]


@pytest.mark.asyncio
async def test_repeated_texts_reuse_their_vectors(db_session, monkeypatch):
    await seed_database(db_session)
    calls = fake_providers(monkeypatch)
    await menu_search(db_session, "gemini", "g-key")
    await SemanticKnowledgeService.search(db_session, "شاي", source_types=["product"])
    asked = len(calls)
    await SemanticKnowledgeService.search(db_session, "شاي", source_types=["product"])
    assert asked and len(calls) == asked  # the same question is not sent to the provider again


@pytest.mark.asyncio
async def test_a_large_menu_is_embedded_in_batches_the_provider_accepts(monkeypatch):
    sizes = []

    async def gemini(cls, api_key, model, texts, task_type):
        sizes.append(len(texts))
        return [[0.0] * EMBEDDING_DIMENSIONS for _ in texts]

    monkeypatch.setattr(EmbeddingService, "_gemini_vectors", classmethod(gemini))
    vectors = await EmbeddingService.provider_vectors("gemini", "key", "gemini-embedding-001",
                                                      [f"dish {i}" for i in range(230)], "RETRIEVAL_DOCUMENT")
    assert len(vectors) == 230 and sizes == [100, 100, 30]
