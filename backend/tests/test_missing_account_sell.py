"""Missing-account WhatsApp users must still be able to start sell/buy collection."""

from __future__ import annotations

from app.ai.chat_memory import _is_eligible_to_post


def test_missing_account_can_collect_listing():
    assert _is_eligible_to_post({
        "account_type": "MISSING",
        "account_eligibility": "NOT_ELIGIBLE",
        "account_reason": "ACCOUNT_NOT_FOUND",
    }) is True
    assert _is_eligible_to_post({
        "account_type": "missing",
        "account_eligibility": "NOT_ELIGIBLE",
        "account_reason": "ACCOUNT_MISSING",
    }) is True
    assert _is_eligible_to_post({
        "account_type": "FREE",
        "account_eligibility": "NOT_ELIGIBLE",
        "account_reason": "FREE_NOT_ONBOARDED",
    }) is True


def test_token_without_credits_blocked():
    assert _is_eligible_to_post({
        "account_type": "TOKEN",
        "account_eligibility": "NOT_ELIGIBLE",
        "account_reason": "TOKEN_NO_CREDITS",
        "account_gate": "ELIGIBILITY_BLOCKED",
    }) is False


def test_onboarded_always_ok():
    assert _is_eligible_to_post({
        "account_onboarded": True,
        "account_eligibility": "NOT_ELIGIBLE",
        "account_reason": "TOKEN_NO_CREDITS",
    }) is True


if __name__ == "__main__":
    test_missing_account_can_collect_listing()
    test_token_without_credits_blocked()
    test_onboarded_always_ok()
    print("OK")
