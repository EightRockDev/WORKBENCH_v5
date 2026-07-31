"""Learning apartment use codes from known-multifamily parcels.

The motivating case: Portsmouth publishes 36,464 parcels whose use codes are
bare integers, so every text rule misses and the city reports zero
multifamily despite 45 known apartment properties.

The acceptance tests matter less than the rejection tests. Sweeping a generic
code like "Residential" into the multifamily set would bury tens of thousands
of houses in the comp pool — the same failure that VB zoning "R-40" caused by
substring-matching "r-4".
"""

from __future__ import annotations

import sqlite3
from collections import Counter

from core.use_code_learn import (
    CityLearning,
    is_multifamily_learned,
    is_opaque,
    learn_city,
    load,
    save,
)


# ---------------------------------------------------------------------------
# Which codes even need learning
# ---------------------------------------------------------------------------

def test_numeric_codes_are_opaque():
    for code in ("9", "18", " 7 ", "405", "12.0"):
        assert is_opaque(code), code


def test_readable_codes_are_not_opaque():
    """phase0's text rules already handle these; learning would add only risk."""
    for code in ("Apartment", "Multi Family", "Residential", "MF", "R-40"):
        assert not is_opaque(code), code


def test_blank_is_not_opaque():
    assert not is_opaque(None)
    assert not is_opaque("   ")


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------

def test_learns_the_apartment_code_from_known_properties():
    """12 of 14 known Portsmouth apartment parcels carry code 18."""
    mf = ["18"] * 12 + ["9", "7"]
    citywide = Counter({"9": 14000, "18": 900, "7": 9000, "5": 8000, "3": 4564})
    out = learn_city("Portsmouth", mf, citywide)
    assert out.accepted_codes == ["18"]


def test_rejects_a_code_carried_by_most_of_the_city():
    """A code on 40% of the roll cannot mean apartments, however many known
    MF parcels happen to carry it."""
    mf = ["9"] * 20
    citywide = Counter({"9": 14000, "18": 900})
    out = learn_city("Portsmouth", mf, citywide)
    assert out.accepted_codes == []
    ev = next(e for e in out.evidence if e.code == "9")
    assert "too common" in ev.reason


def test_rejects_a_code_with_too_little_support():
    """One or two matches could be a bad address match, not a rule."""
    mf = ["18", "18", "9", "7", "5"]
    citywide = Counter({"18": 100, "9": 100, "7": 100, "5": 100, "x": 9999})
    out = learn_city("Portsmouth", mf, citywide)
    assert "18" not in out.accepted_codes


def test_rejects_an_incidental_code_even_with_support():
    """Present on enough parcels, but a small fraction of the city's known MF
    — more likely a mixed-use neighbour than the apartment code."""
    mf = ["18"] * 40 + ["7"] * 4
    citywide = Counter({"18": 500, "7": 400, "z": 40000})
    out = learn_city("Portsmouth", mf, citywide)
    assert out.accepted_codes == ["18"]
    ev = next(e for e in out.evidence if e.code == "7")
    assert "known MF" in ev.reason


def test_learning_nothing_is_not_an_error():
    out = learn_city("Suffolk", [], Counter())
    assert out.accepted_codes == []
    assert out.known_mf_parcels == 0
    assert any("nothing to learn" in line for line in out.describe())


def test_evidence_is_reported_for_every_candidate():
    """Accepted or not, each code's numbers are shown — the operator has to be
    able to audit a rule before it changes what the comp engine sees."""
    mf = ["18"] * 12 + ["9", "9", "9"]
    citywide = Counter({"9": 14000, "18": 900})
    lines = "\n".join(learn_city("Portsmouth", mf, citywide).describe())
    assert "ACCEPT" in lines and "reject" in lines
    assert "'18'" in lines and "'9'" in lines


# ---------------------------------------------------------------------------
# Persistence and lookup
# ---------------------------------------------------------------------------

def _conn():
    return sqlite3.connect(":memory:")


def test_round_trips_through_the_database():
    conn = _conn()
    learning = learn_city("Portsmouth", ["18"] * 12, Counter({"18": 900, "9": 14000}))
    assert save(conn, learning, "2026-07-31T00:00:00") == 1
    assert load(conn) == {"Portsmouth": {"18"}}


def test_relearning_replaces_rather_than_accumulates():
    """A corrected roll must not leave last week's wrong code in force."""
    conn = _conn()
    save(conn, learn_city("Portsmouth", ["18"] * 12,
                          Counter({"18": 900, "9": 14000})), "t1")
    save(conn, learn_city("Portsmouth", ["21"] * 12,
                          Counter({"21": 900, "9": 14000})), "t2")
    assert load(conn) == {"Portsmouth": {"21"}}


def test_lookup_is_scoped_per_city():
    """Portsmouth's 18 says nothing about Suffolk's 18."""
    learned = {"Portsmouth": {"18"}}
    assert is_multifamily_learned("Portsmouth", "18", learned)
    assert not is_multifamily_learned("Suffolk", "18", learned)
    assert not is_multifamily_learned("Portsmouth", "9", learned)


def test_lookup_tolerates_whitespace_and_non_strings():
    learned = {"Portsmouth": {"18"}}
    assert is_multifamily_learned("Portsmouth", " 18 ", learned)
    assert not is_multifamily_learned("Portsmouth", None, learned)


def test_empty_map_never_claims_multifamily():
    assert not is_multifamily_learned("Portsmouth", "18", {})


# ---------------------------------------------------------------------------
# End-to-end: the Portsmouth scenario through run_phase0's wiring
# ---------------------------------------------------------------------------

def _portsmouth_db(tmp_path):
    """A roll with numeric use codes and no unit counts, plus the crosswalk
    that links 12 known apartment properties to their parcels."""
    db = tmp_path / "wb.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE properties_8r (
            property_id TEXT PRIMARY KEY, city TEXT, use_code TEXT, units REAL);
        CREATE TABLE properties (
            property_id TEXT PRIMARY KEY, city TEXT, units REAL);
        CREATE TABLE property_crosswalk (
            legacy_id TEXT, r8_id TEXT);
    """)
    # 12 known apartment parcels, all carrying code 18, no units in the feed.
    for i in range(12):
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?)",
                     (f"8R-P-{i}", "Portsmouth", "18", None))
        conn.execute("INSERT INTO properties VALUES (?,?,?)",
                     (f"LEG-{i}", "Portsmouth", 120))
        conn.execute("INSERT INTO property_crosswalk VALUES (?,?)",
                     (f"LEG-{i}", f"8R-P-{i}"))
    # The rest of the roll: mostly houses on code 9, a few more 18s.
    for i in range(14000):
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?)",
                     (f"8R-H-{i}", "Portsmouth", "9", None))
    for i in range(300):
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?)",
                     (f"8R-A-{i}", "Portsmouth", "18", None))
    conn.commit()
    conn.close()
    return db


def test_portsmouth_scenario_learns_and_then_classifies(tmp_path):
    """Before: 36k parcels, zero multifamily. After: the apartment code is
    known and those parcels classify."""
    from core.phase0 import is_mf_ten_plus, is_mf_ten_plus_for_city
    from scripts.run_phase0 import _learn_use_codes

    db = _portsmouth_db(tmp_path)

    # Baseline: the text rules see nothing in a numeric roll.
    assert not is_mf_ten_plus("18", None)

    lines = _learn_use_codes(db)
    assert any("ACCEPT" in ln and "'18'" in ln for ln in lines), lines

    conn = sqlite3.connect(db)
    learned = load(conn)
    assert learned == {"Portsmouth": {"18"}}

    # The apartment code now classifies; the house code still does not.
    assert is_mf_ten_plus_for_city("Portsmouth", "18", None, learned)
    assert not is_mf_ten_plus_for_city("Portsmouth", "9", None, learned)
    # And nothing leaked into another city.
    assert not is_mf_ten_plus_for_city("Suffolk", "18", None, learned)


def test_a_known_unit_count_still_overrides_a_learned_code(tmp_path):
    """A duplex on the apartment code is still a duplex."""
    from core.phase0 import is_mf_ten_plus_for_city

    learned = {"Portsmouth": {"18"}}
    assert not is_mf_ten_plus_for_city("Portsmouth", "18", 2, learned)
    assert is_mf_ten_plus_for_city("Portsmouth", "18", 120, learned)


def test_learning_is_skipped_where_text_rules_already_work(tmp_path):
    """Norfolk publishes 'Apartment' - learning there could only add error."""
    from scripts.run_phase0 import _learn_use_codes

    db = tmp_path / "nf.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE properties_8r (
            property_id TEXT PRIMARY KEY, city TEXT, use_code TEXT, units REAL);
        CREATE TABLE properties (
            property_id TEXT PRIMARY KEY, city TEXT, units REAL);
        CREATE TABLE property_crosswalk (legacy_id TEXT, r8_id TEXT);
    """)
    for i in range(12):
        conn.execute("INSERT INTO properties_8r VALUES (?,?,?,?)",
                     (f"8R-N-{i}", "Norfolk", "Apartment", None))
        conn.execute("INSERT INTO properties VALUES (?,?,?)",
                     (f"LEG-{i}", "Norfolk", 100))
        conn.execute("INSERT INTO property_crosswalk VALUES (?,?)",
                     (f"LEG-{i}", f"8R-N-{i}"))
    conn.commit(); conn.close()

    _learn_use_codes(db)
    assert load(sqlite3.connect(db)) == {}


def test_no_crosswalk_is_reported_not_crashed(tmp_path):
    from scripts.run_phase0 import _learn_use_codes

    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    lines = _learn_use_codes(db)
    assert any("nothing to learn" in ln for ln in lines)
