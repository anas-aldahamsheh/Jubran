"""A guest's complaints during a visit are kept together in one complaint.

complaints.message becomes text without a length limit (it was at most 500
characters): what a guest complains about later is added to their first complaint
as a timed line. On PostgreSQL only the column's type changes (no data is
rewritten); SQLite never enforced the limit, so nothing changes there.

Revision ID: 0007_complaint_lines
Revises: 0006_serving_units
Create Date: 2026-10-08 14:00:00+00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_complaint_lines"
down_revision = "0006_serving_units"
branch_labels = None
depends_on = None


def _message_length():
    """The length limit of complaints.message (None: already unlimited text)."""
    column = next(column for column in sa.inspect(op.get_bind()).get_columns("complaints")
                  if column["name"] == "message")
    return getattr(column["type"], "length", None)


def upgrade() -> None:
    # A database bridged from before migrations may already have it as text (its missing
    # tables were created from the current models).
    if op.get_bind().dialect.name == "postgresql" and _message_length() is not None:
        op.alter_column("complaints", "message", type_=sa.Text(), existing_type=sa.String(length=500),
                        existing_nullable=False)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql" and _message_length() is None:
        # Back to at most 500 characters: longer complaints keep their first 500.
        op.execute("UPDATE complaints SET message = left(message, 500) WHERE length(message) > 500")
        op.alter_column("complaints", "message", type_=sa.String(length=500), existing_type=sa.Text(),
                        existing_nullable=False)
