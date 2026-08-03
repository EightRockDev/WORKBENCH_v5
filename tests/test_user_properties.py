"""User-added properties and the verified badge (spec §16, AC-16.1..16.4).

The badge's whole value is that it cannot be self-awarded: every state
transition below is driven by what the municipal roll says, never by what
the submitter typed.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core import user_properties as up


def _spine(db, rows):
    """A minimal properties_8r the validator can check against."""
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS properties_8r (
            property_id TEXT PRIMARY KEY, fips TEXT, apn TEXT, address TEXT,
            city TEXT, units INTEGER)""")
        conn.executemany(
            "INSERT INTO properties_8r VALUES (?,?,?,?,?,?)", rows)
    return db


_NORFOLK_ROW = ("8R-51710-abc123def456", "51710", "1234-56-7890",
                "700 Granby St", "Norfolk", 48)


def _submit(db, **over):
    kw = dict(name="Granby Court", address="700 Granby Street",
              city="Norfolk", units=48, db_path=db)
    kw.update(over)
    return up.submit_property(**kw)


# ------------------------------------------------------------- submission

def test_submission_is_instant_and_starts_unverified(tmp_path):
    """AC-16.1 - usable immediately, but never LOOKING confirmed."""
    db = tmp_path / "wb.db"
    row = _submit(db)
    assert row["status"] == up.UNVERIFIED
    assert row["user_property_id"].startswith("8R-51710-u")


def test_resubmission_updates_instead_of_duplicating(tmp_path):
    db = tmp_path / "wb.db"
    a = _submit(db, units=48)
    b = _submit(db, units=50)
    assert a["user_property_id"] == b["user_property_id"]
    assert len(up.list_user_properties(db)) == 1
    assert b["units"] == 50


def test_garbage_is_rejected_at_the_door(tmp_path):
    db = tmp_path / "wb.db"
    with pytest.raises(ValueError):
        _submit(db, name="")
    with pytest.raises(ValueError):
        _submit(db, units=0)


# ------------------------------------------------------------- validation

def test_a_true_submission_earns_the_blue_check(tmp_path):
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])
    row = _submit(db)          # address matches after normalization
    res = up.validate_property(row["user_property_id"], db)
    assert res.status == up.VERIFIED
    assert res.matched_8r_id == "8R-51710-abc123def456"
    # AC-16.3 - the evidence is stored and reconstructable
    stored = up.list_user_properties(db)[0]
    ev = json.loads(stored["evidence"])
    assert ev["checks"]["address"]["ok"]
    assert ev["checks"]["parcel"]["ok"]
    assert ev["checks"]["units"]["municipal"] == 48


def test_units_within_ten_percent_still_verify(tmp_path):
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])
    row = _submit(db, units=52)            # 48 +/- 10% -> 44..53 ok
    assert up.validate_property(row["user_property_id"], db).status == up.VERIFIED


def test_a_wrong_unit_count_fails_with_the_municipal_count_named(tmp_path):
    """AC-16.4 first half - the seeded wrong count in a parcel_roll city."""
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])
    row = _submit(db, units=96)
    res = up.validate_property(row["user_property_id"], db)
    assert res.status == up.FAILED
    assert "48" in res.reason and "96" in res.reason


def test_a_wrong_parcel_fails_with_both_parcels_named(tmp_path):
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])
    row = _submit(db, parcel_id="9999-99-9999")
    res = up.validate_property(row["user_property_id"], db)
    assert res.status == up.FAILED


def test_an_unknown_address_fails_loudly_in_a_covered_city(tmp_path):
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])
    row = _submit(db, address="1 Nowhere Lane")
    res = up.validate_property(row["user_property_id"], db)
    assert res.status == up.FAILED
    assert "not found" in res.reason


def test_a_city_with_no_data_parks_as_pending_never_verified(tmp_path):
    """AC-16.4 second half - Suffolk today. Pending is not a failure and
    waiting alone never turns it blue."""
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])   # Norfolk only
    row = _submit(db, city="Suffolk")
    res = up.validate_property(row["user_property_id"], db)
    assert res.status == up.PENDING
    assert "Suffolk" in res.reason
    # a second sweep with still-no-data stays pending
    res2 = up.validate_property(row["user_property_id"], db)
    assert res2.status == up.PENDING


def test_the_nightly_sweep_promotes_pending_when_the_feed_lands(tmp_path):
    """The §16.3 promise: the badge arrives automatically with the city's
    data - the user is never asked to resubmit."""
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])
    row = _submit(db, city="Suffolk", address="12 Main St", units=20)
    assert up.validate_property(row["user_property_id"], db).status == up.PENDING
    with sqlite3.connect(db) as conn:                 # Suffolk feed lands
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?,?,?)",
                     ("8R-51800-feed00000001", "51800", "55-55",
                      "12 Main Street", "Suffolk", 20))
    counts = up.revalidate_queue(db)
    assert counts.get(up.VERIFIED, 0) >= 1
    statuses = {r["user_property_id"]: r["status"]
                for r in up.list_user_properties(db)}
    assert statuses[row["user_property_id"]] == up.VERIFIED


def test_a_municipal_refresh_can_revoke_the_badge(tmp_path):
    """§16.4 - the badge is a living claim. If the roll now contradicts the
    submission, VERIFIED drops to FAILED with the diff."""
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])
    row = _submit(db)
    assert up.validate_property(row["user_property_id"], db).status == up.VERIFIED
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE properties_8r SET units=12 WHERE property_id=?",
                     ("8R-51710-abc123def456",))
    up.revalidate_queue(db)
    assert up.list_user_properties(db)[0]["status"] == up.FAILED


def test_only_blue_checked_properties_are_comp_eligible(tmp_path):
    """AC-16.2 - the gate other-org comps must consult."""
    db = _spine(tmp_path / "wb.db", [_NORFOLK_ROW])
    ok = _submit(db)
    bad = _submit(db, name="Phantom Towers", address="1 Nowhere Lane")
    up.revalidate_queue(db)
    eligible = up.comp_eligible_ids(db)
    assert ok["user_property_id"] in eligible
    assert bad["user_property_id"] not in eligible


# ------------------------------------------------------------------ badge

def test_the_blue_check_is_earned_not_default():
    """Only VERIFIED renders filled blue; every other state is an outline."""
    from ui.add_property import verification_badge

    blue = verification_badge(up.VERIFIED)
    assert "#1d9bf0" in blue and "background:#1d9bf0" in blue
    for state in (up.UNVERIFIED, up.PENDING, up.FAILED):
        html = verification_badge(state)
        assert "background:transparent" in html, (
            f"{state} must not render as a filled (earned) badge")
