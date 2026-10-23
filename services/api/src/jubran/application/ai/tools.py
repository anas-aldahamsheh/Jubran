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
from jubran.application.ordering_service import DraftOperationError, OrderingService, customer_status_labels
from jubran.application.restaurant_service import RestaurantService
from jubran.application.service_request_service import CustomerServiceManager
from jubran.application.serving_units import category_names, dish_units
from jubran.application.ai.semantic_retrieval import SemanticKnowledgeService, SemanticRetrievalError
from jubran.application.opening_hours import RESTAURANT_TZ
from jubran.domain.enums import OrderStatus, ServiceRequestStatus, ServiceRequestType
from jubran.domain.exceptions import DomainException
from jubran.domain.money import format_jod
from jubran.domain.units import serving_units
from jubran.infrastructure.db.models import (
    ComplaintModel, FeedbackModel, MenuCategoryModel, OrderItemModel, OrderModel, PhysicalTableModel, ProductModel,
    ServiceRequestModel, TableSessionModel,
)

logger = logging.getLogger(__name__)

_UNIT = "the dish's unit as the MENU writes it (needed when it has several, e.g. a plate or a box)"
_GAVE = ("true only when the guest said this quantity (in any message of the conversation) or said yes to a "
         "quantity you proposed; never assumed")
_NOTE = ("how the guest wants it prepared or served, in their own words and language (e.g. بدون سكر، بدون ثلج، "
         "سفري). update: the line's new note (\"\" removes it); split: the note for that part")
_QUANTITY = ("add: how many (1-50); update: the line's new quantity (0 removes it); split: how many of the line "
             "get the new note or unit")
_OPS = ("add (product_id, quantity, unit, guest_gave_quantity, optional note); update a line ({line} with any of "
        "quantity, note, unit); split part of a line off with its own note and/or unit ({line}, quantity, note "
        "and/or unit; the rest stays as it was); remove a line ({line})")


def _operations(line: str) -> Dict[str, Any]:
    """The changes to a basket or a sent order (``line`` names the id of a line in it)."""
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["add", "update", "split", "remove"]},
                "product_id": {"type": "string", "description": "for add: the dish's MENU id"},
                line: {"type": "string", "description": "for update, split and remove"},
                "quantity": {"type": "integer", "description": _QUANTITY},
                "unit": {"type": "string", "description": f"add: {_UNIT}; update/split: the new unit"},
                "guest_gave_quantity": {"type": "boolean", "description": f"for add: {_GAVE}"},
                "note": {"type": "string", "description": _NOTE},
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
        "description": "Change the basket (not sent yet) in one all-or-nothing call: " + _OPS.format(line="line_id")
                       + ". Add each dish as soon as it's clear (which dish, how many, and its unit when it has "
                       "several), even while you still ask about others. If success=false nothing changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "operations": _operations("line_id"),
                "still_asking": {"type": "boolean",
                                 "description": "true when your reply still asks the guest about other dishes "
                                                "of this order (how many, which one, or which way it's served)"},
                "guest_finished": {"type": "boolean",
                                   "description": "true when the guest's message also says they want nothing more "
                                                  "(\"وبس\", \"بس هيك\", \"that's all\"): the order summary is "
                                                  "prepared in the same call, to show and ask whether to send it"},
            },
            "required": ["operations"],
        },
    },
    {
        "name": "recommend_products",
        "description": "Ideas when the guest asks you to recommend something (explicit_request=true): what is in "
                       "the basket, available dishes it doesn't have yet (grouped by section) and the most ordered "
                       "dishes. Not for the one suggestion after adding dishes: its ideas come with update_draft_order.",
        "parameters": {
            "type": "object",
            "properties": {"explicit_request": {"type": "boolean"}},
        },
    },
    {
        "name": "prepare_order_confirmation",
        "description": "When the guest has finished choosing: returns the live summary (lines, prices, total) to show "
                       "with the question whether to send it. Not for answers to clarifying questions.",
        "parameters": {"type": "object", "properties": {
            "customer_finished": {"type": "boolean"},
            "no_fitting_suggestion": {"type": "boolean",
                                      "description": "true when suggestion ideas came in this same turn but your "
                                                     "reply suggests nothing (the guest is done, or nothing fits)"}},
                       "required": ["customer_finished"]},
    },
    {
        "name": "submit_order",
        "description": "Send the basket to the restaurant. Only after a summary shown in an earlier message, when "
                       "the guest's current message approves sending it or approves the summary as it is (\"اه\", "
                       "\"ابعت\", \"تمام ابعته\", \"هيك تمام\", \"yes, send it\") and doesn't change, question, "
                       "postpone or turn down the order. Only saying they want nothing more is not approval. The "
                       "server re-checks prices and availability.",
        "parameters": {"type": "object", "properties": {
            "confirmed_summary": {"type": "boolean"},
            "guest_words": {"type": "string",
                            "description": "the guest's exact words in their current message that approve sending it"}},
                       "required": ["confirmed_summary", "guest_words"]},
    },
    {
        "name": "get_order_status",
        "description": "This guest's own sent orders this visit (other guests' orders are private). Guests see "
                       "four states: waiting for confirmation, preparing, ready, delivered. editable=true means the "
                       "guest can still change it (items carry item_id).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "change_sent_order",
        "description": "Change one of the guest's own sent orders that isn't ready yet, at once: "
                       + _OPS.format(line="item_id") + ". More of a dish already in it: update its item_id with "
                       "the new quantity. Applied right away and returns the changes and the new total. A change "
                       "that removes every item cancels the order: then nothing changes yet; ask the guest whether "
                       "to cancel it, and call again with the same change once they say yes.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "operations": _operations("item_id"),
                "guest_words": {"type": "string",
                                "description": "the guest's exact words in their current message that ask for this "
                                               "change (for a cancellation: their yes to it)"},
            },
            "required": ["order_id", "operations", "guest_words"],
        },
    },
    {
        "name": "request_service",
        "description": "Ask the staff for STAFF (someone to come; also for any table need no other tool can do), "
                       "TISSUES, CLEAN_TABLE or BILL for this table. Only for a real request (not negated or hypothetical).",
        "parameters": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["STAFF", "TISSUES", "CLEAN_TABLE", "BILL"]}}, "required": ["type"]},
    },
    {
        "name": "cancel_service",
        "description": "Cancel this guest's own service request of that type (STAFF, TISSUES, CLEAN_TABLE or BILL) "
                       "that is still on its way, when they ask to cancel it or say they no longer need it.",
        "parameters": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["STAFF", "TISSUES", "CLEAN_TABLE", "BILL"]}}, "required": ["type"]},
    },
    {
        "name": "submit_complaint",
        "description": "Record a complaint the guest actually makes (food, service, cleanliness, delay). A guest has "
                       "one complaint per visit: a new one is added to their earlier complaint (added_to_earlier).",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string", "description": "The complaint in the guest's words."},
            "category": {"type": "string"}}, "required": ["message"]},
    },
    {
        "name": "submit_feedback",
        "description": "Record the guest's rating of the visit (1-5 stars) and optional comment. Allowed after they "
                       "ordered. New stars replace the earlier ones; a new comment is added to the earlier comment.",
        "parameters": {"type": "object", "properties": {
            "rating": {"type": "integer", "minimum": 1, "maximum": 5},
            "comment": {"type": "string"}}, "required": ["rating"]},
    },
]

POPULAR_WINDOW = timedelta(days=30)
MAX_OPTIONS_PER_CATEGORY = 3
MAX_POPULAR = 4
# The ideas that come with the first dishes added: the most ordered dishes, then a few
# from each menu section the basket has nothing from yet.
MAX_IDEAS = 10
MAX_IDEAS_PER_SECTION = 2
# The end of a long complaint is what the turn context shows (its latest lines).
MAX_COMPLAINT_CONTEXT = 600


def _aware(moment: datetime) -> datetime:
    # SQLite hands back naive datetimes; they are stored in UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _amman_time(moment: datetime) -> str:
    return _aware(moment).astimezone(RESTAURANT_TZ).strftime("%H:%M")


def _minutes_since(moment: datetime, now: datetime) -> int:
    return max(0, int((now - _aware(moment)).total_seconds() // 60))


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
            if tool_name == "cancel_service":
                if not arguments.get("type"):
                    return {"success": False, "error_code": "SERVICE_TYPE_REQUIRED"}
                return await self._cancel_service(arguments["type"])
            if tool_name == "submit_complaint":
                res = await CustomerServiceManager.submit_complaint(
                    db=self.db, table_session_id=self.table_session_id, customer_session_id=self.customer_session_id,
                    message=arguments.get("message", ""), category=arguments.get("category"))
                return {"success": True, "complaint_id": res["complaint_id"], "status": res["status"],
                        "added_to_earlier": res["added_to_earlier"],
                        **({"nothing_new": True} if res.get("nothing_new") else {}),
                        **({"reopened": True} if res.get("reopened") else {})}
            if tool_name == "submit_feedback":
                res = await CustomerServiceManager.submit_feedback(
                    db=self.db, table_session_id=self.table_session_id, customer_session_id=self.customer_session_id,
                    rating=arguments.get("rating", 5), comment=arguments.get("comment"))
                return {"success": True, "feedback_id": res["feedback_id"], "updated": res.get("updated", False),
                        "rating": res["rating"], "comment": res.get("comment")}
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
        """Give each dish its category name (helps the model tell drinks, sandwiches and plates apart)
        and the units it is ordered in."""
        if not products:
            return
        names = await category_names(self.db)
        for product in products:
            section = names.get(product.get("category_id"))
            if section is not None:
                product["category_ar"], product["category_en"] = section
            saved = product.pop("units_json", None)
            if "units" not in product:
                product["units"] = serving_units(saved, product.get("name_ar") or "", product.get("name_en") or "",
                                                 *(section or ("", "")))

    async def _amendment(self, action, arguments: Dict[str, Any], **extra) -> Dict[str, Any]:
        try:
            summary = await action(self.db, self.customer_session_id, self.table_session_id,
                                   arguments.get("order_id", ""), list(arguments.get("operations") or []), **extra)
        except AmendmentOperationError as exc:
            return {"success": False, "error_code": exc.code, "failed_operation_index": exc.index, **exc.details}
        return {"success": True, "amendment": summary}

    async def _get_order_status(self) -> Dict[str, Any]:
        """This guest's own sent orders this visit; other guests' orders at the table stay private."""
        orders = (await self.db.execute(
            select(OrderModel).where(OrderModel.table_session_id == self.table_session_id,
                                     OrderModel.customer_session_id == self.customer_session_id)
            .options(selectinload(OrderModel.items))
            .order_by(OrderModel.created_at.desc())
        )).scalars().all()
        result = []
        for order in orders:
            locked = lock_reason(order)
            labels = customer_status_labels(order)
            result.append({
                "order_id": order.id,
                "order_number": order.order_number,
                "status_ar": labels[0],
                "status_en": labels[1],
                "editable": locked is None,
                "locked_reason": locked,
                "total": format_jod(sum(item.line_total_minor for item in order.items), "en").replace(" JOD", ""),
                "items": [
                    {**({"item_id": item.id, "product_id": item.product_id} if locked is None else {}),
                     "name_ar": item.product_name_snapshot_ar, "name_en": item.product_name_snapshot_en,
                     "quantity": item.quantity, **({"unit": item.unit} if item.unit else {}),
                     **({"note": item.note} if item.note else {})}
                    for item in order.items
                ],
                "created_at": order.created_at.isoformat(),
            })
        return {"success": True, "orders": result}

    async def own_orders_brief(self) -> List[Dict[str, Any]]:
        """The guest's own sent orders this visit, with their items, for the turn context (small)."""
        status = await self._get_order_status()
        return [{key: order[key] for key in ("order_id", "order_number", "status_en", "editable", "locked_reason",
                                             "total", "items")}
                for order in status["orders"]]

    async def visit_brief(self) -> Dict[str, Any]:
        """What else this visit holds for the turn context: the table's open service requests, and
        this guest's complaint and rating (each only when there is one)."""
        now = datetime.now(timezone.utc)
        requests = (await self.db.execute(
            select(ServiceRequestModel).where(
                ServiceRequestModel.table_session_id == self.table_session_id,
                ServiceRequestModel.status.in_((ServiceRequestStatus.OPEN, ServiceRequestStatus.IN_PROGRESS)))
            .order_by(ServiceRequestModel.created_at)
        )).scalars().all()
        complaint = (await self.db.execute(
            select(ComplaintModel).where(ComplaintModel.table_session_id == self.table_session_id,
                                         ComplaintModel.customer_session_id == self.customer_session_id)
            .order_by(ComplaintModel.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        rating = (await self.db.execute(
            select(FeedbackModel).where(FeedbackModel.table_session_id == self.table_session_id,
                                        FeedbackModel.customer_session_id == self.customer_session_id)
            .order_by(FeedbackModel.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        brief: Dict[str, Any] = {}
        if requests:
            brief["open_service_requests"] = [
                {"type": request.type.value, "status": request.status.value,
                 "since": _amman_time(request.created_at), "minutes_ago": _minutes_since(request.created_at, now),
                 "by_this_guest": request.customer_session_id == self.customer_session_id}
                for request in requests]
        if complaint is not None:
            brief["my_complaint"] = {"status": complaint.status.value, "since": _amman_time(complaint.created_at),
                                     "text": complaint.message[-MAX_COMPLAINT_CONTEXT:]}
        if rating is not None:
            brief["my_rating"] = {"stars": rating.rating, **({"comment": rating.comment} if rating.comment else {})}
        return brief

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
        sections = {category.id: (category.name_ar, category.name_en) for _, category in rows}
        basket = [{"name_ar": item["name_ar"], "name_en": item["name_en"], "quantity": item["quantity"],
                   "category_en": category_of[item["product_id"]].name_en if item["product_id"] in category_of else None}
                  for item in draft["items"]]
        basket_categories = {category_of[pid].id for pid in in_basket if pid in category_of}

        def option(product: ProductModel, category: MenuCategoryModel) -> Dict[str, Any]:
            return {"id": product.id, "name_ar": product.name_ar, "name_en": product.name_en,
                    "price": format_jod(product.price_minor, "en").replace(" JOD", ""),
                    "units": dish_units(product, sections),
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

    async def suggestion_ideas(self) -> List[Dict[str, Any]]:
        """Real dishes for the one suggestion of an order, given with the first dishes added: the most
        ordered ones, then a few from each section the basket has nothing from (what usually completes
        a meal, such as a drink)."""
        material = await self._recommend_products(explicit_request=False)
        basket_sections = {item["category_en"] for item in material["basket"]}
        ideas: Dict[str, Dict[str, Any]] = {dish["id"]: dish for dish in material["popular"]}
        per_section: Dict[str, int] = {}
        for dish in material["options"]:
            section = dish["category_en"]
            if len(ideas) >= MAX_IDEAS:
                break
            if section in basket_sections or per_section.get(section, 0) >= MAX_IDEAS_PER_SECTION:
                continue
            if dish["id"] not in ideas:
                ideas[dish["id"]] = dish
                per_section[section] = per_section.get(section, 0) + 1
        return [{"id": dish["id"], "name_ar": dish["name_ar"], "name_en": dish["name_en"], "price": dish["price"],
                 "units": dish["units"], "section": dish["category_en"]} for dish in ideas.values()]

    async def _update_draft_order(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        # All operations succeed together or none is saved, so the model never
        # retries a half-applied change (which used to duplicate items).
        try:
            summary = await OrderingService.apply_draft_operations(
                self.db, self.customer_session_id, self.table_session_id, operations)
        except DraftOperationError as exc:
            return {"success": False, "error_code": exc.code, "failed_operation_index": exc.index,
                    "basket_changed": False, **exc.details}
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

    async def _cancel_service(self, req_type: str) -> Dict[str, Any]:
        """Cancel this guest's request of that type that is still on its way (another guest's stays)."""
        try:
            service = ServiceRequestType(str(req_type).upper())
        except ValueError:
            return {"success": False, "error_code": "UNKNOWN_SERVICE_TYPE"}
        open_requests = (await self.db.execute(
            select(ServiceRequestModel).where(
                ServiceRequestModel.table_session_id == self.table_session_id,
                ServiceRequestModel.type == service,
                ServiceRequestModel.status.in_((ServiceRequestStatus.OPEN, ServiceRequestStatus.IN_PROGRESS)))
        )).scalars().all()
        own = [request.id for request in open_requests if request.customer_session_id == self.customer_session_id]
        if not own:
            return {"success": False, "type": service.value,
                    "error_code": "ANOTHER_GUESTS_REQUEST" if open_requests else "NO_OPEN_REQUEST"}
        for request_id in own:
            await CustomerServiceManager.cancel_service_request(
                self.db, request_id, self.table_session_id, self.customer_session_id)
        return {"success": True, "type": service.value}
