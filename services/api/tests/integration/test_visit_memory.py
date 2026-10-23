"""What the assistant knows about this visit, and what each reply really did.

- One complaint per guest per visit: what they add later becomes a timed line on it (only the
  new part), and a resolved complaint opens again. Staff see one complaint with its lines.
- A new rating replaces the stars and keeps the earlier comment.
- Each turn's facts carry the table's open service requests, the guest's complaint and rating,
  and what the last reply did; the guest's screen gets the same list of what was done.
"""
import json

import pytest
from sqlalchemy import select

from jubran.application.service_request_service import merged_comment, new_part
from jubran.infrastructure.db.models import ComplaintModel, OutboxEventModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit
from test_model_agent import model_call, model_response, script_model
from test_order_amendments import place_order
from test_waiter_logic import chat, debug_mode  # noqa: F401 (fixture)


def test_a_later_complaint_adds_only_what_is_new():
    earlier = "الشاي بارد"
    assert new_part(earlier, "الشاي بارد") == ""
    assert new_part(earlier, "  الشاي   بارد!! ") == ""
    assert new_part(earlier, "الشاي بارد، وبدي حدا يغيره") == "وبدي حدا يغيره"
    assert new_part(earlier, "والخدمة بطيئة كمان") == "والخدمة بطيئة كمان"
    lines = "The tea was cold\n[14:05] nobody came"
    assert new_part(lines, "the tea was COLD") == ""
    assert new_part(lines, "Nobody came. And the bill is wrong") == "And the bill is wrong"


def test_a_new_rating_comment_keeps_the_earlier_one():
    assert merged_comment(None, "الأكل زاكي") == "الأكل زاكي"
    assert merged_comment("الأكل زاكي", None) == "الأكل زاكي"
    assert merged_comment("الأكل زاكي", "بس الخدمة بطيئة") == "الأكل زاكي\nبس الخدمة بطيئة"
    assert merged_comment("الأكل زاكي", "الأكل زاكي") == "الأكل زاكي"
    assert merged_comment("x" * 450, "y" * 100) == ("x" * 450 + "\n" + "y" * 100)[:500]


@pytest.mark.asyncio
async def test_one_complaint_per_guest_per_visit(client, db_session, new_browser):
    await seed_database(db_session)
    guest, admin, other = client, new_browser(), new_browser()
    await sign_in(admin)
    await start_visit(guest, db_session, "T5")
    await start_visit(other, db_session, "T5")

    first = (await guest.post("/api/v1/complaints", json={"message": "الشاي بارد"})).json()
    assert first["added_to_earlier"] is False
    again = (await guest.post("/api/v1/complaints", json={"message": "الشاي بارد"})).json()
    assert (again["complaint_id"], again["nothing_new"]) == (first["complaint_id"], True)
    more = (await guest.post("/api/v1/complaints", json={"message": "الشاي بارد، وما حدا إجا"})).json()
    assert more["complaint_id"] == first["complaint_id"] and more["nothing_new"] is False
    theirs = (await other.post("/api/v1/complaints", json={"message": "الطاولة مش نظيفة"})).json()
    assert theirs["complaint_id"] != first["complaint_id"]  # another guest's complaint is their own

    complaint = (await db_session.execute(select(ComplaintModel).where(
        ComplaintModel.id == first["complaint_id"]))).scalar_one()
    await db_session.refresh(complaint)
    head, added = complaint.message.split("\n")
    assert head == "الشاي بارد" and added.endswith("] وما حدا إجا") and added.startswith("[")
    floor = (await admin.get("/api/v1/admin/floor")).json()
    queued = {item["id"]: item for item in floor["service_queue"] if item["kind"] == "COMPLAINT"}
    assert len(queued) == 2  # one per guest, not one per message
    assert queued[first["complaint_id"]]["message"] == complaint.message

    # Staff dealt with it; the guest writes again: the same complaint opens again with the new line.
    assert (await admin.post(f"/api/v1/admin/complaints/{first['complaint_id']}/start")).status_code == 200
    assert (await admin.post(f"/api/v1/admin/complaints/{first['complaint_id']}/resolve")).status_code == 200
    reopened = (await guest.post("/api/v1/complaints", json={"message": "رجع الشاي بارد"})).json()
    assert (reopened["complaint_id"], reopened["status"], reopened["reopened"]) == (first["complaint_id"], "OPEN", True)
    events = (await db_session.execute(select(OutboxEventModel).where(
        OutboxEventModel.event_type == "complaint.updated"))).scalars().all()
    assert [json.loads(event.payload_json)["reopened"] for event in events] == [False, True]


@pytest.mark.asyncio
async def test_a_new_rating_keeps_the_earlier_comment(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T2")
    await place_order(client, [("شاي", 1)])
    first = (await client.post("/api/v1/feedback", json={"rating": 5, "comment": "الأكل زاكي"})).json()
    assert (first["rating"], first["comment"]) == (5, "الأكل زاكي")
    stars_only = (await client.post("/api/v1/feedback", json={"rating": 4})).json()
    assert (stars_only["rating"], stars_only["comment"]) == (4, "الأكل زاكي")
    more = (await client.post("/api/v1/feedback", json={"rating": 3, "comment": "بس الخدمة بطيئة"})).json()
    assert (more["rating"], more["comment"]) == (3, "الأكل زاكي\nبس الخدمة بطيئة")


@pytest.mark.asyncio
async def test_each_turn_knows_what_this_visit_holds(client, db_session, monkeypatch, debug_mode):  # noqa: F811
    await seed_database(db_session)
    await start_visit(client, db_session, "T2")
    order, _ = await place_order(client, [("شاي", 2)])
    chats = script_model(monkeypatch,
        [model_response(model_call("request_service", type="STAFF"),
                        model_call("submit_complaint", message="الشاي بارد")),
         model_response(text="آسفين! ضفت شكواك، وفي حدا جاي لعندك.")],
        [model_response(model_call("submit_complaint", message="الشاي بارد وما حدا إجا"),
                        model_call("submit_feedback", rating=2, comment="الشاي بارد")),
         model_response(text="ضفتها لشكواك، وسجلت تقييمك.")],
        [model_response(text="الإدارة عندها شكواك.")],
    )

    first = await chat(client, "الشاي بارد، ابعتلي حدا")
    assert first["done"] == [{"kind": "service", "type": "STAFF", "already_open": False},
                             {"kind": "complaint", "added_to_earlier": False}]
    context = chats[0].context
    assert context["my_sent_orders"][0]["items"][0]["quantity"] == 2  # sent orders come with their items
    assert "open_service_requests" not in context and "my_complaint" not in context

    second = await chat(client, "وما حدا إجا لهلأ، وبعطيكم 2 من 5")
    assert second["done"] == [{"kind": "complaint", "added_to_earlier": True},
                              {"kind": "rating", "stars": 2, "comment": "الشاي بارد"}]
    context = chats[1].context
    [request] = context["open_service_requests"]
    assert (request["type"], request["status"], request["by_this_guest"]) == ("STAFF", "OPEN", True)
    assert request["minutes_ago"] == 0 and len(request["since"]) == 5  # "HH:MM", restaurant time
    assert (context["my_complaint"]["status"], context["my_complaint"]["text"]) == ("OPEN", "الشاي بارد")
    assert context["your_last_reply_did"] == first["done"]

    await chat(client, "شو صار بشكواي؟")
    context = chats[2].context
    assert context["my_complaint"]["text"].startswith("الشاي بارد\n[")
    assert context["my_rating"] == {"stars": 2, "comment": "الشاي بارد"}
    assert context["your_last_reply_did"] == second["done"]
    assert order["order_number"] == context["my_sent_orders"][0]["order_number"]
