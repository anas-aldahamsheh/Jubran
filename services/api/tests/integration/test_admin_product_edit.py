"""Editing a dish: emptied fields are really removed."""
import pytest

from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in


@pytest.mark.asyncio
async def test_admin_can_remove_a_description_and_image_link(client, db_session):
    await seed_database(db_session)
    await sign_in(client)
    product = (await client.get("/api/v1/admin/menu/products")).json()[0]
    edited = await client.patch(f"/api/v1/admin/menu/products/{product['id']}", json={
        "description_ar": "وصف مؤقت", "description_en": "Temporary", "image_asset_url": "/images/x.png"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["description_ar"] == "وصف مؤقت"

    # What the edit dialog sends when the admin empties the fields.
    cleared = await client.patch(f"/api/v1/admin/menu/products/{product['id']}", json={
        "name_ar": product["name_ar"], "description_ar": "", "description_en": "", "image_asset_url": ""})
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert not body["description_ar"] and not body["description_en"] and not body.get("image_asset_url")
