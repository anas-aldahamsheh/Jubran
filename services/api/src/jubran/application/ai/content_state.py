"""A cheap version number for the restaurant and menu facts the assistant knows.

Every admin change to the menu or the restaurant profile increments
``content_version`` in the same transaction. The assistant's search index records
the version it was built from (``knowledge_indexed_version``), so a search only
has to compare two small numbers instead of re-reading and hashing every table.
"""
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.db.models import CounterModel

CONTENT_VERSION = "content_version"
INDEXED_VERSION = "knowledge_indexed_version"
# A database that never recorded a version counts as version 1 with nothing indexed.
_DEFAULTS = {CONTENT_VERSION: 1, INDEXED_VERSION: 0}


def _insert(db: AsyncSession):
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    return pg_insert if dialect == "postgresql" else sqlite_insert


async def _read(db: AsyncSession, name: str) -> int:
    value = (await db.execute(select(CounterModel.value).where(CounterModel.name == name))).scalar_one_or_none()
    return _DEFAULTS[name] if value is None else value


async def content_version(db: AsyncSession) -> str:
    """Opaque version string the web app compares to notice menu changes."""
    return str(await _read(db, CONTENT_VERSION))


async def read_versions(db: AsyncSession) -> tuple[int, int]:
    return await _read(db, CONTENT_VERSION), await _read(db, INDEXED_VERSION)


async def bump_content_version(db: AsyncSession) -> None:
    """Call inside the transaction that changes menu or restaurant facts (the caller commits)."""
    stmt = _insert(db)(CounterModel).values(name=CONTENT_VERSION, value=_DEFAULTS[CONTENT_VERSION] + 1)
    stmt = stmt.on_conflict_do_update(index_elements=[CounterModel.name],
                                      set_={"value": CounterModel.value + 1})
    await db.execute(stmt)


async def mark_indexed(db: AsyncSession, version: int) -> None:
    """Record that the search index reflects ``version`` (never moves backwards)."""
    stmt = _insert(db)(CounterModel).values(name=INDEXED_VERSION, value=version)
    current = CounterModel.value
    stmt = stmt.on_conflict_do_update(
        index_elements=[CounterModel.name],
        set_={"value": _greatest(db, current, stmt.excluded.value)},
    )
    await db.execute(stmt)
    await db.commit()


def _greatest(db: AsyncSession, left, right):
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    return func.greatest(left, right) if dialect == "postgresql" else func.max(left, right)
