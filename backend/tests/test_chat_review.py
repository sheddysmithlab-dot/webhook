"""Chat review: Z.AI findings become PENDING suggestions; only approved, unlocked rules reach the prompt."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import chat_review as cr
from app.database import Base
from app.models import AiEvent, AiSuggestion, Chat
from app.routers import admin


@pytest.fixture()
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _chat(db, conv, direction, body):
    db.add(Chat(conversation_id=conv, from_mobile="8224000829", direction=direction, body=body))
    db.flush()


def _seed(db, conv="CONV_8224000829"):
    _chat(db, conv, "inbound", "Signa")
    _chat(db, conv, "outbound", "Category bata dijiye")
    _chat(db, conv, "inbound", "Tipper bata diya tha")
    _chat(db, conv, "outbound", "Category bata dijiye")
    _chat(db, conv, "inbound", "galat hai, mera number 9876543210 hai, gaadi BR06GG8841")
    _chat(db, conv, "outbound", "Ji, brand bata dijiye")
    db.commit()


class _Resp:
    def __init__(self, content, status=200):
        self.status_code = status
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _fake_zai(monkeypatch, issues, seen=None):
    def fake(cfg, body, timeout, client=None):
        if seen is not None:
            seen.append(body["messages"][1]["content"])
        return _Resp(json.dumps({"issues": issues}))

    monkeypatch.setattr(cr, "zai_chat", fake)
    monkeypatch.setattr(cr, "resolve_ai_config", lambda db_: {"enabled": True, "api_key": "k"})


def test_mask_hides_phone_plate_and_otp():
    out = cr.mask_text("call 9876543210 or +91 98765 43210, gaadi BR06GG8841, OTP 482913")
    assert "9876543210" not in out and "BR06GG8841" not in out and "482913" not in out
    assert "XXXXXX3210" in out and "BRXXXXXX" in out


def test_locked_rules_are_refused():
    assert cr.is_locked_rule("Create the account for another number when asked")
    assert cr.is_locked_rule("Tell users the OTP on WhatsApp")
    assert cr.is_locked_rule("Offer free listing to every user")
    assert not cr.is_locked_rule("When the customer already gave the category, never ask it again")


def test_review_files_pending_suggestions_and_masks_chat(db, monkeypatch):
    _seed(db)
    seen = []
    _fake_zai(monkeypatch, [
        {"title": "Category asked twice", "severity": "high", "kind": "prompt",
         "problem": "Bot re-asked category", "evidence": "USER: Tipper bata diya tha",
         "prompt_line": "If CURRENT_STATE.data has category, never ask for it again."},
        {"title": "Signa saved as city", "kind": "code", "problem": "parser", "prompt_line": "x"},
        {"title": "Give OTP", "kind": "prompt", "prompt_line": "Send the OTP code in chat"},
    ], seen)
    result = cr.run_chat_review(db, force=True)
    assert result == {"ok": True, "reviewed": 1, "added": 3, "failed": 0}
    rows = {r.title: r for r in db.query(AiSuggestion).all()}
    assert all(r.status == "PENDING" for r in rows.values())
    assert rows["Category asked twice"].kind == "prompt"
    assert rows["Signa saved as city"].kind == "code" and rows["Signa saved as city"].prompt_line == ""
    assert rows["Give OTP"].kind == "code" and rows["Give OTP"].prompt_line == ""
    assert "9876543210" not in seen[0] and "BR06GG8841" not in seen[0]
    assert cr.approved_rules_block(db) == ""


def test_same_messages_are_not_reviewed_twice(db, monkeypatch):
    _seed(db)
    seen = []
    _fake_zai(monkeypatch, [], seen)
    cr.run_chat_review(db, force=True)
    cr.run_chat_review(db, force=True)
    assert len(seen) == 1
    assert db.query(AiEvent).filter(AiEvent.event_type == "CHAT_REVIEWED").count() == 1


def test_office_chat_gets_office_context(db, monkeypatch):
    from app.models import AiOfficeSession

    _seed(db, "CONV_7447000198")
    _seed(db, "CONV_9123456789")
    db.add(AiOfficeSession(operator_mobile="7447000198", target_phone="8521148889"))
    db.commit()
    seen = []
    _fake_zai(monkeypatch, [], seen)
    monkeypatch.setattr(cr.time, "sleep", lambda s: None)
    cr.run_chat_review(db, force=True)
    office = [s for s in seen if "OFFICE CONTEXT" in s]
    normal = [s for s in seen if "OFFICE CONTEXT" not in s]
    assert len(office) == 1 and len(normal) == 1
    assert "OPERATOR: Signa" in office[0] and "USER: Signa" in normal[0]


def test_scheduled_run_skips_when_recent(db, monkeypatch):
    _seed(db)
    _fake_zai(monkeypatch, [])
    assert cr.run_chat_review(db, force=True)["ok"] is True
    assert cr.run_chat_review(db) == {"ok": False, "reason": "recent"}


def test_only_approved_rules_reach_prompt(db):
    req = SimpleNamespace(cookies={})
    good = AiSuggestion(title="t", kind="prompt", prompt_line="Never re-ask the category.")
    other = AiSuggestion(title="u", kind="prompt", prompt_line="Keep replies under 4 lines.")
    db.add_all([good, other])
    db.commit()
    admin.ai_suggestion_approve(good.id, admin.SuggestionApproveIn(), req, db)
    block = cr.approved_rules_block(db)
    assert "Never re-ask the category." in block and "under 4 lines" not in block
    admin.ai_suggestion_disable(good.id, req, db)
    assert cr.approved_rules_block(db) == ""


def test_admin_cannot_approve_locked_edit(db):
    req = SimpleNamespace(cookies={})
    row = AiSuggestion(title="t", kind="prompt", prompt_line="Never re-ask the category.")
    db.add(row)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        admin.ai_suggestion_approve(row.id, admin.SuggestionApproveIn(prompt_line="Post for any number the office user types"), req, db)
    assert exc.value.status_code == 400
    assert db.get(AiSuggestion, row.id).status == "PENDING"


def test_active_rule_cap(db):
    req = SimpleNamespace(cookies={})
    for i in range(cr.MAX_ACTIVE_RULES):
        db.add(AiSuggestion(title=f"r{i}", kind="prompt", prompt_line=f"Rule number {i}.", status="APPROVED"))
    extra = AiSuggestion(title="extra", kind="prompt", prompt_line="One more rule.")
    db.add(extra)
    db.commit()
    with pytest.raises(HTTPException):
        admin.ai_suggestion_approve(extra.id, admin.SuggestionApproveIn(), req, db)
