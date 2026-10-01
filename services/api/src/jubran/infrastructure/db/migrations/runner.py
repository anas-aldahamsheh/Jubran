"""Bring the database schema up to date (Alembic), safely and exactly once.

Called by the server at start-up and by ``init_db``. Several workers may start
together: on PostgreSQL an advisory lock makes them migrate one after another.
A database created before migrations existed is first brought to the baseline
schema by the legacy bridge and stamped, then upgraded like any other.
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from jubran.infrastructure.db.schema_upgrade import bring_legacy_schema_to_baseline

BASELINE_REVISION = "0001_baseline"
_MIGRATION_LOCK = 1642076132


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parent))
    return config


def _upgrade(sync_connection, stamp_baseline: bool) -> None:
    config = alembic_config()
    config.attributes["connection"] = sync_connection
    if stamp_baseline:
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")


def _state(sync_connection) -> str:
    tables = set(inspect(sync_connection).get_table_names())
    if "alembic_version" in tables:
        return "versioned"
    return "legacy" if "users" in tables else "empty"


async def migrate_database(engine: AsyncEngine) -> str:
    """Apply pending migrations; returns what the database was before ("empty", "legacy", "versioned")."""
    async with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            await connection.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": _MIGRATION_LOCK})
        state = await connection.run_sync(_state)
        if state == "legacy":
            await bring_legacy_schema_to_baseline(connection)
        await connection.run_sync(_upgrade, state == "legacy")
    return state
