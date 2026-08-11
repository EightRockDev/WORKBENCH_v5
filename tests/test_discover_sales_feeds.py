"""Sales-source discovery (owner directive 2026-08-11 "research the methods
to pull data"): candidates are scored from CENTRAL catalog metadata only -
the gated city domains are never touched - and nothing is auto-activated.
All network is stubbed."""

from __future__ import annotations

import datetime as dt
import importlib
import json

NOW = dt.datetime(2026, 8, 11, 12, 0, 0)


def _mod():
    import scripts.discover_sales_feeds as m
    importlib.reload(m)
    return m


# ------------------------------------------------------------- scoring

def test_score_columns_price_date_parcel():
    m = _mod()
    score, hits = m.score_columns(
        ["Sale_Price", "Sales_Date", "GPIN", "Grantor"])
    assert score == 90
    assert hits == {"price": "Sale_Price", "date": "Sales_Date",
                    "parcel": "GPIN"}


def test_score_columns_no_evidence_is_zero():
    m = _mod()
    score, hits = m.score_columns(["Shape", "OBJECTID", "ZONING"])
    assert score == 0 and hits == {}


def test_size_bonus_rewards_citywide_and_punishes_extracts():
    m = _mod()
    assert m.size_bonus(594_000) == 10
    assert m.size_bonus(700) == 3
    assert m.size_bonus(374) == -10          # the "Ranked_081920" lesson
    assert m.size_bonus(None) == 0           # unknown never penalized


def test_freshness_bonus_handles_iso_and_garbage():
    m = _mod()
    assert m.freshness_bonus("2026-07-01T00:00:00Z", NOW) == 5
    assert m.freshness_bonus("2020-01-01T00:00:00Z", NOW) == -5
    assert m.freshness_bonus("not-a-date", NOW) == 0
    assert m.freshness_bonus(None, NOW) == 0


def test_locality_match_requires_name_somewhere():
    m = _mod()
    assert m.locality_match("Hampton", "Hampton Property Sales", "", "")
    assert m.locality_match("Norfolk", "", "data.norfolk.gov")
    assert not m.locality_match("Suffolk", "Chesapeake Sales", "cityofchesapeake.net")


# ------------------------------------------------------- catalog sweeps

def test_socrata_sweep_scores_gated_domain_without_touching_it():
    """Richmond's domain 403s direct reads - the CENTRAL catalog must still
    produce a scored candidate, and no request may hit richmondgov."""
    m = _mod()
    urls = []

    def fake_fetch(url, params=None):
        urls.append(url)
        return {"results": [{
            "resource": {"id": "abcd-1234", "name": "Property Transfers",
                         "description": "City of Richmond transfers",
                         "updatedAt": "2026-08-01T00:00:00.000Z",
                         "columns_field_name":
                             ["pin", "consideration", "transfer_date"]},
            "metadata": {"domain": "data.richmondgov.com"}}]}

    out = m.sweep_socrata("Richmond", NOW, fetch=fake_fetch)
    assert all(u == m.SOCRATA_CATALOG for u in urls)
    strong = [c for c in out if "error" not in c]
    assert strong and strong[0]["score"] == 95     # 40+30+20 + 5 fresh
    assert strong[0]["adapter"] == "socrata_stack"
    assert strong[0]["resource_id"] == "abcd-1234"


def test_hub_sweep_yields_arcgis_adapter_candidate_with_url():
    m = _mod()

    def fake_fetch(url, params=None):
        return {"data": [{"attributes": {
            "name": "Hampton Real Estate Sales",
            "orgTitle": "City of Hampton",
            "recordCount": 51_000, "modified": "2026-08-01T00:00:00Z",
            "fields": [{"name": "LRSN"}, {"name": "SalePrice"},
                       {"name": "SaleDate"}, {"name": "ParcelID"}],
            "url": "https://x/FeatureServer/0"}}]}

    out = m.sweep_arcgis_hub("Hampton", NOW, fetch=fake_fetch)
    best = out[0]
    assert best["adapter"] == "arcgis"
    assert best["url"].endswith("/FeatureServer/0")
    assert best["score"] == 105                    # 90 cols +10 size +5 fresh


def test_hub_sweep_drops_other_city_and_non_sales_titles():
    m = _mod()

    def fake_fetch(url, params=None):
        return {"data": [
            {"attributes": {"name": "Chesapeake Property Sales",
                            "orgTitle": "City of Chesapeake", "fields": []}},
            {"attributes": {"name": "Suffolk Zoning Districts",
                            "orgTitle": "City of Suffolk", "fields": []}},
        ]}

    out = m.sweep_arcgis_hub("Suffolk", NOW, fetch=fake_fetch)
    assert [c for c in out if "error" not in c] == []


def test_ckan_sweep_takes_tabular_resources_only():
    m = _mod()

    def fake_fetch(url, params=None):
        return {"result": {"results": [{
            "title": "Property Assessment and Sales - FY25",
            "organization": {"title": "City of Norfolk"},
            "metadata_modified": "2026-07-15T00:00:00",
            "resources": [
                {"format": "CSV", "url": "https://data.virginia.gov/x.csv",
                 "last_modified": "2026-07-15T00:00:00"},
                {"format": "HTML", "url": "https://data.virginia.gov/x"},
            ]}]}}

    out = m.sweep_ckan_va("Norfolk", NOW, fetch=fake_fetch)
    good = [c for c in out if "error" not in c]
    assert len(good) == 1
    assert good[0]["adapter"] == "csv_download"
    assert good[0]["url"].endswith(".csv")


def test_catalog_failure_is_one_report_line_not_a_crash():
    m = _mod()
    out = m.research_locality("Hampton", "VA", NOW,
                              fetch=lambda *a, **k: None)
    assert out and all("error" in c for c in out)


def test_research_dedupes_across_query_terms_and_ranks():
    m = _mod()

    def fake_fetch(url, params=None):
        if url == m.SOCRATA_CATALOG:
            return {"results": [{
                "resource": {"id": "aaaa-aaaa", "name": "Norfolk Sales",
                             "updatedAt": "2026-08-01T00:00:00Z",
                             "columns_field_name": ["gpin", "sale_price",
                                                    "sale_date"]},
                "metadata": {"domain": "data.norfolk.gov"}}]}
        return {"data": []}

    out = m.research_locality("Norfolk", "VA", NOW, fetch=fake_fetch)
    good = [c for c in out if "error" not in c]
    # 3 query terms x 1 dataset -> exactly one deduped candidate
    assert len(good) == 1 and good[0]["resource_id"] == "aaaa-aaaa"


# ------------------------------------------------------------- main gate

def test_main_writes_candidates_json_and_stamp(monkeypatch, tmp_path):
    m = _mod()
    monkeypatch.setattr(m, "OUT_JSON", tmp_path / "cands.json")
    monkeypatch.setattr(m, "_STAMP", tmp_path / "stamp")
    monkeypatch.setattr(m, "tracked_localities", lambda: [("Hampton", "VA")])
    monkeypatch.setattr(m, "research_locality",
                        lambda *a, **k: [{"method": "socrata-catalog",
                                          "title": "T", "score": 90}])
    assert m.main([]) == 0
    data = json.loads((tmp_path / "cands.json").read_text())
    assert data["localities"]["Hampton"][0]["score"] == 90
    assert (tmp_path / "stamp").exists()


def test_main_respects_weekly_gate(monkeypatch, tmp_path):
    m = _mod()
    monkeypatch.setattr(m, "OUT_JSON", tmp_path / "cands.json")
    monkeypatch.setattr(m, "_STAMP", tmp_path / "stamp")
    (tmp_path / "stamp").write_text(dt.datetime.now().isoformat())
    monkeypatch.delenv("ER_SALES_DISCOVERY_FORCE", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(m, "research_locality",
                        lambda *a, **k: called.__setitem__("n", 1) or [])
    assert m.main([]) == 0
    assert called["n"] == 0
    assert not (tmp_path / "cands.json").exists()
