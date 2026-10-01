"""Pydantic Request and Response Schemas for HTTP API."""
from typing import Literal, Optional, List
from pydantic import BaseModel, EmailStr, Field

# A dish picture link may only point at this website's own images (e.g. /images/hummus.png):
# no outside addresses that could track guests or show someone else's content.
SITE_IMAGE_PATH = r"^$|^/[A-Za-z0-9._~-][A-Za-z0-9._~/-]*\.(?i:png|jpe?g|webp|gif|avif|svg)$"


# Auth Schemas
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=256)
    remember_me: bool = False


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool


class LoginResponse(BaseModel):
    # The login token is only ever set as an HttpOnly cookie, never returned here.
    user: UserResponse


class CsrfTokenResponse(BaseModel):
    csrf_token: str


# Session & QR Schemas
class StartSessionRequest(BaseModel):
    qr_token: str = Field(min_length=3, max_length=128)


class StartSessionResponse(BaseModel):
    # The visit token is only ever set as an HttpOnly cookie, never returned here.
    customer_id: str
    table_id: str
    table_number: str
    branch_name_ar: str
    branch_name_en: str
    restaurant_name_ar: str
    restaurant_name_en: str


class SessionContextResponse(BaseModel):
    customer_session_id: str
    customer_id: str
    table_session_id: str
    table_id: str
    table_number: str
    branch_name_ar: str
    branch_name_en: str
    is_authenticated: bool
    user_email: Optional[str] = None


# Admin Table & QR
class TableAdminItem(BaseModel):
    id: str
    table_number: str
    shape: str
    seat_count: int
    x_percent: float
    y_percent: float
    rotation_deg: float
    is_active: bool
    has_active_qr: bool
    qr_token_id: Optional[str] = None
    qr_created_at: Optional[str] = None
    # Raw token for the printable QR; admin-only. None when the table has no QR.
    qr_token: Optional[str] = None


# Draft & Order Schemas
class AddItemRequest(BaseModel):
    product_id: str
    quantity: int = Field(default=1, ge=1, le=50)
    note: Optional[str] = Field(default=None, max_length=200)


class UpdateItemRequest(BaseModel):
    quantity: Optional[int] = Field(default=None, ge=0, le=50)
    note: Optional[str] = Field(default=None, max_length=200)


class SubmitOrderRequest(BaseModel):
    confirmation_token: str
    draft_version: int


class OrderChange(BaseModel):
    """One change to a sent order: add a dish, set a line's quantity (0 removes it) or remove a line."""
    op: Literal["add", "set_quantity", "remove"]
    product_id: Optional[str] = Field(default=None, max_length=36)
    item_id: Optional[str] = Field(default=None, max_length=36)
    quantity: Optional[int] = Field(default=None, ge=0, le=50)
    note: Optional[str] = Field(default=None, max_length=200)


class AmendOrderRequest(BaseModel):
    operations: List[OrderChange] = Field(min_length=1, max_length=20)
    # The order version the guest was looking at; a newer one means "review again".
    expected_version: Optional[int] = None


# Restaurant & Menu Management Schemas
class UpdateProductAvailabilityRequest(BaseModel):
    is_available: bool


class CreateProductRequest(BaseModel):
    category_id: str = Field(min_length=1)
    name_ar: str = Field(min_length=1, max_length=150)
    name_en: str = Field(min_length=1, max_length=150)
    description_ar: Optional[str] = Field(default=None, max_length=500)
    description_en: Optional[str] = Field(default=None, max_length=500)
    price_minor: int = Field(gt=0)
    is_available: bool = Field(default=True)
    image_asset_url: Optional[str] = Field(default=None, max_length=255, pattern=SITE_IMAGE_PATH)


class UpdateProductRequest(BaseModel):
    category_id: Optional[str] = Field(default=None)
    name_ar: Optional[str] = Field(default=None, min_length=1, max_length=150)
    name_en: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description_ar: Optional[str] = Field(default=None, max_length=500)
    description_en: Optional[str] = Field(default=None, max_length=500)
    price_minor: Optional[int] = Field(default=None, gt=0)
    is_available: Optional[bool] = Field(default=None)
    image_asset_url: Optional[str] = Field(default=None, max_length=255, pattern=SITE_IMAGE_PATH)


class OpeningHourInput(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    opens_at: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    closes_at: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    # A note for the day, e.g. "closed during Friday prayer". Left out: the saved note stays.
    notes_ar: Optional[str] = Field(default=None, max_length=200)
    notes_en: Optional[str] = Field(default=None, max_length=200)


class UpdateRestaurantRequest(BaseModel):
    name_ar: Optional[str] = Field(default=None, min_length=1, max_length=100)
    name_en: Optional[str] = Field(default=None, min_length=1, max_length=100)
    about_ar: Optional[str] = Field(default=None, min_length=1)
    about_en: Optional[str] = Field(default=None, min_length=1)
    phone: Optional[str] = Field(default=None, min_length=1, max_length=50)
    branch_address_ar: Optional[str] = Field(default=None, max_length=255)
    branch_address_en: Optional[str] = Field(default=None, max_length=255)
    branch_phone: Optional[str] = Field(default=None, max_length=50)
    branch_name_ar: Optional[str] = Field(default=None, min_length=1, max_length=100)
    branch_name_en: Optional[str] = Field(default=None, min_length=1, max_length=100)
    opening_hours: Optional[List[OpeningHourInput]] = None
