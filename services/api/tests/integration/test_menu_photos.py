"""The seeded menu ships with a photo for every dish."""
import pytest
from sqlalchemy import select

from jubran.infrastructure.db.menu_data import MENU
from jubran.infrastructure.db.models import ProductImageModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.db.seed_photos import add_missing_menu_photos, photo_file, photo_slug
from jubran.infrastructure.media_store import media_store


def test_every_dish_on_the_menu_has_a_photo():
    missing = [name_en for _, _, dishes in MENU for _, name_en, *_ in dishes if photo_file(name_en) is None]
    assert missing == []


def test_photo_keys_are_plain_slugs():
    assert photo_slug("Rigatoni Arrabbiata or Rosé") == "rigatoni-arrabbiata-or-rose"
    assert photo_slug("Kale & Quinoa Salad") == "kale-and-quinoa-salad"


@pytest.mark.asyncio
async def test_a_new_menu_gets_its_photos_and_photos_are_never_added_twice(client, db_session):
    await seed_database(db_session, with_photos=True)
    products = (await db_session.execute(select(ProductModel))).scalars().all()
    images = (await db_session.execute(select(ProductImageModel))).scalars().all()
    assert len(images) == len(products)
    assert all(media_store().path(image.storage_key).is_file() for image in images)

    hummus = next(product for product in products if product.name_en == "Hummus")
    photo = await client.get(hummus.image_asset_url)
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/webp"

    # Every dish already has its photo, so running it again adds nothing.
    assert await add_missing_menu_photos(db_session) == 0


@pytest.mark.asyncio
async def test_dishes_that_already_have_a_photo_keep_it(db_session):
    await seed_database(db_session)  # no photos
    tea = (await db_session.execute(select(ProductModel).where(ProductModel.name_en == "Tea Selection"))).scalar_one()
    tea.image_asset_url = "/images/our-own-tea.png"
    await db_session.commit()

    added = await add_missing_menu_photos(db_session)
    await db_session.commit()
    assert added == sum(len(dishes) for _, _, dishes in MENU) - 1
    await db_session.refresh(tea)
    assert tea.image_asset_url == "/images/our-own-tea.png"
