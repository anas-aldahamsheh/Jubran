"""Guests can change orders they already sent; the assistant remembers recent dishes; usage and settings.

- order_amendments: every change a guest made to a sent order (staff acknowledge
  changes made while the kitchen was preparing).
- order_items.added_at: dishes added after the order was sent.
- assistant_conversations: recently shown dishes and a pending order change.
- assistant_usage: tokens and estimated cost of each assistant turn.
- app_settings: small administrator settings (e.g. the menu-search provider).

Revision ID: 0003_order_amendments
Revises: 0002_product_image_files
Create Date: 2026-09-28 12:00:00+00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_order_amendments"
down_revision = "0002_product_image_files"
branch_labels = None
depends_on = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # A database bridged from before migrations may already have some of these
    # (its missing tables were created from the current models), so each step
    # runs only when needed.
    tables = _tables()
    if "order_amendments" not in tables:
        op.create_table(
            "order_amendments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("order_id", sa.String(length=36), nullable=False),
            sa.Column("customer_session_id", sa.String(length=36), nullable=False),
            sa.Column("order_status", sa.String(length=30), nullable=False),
            sa.Column("changes_json", sa.Text(), nullable=False),
            sa.Column("total_before_minor", sa.Integer(), nullable=False),
            sa.Column("total_after_minor", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("acknowledged_by_user_id", sa.String(length=36), nullable=True),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["customer_session_id"], ["customer_sessions.id"]),
            sa.ForeignKeyConstraint(["acknowledged_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_order_amendments_order_id"), "order_amendments", ["order_id"], unique=False)

    if "assistant_usage" not in tables:
        op.create_table(
            "assistant_usage",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("customer_session_id", sa.String(length=36), nullable=True),
            sa.Column("channel", sa.String(length=20), nullable=False),
            sa.Column("provider", sa.String(length=20), nullable=False),
            sa.Column("model_id", sa.String(length=150), nullable=False),
            sa.Column("model_calls", sa.Integer(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False),
            sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
            sa.Column("output_tokens", sa.Integer(), nullable=False),
            sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
            sa.Column("cost_microusd", sa.Integer(), nullable=True),
            sa.Column("succeeded", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_assistant_usage_created_at"), "assistant_usage", ["created_at"], unique=False)

    if "app_settings" not in tables:
        op.create_table(
            "app_settings",
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("name"),
        )

    if "added_at" not in _columns("order_items"):
        with op.batch_alter_table("order_items") as batch:
            batch.add_column(sa.Column("added_at", sa.DateTime(timezone=True), nullable=True))

    conversation_columns = _columns("assistant_conversations")
    missing = [name for name in ("known_products_json", "pending_amendment_json") if name not in conversation_columns]
    if missing:
        with op.batch_alter_table("assistant_conversations") as batch:
            for name in missing:
                batch.add_column(sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assistant_conversations") as batch:
        batch.drop_column("pending_amendment_json")
        batch.drop_column("known_products_json")
    with op.batch_alter_table("order_items") as batch:
        batch.drop_column("added_at")
    op.drop_table("app_settings")
    op.drop_index(op.f("ix_assistant_usage_created_at"), table_name="assistant_usage")
    op.drop_table("assistant_usage")
    op.drop_index(op.f("ix_order_amendments_order_id"), table_name="order_amendments")
    op.drop_table("order_amendments")
