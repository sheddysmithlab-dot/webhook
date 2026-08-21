"""Regression: listing delete / link must not wipe chat into buy-sell loop."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.account import (
    wants_clear_conversation,
    wants_delete_listing,
    wants_last_post,
    wants_listing_link,
)


def test_delete_listing_not_clear_chat():
    msg = "Mene Jo listing Dali h usey delete Krna h website se"
    assert wants_delete_listing(msg) is True
    assert wants_clear_conversation(msg) is False
    assert wants_clear_conversation("delete conversation") is True
    assert wants_clear_conversation("clear chat") is True
    assert wants_clear_conversation("hata do") is False  # alone too vague now
    print("OK delete listing vs clear chat")


def test_link_and_last_post_detect():
    assert wants_listing_link("Link do direct link se open ho jae wo") is True
    assert wants_last_post("Mene last post Kiya kya tha") is True
    assert wants_listing_link("bechna hai tata") is False
    print("OK link / last post detect")


if __name__ == "__main__":
    test_delete_listing_not_clear_chat()
    test_link_and_last_post_detect()
    print("ALL OK")
