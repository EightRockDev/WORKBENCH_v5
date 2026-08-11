"""Human-facing link for a sale record's reporting source (owner ask
2026-08-11: "add links to the sales data so that user can click on a sale and
be taken to the website that's reported it").

Sale rows carry the MACHINE source_url they were pulled from (an API
endpoint, a synthetic stack tag, a portal item). This maps each to the page a
human should land on. Layered resolution, most specific first:

  1. Explicit overrides (stack tags, LandBook, VB's Esri layer).
  2. Generic transforms (any Socrata /resource/<id>.json -> that domain's
     /d/<id> human dataset page).
  3. Fallback: the source_url itself when it is browsable (an Esri REST layer
     URL renders a human services page), else None.

Never raises - a bad/missing source_url just means no link, never a broken
card.
"""

from __future__ import annotations

import re

# Machine source -> human page. Keys are matched as substrings of the stored
# source_url, checked in order (first hit wins).
_OVERRIDES: tuple[tuple[str, str], ...] = (
    # Virginia Beach Property_Sales_ Esri layer -> the city's dataset page.
    ("services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services/Property_Sales_",
     "https://gis.data.vbgov.com/datasets/1128db0f97374820830c4f97c5ddce6b_0/about"),
    # Norfolk FY snapshot stack -> the live FY dataset page.
    ("socrata-stack:data.norfolk.gov",
     "https://data.norfolk.gov/d/qva7-tzrf"),
    # Richmond transfer stack -> Property Transfer History dataset page.
    ("socrata-stack:data.richmondgov.com",
     "https://data.richmondgov.com/d/uxre-by3i"),
    # Chesapeake LandBook portal items -> the assessor's open-data page that
    # hosts the LandBook downloads.
    ("landbook:gis.cityofchesapeake.net",
     "https://www.cityofchesapeake.net/3409/Open-Data"),
    # Cook County (Chicago) parcel sales -> the dataset page.
    ("datacatalog.cookcountyil.gov/resource/wvhk-k5uv",
     "https://datacatalog.cookcountyil.gov/d/wvhk-k5uv"),
    # Spatialest API bases -> the locality's public portal.
    ("api.spatialest.com/v1/va/virginiabeach",
     "https://propertysearch.virginiabeach.gov/"),
    # Richmond assessor monthly files -> the Data Request page they live on.
    ("files:rva.gov/assessor-real-estate",
     "https://www.rva.gov/assessor-real-estate/data-request"),
)

_SOCRATA_RESOURCE = re.compile(
    r"^(https?://[^/]+)/resource/([a-z0-9]{4}-[a-z0-9]{4})\.json")
_SPATIALEST = re.compile(
    r"^https?://(?:api|community)\.spatialest\.com(?:/api)?/v1/([a-z]{2})/([a-z0-9-]+)")


def sale_source_link(source_url: str | None) -> str | None:
    """The human page for a sale row's reporting source, or None."""
    if not source_url:
        return None
    s = str(source_url).strip()
    for needle, human in _OVERRIDES:
        if needle in s:
            return human
    m = _SOCRATA_RESOURCE.match(s)
    if m:
        return f"{m.group(1)}/d/{m.group(2)}"
    m = _SPATIALEST.match(s)
    if m:
        return f"https://community.spatialest.com/{m.group(1)}/{m.group(2)}/"
    if s.startswith(("http://", "https://")):
        return s          # Esri REST layer URLs render a human services page
    return None
