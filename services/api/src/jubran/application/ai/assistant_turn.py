"""Server-side guards around every tool the assistant model asks for.

Shared by text chat, turn-by-turn voice and live (streaming) voice, so all three
follow exactly the same rules:
- a dish must have been shown (this turn or recently) before it is added;
- an order summary, or a change to a sent order, must be confirmed in a later
  turn than the one that showed it, and any basket change cancels the summary;
- one suggestion considered before an order's first summary (never in the same reply);
- sending an order or a change needs the guest's own words saying yes, quoted from
  their message (typed or dictated turns; the confirm button needs none);
- no repeated actions within a turn.
The model decides what the guest means; these guards decide what is allowed.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.ai.conversation_store import ConversationState, PendingAmendment, PendingConfirmation
from jubran.application.ai.text_match import normalize
from jubran.application.ai.tool_views import amendment_view, draft_view, jod, model_view
from jubran.application.ai.tools import AssistantToolExecutor, TOOL_DEFINITIONS
from jubran.infrastructure.auth.tokens import hash_token
from jubran.domain.enums import DraftStatus
from jubran.infrastructure.db.models import DraftConfirmationModel, DraftOrderModel

TOOL_NAMES = {tool["name"] for tool in TOOL_DEFINITIONS}
# Errors after which a summary can never be submitted again; the guest reviews anew.
_FINAL_SUBMIT_ERRORS = {"INVALID_CONFIRMATION_TOKEN", "DRAFT_VERSION_CONFLICT", "EMPTY_DRAFT",
                        "DRAFT_NOT_SUBMITTABLE", "TABLE_SESSION_CLOSED"}
# Tools whose success changes something for real (used to report a turn that failed midway).
EFFECT_TOOLS = {"update_draft_order", "submit_order", "confirm_order_amendment", "request_service",
                "submit_complaint", "submit_feedback"}


async def stale_pending_reason(db: AsyncSession, pending: PendingConfirmation) -> Optional[str]:
    """Why a summary shown earlier can no longer be confirmed, if it can't.

    The basket may have been sent from the orders page, or emptied/abandoned,
    since the assistant showed its summary.
    """
    confirmation = (await db.execute(select(DraftConfirmationModel).where(
        DraftConfirmationModel.confirmation_token_hash == hash_token(pending.token)))).scalar_one_or_none()
    if confirmation is None:
        return "CONFIRMATION_NOT_FOUND"
    draft_status = (await db.execute(select(DraftOrderModel.status).where(
        DraftOrderModel.id == confirmation.draft_order_id))).scalar_one_or_none()
    if draft_status == DraftStatus.SUBMITTED:
        return "ORDER_ALREADY_SUBMITTED"
    if draft_status != DraftStatus.OPEN or confirmation.consumed_at is not None:
        return "DRAFT_NOT_SUBMITTABLE"
    return None


async def forget_stale_pending(db: AsyncSession, state: ConversationState) -> Optional[str]:
    """Drop an outdated summary from the conversation state; returns why, if it was dropped."""
    reason = await stale_pending_reason(db, state.pending) if state.pending else None
    if reason:
        state.pending = None
    if reason == "ORDER_ALREADY_SUBMITTED":
        state.upsell_suggested = False  # a new order gets its own suggestion
    return reason


def confirmation_changes(previous: Dict[str, Any], current: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Describe any live draft change that makes an earlier confirmation stale."""
    changes: List[Dict[str, Any]] = []
    previous_items = {item["line_id"]: item for item in previous.get("items", [])}
    current_items = {item["line_id"]: item for item in current.get("items", [])}
    if previous.get("version") != current.get("version") or set(previous_items) != set(current_items):
        changes.append({"type": "DRAFT_CONTENT_CHANGED"})
    for line_id, item in current_items.items():
        old = previous_items.get(line_id)
        if old and old.get("unit_price_minor") != item.get("unit_price_minor"):
            changes.append({
                "type": "PRICE_CHANGED", "line_id": line_id,
                "product_id": item.get("product_id"), "name_ar": item.get("name_ar"),
                "old_price_minor": old.get("unit_price_minor"),
                "new_price_minor": item.get("unit_price_minor"),
            })
        if not item.get("is_available", True):
            changes.append({
                "type": "PRODUCT_UNAVAILABLE", "line_id": line_id,
                "product_id": item.get("product_id"), "name_ar": item.get("name_ar"),
            })
    return changes


def draft_context(draft: Dict[str, Any], state: ConversationState, stale_reason: Optional[str],
                  own_orders: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The authoritative facts the model starts a turn from (the guest never sees this)."""
    context: Dict[str, Any] = {
        "basket_not_sent": draft_view(draft),
        "order_summary_awaiting_confirmation": state.pending is not None,
    }
    if state.pending_amendment is not None:
        context["order_change_awaiting_confirmation"] = amendment_view(state.pending_amendment.summary)
    if own_orders:
        context["my_sent_orders"] = own_orders
    if state.known_products:
        context["recently_shown_dishes"] = [
            {"id": pid, "name_ar": info.get("name_ar"), "name_en": info.get("name_en"),
             "price": jod(info.get("price_minor"))}
            for pid, info in state.known_products.items()
        ]
    if state.upsell_suggested:
        context["suggestion_already_made_for_this_order"] = True
    if stale_reason == "ORDER_ALREADY_SUBMITTED":
        context["note"] = "The summary shown earlier was already sent by the guest from the orders page."
    return context


def _products_in(name: str, result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Dishes a tool result showed the model (to remember them for the next turns)."""
    if name == "search_knowledge":
        return ([match["product"] for match in result.get("matches") or [] if match.get("product")]
                + list(result.get("alternatives") or []))
    if name == "get_product_details" and isinstance(result.get("product"), dict):
        return [result["product"]]
    if name == "recommend_products":
        return list(result.get("options") or []) + list(result.get("popular") or [])
    return []


def _with_price_minor(product: Dict[str, Any]) -> Dict[str, Any]:
    if "price_minor" in product or "price" not in product:
        return product
    try:
        return {**product, "price_minor": round(float(product["price"]) * 1000)}
    except (TypeError, ValueError):
        return product


@dataclass
class AssistantTurn:
    """Guards for one model turn. Create a new one for every turn of the guest."""
    state: ConversationState
    stale_reason: Optional[str] = None
    # What the guest wrote this turn; None when a button (or live voice) confirmed.
    guest_message: Optional[str] = None
    seen_products: set = field(default_factory=set)
    completed_actions: set = field(default_factory=set)
    changed_draft: bool = False
    prepared_this_turn: bool = False
    prepared_amendment_this_turn: bool = False
    looked_for_suggestion: bool = False
    action: Optional[Dict[str, Any]] = None
    latest_draft: Optional[Dict[str, Any]] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        self.state.start_turn()
        self.pending_before = self.state.pending
        self.amendment_before = self.state.pending_amendment
        # Dishes shown in the last few turns can be added without searching again.
        self.seen_products.update(self.state.known_products)

    def undo_unshown_summary(self) -> None:
        """A summary prepared in a turn that failed was never shown: go back to the earlier one."""
        if self.prepared_this_turn:
            self.state.pending = self.pending_before
        if self.prepared_amendment_this_turn:
            self.state.pending_amendment = self.amendment_before

    def said_yes(self, args: Dict[str, Any]) -> bool:
        """The model quoted words that really are in the guest's message (the button needs none)."""
        if self.guest_message is None:
            return True
        words = normalize(str(args.get("guest_words") or ""))
        return bool(words) and f" {words} " in f" {normalize(self.guest_message)} "

    def succeeded_effects(self) -> List[Dict[str, Any]]:
        return [call for call in self.tool_calls if call["tool"] in EFFECT_TOOLS and call["full"].get("success")]

    def _remember_pending(self, prepared: Dict[str, Any]) -> None:
        self.state.pending = PendingConfirmation(token=prepared["confirmation_token"],
                                                 version=prepared["draft_version"],
                                                 summary=prepared["summary"])
        self.prepared_this_turn = True
        self.action = {"type": "AWAITING_ORDER_CONFIRMATION", "summary": prepared["summary"],
                       "draft_version": prepared["draft_version"]}
        self.latest_draft = prepared["summary"]

    def _remember_amendment(self, operations: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
        self.state.pending_amendment = PendingAmendment(order_id=summary["order_id"],
                                                        order_version=summary["order_version"],
                                                        operations=operations, summary=summary)
        self.prepared_amendment_this_turn = True
        self.action = {"type": "AWAITING_AMENDMENT_CONFIRMATION", "amendment": summary}

    async def run_tool(self, executor: AssistantToolExecutor, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run one tool call under the guards; returns the compact JSON-safe result given to the model."""
        args = dict(args or {})
        full = jsonable_encoder(await self._guarded(executor, name, args))
        products = [_with_price_minor(product) for product in _products_in(name, full)]
        if products:
            self.seen_products.update(product["id"] for product in products if product.get("id"))
            self.state.remember_products(products)
        view = model_view(name, full)
        self.tool_calls.append({"tool": name, "args": args, "result": view, "full": full})
        return view

    async def _guarded(self, executor: AssistantToolExecutor, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        state = self.state
        if name not in TOOL_NAMES:
            return {"success": False, "error_code": "UNKNOWN_TOOL"}

        if name == "update_draft_order":
            if "submit_order" in self.completed_actions:
                return {"success": False, "error_code": "ORDER_ALREADY_SUBMITTED_THIS_TURN"}
            current = await executor.execute("get_current_draft", {})
            current_lines = {item["line_id"] for item in current.get("items", [])}
            in_basket = {item["product_id"] for item in current.get("items", [])}
            accepted_ops, blocked_ops = [], []
            for op in args.get("operations") or []:
                candidate = dict(op)
                if candidate.get("op") == "add":
                    if candidate.get("product_id") not in self.seen_products | in_basket:
                        blocked_ops.append({"operation": op, "error_code": "LOOKUP_REQUIRED"})
                        continue
                elif candidate.get("op") in {"remove", "set_quantity"}:
                    if candidate.get("line_id") not in current_lines:
                        blocked_ops.append({"operation": op, "error_code": "DRAFT_LINE_NOT_FOUND"})
                        continue
                accepted_ops.append(candidate)
            if accepted_ops:
                result = await executor.execute(name, {"operations": accepted_ops})
                result["executed_operations"] = accepted_ops
                if blocked_ops:
                    result["partial_success"] = True
                    result["blocked_operations"] = blocked_ops
            else:
                result = {"success": False,
                          "error_code": blocked_ops[0]["error_code"] if blocked_ops else "EMPTY_OPERATIONS",
                          "blocked_operations": blocked_ops}
            if result.get("success"):
                self.changed_draft = True
                had_summary = state.pending is not None
                state.pending = None
                latest = result.get("draft", {})
                self.latest_draft = latest if latest.get("item_count") else None
                if had_summary and self.latest_draft:
                    # Changed after seeing the summary: the updated summary replaces it at once, so the
                    # guest's next "yes" sends exactly what they now see (never in this same turn).
                    refreshed = await executor.execute("prepare_order_confirmation", {})
                    if refreshed.get("success"):
                        self._remember_pending(refreshed)
                        result["summary"] = refreshed["summary"]
                        result["next_step"] = ("The order changed after the summary: show this updated summary "
                                               "(every line with its price, and the total) and ask whether to send it.")
            return result

        if name == "prepare_order_confirmation":
            if "submit_order" in self.completed_actions:
                return {"success": False, "error_code": "ORDER_ALREADY_SUBMITTED_THIS_TURN"}
            if state.pending:
                return {"success": False, "error_code": "CONFIRMATION_ALREADY_PREPARED",
                        "hint": "A summary is already pending. Submit it only if the guest confirmed it; otherwise ask for confirmation."}
            if args.get("customer_finished") is not True:
                return {"success": False, "error_code": "CUSTOMER_STILL_ORDERING",
                        "hint": "Decide from the conversation whether the guest has finished choosing."}
            if not state.upsell_suggested:
                return {"success": False, "error_code": "SUGGESTION_NOT_CONSIDERED",
                        "hint": "Before the first summary of an order, call recommend_products: suggest one item "
                                "that fits and wait for the answer, or if nothing fits, show the summary with "
                                "no_fitting_suggestion=true."}
            if self.looked_for_suggestion and args.get("no_fitting_suggestion") is not True:
                return {"success": False, "error_code": "SUGGESTION_FIRST",
                        "hint": "You looked for a suggestion in this turn. If one item fits, suggest it and wait for "
                                "the guest's answer (no summary now). If nothing fits, call again with "
                                "no_fitting_suggestion=true."}
            raw = await executor.execute(name, {})
            if raw.get("success"):
                self._remember_pending(raw)
            return raw

        if name == "submit_order":
            return await self._submit(executor, args)

        if name == "prepare_order_amendment":
            return await self._prepare_amendment(executor, args)

        if name == "confirm_order_amendment":
            return await self._confirm_amendment(executor, args)

        if name in {"request_service", "submit_complaint", "submit_feedback"}:
            signature = f"{name}:{jsonable_encoder(args)}"
            if signature in self.completed_actions:
                return {"success": False, "error_code": "DUPLICATE_ACTION_THIS_TURN"}
            result = await executor.execute(name, args)
            self.completed_actions.add(signature)
            if name == "request_service" and result.get("success") and result.get("type") == "BILL":
                result["hint"] = "Bill requested. Invite an optional 1-5 rating of the visit in one short sentence."
            return result

        if name == "recommend_products" and (
            state.pending or state.upsell_suggested and not args.get("explicit_request")
        ):
            return {"success": False, "error_code": "SUGGESTION_ALREADY_MADE",
                    "hint": "One suggestion per order. Continue: ask if they want anything else, or show the summary."}

        result = await executor.execute(name, args)
        if name == "get_current_draft":
            self.latest_draft = result if result.get("item_count") else None
        elif name == "recommend_products":
            if not args.get("explicit_request"):
                state.upsell_suggested = True
                self.looked_for_suggestion = True
                result["next_step"] = ("Suggest one item that fits and wait for the answer, or if nothing fits "
                                       "show the summary (no_fitting_suggestion=true).")
        return result

    async def _submit(self, executor: AssistantToolExecutor, args: Dict[str, Any]) -> Dict[str, Any]:
        state = self.state
        pending = state.pending
        if not pending and self.stale_reason == "ORDER_ALREADY_SUBMITTED":
            return {"success": False, "error_code": "ORDER_ALREADY_SUBMITTED",
                    "hint": "The guest already sent this basket from the orders page. Tell them it was sent; use get_order_status for its state."}
        if "submit_order" in self.completed_actions:
            return {"success": False, "error_code": "ALREADY_SUBMITTED_THIS_TURN"}
        if not pending:
            current = await executor.execute("get_current_draft", {})
            if not current.get("items"):
                return {"success": False, "error_code": "NOTHING_TO_SEND",
                        "hint": "The basket is empty, so nothing is waiting to be sent. The guest's sent orders "
                                "are in my_sent_orders: if they mean one of those, tell them it was already sent."}
            return {"success": False, "error_code": "PRIOR_CONFIRMATION_REQUIRED",
                    "hint": "Show the summary first (prepare_order_confirmation) and send only after the guest "
                            "says yes in a later message."}
        if self.prepared_this_turn or self.changed_draft or args.get("confirmed_summary") is not True:
            return {"success": False, "error_code": "PRIOR_CONFIRMATION_REQUIRED",
                    "hint": "The summary must be shown first and confirmed in a later message."}
        if not self.said_yes(args):
            return {"success": False, "error_code": "CONFIRMATION_WORDS_REQUIRED",
                    "hint": "Put in guest_words the guest's own words from this message that show they want the "
                            "order sent (a yes, or declining anything more). If the message changes, questions, "
                            "postpones or turns down the order, don't send: handle that instead."}

        current = await executor.execute("get_current_draft", {})
        changes = confirmation_changes(pending.summary, current)
        if changes:
            if any(change["type"] == "PRODUCT_UNAVAILABLE" for change in changes):
                state.pending = None
                return {"success": False, "error_code": "PRODUCT_UNAVAILABLE_BEFORE_SUBMIT",
                        "changes": changes, "current_summary": current}
            refreshed = await executor.execute("prepare_order_confirmation", {})
            if not refreshed.get("success"):
                # e.g. the basket was emptied meanwhile: nothing left to confirm.
                state.pending = None
                return {"success": False, "error_code": refreshed.get("error_code") or "DRAFT_NOT_SUBMITTABLE",
                        "changes": changes, "current_summary": current}
            self._remember_pending(refreshed)
            return {"success": False, "error_code": "LIVE_DRAFT_CHANGED", "changes": changes,
                    "current_summary": refreshed["summary"], "confirmation_refreshed": True}

        result = await executor.execute("submit_confirmed_order", {
            "confirmation_token": pending.token, "draft_version": pending.version})
        if not result.get("success") and result.get("error_code") in _FINAL_SUBMIT_ERRORS:
            state.pending = None  # never retry a summary the server refused
        if result.get("success"):
            state.pending = None
            state.upsell_suggested = False  # the next order gets its own suggestion
            self.action = {"type": "ORDER_SUBMITTED", "order_id": result.get("order_id"),
                           "order_number": result.get("order_number")}
            self.latest_draft = None
            self.completed_actions.add("submit_order")
        return result

    async def _prepare_amendment(self, executor: AssistantToolExecutor, args: Dict[str, Any]) -> Dict[str, Any]:
        operations = [dict(op) for op in args.get("operations") or []]
        # Dishes already in the guest's own orders count as known (like the basket's dishes).
        ordered = {item.get("product_id") for order in await executor.own_orders_brief()
                   for item in order.get("items") or []}
        unknown = [index for index, op in enumerate(operations)
                   if op.get("op") == "add" and op.get("product_id") not in self.seen_products | ordered]
        if unknown:
            return {"success": False, "error_code": "LOOKUP_REQUIRED", "failed_operation_index": unknown[0],
                    "hint": "Find the dish with search_knowledge first."}
        result = await executor.execute("preview_order_amendment", {"order_id": args.get("order_id", ""),
                                                                    "operations": operations})
        if result.get("success"):
            self._remember_amendment(operations, result["amendment"])
            result["next_step"] = "Show the changes and the new total, then ask the guest to confirm."
        return result

    async def _confirm_amendment(self, executor: AssistantToolExecutor, args: Dict[str, Any]) -> Dict[str, Any]:
        state = self.state
        pending = state.pending_amendment
        if not pending or self.prepared_amendment_this_turn or args.get("confirmed") is not True:
            return {"success": False, "error_code": "PRIOR_CONFIRMATION_REQUIRED",
                    "hint": "Prepare the change, show it, and apply it only after the guest confirms in a later message."}
        if "confirm_order_amendment" in self.completed_actions:
            return {"success": False, "error_code": "ALREADY_APPLIED_THIS_TURN"}
        if not self.said_yes(args):
            return {"success": False, "error_code": "CONFIRMATION_WORDS_REQUIRED",
                    "hint": "Put in guest_words the guest's own words from this message that say yes to the change. "
                            "If nothing in it says yes, don't apply it: ask the guest clearly."}
        result = await executor.execute("apply_order_amendment", {
            "order_id": pending.order_id, "operations": pending.operations,
            "expected_version": pending.order_version})
        if result.get("success"):
            state.pending_amendment = None
            self.completed_actions.add("confirm_order_amendment")
            self.action = {"type": "ORDER_AMENDED", "amendment": result["amendment"]}
            return result
        if result.get("error_code") == "ORDER_CHANGED":
            # The order moved on (the guest or the kitchen) since the guest saw the change: show it again.
            fresh = await executor.execute("preview_order_amendment", {"order_id": pending.order_id,
                                                                       "operations": pending.operations})
            if fresh.get("success"):
                self._remember_amendment(pending.operations, fresh["amendment"])
                return {"success": False, "error_code": "ORDER_CHANGED_REVIEW_AGAIN", "amendment": fresh["amendment"]}
            state.pending_amendment = None
            return fresh
        state.pending_amendment = None
        return result
