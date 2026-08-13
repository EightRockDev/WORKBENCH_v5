"""In-workbench public-data pullers: freshness gate, LEI cache, transforms."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd

from core import public_data as pdta


def test_normalize_and_summarize_hmda():
    raw = pd.DataFrame({
        "lei": ["L1", "L1", "L2"],
        "county_code": [51710, 51710, 51810],
        "loan_amount": ["2500000", "NA", "900000"],   # dirty numerics
        "rate_spread": ["0.5", "Exempt", "1.1"],
        "action_taken": [1, 1, 1],
        "ignored_column": ["x", "y", "z"],
    })
    norm = pdta.normalize_hmda(raw, 2025)
    assert "ignored_column" not in norm.columns
    assert norm["loan_amount"].dropna().tolist() == [2500000.0, 900000.0]
    norm["lender_name"] = norm["lei"].map({"L1": "TOWNE", "L2": "AUB"})
    summary = pdta.summarize_lenders(norm)
    towne = summary[summary["lei"] == "L1"].iloc[0]
    # pandas count() skips NaN loan amounts - same behavior as the
    # original GRANITE puller: an origination without an amount isn't
    # counted in the rollup (it stays in the raw table).
    assert towne["n_originations"] == 1
    assert towne["total_loan_amount"] == 2500000.0
    # A frame with no recognized columns normalizes to empty, not a crash.
    assert pdta.normalize_hmda(pd.DataFrame({"zzz": [1]}), 2025).empty


def test_freshness_gate_blocks_repulls(tmp_path):
    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        pdta._stamp(conn, "hmda_originations", "t", "u", 100)
    assert pdta.is_fresh(db, "hmda_originations") is True
    # Stale timestamp -> not fresh.
    old = (dt.datetime.now() - dt.timedelta(days=40)).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE etl_metadata SET last_pull_at = ?", (old,))
    assert pdta.is_fresh(db, "hmda_originations") is False
    # Zero rows -> never fresh (a failed pull must not lock the gate).
    with sqlite3.connect(db) as conn:
        pdta._stamp(conn, "empty_table", "t", "u", 0)
    assert pdta.is_fresh(db, "empty_table") is False
    assert pdta.is_fresh(tmp_path / "missing.db", "x") is False


def test_lei_cache_is_persistent_and_only_queries_new(tmp_path, monkeypatch):
    db = tmp_path / "etl.db"
    calls = []

    class _R:
        status_code = 200
        def json(self):
            return {"data": {"attributes": {"entity": {
                "legalName": {"name": "RESOLVED BANK"}}}}}

    import requests
    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: calls.append(url) or _R())
    names = pdta.resolve_lender_names(db, ["L1", "L2"])
    assert names == {"L1": "RESOLVED BANK", "L2": "RESOLVED BANK"}
    assert len(calls) == 2
    # Second run: cache hit, ZERO network calls.
    names = pdta.resolve_lender_names(db, ["L1", "L2"])
    assert names["L1"] == "RESOLVED BANK" and len(calls) == 2


def test_pull_hmda_fresh_skip_touches_nothing(tmp_path):
    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        pdta._stamp(conn, "hmda_originations", "t", "u", 500)
    assert pdta.pull_hmda(db) == 0   # skipped, no network attempted


def test_pull_hud_fmr_survives_wider_seeded_table(tmp_path, monkeypatch):
    """A seeded/copied hud_fmr can be WIDER than the seven columns we
    write (the owner's had 8) - the 2026-07-31 first live pull crashed
    on a bare-VALUES insert. The pull must name its columns, land the
    live rows, and leave prior-year seeded rows in place."""
    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE hud_fmr (
            fips_county_5 TEXT, year INTEGER, fmr_efficiency REAL,
            fmr_one_bedroom REAL, fmr_two_bedroom REAL,
            fmr_three_bedroom REAL, fmr_four_bedroom REAL,
            metro_name TEXT)""")
        conn.execute("INSERT INTO hud_fmr VALUES "
                     "('51710', 2024, 1000, 1100, 1300, 1700, 2100, 'HR')")
        pdta._stamp(conn, "hud_fmr", "HUD Fair Market Rents", "u", 1)
        # The copied db's stamp was not written by _stamp - it has no
        # "in-workbench" marker, which is what forces the first live pull.
        conn.execute("UPDATE etl_metadata SET description = "
                     "'copied from v2.4.1 db' WHERE table_name = 'hud_fmr'")

    class _R:
        status_code = 200
        def json(self):
            return {"data": {"basicdata": {
                "Efficiency": 1200, "One-Bedroom": 1300,
                "Two-Bedroom": 1500, "Three-Bedroom": 1900,
                "Four-Bedroom": 2300}}}

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: _R())
    monkeypatch.setenv("HUD_API_TOKEN", "tok")
    n = pdta.pull_hud_fmr(db)
    # Every mapped county, not just Hampton Roads (2026-08-13): the
    # backbone is 50-metro, and an HR-only pull left the expansion
    # metros with no FMR row to blend from.
    assert n == len(set(pdta.CITY_TO_COUNTY_FIPS_5.values()))
    year = dt.date.today().year
    with sqlite3.connect(db) as conn:
        live = conn.execute(
            "SELECT fmr_two_bedroom, metro_name FROM hud_fmr "
            "WHERE fips_county_5 = '51710' AND year = ?", (year,)).fetchone()
        seeded = conn.execute(
            "SELECT fmr_two_bedroom FROM hud_fmr "
            "WHERE year = 2024").fetchone()
        stamp = conn.execute(
            "SELECT description FROM etl_metadata "
            "WHERE table_name = 'hud_fmr'").fetchone()
    assert live == (1500.0, None)     # extra column simply stays NULL
    assert seeded == (1300.0,)        # prior-year seed untouched
    assert "in-workbench" in stamp[0]  # next cycle skips as pulled-live


def test_hud_fmr_pulls_when_a_mapped_county_is_missing(tmp_path, monkeypatch):
    """Freshness must not mask incomplete coverage: a table pulled when the
    map held only the HR counties stays 'fresh' for 90 days while every
    expansion metro has no FMR row - which is exactly how rent coverage sat
    frozen at 9.2% of the backbone (2026-08-13)."""
    import sqlite3
    from core import public_data as pdta
    db = tmp_path / "etl.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE hud_fmr (
            fips_county_5 TEXT, year INTEGER, fmr_efficiency REAL,
            fmr_one_bedroom REAL, fmr_two_bedroom REAL,
            fmr_three_bedroom REAL, fmr_four_bedroom REAL)""")
        for f in pdta.HR_CITY_TO_COUNTY_FIPS_5.values():   # HR only - stale scope
            conn.execute("INSERT INTO hud_fmr VALUES (?,?,?,?,?,?,?)",
                         (f, 2026, 1000, 1100, 1300, 1700, 2100))
        pdta._stamp(conn, "hud_fmr", "HUD Fair Market Rents (in-workbench)",
                    "u", 7)

    class _R:
        status_code = 200

        def json(self):
            return {"data": {"basicdata": {
                "Efficiency": 1200, "One-Bedroom": 1300,
                "Two-Bedroom": 1500, "Three-Bedroom": 1900,
                "Four-Bedroom": 2300}, "year": 2026}}

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: _R())
    monkeypatch.setenv("HUD_API_TOKEN", "tok")
    n = pdta.pull_hud_fmr(db)
    assert n == len(set(pdta.CITY_TO_COUNTY_FIPS_5.values()))
    with sqlite3.connect(db) as conn:
        have = {r[0] for r in conn.execute(
            "SELECT DISTINCT fips_county_5 FROM hud_fmr")}
    assert set(pdta.CITY_TO_COUNTY_FIPS_5.values()) <= have
