"""Authoritative SQLAlchemy ORM Models."""
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Any
from sqlalchemy import (
    String, Integer, Boolean, DateTime, ForeignKey, Text, Enum as SQLEnum,
    UniqueConstraint, Index, text, LargeBinary, JSON
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator
from pgvector.sqlalchemy import Vector
from jubran.infrastructure.db.session import Base
from jubran.domain.enums import (
    UserRole, TableShape, TableSessionStatus, DraftStatus, OrderStatus,
    ServiceRequestType, ServiceRequestStatus, ComplaintStatus
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """A moment in time, always stored as UTC and always read back timezone-aware.

    PostgreSQL keeps the offset itself; SQLite stores plain text and would hand back
    "naive" datetimes, which cannot be compared with ``datetime.now(timezone.utc)``.
    """
    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)  # PostgreSQL answers in the connection's time zone


# Size of the stored search vectors. Fixed by the database schema: changing it needs a migration
# and re-indexing, so it is not a setting.
EMBEDDING_DIMENSIONS = 768


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SQLEnum(UserRole), nullable=False, default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)

    sessions: Mapped[List["AuthSessionModel"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AiModelConfigModel(Base):
    """An AI model setting for one purpose: chat, embedding (menu search), transcription
    (speech to text) or voice (live conversation). A purpose keeps one setting per
    provider, and at most one of them is in use."""
    __tablename__ = "ai_model_configs"
    __table_args__ = (
        Index("uq_ai_model_one_active_per_purpose", "purpose", unique=True,
              sqlite_where=text("is_active = 1"), postgresql_where=text("is_active = true")),
        Index("uq_ai_model_purpose_provider", "purpose", "provider", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    purpose: Mapped[str] = mapped_column(String(20), nullable=False, default="chat", server_default="chat")
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model_id: Mapped[str] = mapped_column(String(150), nullable=False)
    api_key_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reasoning_effort: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    reasoning_mode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    thinking_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    thinking_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class AuthSessionModel(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    user: Mapped["UserModel"] = relationship(back_populates="sessions")


class RestaurantModel(Base):
    __tablename__ = "restaurants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name_ar: Mapped[str] = mapped_column(String(100), default="جبران", nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), default="Jubran", nullable=False)
    about_ar: Mapped[str] = mapped_column(Text, default="تراث المشرق بروح جديدة فوق عمّان.", nullable=False)
    about_en: Mapped[str] = mapped_column(Text, default="Levantine heritage, reimagined above Amman.", nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="+962 777 123 456", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)

    branches: Mapped[List["BranchModel"]] = relationship(back_populates="restaurant", cascade="all, delete-orphan")
    categories: Mapped[List["MenuCategoryModel"]] = relationship(back_populates="restaurant", cascade="all, delete-orphan")


class BranchModel(Base):
    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    restaurant_id: Mapped[str] = mapped_column(String(36), ForeignKey("restaurants.id"), nullable=False)
    name_ar: Mapped[str] = mapped_column(String(100), default="بوليفارد العبدلي", nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), default="Abdali Boulevard", nullable=False)
    address_ar: Mapped[str] = mapped_column(String(255), default="بوليفارد العبدلي، مدخل 2، الطابق السابع (الروف)، عمّان", nullable=False)
    address_en: Mapped[str] = mapped_column(String(255), default="Abdali Boulevard, Entrance 2, 7th floor (rooftop), Amman", nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="+962 777 123 456", nullable=False)
    is_demo_branch: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    restaurant: Mapped["RestaurantModel"] = relationship(back_populates="branches")
    opening_hours: Mapped[List["OpeningHourModel"]] = relationship(back_populates="branch", cascade="all, delete-orphan")
    tables: Mapped[List["PhysicalTableModel"]] = relationship(back_populates="branch", cascade="all, delete-orphan")


class OpeningHourModel(Base):
    __tablename__ = "opening_hours"
    __table_args__ = (UniqueConstraint("branch_id", "day_of_week", name="uq_branch_day"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    branch_id: Mapped[str] = mapped_column(String(36), ForeignKey("branches.id"), nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Sunday..6=Saturday or 1=Monday..7=Sunday
    opens_at: Mapped[str] = mapped_column(String(10), default="06:30", nullable=False)
    closes_at: Mapped[str] = mapped_column(String(10), default="01:00", nullable=False)
    notes_ar: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes_en: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    branch: Mapped["BranchModel"] = relationship(back_populates="opening_hours")


class MenuCategoryModel(Base):
    __tablename__ = "menu_categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    restaurant_id: Mapped[str] = mapped_column(String(36), ForeignKey("restaurants.id"), nullable=False)
    name_ar: Mapped[str] = mapped_column(String(100), nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    restaurant: Mapped["RestaurantModel"] = relationship(back_populates="categories")
    products: Mapped[List["ProductModel"]] = relationship(back_populates="category")


class ProductModel(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    category_id: Mapped[str] = mapped_column(String(36), ForeignKey("menu_categories.id"), nullable=False)
    name_ar: Mapped[str] = mapped_column(String(150), nullable=False)
    name_en: Mapped[str] = mapped_column(String(150), nullable=False)
    description_ar: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)  # 3450 = 3.450 JOD (fils)
    currency: Mapped[str] = mapped_column(String(3), default="JOD", nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    image_asset_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)

    category: Mapped["MenuCategoryModel"] = relationship(back_populates="products")
    images: Mapped[List["ProductImageModel"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ProductImageModel.sort_order"
    )


class ProductImageModel(Base):
    __tablename__ = "product_images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # The photo's file under MEDIA_DIR. Photos saved before files were used keep their
    # bytes in image_data until the server moves them out at start-up.
    storage_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    image_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    product: Mapped["ProductModel"] = relationship(back_populates="images")


class KnowledgeDocumentModel(Base):
    """Searchable, source-linked restaurant knowledge for semantic retrieval."""
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_knowledge_source"),
        Index("ix_knowledge_source_type", "source_type"),
        Index(
            "ix_knowledge_embedding_hnsw", "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(150), nullable=False)
    embedding: Mapped[List[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS).with_variant(JSON, "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class PhysicalTableModel(Base):
    __tablename__ = "physical_tables"
    __table_args__ = (UniqueConstraint("branch_id", "table_number", name="uq_branch_table_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    branch_id: Mapped[str] = mapped_column(String(36), ForeignKey("branches.id"), nullable=False)
    table_number: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    shape: Mapped[TableShape] = mapped_column(SQLEnum(TableShape), default=TableShape.SQUARE, nullable=False)
    seat_count: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    x_percent: Mapped[float] = mapped_column(default=0.0, nullable=False)
    y_percent: Mapped[float] = mapped_column(default=0.0, nullable=False)
    rotation_deg: Mapped[float] = mapped_column(default=0.0, nullable=False)

    branch: Mapped["BranchModel"] = relationship(back_populates="tables")
    qr_tokens: Mapped[List["TableQrTokenModel"]] = relationship(back_populates="physical_table", cascade="all, delete-orphan")
    sessions: Mapped[List["TableSessionModel"]] = relationship(back_populates="physical_table")


class TableQrTokenModel(Base):
    __tablename__ = "table_qr_tokens"
    __table_args__ = (
        # A physical table can have at most one scannable QR at a time.
        Index("uq_table_qr_one_active", "physical_table_id", unique=True,
              sqlite_where=text("is_active = 1"), postgresql_where=text("is_active = true")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    physical_table_id: Mapped[str] = mapped_column(String(36), ForeignKey("physical_tables.id"), nullable=False)
    # Lookup uses the hash; the encrypted copy lets an administrator re-download the printable QR.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    token_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    physical_table: Mapped["PhysicalTableModel"] = relationship(back_populates="qr_tokens")


class TableSessionModel(Base):
    __tablename__ = "table_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    physical_table_id: Mapped[str] = mapped_column(String(36), ForeignKey("physical_tables.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    closed_by_admin_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    closure_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TableSessionStatus] = mapped_column(
        SQLEnum(TableSessionStatus), default=TableSessionStatus.ACTIVE, nullable=False
    )

    physical_table: Mapped["PhysicalTableModel"] = relationship(back_populates="sessions")
    customer_sessions: Mapped[List["CustomerSessionModel"]] = relationship(back_populates="table_session")
    orders: Mapped[List["OrderModel"]] = relationship(back_populates="table_session")
    service_requests: Mapped[List["ServiceRequestModel"]] = relationship(back_populates="table_session")
    complaints: Mapped[List["ComplaintModel"]] = relationship(back_populates="table_session")
    feedback: Mapped[List["FeedbackModel"]] = relationship(back_populates="table_session")


class CustomerSessionModel(Base):
    __tablename__ = "customer_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    table_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("table_sessions.id"), nullable=False)
    authenticated_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    table_session: Mapped["TableSessionModel"] = relationship(back_populates="customer_sessions")
    draft_orders: Mapped[List["DraftOrderModel"]] = relationship(back_populates="customer_session")


class DraftOrderModel(Base):
    __tablename__ = "draft_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer_sessions.id"), nullable=False)
    table_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("table_sessions.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[DraftStatus] = mapped_column(SQLEnum(DraftStatus), default=DraftStatus.OPEN, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)

    customer_session: Mapped["CustomerSessionModel"] = relationship(back_populates="draft_orders")
    items: Mapped[List["DraftItemModel"]] = relationship(back_populates="draft_order", cascade="all, delete-orphan")


class DraftItemModel(Base):
    __tablename__ = "draft_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    draft_order_id: Mapped[str] = mapped_column(String(36), ForeignKey("draft_orders.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    draft_order: Mapped["DraftOrderModel"] = relationship(back_populates="items")
    product: Mapped["ProductModel"] = relationship()


class DraftConfirmationModel(Base):
    __tablename__ = "draft_confirmations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer_sessions.id"), nullable=False)
    draft_order_id: Mapped[str] = mapped_column(String(36), ForeignKey("draft_orders.id"), nullable=False)
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmation_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # SHA-256 of exactly what the guest reviewed: lines, quantities, notes, unit prices, availability.
    summary_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)


class AssistantConversationModel(Base):
    """The assistant's memory for one guest visit, shared by every server worker.

    Holds the recent messages, the order summary waiting for the guest's "yes"
    (its token encrypted), whether a suggestion was already offered, and a short
    lease so two messages of the same guest are never processed at once.
    Deleted when the visit ends.
    """
    __tablename__ = "assistant_conversations"

    customer_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("customer_sessions.id", ondelete="CASCADE"), primary_key=True)
    messages_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pending_token_ciphertext: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pending_draft_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pending_summary_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upsell_suggested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    busy_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    busy_lease: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Dishes the assistant showed in the last few turns (id, names, price), so the
    # guest can say "add two of those" without the model searching the menu again.
    known_products_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # A change to an order already sent, shown to the guest and waiting for their "yes".
    pending_amendment_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class OrderModel(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    table_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("table_sessions.id"), nullable=False)
    customer_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer_sessions.id"), nullable=False)
    order_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(SQLEnum(OrderStatus), default=OrderStatus.PENDING_APPROVAL, nullable=False)
    served_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    closure_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    table_session: Mapped["TableSessionModel"] = relationship(back_populates="orders")
    items: Mapped[List["OrderItemModel"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    amendments: Mapped[List["OrderAmendmentModel"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderAmendmentModel.created_at")


class OrderItemModel(Base):
    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name_snapshot_ar: Mapped[str] = mapped_column(String(150), nullable=False)
    product_name_snapshot_en: Mapped[str] = mapped_column(String(150), nullable=False)
    unit_price_minor_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    line_total_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    # Set only for dishes the guest added after sending the order.
    added_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    order: Mapped["OrderModel"] = relationship(back_populates="items")


class OrderAmendmentModel(Base):
    """A change the guest made to an order after sending it (dishes added, removed, quantities).

    Staff see every change. One made while the kitchen was already preparing the
    order stays highlighted until a staff member acknowledges it.
    """
    __tablename__ = "order_amendments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("orders.id", ondelete="CASCADE"),
                                          index=True, nullable=False)
    customer_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer_sessions.id"), nullable=False)
    order_status: Mapped[str] = mapped_column(String(30), nullable=False)  # the order's status at the change
    changes_json: Mapped[str] = mapped_column(Text, nullable=False)
    total_before_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    total_after_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    acknowledged_by_user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    order: Mapped["OrderModel"] = relationship(back_populates="amendments")


class ServiceRequestModel(Base):
    __tablename__ = "service_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    table_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("table_sessions.id"), nullable=False)
    customer_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer_sessions.id"), nullable=False)
    type: Mapped[ServiceRequestType] = mapped_column(SQLEnum(ServiceRequestType), nullable=False)
    status: Mapped[ServiceRequestStatus] = mapped_column(
        SQLEnum(ServiceRequestStatus), default=ServiceRequestStatus.OPEN, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    resolved_by_admin_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    closure_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    table_session: Mapped["TableSessionModel"] = relationship(back_populates="service_requests")


class ComplaintModel(Base):
    __tablename__ = "complaints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    table_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("table_sessions.id"), nullable=False)
    customer_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer_sessions.id"), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[ComplaintStatus] = mapped_column(
        SQLEnum(ComplaintStatus), default=ComplaintStatus.OPEN, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    closure_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    table_session: Mapped["TableSessionModel"] = relationship(back_populates="complaints")


class FeedbackModel(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    table_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("table_sessions.id"), nullable=False)
    customer_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("customer_sessions.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 to 5
    comment: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    table_session: Mapped["TableSessionModel"] = relationship(back_populates="feedback")


class IdempotencyRecordModel(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("key_hash", "operation_type", name="uq_idempotency_key_op"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(36), nullable=False)
    response_payload: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)


class RateLimitCounterModel(Base):
    """Request counters per (bucket, subject) and time window, shared by all workers."""
    __tablename__ = "rate_limit_counters"

    bucket_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AssistantUsageModel(Base):
    """Tokens (and estimated cost) of one assistant turn, for the admin's cost overview."""
    __tablename__ = "assistant_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_session_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model_id: Mapped[str] = mapped_column(String(150), nullable=False)
    model_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_microusd: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, index=True)


class CounterModel(Base):
    """Named counters incremented atomically in the database (e.g. order numbers)."""
    __tablename__ = "counters"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
