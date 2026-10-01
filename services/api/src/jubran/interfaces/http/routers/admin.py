import json
from typing import List, Optional
from fastapi import APIRouter, Depends, status, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.infrastructure.db.models import UserModel
from jubran.application.qr_service import QrService
from jubran.application.restaurant_service import RestaurantService
from jubran.application.menu_service import MenuService
from jubran.application.ai.semantic_retrieval import SemanticKnowledgeService
from jubran.application.events import record_event
from jubran.interfaces.http.dependencies import require_admin
from jubran.interfaces.http.schemas import (
    TableAdminItem,
    UpdateRestaurantRequest, CreateProductRequest, UpdateProductRequest, UpdateProductAvailabilityRequest
)
from jubran.domain.exceptions import BusinessRuleError
from jubran.interfaces.http.rate_limits import per_admin
from jubran.infrastructure.images import detect_image_type

async def announce_product_change(db: AsyncSession, event_type: str, product_id: str,
                                  extra: Optional[dict] = None) -> None:
    """Tell open admin screens (live feed) that the menu changed."""
    record_event(db, event_type, "product", product_id, {"product_id": product_id, **(extra or {})})
    await db.commit()


router = APIRouter(prefix="/api/v1/admin", tags=["Admin Operations"])


class CategoryInput(BaseModel):
    id: Optional[str] = None
    name_ar: str = Field(min_length=1, max_length=100)
    name_en: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(ge=0, le=100000)


class ReplaceCategoriesRequest(BaseModel):
    categories: List[CategoryInput] = Field(min_length=1, max_length=100)


MAX_PRODUCT_IMAGE_BYTES = 8 * 1024 * 1024


@router.get("/tables", response_model=List[TableAdminItem])
async def list_tables(
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """List physical tables and their QR status (Admin only)."""
    return await QrService.list_tables(db)


@router.post("/tables/{table_id}/qr", response_model=TableAdminItem, dependencies=[per_admin("admin_qr")])
async def create_table_qr(
    table_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Create a QR for the table. Any current QR for that table stops working (Admin only)."""
    return await QrService.create_table_qr(db, table_id)


@router.delete("/tables/{table_id}/qr", response_model=TableAdminItem, dependencies=[per_admin("admin_qr")])
async def deactivate_table_qr(
    table_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Disable the table's QR without issuing a new one (Admin only)."""
    return await QrService.deactivate_table_qr(db, table_id)


# ----------------------------------------------------
# Restaurant Profile Endpoints
# ----------------------------------------------------
@router.get("/restaurant")
async def get_restaurant(
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Retrieve full restaurant profile and branch settings (Admin only)."""
    return await RestaurantService.get_restaurant_info(db)


@router.patch("/restaurant")
async def update_restaurant(
    req: UpdateRestaurantRequest,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Update restaurant profile and branch details (Admin only)."""
    updated = await RestaurantService.update_restaurant_info(
        db,
        name_ar=req.name_ar,
        name_en=req.name_en,
        about_ar=req.about_ar,
        about_en=req.about_en,
        phone=req.phone,
        branch_address_ar=req.branch_address_ar,
        branch_address_en=req.branch_address_en,
        branch_phone=req.branch_phone,
        branch_name_ar=req.branch_name_ar,
        branch_name_en=req.branch_name_en,
        # Only the fields sent: a day saved without its note keeps the note it had.
        opening_hours=[day.model_dump(exclude_unset=True) for day in req.opening_hours] if req.opening_hours is not None else None,
    )
    await SemanticKnowledgeService.refresh_after_admin_change(db)
    return updated


@router.get("/menu/categories")
async def admin_list_categories(admin: UserModel = Depends(require_admin),
                                db: AsyncSession = Depends(get_db_session)):
    return {"categories": await MenuService.list_categories_for_admin(db)}


@router.put("/menu/categories")
async def replace_categories(req: ReplaceCategoriesRequest,
                             admin: UserModel = Depends(require_admin),
                             db: AsyncSession = Depends(get_db_session)):
    categories = await MenuService.replace_categories(db, [item.model_dump() for item in req.categories])
    await SemanticKnowledgeService.refresh_after_admin_change(db)
    return {"categories": categories}


# ----------------------------------------------------
# Menu Products Management Endpoints
# ----------------------------------------------------
@router.get("/menu/products")
async def admin_list_products(
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Retrieve all products with categories and availability status (Admin only)."""
    return await MenuService.get_products(db, available_only=False)


@router.post("/menu/products", status_code=status.HTTP_201_CREATED)
async def admin_create_product(
    req: CreateProductRequest,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Create a new product in the menu (Admin only)."""
    new_prod = await MenuService.create_product(
        db,
        category_id=req.category_id,
        name_ar=req.name_ar,
        name_en=req.name_en,
        price_minor=req.price_minor,
        description_ar=req.description_ar,
        description_en=req.description_en,
        is_available=req.is_available,
        image_asset_url=req.image_asset_url
    )
    await SemanticKnowledgeService.refresh_after_admin_change(db)
    await announce_product_change(db, "product.created", new_prod["id"])
    return new_prod


@router.patch("/menu/products/{product_id}")
async def admin_update_product(
    product_id: str,
    req: UpdateProductRequest,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Update approved product editable fields: name, description, price, category, etc (Admin only)."""
    updated = await MenuService.update_product(
        db,
        product_id=product_id,
        category_id=req.category_id,
        name_ar=req.name_ar,
        name_en=req.name_en,
        description_ar=req.description_ar,
        description_en=req.description_en,
        price_minor=req.price_minor,
        is_available=req.is_available,
        image_asset_url=req.image_asset_url
    )
    await SemanticKnowledgeService.refresh_after_admin_change(db)
    await announce_product_change(db, "product.updated", updated["id"])
    return updated


@router.put("/menu/products/{product_id}/images")
async def admin_replace_product_images(
    product_id: str,
    retained_image_ids: str = Form(default="[]"),
    files: List[UploadFile] = File(default=[]),
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Replace the full product image set; omitted old images are removed."""
    try:
        retained_ids = json.loads(retained_image_ids)
    except json.JSONDecodeError:
        retained_ids = None
    if not isinstance(retained_ids, list) or any(not isinstance(value, str) for value in retained_ids):
        raise BusinessRuleError("صيغة الصور الحالية غير صحيحة.", "VALIDATION_ERROR", 422)
    if len(retained_ids) + len(files) > 5:
        raise BusinessRuleError("يمكن إضافة 5 صور كحد أقصى لكل صنف.", "VALIDATION_ERROR", 422)

    new_images = []
    for file in files:
        data = await file.read(MAX_PRODUCT_IMAGE_BYTES + 1)
        if not data:
            raise BusinessRuleError("لا يمكن رفع صورة فارغة.", "VALIDATION_ERROR", 422)
        if len(data) > MAX_PRODUCT_IMAGE_BYTES:
            raise BusinessRuleError("يجب ألا يتجاوز حجم الصورة الواحدة 8 ميغابايت.", "VALIDATION_ERROR", 422)
        # The file's own bytes decide its type; a renamed script or HTML page is refused.
        content_type = detect_image_type(data)
        if content_type is None:
            raise BusinessRuleError("أنواع الصور المدعومة: JPG وPNG وWEBP وGIF.", "VALIDATION_ERROR", 422)
        new_images.append({"content_type": content_type, "data": data})

    updated = await MenuService.replace_product_images(db, product_id, retained_ids, new_images)
    await announce_product_change(db, "product.updated", updated["id"])
    return updated


@router.patch("/menu/products/{product_id}/availability")
async def admin_toggle_availability(
    product_id: str,
    req: UpdateProductAvailabilityRequest,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Toggle product availability on/off authoritatively (Admin only)."""
    updated = await MenuService.update_product_availability(
        db,
        product_id=product_id,
        is_available=req.is_available
    )
    await SemanticKnowledgeService.refresh_after_admin_change(db)
    await announce_product_change(db, "product.availability_changed", updated["id"],
                                  {"is_available": updated["is_available"]})
    return updated


@router.delete("/menu/products/{product_id}")
async def admin_delete_product(
    product_id: str,
    admin: UserModel = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Delete a product from the menu authoritatively (Admin only)."""
    deleted = await MenuService.delete_product(db, product_id)
    await SemanticKnowledgeService.refresh_after_admin_change(db)
    await announce_product_change(db, "product.deleted", product_id)
    return {"success": True, "deleted": deleted}
