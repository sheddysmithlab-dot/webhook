"""WhatsApp registration + password-reset helpers."""

from app.ai.account import (
    suggest_username,
    wants_create_account,
    wants_password_reset,
    should_intercept_account,
    _valid_email,
    _valid_username,
    _valid_password,
    _valid_name,
)


def test_suggest_username():
    assert suggest_username("Shoaib Multani", "8223000824") == "shoaibmultani"
    assert len(suggest_username("A", "8223000824")) >= 3
    print("OK suggest_username")


def test_validators():
    assert _valid_name("Shoaib Multani")
    assert not _valid_name("1")
    assert _valid_email("abc@gmail.com")
    assert not _valid_email("abc")
    assert _valid_username("shoaibmultani")
    assert not _valid_username("ab")
    assert _valid_password("secret1")
    assert not _valid_password("123")
    print("OK validators")


def test_wants_password_reset():
    assert wants_password_reset("password badlo") is True
    assert wants_password_reset("Change password please") is True
    assert wants_password_reset("naya password set karo") is True
    assert wants_password_reset("Bechna hai") is False
    print("OK wants_password_reset")


def test_wants_create_account():
    unmatched = {"wa_account_matched": False, "account_reason": "ACCOUNT_NOT_FOUND"}
    assert wants_create_account("Naya account ban do") is True
    assert wants_create_account("account banao") is True
    assert wants_create_account("Naya bana do", unmatched) is True
    assert wants_create_account("Bechna hai", unmatched) is False
    print("OK wants_create_account")


def test_reg_intercept():
    assert should_intercept_account({"account_step": "offer_create"}, "haan") is True
    assert should_intercept_account({"account_step": "reg_name"}, "Shoaib") is True
    assert should_intercept_account({"account_step": "reg_email"}, "a@b.com") is True
    assert should_intercept_account({"account_step": "ask_exists"}, "JCB 3DX bechna hai 18 lakh") is False
    assert should_intercept_account({"account_step": "pw_otp"}, "123456") is True
    print("OK reg intercept")


if __name__ == "__main__":
    test_suggest_username()
    test_validators()
    test_wants_password_reset()
    test_wants_create_account()
    test_reg_intercept()
    print("ALL OK")
