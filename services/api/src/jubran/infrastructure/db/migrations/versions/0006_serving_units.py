"""Serving units: how a dish is counted (plate, glass, box...) and the unit chosen on each line.

- products.units_json: the dish's units as the admin wrote them (a JSON list, e.g.
  ["صحن", "علبة"]); empty means the unit is chosen automatically.
- draft_items.unit and order_items.unit: the unit of that basket or order line, so
  the restaurant sees "1 plate and 2 boxes". Lines from before have none.

Only new, empty columns: nothing existing changes.

Revision ID: 0006_serving_units
Revises: 0005_live_voice_gpt_live
Create Date: 2026-10-08 09:00:00+00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0006_serving_units"
down_revision = "0005_live_voice_gpt_live"
branch_labels = None
depends_on = None

NEW_COLUMNS = (
    ("products", sa.Column("units_json", sa.Text(), nullable=True)),
    ("draft_items", sa.Column("unit", sa.String(length=40), nullable=True)),
    ("order_items", sa.Column("unit", sa.String(length=40), nullable=True)),
)


def _columns(table: str) -> set:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # A database bridged from before migrations may already have them (its missing
    # tables were created from the current models), so each is added only when absent.
    for table, column in NEW_COLUMNS:
        if column.name not in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.add_column(column)


def downgrade() -> None:
    for table, column in reversed(NEW_COLUMNS):
        if column.name in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column.name)
