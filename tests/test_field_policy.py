"""Field governance (core/field_policy.py) — the data dictionary is the single
source for what the edit form may touch, and locked fields fail closed."""

from __future__ import annotations

from core import field_policy as fp
from core import property_overrides as po


def test_reference_fields_are_never_user_editable():
    editable = fp.user_editable_fields()
    for locked in ("market", "submarket", "rent_per_sqft"):
        assert locked not in editable
    # The v1 user set matches the card's hand-editable fields exactly.
    assert editable == {
        "units", "year_built", "last_remodel", "asset_class",
        "property_type", "occupancy_pct", "avg_sqft", "avg_rent",
        "owner", "manager", "management_company", "pm_software",
        "asset_or_fee",
    }


def test_unknown_field_fails_closed_to_reference():
    assert fp.tier_of("some_future_field") == fp.TIER_REFERENCE


def test_ui_derives_editable_set_from_policy():
    """One shared predicate: the UI must not re-enumerate the list."""
    from ui import property_detail as pd
    assert pd._EDITABLE_FIELDS == fp.user_editable_fields()


def test_saving_a_locked_field_is_dropped():
    cleaned = po._clean({"units": 24, "market": "HACKED", "avg_rent": ""})
    assert cleaned == {"units": 24}     # locked field gone, empty value gone
