"""Photos of the seeded menu's dishes, shipped with the code in menu_photos/.

menu_photos/manifest.json maps each dish (by the slug of its English name) to its
photo file; a few similar dishes share one file. Where every photo comes from, and
its licence, is listed in menu_photos/CREDITS.md.
"""
import json
import re
import unicodedata
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.db.models import ProductImageModel, ProductModel
from jubran.infrastructure.images import detect_image_type
from jubran.infrastructure.media_store import media_store

PHOTO_DIR = Path(__file__).with_name("menu_photos")


def photo_slug(name_en: str) -> str:
    """The key of a dish's photo: "Rigatoni Arrabbiata or Rosé" -> "rigatoni-arrabbiata-or-rose"."""
    plain = unicodedata.normalize("NFKD", name_en).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower().replace("&", " and ")).strip("-")


@lru_cache(maxsize=1)
def _manifest() -> dict[str, str]:
    path = PHOTO_DIR / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def photo_file(name_en: str) -> Optional[Path]:
    """The shipped photo of the dish with this English name, if there is one."""
    name = _manifest().get(photo_slug(name_en))
    path = PHOTO_DIR / name if name else None
    return path if path is not None and path.is_file() else None


async def add_missing_menu_photos(session: AsyncSession) -> int:
    """Give each dish without any photo its shipped photo; returns how many got one.

    Dishes that already have a photo (uploaded or linked) are left as they are. The
    caller commits.
    """
    with_photos = set((await session.execute(select(ProductImageModel.product_id).distinct())).scalars())
    store = media_store()
    added = 0
    for product in (await session.execute(select(ProductModel))).scalars().all():
        if product.id in with_photos or product.image_asset_url:
            continue
        path = photo_file(product.name_en)
        if path is None:
            continue
        data = path.read_bytes()
        content_type = detect_image_type(data)
        if content_type is None:
            continue
        image_id = str(uuid.uuid4())
        session.add(ProductImageModel(id=image_id, product_id=product.id, content_type=content_type,
                                      storage_key=store.save(data, content_type), sort_order=0))
        product.image_asset_url = f"/api/v1/menu/products/{product.id}/images/{image_id}"
        product.version += 1
        added += 1
    await session.flush()
    return added
