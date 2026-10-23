"""Server-side guards around every tool the assistant model asks for.

Shared by text chat, turn-by-turn voice and live (streaming) voice, so all three
follow exactly the same rules:
- a dish must be one the assistant knows (its menu, or shown by search) before it is
  added; the model states that the guest gave or agreed to the quantity (read from the
  whole conversation, never assumed), and a dish served several ways needs its unit;
- an order summary must be confirmed in a later turn than the one that showed it, and
  any basket change cancels the summary (a guest who says they're done gets the summary
  with their last dishes, in the same step);
- at most one suggestion per order: its ideas come with the first dishes added (for the
  "anything else?" question), never in the same reply as a summary;
- sending an order needs the guest's own words saying yes, quoted from their message
  (typed or dictated turns; the confirm button needs none);
- a change to a sent order applies at once, only with the guest's own words asking for
  it quoted from this message; cancelling a whole order waits for their yes in a later
  message (or the button);
- no repeated actions within a turn.
What each turn really did (sent, changed, requested or cancelled, recorded) is kept with the reply,
for the guest's screen and for the model's next turn.
The model decides what the guest means; these guards decide what is allowed.
"""
import logging
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
from jubran.domain.units import resolve_unit
from jubran.infrastructure.db.models import DraftConfirmationModel, DraftOrderModel

logger = logging.getLogger(__name__)

TOOL_NAMES = {tool["name"] for tool in TOOL_DEFINITIONS}
# Used by the server itself, never offered to the model: the confirm button on a cancellation card.
BUTTON_TOOLS = {"confirm_order_amendment"}
# Errors after which a summary can never be submitted again; the guest reviews anew.
_FINAL_SUBMIT_ERRORS = {"INVALID_CONFIRMATION_TOKEN", "DRAFT_VERSION_CONFLICT", "EMPTY_DRAFT",
                        "DRAFT_NOT_SUBMITTABLE", "TABLE_SESSION_CLOSED"}
# Tools whose success changes something for real (used to report a turn that failed midway).
EFFECT_TOOLS = {"update_draft_order", "submit_order", "change_sent_order", "confirm_order_amendment",
                "request_service", "cancel_service", "submit_complaint", "submit_feedback"}
QUANTITY_HINT = ("Not added: guest_gave_quantity wasn't true. If the guest did say this quantity (in any message) "
                 "or agreed to one you proposed, add it again with guest_gave_quantity=true. Otherwise ask how many "
                 "in one short, natural question that names the dish in its unit.")
LOOKUP_HINT = "Not added: that dish isn't in your MENU. Find it with search_knowledge first."
UNIT_HINT = ("Not added: this dish comes in several ways (units). Ask the guest which one, together with the "
             "quantity if that's missing too, and add it with that unit.")
SUGGESTION_STEP = ("Unless the guest said they want nothing more (then show the summary right away: "
                   "prepare_order_confirmation with customer_finished=true and no_fitting_suggestion=true), make your "
                   "'anything else?' question one suggestion from suggestion_ideas that really completes their meal, "
                   "with its price (never something they just turned down); if none fits, just ask if they'd like "
                   "anything else.")
SUMMARY_STEP = ("The guest is done choosing: show this summary (every line with its quantity in its unit and its "
                "note, its price, and the total) and ask whether to send it to the kitchen. No suggestion.")
# Changes to a line refused as a whole (nothing changed), and what the model should do instead.
LINE_HINTS = {
    "UNSUPPORTED_FIELD": ("Nothing changed: that change can't carry these fields (allowed lists what it takes). To "
                          "set, change or remove a note or the unit use update (note \"\" removes it); for part of a "
                          "line use split."),
    "UNIT_NOT_OFFERED": ("Nothing changed: the dish isn't served in that unit (units lists how it is). A takeaway "
                         "wish for a dish without a box is the note «سفري»."),
    "LINE_PRODUCT_MISMATCH": "Nothing changed: that line is a different dish. Check the lines in the turn context.",
    "ITEM_PRODUCT_MISMATCH": "Nothing changed: that item is a different dish. Check the order's items in my_sent_orders.",
    "SPLIT_WITHOUT_CHANGE": "Nothing changed: split needs a new note or unit for that part.",
    "NOTHING_TO_CHANGE": "Nothing changed: update needs a quantity, a note or a unit.",
}
CHANGE_WORDS_HINT = ("Nothing changed. Put in guest_words the guest's own words from this message that ask for this "
                     "change. If this message doesn't clearly ask to change the order, ask the guest.")
CANCEL_HINT = ("Nothing changed: this removes every item, so it cancels the whole order, which can't be undone. Ask the "
               "guest in one short question whether to cancel the whole order (the screen also shows a button). If "
               "they say yes, call change_sent_order again with the same change and their words.")
CHANGED_STEP = ("Done: tell the guest in one or two short sentences what changed and the new total (say so if the "
                "kitchen already started preparing it). Don't ask them to confirm it.")
SERVICE_CANCEL_HINTS = {
    "NO_OPEN_REQUEST": ("Nothing was cancelled: this guest has no such request on its way (it may be done already). "
                        "Tell the guest."),
    "ANOTHER_GUESTS_REQUEST": ("Nothing was cancelled: another guest at the table asked for it, so only they can "
                               "cancel it. Tell the guest kindly."),
}


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
                  own_orders: Optional[List[Dict[str, Any]]] = None, menu_complete: bool = False,
                  visit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The authoritative facts the model starts a turn from (the guest never sees this)."""
    context: Dict[str, Any] = {
        "basket_not_sent": draft_view(draft),
        "order_summary_awaiting_confirmation": state.pending is not None,
    }
    if state.pending_amendment is not None:
        context["order_change_awaiting_confirmation"] = amendment_view(state.pending_amendment.summary)
    if own_orders:
        context["my_sent_orders"] = own_orders
    # The table's open service requests, this guest's complaint and rating (only those that exist).
    context.update(visit or {})
    last = state.last_done()
    if last:
        context["your_last_reply_did"] = last
    if state.known_products and not menu_complete:  # with the whole menu in mind, nothing to add
        context["recently_shown_dishes"] = [
            {"id": pid, "name_ar": info.get("name_ar"), "name_en": info.get("name_en"),
             "price": jod(info.get("price_minor")), **({"units": info["units"]} if info.get("units") else {})}
            for pid, info in state.known_products.items()
        ]
    if state.upsell_suggested:
        context["suggestion_already_made_for_this_order"] = True
    if stale_reason == "ORDER_ALREADY_SUBMITTED":
        context["note"] = "The summary shown earlier was already sent by the guest from the orders page."
    return context


def basket_changes(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What a basket change did, line by line (added, removed, new quantity, note or unit)."""
    old = {item["line_id"]: item for item in before.get("items") or []}
    changes: List[Dict[str, Any]] = []

    def line(item: Dict[str, Any]) -> Dict[str, Any]:
        return {"line_id": item.get("line_id"), "name_ar": item.get("name_ar"), "name_en": item.get("name_en"),
                **({"unit": item["unit"]} if item.get("unit") else {}),
                **({"note": item["note"]} if item.get("note") else {})}

    for item in after.get("items") or []:
        previous = old.pop(item["line_id"], None)
        if previous is None:
            changes.append({"type": "added", **line(item), "quantity": item["quantity"]})
            continue
        if previous["quantity"] != item["quantity"]:
            changes.append({"type": "quantity_changed", **line(item), "quantity_before": previous["quantity"],
                            "quantity_after": item["quantity"]})
        if (previous.get("note") or None) != (item.get("note") or None):
            changes.append({"type": "note_changed", **line(item), "note_before": previous.get("note"),
                            "note_after": item.get("note")})
        if previous.get("unit") != item.get("unit"):
            changes.append({"type": "unit_changed", **line(item), "unit_before": previous.get("unit")})
    changes += [{"type": "removed", **line(item), "quantity": item["quantity"]} for item in old.values()]
    return changes


def order_change_entry(summary: Dict[str, Any]) -> Dict[str, Any]:
    """A change made to a sent order, as remembered with the reply (enough to undo it)."""
    keys = ("type", "item_id", "product_id", "name_ar", "name_en", "unit", "unit_before", "quantity_before",
            "quantity_after", "note", "note_before", "note_after")
    return {"kind": "order_changed", "order_id": summary.get("order_id"), "order_number": summary.get("order_number"),
            "cancelled": bool(summary.get("cancels_order")), "total_after": jod(summary.get("total_after_minor")),
            "changes": [{key: change[key] for key in keys if change.get(key) is not None}
                        for change in summary.get("changes") or []]}


def done_entry(name: str, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What a successful tool call really did, kept with the reply (None: nothing to keep)."""
    if not result.get("success"):
        return None
    if name == "submit_order":
        return {"kind": "order_sent", "order_number": result.get("order_number")}
    if name in {"change_sent_order", "confirm_order_amendment"} and isinstance(result.get("amendment"), dict):
        return order_change_entry(result["amendment"])
    if name == "request_service":
        return {"kind": "service", "type": result.get("type"), "already_open": bool(result.get("is_duplicate"))}
    if name == "cancel_service":
        return {"kind": "service_cancelled", "type": result.get("type")}
    if name == "submit_complaint":
        return {"kind": "complaint", "added_to_earlier": bool(result.get("added_to_earlier")),
                **({"nothing_new": True} if result.get("nothing_new") else {})}
    if name == "submit_feedback":
        return {"kind": "rating", "stars": result.get("rating"),
                **({"comment": result["comment"]} if result.get("comment") else {})}
    return None


def _products_in(name: str, result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Dishes a tool result showed the model (to remember them for the next turns)."""
    if name == "search_knowledge":
        return ([match["product"] for match in result.get("matches") or [] if match.get("product")]
                + list(result.get("alternatives") or []))
    if name == "get_product_details" and isinstance(result.get("product"), dict):
        return [result["product"]]
    if name == "recommend_products":
        return list(result.get("options") or []) + list(result.get("popular") or [])
    if name == "update_draft_order":
        return list(result.get("suggestion_ideas") or [])
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
    # The dishes of the menu the model holds in its instructions, with their units.
    menu_units: Dict[str, List[str]] = field(default_factory=dict)
    seen_products: set = field(default_factory=set)
    completed_actions: set = field(default_factory=set)
    changed_draft: bool = False
    added_dishes: bool = False
    prepared_this_turn: bool = False
    prepared_amendment_this_turn: bool = False
    looked_for_suggestion: bool = False
    action: Optional[Dict[str, Any]] = None
    latest_draft: Optional[Dict[str, Any]] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    # What this turn really did (see done_entry), kept with the reply.
    done: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        self.state.start_turn()
        self.pending_before = self.state.pending
        self.amendment_before = self.state.pending_amendment
        # Dishes on the menu the model holds, or shown in the last few turns, need no search.
        self.seen_products.update(self.state.known_products)
        self.seen_products.update(self.menu_units)

    def undo_unshown_summary(self) -> None:
        """A summary prepared in a turn that failed was never shown: go back to the earlier one."""
        if self.prepared_this_turn:
            self.state.pending = self.pending_before
        if self.prepared_amendment_this_turn:
            self.state.pending_amendment = self.amendment_before

    @staticmethod
    def gave_quantity(operation: Dict[str, Any]) -> bool:
        """The model states that the guest gave this quantity or agreed to it, anywhere in the
        conversation. The model judges that from the context; a quantity is never assumed."""
        return operation.get("guest_gave_quantity") is True

    def dish_units(self, product_id: str) -> List[str]:
        return (self.menu_units.get(product_id)
                or (self.state.known_products.get(product_id) or {}).get("units") or [])

    def unit_unclear(self, operation: Dict[str, Any]) -> Optional[List[str]]:
        """The units of a dish served several ways when the add doesn't say which one."""
        units = self.dish_units(operation.get("product_id") or "")
        if len(units) > 1 and resolve_unit(operation.get("unit"), units) is None:
            return units
        return None

    def unit_not_served(self, operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """An add naming a unit a dish served one way only doesn't come in (it is added in its own
        unit, and the model is told so: a takeaway wish then needs the note «سفري»)."""
        units = self.dish_units(operation.get("product_id") or "")
        if len(units) == 1 and operation.get("unit") and resolve_unit(str(operation["unit"]), units) is None:
            return {"product_id": operation.get("product_id"), "asked": operation["unit"], "added_as": units[0]}
        return None

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
        entry = done_entry(name, full)
        if entry:
            self.done.append(entry)
        products = [_with_price_minor(product) for product in _products_in(name, full)]
        if products:
            self.seen_products.update(product["id"] for product in products if product.get("id"))
            self.state.remember_products(products)
        view = model_view(name, full)
        self.tool_calls.append({"tool": name, "args": args, "result": view, "full": full})
        return view

    async def _guarded(self, executor: AssistantToolExecutor, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        state = self.state
        if name not in TOOL_NAMES and not (name in BUTTON_TOOLS and self.guest_message is None):
            return {"success": False, "error_code": "UNKNOWN_TOOL"}

        if name == "update_draft_order":
            if "submit_order" in self.completed_actions:
                return {"success": False, "error_code": "ORDER_ALREADY_SUBMITTED_THIS_TURN"}
            # The model is still asking the guest about this order: the suggestion waits.
            still_asking = args.get("still_asking") is True
            # The guest said they want nothing more: the summary comes with these dishes.
            finished = args.get("guest_finished") is True and not still_asking
            current = await executor.execute("get_current_draft", {})
            current_lines = {item["line_id"] for item in current.get("items", [])}
            in_basket = {item["product_id"] for item in current.get("items", [])}
            accepted_ops, blocked_ops, served_as = [], [], []
            for op in args.get("operations") or []:
                op = dict(op) if isinstance(op, dict) else {}
                candidate = {key: value for key, value in op.items() if key != "guest_gave_quantity"}
                if candidate.get("op") == "add":
                    if candidate.get("product_id") not in self.seen_products | in_basket:
                        blocked_ops.append({"operation": op, "error_code": "LOOKUP_REQUIRED"})
                        continue
                    if not self.gave_quantity(op):
                        blocked_ops.append({"operation": op, "error_code": "QUANTITY_NOT_GIVEN"})
                        continue
                    units = self.unit_unclear(op)
                    if units:
                        blocked_ops.append({"operation": op, "error_code": "UNIT_REQUIRED", "units": units})
                        continue
                    other_unit = self.unit_not_served(op)
                    if other_unit:
                        served_as.append(other_unit)
                elif candidate.get("op") in {"remove", "set_quantity", "update", "split"}:
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
            hints = {"QUANTITY_NOT_GIVEN": QUANTITY_HINT, "LOOKUP_REQUIRED": LOOKUP_HINT, "UNIT_REQUIRED": UNIT_HINT,
                     **LINE_HINTS}
            refused = {blocked["error_code"] for blocked in blocked_ops} | {result.get("error_code")}
            if refused & set(hints):
                result["hint"] = " ".join(hint for code, hint in hints.items() if code in refused)
            if result.get("success"):
                self.changed_draft = True
                added = any(op.get("op") == "add" for op in accepted_ops)
                self.added_dishes = self.added_dishes or added
                had_summary = state.pending is not None
                state.pending = None
                latest = result.get("draft", {})
                self.latest_draft = latest if latest.get("item_count") else None
                self.done.append({"kind": "basket", "changes": basket_changes(current, latest)})
                if served_as:
                    result["served_in_own_unit"] = served_as
                    result["unit_note"] = ("These dishes come one way only and were added in their own unit "
                                           "(added_as). If the guest wanted one to go, give its line the note «سفري».")
                if (had_summary or finished and not blocked_ops) and self.latest_draft:
                    # Changed after seeing the summary, or the guest is done: the (updated) summary comes now,
                    # so the guest's next "yes" sends exactly what they see (never in this same turn).
                    refreshed = await executor.execute("prepare_order_confirmation", {})
                    if refreshed.get("success"):
                        self._remember_pending(refreshed)
                        result["summary"] = refreshed["summary"]
                        result["next_step"] = SUMMARY_STEP if finished else (
                            "The order changed after the summary: show this updated summary (every line with its "
                            "price, and the total) and ask whether to send it.")
                elif added and self.latest_draft and not state.upsell_suggested and not still_asking and not finished:
                    # The one suggestion of this order: its ideas come with the dishes just added.
                    ideas = await self._suggestion_ideas(executor)
                    if ideas:
                        state.upsell_suggested = True
                        self.looked_for_suggestion = True
                        result["suggestion_ideas"] = ideas
                        result["next_step"] = SUGGESTION_STEP
            return result

        if name == "prepare_order_confirmation":
            if "submit_order" in self.completed_actions:
                return {"success": False, "error_code": "ORDER_ALREADY_SUBMITTED_THIS_TURN"}
            if state.pending and self.prepared_this_turn:
                # Already prepared in this turn (e.g. with the guest's last dishes): just show it.
                return {"success": True, "summary": state.pending.summary, "next_step": SUMMARY_STEP}
            if state.pending:
                return {"success": False, "error_code": "CONFIRMATION_ALREADY_PREPARED",
                        "hint": "A summary is already pending. Submit it only if the guest confirmed it; otherwise ask for confirmation."}
            if args.get("customer_finished") is not True:
                return {"success": False, "error_code": "CUSTOMER_STILL_ORDERING",
                        "hint": "Decide from the conversation whether the guest has finished choosing."}
            if self.looked_for_suggestion and args.get("no_fitting_suggestion") is not True:
                return {"success": False, "error_code": "SUGGESTION_FIRST",
                        "hint": "Ideas for a suggestion came in this turn. If one item fits, suggest it and wait for "
                                "the guest's answer (no summary now). If the guest is done or nothing fits, call "
                                "again with no_fitting_suggestion=true."}
            raw = await executor.execute(name, {})
            if raw.get("success"):
                self._remember_pending(raw)
            return raw

        if name == "submit_order":
            return await self._submit(executor, args)

        if name == "change_sent_order":
            return await self._change_sent_order(executor, args)

        if name == "confirm_order_amendment":  # the confirm button on a cancellation card
            return await self._confirm_amendment(executor, args)

        if name in {"request_service", "cancel_service", "submit_complaint", "submit_feedback"}:
            signature = f"{name}:{jsonable_encoder(args)}"
            if signature in self.completed_actions:
                return {"success": False, "error_code": "DUPLICATE_ACTION_THIS_TURN"}
            result = await executor.execute(name, args)
            self.completed_actions.add(signature)
            if name == "request_service" and result.get("success") and result.get("type") == "BILL":
                result["hint"] = "Bill requested. Invite an optional 1-5 rating of the visit in one short sentence."
            if name == "cancel_service" and result.get("error_code") in SERVICE_CANCEL_HINTS:
                result["hint"] = SERVICE_CANCEL_HINTS[result["error_code"]]
            return result

        if name == "recommend_products" and (
            state.pending or state.upsell_suggested and not args.get("explicit_request")
        ):
            return {"success": False, "error_code": "SUGGESTION_ALREADY_MADE",
                    "hint": "One suggestion per order. Continue: ask if they want anything else, or show the summary."}
        if name == "recommend_products" and not args.get("explicit_request") and not self.added_dishes:
            # A suggestion only comes with dishes just added, as the "anything else?" question:
            # never after the guest said they want nothing more.
            return {"success": False, "error_code": "SUGGESTION_NOT_NOW",
                    "hint": "Ideas for the suggestion come with the dishes you add. If the guest said they want "
                            "nothing more, show the summary now (prepare_order_confirmation)."}

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

    @staticmethod
    async def _suggestion_ideas(executor: AssistantToolExecutor) -> List[Dict[str, Any]]:
        """Dishes for the one suggestion of an order (none if they can't be read: the order goes on)."""
        try:
            return await executor.suggestion_ideas()
        except Exception:
            logger.exception("Could not read suggestion ideas")
            return []

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
                    "hint": "Put in guest_words the guest's own words from this message that approve sending or "
                            "the summary as it is. If they only said they want nothing more, don't send: ask in one "
                            "short line whether to send it now. If the message changes, questions, postpones or "
                            "turns down the order, don't send: handle that instead."}

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

    async def _change_sent_order(self, executor: AssistantToolExecutor, args: Dict[str, Any]) -> Dict[str, Any]:
        """Change one of the guest's sent orders at once (the guest's own words in this message ask
        for it). Cancelling the whole order waits for the guest's yes in a later message."""
        state = self.state
        order_id = str(args.get("order_id") or "")
        raw_operations = [dict(op) for op in args.get("operations") or [] if isinstance(op, dict)]
        operations = [{key: value for key, value in op.items() if key != "guest_gave_quantity"}
                      for op in raw_operations]
        signature = f"change_sent_order:{jsonable_encoder({'order_id': order_id, 'operations': operations})}"
        if signature in self.completed_actions:
            return {"success": False, "error_code": "DUPLICATE_ACTION_THIS_TURN"}
        # Dishes in the guest's own orders, or in a change the last reply made (to undo it), count as known.
        known = self.seen_products | {item.get("product_id") for order in await executor.own_orders_brief()
                                      for item in order.get("items") or []}
        known |= {change.get("product_id") for entry in state.last_done() if entry.get("kind") == "order_changed"
                  for change in entry.get("changes") or []}
        unknown = [index for index, op in enumerate(operations)
                   if op.get("op") == "add" and op.get("product_id") not in known]
        if unknown:
            return {"success": False, "error_code": "LOOKUP_REQUIRED", "failed_operation_index": unknown[0],
                    "hint": LOOKUP_HINT}
        unsaid = [index for index, op in enumerate(raw_operations)
                  if op.get("op") == "add" and not self.gave_quantity(op)]
        if unsaid:
            return {"success": False, "error_code": "QUANTITY_NOT_GIVEN", "failed_operation_index": unsaid[0],
                    "hint": QUANTITY_HINT}
        unclear = [index for index, op in enumerate(operations) if op.get("op") == "add" and self.unit_unclear(op)]
        if unclear:
            return {"success": False, "error_code": "UNIT_REQUIRED", "failed_operation_index": unclear[0],
                    "units": self.unit_unclear(operations[unclear[0]]), "hint": UNIT_HINT}
        if not self.said_yes(args):
            return {"success": False, "error_code": "CHANGE_WORDS_REQUIRED", "hint": CHANGE_WORDS_HINT}

        for attempt in range(2):
            preview = await executor.execute("preview_order_amendment", {"order_id": order_id,
                                                                         "operations": operations})
            if not preview.get("success"):
                if preview.get("error_code") in LINE_HINTS:
                    preview["hint"] = LINE_HINTS[preview["error_code"]]
                return preview
            summary = preview["amendment"]
            version = summary["order_version"]
            if summary["cancels_order"]:
                pending = state.pending_amendment
                asked = (pending is not None and pending.order_id == order_id
                         and pending.summary.get("cancels_order") and not self.prepared_amendment_this_turn)
                if not asked:
                    # A cancelled order can't come back: the guest says yes to it first.
                    self._remember_amendment(operations, summary)
                    return {"success": False, "error_code": "CANCEL_NEEDS_YES", "amendment": summary,
                            "hint": CANCEL_HINT}
                version = pending.order_version  # the order as it was when the guest was asked
            result = await executor.execute("apply_order_amendment", {"order_id": order_id, "operations": operations,
                                                                      "expected_version": version})
            if result.get("success"):
                state.pending_amendment = None
                self.completed_actions.add(signature)
                self.action = {"type": "ORDER_AMENDED", "amendment": result["amendment"]}
                result["next_step"] = CHANGED_STEP
                return result
            if result.get("error_code") != "ORDER_CHANGED":
                return result
            if summary["cancels_order"]:
                # The order moved on since the guest was asked: ask again about it as it is now.
                state.pending_amendment = None
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
