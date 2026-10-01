"""Pytest configuration and fixtures."""
import os

# Must be set before the app (and its settings) are imported: tests run with the
# local demo accounts and development secrets, never in production mode.
os.environ["ENVIRONMENT"] = "test"

import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from jubran.main import app
from jubran.infrastructure.db.session import Base, get_db_session
from helpers import BrowserClient

# SQLite in memory by default; set TEST_DATABASE_URL to run the same tests on
# PostgreSQL (with pgvector), e.g. postgresql+asyncpg://user:pass@localhost:5433/jubran_test
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
IS_POSTGRES = TEST_DB_URL.startswith("postgresql")

test_engine = create_async_engine(TEST_DB_URL, echo=False)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def init_test_db():
    async with test_engine.begin() as conn:
        if IS_POSTGRES:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture(autouse=True)
def fresh_embedding_cache():
    """Each test starts without remembered vectors, like a fresh server. Live model runs keep
    them: the same text always gets the same vector, and it saves the provider's quota."""
    from jubran.application.ai.semantic_retrieval import EmbeddingService
    if os.getenv("AI_LIVE_TEST") != "1" and os.getenv("GEMINI_LIVE_TEST") != "1":
        EmbeddingService._cache.clear()


@pytest.fixture(autouse=True)
def media_in_a_temporary_folder(monkeypatch, tmp_path):
    """Uploaded photos go to a per-test folder, never into the project."""
    from jubran.settings import settings
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path / "media"))


@pytest.fixture(autouse=True)
def disable_external_gemini_quota_in_standard_tests(monkeypatch):
    """Keep ordinary tests offline; opt-in live checks may use the configured key."""
    from jubran.settings import settings
    if os.getenv("AI_LIVE_TEST") != "1" and os.getenv("GEMINI_LIVE_TEST") != "1":
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
        monkeypatch.setattr(settings, "SEMANTIC_MIN_SIMILARITY", 0.0)

        from jubran.application.ai.semantic_retrieval import EmbeddingService, SemanticKnowledgeService
        from jubran.infrastructure.db.models import EMBEDDING_DIMENSIONS

        async def offline_embeddings(cls, db, texts, *, task_type):
            # Provider-free transport stub. Production never uses this; it only
            # gives integration tests stable similarity ordering.
            import hashlib
            import math
            import re

            vectors = []
            for value in texts:
                normalized = re.sub(r"\s+", " ", value.casefold()).strip()
                tokens = normalized.split()
                tokens.extend(normalized[index:index + 3] for index in range(max(0, len(normalized) - 2)))
                vector = [0.0] * EMBEDDING_DIMENSIONS
                for token in tokens:
                    index = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:4], "big") % EMBEDDING_DIMENSIONS
                    vector[index] += 1.0
                norm = math.sqrt(sum(item * item for item in vector)) or 1.0
                vectors.append([item / norm for item in vector])
            return vectors

        monkeypatch.setattr(EmbeddingService, "embed", classmethod(offline_embeddings))
        monkeypatch.setattr(SemanticKnowledgeService, "SOURCE_THRESHOLDS", {
            "category": 0.0, "restaurant": 0.0, "branch": 0.0,
            "service": 0.0, "policy": 0.0,
        })


@pytest_asyncio.fixture
async def db_session():
    async with test_session_factory() as session:
        yield session
        await session.rollback()


def _browser() -> BrowserClient:
    return BrowserClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def client(db_session):
    """One browser: its own cookie jar, CSRF header on writes (like the web app)."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db
    async with _browser() as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def new_browser(client):
    """Open more independent browsers (for example an admin and a guest at once)."""
    opened = []

    def _open() -> BrowserClient:
        browser = _browser()
        opened.append(browser)
        return browser

    yield _open
    for browser in opened:
        await browser.aclose()
