"""Generalized Spatialest sales puller — slug generation, runtime-verified
discovery (no blind pulls), and skip-when-not-Spatialest."""

from __future__ import annotations

import importlib


def test_slug_variants_cover_common_forms():
    import scripts.pull_sales as ps
    importlib.reload(ps)
    v = ps.slug_variants("Virginia Beach")
    assert "virginiabeach" in v and "virginia-beach" in v


def test_has_sale_detects_real_sale_and_rejects_noise():
    import scripts.pull_sales as ps
    importlib.reload(ps)
    assert ps._has_sale({"TOTSALPRICE": 795000, "SALE_DATE": "2021-07-15"})
    assert ps._has_sale({"sales": [{"saleprice": 100, "saledate": "2020-01-01"}]})
    assert not ps._has_sale({"owner": "SPADA LLC", "units": 96})
    assert not ps._has_sale([])


def test_discover_keeps_only_sale_bearing_combo(monkeypatch):
    import scripts.pull_sales as ps
    importlib.reload(ps)
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)

    def fake_get(url):
        # right slug 'virginiabeach', sales only under 'deeds'
        if "/va/virginiabeach/deeds/" in url:
            return 200, {"saleprice": 12300000, "saledate": "2021-07-15"}
        if "/va/virginiabeach/" in url:
            return 200, {"nothing": "here"}       # reachable, not sales
        return 404, None

    monkeypatch.setattr(ps, "_get", fake_get)
    ep = ps.discover("Virginia Beach", "VA", ["14552807310000"])
    assert ep == {"base": "https://api.spatialest.com/v1/va/virginiabeach",
                  "resource": "deeds"}


def test_sample_gpins_handles_numeric_apn(monkeypatch):
    """Some rolls store APN/GPIN as a bare int; `int or ""` stays an int and
    `.strip()` used to crash the whole pull mid-locality (autopilot 2026-08-10)."""
    import sqlite3
    import json
    import scripts.pull_sales as ps
    importlib.reload(ps)
    # normalize_record echoes an integer apn straight through.
    monkeypatch.setattr(ps.phase0, "normalize_record",
                        lambda m, s, raw: {"apn": raw.get("gpin")})
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE muni_records (market TEXT, state TEXT, "
                 "kind TEXT, record TEXT)")
    conn.execute("INSERT INTO muni_records VALUES (?,?,?,?)",
                 ("Norfolk", "VA", "assessor", json.dumps({"gpin": 14552807310000})))
    out = ps.sample_gpins(conn, "Norfolk", "VA", 5)
    assert out == ["14552807310000"]


def test_discover_returns_none_when_not_on_spatialest(monkeypatch):
    import scripts.pull_sales as ps
    importlib.reload(ps)
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)
    monkeypatch.setattr(ps, "_get", lambda url: (404, None))
    assert ps.discover("Nowheresville", "TX", ["123"]) is None
