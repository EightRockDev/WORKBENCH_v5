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


def test_a_subset_named_layer_cannot_become_the_city_roll():
    """Richmond's Undeveloped_Parcels layer carried 6,570 records - past the
    size gate - and became the city roll; every parcel in it is vacant land.
    A subset BY NAME must demote and must not suppress the VGIN fallback."""
    import scripts.discover_feeds as df

    assert df.subset_named("Undeveloped_Parcels_Richmond_Virginia",
                           "https://x/FeatureServer/0")
    assert df.subset_named("CZM_Hampton_Data", "https://x")
    assert not df.subset_named("Parcels_Real_Estate_View", "https://x")
    assert not df.subset_named("TaxParcels_public", "https://x")


def test_vgin_prefers_fips_over_ambiguous_name_fields():
    """Virginia has a Richmond CITY and a Richmond COUNTY. When the layer
    exposes a FIPS field, the filter must use it before any name match."""
    import scripts.discover_feeds as df

    assert df.VGIN_LOCALITY_FIELDS[0] == "FIPS"
    wheres = df.vgin_where_candidates("FIPS", "Richmond")
    assert any("51760" in w for w in wheres)
    # Name-field fallback tries exact and 'RICHMOND CITY' before prefix LIKE.
    name_wheres = df.vgin_where_candidates("LOCALITY", "Richmond")
    like = [i for i, w in enumerate(name_wheres) if "LIKE" in w]
    city_exact = [i for i, w in enumerate(name_wheres)
                  if "RICHMOND CITY" in w and "LIKE" not in w]
    assert city_exact and like and city_exact[0] < like[0]


def test_a_coordinate_less_roll_gets_the_statewide_geometry_supplement():
    """Portsmouth: 36K-parcel roll, zero coordinates -> crosswalk matching
    caps at address-only and the use-code learner starves at 7 anchors. The
    statewide layer's geometry merges onto the same APNs."""
    import scripts.discover_feeds as df

    roll_url = "https://city.test/arcgis/rest/services"
    layer = roll_url + "/Parcels/FeatureServer/3"
    vgin_url = df.VGIN_LAYER_CANDIDATES[0]

    def fake_fetch(url, params=None):
        params = params or {}
        if url == roll_url:
            return {"services": [{"name": "Parcels", "type": "FeatureServer"}]}
        if url == roll_url + "/Parcels/FeatureServer":
            return {"layers": [{"id": 3, "name": "Parcels"}]}
        if url == layer:
            return {"fields": [{"name": "PARID"}, {"name": "LANDUSE"},
                               {"name": "OWNER"}, {"name": "ACREAGE"}]}
        if url == layer + "/query" and params.get("returnCountOnly"):
            return {"count": 36464}
        if url == layer + "/query":
            return {"features": []}         # no geometry - the point
        if url == vgin_url:
            return {"fields": [{"name": "PARCELID"}, {"name": "LOCALITY"}]}
        if url == vgin_url + "/query" and params.get("returnCountOnly"):
            where = params.get("where", "")
            return {"count": 36000 if "PORTSMOUTH" in where.upper() else 0}
        if url == vgin_url + "/query":
            return {"features": [{"geometry": {"y": 36.83, "x": -76.35}}] * 5}
        return None

    found = df.discover(cities=("Portsmouth",), extra_roots=(roll_url,),
                        fetch=fake_fetch, soda=lambda *a, **k: None)
    specs = found["Portsmouth"]
    assert any("geometry supplement" in (s.get("note") or "") for s in specs), (
        [s.get("note") for s in specs])
    supp = next(s for s in specs if "geometry supplement" in s["note"])
    assert "PORTSMOUTH" in supp["where"].upper()


# --- honest labeling for covered-but-unconfirmable metros (owner 2026-08-05) ---
# Hampton (2 confirmed vs ~52K parcels) etc. must read "feed incomplete", not a
# tiny number as if it were the whole market, nor a false "Coming soon".

def test_a_big_roll_with_almost_no_confirmed_mf_is_feed_incomplete(tmp_path):
    rows = [(f"h{i}", "Hampton", 1) for i in range(3000)]      # sub-floor roll
    rows += [("h-a", "Hampton", 20), ("h-b", "Hampton", 15)]   # 2 confirmed MF
    m = {r.metro: r for r in rollout.coverage(_db(tmp_path, rows))}["Hampton"]
    assert m.records == 2 and m.parcels >= 3000
    assert m.feed_incomplete and not m.confident


def test_a_real_market_count_is_confident(tmp_path):
    rows = [(f"n{i}", "Norfolk", 40) for i in range(30)]       # 30 confirmed MF
    m = {r.metro: r for r in rollout.coverage(_db(tmp_path, rows))}["Norfolk"]
    assert m.records == 30 and m.confident and not m.feed_incomplete


def test_parcels_but_zero_confirmed_is_incomplete_not_coming_soon(tmp_path):
    rows = [(f"p{i}", "Portsmouth", 2) for i in range(4000)]   # all sub-floor
    m = {r.metro: r for r in rollout.coverage(_db(tmp_path, rows))}["Portsmouth"]
    assert m.records == 0 and m.parcels >= 4000
    assert not m.live and m.feed_incomplete and not m.confident


def test_no_parcels_at_all_stays_coming_soon(tmp_path):
    m = {r.metro: r for r in rollout.coverage(_db(tmp_path, [("a", "Norfolk", 40)]))}
    charlotte = m["Charlotte"]
    assert charlotte.parcels == 0 and not charlotte.feed_incomplete
    assert not charlotte.confident        # renders "Coming soon"


def test_coverage_uses_parcel_index_when_present(tmp_path):
    # After a prune, the full roll lives in parcel_index; parcels must count it.
    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE properties_8r (property_id TEXT, city TEXT, units INTEGER)")
        conn.executemany("INSERT INTO properties_8r VALUES (?,?,?)",
                         [("a", "Suffolk", 20)])          # 1 confirmed kept
        conn.execute("CREATE TABLE parcel_index (fips TEXT, apn TEXT, address TEXT, city TEXT, units INTEGER, use_code TEXT)")
        conn.executemany("INSERT INTO parcel_index (city, units) VALUES (?,?)",
                         [("Suffolk", None)] * 4000)      # full roll, units NULL
    m = {r.metro: r for r in rollout.coverage(db)}["Suffolk"]
    assert m.records == 1 and m.parcels >= 4000 and m.feed_incomplete
