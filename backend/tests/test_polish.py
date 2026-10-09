"""Z.AI response step: rule drafts are rephrased only when facts survive."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.ai import polish


@pytest.fixture()
def zai(monkeypatch):
    monkeypatch.setattr(polish, "resolve_ai_config", lambda db: {
        "enabled": True, "api_key": "k", "api_base": "https://api.z.ai/api/paas/v4", "model": "glm-4.5-flash",
    })
    monkeypatch.setattr(polish, "_pause_until", 0.0)
    calls = []

    def use(answer):
        def fake(cfg, user_text, draft, lang):
            calls.append(draft)
            return answer
        monkeypatch.setattr(polish, "_call_zai", fake)
        return calls

    return use


def test_question_is_rephrased(zai):
    calls = zai("Sir, ye machine kitne saal purani hai? Model year bata dijiye.")
    out, how = polish.polish_reply(None, "Kobelco SK220", "Model year kya hai?", "hinglish")
    assert how == "polished" and calls and "saal" in out


def test_changed_number_keeps_draft(zai):
    zai("Sir, price 30 lakh confirm karein?")
    out, how = polish.polish_reply(None, "", "Price 32 lakh sahi hai?", "hinglish")
    assert (out, how) == ("Price 32 lakh sahi hai?", "kept")


def test_fake_submit_keeps_draft(zai):
    zai("Listing submit ho gayi hai, kitne hours chali hai?")
    out, how = polish.polish_reply(None, "", "Kitne operating hours hain?", "hinglish")
    assert (out, how) == ("Kitne operating hours hain?", "kept")


def test_dropped_question_keeps_draft(zai):
    zai("Theek hai Sir.")
    assert polish.polish_reply(None, "", "Location kya hai?", "hinglish")[1] == "kept"


@pytest.mark.parametrize("draft", [
    "Please confirm details:\nVehicle: Kobelco SK220\nYear: 2022\nPrice: 3200000\n\nSahi hain to Haan likhiye.",
    "Listing dekhein: https://infradealer.com/listings/200",
    "OTP bhej diya hai, 6 digit OTP likhiye.",
    "Password set kijiye (kam se kam 8 characters).",
])
def test_structured_drafts_never_sent_to_zai(zai, draft):
    calls = zai("anything")
    assert polish.polish_reply(None, "", draft, "hinglish") == (draft, "skipped")
    assert calls == []


def test_off_without_key(monkeypatch):
    monkeypatch.setattr(polish, "resolve_ai_config", lambda db: {"enabled": False, "api_key": ""})
    assert polish.polish_reply(None, "", "Model year kya hai?", "hinglish") == ("Model year kya hai?", "off")


def test_zai_error_keeps_draft_and_pauses(zai, monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("slow")
    monkeypatch.setattr(polish, "_call_zai", boom)
    assert polish.polish_reply(None, "", "Model year kya hai?", "hinglish") == ("Model year kya hai?", "error")
    calls = zai("Sir, model year kya hai?")
    assert polish.polish_reply(None, "", "Model year kya hai?", "hinglish")[1] == "paused"
    assert calls == []


class _Resp:
    def __init__(self, status):
        self.status_code = status


class _Http:
    def __init__(self, statuses):
        self.statuses, self.models = list(statuses), []

    def post(self, url, headers=None, json=None, timeout=None):
        self.models.append(json["model"])
        return _Resp(self.statuses.pop(0))


def test_zai_chat_falls_back_on_rate_limit():
    from app.services import zai_chat

    cfg = {"api_base": "https://api.z.ai/api/paas/v4", "api_key": "k", "model": "glm-4.5-flash"}
    http = _Http([429, 200])
    assert zai_chat(cfg, {"messages": []}, 5, http).status_code == 200
    assert http.models == ["glm-4.7-flash", "glm-4.5-flash"]
    http = _Http([200])
    zai_chat(cfg, {"messages": []}, 5, http)
    assert http.models == ["glm-4.7-flash"]
    http = _Http([401])
    assert zai_chat(cfg, {"messages": []}, 5, http).status_code == 401 and len(http.models) == 1
