"""Data Filter agent tests — InfraDealer Data_filter markdown cases."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.data_filteration import (
    filter_collected,
    filter_payload,
    is_collection_ready,
    normalize_currency,
    normalize_year,
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
    from app.ai import data_filter
    from app.ai.data_filter import process_listing_intelligence, validate_required_fields

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
    assert data_filter.FILTER_VERSION
    print("OK data_filter alias")


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
    print("ALL OK")
