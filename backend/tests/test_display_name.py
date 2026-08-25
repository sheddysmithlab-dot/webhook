"""Display-name flow helpers (no live Graph calls)."""

from app.models import MetaSettings
from app.services import (
    _display_name_steps,
    apply_phone_number_name_update,
)


def test_steps_pending_review():
    steps = {s["id"]: s["state"] for s in _display_name_steps("APPROVED", "PENDING_REVIEW", False)}
    assert steps["submit"] == "done"
    assert steps["review"] == "active"
    assert steps["register"] == "todo"


def test_steps_needs_register():
    steps = {s["id"]: s["state"] for s in _display_name_steps("APPROVED", "APPROVED", True, "APPROVED")}
    assert steps["review"] == "done"
    assert steps["register"] == "active"


def test_apply_phone_number_name_update():
    meta = MetaSettings()
    out = apply_phone_number_name_update(
        meta,
        {
            "decision": "APPROVED",
            "requested_verified_name": "Infradealer",
        },
    )
    assert out["decision"] == "APPROVED"
    assert meta.display_name_decision == "APPROVED"
    assert meta.display_name_requested == "Infradealer"
    assert "webhook" in (meta.display_name_history or "")
