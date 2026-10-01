"""The assistant's tools: what the model may ask for, and how the server does it.

- Identity (table, visit, guest) is injected by the server, never taken from the model.
- Prices, availability and order states always come from the live database.
- Tools return structured data. What the model finally sees is trimmed by
  ``tool_views`` (fewer tokens); server logic works on the full results here.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jubran.application.menu_service import MenuService
from jubran.application.order_amendment_service import (
    AmendmentOperationError, OrderAmendmentService, lock_reason,
)
from jubran.application.ordering_service import CUSTOMER_STATUS_LABELS, DraftOperationError, OrderingService
from jubran.application.restaurant_service import RestaurantService
from jubran.application.service_request_service import CustomerServiceManager
from jubran.application.ai.semantic_retrieval import SemanticKnowledgeService, SemanticRetrievalError
from jubran.domain.enums import OrderStatus, ServiceRequestType
from jubran.domain.exceptions import DomainException
from jubran.domain.money import format_jod
from jubran.infrastructure.db.models import (
    MenuCategoryModel, OrderItemModel, OrderModel, PhysicalTableModel, ProductModel, TableSessionModel,
)

logger = logging.getLogger(__name__)

_CHANGE_ITEMS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["add", "set_quantity", "remove"]},
            "product_id": {"type": "string", "description": "for add"},
            "item_id": {"type": "string", "description": "for set_quantity/remove: the order's item_id"},
            "quantity": {"type": "integer", "description": "1-50 (set_quantity 0 removes the line)"},
            "note": {"type": "string", "description": "the guest's preparation note, if any"},
        },
        "required": ["op"],
    },
}

TOOL_DEFINITIONS = [
    {
        "name": "search_knowledge",
        "description": "Search the menu and restaurant knowledge by meaning (dish names are also matched literally). "
                       "Write the guest's intent as a natural phrase with the context needed, not keywords. Results "
                       "are candidates: use only those that really answer the guest. Unavailable dishes are returned "
                       "with available=false.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["product", "category", "restaurant", "branch", "service", "policy"]},
                    "description": "Optional filter; leave empty for mixed questions.",
                },
                "exclude_queries": {"type": "array", "items": {"type": "string"},
                                    "description": "Meanings the guest rejected, filtered out."},
                "available_only": {"type": "boolean"},
                "sort_by_price": {"type": "boolean", "description": "Cheapest first."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_restaurant_info",
        "description": "Verified restaurant facts: name, phone, about, current_branch (the branch the guest is sitting "
                       "in: address, phone, opening hours per day with day_of_week 0=Sunday and any note for the day), "
                       "other_branches (the restaurant's other locations, same details) and open_now for the current "
                       "branch (computed in Amman time, including hours past midnight).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_product_details",
        "description": "One dish by id: description, price and current availability.",
        "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}}, "required": ["product_id"]},
    },
    {
        "name": "get_current_draft",
        "description": "The guest's basket (not sent yet): lines with line_id, quantities, notes, prices and total.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "update_draft_order",
        "description": "Change the basket (not sent yet) in one all-or-nothing call: add (product_id, quantity, "
                       "optional note), set_quantity (line_id, quantity; 0 removes) or remove (line_id). "
                       "A dish can be added only if it was shown in search/details/suggestions this turn or is "
                       "in the recently-shown list. If success=false nothing changed.",
        "parameters": {
            "type": "object",
            "properties": {"operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["add", "set_quantity", "remove"]},
                        "product_id": {"type": "string"},
                        "line_id": {"type": "string"},
                        "quantity": {"type": "integer"},
                        "note": {"type": "string"},
                    },
                    "required": ["op"],
                },
            }},
            "required": ["operations"],
        },
    },
    {
        "name": "recommend_products",
        "description": "Ideas for ONE complementary suggestion: what is in the basket and available dishes it does "
                       "not have yet (grouped by category), plus the restaurant's most ordered dishes. Use when the "
                       "guest says they are done (once per order), or with explicit_request=true when the guest asks "
                       "for a recommendation.",
        "parameters": {
            "type": "object",
            "properties": {"explicit_request": {"type": "boolean"}},
        },
    },
    {
        "name": "prepare_order_confirmation",
        "description": "When the guest has finished choosing: returns the live summary (lines, prices, total) to show "
                       "with the question whether to send it. Before an order's first summary, recommend_products "
                       "must have been called for that order. Not for answers to clarifying questions.",
        "parameters": {"type": "object", "properties": {
            "customer_finished": {"type": "boolean"},
            "no_fitting_suggestion": {"type": "boolean",
                                      "description": "true when you looked for a suggestion in this same turn and "
                                                     "nothing fits (so your reply suggests nothing)"}},
                       "required": ["customer_finished"]},
    },
    {
        "name": "submit_order",
        "description": "Send the basket to the restaurant. Only after a summary shown in an earlier message, when "
                       "the guest's current message shows they want it sent (a yes, or just declining anything more) "
                       "and doesn't change, question, postpone or turn down the order. The server re-checks prices "
                       "and availability.",
        "parameters": {"type": "object", "properties": {
            "confirmed_summary": {"type": "boolean"},
            "guest_words": {"type": "string",
                            "description": "the guest's exact words in their current message that show they want it sent"}},
                       "required": ["confirmed_summary", "guest_words"]},
    },
    {
        "name": "get_order_status",
        "description": "The table's sent orders this visit (mine=true: this guest's). Guests see three states: "
                       "waiting for confirmation, preparing, ready. editable=true means this guest can still "
                       "change it (items carry item_id).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "prepare_order_amendment",
        "description": "Prepare a change to one of the guest's own sent orders that is not ready/served yet: "
                       "more or fewer of a dish already in it (set_quantity with its item_id), remove items, or add "
                       "new dishes (add with product_id). Nothing changes yet: show the returned changes and new "
                       "total and ask the guest to confirm. Preparing again replaces a change not confirmed yet.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}, "operations": _CHANGE_ITEMS},
            "required": ["order_id", "operations"],
        },
    },
    {
        "name": "confirm_order_amendment",
        "description": "Apply the order change shown earlier, only when the guest's current message clearly "
                       "says yes to it.",
        "parameters": {"type": "object", "properties": {
            "confirmed": {"type": "boolean"},
            "guest_words": {"type": "string",
                            "description": "the guest's exact words in their current message that say yes to the change"}},
                       "required": ["confirmed", "guest_words"]},
    },
    {
        "name": "request_service",
        "description": "Ask the staff for STAFF (someone to come; also for any table need no other tool can do), "
                       "TISSUES, CLEAN_TABLE or BILL for this table. Only for a real request (not negated or hypothetical).",
        "parameters": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["STAFF", "TISSUES", "CLEAN_TABLE", "BILL"]}}, "required": ["type"]},
    },
    {
        "name": "submit_complaint",
        "description": "Record a complaint the guest actually makes (food, service, cleanliness, delay).",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string", "description": "The complaint in the guest's words."},
            "category": {"type": "string"}}, "required": ["message"]},
    },
    {
        "name": "submit_feedback",
        "description": "Record the guest's rating of the visit (1-5 stars) and optional comment. Allowed after they "
                       "ordered; a new rating replaces the previous one.",
        "parameters": {"type": "object", "properties": {
            "rating": {"type": "integer", "minimum": 1, "maximum": 5},
            "comment": {"type": "string"}}, "required": ["rating"]},
    },
]

POPULAR_WINDOW = timedelta(days=30)
MAX_OPTIONS_PER_CATEGORY = 3
MAX_POPULAR = 4


class AssistantToolExecutor:
    """Executes tools with the guest's identity injected by the server."""

    def __init__(self, db: AsyncSession, customer_session_id: str, table_session_id: str, table_number: str):
        self.db = db
        self.customer_session_id = customer_session_id
        self.table_session_id = table_session_id
        self.table_number = table_number

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Run one tool against the authoritative services."""
        try:
            if tool_name == "search_knowledge":
                result = await SemanticKnowledgeService.search(
                    self.db, query=arguments.get("query", ""), source_types=arguments.get("source_types") or None,
                    exclude_queries=arguments.get("exclude_queries"), limit=min(int(arguments.get("limit") or 8), 12),
                    available_only=bool(arguments.get("available_only", False)),
                    sort_by_price=bool(arguments.get("sort_by_price", False)),
                )
                await self._name_categories([m["product"] for m in result.get("matches") or [] if m.get("product")])
                wanted = arguments.get("source_types") or ["product"]
                if result.get("success") and "product" in wanted and not any(
                        m.get("product") for m in result.get("matches") or []):
                    # Nothing on the menu matches: real dishes the waiter can offer instead.
                    ideas = await self._recommend_products(explicit_request=True)
                    result["alternatives"] = (ideas["popular"] + ideas["options"])[:3]
                return result
            if tool_name == "get_restaurant_info":
                return {"success": True, "restaurant": await RestaurantService.get_restaurant_info(
                    self.db, branch_id=await self._guest_branch_id())}
            if tool_name == "get_product_details":
                product = await MenuService.get_product(self.db, arguments.get("product_id", ""))
                await self._name_categories([product])
                return {"product": product}
            if tool_name == "get_order_status":
                return await self._get_order_status()
            if tool_name == "get_current_draft":
                return await OrderingService.get_draft_summary(self.db, self.customer_session_id, self.table_session_id)
            if tool_name == "recommend_products":
                return await self._recommend_products(bool(arguments.get("explicit_request", False)))
            if tool_name == "update_draft_order":
                return await self._update_draft_order(arguments.get("operations") or [])
            if tool_name == "prepare_order_confirmation":
                conf = await OrderingService.prepare_confirmation(self.db, self.customer_session_id, self.table_session_id)
                return {"success": True, "draft_version": conf["draft_version"],
                        "confirmation_token": conf["confirmation_token"], "summary": conf["summary"]}
            if tool_name == "submit_confirmed_order":
                res = await OrderingService.submit_order(
                    db=self.db, customer_session_id=self.customer_session_id, table_session_id=self.table_session_id,
                    confirmation_token=arguments.get("confirmation_token", ""),
                    draft_version=arguments.get("draft_version", 1))
                return {"success": True, "order_id": res["order_id"], "order_number": res["order_number"],
                        "status": res["status"], "total_display_ar": res["total_display_ar"],
                        "total_display_en": res.get("total_display_en")}
            if tool_name == "preview_order_amendment":
                return await self._amendment(OrderAmendmentService.preview, arguments)
            if tool_name == "apply_order_amendment":
                return await self._amendment(OrderAmendmentService.apply, arguments,
                                             expected_version=arguments.get("expected_version"))
            if tool_name == "request_service":
                if not arguments.get("type"):
                    return {"success": False, "error_code": "SERVICE_TYPE_REQUIRED"}
                return await self._request_service(arguments["type"])
            if tool_name == "submit_complaint":
                res = await CustomerServiceManager.submit_complaint(
                    db=self.db, table_session_id=self.table_session_id, customer_session_id=self.customer_session_id,
                    message=arguments.get("message", ""), category=arguments.get("category"))
                return {"success": True, "complaint_id": res["complaint_id"]}
            if tool_name == "submit_feedback":
                res = await CustomerServiceManager.submit_feedback(
                    db=self.db, table_session_id=self.table_session_id, customer_session_id=self.customer_session_id,
                    rating=arguments.get("rating", 5), comment=arguments.get("comment"))
                return {"success": True, "feedback_id": res["feedback_id"], "updated": res.get("updated", False)}
            return {"success": False, "error_code": "UNKNOWN_TOOL"}
        except SemanticRetrievalError:
            raise
        except DomainException as exc:
            # An expected refusal (empty basket, closed table, unavailable dish, locked order...):
            # the model gets the precise reason so it can explain it to the guest.
            result = {"success": False, "error_code": exc.code, "error": exc.message}
            if exc.details:
                result["details"] = exc.details
            return result
        except Exception as exc:
            # Details may contain database/provider internals: logs only.
            logger.exception("Assistant tool execution failed: %s", tool_name)
            if tool_name == "search_knowledge":
                raise SemanticRetrievalError("KNOWLEDGE_SEARCH_FAILED") from exc
            return {"success": False, "error_code": "TOOL_EXECUTION_FAILED"}

    async def _guest_branch_id(self) -> Optional[str]:
        """The branch of the table this guest is sitting at."""
        return (await self.db.execute(
            select(PhysicalTableModel.branch_id)
            .join(TableSessionModel, TableSessionModel.physical_table_id == PhysicalTableModel.id)
            .where(TableSessionModel.id == self.table_session_id)
        )).scalar_one_or_none()

    async def _name_categories(self, products: List[Dict[str, Any]]) -> None:
        """Give each dish its category name (helps the model tell drinks, sandwiches and plates apart)."""
        if not products:
            return
        names = {category.id: category for category in (await self.db.execute(select(MenuCategoryModel))).scalars()}
        for product in products:
            category = names.get(product.get("category_id"))
            if category is not None:
                product["category_ar"], product["category_en"] = category.name_ar, category.name_en

    async def _amendment(self, action, arguments: Dict[str, Any], **extra) -> Dict[str, Any]:
        try:
            summary = await action(self.db, self.customer_session_id, self.table_session_id,
                                   arguments.get("order_id", ""), list(arguments.get("operations") or []), **extra)
        except AmendmentOperationError as exc:
            return {"success": False, "error_code": exc.code, "failed_operation_index": exc.index}
        return {"success": True, "amendment": summary}

    async def _get_order_status(self) -> Dict[str, Any]:
        orders = (await self.db.execute(
            select(OrderModel).where(OrderModel.table_session_id == self.table_session_id)
            .options(selectinload(OrderModel.items))
            .order_by(OrderModel.created_at.desc())
        )).scalars().all()
        result = []
        for order in orders:
            mine = order.customer_session_id == self.customer_session_id
            locked = lock_reason(order)
            labels = CUSTOMER_STATUS_LABELS.get(order.status, (order.status.value, order.status.value))
            result.append({
                "order_id": order.id,
                "order_number": order.order_number,
                "mine": mine,
                "status": order.status.value,
                "status_ar": labels[0],
                "status_en": labels[1],
                "editable": mine and locked is None,
                "locked_reason": locked,
                "total": format_jod(sum(item.line_total_minor for item in order.items), "en").replace(" JOD", ""),
                "items": [
                    {**({"item_id": item.id, "product_id": item.product_id} if mine and locked is None else {}),
                     "name_ar": item.product_name_snapshot_ar, "name_en": item.product_name_snapshot_en,
                     "quantity": item.quantity, **({"note": item.note} if item.note else {})}
                    for item in order.items
                ],
                "created_at": order.created_at.isoformat(),
            })
        return {"success": True, "orders": result}

    async def own_orders_brief(self) -> List[Dict[str, Any]]:
        """The guest's own sent orders this visit, for the turn context (small)."""
        status = await self._get_order_status()
        return [{key: order[key] for key in ("order_id", "order_number", "status_en", "editable", "locked_reason")}
                | ({"items": order["items"]} if order["editable"] else {})
                for order in status["orders"] if order["mine"]]

    async def _recommend_products(self, explicit_request: bool) -> Dict[str, Any]:
        """Material for one smart suggestion; the model chooses what fits (no fixed rules here)."""
        draft = await OrderingService.get_draft_summary(self.db, self.customer_session_id, self.table_session_id)
        in_basket = {item["product_id"] for item in draft["items"]}
        rows = (await self.db.execute(
            select(ProductModel, MenuCategoryModel)
            .join(MenuCategoryModel, ProductModel.category_id == MenuCategoryModel.id)
            .where(MenuCategoryModel.is_active.is_(True))
            .order_by(MenuCategoryModel.sort_order, ProductModel.sort_order, ProductModel.name_ar)
        )).all()
        category_of = {product.id: category for product, category in rows}
        basket = [{"name_ar": item["name_ar"], "name_en": item["name_en"], "quantity": item["quantity"],
                   "category_en": category_of[item["product_id"]].name_en if item["product_id"] in category_of else None}
                  for item in draft["items"]]
        basket_categories = {category_of[pid].id for pid in in_basket if pid in category_of}

        def option(product: ProductModel, category: MenuCategoryModel) -> Dict[str, Any]:
            return {"id": product.id, "name_ar": product.name_ar, "name_en": product.name_en,
                    "price": format_jod(product.price_minor, "en").replace(" JOD", ""),
                    "category_ar": category.name_ar, "category_en": category.name_en,
                    "description_en": product.description_en or None}

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for product, category in rows:
            if product.is_available and product.id not in in_basket:
                bucket = grouped.setdefault(category.id, [])
                if len(bucket) < MAX_OPTIONS_PER_CATEGORY:
                    bucket.append(option(product, category))
        # Categories the basket has nothing from come first: that is usually what completes a meal.
        ordered = sorted(grouped.items(), key=lambda entry: entry[0] in basket_categories)
        options = [item for _, items in ordered for item in items]

        since = datetime.now(timezone.utc) - POPULAR_WINDOW
        popular_rows = (await self.db.execute(
            select(OrderItemModel.product_id, func.sum(OrderItemModel.quantity).label("sold"))
            .join(OrderModel, OrderModel.id == OrderItemModel.order_id)
            .where(OrderModel.created_at >= since, OrderModel.status != OrderStatus.CANCELLED,
                   OrderItemModel.product_id.is_not(None))
            .group_by(OrderItemModel.product_id)
            .order_by(func.sum(OrderItemModel.quantity).desc())
            .limit(MAX_POPULAR * 3)
        )).all()
        by_id = {product.id: (product, category) for product, category in rows}
        popular = [option(*by_id[pid]) for pid, _ in popular_rows
                   if pid in by_id and by_id[pid][0].is_available and pid not in in_basket][:MAX_POPULAR]
        return {"success": True, "explicit_request": explicit_request, "basket": basket,
                "options": options, "popular": popular}

    async def _update_draft_order(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        # All operations succeed together or none is saved, so the model never
        # retries a half-applied change (which used to duplicate items).
        try:
            summary = await OrderingService.apply_draft_operations(
                self.db, self.customer_session_id, self.table_session_id, operations)
        except DraftOperationError as exc:
            return {"success": False, "error_code": exc.code, "failed_operation_index": exc.index,
                    "basket_changed": False}
        return {"success": True, "draft": summary}

    async def _request_service(self, req_type: str) -> Dict[str, Any]:
        try:
            service = ServiceRequestType(str(req_type).upper())
        except ValueError:
            return {"success": False, "error_code": "UNKNOWN_SERVICE_TYPE"}
        res = await CustomerServiceManager.create_service_request(
            db=self.db, table_session_id=self.table_session_id, customer_session_id=self.customer_session_id,
            request_type=service)
        return {"success": True, "request_id": res["request_id"], "type": res["type"], "status": res["status"],
                "is_duplicate": res["is_duplicate"]}
