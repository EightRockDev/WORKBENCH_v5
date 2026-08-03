"""The 50-metro rollout registry and the Coverage page math (spec §15)."""

from __future__ import annotations

import sqlite3

from core import rollout


def _db(tmp_path, rows):
    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE properties_8r "
                     "(property_id TEXT, city TEXT, units INTEGER)")
        conn.executemany("INSERT INTO properties_8r VALUES (?,?,?)", rows)
    return db


def test_the_registry_holds_hampton_roads_plus_fifty_metros():
    assert len(rollout.ROLLOUT) == 57          # 7 home cities + 50 rollout
    states = {s for s, _m, _c in rollout.ROLLOUT}
    assert {"Virginia", "North Carolina", "South Carolina", "Texas",
            "Georgia", "Florida", "Pennsylvania"} <= states
    # Richmond is wave 1, i.e. the first metro after the seven home cities.
    assert rollout.ROLLOUT[7][1] == "Richmond"


def test_counts_come_from_the_backbone_at_ten_plus_doors(tmp_path):
    db = _db(tmp_path, [
        ("a", "Norfolk", 48), ("b", "Norfolk", 12), ("c", "Norfolk", 4),
        ("d", "Richmond", 200),
    ])
    rows = {r.metro: r for r in rollout.coverage(db)}
    assert rows["Norfolk"].records == 2          # the 4-unit row is below floor
    assert rows["Norfolk"].doors == 60
    assert rows["Richmond"].live and rows["Richmond"].doors == 200
    assert not rows["Charlotte"].live            # nothing pulled -> Coming soon


def test_a_multi_city_metro_aggregates_its_cities(tmp_path):
    db = _db(tmp_path, [("a", "Dallas", 100), ("b", "Fort Worth", 50)])
    rows = {r.metro: r for r in rollout.coverage(db)}
    assert rows["Dallas-Fort Worth"].doors == 150
    assert rows["Dallas-Fort Worth"].records == 2


def test_every_metro_renders_even_with_no_database(tmp_path):
    """The page promises the full 50-metro list with Coming soon marks -
    an empty or missing backbone must not shrink it."""
    rows = rollout.coverage(tmp_path / "missing.db")
    assert len(rows) == len(rollout.ROLLOUT)
    assert all(not r.live for r in rows)


def test_state_grouping_totals_and_keeps_deployment_order(tmp_path):
    db = _db(tmp_path, [("a", "Norfolk", 60), ("b", "Richmond", 40)])
    grouped = rollout.by_state(rollout.coverage(db))
    assert grouped[0][0] == "Virginia"           # home state leads
    va_state, va_doors, va_records, va_metros = grouped[0]
    assert va_doors == 100 and va_records == 2
    labels = [m.metro for m in va_metros]
    assert labels.index("Norfolk") < labels.index("Richmond") < \
        labels.index("Charlottesville")


def test_richmond_has_a_fips_and_rides_the_active_pull():
    """Wave 1 wiring: a city can only mint 8R ids if the FIPS map knows it,
    and its feeds only pull nightly if the market is active."""
    from core.market_data import CITY_TO_COUNTY_FIPS_5
    import etl_munidata

    assert CITY_TO_COUNTY_FIPS_5["Richmond"] == "51760"
    assert "Richmond" in etl_munidata.ACTIVE_MARKETS
    assert "Richmond" not in etl_munidata.HR_MARKETS


def test_feedspec_where_reaches_the_arcgis_puller():
    """A statewide layer without its locality filter would ingest 4M rows
    under one city's FIPS - the where clause must survive the FeedSpec
    round trip into the puller."""
    import etl_munidata as em

    feed = em.FeedSpec("Hampton", "VA", "Hampton", "assessor", "arcgis",
                       "https://example.test/FeatureServer/0",
                       where="UPPER(LOCALITY) = 'HAMPTON'")
    puller = em.puller_for(feed)
    assert puller.where == "UPPER(LOCALITY) = 'HAMPTON'"


def test_discovery_falls_back_to_vgin_when_a_city_has_no_roll():
    """Hampton's portal serves 700-row study extracts; Suffolk's serves
    nothing. The statewide VGIN layer, locality-filtered and geo-verified,
    is the fallback for exactly that case."""
    import scripts.discover_feeds as df

    vgin_url = df.VGIN_LAYER_CANDIDATES[0]

    def fake_fetch(url, params=None):
        params = params or {}
        if url == vgin_url:
            return {"fields": [{"name": "PARCELID"}, {"name": "LOCALITY"},
                               {"name": "PTM_ID"}]}
        if url == vgin_url + "/query" and params.get("returnCountOnly"):
            where = params.get("where", "")
            if "SUFFOLK" in where.upper():
                return {"count": 31000}
            return {"count": 0}
        if url == vgin_url + "/query":
            return {"features": [
                {"geometry": {"y": 36.73, "x": -76.58}}] * 5}
        return None

    spec = df.vgin_fallback("Suffolk", fake_fetch)
    assert spec is not None
    assert spec["where"] and "SUFFOLK" in spec["where"].upper()
    assert spec["record_count"] == 31000
    assert spec["market"] == "Suffolk"


def test_vgin_rejects_a_filter_whose_sample_lands_outside_the_city():
    """A wrong locality filter is worse than no feed - it files another
    city's parcels under this city's FIPS. The geo check must veto it."""
    import scripts.discover_feeds as df

    vgin_url = df.VGIN_LAYER_CANDIDATES[0]

    def fake_fetch(url, params=None):
        params = params or {}
        if url == vgin_url:
            return {"fields": [{"name": "PARCELID"}, {"name": "LOCALITY"}]}
        if url == vgin_url + "/query" and params.get("returnCountOnly"):
            return {"count": 31000}
        if url == vgin_url + "/query":
            # Sample sits in Richmond, not Suffolk.
            return {"features": [
                {"geometry": {"y": 37.54, "x": -77.46}}] * 5}
        return None

    assert df.vgin_fallback("Suffolk", fake_fetch) is None
