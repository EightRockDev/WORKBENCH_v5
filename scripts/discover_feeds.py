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
                 "Suffolk", "Norfolk")

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
}

MAX_SERVICES_PER_ROOT = 200
MAX_LAYERS_PER_SERVICE = 25
TIMEOUT = 25

# Which spine fields make a layer valuable, and how much.
_FIELD_WEIGHTS = {"apn": 4, "units": 5, "use_code": 3, "year_built": 2,
                  "address": 2, "sqft": 1, "owner_name": 1,
                  "assessed_value": 1}
MIN_SCORE = 7      # needs apn + (units or use_code) at minimum


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


def named_for_other_city(layer_name: str, layer_url: str, city: str) -> bool:
    """True when a layer is titled after a DIFFERENT Hampton Roads city.

    The bbox sample can miss this - neighboring cities' boxes overlap along
    the border (VB's own AGOL org serves a ``Chesapeake_Norfolk_Streets_
    Parcels`` layer). The check itself lives in etl_munidata so a stale
    feeds_extra.json can't poison a pull either.
    """
    return _named_for_other_city(f"{layer_name} {layer_url}", city)


def sample_in_city(layer_url: str, city: str, fetch) -> bool | None:
    """Sample a few real records and check they sit inside the city's box.

    True = verified in-city; False = verified OUT of city (reject);
    None = could not verify (no coordinates in the sample - keep, but note).
    """
    bbox = CITY_BBOX.get(city)
    if bbox is None:
        return None
    data = fetch(f"{layer_url}/query", {
        "where": "1=1", "outFields": "*", "resultRecordCount": 5,
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


def discover(cities=TARGET_CITIES, extra_roots=(), fetch=_get_json) -> dict[str, list[dict]]:
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
                candidates.append((score, {
                    "market": city, "state": "VA", "county": city,
                    "kind": "assessor", "platform": "arcgis",
                    "url": layer_url, "status": "live",
                    "note": f"auto-discovered: {name}; score {score}; "
                            f"fields {sorted(mapped)}{geo_note}",
                }))
        candidates.sort(key=lambda t: -t[0])
        out[city] = [spec for _s, spec in candidates[:2]]
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
