"""Integration tests for Service Requests, Staff Calling, Complaints, and Feedback."""
import pytest
from jubran.infrastructure.db.seed import seed_database
from helpers import sign_in, start_visit


@pytest.mark.asyncio
async def test_service_requests_and_complaints_flow(client, db_session, new_browser):
    await seed_database(db_session)

    # 1. Start QR Table Session (T5) on the guest's phone
    guest = client
    await start_visit(guest, db_session, "T5")

    # Admin Login on the staff device
    admin = new_browser()
    await sign_in(admin)

    # 2. Call Staff / Human Handover (REQ-010, REQ-020)
    staff_res = await guest.post(
        "/api/v1/service-requests",
        json={"type": "STAFF"}
    )
    assert staff_res.status_code == 200
    staff_data = staff_res.json()
    assert staff_data["type"] == "STAFF"
    assert staff_data["status"] == "OPEN"
    assert staff_data["is_duplicate"] is False
    staff_req_id = staff_data["request_id"]

    # 3. Duplicate request suppression (REQ-009)
    # Repeated call within seconds should return is_duplicate=True without creating duplicate record
    staff_repeat = await guest.post(
        "/api/v1/service-requests",
        json={"type": "STAFF"}
    )
    assert staff_repeat.status_code == 200
    assert staff_repeat.json()["is_duplicate"] is True
    assert staff_repeat.json()["request_id"] == staff_req_id

    # 4. Request Bill (REQ-009)
    bill_res = await guest.post(
        "/api/v1/service-requests",
        json={"type": "BILL"}
    )
    assert bill_res.status_code == 200
    assert bill_res.json()["type"] == "BILL"

    # 5. Check admin floor snapshot shows service_requested and bill_requested overlays
    floor_res = await admin.get("/api/v1/admin/floor")
    t5 = next(t for t in floor_res.json()["tables"] if t["table_number"] == "T5")
    assert t5["overlays"]["service_requested"] is True
    assert t5["overlays"]["bill_requested"] is True

    # 6. Staff take the request, then mark it done (OPEN -> IN_PROGRESS -> RESOLVED)
    start_srv = await admin.post(f"/api/v1/admin/service-requests/{staff_req_id}/start")
    assert start_srv.status_code == 200 and start_srv.json()["status"] == "IN_PROGRESS"
    resolve_srv = await admin.post(f"/api/v1/admin/service-requests/{staff_req_id}/resolve")
    assert resolve_srv.status_code == 200
    assert resolve_srv.json()["status"] == "RESOLVED"

    # 7. Submit Complaint (REQ-013)
    complaint_res = await guest.post(
        "/api/v1/complaints",
        json={"message": "الطاولة تحتاج لمسح إضافي بسرعة من فضلكم.", "category": "cleanliness"}
    )
    assert complaint_res.status_code == 200
    complaint_id = complaint_res.json()["complaint_id"]
    assert complaint_res.json()["status"] == "OPEN"

    # Verify admin floor shows has_complaint overlay
    floor_res2 = await admin.get("/api/v1/admin/floor")
    t5_complaint = next(t for t in floor_res2.json()["tables"] if t["table_number"] == "T5")
    assert t5_complaint["overlays"]["has_complaint"] is True

    # Management follows the complaint up, then closes it
    start_comp = await admin.post(f"/api/v1/admin/complaints/{complaint_id}/start")
    assert start_comp.status_code == 200
    resolve_comp = await admin.post(f"/api/v1/admin/complaints/{complaint_id}/resolve")
    assert resolve_comp.status_code == 200
    assert resolve_comp.json()["status"] == "RESOLVED"

    # 8. Submit Feedback after bill (REQ-018) — there is a meal to rate once something was ordered
    products = (await guest.get("/api/v1/menu/products")).json()
    await guest.post("/api/v1/draft/items", json={"product_id": products[0]["id"], "quantity": 1})
    prepared = (await guest.post("/api/v1/draft/prepare-confirmation")).json()
    await guest.post("/api/v1/orders", json={"confirmation_token": prepared["confirmation_token"],
                                             "draft_version": prepared["draft_version"]})
    feedback_res = await guest.post(
        "/api/v1/feedback",
        json={"rating": 5, "comment": "خدمة رائعة وطعام أصيل كالعادة!"}
    )
    assert feedback_res.status_code == 200
    assert feedback_res.json()["rating"] == 5

    # Validate feedback rating bounds (reject 0 or 6)
    bad_feedback = await guest.post(
        "/api/v1/feedback",
        json={"rating": 7}
    )
    assert bad_feedback.status_code == 422
