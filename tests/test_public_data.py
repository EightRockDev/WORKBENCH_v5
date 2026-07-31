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
