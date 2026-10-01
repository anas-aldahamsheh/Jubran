"""What the model sees from each tool: only what it needs, in few tokens.

Server logic (guards, the web app, confirmations) keeps the full results; the
model gets these compact views. Prices are plain JOD amounts ("3.45"); the model
writes them as "3.45 د.أ" or "3.45 JOD" in the guest's language.
"""
from typing import Any, Dict, List, Optional

from jubran.domain.money import format_jod


def jod(fils: Optional[int]) -> Optional[str]:
    return None if fils is None else format_jod(int(fils), "en").replace(" JOD", "")


def draft_view(draft: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The basket: numbered lines (1 = added first) the guest may refer to ("the last one")."""
    draft = draft or {}
    items = []
    for position, item in enumerate(draft.get("items") or [], start=1):
        line = {"n": position, "line_id": item.get("line_id"), "product_id": item.get("product_id"),
                "name_ar": item.get("name_ar"), "name_en": item.get("name_en"), "quantity": item.get("quantity"),
                "unit_price": jod(item.get("unit_price_minor")), "line_total": jod(item.get("line_total_minor"))}
        if item.get("note"):
            line["note"] = item["note"]
        if item.get("is_available") is False:
            line["available"] = False
        items.append(line)
    return {"items": items, "total": jod(draft.get("total_minor", 0)), "item_count": draft.get("item_count", 0)}


def product_view(product: Dict[str, Any]) -> Dict[str, Any]:
    view = {"id": product.get("id"), "name_ar": product.get("name_ar"), "name_en": product.get("name_en"),
            "price": jod(product.get("price_minor")), "available": bool(product.get("is_available", True))}
    for key in ("category_ar", "category_en", "description_ar", "description_en"):
        if product.get(key):
            view[key] = product[key]
    return view


def search_view(result: Dict[str, Any]) -> Dict[str, Any]:
    if not result.get("success"):
        return {key: result[key] for key in ("success", "error_code") if key in result}
    matches: List[Dict[str, Any]] = []
    for match in result.get("matches") or []:
        if match.get("product"):
            view = product_view(match["product"])
            view["found_by"] = match.get("match_type")
            if view["found_by"] == "listed":  # the whole menu: descriptions come from get_product_details
                view.pop("description_ar", None)
                view.pop("description_en", None)
            matches.append(view)
        else:
            matches.append({"type": match.get("source_type"), "title": match.get("title"),
                            "text": match.get("content")})
    view = {"success": True, "matches": matches}
    if result.get("kinds"):
        view["same_kind"] = result["kinds"]
        view["same_kind_note"] = ("Each list is one kind of dish. If the guest asks about the kind or orders it by "
                                  "its general name, name every dish in it and ask which; if they named one specific "
                                  "dish, that's the one.")
    if result.get("no_reliable_match"):
        view["no_reliable_match"] = True
    if result.get("alternatives"):
        view["alternatives"] = [{key: dish.get(key) for key in ("id", "name_ar", "name_en", "price")}
                                for dish in result["alternatives"]]
        view["alternatives_note"] = "Nothing on the menu matches this. To offer something instead, use these real dishes."
    if result.get("retrieval") == "name_only":
        view["note"] = "Meaning search is unavailable right now; only dishes named in the query were matched."
    elif result.get("retrieval") == "listing":
        view["note"] = ("Meaning search is unavailable right now, so this is the menu in order, not ranked: "
                        "use only what really matches the guest.")
    return view


def _branch_facts(branch: Dict[str, Any]) -> Dict[str, Any]:
    facts = {key: value for key, value in branch.items() if key not in {"id", "opening_hours"}}
    facts["opening_hours"] = [{key: value for key, value in day.items() if value is not None}
                              for day in branch.get("opening_hours") or []]
    return facts


def restaurant_view(result: Dict[str, Any]) -> Dict[str, Any]:
    info = result.get("restaurant") or {}
    view = {"success": True, "name_ar": info.get("name_ar"), "name_en": info.get("name_en"),
            "phone": info.get("phone"), "about_ar": info.get("about_ar"), "about_en": info.get("about_en"),
            "open_now": info.get("open_now")}
    # The branch the guest is sitting in, then the restaurant's other locations.
    view["current_branch"] = _branch_facts(info.get("branch") or {})
    view["other_branches"] = [_branch_facts(branch) for branch in info.get("other_branches") or []]
    return view


def amendment_view(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "order_id": summary.get("order_id"),
        "order_number": summary.get("order_number"),
        "order_status": summary.get("order_status"),
        "kitchen_already_preparing": summary.get("kitchen_already_preparing"),
        "changes": [{"type": change["type"], "name_ar": change["name_ar"], "name_en": change["name_en"],
                     "quantity_before": change["quantity_before"], "quantity_after": change["quantity_after"],
                     "unit_price": jod(change.get("unit_price_minor")),
                     **({"note": change["note"]} if change.get("note") else {})}
                    for change in summary.get("changes") or []],
        "total_before": jod(summary.get("total_before_minor")),
        "total_after": jod(summary.get("total_after_minor")),
        "cancels_order": bool(summary.get("cancels_order")),
    }


def model_view(name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """The compact result given to the model for tool ``name``."""
    if not isinstance(result, dict):
        return result
    if name == "search_knowledge":
        return search_view(result)
    if name == "get_restaurant_info" and result.get("success"):
        return restaurant_view(result)
    if name == "get_product_details" and isinstance(result.get("product"), dict):
        return {"success": True, "product": product_view(result["product"])}
    if name == "get_current_draft":
        return {"success": True, "basket": draft_view(result)}

    view = {key: value for key, value in result.items()
            if key not in {"draft", "summary", "current_summary", "amendment", "confirmation_token", "draft_version"}}
    if isinstance(result.get("draft"), dict):
        view["basket"] = draft_view(result["draft"])
    if isinstance(result.get("summary"), dict):
        view["summary"] = draft_view(result["summary"])
    if isinstance(result.get("current_summary"), dict):
        view["current_summary"] = draft_view(result["current_summary"])
    if isinstance(result.get("amendment"), dict):
        view["amendment"] = amendment_view(result["amendment"])
    if name == "submit_order" and result.get("success"):
        view["total"] = jod_from_display(result.get("total_display_en"))
        view.pop("total_display_ar", None)
        view.pop("total_display_en", None)
    return view


def jod_from_display(display: Optional[str]) -> Optional[str]:
    return display.replace(" JOD", "") if display else None
