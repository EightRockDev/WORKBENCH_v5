"""Municipal sales pullers (three adapter types): size-before-paginate,
verbatim rows core.sale_index already understands, dedupe semantics, and
never deleting good data on a transient empty pull."""

from __future__ import annotations

import importlib
import io
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


# ------------------------------------------------------------ VB / esri

def test_where_is_arms_length_and_dated():
    m = _mod()
    assert "Sale_Price > 0" in m.WHERE
    assert "Sales_Date >=" in m.WHERE


def test_vb_sizes_then_paginates_and_writes(monkeypatch):
    m = _mod()
    calls = {"count": 0}

    def fake_query(u, params):
        if params.get("returnCountOnly"):
            calls["count"] += 1
            return {"count": 3}
        off = params["resultOffset"]
        allf = [{"attributes": {"GPIN": f"{i}", "Sale_Price": 100 + i,
                                "Sales_Date": 1609459200000}} for i in range(3)]
        return {"features": allf[off:off + m.PAGE]}

    monkeypatch.setattr(m, "PAGE", 2)
    monkeypatch.setattr(m, "_query", fake_query)
    conn = _mk_db()
    n = m.pull_market(conn, "Virginia Beach", m.SALES_SOURCES["Virginia Beach"])
    assert n == 3
    assert calls["count"] == 1              # sized exactly once, first
    rows = conn.execute("SELECT kind, record FROM muni_records").fetchall()
    assert len(rows) == 3 and all(r[0] == "sales" for r in rows)
    rec = json.loads(rows[0][1])
    assert "Sale_Price" in rec and "Sales_Date" in rec   # verbatim


def test_vb_transient_empty_does_not_delete_existing(monkeypatch):
    m = _mod()
    url = m.SALES_SOURCES["Virginia Beach"]["url"]
    conn = _mk_db()
    conn.execute("INSERT INTO muni_records VALUES ('Virginia Beach','VA',"
                 "'Virginia Beach','sales',?, '2000-01-01', '{}')", (url,))

    def fake_query(u, params):
        if params.get("returnCountOnly"):
            return {"count": 500}
        return {"features": []}

    monkeypatch.setattr(m, "_query", fake_query)
    assert m.pull_market(conn, "Virginia Beach",
                         m.SALES_SOURCES["Virginia Beach"]) == 0
    assert conn.execute("SELECT count(*) FROM muni_records").fetchone()[0] == 1


def test_vb_count_failure_skips_cleanly(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m, "_query", lambda u, p: None)
    conn = _mk_db()
    assert m.pull_market(conn, "Virginia Beach",
                         m.SALES_SOURCES["Virginia Beach"]) == 0


# ---------------------------------------------------- Norfolk / socrata

def test_norfolk_stack_dedupes_latest_fy_wins(monkeypatch):
    m = _mod()
    cfg = dict(m.SALES_SOURCES["Norfolk"])
    cfg["resources"] = (("FY26", "aaaa-aaaa"), ("FY27", "bbbb-bbbb"))

    data = {
        "aaaa-aaaa": [
            {"gpin": "G1", "transfer_date": "2020-05-01T00:00:00",
             "consideration": "100000", "grantee": "OLD LLC"},
            {"gpin": "G1", "transfer_date": "2015-01-01T00:00:00",
             "consideration": "50000"},
            {"gpin": "G2", "consideration": "1"},          # no date -> skip
        ],
        "bbbb-bbbb": [
            # same (gpin, date) as FY26 but fresher snapshot -> must win
            {"gpin": "G1", "transfer_date": "2020-05-01T00:00:00",
             "consideration": "100000", "grantee": "NEW LLC"},
            {"gpin": "G3", "transfer_date": "2026-08-01T00:00:00",
             "consideration": "2500000",
             "location": {"latitude": "36.8"}},            # dict stripped
        ],
    }

    def fake_get_json(url, params=None):
        rid = url.rsplit("/", 1)[-1].replace(".json", "")
        if params and "$select" in params:
            return [{"count": str(len(data[rid]))}]
        if params and params.get("$offset", 0) >= len(data[rid]):
            return []
        return data[rid]

    monkeypatch.setattr(m, "_get_json", fake_get_json)
    conn = _mk_db()
    n = m.pull_market(conn, "Norfolk", cfg)
    assert n == 3                       # G1@2020, G1@2015, G3@2026
    recs = [json.loads(r[0]) for r in conn.execute(
        "SELECT record FROM muni_records").fetchall()]
    g1_2020 = next(r for r in recs
                   if r.get("gpin") == "G1" and "2020" in r["transfer_date"])
    assert g1_2020["grantee"] == "NEW LLC"           # later FY won
    assert g1_2020["_fy_resource"].startswith("FY27")
    assert all("location" not in r for r in recs)    # geometry stripped


def test_stack_id_and_date_key_flexibility(monkeypatch):
    # Richmond's Socrata source is retired (rva.gov files won), but the
    # stack adapter's id/date-key probing must keep working for any future
    # socrata_stack promotion out of discover_sales_feeds candidates.
    m = _mod()
    cfg = {
        "type": "socrata_stack",
        "base": "https://example.socrata.test/resource/",
        "resources": (("history", "cccc-cccc"),),
        "state": "VA", "county": "Richmond",
        "source_tag": "socrata-stack:test/property-transfers",
        "refresh_d": 7,
    }
    rows = [{"pin": "W0001", "transfer_date": "2024-03-01T00:00:00",
             "consideration_amount": "750000"}]

    def fake_get_json(url, params=None):
        if params and "$select" in params:
            return [{"count": "1"}]
        if params and params.get("$offset", 0) >= 1:
            return []
        return rows

    monkeypatch.setattr(m, "_get_json", fake_get_json)
    conn = _mk_db()
    assert m.pull_market(conn, "Richmond", cfg) == 1


def test_chicago_passes_soql_where_and_pulls(monkeypatch):
    m = _mod()
    cfg = m.SALES_SOURCES["Chicago"]
    seen = {"where": None}

    def fake_get_json(url, params=None):
        if params and "$where" in params:
            seen["where"] = params["$where"]
        if params and "$select" in params:
            return [{"count": "1"}]
        if params and params.get("$offset", 0) >= 1:
            return []
        return [{"pin": "14-05-403-021-0000",
                 "sale_date": "2024-09-12T00:00:00",
                 "sale_price": "1250000"}]

    monkeypatch.setattr(m, "_get_json", fake_get_json)
    conn = _mk_db()
    assert m.pull_market(conn, "Chicago", cfg) == 1
    assert "sale_price > 0" in seen["where"]


def test_stack_all_counts_failing_touches_nothing(monkeypatch):
    m = _mod()
    cfg = dict(m.SALES_SOURCES["Norfolk"])
    monkeypatch.setattr(m, "_get_json", lambda u, p=None: None)
    conn = _mk_db()
    conn.execute("INSERT INTO muni_records VALUES ('Norfolk','VA','Norfolk',"
                 "'sales',?, '2000-01-01', '{}')", (cfg["source_tag"],))
    assert m.pull_market(conn, "Norfolk", cfg) == 0
    assert conn.execute("SELECT count(*) FROM muni_records").fetchone()[0] == 1


# ------------------------------------------------- Chesapeake / landbook

def test_landbook_keeps_date_bearing_rows_and_cleans_timestamps(monkeypatch):
    import pandas as pd
    m = _mod()
    df = pd.DataFrame([
        {"MAP_PARCEL": "0123000", "CONSIDERATION": 1_200_000,
         "TRANSFER DATE": pd.Timestamp("2025-11-03"),
         "CURRENTOWNER": "MF HOLDINGS LLC", "TOTALVALUE": 950_000},
        {"MAP_PARCEL": "0456000", "CONSIDERATION": None,
         "TRANSFER DATE": pd.NaT, "TOTALVALUE": 300_000},   # never sold
    ])
    monkeypatch.setattr(m, "_landbook_frames",
                        lambda cfg: iter([("commercial", df)]))
    conn = _mk_db()
    n = m.pull_market(conn, "Chesapeake", m.SALES_SOURCES["Chesapeake"])
    assert n == 1
    rec = json.loads(conn.execute(
        "SELECT record FROM muni_records").fetchone()[0])
    assert rec["TRANSFER DATE"] == "2025-11-03"      # Timestamp -> ISO
    assert rec["_landbook"] == "commercial"


def test_landbook_empty_parse_touches_nothing(monkeypatch):
    m = _mod()
    cfg = m.SALES_SOURCES["Chesapeake"]
    monkeypatch.setattr(m, "_landbook_frames", lambda c: iter([]))
    conn = _mk_db()
    conn.execute("INSERT INTO muni_records VALUES ('Chesapeake','VA',"
                 "'Chesapeake','sales',?, '2000-01-01', '{}')",
                 (cfg["source_tag"],))
    assert m.pull_market(conn, "Chesapeake", cfg) == 0
    assert conn.execute("SELECT count(*) FROM muni_records").fetchone()[0] == 1


# --------------------------- assessor layers via the Esri REST adapter
#
# These used to read SALES_SOURCES["Richmond-parcels"], but that feed was
# Richmond CALIFORNIA and got quarantined (V5.66.1.0.0). The mechanics the
# tests pin - market/kind override, replace-not-append, stale-generation
# sweep - are adapter behavior any future assessor layer rides, so they
# now run against a synthetic cfg.

def _assessor_cfg() -> dict:
    return {
        "type": "arcgis",
        "url": ("https://example.invalid/arcgis/rest/services/"
                "Assessor_Roll/FeatureServer/0"),
        "state": "VA", "county": "Testville",
        "market": "Richmond", "kind": "assessor", "where": "1=1",
        "refresh_d": 7,
    }


def test_arcgis_kind_and_market_override(monkeypatch):
    """An assessor layer rides the arcgis adapter with kind='assessor' and
    market from cfg (not the dict key) so phase0 sees an assessor roll."""
    m = _mod()
    cfg = _assessor_cfg()
    monkeypatch.setattr(m, "count_sales", lambda url, where="": 2)
    monkeypatch.setattr(
        m, "iter_features",
        lambda url, total, where="": iter(
            [{"PIN": "W0001", "LandUse": "Multi-Family", "TotalValue": 1.0},
             {"PIN": "W0002", "LandUse": "Single Family", "TotalValue": 2.0}]))
    conn = _mk_db()
    assert m.pull_market(conn, "Assessor-roll", cfg) == 2
    rows = conn.execute(
        "SELECT DISTINCT market, kind FROM muni_records").fetchall()
    assert rows == [("Richmond", "assessor")]


def test_arcgis_assessor_repull_replaces_and_then_skips(monkeypatch):
    """2026-08-11 4:20 cycle: the COR roll doubled (32,907 -> 65,814) and
    re-downloaded every cycle because both the freshness stamp and the
    delete-before-insert filtered kind='sales' while this feed writes
    kind='assessor'. A repeat pull must REPLACE, and once stamped the next
    cycle must skip."""
    m = _mod()
    cfg = _assessor_cfg()
    monkeypatch.setattr(m, "count_sales", lambda url, where="": 2)
    monkeypatch.setattr(
        m, "iter_features",
        lambda url, total, where="": iter(
            [{"PIN": "W0001"}, {"PIN": "W0002"}]))
    conn = _mk_db()
    m._ADAPTERS["arcgis"](conn, "Assessor-roll", cfg)  # bypass freshness:
    m._ADAPTERS["arcgis"](conn, "Assessor-roll", cfg)  # two forced pulls
    n = conn.execute("SELECT count(*) FROM muni_records").fetchone()[0]
    assert n == 2, "second pull appended instead of replacing"
    assert m.pull_market(conn, "Assessor-roll", cfg) == 0   # fresh - skip
    assert conn.execute(
        "SELECT count(*) FROM muni_records").fetchone()[0] == 2


def test_stale_generation_sweep_drains_prior_duplicates():
    """Duplicate rows a prior buggy cycle left behind (older pulled_at, same
    source and kind) must drain on the next cycle even when the source is
    fresh enough to skip - otherwise the doubled COR roll would sit for the
    whole refresh window."""
    import datetime as dt
    m = _mod()
    cfg = _assessor_cfg()
    conn = _mk_db()
    now = dt.datetime.now().isoformat(timespec="seconds")
    for stamp, pin in (("2026-08-10T04:20:00", "W0001"),
                       ("2026-08-10T04:20:00", "W0002"),
                       (now, "W0001"), (now, "W0002")):
        conn.execute(
            "INSERT INTO muni_records (market,state,county,kind,source_url,"
            "pulled_at,record) VALUES ('Richmond','VA','Richmond','assessor',"
            "?,?,?)", (cfg["url"], stamp, json.dumps({"PIN": pin})))
    assert m.pull_market(conn, "Assessor-roll", cfg) == 0   # fresh - skip
    rows = conn.execute("SELECT pulled_at FROM muni_records").fetchall()
    assert len(rows) == 2 and all(r[0] == now for r in rows)


# -------------------------------------------- Richmond / rva.gov files

def test_html_files_scrapes_classifies_and_writes(monkeypatch):
    import pandas as pd
    m = _mod()
    cfg = m.SALES_SOURCES["Richmond-files"]

    html = ('<a href="/sites/default/files/PublicDataSet_Parcels_0726.xlsx">'
            'parcels</a>'
            '<a href="/files/Transfers_July2026.xlsx">transfers</a>'
            '<a href="/files/notes.pdf">ignore</a>')

    class R:
        status_code = 200
        text = html
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: R())

    frames = {
        "PublicDataSet_Parcels_0726.xlsx": pd.DataFrame(
            [{"PIN": "W0001", "PropertyClass": "R41 Apartment",
              "TotalValue": 2_500_000}]),
        "Transfers_July2026.xlsx": pd.DataFrame(
            [{"PIN": "W0001", "TransferDate": pd.Timestamp("2026-05-04"),
              "Consideration": 1_800_000, "Qualified": "Q"}]),
    }
    monkeypatch.setattr(
        m, "_download_tables",
        lambda url: ([(frames[url.rsplit("/", 1)[-1]], url)]
                     if url.rsplit("/", 1)[-1] in frames else []))
    conn = _mk_db()
    n = m.pull_market(conn, "Richmond-files", cfg)
    assert n == 2
    rows = conn.execute(
        "SELECT market, kind, record FROM muni_records").fetchall()
    kinds = sorted(r[1] for r in rows)
    assert kinds == ["assessor", "sales"]
    assert all(r[0] == "Richmond" for r in rows)
    sale = json.loads(next(r[2] for r in rows if r[1] == "sales"))
    assert sale["TransferDate"] == "2026-05-04"      # Timestamp -> ISO
    assert sale["_file"].endswith("Transfers_July2026.xlsx")


def test_html_files_media_links_classified_by_anchor_text(monkeypatch):
    """rva.gov links files as extension-less /media/<id> (first-contact
    failure, midnight cycle 2026-08-11: HTTP 200 but 0 links matched). The
    anchor TEXT is the only classification signal on those."""
    import pandas as pd
    m = _mod()
    links = m._list_file_links(
        '<a href="/media/50901"><span>Property Transfers - July 2026'
        '</span></a> <a href="/media/50902">Public Data Set 2026</a>'
        ' <a href="/about">not a file</a>',
        "https://www.rva.gov/assessor-real-estate/data-request")
    assert links == [
        ("https://www.rva.gov/media/50901", "Property Transfers - July 2026"),
        ("https://www.rva.gov/media/50902", "Public Data Set 2026")]
    assert m._file_kind(*[links[0][0], links[0][1]]) == "sales"
    assert m._file_kind(links[1][0], links[1][1]) == "assessor"

    # /media/<id> serves an HTML LANDING PAGE (2 AM ET first contact:
    # download/parse FAILED on both files) - _download_tables must follow it
    # one level to the real file, then sniff xlsx from magic bytes. The
    # resolved filename ("...Transfers...") is what classifies the file.
    buf = io.BytesIO()
    pd.DataFrame([{"PIN": "W1", "Consideration": 5}]).to_excel(
        buf, index=False)
    landing = ('<html><body><a href="/sites/default/files/2026-07/'
               'Assessor_Transfers_2015-2025.xlsx">Download</a>'
               '</body></html>')

    def fake_get(url, **kw):
        class R:
            status_code = 200
        R.url = url
        if "/media/" in url:
            R.content = landing.encode()
        else:
            R.content = buf.getvalue()
        return R()
    monkeypatch.setattr(m.requests, "get", fake_get)
    tables = m._download_tables("https://www.rva.gov/media/50901")
    assert len(tables) == 1
    df, final = tables[0]
    assert df is not None and df.iloc[0]["PIN"] == "W1"
    assert final.endswith("Assessor_Transfers_2015-2025.xlsx")
    assert m._file_kind(final, "2015-2025") == "sales"


def test_html_files_landing_page_yields_every_file(monkeypatch):
    """Richmond's 'Public Data Set' anchor lands on a page listing THREE
    workbooks (parcels, land, building characteristics). The first version
    of the follower returned only the FIRST file that parsed - so the
    building-characteristics file, the only one carrying unit counts,
    was never ingested and Richmond had zero units (2026-09-03). Every
    file behind a landing page must land, and two anchors resolving to
    the same file must not double-ingest it."""
    import pandas as pd
    m = _mod()
    cfg = m.SALES_SOURCES["Richmond-files"]

    page = ('<a href="/media/50902">Public Data Set 2026</a>'
            '<a href="/sites/default/files/Parcels_0826.xlsx">parcels</a>')
    landing = ('<html><body>'
               '<a href="/sites/default/files/Parcels_0826.xlsx">1</a>'
               '<a href="/sites/default/files/Land_0826.xlsx">2</a>'
               '<a href="/sites/default/files/Buildings_0826.xlsx">3</a>'
               '</body></html>')
    bufs = {}
    for fname, rec in (
            ("Parcels_0826.xlsx", {"PIN": "W0001", "TotalValue": 1}),
            ("Land_0826.xlsx", {"PIN": "W0001", "LandValue": 2}),
            ("Buildings_0826.xlsx", {"PIN": "W0001", "NumberOfUnits": 48})):
        b = io.BytesIO()
        pd.DataFrame([rec]).to_excel(b, index=False)
        bufs[fname] = b.getvalue()

    def fake_get(url, **kw):
        class R:
            status_code = 200
        R.url = url
        R.text = page
        if "/media/" in url:
            R.content = landing.encode()
        else:
            R.content = bufs.get(url.rsplit("/", 1)[-1], page.encode())
        return R()
    monkeypatch.setattr(m.requests, "get", fake_get)
    conn = _mk_db()
    n = m.pull_market(conn, "Richmond-files", cfg)
    assert n == 3, "every landing-page file must be ingested exactly once"
    recs = [json.loads(r[0]) for r in
            conn.execute("SELECT record FROM muni_records").fetchall()]
    files = sorted(r["_file"].rsplit("/", 1)[-1] for r in recs)
    assert files == ["Buildings_0826.xlsx", "Land_0826.xlsx",
                     "Parcels_0826.xlsx"]
    assert any(r.get("NumberOfUnits") == 48 for r in recs), \
        "the unit-bearing building file must survive ingestion"


def test_html_files_no_links_touches_nothing(monkeypatch):
    m = _mod()
    cfg = m.SALES_SOURCES["Richmond-files"]

    class R:
        status_code = 403
        text = ""
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: R())
    conn = _mk_db()
    conn.execute("INSERT INTO muni_records VALUES ('Richmond','VA','Richmond',"
                 "'sales',?, '2000-01-01', '{}')", (cfg["source_tag"],))
    assert m.pull_market(conn, "Richmond-files", cfg) == 0
    assert conn.execute("SELECT count(*) FROM muni_records").fetchone()[0] == 1


# ------------------------------------- rows are readable by sale_history

def test_extractor_reads_all_three_jurisdictions_rows():
    from core.sale_history import extract_sale_records
    norfolk = extract_sale_records(
        {"gpin": "G1", "consideration": "875000",
         "transfer_date": "2024-06-15T00:00:00", "grantee": "BUYER LLC"})
    assert norfolk and norfolk[0]["price"] == 875000.0
    assert norfolk[0]["date"] == "2024-06-15"
    assert norfolk[0]["grantee"] == "BUYER LLC"

    ches_landbook = extract_sale_records(
        {"MAP_PARCEL": "0123000", "CONSIDERATION": 1200000,
         "TRANSFER DATE": "2025-11-03", "CURRENTOWNER": "MF HOLDINGS LLC",
         "DEEDBK": "9012", "DEEDPG": "345"})
    assert ches_landbook and ches_landbook[0]["price"] == 1200000.0
    assert ches_landbook[0]["grantee"] == "MF HOLDINGS LLC"
    assert "9012" in ches_landbook[0]["notes"]

    ches_parcel = extract_sale_records(
        {"PARNO": "0123000", "TRANSFER": "2025-11-03",
         "DEEDBK": "9012", "DEEDPG": "345"})
    assert ches_parcel and ches_parcel[0]["date"] == "2025-11-03"

    richmond = extract_sale_records(
        {"pin": "W0001", "consideration_amount": "750000",
         "transfer_date": "2024-03-01T00:00:00"})
    assert richmond and richmond[0]["price"] == 750000.0


# ------------------------------------------- csv_download (state mirrors)

def _csv_cfg(**kw):
    base = dict(type="csv_download",
                files=(("FY24", "https://data.virginia.gov/a.csv"),
                       ("FY25", "https://data.virginia.gov/b.csv")),
                market="Hampton", state="VA", county="Hampton",
                source_tag="ckan:data.virginia.gov/hampton-sales",
                refresh_d=7)
    base.update(kw)
    return base


class _Resp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status


def test_csv_download_parses_sizes_and_writes(monkeypatch):
    m = _mod()
    csv_by_url = {
        "https://data.virginia.gov/a.csv":
            b"GPIN,TRANSFER_DATE,CONSIDERATION\nG1,2024-01-02,100000\n",
        "https://data.virginia.gov/b.csv":
            b"GPIN,TRANSFER_DATE,CONSIDERATION\nG2,2025-03-04,250000\n",
    }
    monkeypatch.setattr(m.requests, "get",
                        lambda url, **k: _Resp(csv_by_url[url]))
    conn = _mk_db()
    n = m.pull_market(conn, "Hampton-state-mirror", _csv_cfg())
    assert n == 2
    rows = conn.execute("SELECT market, kind, record FROM muni_records").fetchall()
    assert all(r[0] == "Hampton" and r[1] == "sales" for r in rows)
    recs = [json.loads(r[2]) for r in rows]
    assert {r["GPIN"] for r in recs} == {"G1", "G2"}
    assert {r["_file"] for r in recs} == {"FY24", "FY25"}


def test_csv_download_dedupes_later_file_wins(monkeypatch):
    m = _mod()
    csv_by_url = {
        "https://data.virginia.gov/a.csv":
            b"GPIN,TRANSFER_DATE,OWNER\nG1,2024-01-02,OLD LLC\n",
        "https://data.virginia.gov/b.csv":
            b"GPIN,TRANSFER_DATE,OWNER\nG1,2024-01-02,NEW LLC\n",
    }
    monkeypatch.setattr(m.requests, "get",
                        lambda url, **k: _Resp(csv_by_url[url]))
    conn = _mk_db()
    n = m.pull_market(conn, "Hampton-state-mirror", _csv_cfg(dedupe=True))
    assert n == 1
    rec = json.loads(conn.execute("SELECT record FROM muni_records").fetchone()[0])
    assert rec["OWNER"] == "NEW LLC"                 # later file won


def test_csv_download_transient_empty_never_deletes(monkeypatch):
    m = _mod()
    conn = _mk_db()
    conn.execute("INSERT INTO muni_records (market,state,county,kind,"
                 "source_url,pulled_at,record) VALUES ('Hampton','VA',"
                 "'Hampton','sales','ckan:data.virginia.gov/hampton-sales',"
                 "'2020-01-01T00:00:00','{}')")
    monkeypatch.setattr(m.requests, "get",
                        lambda url, **k: _Resp(b"", status=503))
    cfg = _csv_cfg()
    n = m._ADAPTERS["csv_download"](conn, "Hampton-state-mirror", cfg)
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM muni_records").fetchone()[0] == 1


def test_csv_download_assessor_kind_lands_as_assessor(monkeypatch):
    m = _mod()
    monkeypatch.setattr(m.requests, "get", lambda url, **k: _Resp(
        b"PIN,ASSESSED\nR1,500000\n"))
    conn = _mk_db()
    cfg = _csv_cfg(files=(("roll", "https://data.virginia.gov/r.csv"),),
                   kind="assessor")
    n = m.pull_market(conn, "Hampton-roll", cfg)
    assert n == 1
    assert conn.execute("SELECT kind FROM muni_records").fetchone()[0] == "assessor"


# ------------------------- Richmond unit rollup (master address table)

def _units_cfg():
    m = _mod()
    return m.SALES_SOURCES["Richmond-units"]


def _addr(pin, sub, utype, uval, lat=37.53, lng=-77.44):
    attrs = {"PIN": pin, "SubaddressID": sub, "UnitType": utype,
             "UnitValue": uval, "Latitude": lat, "Longitude": lng}
    return attrs, None


def _patch_feed(monkeypatch, m, feats):
    monkeypatch.setattr(m, "count_sales", lambda url, where="": len(feats))
    monkeypatch.setattr(m, "_iter_unit_features",
                        lambda url, total, where: iter(feats))


def test_unit_rollup_counts_distinct_units_per_stripped_pin(monkeypatch):
    """One record per parcel; the 'T' PIN variant folds onto the base;
    duplicate subaddress ids count once; suites/offices don't count;
    blank unit TYPES do (bare unit numbers are how residential rows
    arrive)."""
    m = _mod()
    cfg = _units_cfg()
    feats = [
        _addr("N0170390020", 1, "Apt", "101"),
        _addr("N0170390020", 2, "Apt", "102"),
        _addr("N0170390020T", 1, "Apt", "101"),   # T-variant duplicate
        _addr("N0170390020", 3, "", "103"),       # blank type still counts
        _addr("N0170390020", 4, "Ste", "300"),    # suite: not a dwelling
        _addr("W0000356014", 9, "Unit", "A"),
        _addr("W0000356014", None, "Unit", "B"),  # null sub-id fallback
        _addr("C0010124002", 7, "Apt", ""),       # blank VALUE: skip
    ]
    _patch_feed(monkeypatch, m, feats)
    conn = _mk_db()
    n = m.pull_market(conn, "Richmond-units", cfg)
    assert n == 2
    recs = {json.loads(r[0])["PIN"]: json.loads(r[0]) for r in conn.execute(
        "SELECT record FROM muni_records").fetchall()}
    assert recs["N0170390020"]["UnitCount"] == 3     # 101, 102, 103
    assert recs["W0000356014"]["UnitCount"] == 2
    assert "C0010124002" not in recs
    assert abs(recs["N0170390020"]["Latitude"] - 37.53) < 1e-6
    rows = conn.execute(
        "SELECT DISTINCT market, kind FROM muni_records").fetchall()
    assert rows == [("Richmond", "assessor")]


def test_unit_rollup_resolves_the_layers_real_field_names(monkeypatch):
    """2026-09-03 22:30 host run: the where clause matched 55,435 rows
    but every one was skipped - the live layer's attribute spellings
    differ from the configured guesses, and the log could not say what
    they really were. Resolution must be case/underscore-insensitive
    with per-role fallbacks, and geometry must stand in for missing
    lat/lng attributes."""
    m = _mod()
    cfg = _units_cfg()
    feats = [({"Parcel_Pin": "N0170390020", "SubAddress_Id": i,
               "Unit_Type": "Apt", "Unit_Value": str(100 + i)},
              {"x": -77.44, "y": 37.53}) for i in range(12)]
    _patch_feed(monkeypatch, m, feats)
    conn = _mk_db()
    n = m._ADAPTERS["arcgis_unit_rollup"](conn, "Richmond-units", cfg)
    assert n == 1
    rec = json.loads(conn.execute(
        "SELECT record FROM muni_records").fetchone()[0])
    assert rec["PIN"] == "N0170390020" and rec["UnitCount"] == 12
    assert abs(rec["Latitude"] - 37.53) < 1e-6    # from geometry


def test_unit_rollup_without_pin_refuses_and_names_the_attributes(
        monkeypatch, capsys):
    """A layer with no PIN-like attribute cannot be rolled up - refuse,
    and print the layer's ACTUAL attribute names so the next report says
    what to configure instead of leaving another silent zero."""
    m = _mod()
    cfg = _units_cfg()
    feats = [({"AddressLabel": "1 MAIN ST", "Unit_Value": "101"},
              {"x": -77.44, "y": 37.53})]
    _patch_feed(monkeypatch, m, feats)
    conn = _mk_db()
    n = m._ADAPTERS["arcgis_unit_rollup"](conn, "Richmond-units", cfg)
    assert n == 0
    out = capsys.readouterr().out
    assert "NO PIN-like attribute" in out and "AddressLabel" in out
    assert conn.execute(
        "SELECT COUNT(*) FROM muni_records").fetchone()[0] == 0


def test_unit_rollup_refuses_to_write_the_wrong_city(monkeypatch):
    """The California lesson as executable policy: parcels averaging
    outside the Richmond VA bbox mean the wrong city - nothing may be
    written, and existing rows stay."""
    m = _mod()
    cfg = _units_cfg()
    feats = [_addr("N0170390020", 1, "Apt", "101",
                   lat=37.93, lng=-122.34),     # Richmond, CALIFORNIA
             _addr("W0000356014", 2, "Apt", "1", lat=37.94, lng=-122.35)]
    _patch_feed(monkeypatch, m, feats)
    conn = _mk_db()
    conn.execute("INSERT INTO muni_records VALUES ('Richmond','VA',"
                 "'Richmond','assessor',?, '2000-01-01', '{}')",
                 (cfg["url"],))
    n = m._ADAPTERS["arcgis_unit_rollup"](conn, "Richmond-units", cfg)
    assert n == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM muni_records").fetchone()[0] == 1


def test_unit_rollup_refuses_without_coordinates(monkeypatch):
    """No coordinates = no geography proof = no write."""
    m = _mod()
    cfg = _units_cfg()
    feats = [_addr("N0170390020", 1, "Apt", "101", lat=None, lng=None)]
    _patch_feed(monkeypatch, m, feats)
    conn = _mk_db()
    n = m._ADAPTERS["arcgis_unit_rollup"](conn, "Richmond-units", cfg)
    assert n == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM muni_records").fetchone()[0] == 0


def test_unit_rollup_zero_fetched_reports_the_page_error(monkeypatch,
                                                         capsys):
    """count says 55,435 but pagination returns nothing: the log must
    distinguish 'pages failed' from 'rows all skipped' - the first host
    run could not."""
    m = _mod()
    cfg = _units_cfg()
    monkeypatch.setattr(m, "count_sales", lambda url, where="": 55435)
    monkeypatch.setattr(m, "_iter_unit_features",
                        lambda url, total, where: iter([]))
    conn = _mk_db()
    n = m._ADAPTERS["arcgis_unit_rollup"](conn, "Richmond-units", cfg)
    assert n == 0
    out = capsys.readouterr().out
    assert "0 parcels from 0 fetched rows" in out


def test_unit_rollup_records_reach_the_spine_aliases():
    """The whole point: phase0 must map PIN -> apn, UnitCount -> units,
    Latitude/Longitude -> coords, with the Richmond letter-PIN shape
    accepted - otherwise the rollup writes orphans like the 08-11
    workbook did."""
    import core.phase0 as phase0
    mapped = phase0.normalize_record("Richmond", "VA", {
        "PIN": "N0170390020", "UnitCount": 1051,
        "Latitude": 37.5316, "Longitude": -77.4428,
        "_derived": "distinct dwelling-unit addresses"})
    assert str(mapped.get("apn")) == "N0170390020"
    assert int(float(mapped.get("units"))) == 1051
    assert mapped.get("lat") and mapped.get("lng")


def test_unit_rollup_source_is_not_the_california_org():
    cfg = _units_cfg()
    assert "il6vO1TutlF580Ku" not in cfg["url"]
    assert "k3vhq11XkBNeeOfM" in cfg["url"]
