"""Basket changes made by the assistant are all-or-nothing."""
import pytest
from sqlalchemy import select

from jubran.application.ai.tools import AssistantToolExecutor
from jubran.application.ordering_service import OrderingService
from jubran.infrastructure.db.models import CustomerSessionModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit


async def executor_for(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T4")
    customer = (await db_session.execute(select(CustomerSessionModel))).scalars().first()
    products = (await db_session.execute(select(ProductModel).where(ProductModel.is_available == True))).scalars().all()  # noqa: E712
    return AssistantToolExecutor(db_session, customer.id, customer.table_session_id, "T4"), customer, products


async def basket(db_session, customer):
    return await OrderingService.get_draft_summary(db_session, customer.id, customer.table_session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_operation, code", [
    ({"op": "add", "quantity": 51}, "INVALID_ADD"),
    ({"op": "set_quantity", "line_id": "missing", "quantity": 2}, "DRAFT_LINE_NOT_FOUND"),
    ({"op": "explode"}, "INVALID_OPERATION"),
])
async def test_one_bad_operation_saves_nothing(client, db_session, bad_operation, code):
    executor, customer, products = await executor_for(client, db_session)
    if bad_operation.get("op") == "add":
        bad_operation = {**bad_operation, "product_id": products[2].id}
    result = await executor.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": products[0].id, "quantity": 2},
        {"op": "add", "product_id": products[1].id, "quantity": 1},
        bad_operation,
    ]})
    assert result["success"] is False
    assert (result["error_code"], result["failed_operation_index"], result["basket_changed"]) == (code, 2, False)
    assert (await basket(db_session, customer))["items"] == []


@pytest.mark.asyncio
async def test_retrying_after_a_failure_never_duplicates_items(client, db_session):
    executor, customer, products = await executor_for(client, db_session)
    first = {"op": "add", "product_id": products[0].id, "quantity": 2}
    failed = await executor.execute("update_draft_order", {"operations": [first, {"op": "remove", "line_id": "missing"}]})
    assert failed["success"] is False
    ok = await executor.execute("update_draft_order", {"operations": [first]})
    assert ok["success"] is True
    lines = (await basket(db_session, customer))["items"]
    assert [(line["product_id"], line["quantity"]) for line in lines] == [(products[0].id, 2)]


@pytest.mark.asyncio
async def test_line_quantity_can_never_pass_fifty(client, db_session):
    executor, customer, products = await executor_for(client, db_session)
    assert (await executor.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": products[0].id, "quantity": 40}]}))["success"]
    over = await executor.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": products[0].id, "quantity": 20}]})
    assert over["error_code"] == "QUANTITY_LIMIT"
    assert (await basket(db_session, customer))["items"][0]["quantity"] == 40


@pytest.mark.asyncio
async def test_mixed_changes_apply_together(client, db_session):
    executor, customer, products = await executor_for(client, db_session)
    added = await executor.execute("update_draft_order", {"operations": [
        {"op": "add", "product_id": products[0].id, "quantity": 1},
        {"op": "add", "product_id": products[1].id, "quantity": 1}]})
    first_line, second_line = (line["line_id"] for line in added["draft"]["items"])
    changed = await executor.execute("update_draft_order", {"operations": [
        {"op": "set_quantity", "line_id": first_line, "quantity": 3},
        {"op": "remove", "line_id": second_line},
        {"op": "add", "product_id": products[2].id, "quantity": 2}]})
    assert changed["success"] is True
    items = (await basket(db_session, customer))["items"]
    assert sorted((item["product_id"], item["quantity"]) for item in items) == sorted(
        [(products[0].id, 3), (products[2].id, 2)])
