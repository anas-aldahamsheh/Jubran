"""Menu HTTP Router."""
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from jubran.infrastructure.db.session import get_db_session
from jubran.application.menu_service import MenuService
from jubran.domain.exceptions import EntityNotFoundException
from jubran.infrastructure.images import detect_image_type
from jubran.infrastructure.media_store import media_store

router = APIRouter(prefix="/api/v1/menu", tags=["Menu"])


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db_session)):
    return await MenuService.get_categories(db)


@router.get("/products")
async def list_products(
    category_id: Optional[str] = Query(None),
    available_only: bool = Query(False),
    db: AsyncSession = Depends(get_db_session)
):
    return await MenuService.get_products(db, category_id=category_id, available_only=available_only)


@router.get("/products/{product_id}")
async def get_product_details(
    product_id: str,
    db: AsyncSession = Depends(get_db_session)
):
    return await MenuService.get_product(db, product_id)


@router.get("/products/{product_id}/images/{image_id}")
async def get_product_image(
    product_id: str,
    image_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    image = await MenuService.get_product_image(db, product_id, image_id)
    # A photo never changes under its id (a new upload gets a new id): cache it for a year.
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    if image.storage_key:
        try:
            path = media_store().path(image.storage_key)
        except ValueError:
            path = None
        if path is None or not path.is_file():
            raise EntityNotFoundException("ProductImage", image_id)
        return FileResponse(path, media_type=image.content_type, headers=headers)
    # A photo from before files were used, not moved yet: serve the type the bytes really are.
    data = image.image_data or b""
    media_type = detect_image_type(data)
    if media_type is None:
        media_type = "application/octet-stream"
        headers["Content-Disposition"] = "attachment"
    return Response(content=data, media_type=media_type, headers=headers)
