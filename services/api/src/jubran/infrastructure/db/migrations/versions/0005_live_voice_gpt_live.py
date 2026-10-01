"""Live voice runs on OpenAI GPT-Live only: settings saved for another provider are removed.

The live voice section used to offer Gemini Live. Its saved settings (and any key kept for
it) can no longer run, so they go, and the section is off until GPT-Live is saved.

Revision ID: 0005_live_voice_gpt_live
Revises: 0004_ai_purposes
Create Date: 2026-09-30 12:00:00+00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_live_voice_gpt_live"
down_revision = "0004_ai_purposes"
branch_labels = None
depends_on = None

configs = sa.table("ai_model_configs", sa.column("purpose", sa.String), sa.column("provider", sa.String))


def upgrade() -> None:
    op.execute(configs.delete().where(configs.c.purpose == "voice", configs.c.provider != "openai"))


def downgrade() -> None:
    pass  # the removed settings could not run any more; nothing to bring back
