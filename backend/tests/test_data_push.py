"""data_push listing URL + open-token helpers."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.data_push import listing_open_url, public_listing_url, wa_open_token


def test_public_listing_url():
    assert public_listing_url("104") == "https://infradealer.com/listings/104"
    assert public_listing_url("") == ""
    print("OK public_listing_url")


def test_wa_open_link_has_token():
    url = listing_open_url("104", mobile="9876543210")
    assert url.startswith("https://infradealer.com/listings/104")
    assert "from=whatsapp" in url
    assert "wa=9876543210" in url
    assert "t=" in url
    assert wa_open_token("9876543210", "104")
    # Prefer remote URL but normalize /listing/ → /listings/
    remote = listing_open_url(
        "104",
        mobile="9876543210",
        payload={"listing": {"url": "https://infradealer.com/listing/104"}},
    )
    assert "/listings/104" in remote
    print("OK wa open link")


if __name__ == "__main__":
    test_public_listing_url()
    test_wa_open_link_has_token()
    print("ALL OK")
