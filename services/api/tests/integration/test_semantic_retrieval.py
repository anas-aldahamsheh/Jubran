"""Semantic knowledge retrieval uses vectors and hydrates live domain facts."""
import pytest

from jubran.application.ai.semantic_retrieval import EmbeddingService, SemanticKnowledgeService
from jubran.infrastructure.db.models import EMBEDDING_DIMENSIONS
from jubran.infrastructure.db.seed import seed_database
from jubran.settings import settings


def vector(axis: int) -> list[float]:
    value = [0.0] * EMBEDDING_DIMENSIONS
    value[axis] = 1.0
    return value


@pytest.mark.asyncio
async def test_vector_meaning_finds_product_without_text_match(db_session, monkeypatch):
    await seed_database(db_session)
    monkeypatch.setattr(settings, "SEMANTIC_MIN_SIMILARITY", 0.5)

    async def controlled_embeddings(cls, db, texts, *, task_type):
        results = []
        for text in texts:
            # Only the plain hummus dish (and the question) share a meaning here, so there are no ties.
            if text.startswith("منتج من المنيو: حمص.") or "creamy levantine dip" in text:
                results.append(vector(0))
            else:
                results.append(vector(1))
        return results

    monkeypatch.setattr(EmbeddingService, "embed", classmethod(controlled_embeddings))
    result = await SemanticKnowledgeService.search(
        db_session,
        query="creamy levantine dip",
        source_types=["product"],
        limit=3,
    )

    assert result["matches"]
    assert result["matches"][0]["product"]["name_ar"] == "حمص"
    assert result["matches"][0]["match_type"] == "meaning"  # found by meaning, not by name


@pytest.mark.asyncio
async def test_unrelated_vector_returns_no_reliable_match(db_session, monkeypatch):
    await seed_database(db_session)
    monkeypatch.setattr(settings, "SEMANTIC_MIN_SIMILARITY", 0.5)

    async def unrelated_embeddings(cls, db, texts, *, task_type):
        return [vector(2) if text == "gaming laptop hardware" else vector(1) for text in texts]

    monkeypatch.setattr(EmbeddingService, "embed", classmethod(unrelated_embeddings))
    result = await SemanticKnowledgeService.search(
        db_session,
        query="gaming laptop hardware",
        source_types=["product"],
    )

    assert result["matches"] == []
    assert result["no_reliable_match"] is True

