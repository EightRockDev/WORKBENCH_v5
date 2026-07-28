"""Phase 0 execution, step P0-1 (spec 7.3): build `properties_8r` from muni_records.

Turns the raw municipal pulls (heterogeneous per-city JSON in the
``muni_records`` table) into the Eight Rock property spine:

    properties_8r(property_id, fips, apn, address, city, state, zip,
                  units, year_built, sqft, use_code, r8_form, r8_market,
                  r8_submarket, assessed_value, owner_name, lat, lng,
                  provenance, built_at)

Everything ALN-free by construction: IDs from `core.spine.property_id`
(FIPS + APN hash), form from assessor use codes, market taxonomy = the
Eight Rock HR submarkets. Provenance is "8r".

Normalization is two-tier:
  * A generic alias table maps the common attribute spellings that the
    ArcGIS/Socrata feeds use (APN/GPIN/PARCELID..., LIVUNIT/UNITS...,
    YEAR_BUILT/YRBLT...). This resolves most feeds with zero per-city code.
  * `field_report()` lists the attribute keys that did NOT map, per city, so
    unrecognized feeds are tuned by adding aliases - the same pattern as the
    skip-trace live adapters.

The P0-1 gate (`CoverageReport.gate_passed`) mirrors the spec: the spine must
cover >=95% of Hampton Roads multifamily parcels (>=10 units) present in the
municipal data.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from core import spine
from core.market_data import HR_CITY_TO_COUNTY_FIPS_5

MIN_MF_UNITS = 10          # the coverage gate counts parcels >= this
GATE_COVERAGE = 0.95       # P0-1 gate from spec 7.3

# ---------------------------------------------------------------------------
# Attribute aliasing - normalized key (lowercase alnum) -> spine field.
# Sources: Norfolk Socrata reference model, Newport News/VB ArcGIS layers,
# and the common ArcGIS parcel-schema spellings.
# ---------------------------------------------------------------------------

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    # Within each field, earlier aliases WIN over later ones when a record
    # carries several (e.g. yearbuilt beats effectiveyear).
    "apn": ("apn", "gpin", "parcelid", "parcel", "mapparcel", "parcelnumber",
            "parcelno", "pin", "mappin", "acct", "account", "accountnumber",
            "taxparcelid", "parid", "parno", "prop_id", "propertyid",
            "realestateid", "reid", "lrsn"),
    "address": ("address", "situsaddress", "situs", "propertyaddress",
                "siteaddress", "locationaddress", "location", "fulladdress",
                "propertystreet", "streetaddress", "situsaddr", "propaddr"),
    # Some feeds (Norfolk) split the address into number + name + type.
    "address_number": ("propertystreetnumber", "streetnumber", "housenumber",
                       "stnum", "situsnumber"),
    "address_street": ("propertystreetname", "streetname", "situsstreet"),
    "address_suffix": ("propertystreettype", "streettype", "stsuffix",
                       "streetsuffix"),
    "city": ("city", "situscity", "propertycity", "municipality"),
    "zip": ("zip", "zipcode", "situszip", "propertyzip", "postalcode",
            "addresszip"),
    "units": ("units", "livunit", "livingunits", "numunits", "unitcount",
              "dwellingunits", "totalunits", "resunits", "apartments",
              "numberofunits", "livunits"),
    "year_built": ("yearbuilt", "yrbuilt", "yrblt", "yearblt",
                   "actualyearbuilt", "yearbuild", "ayb", "effyearbuilt",
                   "effectiveyear", "improvementyearbuilt"),
    "sqft": ("sqft", "squarefeet", "buildingsqft", "bldgsqft", "grosssqft",
             "totalsqft", "finishedsqft", "gba", "grossarea", "bldgarea",
             "sfla", "totallivingarea", "livingarea", "resflrarea"),
    "use_code": ("usecode", "use", "landuse", "landusecode", "propertyuse",
                 "propertyclass", "propclass", "classcd", "class", "classcode",
                 "zoning", "propertyusecode", "usedesc", "usedescription",
                 "landusedescription", "propertyclassdescription", "usecd",
                 "classdscrp", "proptype", "propertytype", "statecode", "luc"),
    "assessed_value": ("assessedvalue", "totalvalue", "totalassessed",
                       "assessedtotal", "totalval", "currenttotal",
                       "currenttotalvalue", "totalcurrentvalue", "assessment",
                       "totalassessment", "appraisedvalue"),
    "owner_name": ("owner", "ownername", "ownernme1", "owner1",
                   "primaryowner", "ownersname", "currentowner"),
    "lat": ("lat", "latitude", "y", "pointy", "centroidy"),
    "lng": ("lng", "lon", "long", "longitude", "x", "pointx", "centroidx"),
}

_ALIAS_LOOKUP: dict[str, tuple[str, int]] = {
    alias: (fieldname, priority)
    for fieldname, aliases in _FIELD_ALIASES.items()
    for priority, alias in enumerate(aliases)
}

# Feed bookkeeping/geometry columns that carry no property data - kept out of
# the "no mapping yet" report so it only shows real gaps.
_IGNORED_KEYS = re.compile(
    r"^(objectid|globalid|shape.*|.*link$|legal|legaldescription|cntrlno|"
    r"transfer|transferdate|saledate|saleprice|landvalue|improvementvalue|"
    r"currentlandvalue|currentimprovementvalue|priorlandvalue|"
    r"priorimprovementvalue|vacant|government|neighborhood|state|"
    r"landuseyesorno|fy|fiscalyear|deedbk|deedpg|deedbook|deedpage|"
    r"documentnumber|assessmntdist|calcacreage|acreage|landsquarefootage|"
    r"mapbookpg|project|hubzone|pspzone|cityowned|censustract|censusblock|"
    r"consideration|grantee|extension)$")

# Use-code fragments that identify multifamily in municipal rolls.
_MF_USE_FRAGMENTS = (
    "apartment", "apartments", "multifamily", "multi-family", "multi family",
    "condo hi rise", "garden apt", "duplex", "triplex", "quadplex",
    "townhouse rental", "res 4+", "r-4", "mf", "405",   # 405 = VA apartment class
)


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class SpineRow:
    property_id: str
    fips: str
    apn: str | None
    address: str | None
    city: str
    state: str
    zip: str | None
    units: int | None
    year_built: int | None
    sqft: float | None
    use_code: str | None
    r8_form: str
    r8_market: str
    r8_submarket: str
    assessed_value: float | None
    owner_name: str | None
    lat: float | None
    lng: float | None
    provenance: str = "8r"


@dataclass
class CoverageReport:
    """P0-1 gate arithmetic + the tuning data for unmapped feeds."""
    scanned: int = 0
    normalized: int = 0
    multifamily: int = 0                 # >= MIN_MF_UNITS
    written: int = 0
    skipped_no_parcel_or_latlng: int = 0
    provisional_ids: int = 0
    by_city: Counter = field(default_factory=Counter)
    mf_by_city: Counter = field(default_factory=Counter)
    unmapped_keys: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    @property
    def coverage(self) -> float:
        """Share of scanned multifamily-relevant records that made the spine."""
        denom = self.multifamily + self.skipped_no_parcel_or_latlng
        return (self.multifamily / denom) if denom else 0.0

    @property
    def gate_passed(self) -> bool:
        return self.multifamily > 0 and self.coverage >= GATE_COVERAGE

    def summary(self) -> str:
        lines = [
            f"muni records scanned:      {self.scanned:,}",
            f"parcels normalized:        {self.normalized:,}",
            f"multifamily (>= {MIN_MF_UNITS} units): {self.multifamily:,}",
            f"written to properties_8r:  {self.written:,}",
            f"provisional (no-APN) ids:  {self.provisional_ids:,}",
            f"unusable (no parcel/latlng): {self.skipped_no_parcel_or_latlng:,}",
            f"P0-1 coverage:             {self.coverage:.1%}"
            f"  (gate >= {GATE_COVERAGE:.0%}: {'PASS' if self.gate_passed else 'not yet'})",
            "",
            "Multifamily by city: " + ", ".join(
                f"{c} {n:,}" for c, n in self.mf_by_city.most_common()) ,
        ]
        pending = {c: k for c, k in self.unmapped_keys.items() if k}
        if pending:
            lines.append("")
            lines.append("Attribute keys with NO mapping yet (add aliases in core/phase0.py):")
            for city, keys in pending.items():
                top = ", ".join(k for k, _n in keys.most_common(8))
                lines.append(f"  {city}: {top}")
        return "\n".join(lines)


def normalize_record(city: str, state: str, raw: dict,
                     report: CoverageReport | None = None) -> dict[str, Any]:
    """Map one raw municipal attribute dict onto spine fields.

    When a record carries several aliases for one field (yearbuilt AND
    effectiveyear), the alias listed earlier in _FIELD_ALIASES wins.
    """
    out: dict[str, Any] = {}
    prio: dict[str, int] = {}
    for key, value in (raw or {}).items():
        if value in (None, "", " "):
            continue
        norm = _norm_key(key)
        hit = _ALIAS_LOOKUP.get(norm)
        if hit is None:
            if report is not None and not _IGNORED_KEYS.match(norm):
                report.unmapped_keys[city][key] += 1
            continue
        fieldname, priority = hit
        if fieldname not in out or priority < prio[fieldname]:
            out[fieldname] = value
            prio[fieldname] = priority
    # Norfolk-style split address: assemble number + name + type when the
    # feed carries the pieces separately - without this no Norfolk address
    # ever matches the legacy spine.
    addr = str(out.get("address") or "").strip()
    number = str(out.get("address_number") or "").strip()
    street = str(out.get("address_street") or "").strip()
    suffix = str(out.get("address_suffix") or "").strip()
    if not addr and street:
        out["address"] = " ".join(x for x in (number, street, suffix) if x)
    elif number and addr and not addr[0].isdigit():
        out["address"] = f"{number} {addr}"
    return out


def is_multifamily(use_code: str | None, units: float | None) -> bool:
    """Multifamily = an apartment-style use code OR a unit count >= the bar."""
    if units is not None and units >= MIN_MF_UNITS:
        return True
    token = (use_code or "").lower()
    return any(fragment in token for fragment in _MF_USE_FRAGMENTS)


def build_row(city: str, state: str, raw: dict,
              report: CoverageReport | None = None) -> SpineRow | None:
    """One muni record -> one SpineRow, or None when it can't carry an id."""
    fips = HR_CITY_TO_COUNTY_FIPS_5.get(city)
    if fips is None:
        return None
    mapped = normalize_record(city, state, raw, report)

    apn = str(mapped.get("apn") or "").strip()
    lat, lng = _num(mapped.get("lat")), _num(mapped.get("lng"))
    if apn and spine.normalize_apn(apn):
        pid = spine.property_id(fips, apn)
    elif lat is not None and lng is not None:
        pid = spine.provisional_property_id(fips, lat, lng)
    else:
        return None

    units = _num(mapped.get("units"))
    year_built = _num(mapped.get("year_built"))
    use_code = str(mapped.get("use_code") or "") or None
    return SpineRow(
        property_id=pid,
        fips=fips,
        apn=apn or None,
        address=str(mapped.get("address") or "") or None,
        city=city,
        state=state,
        zip=str(mapped.get("zip") or "") or None,
        units=int(units) if units else None,
        year_built=int(year_built) if year_built and year_built > 1600 else None,
        sqft=_num(mapped.get("sqft")),
        use_code=use_code,
        r8_form=spine.derive_8r_form(use_code, int(units) if units else None,
                                     int(year_built) if year_built else None),
        r8_market="Hampton Roads",
        r8_submarket=city,
        assessed_value=_num(mapped.get("assessed_value")),
        owner_name=str(mapped.get("owner_name") or "") or None,
        lat=lat,
        lng=lng,
    )


# ---------------------------------------------------------------------------
# SQLite plumbing
# ---------------------------------------------------------------------------

_SPINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS properties_8r (
    property_id    TEXT PRIMARY KEY,
    fips           TEXT NOT NULL,
    apn            TEXT,
    address        TEXT,
    city           TEXT,
    state          TEXT,
    zip            TEXT,
    units          INTEGER,
    year_built     INTEGER,
    sqft           REAL,
    use_code       TEXT,
    r8_form        TEXT,
    r8_market      TEXT,
    r8_submarket   TEXT,
    assessed_value REAL,
    owner_name     TEXT,
    lat            REAL,
    lng            REAL,
    provenance     TEXT NOT NULL DEFAULT '8r',
    built_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_8r_city_units ON properties_8r (city, units);
CREATE INDEX IF NOT EXISTS ix_8r_form ON properties_8r (r8_form);
"""


def _iter_muni_assessor_rows(conn: sqlite3.Connection,
                             cities: tuple[str, ...]) -> Iterator[tuple[str, str, dict]]:
    marks = ",".join("?" for _ in cities)
    cur = conn.execute(
        f"""SELECT market, state, record FROM muni_records
             WHERE kind LIKE 'assessor%' AND market IN ({marks})""",
        cities)
    for market, state, record in cur:
        try:
            raw = json.loads(record) if record else {}
        except json.JSONDecodeError:
            raw = {}
        # ArcGIS rows nest the payload under "attributes"; Socrata rows are flat.
        if isinstance(raw, dict) and isinstance(raw.get("attributes"), dict):
            geo = raw.get("geometry") or {}
            raw = {**raw["attributes"],
                   **({"x": geo.get("x"), "y": geo.get("y")} if geo else {})}
        if isinstance(raw, dict):
            yield market, state or "VA", raw


def build_spine(db_path: Path,
                cities: tuple[str, ...] = tuple(HR_CITY_TO_COUNTY_FIPS_5),
                rebuild: bool = True) -> CoverageReport:
    """P0-1: populate properties_8r inside `db_path` from its muni_records.

    Idempotent: rows key on the deterministic 8R id; a re-run refreshes them.
    """
    report = CoverageReport()
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SPINE_SCHEMA)
        if rebuild:
            conn.execute("DELETE FROM properties_8r")
        now = dt.datetime.now().isoformat(timespec="seconds")
        for city, state, raw in _iter_muni_assessor_rows(conn, cities):
            report.scanned += 1
            row = build_row(city, state, raw, report)
            if row is None:
                mapped = normalize_record(city, state, raw)
                if is_multifamily(str(mapped.get("use_code") or ""),
                                  _num(mapped.get("units"))):
                    report.skipped_no_parcel_or_latlng += 1
                continue
            report.normalized += 1
            report.by_city[city] += 1
            if spine.is_provisional(row.property_id):
                report.provisional_ids += 1
            mf = is_multifamily(row.use_code, row.units)
            if mf:
                report.multifamily += 1
                report.mf_by_city[city] += 1
            conn.execute(
                """INSERT INTO properties_8r
                   (property_id, fips, apn, address, city, state, zip, units,
                    year_built, sqft, use_code, r8_form, r8_market,
                    r8_submarket, assessed_value, owner_name, lat, lng,
                    provenance, built_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(property_id) DO UPDATE SET
                     address=excluded.address, units=excluded.units,
                     year_built=excluded.year_built, sqft=excluded.sqft,
                     use_code=excluded.use_code, r8_form=excluded.r8_form,
                     assessed_value=excluded.assessed_value,
                     owner_name=excluded.owner_name,
                     lat=excluded.lat, lng=excluded.lng,
                     built_at=excluded.built_at""",
                (row.property_id, row.fips, row.apn, row.address, row.city,
                 row.state, row.zip, row.units, row.year_built, row.sqft,
                 row.use_code, row.r8_form, row.r8_market, row.r8_submarket,
                 row.assessed_value, row.owner_name, row.lat, row.lng,
                 row.provenance, now))
            report.written += 1
        conn.commit()
    return report


def find_workbench_db() -> Path | None:
    """The SQLite holding muni_records: $ER_WORKBENCH_DB, then data/workbench.db."""
    import os
    override = os.environ.get("ER_WORKBENCH_DB", "").strip()
    if override and Path(override).is_file():
        return Path(override)
    default = Path(__file__).resolve().parent.parent / "data" / "workbench.db"
    return default if default.is_file() else None


def has_muni_records(db_path: Path) -> int:
    """Row count of muni_records (0 when the table is absent)."""
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT count(*) FROM muni_records WHERE kind LIKE 'assessor%'")
            return int(cur.fetchone()[0])
    except sqlite3.Error:
        return 0
