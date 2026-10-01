"""One-time bridge for databases created before migrations existed.

Those databases were built by the old start-up routine (create_all plus the
idempotent upgrades below, run at every start). The migration runner calls
``bring_legacy_schema_to_baseline`` once for such a database, then stamps it
with the baseline revision; from then on only Alembic migrations change it.

Only schema upgrades and one-time clean-ups live here. The old start-up
"patches" that reset every table to round/4 seats and moved table T1 back were
removed: they undid the administrator's floor-plan edits on every restart.
"""
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_ai_model_columns(connection: AsyncConnection) -> None:
    def missing_transcription_column(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        if not inspector.has_table("ai_model_configs"):
            return False
        return "transcription_model_id" not in {
            column["name"] for column in inspector.get_columns("ai_model_configs")
        }

    if await connection.run_sync(missing_transcription_column):
        await connection.execute(text(
            "ALTER TABLE ai_model_configs ADD COLUMN transcription_model_id VARCHAR(150)"
        ))


async def remove_retired_knowledge_table(connection: AsyncConnection) -> None:
    """Retire the old chunk design and rebuild an incompatible derived index."""
    await connection.execute(text("DROP TABLE IF EXISTS knowledge_chunks"))
    required = {
        "id", "source_type", "source_id", "title", "content", "metadata_json",
        "source_fingerprint", "embedding_model", "embedding", "created_at", "updated_at",
    }

    def incompatible(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        if not inspector.has_table("knowledge_documents"):
            return False
        return not required.issubset({column["name"] for column in inspector.get_columns("knowledge_documents")})

    if await connection.run_sync(incompatible):
        # This table is a fully derived search index; source-of-truth records
        # remain in their domain tables and will be re-embedded automatically.
        await connection.execute(text("DROP TABLE knowledge_documents"))
        from jubran.infrastructure.db.models import KnowledgeDocumentModel
        await connection.run_sync(lambda sync_connection: KnowledgeDocumentModel.__table__.create(sync_connection))

    if connection.dialect.name == "postgresql":
        await connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_embedding_hnsw "
            "ON knowledge_documents USING hnsw (embedding vector_cosine_ops)"
        ))


async def upgrade_service_request_status_enum(connection: AsyncConnection) -> None:
    """Add new service workflow states to existing PostgreSQL installations."""
    if connection.dialect.name == "postgresql":
        await connection.execute(text(
            "ALTER TYPE servicerequeststatus ADD VALUE IF NOT EXISTS 'CANCELLED'"
        ))
        await connection.execute(text(
            "ALTER TYPE servicerequeststatus ADD VALUE IF NOT EXISTS 'IN_PROGRESS'"
        ))
        await connection.execute(text(
            "ALTER TYPE complaintstatus ADD VALUE IF NOT EXISTS 'IN_PROGRESS'"
        ))
        await connection.execute(text(
            "ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'CLOSED'"
        ))
        await connection.execute(text(
            "ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'CANCELLED'"
        ))
        await connection.execute(text(
            "ALTER TYPE complaintstatus ADD VALUE IF NOT EXISTS 'CANCELLED'"
        ))


async def ensure_table_session_closure_note(connection: AsyncConnection) -> None:
    def missing_closure_note(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        return inspector.has_table("table_sessions") and "closure_note" not in {
            column["name"] for column in inspector.get_columns("table_sessions")
        }

    if await connection.run_sync(missing_closure_note):
        await connection.execute(text("ALTER TABLE table_sessions ADD COLUMN closure_note TEXT"))


async def ensure_customer_action_closure_notes(connection: AsyncConnection) -> None:
    def missing_columns(sync_connection) -> list[tuple[str, str]]:
        inspector = inspect(sync_connection)
        upgrades = []
        for table_name in ("orders", "service_requests", "complaints"):
            if inspector.has_table(table_name) and "closure_note" not in {
                column["name"] for column in inspector.get_columns(table_name)
            }:
                upgrades.append((table_name, "closure_note"))
        return upgrades

    for table_name, column_name in await connection.run_sync(missing_columns):
        await connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} TEXT"))


async def ensure_draft_item_created_at(connection: AsyncConnection) -> None:
    def missing(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        return inspector.has_table("draft_items") and "created_at" not in {
            column["name"] for column in inspector.get_columns("draft_items")
        }

    if await connection.run_sync(missing):
        timestamp_type = "TIMESTAMP WITH TIME ZONE" if connection.dialect.name == "postgresql" else "TIMESTAMP"
        await connection.execute(text(f"ALTER TABLE draft_items ADD COLUMN created_at {timestamp_type}"))
        await connection.execute(text("UPDATE draft_items SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))


async def ensure_draft_confirmation_fingerprint(connection: AsyncConnection) -> None:
    """Confirmations remember exactly what the guest reviewed (older ones must be re-confirmed)."""
    def missing(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        return inspector.has_table("draft_confirmations") and "summary_fingerprint" not in {
            column["name"] for column in inspector.get_columns("draft_confirmations")
        }

    if await connection.run_sync(missing):
        await connection.execute(text("ALTER TABLE draft_confirmations ADD COLUMN summary_fingerprint VARCHAR(64)"))


async def upgrade_table_qr_tokens(connection: AsyncConnection) -> None:
    """Move installations to recoverable, unguessable table QR tokens.

    Older installations stored only a hash of predictable seed tokens such as
    ``qr-t4-jubran``. Those cannot be shown again and must not stay usable, so
    every active token without an encrypted copy is revoked. Tables then show no
    QR until an administrator creates a new one.
    """
    def missing_ciphertext(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        return inspector.has_table("table_qr_tokens") and "token_ciphertext" not in {
            column["name"] for column in inspector.get_columns("table_qr_tokens")
        }

    has_table = await connection.run_sync(
        lambda sync_connection: inspect(sync_connection).has_table("table_qr_tokens")
    )
    if not has_table:
        return
    if await connection.run_sync(missing_ciphertext):
        await connection.execute(text("ALTER TABLE table_qr_tokens ADD COLUMN token_ciphertext TEXT"))

    is_postgres = connection.dialect.name == "postgresql"
    active = "true" if is_postgres else "1"
    inactive = "false" if is_postgres else "0"
    await connection.execute(text(
        f"UPDATE table_qr_tokens SET is_active = {inactive}, revoked_at = CURRENT_TIMESTAMP "
        f"WHERE is_active = {active} AND token_ciphertext IS NULL"
    ))
    await connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_table_qr_one_active "
        f"ON table_qr_tokens (physical_table_id) WHERE is_active = {active}"
    ))


async def ensure_outbox_created_at_index(connection: AsyncConnection) -> None:
    """The event relay reads recent events by time."""
    await connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_outbox_events_created_at ON outbox_events (created_at)"))


async def retire_old_seed_overrides(connection: AsyncConnection) -> None:
    """Clean-ups the seeder used to force at every start; now applied once."""
    inspector_tables = await connection.run_sync(lambda sync_connection: set(inspect(sync_connection).get_table_names()))
    if "opening_hours" in inspector_tables:
        # Hour notes are no longer part of the schedule (the hours themselves are).
        await connection.execute(text(
            "UPDATE opening_hours SET notes_ar = NULL, notes_en = NULL "
            "WHERE notes_ar IS NOT NULL OR notes_en IS NOT NULL"))
    if "menu_categories" in inspector_tables:
        # Categories are shown by existence; an old hidden flag is retired.
        true = "true" if connection.dialect.name == "postgresql" else "1"
        await connection.execute(text(f"UPDATE menu_categories SET is_active = {true} WHERE NOT is_active"))


async def bring_legacy_schema_to_baseline(connection: AsyncConnection) -> None:
    """Make a pre-migration database identical to the baseline revision (idempotent)."""
    from jubran.infrastructure.db.session import Base
    from jubran.infrastructure.db import models  # noqa: F401  (registers every table)

    if connection.dialect.name == "postgresql":
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await connection.run_sync(Base.metadata.create_all)  # tables added since the database was created
    await ensure_ai_model_columns(connection)
    await upgrade_table_qr_tokens(connection)
    await ensure_table_session_closure_note(connection)
    await ensure_customer_action_closure_notes(connection)
    await ensure_draft_item_created_at(connection)
    await ensure_draft_confirmation_fingerprint(connection)
    await upgrade_service_request_status_enum(connection)
    await remove_retired_knowledge_table(connection)
    await ensure_outbox_created_at_index(connection)
    await retire_old_seed_overrides(connection)
