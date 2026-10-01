"""Existing AI model tables gain new nullable settings without losing records."""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from jubran.infrastructure.db.schema_upgrade import ensure_ai_model_columns, upgrade_table_qr_tokens


@pytest.mark.asyncio
async def test_existing_model_table_gets_transcription_column():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE ai_model_configs (id VARCHAR(36) PRIMARY KEY)"))
            await connection.execute(text("INSERT INTO ai_model_configs (id) VALUES ('existing-model')"))
            await ensure_ai_model_columns(connection)
            await ensure_ai_model_columns(connection)
            columns = await connection.run_sync(
                lambda sync_connection: {item["name"] for item in inspect(sync_connection).get_columns("ai_model_configs")}
            )
            assert "transcription_model_id" in columns
            assert (await connection.execute(text("SELECT id FROM ai_model_configs"))).scalar_one() == "existing-model"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_unrecoverable_qr_tokens_are_retired():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text(
                "CREATE TABLE table_qr_tokens (id VARCHAR(36) PRIMARY KEY, physical_table_id VARCHAR(36), "
                "token_hash VARCHAR(64), is_active BOOLEAN, created_at TIMESTAMP, revoked_at TIMESTAMP)"
            ))
            await connection.execute(text(
                "INSERT INTO table_qr_tokens (id, physical_table_id, token_hash, is_active) "
                "VALUES ('legacy', 'table-1', 'hash-of-qr-t1-jubran', 1)"
            ))
            await upgrade_table_qr_tokens(connection)
            await upgrade_table_qr_tokens(connection)
            row = (await connection.execute(text(
                "SELECT is_active, revoked_at, token_ciphertext FROM table_qr_tokens WHERE id = 'legacy'"
            ))).one()
            assert row.is_active == 0 and row.revoked_at is not None and row.token_ciphertext is None
            # Only one active QR per table is allowed from now on.
            await connection.execute(text(
                "INSERT INTO table_qr_tokens (id, physical_table_id, token_hash, token_ciphertext, is_active) "
                "VALUES ('new-1', 'table-1', 'h1', 'c1', 1)"
            ))
            with pytest.raises(Exception):
                await connection.execute(text(
                    "INSERT INTO table_qr_tokens (id, physical_table_id, token_hash, token_ciphertext, is_active) "
                    "VALUES ('new-2', 'table-1', 'h2', 'c2', 1)"
                ))
    finally:
        await engine.dispose()
