"""Baseline: the complete schema as of the switch to migrations.

Databases created earlier by the server's old start-up routine (create_all +
in-place upgrades) are brought to this same schema and then *stamped* with this
revision by the migration runner, so this file only ever runs on empty databases.

Revision ID: 0001_baseline
Revises: 
Create Date: 2026-09-27 20:09:03.868104+00:00
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None


POSTGRES_ENUMS = ("userrole", "tableshape", "tablesessionstatus", "complaintstatus", "draftstatus",
                  "orderstatus", "servicerequesttype", "servicerequeststatus")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table('ai_model_configs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('model_id', sa.String(length=150), nullable=False),
    sa.Column('transcription_model_id', sa.String(length=150), nullable=True),
    sa.Column('api_key_ciphertext', sa.Text(), nullable=True),
    sa.Column('reasoning_effort', sa.String(length=20), nullable=True),
    sa.Column('reasoning_mode', sa.String(length=20), nullable=True),
    sa.Column('thinking_level', sa.String(length=20), nullable=True),
    sa.Column('thinking_budget', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_ai_model_one_active', 'ai_model_configs', ['is_active'], unique=True, sqlite_where=sa.text('is_active = 1'), postgresql_where=sa.text('is_active = true'))

    op.create_table('counters',
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('value', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('idempotency_records',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('operation_type', sa.String(length=50), nullable=False),
    sa.Column('scope_id', sa.String(length=36), nullable=False),
    sa.Column('response_payload', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key_hash', 'operation_type', name='uq_idempotency_key_op')
    )
    op.create_index(op.f('ix_idempotency_records_key_hash'), 'idempotency_records', ['key_hash'], unique=False)

    op.create_table('knowledge_documents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('source_type', sa.String(length=40), nullable=False),
    sa.Column('source_id', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('source_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('embedding_model', sa.String(length=150), nullable=False),
    sa.Column('embedding', Vector(768).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_type', 'source_id', name='uq_knowledge_source')
    )
    op.create_index('ix_knowledge_embedding_hnsw', 'knowledge_documents', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('ix_knowledge_source_type', 'knowledge_documents', ['source_type'], unique=False)

    op.create_table('outbox_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('aggregate_type', sa.String(length=50), nullable=False),
    sa.Column('aggregate_id', sa.String(length=36), nullable=False),
    sa.Column('payload_json', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outbox_events_created_at'), 'outbox_events', ['created_at'], unique=False)

    op.create_table('rate_limit_counters',
    sa.Column('bucket_key', sa.String(length=96), nullable=False),
    sa.Column('window_start', sa.Integer(), nullable=False),
    sa.Column('hits', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('bucket_key', 'window_start')
    )
    op.create_table('restaurants',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name_ar', sa.String(length=100), nullable=False),
    sa.Column('name_en', sa.String(length=100), nullable=False),
    sa.Column('about_ar', sa.Text(), nullable=False),
    sa.Column('about_en', sa.Text(), nullable=False),
    sa.Column('phone', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('USER', 'ADMIN', name='userrole'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    op.create_table('auth_sessions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_auth_sessions_token_hash'), 'auth_sessions', ['token_hash'], unique=True)

    op.create_table('branches',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('restaurant_id', sa.String(length=36), nullable=False),
    sa.Column('name_ar', sa.String(length=100), nullable=False),
    sa.Column('name_en', sa.String(length=100), nullable=False),
    sa.Column('address_ar', sa.String(length=255), nullable=False),
    sa.Column('address_en', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=50), nullable=False),
    sa.Column('is_demo_branch', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('menu_categories',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('restaurant_id', sa.String(length=36), nullable=False),
    sa.Column('name_ar', sa.String(length=100), nullable=False),
    sa.Column('name_en', sa.String(length=100), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('opening_hours',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('branch_id', sa.String(length=36), nullable=False),
    sa.Column('day_of_week', sa.Integer(), nullable=False),
    sa.Column('opens_at', sa.String(length=10), nullable=False),
    sa.Column('closes_at', sa.String(length=10), nullable=False),
    sa.Column('notes_ar', sa.String(length=255), nullable=True),
    sa.Column('notes_en', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('branch_id', 'day_of_week', name='uq_branch_day')
    )
    op.create_table('physical_tables',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('branch_id', sa.String(length=36), nullable=False),
    sa.Column('table_number', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('shape', sa.Enum('SQUARE', 'ROUND', 'RECTANGLE', name='tableshape'), nullable=False),
    sa.Column('seat_count', sa.Integer(), nullable=False),
    sa.Column('x_percent', sa.Float(), nullable=False),
    sa.Column('y_percent', sa.Float(), nullable=False),
    sa.Column('rotation_deg', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('branch_id', 'table_number', name='uq_branch_table_number')
    )
    op.create_table('products',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('category_id', sa.String(length=36), nullable=False),
    sa.Column('name_ar', sa.String(length=150), nullable=False),
    sa.Column('name_en', sa.String(length=150), nullable=False),
    sa.Column('description_ar', sa.Text(), nullable=True),
    sa.Column('description_en', sa.Text(), nullable=True),
    sa.Column('price_minor', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('is_available', sa.Boolean(), nullable=False),
    sa.Column('image_asset_url', sa.String(length=255), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['menu_categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('product_images',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('product_id', sa.String(length=36), nullable=False),
    sa.Column('content_type', sa.String(length=40), nullable=False),
    sa.Column('image_data', sa.LargeBinary(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_product_images_product_id'), 'product_images', ['product_id'], unique=False)

    op.create_table('table_qr_tokens',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('physical_table_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('token_ciphertext', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['physical_table_id'], ['physical_tables.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_table_qr_tokens_token_hash'), 'table_qr_tokens', ['token_hash'], unique=True)
    op.create_index('uq_table_qr_one_active', 'table_qr_tokens', ['physical_table_id'], unique=True, sqlite_where=sa.text('is_active = 1'), postgresql_where=sa.text('is_active = true'))

    op.create_table('table_sessions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('physical_table_id', sa.String(length=36), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_by_admin_id', sa.String(length=36), nullable=True),
    sa.Column('closure_note', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('ACTIVE', 'CLOSED', name='tablesessionstatus'), nullable=False),
    sa.ForeignKeyConstraint(['closed_by_admin_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['physical_table_id'], ['physical_tables.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('customer_sessions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('table_session_id', sa.String(length=36), nullable=False),
    sa.Column('authenticated_user_id', sa.String(length=36), nullable=True),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['authenticated_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['table_session_id'], ['table_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customer_sessions_token_hash'), 'customer_sessions', ['token_hash'], unique=True)

    op.create_table('assistant_conversations',
    sa.Column('customer_session_id', sa.String(length=36), nullable=False),
    sa.Column('messages_json', sa.Text(), nullable=False),
    sa.Column('pending_token_ciphertext', sa.Text(), nullable=True),
    sa.Column('pending_draft_version', sa.Integer(), nullable=True),
    sa.Column('pending_summary_json', sa.Text(), nullable=True),
    sa.Column('upsell_suggested', sa.Boolean(), nullable=False),
    sa.Column('busy_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('busy_lease', sa.String(length=32), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['customer_session_id'], ['customer_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('customer_session_id')
    )
    op.create_table('complaints',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('table_session_id', sa.String(length=36), nullable=False),
    sa.Column('customer_session_id', sa.String(length=36), nullable=False),
    sa.Column('message', sa.String(length=500), nullable=False),
    sa.Column('category', sa.String(length=50), nullable=True),
    sa.Column('status', sa.Enum('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CANCELLED', name='complaintstatus'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closure_note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['customer_session_id'], ['customer_sessions.id'], ),
    sa.ForeignKeyConstraint(['table_session_id'], ['table_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('draft_orders',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('customer_session_id', sa.String(length=36), nullable=False),
    sa.Column('table_session_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'SUBMITTED', 'ABANDONED', name='draftstatus'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['customer_session_id'], ['customer_sessions.id'], ),
    sa.ForeignKeyConstraint(['table_session_id'], ['table_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('feedback',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('table_session_id', sa.String(length=36), nullable=False),
    sa.Column('customer_session_id', sa.String(length=36), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('comment', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['customer_session_id'], ['customer_sessions.id'], ),
    sa.ForeignKeyConstraint(['table_session_id'], ['table_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('orders',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('table_session_id', sa.String(length=36), nullable=False),
    sa.Column('customer_session_id', sa.String(length=36), nullable=False),
    sa.Column('order_number', sa.String(length=20), nullable=False),
    sa.Column('status', sa.Enum('PENDING_APPROVAL', 'PREPARING', 'READY', 'CLOSED', 'CANCELLED', name='orderstatus'), nullable=False),
    sa.Column('served_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('closure_note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['customer_session_id'], ['customer_sessions.id'], ),
    sa.ForeignKeyConstraint(['table_session_id'], ['table_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_orders_order_number'), 'orders', ['order_number'], unique=True)

    op.create_table('service_requests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('table_session_id', sa.String(length=36), nullable=False),
    sa.Column('customer_session_id', sa.String(length=36), nullable=False),
    sa.Column('type', sa.Enum('STAFF', 'TISSUES', 'CLEAN_TABLE', 'BILL', name='servicerequesttype'), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'IN_PROGRESS', 'RESOLVED', 'CANCELLED', name='servicerequeststatus'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by_admin_id', sa.String(length=36), nullable=True),
    sa.Column('closure_note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['customer_session_id'], ['customer_sessions.id'], ),
    sa.ForeignKeyConstraint(['resolved_by_admin_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['table_session_id'], ['table_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('draft_confirmations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('customer_session_id', sa.String(length=36), nullable=False),
    sa.Column('draft_order_id', sa.String(length=36), nullable=False),
    sa.Column('draft_version', sa.Integer(), nullable=False),
    sa.Column('confirmation_token_hash', sa.String(length=64), nullable=False),
    sa.Column('summary_fingerprint', sa.String(length=64), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['customer_session_id'], ['customer_sessions.id'], ),
    sa.ForeignKeyConstraint(['draft_order_id'], ['draft_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_draft_confirmations_confirmation_token_hash'), 'draft_confirmations', ['confirmation_token_hash'], unique=True)

    op.create_table('draft_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('draft_order_id', sa.String(length=36), nullable=False),
    sa.Column('product_id', sa.String(length=36), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['draft_order_id'], ['draft_orders.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('order_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('order_id', sa.String(length=36), nullable=False),
    sa.Column('product_id', sa.String(length=36), nullable=True),
    sa.Column('product_name_snapshot_ar', sa.String(length=150), nullable=False),
    sa.Column('product_name_snapshot_en', sa.String(length=150), nullable=False),
    sa.Column('unit_price_minor_snapshot', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.Column('line_total_minor', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('order_items')
    op.drop_table('draft_items')
    op.drop_index(op.f('ix_draft_confirmations_confirmation_token_hash'), table_name='draft_confirmations')

    op.drop_table('draft_confirmations')
    op.drop_table('service_requests')
    op.drop_index(op.f('ix_orders_order_number'), table_name='orders')

    op.drop_table('orders')
    op.drop_table('feedback')
    op.drop_table('draft_orders')
    op.drop_table('complaints')
    op.drop_table('assistant_conversations')
    op.drop_index(op.f('ix_customer_sessions_token_hash'), table_name='customer_sessions')

    op.drop_table('customer_sessions')
    op.drop_table('table_sessions')
    op.drop_index('uq_table_qr_one_active', table_name='table_qr_tokens', sqlite_where=sa.text('is_active = 1'), postgresql_where=sa.text('is_active = true'))
    op.drop_index(op.f('ix_table_qr_tokens_token_hash'), table_name='table_qr_tokens')

    op.drop_table('table_qr_tokens')
    op.drop_index(op.f('ix_product_images_product_id'), table_name='product_images')

    op.drop_table('product_images')
    op.drop_table('products')
    op.drop_table('physical_tables')
    op.drop_table('opening_hours')
    op.drop_table('menu_categories')
    op.drop_table('branches')
    op.drop_index(op.f('ix_auth_sessions_token_hash'), table_name='auth_sessions')

    op.drop_table('auth_sessions')
    op.drop_index(op.f('ix_users_email'), table_name='users')

    op.drop_table('users')
    op.drop_table('restaurants')
    op.drop_table('rate_limit_counters')
    op.drop_index(op.f('ix_outbox_events_created_at'), table_name='outbox_events')

    op.drop_table('outbox_events')
    op.drop_index('ix_knowledge_source_type', table_name='knowledge_documents')
    op.drop_index('ix_knowledge_embedding_hnsw', table_name='knowledge_documents', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})

    op.drop_table('knowledge_documents')
    op.drop_index(op.f('ix_idempotency_records_key_hash'), table_name='idempotency_records')

    op.drop_table('idempotency_records')
    op.drop_table('counters')
    op.drop_index('uq_ai_model_one_active', table_name='ai_model_configs', sqlite_where=sa.text('is_active = 1'), postgresql_where=sa.text('is_active = true'))

    op.drop_table('ai_model_configs')
    if op.get_bind().dialect.name == "postgresql":
        for enum_name in POSTGRES_ENUMS:
            op.execute(f"DROP TYPE IF EXISTS {enum_name}")
