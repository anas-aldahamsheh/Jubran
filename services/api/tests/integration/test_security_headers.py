"""Browser hardening headers, private error details and real image types."""
import struct
import zlib

import pytest
from sqlalchemy import select

from jubran.infrastructure.db.models import ProductImageModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.db.session import get_db_session
from jubran.main import app
from jubran.settings import settings
from helpers import sign_in


def tiny_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def assert_hardened(response):
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_every_api_response_is_hardened(client):
    menu = await client.get("/api/v1/menu/categories")
    assert_hardened(menu)
    assert menu.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in menu.headers  # plain HTTP locally

    # Rejections carry the same protection.
    forged = await client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"})
    assert forged.status_code == 403
    assert_hardened(forged)


@pytest.mark.asyncio
async def test_https_deployments_get_hsts(client, monkeypatch):
    monkeypatch.setattr(settings, "SECURE_COOKIES", True)
    response = await client.get("/health")
    assert "max-age=" in response.headers["strict-transport-security"]


@pytest.mark.asyncio
async def test_health_check_never_reveals_database_details(client):
    class BrokenSession:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("could not connect to db.internal:5432 as user admin with password hunter2")

    async def broken_db():
        yield BrokenSession()

    previous = app.dependency_overrides[get_db_session]
    app.dependency_overrides[get_db_session] = broken_db
    try:
        response = await client.get("/api/v1/health")
    finally:
        app.dependency_overrides[get_db_session] = previous
    assert response.json()["database"] == "unhealthy"
    assert "hunter2" not in response.text and "db.internal" not in response.text


@pytest.mark.asyncio
async def test_uploaded_images_are_checked_by_content(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    product = (await db_session.execute(select(ProductModel))).scalars().first()
    url = f"/api/v1/admin/menu/products/{product.id}/images"

    # A page disguised as a picture is refused.
    disguised = await client.put(url, files={"files": ("photo.png", b"<html><script>alert(1)</script>", "image/png")})
    assert disguised.status_code == 422

    # A real PNG sent with a wrong type is stored and served as PNG.
    uploaded = await client.put(url, files={"files": ("photo.bin", tiny_png(), "text/html")})
    assert uploaded.status_code == 200, uploaded.text
    image_url = uploaded.json()["images"][0]["url"]
    served = await client.get(image_url)
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
async def test_old_unrecognised_image_data_is_never_rendered(client, db_session):
    await seed_database(db_session)
    product = (await db_session.execute(select(ProductModel))).scalars().first()
    legacy = ProductImageModel(product_id=product.id, content_type="text/html", image_data=b"<svg onload=alert(1)>", sort_order=0)
    db_session.add(legacy)
    await db_session.commit()
    served = await client.get(f"/api/v1/menu/products/{product.id}/images/{legacy.id}")
    assert served.headers["content-type"] == "application/octet-stream"
    assert served.headers["content-disposition"] == "attachment"
