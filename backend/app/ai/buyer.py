"""Buyer requests: answer with matching InfraDealer listings instead of a sell card.

A BUY conversation never builds a listing card. Once the requirement is known
(vehicle, budget, location) the customer gets the matching live listings and a
website search link. The same requirement is not re-sent on every message.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from ..models import AiConversation
from .schema import normalize_vehicle_category
from .tools import _payload, _write_payload

log = logging.getLogger("infradealer.ai.buyer")

PUBLIC_LISTINGS_API = "https://api.infradealer.com/api/listings"
SITE_LISTINGS = "https://infradealer.com/listings"
MAX_MATCHES = 3
BUDGET_STRETCH = 1.15

# Website category ids (Frontend Listings filter).
_SITE_CATEGORY = {
    "Truck": "trucks",
    "JCB": "jcb",
    "Backhoe Loader": "jcb",
    "Excavator": "excavator",
    "Poclain": "excavator",
    "Road Roller": "road-roller",
    "Crane": "crane",
    "Dumper": "dumpers",
    "Tipper": "dumpers",
    "Bus": "buses",
    "Tractor": "tractors",
    "Loader": "loaders",
    "Crusher": "crushers",
    "Other": "others",
}


def is_buyer(payload: dict) -> bool:
    return str((payload or {}).get("intent") or "").upper() == "BUY"


def _amount(value) -> int:
    from .data_filteration import normalize_currency

    cur = normalize_currency(value)
    return int(cur["value"]) if cur else 0


def _year(value) -> int:
    m = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(m.group(0)) if m else 0


def buyer_query(payload: dict) -> dict:
    pl = payload or {}
    brand = str(pl.get("brand") or "").strip()
    model = str(pl.get("model") or "").strip()
    category = normalize_vehicle_category(pl.get("category") or pl.get("type") or "")
    return {
        "brand": brand,
        "model": model,
        "category": category,
        "state": str(pl.get("state") or pl.get("location") or "").strip(),
        "budget": _amount(pl.get("budget_max") or pl.get("budget")),
        "year_min": _year(pl.get("year_min") or pl.get("year")),
    }


def _query_key(q: dict) -> str:
    return "|".join(str(q.get(k) or "") for k in ("brand", "model", "category", "state", "budget", "year_min"))


def _api_params(q: dict, with_state: bool = True) -> dict:
    params: dict = {"limit": 20}
    term = q["model"] or q["brand"]
    if term:
        params["search"] = term
    elif q["category"] in _SITE_CATEGORY:
        params["category"] = _SITE_CATEGORY[q["category"]]
    if with_state and q["state"]:
        params["state"] = q["state"]
    return params


def search_url(q: dict) -> str:
    params = _api_params(q)
    params.pop("limit", None)
    return f"{SITE_LISTINGS}?{urlencode(params)}" if params else SITE_LISTINGS


def fetch_listings(params: dict) -> list[dict]:
    try:
        resp = httpx.get(PUBLIC_LISTINGS_API, params=params, timeout=4.0)
        resp.raise_for_status()
        rows = resp.json().get("listings") or []
        return [r for r in rows if isinstance(r, dict)]
    except Exception as exc:
        log.warning("buyer.search failed: %s", exc)
        return []


def _fits(row: dict, q: dict) -> bool:
    price = _amount(row.get("price"))
    if q["budget"] and price and price > q["budget"] * BUDGET_STRETCH:
        return False
    year = _year(row.get("year"))
    if q["year_min"] and year and year < q["year_min"]:
        return False
    return True


def find_matches(q: dict) -> tuple[list[dict], bool]:
    """Matching listings, and whether they are from the requested state."""
    if not (q["model"] or q["brand"] or q["category"]):
        return [], True
    rows = [r for r in fetch_listings(_api_params(q)) if _fits(r, q)]
    if rows or not q["state"]:
        return rows[:MAX_MATCHES], True
    rows = [r for r in fetch_listings(_api_params(q, with_state=False)) if _fits(r, q)]
    return rows[:MAX_MATCHES], False


def _vehicle_label(q: dict) -> str:
    name = " ".join(x for x in (q["brand"], q["model"]) if x)
    if q["model"] and q["brand"] and q["model"].lower().startswith(q["brand"].lower()):
        name = q["model"]
    return name or q["category"] or "Vehicle"


def _listing_line(i: int, row: dict) -> str:
    from .office_mode import _inr

    bits = [str(row.get("title") or "Listing").strip()[:60]]
    price = _inr(row.get("price"))
    if price:
        bits.append(price)
    place = str(row.get("city") or row.get("location") or row.get("state") or "").strip()
    if place:
        bits.append(place)
    line = f"{i}. " + " — ".join(bits)
    lid = row.get("id") or row.get("_id")
    return line + (f"\n   {SITE_LISTINGS}/{lid}" if lid else "")


def buyer_text(q: dict, matches: list[dict], in_state: bool, lang: str = "hinglish") -> str:
    from .office_mode import _inr

    english = (lang or "").lower() in {"en", "english"}
    lines = ["🔎 *Your requirement*" if english else "🔎 *Aapki requirement*"]
    lines.append(f"• Vehicle: {_vehicle_label(q)}")
    if q["year_min"]:
        lines.append(f"• Model: {q['year_min']} or newer" if english else f"• Model: {q['year_min']} ya naya")
    if q["budget"]:
        lines.append(f"• Budget: up to {_inr(q['budget'])}" if english else f"• Budget: {_inr(q['budget'])} tak")
    if q["state"]:
        lines.append(f"• Location: {q['state']}")
    lines.append("")
    if matches:
        if in_state or not q["state"]:
            lines.append("*Matching listings:*" if english else "*InfraDealer par matching listings:*")
        else:
            lines.append(
                f"*Nothing in {q['state']} yet — matches in other states:*" if english
                else f"*{q['state']} me abhi nahi mili — dusre states me ye hain:*"
            )
        lines.extend(_listing_line(i, row) for i, row in enumerate(matches, 1))
        lines.append("")
        lines.append(f"All listings: {search_url(q)}" if english else f"Sab listings: {search_url(q)}")
    else:
        lines.append(
            "No listing matches this requirement on InfraDealer yet. Keep checking here:" if english
            else "Abhi InfraDealer par is requirement ki listing nahi hai. Yahan dekhte rahiye:"
        )
        lines.append(search_url(q))
    lines.append(
        "Want to change the budget or location? Just tell me." if english
        else "Budget ya location badalna ho to bata dijiye."
    )
    return "\n".join(lines)


def buyer_reply(db: Session, conv: AiConversation, lang: str = "hinglish", repeat: bool = False) -> str:
    """Matching listings for a ready BUY requirement; "" when this exact search was already sent."""
    payload = _payload(conv)
    q = buyer_query(payload)
    key = _query_key(q)
    if not repeat and payload.get("buy_search_key") == key:
        return ""
    matches, in_state = find_matches(q)
    payload["buy_search_key"] = key
    payload["awaiting_confirm"] = False
    _write_payload(conv, payload)
    log.info(
        "buyer.search mobile=***%s matches=%d in_state=%s",
        (conv.mobile or "")[-4:], len(matches), in_state,
    )
    return buyer_text(q, matches, in_state, lang)
