"""A guest cancels their own service request (staff, tissues, table cleaning, bill) until staff
mark it done: from the table service panel, or by asking the assistant. Another guest's request
at the table stays, and staff see that the guest cancelled it."""
import pytest
from sqlalchemy import select

from jubran.infrastructure.db.models import ServiceRequestModel
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit
from test_model_agent import model_call, model_response, script_model
from test_waiter_logic import chat, debug_mode  # noqa: F401 (fixture)


def error_code(response):
    return response.json()["detail"]["error"]["code"]


async def can_cancel(browser):
    return {request["type"]: request["can_cancel"] for request in (await browser.get("/api/v1/service-requests")).json()}


@pytest.mark.asyncio
async def test_a_guest_cancels_their_request_until_it_is_done(client, db_session, new_browser):
    await seed_database(db_session)
    guest, other, admin = client, new_browser(), new_browser()
    await sign_in(admin)
    await start_visit(guest, db_session, "T5")
    await start_visit(other, db_session, "T5")
    staff, bill, tissues = [(await guest.post("/api/v1/service-requests", json={"type": kind})).json()["request_id"]
                            for kind in ("STAFF", "BILL", "TISSUES")]

    # Still waiting for the staff: cancelled at once.
    assert (await can_cancel(guest))["STAFF"] is True
    cancelled = await guest.post(f"/api/v1/service-requests/{staff}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"

    # Being handled already: the guest can still cancel it, and staff acting on it learn why it's gone.
    assert (await admin.post(f"/api/v1/admin/service-requests/{bill}/start")).status_code == 200
    assert (await can_cancel(guest))["BILL"] is True
    assert (await guest.post(f"/api/v1/service-requests/{bill}/cancel")).status_code == 200
    late = await admin.post(f"/api/v1/admin/service-requests/{bill}/resolve")
    assert late.status_code == 409 and error_code(late) == "SERVICE_REQUEST_CANCELLED"
    assert late.json()["detail"]["error"]["message"] == "الزبون ألغى هذا الطلب"
    late = await admin.post(f"/api/v1/admin/service-requests/{staff}/start")
    assert late.status_code == 409 and error_code(late) == "SERVICE_REQUEST_CANCELLED"

    # Another guest at the table can't cancel it; once staff finished it, nobody can.
    assert (await can_cancel(other))["TISSUES"] is False
    assert (await other.post(f"/api/v1/service-requests/{tissues}/cancel")).status_code == 404
    assert (await admin.post(f"/api/v1/admin/service-requests/{tissues}/start")).status_code == 200
    assert (await admin.post(f"/api/v1/admin/service-requests/{tissues}/resolve")).status_code == 200
    assert (await can_cancel(guest))["TISSUES"] is False
    done = await guest.post(f"/api/v1/service-requests/{tissues}/cancel")
    assert done.status_code == 409 and error_code(done) == "REQUEST_NOT_CANCELLABLE"

    # The floor: nothing open at T5 any more, and staff see what the guest cancelled.
    floor = (await admin.get("/api/v1/admin/floor")).json()
    t5 = next(table for table in floor["tables"] if table["table_number"] == "T5")
    assert t5["overlays"]["service_requested"] is False and t5["overlays"]["bill_requested"] is False
    statuses = {item["id"]: item["status"] for item in floor["service_queue"] if item["kind"] == "SERVICE"}
    assert (statuses[staff], statuses[bill], statuses[tissues]) == ("CANCELLED", "CANCELLED", "RESOLVED")


@pytest.mark.asyncio
async def test_the_assistant_cancels_only_the_guests_own_request(client, db_session, new_browser, monkeypatch,
                                                                 debug_mode):  # noqa: F811
    await seed_database(db_session)
    other = new_browser()
    await start_visit(client, db_session, "T2")
    await start_visit(other, db_session, "T2")
    await client.post("/api/v1/service-requests", json={"type": "STAFF"})
    await other.post("/api/v1/service-requests", json={"type": "BILL"})
    chats = script_model(monkeypatch,
        [model_response(model_call("cancel_service", type="STAFF"),
                        model_call("cancel_service", type="STAFF")),  # the same again: refused
         model_response(text="تمام، لغيت طلب الموظف.")],
        [model_response(model_call("cancel_service", type="BILL"),
                        model_call("cancel_service", type="TISSUES")),
         model_response(text="الحساب طلبه حدا ثاني عالطاولة، وما في طلب محارم.")],
    )

    first = await chat(client, "الغي طلب الموظف")
    assert first["done"] == [{"kind": "service_cancelled", "type": "STAFF"}]
    assert [call["result"].get("error_code") for call in first["tool_calls"]] == [None, "DUPLICATE_ACTION_THIS_TURN"]
    requests = {request["type"]: request for request in chats[0].context["open_service_requests"]}
    assert (requests["STAFF"]["by_this_guest"], requests["BILL"]["by_this_guest"]) == (True, False)

    second = await chat(client, "والحساب كمان الغيه، والمحارم")
    assert second["done"] == []
    results = [call["result"] for call in second["tool_calls"]]
    assert [result["error_code"] for result in results] == ["ANOTHER_GUESTS_REQUEST", "NO_OPEN_REQUEST"]
    assert all(result["hint"].startswith("Nothing was cancelled") for result in results)
    assert chats[1].context["your_last_reply_did"] == first["done"]
    assert [request["type"] for request in chats[1].context["open_service_requests"]] == ["BILL"]

    statuses = {request.type.value: request.status.value
                for request in (await db_session.execute(select(ServiceRequestModel))).scalars().all()}
    assert statuses == {"STAFF": "CANCELLED", "BILL": "OPEN"}
    history = (await client.get("/api/v1/assistant/history")).json()["history"]
    assert history[1]["done"] == first["done"]
