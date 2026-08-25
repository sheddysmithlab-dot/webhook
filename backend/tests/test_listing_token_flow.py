"""Token wallet gate + listing cooldown helpers for WhatsApp listing loop."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.account_filter import apply_remote_account
from app.ai.chat_memory import _is_eligible_to_post, _listing_cooldown_active, _set_next_listing_cooldown
from app.ai.i18n import t
from app.models import InfraDealerAccountState


def test_apply_remote_account_maps_tokens_and_buy_link():
    state = InfraDealerAccountState(mobile="9893313715", account_status="CHECKING", meta_json="{}")
    apply_remote_account(
        state,
        {
            "code": "ACCOUNT_FOUND",
            "account": {
                "user_id": "42",
                "name": "Ridwan",
                "account_type": "token",
                "tokens": 0,
                "buy_link": "https://infradealer.com/wallet",
            },
        },
    )
    import json

    meta = json.loads(state.meta_json or "{}")
    assert meta.get("credits") == 0
    assert meta.get("buy_link") == "https://infradealer.com/wallet"
    assert meta.get("account_type") == "token"
    print("OK apply_remote_account tokens")


def test_eligibility_blocks_token_zero():
    pl = {
        "account_onboarded": True,
        "account_eligibility": "NOT_ELIGIBLE",
        "account_reason": "TOKEN_NO_CREDITS",
    }
    assert _is_eligible_to_post(pl) is False
    pl["account_eligibility"] = "ELIGIBLE"
    assert _is_eligible_to_post(pl) is True
    print("OK eligibility gate")


def test_listing_cooldown():
    pl = {}
    assert _listing_cooldown_active(pl) is False
    _set_next_listing_cooldown(pl, minutes=10)
    assert _listing_cooldown_active(pl) is True
    # Force past cooldown
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    pl["next_listing_not_before"] = past
    assert _listing_cooldown_active(pl) is False
    print("OK listing cooldown")


def test_tokens_buy_i18n():
    msg = t("hinglish", "tokens_buy", link="https://infradealer.com/wallet")
    assert "https://infradealer.com/wallet" in msg
    assert "token" in msg.lower() or "Token" in msg or "wallet" in msg.lower()
    submitted = t("en", "submitted")
    assert "10 minute" in submitted.lower() or "10 minutes" in submitted.lower()
    print("OK i18n tokens_buy / submitted")


if __name__ == "__main__":
    test_apply_remote_account_maps_tokens_and_buy_link()
    test_eligibility_blocks_token_zero()
    test_listing_cooldown()
    test_tokens_buy_i18n()
    print("ALL OK")
