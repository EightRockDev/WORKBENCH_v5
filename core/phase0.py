"""Phase 0 execution, step P0-1 (spec 7.3): build `properties_8r` from muni_records.

Turns the raw municipal pulls (heterogeneous per-city JSON in the
``muni_records`` table) into the Eight Rock property spine:

    properties_8r(property_id, fips, apn, address, city, state, zip,
                  units, year_built, sqft, use_code, r8_form, r8_market,
                  r8_submarket, assessed_value, owner_name, lat, lng,
                  provenance, built_at)

Everything provider-free by construction: IDs from `core.spine.property_id`
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
from core.market_data import CITY_TO_COUNTY_FIPS_5

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
            "realestateid", "reid", "lrsn", "mastergpin", "recordedgpin"),
    "address": ("address", "situsaddress", "situs", "propertyaddress",
                "siteaddress", "locationaddress", "location", "fulladdress",
                "fulladdr", "propaddress", "propertystreet", "streetaddress",
                "situsaddr", "propaddr", "siteaddre"),
    # Some feeds (Norfolk) split the address into number + name + type.
    "address_number": ("propertystreetnumber", "streetnumber", "housenumber",
                       "stnum", "situsnumber", "stnumber", "strnum"),
    "address_street": ("propertystreetname", "streetname", "situsstreet",
                       "stname", "strname"),
    "address_suffix": ("propertystreettype", "streettype", "stsuffix",
                       "streetsuffix", "sttype", "strtype", "suffixtype"),
    "address_direction": ("propertystreetdirection", "streetdirection",
                          "stdir", "predirection", "stprefix"),
    "address_number_suffix": ("propertystreetnumbersuffix",
                              "streetnumbersuffix", "addnumwsuffix",
                              "addrnumsuffix"),
    "city": ("city", "situscity", "propertycity", "municipality", "stcity"),
    "zip": ("zip", "zipcode", "situszip", "propertyzip", "postalcode",
            "addresszip", "stzipcode", "sitezip"),
    "units": ("units", "livunit", "livingunits", "numunits", "unitcount",
              "dwellingunits", "totalunits", "resunits", "apartments",
              "numberofunits", "livunits"),
    "year_built": ("yearbuilt", "yrbuilt", "yrblt", "yearblt",
                   "actualyearbuilt", "yearbuild", "ayb", "resyrblt",
                   "dwellingyearbuilt",
                   "effyearbuilt", "effectiveyear", "improvementyearbuilt"),
    "sqft": ("sqft", "squarefeet", "buildingsqft", "bldgsqft", "grosssqft",
             "totalsqft", "totsqft", "finishedsqft", "gba", "grossarea",
             "bldgarea", "sfla", "totallivingarea", "livingarea", "resflrarea",
             "residentialfinishedliving", "finishedlivingarea",
             "grossfloorarea"),
    "use_code": ("usecode", "use", "landuse", "landusecode", "propertyuse",
                 "propertyclass", "propclass", "classcd", "class", "classcode",
                 # typebldg BEFORE zoning: Portsmouth's feed carries both, and
                 # its zoning strings (UR-M, T4...) hid the real building type.
                 "typebldg",
                 "zoning", "propertyusecode", "usedesc", "usedescription",
                 "landusedescription", "propertyclassdescription", "usecd",
                 "classdscrp", "usedscrp", "prprtydscrp", "proptype",
                 "propertytype", "statecode", "luc", "bldguse", "resstrtyp",
                 "typeprop", "bldgtype", "resclscode", "improvemen", "clas"),
    "assessed_value": ("assessedvalue", "totalvalue", "totvalue",
                       "totalassessed",
                       "assessedtotal", "totalval", "currenttotal",
                       "currenttotalvalue", "totalcurrentvalue", "assessment",
                       "totalassessment", "appraisedvalue"),
    "owner_name": ("owner", "ownername", "ownernme1", "owner1",
                   "primaryowner", "ownersname", "currentowner"),
    # geolat/geolng come first: the ETL writes them from the layer's actual
    # geometry (WGS84-verified), which beats any attribute column.
    "lat": ("geolat", "lat", "latitude", "y", "pointy", "centroidy"),
    "lng": ("geolng", "lng", "lon", "long", "longitude", "x", "pointx",
            "centroidx"),
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
    r"consideration|grantee|grantor|extension|commercialbuildingarea|"
    r"nghbrhdcd|vahu6|zone|pstladdress1|pstlcity|pstlzip5|pstlstate|"
    r"unit|unitnumber|cntlndval|cntimpval|prvlndval|prvimpval|subdivcd|"
    r"subdivdscrp|statedarea|status|ststate|lastediteduser|lastediteddate|"
    r"fid|objectid1|calcacreag|createdda|lastedite|assessmnt|bldgdiagr|"
    r"impervarea|lastsaledate|lastsaleprice|deed|pstlzip4|floorcount|"
    r"resextwall|acres|landval|bldgval|prevprice|story|stories|rooms|"
    r"bedrooms|noisezone|aicuzzone|taxarea|spx|spy|isprimary|"
    r"createduser|createddate|dateadded|floor|comments|fieldverified|"
    r"dateaddresschanged|dfirmid|ecbfe|ecelevda|topbf|topnhf|blhm|atgar|"
    r"elevequip|lag|hag|lowelevst|ffe|dimen|section|ownernme2|overlay|"
    r"lasteditor|lastupdate|cbpa|caseid|enterprise|floodzone|bfe|bldgdate|"
    r"bath|halfbath|garsf|fp|dtsqft|book|page|prevbook|prevpage|cpn|"
    r"gp10k|gptax|gpcomp|agrid|taxdistrict|taxdistrictdescription|"
    r"daypickup|policepatrolzone|policeprecinct|votingprecinct|"
    r"femazonebldg|watershed|edasite|pstladdress2|frontage|depth|culdesac|"
    r"lglstartdt|ffh|lowfloor99|highwater9|lengthuni|fips|impvalue|"
    r"lndvalue|srcagency|currentda|convm|convft|map|wf|mail1|mail2|"
    r"prevdate|neighborhd|heattype|ac|basement|legaldescr|soildesc|"
    r"soiltype|propaddresssearch|censusblkgrp|votingdistrict|"
    r"votingdistrictname|recyclingweek|platinstr|lotnumber|deedinstr|"
    r"mapbook|mappage|planningsubdivisionid|planningsubdivisionname|"
    r"totalrooms|zipext|strunit|const|dtgar|foundation|attic|instno|"
    r"uniqueidz|rbldgfactr|rphysdprc1|rfuncdprc1|recondprc3|requalfctr|"
    r"firmdate|ecfloodz|issuedate|expdate|firmstatu|pstladdres|bf|"
    r"newfldzo|newstatic|newsfhat|femasourc|"
    r"siteenergyuse.*|sourceenergyuse.*|energycost.*|rollingyearenddate)$")

# Use-code text that identifies multifamily in municipal rolls. Two tiers:
#   * SUBSTRINGS - long unambiguous words, safe to match anywhere in the code.
#   * TOKENS - short codes that must match a WHOLE token. Substring-matching
#     these poisoned the spine: VB zoning "R-40" (single-family!) contains
#     "r-4", classifying ~116K SFH parcels as multifamily and burying the
#     P0-2 comp pool. Tokens split on whitespace/commas/slashes but KEEP
#     hyphens, so "r-4" stays one token distinct from "r-40".
# Duplex/triplex/quadplex are deliberately absent: the product's multifamily
# bar is >= 10 units (spec 7.3); 2-4 unit forms only add proximity noise.
_MF_USE_SUBSTRINGS = (
    "apartment", "multifamily", "multi-family", "multi family",
    "condo hi rise", "garden apt", "townhouse rental", "res 4+",
    "housing",   # public/subsidized/senior housing = rental multifamily
)
_MF_USE_TOKENS = frozenset({"mf", "405", "r-4", "apt", "apts"})  # 405 = VA apartment class

_TOKEN_SPLIT = re.compile(r"[\s,/;:()]+")


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def extract_dict_coords(value: Any) -> tuple[float, float] | None:
    """(lat, lng) from a Socrata location/point column value, else None.

    Socrata serves coordinates as structured values, not scalars:
    ``{"latitude": "36.86", "longitude": "-76.28", ...}`` (location type)
    or GeoJSON ``{"type": "Point", "coordinates": [lng, lat]}``. These must
    never be str()'d into a text field.
    """
    if not isinstance(value, dict):
        return None
    lat = value.get("latitude") or value.get("lat")
    lng = value.get("longitude") or value.get("lng") or value.get("lon")
    if lat is None or lng is None:
        coords = value.get("coordinates")
        if (str(value.get("type", "")).lower() == "point"
                and isinstance(coords, (list, tuple)) and len(coords) >= 2):
            lng, lat = coords[0], coords[1]
    try:
        return (float(lat), float(lng)) if lat is not None and lng is not None else None
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


# Continental-US sanity box: coordinates outside it are junk for a Hampton
# Roads spine no matter what a feed claims.
_US_LAT = (24.0, 50.0)
_US_LNG = (-130.0, -60.0)
_MERCATOR_MAX = 20_100_000  # Web Mercator meters bound (~half circumference)


def sanitize_latlng(lat: float | None, lng: float | None) -> tuple[float | None, float | None]:
    """Return plausible WGS84 degrees or (None, None) - never poison.

    ArcGIS servers that ignore outSR hand back Web Mercator meters; those
    convert deterministically. Virginia state-plane FEET (x ~12M) convert to
    a longitude outside the US box and are dropped - a missing coordinate
    matches by address, a wrong one matches the wrong parcel.
    """
    if lat is None or lng is None:
        return None, None
    if abs(lat) > 90 or abs(lng) > 180:
        if abs(lat) <= _MERCATOR_MAX and abs(lng) <= _MERCATOR_MAX:
            import math
            lng = math.degrees(lng / 6378137.0)
            lat = math.degrees(2 * math.atan(math.exp(lat / 6378137.0)) - math.pi / 2)
        else:
            return None, None
    if _US_LAT[0] <= lat <= _US_LAT[1] and _US_LNG[0] <= lng <= _US_LNG[1]:
        return lat, lng
    return None, None


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
    units_from_points: int = 0
    units_from_points_skipped: int = 0   # non-residential parcels (marinas...)
    pruned_sub10: int = 0                # rows dropped: KNOWN unit count < 10
    coords_backfilled: int = 0           # MF rows given coords by address match
    rents_stamped: int = 0               # MF rows given a HUD-FMR rent estimate
    rents_from_listings: int = 0         # rows given scraped listings rents
    by_city: Counter = field(default_factory=Counter)
    mf_by_city: Counter = field(default_factory=Counter)
    unmapped_keys: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    # Which use-code values drove the MF classification, per city. A wrong
    # alias or over-broad fragment shows up here immediately (the VB "R-40"
    # zoning incident would have been one glance).
    mf_use_codes: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    # WHY each MF row qualified: real unit count >= 10, an MF use code
    # DESPITE a small known unit count (suspicious - duplexes labeled
    # "Multi Family"), or an MF use code with no unit data at all.
    mf_basis: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    # For cities with parcels but ZERO multifamily: their top use-code
    # values overall, so the missing-MF mystery names its own suspects.
    no_mf_use_codes: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

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
            f"units derived from address points: {self.units_from_points:,}"
            f"  (skipped non-residential: {self.units_from_points_skipped:,})",
            f"coords backfilled by address (multifamily): {self.coords_backfilled:,}",
            f"rent estimates stamped (HUD FMR blend): {self.rents_stamped:,}"
            + ("" if self.rents_stamped else "  (ETL db with hud_fmr not found)"),
            f"rents from scraped listings: {self.rents_from_listings:,}"
            + ("" if self.rents_from_listings
               else "  (rent_listings empty or crosswalk not built yet)"),
            f"unusable (no parcel/latlng): {self.skipped_no_parcel_or_latlng:,}",
            f"pruned from backbone (known < {MIN_MF_UNITS} units): "
            f"{self.pruned_sub10:,}  (full roll kept in parcel_index)",
            f"P0-1 coverage:             {self.coverage:.1%}"
            f"  (gate >= {GATE_COVERAGE:.0%}: {'PASS' if self.gate_passed else 'not yet'})",
            "",
            "Multifamily by city: " + ", ".join(
                f"{c} {n:,}" for c, n in self.mf_by_city.most_common()) ,
        ]
        if self.mf_use_codes:
            lines.append("")
            lines.append("Top use codes classified multifamily (wrong codes here = bad aliasing):")
            for city, codes in self.mf_use_codes.items():
                top = ", ".join(f"{c or '(units only)'} x{n}"
                                for c, n in codes.most_common(5))
                lines.append(f"  {city}: {top}")
        if self.no_mf_use_codes:
            lines.append("")
            lines.append("Cities with parcels but NO multifamily found - their top use codes:")
            for city, codes in self.no_mf_use_codes.items():
                top = ", ".join(f"{c or '(blank)'} x{n}"
                                for c, n in codes.most_common(8))
                lines.append(f"  {city}: {top}")
        if self.mf_basis:
            lines.append("")
            lines.append("Why rows qualified as multifamily (suspicious bucket = code w/ small units):")
            for city, basis in self.mf_basis.items():
                parts = ", ".join(f"{k}: {n:,}" for k, n in basis.most_common())
                lines.append(f"  {city}: {parts}")
        pending = {c: k for c, k in self.unmapped_keys.items() if k}
        if pending:
            lines.append("")
            lines.append("Attribute keys with NO mapping yet (add aliases in core/phase0.py):")
            for city, keys in pending.items():
                top = ", ".join(k for k, _n in keys.most_common(12))
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
        # Structured coordinate values (Socrata location/point columns)
        # supply lat/lng only when no scalar column already did - and a
        # dict must NEVER be assigned to a text field like address.
        if isinstance(value, (dict, list)):
            coords = extract_dict_coords(value)
            if coords is not None:
                # Weakest priority: any scalar/geo_lat column may override.
                for fieldname, v in (("lat", coords[0]), ("lng", coords[1])):
                    if fieldname not in out:
                        out[fieldname] = v
                        prio[fieldname] = 999
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
    num_sfx = str(out.get("address_number_suffix") or "").strip()
    direction = str(out.get("address_direction") or "").strip()
    street = str(out.get("address_street") or "").strip()
    suffix = str(out.get("address_suffix") or "").strip()
    if not addr and street:
        house = f"{number}{num_sfx}" if number else ""
        out["address"] = " ".join(x for x in (house, direction, street, suffix) if x)
    elif number and addr and not addr[0].isdigit():
        out["address"] = f"{number} {addr}"
    return out


def derivation_allowed(use_code: str | None) -> bool:
    """May address-point multiplicity supply this parcel's unit count?

    ALLOWLIST, not blocklist. A marina has one point per boat slip and a
    mall one per suite; a subdivision plat puts many points on one
    single-family parcel — and no blocklist can enumerate every spelling
    ('BOAT SLIP', '1 FAM RES', 'R-1', numeric class '101'...). So points
    only count as units when the assessor's code is affirmatively
    multifamily, or when the parcel has no code at all (pure address-point
    feeds - the case the derivation exists for).
    """
    text = (use_code or "").strip()
    if not text:
        return True
    return is_multifamily(text, None)


def is_multifamily(use_code: str | None, units: float | None) -> bool:
    """Multifamily = an apartment-style use code OR a unit count >= the bar."""
    if units is not None and units >= MIN_MF_UNITS:
        return True
    text = (use_code or "").lower()
    if any(fragment in text for fragment in _MF_USE_SUBSTRINGS):
        return True
    tokens = {t.strip(".") for t in _TOKEN_SPLIT.split(text) if t}
    return not _MF_USE_TOKENS.isdisjoint(tokens)


def is_mf_ten_plus_for_city(city: str | None, use_code: str | None,
                            units: float | None,
                            learned: dict[str, set[str]] | None = None) -> bool:
    """`is_mf_ten_plus`, plus any code this city has been taught.

    Rolls that publish bare numeric use codes ("9", "18") match none of the
    text rules, so Portsmouth reported zero multifamily across 36,464 parcels.
    `core.use_code_learn` derives those codes from properties already known to
    be apartments; this is where that learning takes effect. A known unit
    count still wins, exactly as before — the learned map only speaks when the
    feed carries no units at all.
    """
    if units is not None:
        return units >= MIN_MF_UNITS
    if is_multifamily(use_code, None):
        return True
    if learned and city:
        from core.use_code_learn import is_multifamily_learned
        return is_multifamily_learned(city, use_code, learned)
    return False


def is_mf_ten_plus(use_code: str | None, units: float | None) -> bool:
    """The PRODUCT definition of multifamily: >= 10 units (spec 7.3).

    A KNOWN unit count always decides — VB labels ~15.7K duplexes
    "Multi Family", and counting them by label inflated the gate and the
    comp pool alike. The use code only decides when the feed carries no
    unit data at all. Shared by the P0-1 gate and the P0-2 comp pool so
    the two modules can never silently disagree.
    """
    if units is not None:
        return units >= MIN_MF_UNITS
    return is_multifamily(use_code, None)


def build_row(city: str, state: str, raw: dict,
              report: CoverageReport | None = None) -> SpineRow | None:
    """One muni record -> one SpineRow, or None when it can't carry an id."""
    fips = CITY_TO_COUNTY_FIPS_5.get(city)
    if fips is None:
        return None
    mapped = normalize_record(city, state, raw, report)

    apn = str(mapped.get("apn") or "").strip()
    lat, lng = sanitize_latlng(_num(mapped.get("lat")), _num(mapped.get("lng")))
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
        # Third derive param is STORIES (not year built - passing the year
        # made every styled-less parcel a "high-rise"). No feed maps
        # stories yet, and the value here is provisional anyway: build_spine
        # recomputes r8_form from the merged row after all feeds land.
        r8_form=spine.derive_8r_form(use_code, int(units) if units else None,
                                     None),
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
    est_avg_rent   REAL,
    rent_source    TEXT,
    provenance     TEXT NOT NULL DEFAULT '8r',
    built_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_8r_city_units ON properties_8r (city, units);
CREATE INDEX IF NOT EXISTS ix_8r_form ON properties_8r (r8_form);
"""


def _iter_muni_assessor_rows(conn: sqlite3.Connection,
                             cities: tuple[str, ...]) -> Iterator[tuple[str, str, str, dict]]:
    marks = ",".join("?" for _ in cities)
    # Deterministic scan order: the COALESCE merge keeps the first non-NULL
    # value per field, and rowid order shifts every re-pull (run_feed
    # DELETEs + re-INSERTs a feed's rows, moving it to the end). Ordering by
    # source_url makes "which feed wins" identical on every host and run.
    cur = conn.execute(
        f"""SELECT market, state, source_url, record FROM muni_records
             WHERE kind LIKE 'assessor%' AND market IN ({marks})
             ORDER BY source_url, id""",
        cities)
    for market, state, source, record in cur:
        raw = _decode_muni_record(record)
        if raw is not None:
            yield market, state or "VA", source or "", raw


def _decode_muni_record(record: str | None) -> dict | None:
    """One stored muni_records payload -> a flat attribute dict (or None)."""
    try:
        raw = json.loads(record) if record else {}
    except json.JSONDecodeError:
        raw = {}
    # ArcGIS rows nest the payload under "attributes"; Socrata rows are flat.
    if isinstance(raw, dict) and isinstance(raw.get("attributes"), dict):
        geo = raw.get("geometry") or {}
        raw = {**raw["attributes"],
               **({"x": geo.get("x"), "y": geo.get("y")} if geo else {})}
    return raw if isinstance(raw, dict) else None


def build_spine(db_path: Path,
                cities: tuple[str, ...] = tuple(CITY_TO_COUNTY_FIPS_5),
                rebuild: bool = True) -> CoverageReport:
    """P0-1: populate properties_8r inside `db_path` from its muni_records.

    Idempotent: rows key on the deterministic 8R id; a re-run refreshes them.
    """
    report = CoverageReport()
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        conn.executescript(_SPINE_SCHEMA)
        from core.rent_signal import ensure_rent_columns
        ensure_rent_columns(conn)   # pre-rent-signal DBs migrate in place
        if rebuild:
            conn.execute("DELETE FROM properties_8r")
        now = dt.datetime.now().isoformat(timespec="seconds")
        # Address-point feeds (Chesapeake/Norfolk) emit ONE ROW PER APARTMENT
        # sharing the parcel id - the row count per (parcel, feed) IS the
        # unit count. Track it; max across feeds so overlapping sources
        # never double-count.
        point_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for city, state, source, raw in _iter_muni_assessor_rows(conn, cities):
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
            point_counts[row.property_id][source] += 1
            if spine.is_provisional(row.property_id):
                report.provisional_ids += 1
            conn.execute(
                """INSERT INTO properties_8r
                   (property_id, fips, apn, address, city, state, zip, units,
                    year_built, sqft, use_code, r8_form, r8_market,
                    r8_submarket, assessed_value, owner_name, lat, lng,
                    provenance, built_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(property_id) DO UPDATE SET
                     address=COALESCE(properties_8r.address, excluded.address),
                     units=COALESCE(properties_8r.units, excluded.units),
                     year_built=COALESCE(properties_8r.year_built,
                                         excluded.year_built),
                     sqft=COALESCE(properties_8r.sqft, excluded.sqft),
                     use_code=COALESCE(properties_8r.use_code,
                                       excluded.use_code),
                     assessed_value=COALESCE(properties_8r.assessed_value,
                                             excluded.assessed_value),
                     owner_name=COALESCE(properties_8r.owner_name,
                                         excluded.owner_name),
                     lat=COALESCE(properties_8r.lat, excluded.lat),
                     lng=COALESCE(properties_8r.lng, excluded.lng),
                     built_at=excluded.built_at""",
                (row.property_id, row.fips, row.apn, row.address, row.city,
                 row.state, row.zip, row.units, row.year_built, row.sqft,
                 row.use_code, row.r8_form, row.r8_market, row.r8_submarket,
                 row.assessed_value, row.owner_name, row.lat, row.lng,
                 row.provenance, now))
            report.written += 1

        # Derive units from address-point multiplicity: N apartment-points
        # on one parcel = N units. Allowlist-guarded (see
        # derivation_allowed): marinas, malls, and single-family plats all
        # have 10+ points too - Chesapeake classified "BOAT SLIP x92" as
        # multifamily before this. Derived counts may also RAISE a smaller
        # stored value: building-card feeds write units=1 per row, and the
        # first card freezes via COALESCE - 12 points on a "units=1" parcel
        # means 12, not 1. An explicit LARGER count is never lowered.
        derived = 0
        skipped_nonres = 0
        for pid, per_source in point_counts.items():
            n = max(per_source.values())
            if n < 2:
                continue
            row = conn.execute(
                "SELECT use_code, units FROM properties_8r "
                " WHERE property_id = ?", (pid,)).fetchone()
            if row is None:
                continue
            if not derivation_allowed(row[0]):
                skipped_nonres += 1
                continue
            cur = conn.execute(
                "UPDATE properties_8r SET units = ? "
                " WHERE property_id = ? AND (units IS NULL OR units < ?)",
                (n, pid, n))
            derived += cur.rowcount
        report.units_from_points = derived
        report.units_from_points_skipped = skipped_nonres

        # Address-based coordinate backfill: some feeds carry no geometry at
        # all (Norfolk's Socrata assessor), leaving a whole city's
        # multifamily backbone coordinate-blind - every comp subject there
        # is skipped and legacy rows can match by address only. Sibling
        # records for the SAME street address (building permits, other
        # layers) usually DO carry verified coordinates; borrow them.
        # Multifamily rows only (the parity/comp universe), exact
        # normalized-address match, same city, sanitized like any other
        # coordinate.
        from core.phase0_parity import normalize_address
        blind: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for pid, city, address, use_code, units in conn.execute(
                "SELECT property_id, city, address, use_code, units "
                "  FROM properties_8r "
                " WHERE (lat IS NULL OR lng IS NULL) "
                "   AND address IS NOT NULL AND address != ''"):
            if city in cities and is_mf_ten_plus(use_code, _num(units)):
                blind[city].append((pid, address))
        backfilled = 0
        for city, rows in blind.items():
            addr_coords: dict[str, tuple[float, float]] = {}
            for (record,) in conn.execute(
                    "SELECT record FROM muni_records WHERE market = ? "
                    " ORDER BY source_url, id", (city,)):
                raw = _decode_muni_record(record)
                if not raw:
                    continue
                mapped = normalize_record(city, "VA", raw)
                lat, lng = sanitize_latlng(_num(mapped.get("lat")),
                                           _num(mapped.get("lng")))
                if lat is None:
                    continue
                key = normalize_address(str(mapped.get("address") or ""))
                if key and key not in addr_coords:
                    addr_coords[key] = (lat, lng)
            updates = [(hit[0], hit[1], pid)
                       for pid, address in rows
                       if (hit := addr_coords.get(normalize_address(address)))]
            conn.executemany(
                "UPDATE properties_8r SET lat = ?, lng = ? "
                " WHERE property_id = ?", updates)
            backfilled += len(updates)
        report.coords_backfilled = backfilled

        # r8_form is a function of the MERGED (use_code, units) - recompute
        # it once everything (multi-feed COALESCE + point derivation) has
        # settled, so it can never desync from the row it describes.
        conn.executemany(
            "UPDATE properties_8r SET r8_form = ? WHERE property_id = ?",
            [(spine.derive_8r_form(uc, u, None), pid)
             for pid, uc, u in conn.execute(
                 "SELECT property_id, use_code, units FROM properties_8r")])

        # Multifamily counts are computed from the FINISHED table (derived
        # units included), not incrementally.
        from core.use_code_learn import load as _load_learned
        learned = _load_learned(conn)
        for city, units, use_code in conn.execute(
                "SELECT city, units, use_code FROM properties_8r"):
            if is_mf_ten_plus_for_city(city, use_code, units, learned):
                report.multifamily += 1
                report.mf_by_city[city] += 1
                report.mf_use_codes[city][(use_code or "").strip()[:40]] += 1
                if units is not None:
                    basis = "units>=10"
                elif is_multifamily(use_code, None):
                    basis = "code only (no units)"
                else:
                    basis = "learned code (no units)"
                report.mf_basis[city][basis] += 1
        for city in report.by_city:
            if report.mf_by_city.get(city):
                continue
            for (uc,), n in (
                    (r[:1], r[1]) for r in conn.execute(
                        """SELECT use_code, count(*) FROM properties_8r
                            WHERE city = ? GROUP BY use_code
                            ORDER BY count(*) DESC LIMIT 8""", (city,))):
                report.no_mf_use_codes[city][(uc or "").strip()[:40]] = n
        report.pruned_sub10 = prune_backbone(conn)
        conn.commit()

    # Rent signal: scraped listings rents first (best source, mapped via
    # last run's crosswalk), then HUD-FMR estimates fill every remaining
    # multifamily row so the P0-2 rent-delta gate measures something real
    # instead of passing vacuously. No ETL DB on this machine -> 0, and
    # the report says so. (Own connections - after the build txn closes.)
    from core import rent_signal
    report.rents_from_listings = rent_signal.apply_listings_rents(db_path)
    report.rents_stamped = rent_signal.apply_rent_signal(db_path)
    return report


# Bump when the BUILD's behavior changes (new classification rule, new
# aliasing, prune logic...) so an unchanged-inputs skip cannot pin an old
# spine under new code. Same pattern, same reason as listings
# PULL_GENERATION: a fix that cannot run is indistinguishable from no fix.
SPINE_BUILD_GENERATION = 1


def spine_input_fingerprint(db_path: Path) -> str:
    """Hash of everything the spine build consumes.

    The autopilot chains cycles continuously in dev cadence; rebuilding a
    ~1M-row spine when NONE of its inputs moved starved the office server
    (the app crawled, reruns ghosted stale elements). Inputs: the muni
    cache, learned use codes, and scraped rents - plus the code generation
    above.
    """
    import hashlib
    parts = [f"gen={SPINE_BUILD_GENERATION}"]
    with sqlite3.connect(db_path) as conn:
        for label, sql in (
                ("muni", "SELECT COUNT(*), MAX(pulled_at) FROM muni_records"),
                ("learned", "SELECT COUNT(*), MAX(learned_at) "
                            "FROM learned_mf_use_codes"),
                ("rents", "SELECT COUNT(*), MAX(scraped_at) "
                          "FROM rent_listings")):
            try:
                n, latest = conn.execute(sql).fetchone()
            except sqlite3.Error:
                n, latest = 0, None
            parts.append(f"{label}={n}:{latest}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def load_spine_meta(db_path: Path, key: str) -> str | None:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM spine_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def save_spine_meta(db_path: Path, key: str, value: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS spine_meta "
                     "(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO spine_meta (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))
        conn.commit()


def prune_backbone(conn: sqlite3.Connection) -> int:
    """Drop rows with a KNOWN unit count below 10 from the backbone
    (owner directive 2026-08-03: single-family parcels are waste).

    What must survive, and why the rule is exactly `units IS NOT NULL AND
    units < 10` and nothing looser:

      * Every classified multifamily row survives by construction — a known
        count >= 10 fails the filter, and code-only / learned-code MF rows
        carry NULL units.
      * Rows with NO unit count survive. They are not "probably houses":
        Portsmouth's whole roll is units-NULL, and those rows are the
        use-code learner's anchors and next cycle's classification targets.
        Pruning the unknown would freeze every blind city at zero forever.
      * The prune runs AFTER multi-parcel footprint aggregation, so a
        258-unit community fragmented across small parcels has already been
        summed — its rows read >= 10 here.

    The FULL roll is preserved first in `parcel_index` — six compact
    columns, no heavy attributes — because two consumers genuinely need
    every parcel: the verified-badge lookup (a user-added property must be
    checkable against the municipal roll even when the roll says 8 units)
    and the learner's citywide denominators ("too common to mean
    apartments" needs the whole city as its base). And the prune is
    non-destructive by design: the raw `muni_records` cache remains the
    rebuild source, so a future reclassification resurrects anything.

    ER_SPINE_KEEP_ALL=1 skips the prune (debugging/parity investigation).
    """
    import os
    conn.execute("DROP TABLE IF EXISTS parcel_index")
    conn.execute("""CREATE TABLE parcel_index AS
        SELECT fips, apn, address, city, units, use_code
          FROM properties_8r""")
    conn.execute("CREATE INDEX ix_pidx_city ON parcel_index (city)")
    if os.environ.get("ER_SPINE_KEEP_ALL", "").strip() in ("1", "true", "yes"):
        return 0
    cur = conn.execute(
        "DELETE FROM properties_8r WHERE units IS NOT NULL AND units < ?",
        (MIN_MF_UNITS,))
    return cur.rowcount


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
