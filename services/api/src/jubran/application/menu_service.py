"""Menu Application Service."""
from typing import List, Optional, Dict, Any
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, func
from sqlalchemy.orm import selectinload, defer
from jubran.infrastructure.db.models import MenuCategoryModel, ProductModel, ProductImageModel, DraftItemModel, DraftOrderModel, OrderItemModel, RestaurantModel
from jubran.domain.enums import DraftStatus
from jubran.domain.exceptions import BusinessRuleError, EntityNotFoundException
from jubran.domain.money import format_jod, fils_to_jod
from jubran.application.ai.content_state import bump_content_version
from jubran.infrastructure.media_store import media_store


async def invalidate_open_drafts_with(db: AsyncSession, product_id: str) -> None:
    """A price/availability change or removal makes every pending confirmation of an
    open basket holding this dish stale, so the guest must review it again."""
    affected = select(DraftItemModel.draft_order_id).where(DraftItemModel.product_id == product_id)
    await db.execute(
        update(DraftOrderModel)
        .where(DraftOrderModel.status == DraftStatus.OPEN, DraftOrderModel.id.in_(affected))
        .values(version=DraftOrderModel.version + 1)
    )


class MenuService:
    @staticmethod
    async def list_categories_for_admin(db: AsyncSession) -> List[Dict[str, Any]]:
        categories = (await db.execute(select(MenuCategoryModel).order_by(
            MenuCategoryModel.sort_order, MenuCategoryModel.id
        ))).scalars().all()
        return [{"id": c.id, "name_ar": c.name_ar, "name_en": c.name_en, "sort_order": c.sort_order}
                for c in categories]

    @staticmethod
    async def replace_categories(db: AsyncSession, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Save the admin's full category list: rename, reorder, add, and delete empty ones."""
        restaurant_id = (await db.execute(select(RestaurantModel.id).limit(1))).scalar_one()
        existing = {c.id: c for c in (await db.execute(select(MenuCategoryModel).where(
            MenuCategoryModel.restaurant_id == restaurant_id
        ))).scalars().all()}
        incoming_ids = [item["id"] for item in items if item.get("id")]
        if any(not item["name_ar"].strip() or not item["name_en"].strip() for item in items):
            raise BusinessRuleError("اسم القسم بالعربية والإنجليزية مطلوب.", "EMPTY_CATEGORY_NAME", 422)
        if len(incoming_ids) != len(set(incoming_ids)) or any(cid not in existing for cid in incoming_ids):
            raise BusinessRuleError("معرّف تصنيف غير صالح أو مكرر.", "INVALID_CATEGORY_ID", 422)
        removed = set(existing) - set(incoming_ids)
        if removed:
            used = (await db.execute(select(ProductModel.category_id).where(
                ProductModel.category_id.in_(removed)
            ).limit(1))).scalar_one_or_none()
            if used:
                raise BusinessRuleError("انقل أصناف التصنيف أو احذفها قبل حذف التصنيف.", "CATEGORY_HAS_PRODUCTS", 409)
        for item in items:
            if item.get("id"):
                category = existing[item["id"]]
            else:
                category = MenuCategoryModel(restaurant_id=restaurant_id)
                db.add(category)
            category.name_ar = item["name_ar"].strip()
            category.name_en = item["name_en"].strip()
            category.sort_order = item["sort_order"]
            category.is_active = True
        for cid in removed:
            await db.delete(existing[cid])
        await bump_content_version(db)
        await db.commit()
        return await MenuService.list_categories_for_admin(db)

    @staticmethod
    async def get_categories(db: AsyncSession) -> List[Dict[str, Any]]:
        """Retrieve all existing menu categories."""
        stmt = (
            select(MenuCategoryModel)
            .order_by(MenuCategoryModel.sort_order)
        )
        cats = (await db.execute(stmt)).scalars().all()
        return [
            {
                "id": c.id,
                "name_ar": c.name_ar,
                "name_en": c.name_en,
                "sort_order": c.sort_order
            }
            for c in cats
        ]

    @staticmethod
    async def get_products(
        db: AsyncSession,
        category_id: Optional[str] = None,
        available_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Retrieve products with authoritative pricing and availability."""
        stmt = select(ProductModel).options(
            selectinload(ProductModel.images).defer(ProductImageModel.image_data)
        ).order_by(ProductModel.sort_order, ProductModel.name_ar)
        if category_id:
            stmt = stmt.where(ProductModel.category_id == category_id)
        if available_only:
            stmt = stmt.where(ProductModel.is_available == True)

        products = (await db.execute(stmt)).scalars().all()
        return [
            {
                "id": p.id,
                "category_id": p.category_id,
                "name_ar": p.name_ar,
                "name_en": p.name_en,
                "description_ar": p.description_ar,
                "description_en": p.description_en,
                "price_minor": p.price_minor,
                "price_jod": str(fils_to_jod(p.price_minor)),
                "price_display_ar": format_jod(p.price_minor, "ar"),
                "price_display_en": format_jod(p.price_minor, "en"),
                "is_available": p.is_available,
                "image_asset_url": p.image_asset_url,
                "images": MenuService._format_product_images(p)
            }
            for p in products
        ]

    @staticmethod
    async def get_product(db: AsyncSession, product_id: str) -> Dict[str, Any]:
        """Retrieve single product details."""
        stmt = select(ProductModel).options(
            selectinload(ProductModel.images).defer(ProductImageModel.image_data)
        ).where(ProductModel.id == product_id)
        p = (await db.execute(stmt)).scalar_one_or_none()
        if not p:
            raise EntityNotFoundException("Product", product_id)

        return {
            "id": p.id,
            "category_id": p.category_id,
            "name_ar": p.name_ar,
            "name_en": p.name_en,
            "description_ar": p.description_ar,
            "description_en": p.description_en,
            "price_minor": p.price_minor,
            "price_jod": str(fils_to_jod(p.price_minor)),
            "price_display_ar": format_jod(p.price_minor, "ar"),
            "price_display_en": format_jod(p.price_minor, "en"),
            "is_available": p.is_available,
            "image_asset_url": p.image_asset_url,
            "images": MenuService._format_product_images(p)
        }

    @staticmethod
    def _format_product_images(product: ProductModel) -> List[Dict[str, Any]]:
        images = [MenuService._format_image(product.id, image) for image in product.images]
        if product.image_asset_url and all(image["url"] != product.image_asset_url for image in images):
            images.insert(0, {
                "id": "legacy",
                "url": product.image_asset_url,
                "content_type": "",
                "sort_order": -1,
                "legacy": True,
            })
        return images

    @staticmethod
    def _format_image(product_id: str, image: ProductImageModel) -> Dict[str, Any]:
        return {
            "id": image.id,
            "url": f"/api/v1/menu/products/{product_id}/images/{image.id}",
            "content_type": image.content_type,
            "sort_order": image.sort_order,
        }

    @staticmethod
    async def replace_product_images(
        db: AsyncSession,
        product_id: str,
        retained_image_ids: List[str],
        new_images: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Replace the product's complete image set in a single commit."""
        product = (await db.execute(select(ProductModel).where(ProductModel.id == product_id))).scalar_one_or_none()
        if not product:
            raise EntityNotFoundException("Product", product_id)
        keep_legacy = "legacy" in retained_image_ids
        keep_ids = [image_id for image_id in retained_image_ids if image_id != "legacy"]
        if len(keep_ids) + len(new_images) + int(keep_legacy) > 5:
            raise BusinessRuleError("يمكن إضافة 5 صور كحد أقصى لكل صنف.", "VALIDATION_ERROR", 422)
        if len(set(retained_image_ids)) != len(retained_image_ids):
            raise BusinessRuleError("قائمة الصور تحتوي على تكرار.", "VALIDATION_ERROR", 422)

        existing = (await db.execute(
            select(ProductImageModel).options(defer(ProductImageModel.image_data))
            .where(ProductImageModel.product_id == product_id)
        )).scalars().all()
        existing_by_id = {image.id: image for image in existing}
        if any(image_id not in existing_by_id for image_id in keep_ids):
            raise BusinessRuleError("بعض الصور المختارة لا تخص هذا الصنف.", "VALIDATION_ERROR", 422)
        if keep_legacy and not product.image_asset_url:
            raise BusinessRuleError("الصورة القديمة لم تعد متاحة.", "VALIDATION_ERROR", 422)

        keep = set(keep_ids)
        removed_files = []
        for image in existing:
            if image.id not in keep:
                removed_files.append(image.storage_key)
                await db.delete(image)

        for index, image_id in enumerate(keep_ids):
            existing_by_id[image_id].sort_order = index

        # New photos are written as files first; the database only records their keys.
        store = media_store()
        saved_files = []
        for index, image in enumerate(new_images, start=len(keep_ids)):
            key = store.save(image["data"], image["content_type"])
            saved_files.append(key)
            db.add(ProductImageModel(
                product_id=product_id,
                content_type=image["content_type"],
                storage_key=key,
                sort_order=index,
            ))

        # Preserve a pre-existing external image only when it was retained.
        if not keep_legacy:
            product.image_asset_url = None
        product.version += 1
        await db.flush()
        if not keep_legacy:
            first_image = (await db.execute(
                select(ProductImageModel)
                .where(ProductImageModel.product_id == product_id)
                .order_by(ProductImageModel.sort_order)
            )).scalars().first()
            if first_image:
                product.image_asset_url = MenuService._format_image(product_id, first_image)["url"]
        try:
            await db.commit()
        except Exception:
            for key in saved_files:  # nothing points at them
                store.delete(key)
            raise
        for key in removed_files:
            store.delete(key)
        return await MenuService.get_product(db, product_id)

    @staticmethod
    async def get_product_image(db: AsyncSession, product_id: str, image_id: str) -> ProductImageModel:
        image = (await db.execute(
            select(ProductImageModel).where(
                ProductImageModel.product_id == product_id,
                ProductImageModel.id == image_id,
            )
        )).scalar_one_or_none()
        if image is None:
            raise EntityNotFoundException("ProductImage", image_id)
        return image

    @staticmethod
    async def create_product(
        db: AsyncSession,
        category_id: str,
        name_ar: str,
        name_en: str,
        price_minor: int,
        description_ar: Optional[str] = None,
        description_en: Optional[str] = None,
        is_available: bool = True,
        image_asset_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new product in the menu."""
        cat_stmt = select(MenuCategoryModel).where(MenuCategoryModel.id == category_id)
        category = (await db.execute(cat_stmt)).scalar_one_or_none()
        if not category:
            raise EntityNotFoundException("MenuCategory", category_id)

        clean_name_ar = name_ar.strip() if name_ar else ""
        clean_name_en = name_en.strip() if name_en else ""
        if not clean_name_ar:
            raise BusinessRuleError("اسم الصنف بالعربية مطلوب.", "VALIDATION_ERROR", 422)
        if not clean_name_en:
            raise BusinessRuleError("اسم الصنف بالإنجليزية مطلوب.", "VALIDATION_ERROR", 422)
        if price_minor <= 0:
            raise BusinessRuleError("سعر الصنف يجب أن يكون أكبر من صفر.", "VALIDATION_ERROR", 422)

        max_sort_stmt = (
            select(func.coalesce(func.max(ProductModel.sort_order), 0))
            .where(ProductModel.category_id == category_id)
        )
        max_sort = (await db.execute(max_sort_stmt)).scalar() or 0

        new_product = ProductModel(
            id=str(uuid.uuid4()),
            category_id=category_id,
            name_ar=clean_name_ar,
            name_en=clean_name_en,
            description_ar=description_ar.strip() if description_ar else None,
            description_en=description_en.strip() if description_en else None,
            price_minor=price_minor,
            currency="JOD",
            is_available=is_available,
            image_asset_url=image_asset_url.strip() if image_asset_url else None,
            sort_order=max_sort + 10,
            version=1
        )
        db.add(new_product)
        await bump_content_version(db)
        await db.commit()
        await db.refresh(new_product)
        return await MenuService.get_product(db, new_product.id)

    @staticmethod
    async def update_product_availability(
        db: AsyncSession,
        product_id: str,
        is_available: bool
    ) -> Dict[str, Any]:
        """Update product availability status authoritatively."""
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        product = (await db.execute(stmt)).scalar_one_or_none()
        if not product:
            raise EntityNotFoundException("Product", product_id)

        if product.is_available != is_available:
            await invalidate_open_drafts_with(db, product_id)
        product.is_available = is_available
        product.version += 1
        await bump_content_version(db)
        await db.commit()
        await db.refresh(product)
        return await MenuService.get_product(db, product_id)

    @staticmethod
    async def update_product(
        db: AsyncSession,
        product_id: str,
        category_id: Optional[str] = None,
        name_ar: Optional[str] = None,
        name_en: Optional[str] = None,
        description_ar: Optional[str] = None,
        description_en: Optional[str] = None,
        price_minor: Optional[int] = None,
        is_available: Optional[bool] = None,
        image_asset_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update approved product editable fields: name, description, price, category, etc."""
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        product = (await db.execute(stmt)).scalar_one_or_none()
        if not product:
            raise EntityNotFoundException("Product", product_id)

        if category_id is not None:
            cat_stmt = select(MenuCategoryModel).where(MenuCategoryModel.id == category_id)
            category = (await db.execute(cat_stmt)).scalar_one_or_none()
            if not category:
                raise EntityNotFoundException("MenuCategory", category_id)
            product.category_id = category_id

        if name_ar is not None:
            clean_name_ar = name_ar.strip()
            if not clean_name_ar:
                raise BusinessRuleError("اسم الصنف بالعربية مطلوب.", "VALIDATION_ERROR", 422)
            product.name_ar = clean_name_ar

        if name_en is not None:
            clean_name_en = name_en.strip()
            if not clean_name_en:
                raise BusinessRuleError("اسم الصنف بالإنجليزية مطلوب.", "VALIDATION_ERROR", 422)
            product.name_en = clean_name_en

        if description_ar is not None:
            product.description_ar = description_ar.strip() or None

        if description_en is not None:
            product.description_en = description_en.strip() or None

        if price_minor is not None:
            if price_minor <= 0:
                raise BusinessRuleError("سعر الصنف يجب أن يكون أكبر من صفر.", "VALIDATION_ERROR", 422)
            if price_minor != product.price_minor:
                await invalidate_open_drafts_with(db, product_id)
            product.price_minor = price_minor

        if is_available is not None:
            if is_available != product.is_available:
                await invalidate_open_drafts_with(db, product_id)
            product.is_available = is_available

        if image_asset_url is not None:
            product.image_asset_url = image_asset_url.strip() or None

        product.version += 1
        await bump_content_version(db)
        await db.commit()
        await db.refresh(product)
        return await MenuService.get_product(db, product_id)

    @staticmethod
    async def delete_product(db: AsyncSession, product_id: str) -> Dict[str, Any]:
        """Delete product from menu authoritatively."""
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        product = (await db.execute(stmt)).scalar_one_or_none()
        if not product:
            raise EntityNotFoundException("Product", product_id)

        deleted_summary = {
            "id": product.id,
            "name_ar": product.name_ar,
            "name_en": product.name_en
        }

        # Baskets holding this dish change: their confirmations must be reviewed again.
        await invalidate_open_drafts_with(db, product_id)
        await db.execute(delete(DraftItemModel).where(DraftItemModel.product_id == product_id))
        # Nullify any historical order item references so past receipts remain intact
        await db.execute(
            update(OrderItemModel)
            .where(OrderItemModel.product_id == product_id)
            .values(product_id=None)
        )

        photo_files = (await db.execute(
            select(ProductImageModel.storage_key).where(ProductImageModel.product_id == product_id)
        )).scalars().all()
        await db.delete(product)
        await bump_content_version(db)
        await db.commit()
        store = media_store()
        for key in photo_files:
            store.delete(key)
        return deleted_summary
