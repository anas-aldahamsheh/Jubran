"""Input rules that hold for every way in (pages and assistant)."""
import pytest
from sqlalchemy import select

from jubran.application.service_request_service import CustomerServiceManager
from jubran.domain.exceptions import BusinessRuleError
from jubran.infrastructure.db.models import FeedbackModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit


async def order_tea(guest):
    products = (await guest.get("/api/v1/menu/products")).json()
    await guest.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 1})
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    await guest.post("/api/v1/orders", json={"confirmation_token": prepared["confirmation_token"],
                                             "draft_version": prepared["draft_version"]})


@pytest.mark.asyncio
async def test_feedback_comes_after_ordering_and_once_per_guest(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    early = await client.post("/api/v1/feedback", json={"rating": 5})
    assert early.status_code == 409 and early.json()["detail"]["error"]["code"] == "FEEDBACK_TOO_EARLY"

    await order_tea(client)
    assert (await client.post("/api/v1/feedback", json={"rating": 3, "comment": "طيب"})).status_code in (200, 201)
    again = await client.post("/api/v1/feedback", json={"rating": 5})
    assert again.status_code in (200, 201) and again.json().get("updated") is True
    ratings = (await db_session.execute(select(FeedbackModel.rating))).scalars().all()
    assert ratings == [5]  # one rating per guest, the latest one


@pytest.mark.asyncio
async def test_an_empty_complaint_is_refused_even_from_the_assistant(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    context = (await client.get("/api/v1/session/context")).json()
    with pytest.raises(BusinessRuleError):
        await CustomerServiceManager.submit_complaint(db_session, context["table_session_id"],
                                                      context["customer_session_id"], "  ")


@pytest.mark.asyncio
@pytest.mark.parametrize("link,accepted", [
    ("/images/hummus.png", True), ("", True),
    ("https://tracker.example.com/pixel.gif", False), ("//tracker.example.com/p.png", False),
    ("/images/page.html", False), ("javascript:alert(1)", False),
])
async def test_dish_image_links_stay_on_this_site(client, db_session, link, accepted):
    await seed_database(db_session)
    await sign_in(client)
    product = (await client.get("/api/v1/admin/menu/products")).json()[0]
    response = await client.patch(f"/api/v1/admin/menu/products/{product['id']}", json={"image_asset_url": link})
    assert (response.status_code == 200) is accepted, response.text
    if not accepted:
        assert "رابط الصورة" in response.json()["detail"]["error"]["message"]


@pytest.mark.asyncio
async def test_quantity_zero_removes_the_line_even_with_a_note(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    products = (await client.get("/api/v1/menu/products")).json()
    draft = (await client.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 2})).json()
    line_id = draft["items"][0]["line_id"]
    after = await client.patch(f"/api/v1/draft/items/{line_id}", json={"quantity": 0, "note": "بدون بصل"})
    assert after.status_code == 200 and after.json()["items"] == []
