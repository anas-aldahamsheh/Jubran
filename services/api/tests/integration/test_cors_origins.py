"""Which website addresses may call the API (CORS); the assistant itself is covered elsewhere."""
import pytest


@pytest.mark.asyncio
async def test_lan_frontend_origin_is_allowed_for_development(client):
    response = await client.options(
        "/api/v1/admin/floor",
        headers={"Origin": "http://172.29.48.1:3001", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://172.29.48.1:3001"
