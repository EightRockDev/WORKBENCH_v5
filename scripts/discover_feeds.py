"""Feed discovery for the uncovered Hampton Roads cities (Module F / P0-1).

The parity report proved the ceiling: only Norfolk and Newport News feeds
carry unit data. This tool runs ON THE SERVER (the build environment can't
reach city portals), finds ArcGIS layers that DO carry parcel/unit/vintage
fields for the uncovered cities, and writes them to ``data/feeds_extra.json``
where the municipal ETL picks them up automatically.

Three probe strategies, all bounded and polite:
  1. Known org roots (Virginia Beach's AGOL org, Chesapeake's own server)
     are walked service-by-service, layer-by-layer.
  2. ArcGIS Online's public search API is queried for "<city> virginia
     parcels" feature services.
  3. Extra roots can be passed on the command line.

Each layer's fields are scored against the same alias vocabulary the spine
builder uses - a layer only qualifies when it has a parcel id AND a
unit-count-ish or use-code field. Top candidates per city are written as
FeedSpec dicts; everything found is printed for the human.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.phase0 import _ALIAS_LOOKUP, _norm_key  # noqa: E402
from etl_munidata import named_for_other_city as _named_for_other_city  # noqa: E402

# Norfolk is here for COORDINATES: its Socrata roll has full attributes but
# no lat/lng at all (parity: "no coordinates in feed"), capping matches at
# address-only. An ArcGIS parcel layer fixes that.
TARGET_CITIES = ("Virginia Beach", "Chesapeake", "Hampton", "Portsmouth",
                 "Suffolk", "Norfolk", "Richmond")

KNOWN_ROOTS: dict[str, list[str]] = {
    "Virginia Beach": [
        "https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services",
    ],
    "Chesapeake": [
        "https://gis.cityofchesapeake.net/mapping/rest/services",
    ],
    "Hampton": [
        "https://gis.hampton.gov/arcgis/rest/services",
    ],
    "Portsmouth": [
        "https://gis.portsmouthva.gov/arcgis/rest/services",
    ],
    "Suffolk": [
        "https://services2.arcgis.com/roiGKZTZbeqAsCZi/arcgis/rest/services",
    ],
    # Norfolk's GIS lives on its own server (the Socrata roll has no
    # coordinates); AGOL search is the fallback if this root is offline.
    "Norfolk": [
        "https://gis.norfolk.gov/arcgis/rest/services",
    ],
    # Wave 1 of the 50-metro rollout (spec 15). Richmond's own server plus
    # AGOL search; the VGIN statewide fallback below covers it regardless.
    "Richmond": [
        "https://gis.richmondgov.com/arcgis/rest/services",
    ],
}

AGOL_SEARCH = "https://www.arcgis.com/sharing/rest/search"

# Generous city bounding boxes (lat_min, lat_max, lng_min, lng_max) - used to
# verify a candidate layer's records actually sit in the claimed city. AGOL
# search returns plenty of look-alike layers from OTHER cities (a "Hampton"
# query surfaced Chesapeake blast-zone parcels); ingesting those under the
# wrong city would poison the spine with wrong-FIPS ids.
CITY_BBOX = {
    "Virginia Beach": (36.55, 36.95, -76.23, -75.87),
    "Chesapeake":     (36.50, 36.90, -76.50, -76.15),
    "Hampton":        (36.98, 37.14, -76.48, -76.23),
    "Portsmouth":     (36.77, 36.91, -76.43, -76.27),
    "Suffolk":        (36.55, 36.95, -76.78, -76.32),
    "Norfolk":        (36.82, 36.98, -76.35, -76.16),
    "Newport News":   (36.93, 37.22, -76.65, -76.35),
    "Richmond":       (37.44, 37.62, -77.61, -77.38),
}

MAX_SERVICES_PER_ROOT = 200
MAX_LAYERS_PER_SERVICE = 25
TIMEOUT = 25

# Which spine fields make a layer valuable, and how much.
_FIELD_WEIGHTS = {"apn": 4, "units": 5, "use_code": 3, "year_built": 2,
                  "address": 2, "sqft": 1, "owner_name": 1,
                  "assessed_value": 1}
MIN_SCORE = 7      # needs apn + (units or use_code) at minimum

# Every Hampton Roads city has tens of thousands of parcels (Suffolk, the
# smallest, ~30K). A layer with a few hundred rows is a study extract or a
# subset, not the roll. Hampton's accepted feed had 716 records against a
# ~50K-parcel city, which is why the city reported no multifamily at all -
# nothing about the FIELDS was wrong, so field scoring alone never caught it.
PLAUSIBLE_ROLL_MIN = 5_000

# Scoring adjustments by size. A real roll should outrank a subset, but a
# subset is still better than nothing when a city has no other candidate, so
# this demotes rather than rejects.
SIZE_BONUS = 4
SIZE_PENALTY = 6

# A layer whose NAME declares a subset can pass the size gate and still
# never contain the city's apartments (Richmond's Undeveloped_Parcels layer:
# 6,570 records, all vacant land). Demoted like small layers, and it does
# not count as a real roll, so the statewide fallback still fires.
SUBSET_NAME_TOKENS = ("undeveloped", "vacant", "blast", "study", "czm",
                      "elevation", "missing", "flood", "wetland", "historic",
                      "easement", "surplus", "solar", "radiation")


def subset_named(name: str, url: str) -> bool:
    text = f"{name} {url}".lower()
    return any(t in text for t in SUBSET_NAME_TOKENS)


def score_fields(field_names: list[str]) -> tuple[int, dict[str, str]]:
    """(score, {spine_field: layer_field}) for one layer's field list."""
    mapped: dict[str, str] = {}
    for name in field_names:
        hit = _ALIAS_LOOKUP.get(_norm_key(name))
        if hit and hit[0] not in mapped and not hit[0].startswith("address_"):
            mapped[hit[0]] = name
    score = sum(_FIELD_WEIGHTS.get(f, 0) for f in mapped)
    if "apn" not in mapped:
        score = 0                      # no parcel id -> no deterministic 8R id
    return score, mapped


def layer_record_count(layer_url: str, fetch) -> int | None:
    """How many records the layer actually serves, or None if it won't say.

    Cheap: `returnCountOnly` asks the server for a number, not the data.
    """
    data = fetch(layer_url + "/query",
                 {"where": "1=1", "returnCountOnly": "true"})
    if not isinstance(data, dict):
        return None
    n = data.get("count")
    return int(n) if isinstance(n, (int, float)) else None


def size_adjustment(count: int | None) -> tuple[int, str]:
    """(score delta, note) for a layer of this size."""
    if count is None:
        return 0, "; size unknown"
    if count >= PLAUSIBLE_ROLL_MIN:
        return SIZE_BONUS, f"; {count:,} records"
    return (-SIZE_PENALTY,
            f"; ONLY {count:,} records - too small to be a full parcel roll, "
            f"probably a subset or study extract")


def named_for_other_city(layer_name: str, layer_url: str, city: str) -> bool:
    """True when a layer is titled after a DIFFERENT Hampton Roads city.

    The bbox sample can miss this - neighboring cities' boxes overlap along
    the border (VB's own AGOL org serves a ``Chesapeake_Norfolk_Streets_
    Parcels`` layer). The check itself lives in etl_munidata so a stale
    feeds_extra.json can't poison a pull either.
    """
    return _named_for_other_city(f"{layer_name} {layer_url}", city)


def sample_in_city(layer_url: str, city: str, fetch,
                   where: str = "1=1") -> bool | None:
    """Sample a few real records and check they sit inside the city's box.

    True = verified in-city; False = verified OUT of city (reject);
    None = could not verify (no coordinates in the sample - keep, but note).
    """
    bbox = CITY_BBOX.get(city)
    if bbox is None:
        return None
    data = fetch(f"{layer_url}/query", {
        "where": where, "outFields": "*", "resultRecordCount": 5,
        "returnGeometry": "true", "outSR": 4326}) or {}
    lat_min, lat_max, lng_min, lng_max = bbox
    inside = outside = 0
    for feat in (data.get("features") or []):
        geo = feat.get("geometry") or {}
        lat = geo.get("y")
        lng = geo.get("x")
        if lat is None or lng is None:
            rings = geo.get("rings") or []
            if rings and rings[0]:
                lng, lat = rings[0][0][0], rings[0][0][1]
        if lat is None or lng is None:
            continue
        if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
            inside += 1
        else:
            outside += 1
    if inside + outside == 0:
        return None
    return inside >= outside


def _get_json(url: str, params: dict | None = None) -> dict | None:
    import requests
    try:
        r = requests.get(url, params={**(params or {}), "f": "json"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _soda_get(url: str, params: dict | None = None):
    """Socrata fetch - NO forced f=json param (SODA treats unknown non-$
    params as column filters and 400s), and list responses are valid
    (resource endpoints return JSON arrays)."""
    import requests
    try:
        r = requests.get(url, params=params or {}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, (list, dict)) else None
    except Exception:
        return None


def walk_root(root: str, fetch=_get_json):
    """Yield (layer_url, layer_name, field_names) for every layer under an
    ArcGIS services directory (including one folder level)."""
    seen = 0
    top = fetch(root) or {}
    folders = [""] + list(top.get("folders") or [])
    for folder in folders:
        base = f"{root}/{folder}".rstrip("/")
        listing = top if folder == "" else (fetch(base) or {})
        for svc in (listing.get("services") or []):
            if seen >= MAX_SERVICES_PER_ROOT:
                return
            if svc.get("type") not in ("FeatureServer", "MapServer"):
                continue
            seen += 1
            name = svc.get("name", "")
            leaf = name.split("/")[-1]
            svc_url = f"{base}/{leaf}/{svc['type']}"
            info = fetch(svc_url) or {}
            for layer in (info.get("layers") or [])[:MAX_LAYERS_PER_SERVICE]:
                layer_url = f"{svc_url}/{layer.get('id', 0)}"
                detail = fetch(layer_url) or {}
                fields = [f.get("name", "") for f in (detail.get("fields") or [])]
                if fields:
                    yield layer_url, f"{leaf}/{layer.get('name', '')}", fields


def search_agol(city: str, fetch=_get_json):
    """Yield candidate service URLs from ArcGIS Online public search."""
    data = fetch(AGOL_SEARCH, {
        "q": f'{city} virginia parcels type:"Feature Service"',
        "num": 15}) or {}
    for item in (data.get("results") or []):
        url = (item.get("url") or "").rstrip("/")
        if url and "FeatureServer" in url:
            info = fetch(url) or {}
            for layer in (info.get("layers") or [])[:MAX_LAYERS_PER_SERVICE]:
                layer_url = f"{url}/{layer.get('id', 0)}"
                detail = fetch(layer_url) or {}
                fields = [f.get("name", "") for f in (detail.get("fields") or [])]
                if fields:
                    yield layer_url, item.get("title", ""), fields


# Cities whose GIS is NOT ArcGIS: their open-data portal is Socrata, so the
# ArcGIS walk finds nothing (Norfolk: "nothing suitable found" while its
# assessment roll lives on data.norfolk.gov without coordinates - a Socrata
# dataset WITH a location column is the fix).
# ---------------------------------------------------------------------------
# Statewide fallback - VGIN's Virginia parcel aggregate
# ---------------------------------------------------------------------------
# Hampton's own portal serves only CZM study extracts (~700 rows for a ~50K
# parcel city) and Suffolk's serves nothing at all - but the Commonwealth
# aggregates EVERY locality's parcels into one VGIN service. One layer,
# filtered per locality, is a full roll for any Virginia city whose own GIS
# fails us. Attributes are thinner than a city assessor roll (units rarely
# present), so it ranks BELOW any real city roll: parcels + APNs + geometry
# make address-point unit derivation and the verified-badge parcel check
# work, which is exactly what Hampton and Suffolk are missing.
VGIN_LAYER_CANDIDATES = (
    "https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
    "VA_Base_Layers/VA_Parcels/FeatureServer/0",
    "https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
    "VA_Base_layers/VA_Parcels/FeatureServer/0",
    "https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
    "VA_Base_Layers/VA_Parcels/MapServer/0",
)
# The locality column has gone by different names across VGIN publications;
# probe the layer's actual fields rather than assuming.
# FIPS-style fields first: name fields are ambiguous where the state has
# both a city and a county by the same name (Richmond, Roanoke, Fairfax...).
VGIN_LOCALITY_FIELDS = ("FIPS", "LOCFIPS", "LOCALITY", "LOCALITY_NAME",
                        "JURISDICTION", "LOCAL_NAME", "COUNTY")


def vgin_where_candidates(field: str, city: str) -> list[str]:
    """Filter clauses to try, most specific first. FIPS-style fields get the
    numeric codes; name fields get case variants."""
    from core.market_data import CITY_TO_COUNTY_FIPS_5
    fips5 = CITY_TO_COUNTY_FIPS_5.get(city, "")
    if "fips" in field.lower():
        vals = [fips5, fips5[2:].lstrip("0"), fips5[2:]]
        return [f"{field} = '{v}'" for v in vals if v] +                [f"{field} = {v}" for v in (fips5[2:].lstrip("0"),) if v]
    return [f"UPPER({field}) = '{city.upper()}'",
            f"{field} = '{city.upper()}'",
            f"{field} = '{city}'",
            f"UPPER({field}) = '{city.upper()} CITY'",
            f"UPPER({field}) LIKE '{city.upper()}%'"]


def vgin_fallback(city: str, fetch) -> dict | None:
    """A per-locality FeedSpec over the statewide parcel layer, or None.

    Verified the same three ways as any candidate: the filter must select a
    plausible-roll count, a sample must geo-verify inside the city's bbox,
    and the layer must expose a parcel id. Never trusted blind - a wrong
    filter here would ingest another locality under this city's FIPS.
    """
    for layer_url in VGIN_LAYER_CANDIDATES:
        detail = fetch(layer_url) or {}
        fields = [f.get("name", "") for f in (detail.get("fields") or [])]
        if not fields:
            continue
        score, mapped = score_fields(fields)
        if "apn" not in mapped:
            continue
        by_upper = {f.upper(): f for f in fields}
        loc_fields = [by_upper[c] for c in VGIN_LOCALITY_FIELDS
                      if c in by_upper]
        for loc_field in loc_fields:
            for where in vgin_where_candidates(loc_field, city):
                data = fetch(f"{layer_url}/query",
                             {"where": where, "returnCountOnly": "true"})
                count = (data or {}).get("count")
                if not isinstance(count, (int, float))                         or count < PLAUSIBLE_ROLL_MIN:
                    continue
                verdict = sample_in_city(layer_url, city, fetch, where=where)
                if verdict is False:
                    continue
                geo_note = "" if verdict else "; geo-verify inconclusive"
                return {
                    "market": city, "state": "VA", "county": city,
                    "kind": "assessor", "platform": "arcgis",
                    "url": layer_url, "status": "live", "where": where,
                    "record_count": int(count),
                    "note": (f"VGIN statewide fallback: {where}; "
                             f"{int(count):,} records; fields "
                             f"{sorted(mapped)}{geo_note}"),
                }
    return None


SOCRATA_PORTALS = {
    "Norfolk": "https://data.norfolk.gov",
    # Richmond's open-data portal (wave 1, spec 15).
    "Richmond": "https://data.richmondgov.com",
}
SOCRATA_QUERIES = ("parcel", "real estate", "property", "address")
_COORD_DATATYPES = ("point", "location")
_COORD_COLUMNS = ("latitude", "longitude", "location", "the_geom",
                  "geocoded_column", "point", "geolocation")


def search_socrata(city: str, soda=_soda_get):
    """Yield (resource_url, name, field_names, has_coords) from a city's
    Socrata catalog. Only cities in SOCRATA_PORTALS are probed."""
    portal = SOCRATA_PORTALS.get(city)
    if not portal:
        return
    domain = portal.split("//", 1)[-1].strip("/")
    seen: set[str] = set()
    for q in SOCRATA_QUERIES:
        # Socrata catalogs FEDERATE: without a domain restriction the search
        # returns datasets hosted on other portals (Norfolk's catalog served
        # up New York City's assessment roll, whose id then 404s on
        # data.norfolk.gov). Restrict the search AND verify each result's
        # home domain.
        data = soda(f"{portal}/api/catalog/v1",
                    {"q": q, "limit": 30, "only": "datasets",
                     "domains": domain, "search_context": domain})
        if not isinstance(data, dict):
            continue
        for item in (data.get("results") or []):
            res = (item or {}).get("resource") or {}
            home = str(((item or {}).get("metadata") or {})
                       .get("domain") or "").lower()
            if home and home != domain:
                continue
            rid = res.get("id")
            cols = res.get("columns_field_name") or []
            dtypes = [str(t).lower() for t in (res.get("columns_datatype") or [])]
            if not rid or rid in seen or not cols:
                continue
            seen.add(rid)
            has_coords = (
                any(t in _COORD_DATATYPES for t in dtypes)
                or any(str(c).lower() in _COORD_COLUMNS for c in cols))
            yield (f"{portal}/resource/{rid}.json",
                   res.get("name", ""), list(cols), has_coords)


def socrata_sample_in_city(resource_url: str, city: str, soda=_soda_get) -> bool | None:
    """Same contract as sample_in_city, over a SODA resource."""
    bbox = CITY_BBOX.get(city)
    if bbox is None:
        return None
    rows = soda(resource_url, {"$limit": 5})
    if not isinstance(rows, list):
        return None
    from core.phase0 import extract_dict_coords, _norm_key
    lat_min, lat_max, lng_min, lng_max = bbox
    inside = outside = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        lat = lng = None
        for v in row.values():
            c = extract_dict_coords(v)
            if c:
                lat, lng = c
                break
        if lat is None:
            scal = {_norm_key(k): v for k, v in row.items()
                    if not isinstance(v, (dict, list))}
            try:
                lat = float(scal.get("latitude") or scal.get("lat"))
                lng = float(scal.get("longitude") or scal.get("lng"))
            except (TypeError, ValueError):
                continue
        if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
            inside += 1
        else:
            outside += 1
    if inside + outside == 0:
        return None
    return inside >= outside


def discover(cities=TARGET_CITIES, extra_roots=(), fetch=_get_json,
             soda=_soda_get) -> dict[str, list[dict]]:
    """{city: [candidate FeedSpec dicts, best first]}"""
    out: dict[str, list[dict]] = {}
    for city in cities:
        candidates: list[tuple[int, dict]] = []
        roots = KNOWN_ROOTS.get(city, []) + list(extra_roots)
        sources = []
        for root in roots:
            sources.append(walk_root(root, fetch))
        sources.append(search_agol(city, fetch))
        seen_urls: set[str] = set()
        rejected: list[str] = []
        for source in sources:
            for layer_url, name, fields in source:
                if layer_url in seen_urls:
                    continue
                seen_urls.add(layer_url)
                score, mapped = score_fields(fields)
                if score < MIN_SCORE:
                    continue
                if named_for_other_city(name, layer_url, city):
                    rejected.append(f"{name}: layer is named for another city")
                    continue
                verdict = sample_in_city(layer_url, city, fetch)
                if verdict is False:
                    rejected.append(f"{name}: records are NOT in {city}")
                    continue
                geo_note = "" if verdict else "; geo-verify inconclusive"
                if "units" in mapped:
                    score += 3        # unit-bearing layers first, always
                count = layer_record_count(layer_url, fetch)
                delta, size_note = size_adjustment(count)
                score += delta
                if count is not None and count < PLAUSIBLE_ROLL_MIN:
                    rejected.append(
                        f"{name}: only {count:,} records - kept but demoted, "
                        f"a citywide roll should have >= {PLAUSIBLE_ROLL_MIN:,}")
                is_subset = subset_named(name, layer_url)
                if is_subset:
                    score -= SIZE_PENALTY
                    size_note += "; NAMED as a subset - demoted"
                    rejected.append(
                        f"{name}: layer name declares a subset - demoted, "
                        f"does not count as the city roll")
                candidates.append((score, {
                    "market": city, "state": "VA", "county": city,
                    "kind": "assessor", "platform": "arcgis",
                    "url": layer_url, "status": "live",
                    "record_count": count,
                    "fields_mapped": sorted(mapped),
                    "is_subset": is_subset,
                    "note": f"auto-discovered: {name}; score {score}; "
                            f"fields {sorted(mapped)}{geo_note}{size_note}",
                }))
        # Socrata portals (cities whose GIS is not ArcGIS at all)
        for res_url, name, cols, has_coords in search_socrata(city, soda):
            if res_url in seen_urls:
                continue
            seen_urls.add(res_url)
            score, mapped = score_fields(cols)
            if has_coords:
                score += 5            # a coordinate column is the point
                mapped.setdefault("lat", "(location column)")
            if score < MIN_SCORE:
                continue
            if named_for_other_city(name, res_url, city):
                rejected.append(f"{name}: layer is named for another city")
                continue
            verdict = socrata_sample_in_city(res_url, city, soda)
            if verdict is False:
                rejected.append(f"{name}: records are NOT in {city}")
                continue
            geo_note = "" if verdict else "; geo-verify inconclusive"
            if "units" in mapped:
                score += 3
            candidates.append((score, {
                "market": city, "state": "VA", "county": city,
                "kind": "assessor", "platform": "socrata",
                "url": res_url, "status": "live",
                "note": f"auto-discovered (socrata): {name}; score {score}; "
                        f"fields {sorted(mapped)}{geo_note}",
            }))
        candidates.sort(key=lambda t: -t[0])
        real_rolls = [c for _s, c in candidates
                      if (c.get("record_count") or 0) >= PLAUSIBLE_ROLL_MIN
                      and not c.get("is_subset")]
        if not real_rolls:
            vgin = vgin_fallback(city, fetch)
            if vgin is not None:
                # Below any real city roll, above every subset/extract.
                candidates.append((MIN_SCORE + 1, vgin))
                candidates.sort(key=lambda t: -t[0])
        out[city] = [spec for _s, spec in candidates[:2]]
        if real_rolls and not any("lat" in (c.get("fields_mapped") or [])
                                  for c in real_rolls):
            # The roll is real but coordinate-less (Portsmouth). The
            # statewide layer's geometry merges onto the same APNs, which
            # is what parity matching and the learner's anchors need.
            vgin = vgin_fallback(city, fetch)
            if vgin is not None:
                vgin["note"] = "geometry supplement; " + vgin["note"]
                out[city] = out[city] + [vgin]
        for r in rejected:
            print(f"   [rejected - wrong city] {city}: {r}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="*", default=[],
                    help="Extra ArcGIS service-directory roots to probe")
    args = ap.parse_args(argv)

    print("Probing city GIS portals for unit-bearing parcel layers...")
    print("(a few minutes - each city's services are walked layer by layer)")
    print()
    found = discover(extra_roots=args.roots)

    specs = [spec for lst in found.values() for spec in lst]
    for city in TARGET_CITIES:
        lst = found.get(city) or []
        if lst:
            print(f"{city}: {len(lst)} candidate layer(s)")
            for spec in lst:
                print(f"   {spec['url']}")
                print(f"      {spec['note']}")
        else:
            print(f"{city}: nothing suitable found (portal may be offline or "
                  "hides fields until queried; send me this output)")
        print()

    if specs:
        out_path = Path(__file__).resolve().parent.parent / "data" / "feeds_extra.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(specs, indent=2))
        print(f"Wrote {len(specs)} feed spec(s) to {out_path}")
        print("NEXT: double-click pull-muni.bat to ingest them, then run-phase0.bat.")
    else:
        print("No feeds written. Send me this output and I will widen the probes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
