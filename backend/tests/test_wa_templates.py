"""Template listing / sending for admin messages outside the 24h window (Graph mocked)."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import wa_templates
from app.models import MetaSettings

TEMPLATES = {
    "data": [
        {
            "name": "infradealer_followup", "language": "en", "status": "APPROVED", "category": "MARKETING",
            "components": [{"type": "BODY", "text": "Hello {{1}} 👋 team here, {{2}}."}],
        },
        {
            "name": "order_update", "language": "en", "status": "APPROVED", "category": "UTILITY",
            "parameter_format": "NAMED",
            "components": [
                {"type": "HEADER", "format": "TEXT", "text": "Update"},
                {"type": "BODY", "text": "Hi {{customer}}, card {{card}} is live."},
                {"type": "FOOTER", "text": "InfraDealer"},
            ],
        },
        {
            "name": "promo_image", "language": "en", "status": "APPROVED", "category": "MARKETING",
            "components": [{"type": "HEADER", "format": "IMAGE"}, {"type": "BODY", "text": "Sale"}],
        },
        {"name": "pending_one", "language": "hi", "status": "PENDING", "components": [{"type": "BODY", "text": "x"}]},
    ]
}


def _meta() -> MetaSettings:
    return MetaSettings(waba_id="111", phone_number_id="222", system_user_token="tok", graph_version="v23.0")


@pytest.fixture
def graph(monkeypatch):
    calls = []

    def fake(meta, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return TEMPLATES
        if path.endswith("/messages"):
            return {"messages": [{"id": "wamid.T1"}]}
        return {"id": "tpl1", "status": "PENDING"}

    monkeypatch.setattr(wa_templates, "_graph", fake)
    return calls


def test_list_marks_media_header_unsupported(graph):
    rows = {t["name"]: t for t in wa_templates.list_templates(_meta())}
    assert rows["infradealer_followup"]["params"] == ["1", "2"] and rows["infradealer_followup"]["supported"]
    assert rows["order_update"]["named"] and rows["order_update"]["params"] == ["customer", "card"]
    assert rows["order_update"]["header"] == "Update"
    assert not rows["promo_image"]["supported"]


def test_send_positional_template(graph):
    out = wa_templates.send_template(_meta(), "9893313715", "infradealer_followup", "en", {"1": "Ridwan", "2": "ok"})
    assert out == {"wamid": "wamid.T1", "text": "Hello Ridwan 👋 team here, ok."}
    method, path, kwargs = graph[-1]
    payload = kwargs["json"]
    assert path == "222/messages" and payload["to"] == "919893313715" and payload["type"] == "template"
    assert payload["template"]["components"][0]["parameters"] == [
        {"type": "text", "text": "Ridwan"}, {"type": "text", "text": "ok"},
    ]


def test_send_named_template_renders_header_footer(graph):
    out = wa_templates.send_template(_meta(), "9893313715", "order_update", "en", {"customer": "A", "card": "C-1"})
    assert out["text"] == "Update\nHi A, card C-1 is live.\nInfraDealer"
    params = graph[-1][2]["json"]["template"]["components"][0]["parameters"]
    assert params[0] == {"type": "text", "text": "A", "parameter_name": "customer"}


def test_send_rejects_missing_vars_pending_and_unsupported(graph):
    with pytest.raises(RuntimeError, match="variables"):
        wa_templates.send_template(_meta(), "9893313715", "infradealer_followup", "en", {"1": "x"})
    with pytest.raises(RuntimeError, match="PENDING"):
        wa_templates.send_template(_meta(), "9893313715", "pending_one", "hi", {})
    with pytest.raises(RuntimeError, match="support"):
        wa_templates.send_template(_meta(), "9893313715", "promo_image", "en", {})
    assert not any(c[1].endswith("/messages") for c in graph)


def test_create_default_and_missing_waba(graph):
    out = wa_templates.create_default_template(_meta())
    assert out["name"] == wa_templates.DEFAULT_TEMPLATE_NAME and out["status"] == "PENDING"
    body = graph[-1][2]["json"]
    assert graph[-1][1] == "111/message_templates" and body["category"] == "MARKETING"
    with pytest.raises(RuntimeError, match="WABA"):
        wa_templates.list_templates(MetaSettings(system_user_token="tok", phone_number_id="2", waba_id=""))
