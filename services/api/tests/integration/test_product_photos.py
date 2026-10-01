"""Dish photos are files on disk, served with long caching; old in-database photos are moved out."""
import pytest
import uuid

from sqlalchemy import select

from jubran.application.media_migration import move_photos_to_files
from jubran.infrastructure.db.models import ProductImageModel
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.media_store import media_store
from helpers import sign_in

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x02" * 64


async def first_product(admin):
    return (await admin.get("/api/v1/admin/menu/products")).json()[0]


async def upload(admin, product_id, files, keep=()):
    return await admin.put(f"/api/v1/admin/menu/products/{product_id}/images",
                           data={"retained_image_ids": __import__("json").dumps(list(keep))},
                           files=[("files", (name, data, "image/png")) for name, data in files])


async def stored(db, product_id):
    # Every seeded dish has its own photos too: only this dish's are looked at.
    db.expire_all()
    return (await db.execute(select(ProductImageModel.id, ProductImageModel.storage_key, ProductImageModel.image_data)
                             .where(ProductImageModel.product_id == product_id)
                             .order_by(ProductImageModel.sort_order))).all()


@pytest.mark.asyncio
async def test_photos_are_saved_as_files_and_cached(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    product = await first_product(client)
    response = await upload(client, product["id"], [("a.png", PNG), ("b.jpg", JPEG)])
    assert response.status_code == 200, response.text

    rows = await stored(db_session, product["id"])
    assert len(rows) == 2 and all(key and data is None for _, key, data in rows)  # nothing in the database
    files = [media_store().path(key) for _, key, _ in rows]
    assert [path.read_bytes() for path in files] == [PNG, JPEG]

    served = await client.get(f"/api/v1/menu/products/{product['id']}/images/{rows[0][0]}")
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert "immutable" in served.headers["cache-control"]

    # Keep the second photo only: the first file goes away with its record.
    assert (await upload(client, product["id"], [], keep=[rows[1][0]])).status_code == 200
    assert not files[0].exists() and files[1].exists()

    # Deleting the dish removes its photos too.
    assert (await client.delete(f"/api/v1/admin/menu/products/{product['id']}")).status_code == 200
    assert not files[1].exists()


@pytest.mark.asyncio
async def test_photos_kept_in_the_database_by_older_versions_move_to_files(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    product = await first_product(client)
    photo_id = str(uuid.uuid4())
    db_session.add(ProductImageModel(id=photo_id, product_id=product["id"], content_type="image/png", image_data=PNG,
                                     sort_order=5))
    await db_session.commit()
    url = f"/api/v1/menu/products/{product['id']}/images/{photo_id}"
    assert (await client.get(url)).content == PNG  # readable before the move

    assert await move_photos_to_files(db_session) == 1
    assert await move_photos_to_files(db_session) == 0  # nothing left
    (_, key, data), = [row for row in await stored(db_session, product["id"]) if row[0] == photo_id]
    assert key and data is None and media_store().path(key).read_bytes() == PNG
    assert (await client.get(url)).content == PNG  # and after


def test_media_keys_cannot_leave_the_media_folder(tmp_path):
    from jubran.infrastructure.media_store import MediaStore
    store = MediaStore(tmp_path)
    with pytest.raises(ValueError):
        store.path("../outside.png")
