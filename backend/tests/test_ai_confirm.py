from app.ai.extract import extract_from_text, extract_state
from app.ai.i18n import t
from app.ai.free_chat import is_repeat_outbound, too_similar


def _card(location: str) -> str:
    return (
        "Vehicle : Tata Signa\n"
        "Category : Dumper\n"
        "Year : 2024\n"
        "Rate : 42 lakh\n"
        f"Location : {location}\n"
        "Phone : 9826845493\n\n"
        + t("hinglish", "confirm_ask")
    )


def test_extract_hindi_and_english_state():
    assert extract_state("location. मध्य प्रदेश") == "Madhya Pradesh"
    assert extract_from_text("Location. मध्य प्रदेश")["state"] == "Madhya Pradesh"
    assert extract_from_text("Madhya pradesh")["state"] == "Madhya Pradesh"
    assert extract_from_text("Location karo").get("state") in (None, "")


def test_updated_summary_is_not_a_repeat():
    old = _card("Bihar / Patna")
    new = _card("Madhya Pradesh")
    assert not too_similar(old, new)
    assert not is_repeat_outbound(new, [old])
    assert too_similar(old, old)
    assert is_repeat_outbound(old, [old])


def test_confirm_ready_is_not_repeat_of_card():
    old = _card("Madhya Pradesh")
    ready = t("hinglish", "confirm_ready")
    assert not too_similar(old, ready)
    assert not is_repeat_outbound(ready, [old])


def test_understands_existing_account_and_new_chat():
    from app.ai.account import says_has_account, wants_new_chat

    assert says_has_account("Mera tow pahle se hi account bana hua he ... check tow karo")
    assert says_has_account("मेरा तो पहले से ही account बना हुआ है")
    assert wants_new_chat("New chat start karo")
    assert not wants_new_chat("Ha")
