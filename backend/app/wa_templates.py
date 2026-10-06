"""Approved WhatsApp message templates for admin sends outside the 24h customer-service window.

Meta only delivers free-form text within 24h of the customer's last message; after that only
approved templates go through. Once the customer replies, normal chat works again.
"""

from __future__ import annotations

import re

import httpx

from .models import MetaSettings
from .services import graph_url, to_whatsapp_id

DEFAULT_TEMPLATE_NAME = "infradealer_followup"
DEFAULT_TEMPLATE_BODY = (
    "Hello {{1}} 👋\n"
    "This is the InfraDealer team 🚜 following up on your enquiry about trucks / heavy equipment.\n"
    "Please reply to this message and we'll continue helping you right here."
)

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _require(meta: MetaSettings, need_waba: bool = False) -> None:
    if not (meta.system_user_token or "").strip():
        raise RuntimeError("System User Token save karo (/meta settings).")
    if need_waba and not (meta.waba_id or "").strip():
        raise RuntimeError("WABA ID save karo (/meta settings → WhatsApp account).")
    if not need_waba and not (meta.phone_number_id or "").strip():
        raise RuntimeError("Phone Number ID save karo (/meta settings).")


def _graph(meta: MetaSettings, method: str, path: str, **kwargs) -> dict:
    with httpx.Client(timeout=20) as client:
        resp = client.request(
            method, graph_url(meta, path),
            headers={"Authorization": f"Bearer {meta.system_user_token}"}, **kwargs,
        )
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        err = (data or {}).get("error") or {}
        raise RuntimeError(err.get("error_user_msg") or err.get("message") or f"Graph API HTTP {resp.status_code}")
    return data


def _vars(text: str) -> list[str]:
    seen: list[str] = []
    for name in _VAR.findall(text or ""):
        if name not in seen:
            seen.append(name)
    return seen


def summarize(t: dict) -> dict:
    comps = {c.get("type"): c for c in (t.get("components") or [])}
    header = comps.get("HEADER") or {}
    body = (comps.get("BODY") or {}).get("text") or ""
    footer = (comps.get("FOOTER") or {}).get("text") or ""
    buttons = (comps.get("BUTTONS") or {}).get("buttons") or []
    named = (t.get("parameter_format") or "").upper() == "NAMED"
    params = _vars(body)
    if not named:
        params.sort(key=lambda v: int(v) if v.isdigit() else 0)
    supported = (
        (not header or (header.get("format") == "TEXT" and not _vars(header.get("text") or "")))
        and not any(_vars(b.get("url") or "") for b in buttons)
    )
    return {
        "name": t.get("name") or "",
        "language": t.get("language") or "",
        "status": t.get("status") or "",
        "category": t.get("category") or "",
        "header": (header.get("text") or "") if header.get("format") == "TEXT" else "",
        "body": body,
        "footer": footer,
        "params": params,
        "named": named,
        "supported": supported,
    }


def list_templates(meta: MetaSettings) -> list[dict]:
    _require(meta, need_waba=True)
    data = _graph(
        meta, "GET", f"{meta.waba_id}/message_templates",
        params={"fields": "name,language,status,category,components,parameter_format", "limit": 250},
    )
    return [summarize(t) for t in data.get("data") or []]


def render(tpl: dict, values: dict) -> str:
    body = _VAR.sub(lambda m: str(values.get(m.group(1), "") or m.group(0)), tpl.get("body") or "")
    parts = [tpl.get("header") or "", body, tpl.get("footer") or ""]
    return "\n".join(p for p in parts if p).strip()


def send_template(meta: MetaSettings, to: str, name: str, language: str, values: dict) -> dict:
    """Send an APPROVED template; returns {wamid, text} where text is the rendered message."""
    _require(meta)
    _require(meta, need_waba=True)
    tpl = next(
        (t for t in list_templates(meta) if t["name"] == name and t["language"] == language),
        None,
    )
    if not tpl:
        raise RuntimeError("Template nahi mila.")
    if tpl["status"] != "APPROVED":
        raise RuntimeError(f"Template abhi {tpl['status'] or 'approved nahi'} hai — Meta approval ke baad hi jayega.")
    if not tpl["supported"]:
        raise RuntimeError("Ye template (media header / dynamic button) panel se support nahi hai.")
    values = {k: str(v or "").strip() for k, v in (values or {}).items()}
    missing = [p for p in tpl["params"] if not values.get(p)]
    if missing:
        raise RuntimeError("Template ke saare variables bharo: " + ", ".join("{{%s}}" % p for p in missing))
    template: dict = {"name": name, "language": {"code": language}}
    if tpl["params"]:
        template["components"] = [{
            "type": "body",
            "parameters": [
                {"type": "text", "text": values[p], **({"parameter_name": p} if tpl["named"] else {})}
                for p in tpl["params"]
            ],
        }]
    data = _graph(meta, "POST", f"{meta.phone_number_id}/messages", json={
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_whatsapp_id(to),
        "type": "template",
        "template": template,
    })
    wamid = ((data.get("messages") or [{}])[0] or {}).get("id") or ""
    return {"wamid": wamid, "text": render(tpl, values)}


def create_default_template(meta: MetaSettings) -> dict:
    """Submit the standard follow-up template for Meta review (approval usually takes minutes)."""
    _require(meta, need_waba=True)
    data = _graph(meta, "POST", f"{meta.waba_id}/message_templates", json={
        "name": DEFAULT_TEMPLATE_NAME,
        "language": "en",
        "category": "MARKETING",
        "components": [{
            "type": "BODY",
            "text": DEFAULT_TEMPLATE_BODY,
            "example": {"body_text": [["Sir"]]},
        }],
    })
    return {"id": data.get("id") or "", "status": data.get("status") or "PENDING", "name": DEFAULT_TEMPLATE_NAME}
