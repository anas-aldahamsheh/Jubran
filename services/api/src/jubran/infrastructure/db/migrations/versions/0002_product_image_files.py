"""Dish photos move out of the database into files: a storage key per photo.

The bytes of existing photos stay readable in image_data; the server copies them to
files at start-up (application/media_migration.py) and then clears the column.

Revision ID: 0002_product_image_files
Revises: 0001_baseline
Create Date: 2026-09-28 10:00:00+00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_product_image_files"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A database bridged from before migrations may already have the column (its missing
    # tables were created from the current models), so add it only when absent.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("product_images")}
    with op.batch_alter_table("product_images") as batch:
        if "storage_key" not in columns:
            batch.add_column(sa.Column("storage_key", sa.String(length=120), nullable=True))
        batch.alter_column("image_data", existing_type=sa.LargeBinary(), nullable=True)


def downgrade() -> None:
    # Only safe while every photo still has its bytes in the database.
    with op.batch_alter_table("product_images") as batch:
        batch.alter_column("image_data", existing_type=sa.LargeBinary(), nullable=False)
        batch.drop_column("storage_key")
