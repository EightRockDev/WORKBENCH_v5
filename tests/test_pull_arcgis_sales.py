"""ArcGIS Hub Property-Sales puller: sizes before paginating, writes rows in
the shape core.sale_index already understands, and never deletes on a
transient empty pull."""

from __future__ import annotations

import importlib
import json
import sqlite3


def _mod():
    import scripts.pull_arcgis_sales as m
    importlib.reload(m)
    return m


def _mk_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE muni_records (market TEXT, state TEXT, "
                 "county TEXT, kind TEXT, source_url TEXT, pulled_at TEXT, "
                 "record TEXT)")
    return conn


def test_where_is_arms_length_and_dated():
    m = _mod()
    assert "Sale_Price > 0" in m.WHERE
    assert "Sales_Date >=" in m.WHERE


def test_sizes_then_paginates_and_writes(monkeypatch):
    m = _mod()
    url = m.ARCGIS_SALES_FEEDS["Virginia Beach"]["url"]

    calls = {"count": 0, "pages": 0}

    def fake_query(u, params):
        if params.get("returnCountOnly"):
            calls["count"] += 1
            return {"count": 3}
        calls["pages"] += 1
        off = params["resultOffset"]
        # 3 features total, PAGE forced to 2 so we exercise a second page.
        allf = [{"attributes": {"GPIN": f"{i}", "Sale_Price": 100 + i,
                                "Sales_Date": 1609459200000}} for i in range(3)]
        chunk = allf[off:off + m.PAGE]
        return {"features": chunk}

    monkeypatch.setattr(m, "PAGE", 2)
    monkeypatch.setattr(m, "_query", fake_query)
    conn = _mk_db()
    n = m.pull_market(conn, "Virginia Beach",
                      m.ARCGIS_SALES_FEEDS["Virginia Beach"])
    assert n == 3
    assert calls["count"] == 1              # sized exactly once, first
    rows = conn.execute("SELECT market,kind,record FROM muni_records").fetchall()
    assert len(rows) == 3
    assert all(r[1] == "sales" for r in rows)
    rec = json.loads(rows[0][2])
    assert "Sale_Price" in rec and "Sales_Date" in rec   # verbatim attributes


def test_transient_empty_does_not_delete_existing(monkeypatch):
    m = _mod()
    url = m.ARCGIS_SALES_FEEDS["Virginia Beach"]["url"]
    conn = _mk_db()
    # a prior good pull is on hand
    conn.execute("INSERT INTO muni_records VALUES ('Virginia Beach','VA',"
                 "'Virginia Beach','sales',?, '2000-01-01', '{\"GPIN\":\"x\"}')",
                 (url,))

    def fake_query(u, params):
        if params.get("returnCountOnly"):
            return {"count": 500}          # server says there ARE rows
        return {"features": []}            # but pagination returns nothing

    monkeypatch.setattr(m, "_query", fake_query)
    n = m.pull_market(conn, "Virginia Beach",
                      m.ARCGIS_SALES_FEEDS["Virginia Beach"])
    assert n == 0
    # existing row survives - a transient blip must not wipe good data
    assert conn.execute("SELECT count(*) FROM muni_records").fetchone()[0] == 1


def test_count_failure_skips_cleanly(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "_query", lambda u, p: None)
    conn = _mk_db()
    assert m.pull_market(conn, "Virginia Beach",
                         m.ARCGIS_SALES_FEEDS["Virginia Beach"]) == 0
