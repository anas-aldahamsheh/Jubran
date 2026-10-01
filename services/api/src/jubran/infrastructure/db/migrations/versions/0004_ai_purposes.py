"""Separate AI settings per purpose: chat, menu search, speech to text and live voice.

- ai_model_configs.purpose: what a setting is for; existing settings are chat settings.
- Each purpose keeps one setting per provider (of duplicates, the one in use or else the
  latest stays) and uses at most one of them.
- Choices that lived elsewhere move into their own sections: the speech-to-text model of
  the chat setting in use, and the menu-search provider chosen on the settings page. The
  columns and the settings table that held them go, and so does the setting name (a
  section needs none).
- Live voice used to follow a Gemini chat model; it now has its own section and starts off.

Revision ID: 0004_ai_purposes
Revises: 0003_order_amendments
Create Date: 2026-09-28 18:00:00+00:00
"""
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0004_ai_purposes"
down_revision = "0003_order_amendments"
branch_labels = None
depends_on = None

OLD_INDEX = "uq_ai_model_one_active"
ACTIVE_INDEX = "uq_ai_model_one_active_per_purpose"
PROVIDER_INDEX = "uq_ai_model_purpose_provider"
ACTIVE = {"sqlite_where": sa.text("is_active = 1"), "postgresql_where": sa.text("is_active = true")}
# Menu-search models of that time (the server defaults).
EMBEDDING_MODELS = {"gemini": "gemini-embedding-001", "openai": "text-embedding-3-small"}

configs = sa.table(
    "ai_model_configs",
    sa.column("id", sa.String), sa.column("purpose", sa.String), sa.column("provider", sa.String),
    sa.column("model_id", sa.String), sa.column("api_key_ciphertext", sa.Text), sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)),
)
# The chat settings as they were: the speech-to-text model was one of their columns.
old_chat = sa.table(
    "ai_model_configs",
    sa.column("purpose", sa.String), sa.column("provider", sa.String), sa.column("transcription_model_id", sa.String),
    sa.column("api_key_ciphertext", sa.Text), sa.column("is_active", sa.Boolean),
)
app_settings = sa.table("app_settings", sa.column("name", sa.String), sa.column("value", sa.Text))


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns() -> set:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("ai_model_configs")}


def _indexes() -> set:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("ai_model_configs")}


def _moved_choices(bind, columns: set, tables: set) -> list:
    """The speech-to-text and menu-search choices, as settings of their own sections."""
    moved = []
    if "transcription_model_id" in columns:
        chosen = bind.execute(
            sa.select(old_chat.c.provider, old_chat.c.transcription_model_id, old_chat.c.api_key_ciphertext)
            .where(old_chat.c.purpose == "chat", old_chat.c.is_active == sa.true(),
                   old_chat.c.transcription_model_id.is_not(None))
        ).first()
        if chosen:
            moved.append({"purpose": "transcription", "provider": chosen.provider,
                          "model_id": chosen.transcription_model_id, "api_key_ciphertext": chosen.api_key_ciphertext})
    if "app_settings" in tables:
        provider = bind.execute(sa.select(app_settings.c.value)
                                .where(app_settings.c.name == "embedding_provider")).scalar()
        if provider in EMBEDDING_MODELS:
            # The key menu search used: one saved for that provider, the setting in use first.
            key = bind.execute(
                sa.select(configs.c.api_key_ciphertext)
                .where(configs.c.provider == provider, configs.c.api_key_ciphertext.is_not(None))
                .order_by(configs.c.is_active.desc(), configs.c.updated_at.desc()).limit(1)
            ).scalar()
            moved.append({"purpose": "embedding", "provider": provider, "model_id": EMBEDDING_MODELS[provider],
                          "api_key_ciphertext": key})
    taken = set(bind.execute(sa.select(configs.c.purpose).distinct()).scalars())
    now = datetime.now(timezone.utc)
    return [dict(choice, id=str(uuid.uuid4()), is_active=True, created_at=now, updated_at=now)
            for choice in moved if choice["purpose"] not in taken]


def _merge_duplicates(bind) -> None:
    """One setting per provider in each purpose: the one in use, else the latest."""
    rows = bind.execute(sa.select(configs.c.id, configs.c.purpose, configs.c.provider,
                                  configs.c.is_active, configs.c.updated_at)).all()
    rows.sort(key=lambda row: (not row.is_active, -(row.updated_at.timestamp() if row.updated_at else 0)))
    kept, extra = set(), []
    for row in rows:
        slot = (row.purpose, row.provider)
        if slot in kept:
            extra.append(row.id)
        kept.add(slot)
    if extra:
        bind.execute(configs.delete().where(configs.c.id.in_(extra)))


def upgrade() -> None:
    # A database bridged from before migrations may already have parts of the new shape
    # (its missing tables were created from the current models), so each step checks.
    bind = op.get_bind()
    tables = _tables()
    if "purpose" not in _columns():
        op.add_column("ai_model_configs",
                      sa.Column("purpose", sa.String(length=20), nullable=False, server_default="chat"))
    moved = _moved_choices(bind, _columns(), tables)
    _merge_duplicates(bind)

    # Indexes go first: SQLite rebuilds the table to drop columns.
    indexes = _indexes()
    for name, where in ((OLD_INDEX, ACTIVE), (ACTIVE_INDEX, ACTIVE), (PROVIDER_INDEX, {})):
        if name in indexes:
            op.drop_index(name, table_name="ai_model_configs", **where)
    retired = [name for name in ("name", "transcription_model_id") if name in _columns()]
    if retired:
        with op.batch_alter_table("ai_model_configs") as batch:
            for name in retired:
                batch.drop_column(name)
    if moved:
        bind.execute(configs.insert(), moved)
    op.create_index(ACTIVE_INDEX, "ai_model_configs", ["purpose"], unique=True, **ACTIVE)
    op.create_index(PROVIDER_INDEX, "ai_model_configs", ["purpose", "provider"], unique=True)
    if "app_settings" in tables:
        op.drop_table("app_settings")


def downgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.drop_index(PROVIDER_INDEX, table_name="ai_model_configs")
    op.drop_index(ACTIVE_INDEX, table_name="ai_model_configs", **ACTIVE)
    # Before sections, settings existed only for chat.
    op.execute("DELETE FROM ai_model_configs WHERE purpose <> 'chat'")
    with op.batch_alter_table("ai_model_configs") as batch:
        batch.add_column(sa.Column("name", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("transcription_model_id", sa.String(length=150), nullable=True))
    op.execute("UPDATE ai_model_configs SET name = SUBSTR(model_id, 1, 100)")
    with op.batch_alter_table("ai_model_configs") as batch:
        batch.alter_column("name", existing_type=sa.String(length=100), nullable=False)
        batch.drop_column("purpose")
    op.create_index(OLD_INDEX, "ai_model_configs", ["is_active"], unique=True, **ACTIVE)
