"""Data Filter agent tests — InfraDealer Data_filter markdown cases."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.data_filteration import (
    build_canonical_listing,
    build_missing_fields,
    calculate_confidence,
    calculate_quality_score,
    check_duplicates,
    check_master_data,
    filter_collected,
    filter_payload,
    is_collection_ready,
    normalize_currency,
    normalize_year,
    resolve_category,
    validate_cross_fields,
    validate_field_types,
    validate_ranges,
    validate_required_fields,
)


def test_complete_truck_ready():
    pl = {
        "intent": "SELL",
        "category": "Truck",
        "brand": "Tata",
        "model": "1618",
        "year": 2023,
        "km": 82000,
        "location": "Indore",
        "state": "Madhya Pradesh",
        "city": "Indore",
        "expected_price": "23 lakh",
    }
    assert is_collection_ready(pl)
    result = filter_payload(pl)
    assert result.readiness == "READY_FOR_CONFIRMATION"
    assert result.ready is True
    assert result.normalized_data.get("brand") == "Tata"
    assert result.normalized_data.get("price") == 2300000
    print("OK complete truck")


def test_missing_price():
    pl = {
        "intent": "SELL",
        "category": "Truck",
        "brand": "Tata",
        "model": "1618",
        "year": 2023,
        "state": "Madhya Pradesh",
    }
    result = filter_payload(pl)
    assert result.readiness == "MISSING_REQUIRED_DATA"
    fields = [m["field"] for m in result.missing_fields]
    assert "price" in fields
    print("OK missing price")


def test_future_year():
    pl = {
        "intent": "SELL",
        "category": "Truck",
        "brand": "Tata",
        "model": "1618",
        "year": 2035,
        "state": "Madhya Pradesh",
        "expected_price": "10 lakh",
    }
    result = filter_payload(pl)
    codes = [e["code"] for e in result.validation_errors]
    assert "FUTURE_YEAR" in codes
    assert result.readiness == "INVALID_DATA"
    y = normalize_year("2035 model")
    assert y and y["status"] == "INVALID"
    print("OK future year")


def test_natural_price_format():
    cur = normalize_currency("23.5 lac")
    assert cur and cur["value"] == 2350000 and cur["currency"] == "INR"
    cur2 = normalize_currency("23 lakh")
    assert cur2 and cur2["value"] == 2300000
    print("OK natural price")


def test_document_conflict():
    pl = {
        "intent": "SELL",
        "category": "Truck",
        "brand": "Tata",
        "model": "1618",
        "year": 2023,
        "state": "Madhya Pradesh",
        "expected_price": "23 lakh",
        "documents": {"year": 2022},
    }
    result = filter_payload(pl, documents={"year": 2022})
    assert result.readiness == "CONFLICT_REQUIRES_USER"
    assert result.conflicts
    assert result.conflicts[0]["field"] == "year"
    print("OK document conflict")


def test_inferred_not_confirmed():
    pl = {
        "intent": "SELL",
        "category": "JCB",
        "brand": "JCB",
        "model": "3DX",
        "year": 2022,
        "operating_hours": 4500,
        "state": "Madhya Pradesh",
        "expected_price": "18 lakh",
        "confidence": {"model": "INFERRED_BY_AI"},
        "source": {"inferred": {"model": "3DX"}, "customer": {"brand": "JCB"}},
    }
    result = filter_payload(pl)
    assert result.field_status.get("model") == "INFERRED"
    assert result.confirmation.get("required") is True
    print("OK inferred model")


def test_filter_collected_preserves_brand_category():
    pl = {
        "intent": "SELL",
        "category": "Truck",
        "brand": "Tata",
        "model": "407",
        "year": "2019",
        "expected_price": "8 lakh",
        "state": "Madhya Pradesh",
    }
    filtered = filter_collected(pl)
    assert filtered["brand"] == "Tata"
    assert filtered["category"] == "Truck"
    assert is_collection_ready(pl)
    print("OK filter_collected")


def test_nl_extract_message():
    pl = {"intent": "SELL"}
    result = filter_payload(
        pl,
        messages=[{"text": "Tata 1618 2023 model hai, 82 हजार km, Indore, 23 lakh", "source": "USER"}],
    )
    assert result.normalized_data.get("brand") == "Tata" or result.normalized_data.get("year") == 2023
    assert result.normalized_data.get("price") == 2300000 or result.normalized_data.get("year") == 2023
    print("OK nl extract", result.normalized_data)


def test_data_filter_module_alias():
    from app.ai import data_filteration
    from app.ai.data_filteration import process_listing_intelligence, validate_required_fields

    pl = {
        "intent": "SELL",
        "category": "Truck",
        "brand": "Tata",
        "model": "1618",
        "year": 2023,
        "state": "Madhya Pradesh",
        "expected_price": "23.5 lac",
    }
    out = process_listing_intelligence(pl)
    assert out["success"] is True
    assert out["normalized_data"]["price"] == 2350000
    assert out["readiness"] == "READY_FOR_CONFIRMATION"
    missing = validate_required_fields({"brand": "Tata"}, "Truck")
    assert any(m["field"] == "price" for m in missing)
    assert data_filteration.FILTER_VERSION
    print("OK data_filteration module")


def test_validate_ranges_future_year():
    errors = validate_ranges({"year": 2099}, "Truck")
    assert any(e["code"] == "FUTURE_YEAR" for e in errors)


def test_validate_ranges_invalid_price():
    errors = validate_ranges({"price": -5}, "Truck")
    assert any(e["code"] == "INVALID_PRICE" for e in errors)


def test_validate_ranges_year_too_old():
    errors = validate_ranges({"year": 1950}, "Truck")
    assert any(e["code"] == "YEAR_TOO_OLD" for e in errors)


def test_validate_cross_fields_reg_before_mfg():
    flags = validate_cross_fields({"year": 2020, "registration_year": 2018})
    assert any(f["code"] == "REG_BEFORE_MFG" for f in flags)


def test_validate_cross_fields_suspicious_fuel():
    flags = validate_cross_fields({"fuel": "petrol"}, "Excavator")
    assert any(f["code"] == "SUSPICIOUS_FUEL" for f in flags)


def test_validate_field_types_invalid_year():
    errors = validate_field_types({"year": "abcd"})
    assert any(e["code"] == "INVALID_TYPE" and e["field"] == "year" for e in errors)


def test_validate_field_types_non_numeric_price():
    errors = validate_field_types({"price": "lakh"})
    assert any(e["field"] == "price" for e in errors)


def test_calculate_confidence_confirmed():
    out = calculate_confidence({"brand": "CONFIRMED", "year": "NORMALIZED"}, [], [])
    assert out["score"] > 0.8


def test_calculate_confidence_conflict_lowers():
    out = calculate_confidence({"brand": "CONFIRMED"}, [{"field": "year"}], [])
    assert out["score"] <= 0.45


def test_calculate_quality_score():
    out = calculate_quality_score(
        missing=[{"field": "price"}],
        conflicts=[],
        validation_errors=[],
        duplicate={"possible_duplicate": False},
        photos={"received": 2, "valid": 2},
        confidence={"score": 0.9},
    )
    assert "score" in out
    assert out["score"] < 100


def test_check_master_data_unknown_brand():
    warnings = check_master_data({"brand": "X"})
    assert any(w["code"] == "BRAND_NOT_RECOGNIZED" for w in warnings)


def test_check_master_data_known_brand():
    warnings = check_master_data({"brand": "Tata"})
    assert not any(w["code"] == "BRAND_NOT_RECOGNIZED" for w in warnings)


def test_check_duplicates_no_db():
    out = check_duplicates(None, {"brand": "Tata", "model": "1618"})
    assert out["possible_duplicate"] is False


def test_build_canonical_listing_sell():
    listing = build_canonical_listing(
        {"brand": "Tata", "model": "1618", "year": 2020, "state": "MP", "price": 1500000},
        intent="SELL",
        category="Truck",
        photos={"valid": 2},
    )
    assert listing["intent"] == "SELL"
    assert listing["category"] == "Truck"
    assert listing["brand"] == "Tata"
    assert listing["model"] == "1618"
    assert listing["price"] == 1500000
    assert listing["state"] == "MP"
    assert listing["media"]["photo_count"] == 2


def test_build_canonical_listing_minimal():
    listing = build_canonical_listing({}, "BUY", "Truck", {"valid": 0})
    assert listing["intent"] == "BUY"
    assert listing["category"] == "Truck"


def test_resolve_category_from_text():
    assert resolve_category("JCB")
    assert resolve_category("") == ""
    assert resolve_category(None, [{"text": "Tata tipper"}])


def test_build_missing_fields_complete():
    missing = build_missing_fields(
        {"brand": "Tata", "model": "1618", "year": 2020, "price": 1500000, "state": "MP"},
        "Truck",
        "SELL",
    )
    core = {m["field"] for m in missing}
    assert "brand" not in core
    assert "price" not in core


def test_build_missing_fields_empty():
    missing = build_missing_fields({}, "Truck", "SELL")
    fields = {m["field"] for m in missing}
    assert "brand" in fields
    assert "price" in fields


if __name__ == "__main__":
    test_complete_truck_ready()
    test_missing_price()
    test_future_year()
    test_natural_price_format()
    test_document_conflict()
    test_inferred_not_confirmed()
    test_filter_collected_preserves_brand_category()
    test_nl_extract_message()
    test_data_filter_module_alias()
    test_validate_ranges_future_year()
    test_validate_ranges_invalid_price()
    test_validate_ranges_year_too_old()
    test_validate_cross_fields_reg_before_mfg()
    test_validate_cross_fields_suspicious_fuel()
    test_validate_field_types_invalid_year()
    test_validate_field_types_non_numeric_price()
    test_calculate_confidence_confirmed()
    test_calculate_confidence_conflict_lowers()
    test_calculate_quality_score()
    test_check_master_data_unknown_brand()
    test_check_master_data_known_brand()
    test_check_duplicates_no_db()
    test_build_canonical_listing_sell()
    test_build_canonical_listing_minimal()
    test_resolve_category_from_text()
    test_build_missing_fields_complete()
    test_build_missing_fields_empty()
    print("ALL OK")
