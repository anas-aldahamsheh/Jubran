"""Integration tests for Restaurant & Menu Management (REQ-024)."""
import pytest
from jubran.infrastructure.db.menu_data import MENU
from jubran.infrastructure.db.seed import seed_database
from helpers import USER_EMAIL, USER_PASSWORD, sign_in, start_visit


@pytest.mark.asyncio
async def test_restaurant_and_menu_management_flow(client, db_session, new_browser):
    await seed_database(db_session)

    # 1. Admin Login
    admin = client
    await sign_in(admin)
    guest = new_browser()

    # 2. Get Restaurant Info (Admin)
    admin_rest_res = await admin.get("/api/v1/admin/restaurant")
    assert admin_rest_res.status_code == 200
    rest_data = admin_rest_res.json()
    assert rest_data["name_ar"] == "جبران"
    assert rest_data["branch"]["name_ar"] == "بوليفارد العبدلي"
    assert len(rest_data["branch"]["opening_hours"]) == 7
    # Jubran opens 09:00 to 01:30 every day (no notes) and has a single branch.
    assert all((day["opens_at"], day["closes_at"], day["notes_ar"]) == ("09:00", "01:30", None)
               for day in rest_data["branch"]["opening_hours"])
    assert rest_data["other_branches"] == []

    # 3. Public Restaurant Profile
    public_rest_res = await client.get("/api/v1/restaurant")
    assert public_rest_res.status_code == 200
    assert public_rest_res.json()["name_ar"] == "جبران"

    # 4. Update Restaurant Info (Admin)
    patch_rest_res = await admin.patch(
        "/api/v1/admin/restaurant",
        json={
            "phone": "+962 6 500 9999",
            "about_ar": "مطعم على سطح بوليفارد العبدلي في عمّان",
            "branch_phone": "+962 6 500 8888"
        }
    )
    assert patch_rest_res.status_code == 200
    updated_rest = patch_rest_res.json()
    assert updated_rest["phone"] == "+962 6 500 9999"
    assert updated_rest["about_ar"] == "مطعم على سطح بوليفارد العبدلي في عمّان"
    assert updated_rest["branch"]["phone"] == "+962 6 500 8888"

    # Hours saved without their notes keep the notes; an empty note clears it.
    with_friday_note = [{"day_of_week": d["day_of_week"], "opens_at": d["opens_at"], "closes_at": d["closes_at"],
                         **({"notes_ar": "مغلق خلال صلاة الجمعة", "notes_en": "Closed during Friday prayer"}
                            if d["day_of_week"] == 5 else {})}
                        for d in rest_data["branch"]["opening_hours"]]
    noted = await admin.patch("/api/v1/admin/restaurant", json={"opening_hours": with_friday_note})
    assert noted.status_code == 200
    hours_without_notes = [{"day_of_week": d["day_of_week"], "opens_at": d["opens_at"], "closes_at": "02:00"}
                           for d in rest_data["branch"]["opening_hours"]]
    kept = (await admin.patch("/api/v1/admin/restaurant", json={"opening_hours": hours_without_notes})).json()
    friday = next(day for day in kept["branch"]["opening_hours"] if day["day_of_week"] == 5)
    assert friday["closes_at"] == "02:00" and "صلاة الجمعة" in friday["notes_ar"]
    cleared_notes = [dict(day, notes_ar="", notes_en="") for day in hours_without_notes]
    cleared = (await admin.patch("/api/v1/admin/restaurant", json={"opening_hours": cleared_notes})).json()
    assert all(day["notes_ar"] is None for day in cleared["branch"]["opening_hours"])

    # 5. List Menu Products (Admin)
    prods_res = await admin.get("/api/v1/admin/menu/products")
    assert prods_res.status_code == 200
    products = prods_res.json()
    assert len(products) == sum(len(dishes) for _, _, dishes in MENU)

    target_prod = next(p for p in products if p["name_ar"] == "حمص")
    prod_id = target_prod["id"]

    # 6. Toggle Product Availability to False (REQ-024)
    toggle_off_res = await admin.patch(
        f"/api/v1/admin/menu/products/{prod_id}/availability",
        json={"is_available": False}
    )
    assert toggle_off_res.status_code == 200
    assert toggle_off_res.json()["is_available"] is False

    # 7. Verify customer menu immediately reflects unavailable
    cust_menu_res = await client.get("/api/v1/menu/products")
    assert cust_menu_res.status_code == 200
    hummus_cust = next(p for p in cust_menu_res.json() if p["id"] == prod_id)
    assert hummus_cust["is_available"] is False

    # 8. Verify customer cannot add unavailable item to draft
    await start_visit(guest, db_session, "T1")

    add_res = await guest.post(
        "/api/v1/draft/items",
        json={"product_id": prod_id, "quantity": 1}
    )
    assert add_res.status_code == 400
    assert add_res.json()["detail"]["error"]["code"] == "PRODUCT_UNAVAILABLE"

    # 9. Toggle Availability back to True (REQ-024)
    toggle_on_res = await admin.patch(
        f"/api/v1/admin/menu/products/{prod_id}/availability",
        json={"is_available": True}
    )
    assert toggle_on_res.status_code == 200
    assert toggle_on_res.json()["is_available"] is True

    # Customer can now add it
    add_success_res = await guest.post(
        "/api/v1/draft/items",
        json={"product_id": prod_id, "quantity": 1}
    )
    assert add_success_res.status_code == 200
    assert add_success_res.json()["item_count"] == 1

    # 10. Update Product Details (name, description, price)
    patch_prod_res = await admin.patch(
        f"/api/v1/admin/menu/products/{prod_id}",
        json={
            "name_ar": "حمص ممتاز بالزيت البلدي",
            "price_minor": 3650,
            "description_ar": "حمص ناعم بزيت الزيتون البكر البلدي الفاخر"
        }
    )
    assert patch_prod_res.status_code == 200
    updated_prod = patch_prod_res.json()
    assert updated_prod["name_ar"] == "حمص ممتاز بالزيت البلدي"
    assert updated_prod["price_minor"] == 3650
    assert updated_prod["price_display_ar"] == "3.65 د.أ"

    # 11. Validation: negative or zero price rejected
    bad_price_res = await admin.patch(
        f"/api/v1/admin/menu/products/{prod_id}",
        json={"price_minor": 0}
    )
    assert bad_price_res.status_code in [400, 422]

    # 12. Unauthorized access rejected for regular users
    member = new_browser()
    await sign_in(member, USER_EMAIL, USER_PASSWORD)

    forbidden_res = await member.get("/api/v1/admin/restaurant")
    assert forbidden_res.status_code == 403


@pytest.mark.asyncio
async def test_create_and_delete_product_flow(client, db_session, new_browser):
    """Test full lifecycle of adding, updating, ordering, and deleting a product."""
    await seed_database(db_session)

    # 1. Admin Login
    admin = client
    await sign_in(admin)
    guest = new_browser()

    # 2. Get categories to pick a category_id
    cat_res = await client.get("/api/v1/menu/categories")
    assert cat_res.status_code == 200
    categories = cat_res.json()
    assert len(categories) > 0
    cat_id = categories[0]["id"]

    # 3. Create a brand new product (POST /api/v1/admin/menu/products)
    create_res = await admin.post(
        "/api/v1/admin/menu/products",
        json={
            "category_id": cat_id,
            "name_ar": "فلافل محشية عين جمل وجبنة",
            "name_en": "Walnut & Cheese Stuffed Falafel",
            "description_ar": "حبات فلافل ذهبية مقرمشة محشية بعين الجمل والجبن البلدي",
            "description_en": "Crispy golden falafel balls stuffed with walnuts and local cheese",
            "price_minor": 2750,
            "is_available": True
        }
    )
    assert create_res.status_code == 201
    new_prod = create_res.json()
    assert new_prod["name_ar"] == "فلافل محشية عين جمل وجبنة"
    assert new_prod["price_minor"] == 2750
    assert new_prod["price_display_ar"] == "2.75 د.أ"
    new_prod_id = new_prod["id"]

    # 4. Verify product appears in public customer menu
    menu_res = await client.get("/api/v1/menu/products")
    assert menu_res.status_code == 200
    all_products = menu_res.json()
    assert any(p["id"] == new_prod_id for p in all_products)

    # 5. Customer starts session and adds the new product to draft
    await start_visit(guest, db_session, "T1")

    add_res = await guest.post(
        "/api/v1/draft/items",
        json={"product_id": new_prod_id, "quantity": 2, "note": "مع شطة إضافية"}
    )
    assert add_res.status_code == 200

    # 6. Delete the product as Admin (DELETE /api/v1/admin/menu/products/{id})
    del_res = await admin.delete(
        f"/api/v1/admin/menu/products/{new_prod_id}"
    )
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["success"] is True
    assert del_data["deleted"]["id"] == new_prod_id

    # 7. Verify product is gone from customer menu and admin menu
    menu_after_del = await client.get("/api/v1/menu/products")
    assert not any(p["id"] == new_prod_id for p in menu_after_del.json())

    admin_prods_after = await admin.get("/api/v1/admin/menu/products")
    assert not any(p["id"] == new_prod_id for p in admin_prods_after.json())

    # 8. Deleting a non-existent product returns 404
    del_again = await admin.delete(
        f"/api/v1/admin/menu/products/{new_prod_id}"
    )
    assert del_again.status_code == 404

