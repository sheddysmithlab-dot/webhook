"""The AI Review list gets a text title even when the draft stores the vehicle as an object."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routers.admin import vehicle_label


def test_object_vehicle_becomes_text():
    assert vehicle_label({"brand": "Tata", "model": "5530", "year": 2023}) == "Tata 5530 2023"


def test_brand_repeated_as_model_is_not_doubled():
    assert vehicle_label({"brand": "JCB", "model": "JCB", "year": 2015}) == "JCB 2015"


def test_text_and_empty_values():
    assert vehicle_label("Tata 709") == "Tata 709"
    assert vehicle_label(None) == ""
    assert vehicle_label({}) == ""
