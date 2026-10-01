"""Move dish photos that are still stored inside the database out to files (once, at start-up)."""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.db.models import ProductImageModel
from jubran.infrastructure.images import detect_image_type
from jubran.infrastructure.media_store import media_store

logger = logging.getLogger("jubran.media")


async def move_photos_to_files(db: AsyncSession) -> int:
    """One photo at a time (never all of them in memory); safe to run again or in several workers."""
    store = media_store()
    moved = 0
    while True:
        photo_id = (await db.execute(
            select(ProductImageModel.id).where(ProductImageModel.storage_key.is_(None),
                                               ProductImageModel.image_data.is_not(None)).limit(1)
        )).scalar_one_or_none()
        if photo_id is None:
            break
        photo = (await db.execute(select(ProductImageModel).where(ProductImageModel.id == photo_id)
                                  .with_for_update())).scalar_one_or_none()
        if photo is None or photo.storage_key or photo.image_data is None:
            await db.commit()
            continue  # another worker moved it meanwhile
        content_type = detect_image_type(photo.image_data) or photo.content_type
        key = store.save(photo.image_data, content_type)
        photo.storage_key, photo.content_type, photo.image_data = key, content_type, None
        try:
            await db.commit()
        except Exception:
            store.delete(key)
            raise
        moved += 1
    if moved:
        logger.info("Moved %d dish photo(s) from the database to %s.", moved, store.root)
    return moved
