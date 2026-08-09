"""HUD Fair Market Rents — Section 8 voucher rent ceilings by ZIP.

For Eight Rock: caps on what HUD will pay for Housing Choice voucher
tenants. A meaningful share of Class C tenancy is voucher-supported, so
FMR is effectively a rent ceiling for that tenant pool.

Source: HUD User API (https://www.huduser.gov/hudapi/public/fmr).
Requires `HUD_API_TOKEN` (free at https://www.huduser.gov/portal/dataset/fmr-api.html).
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import requests

from config import hr_county_fips_5

HUD_FMR_API = "https://www.huduser.gov/hudapi/public/fmr/data"


def pull_hud_fmr() -> pd.DataFrame:
    token = os.environ.get("HUD_API_TOKEN")
    if not token:
        print("  [hud_fmr] HUD_API_TOKEN not set — skipping")
        return pd.DataFrame()

    today = dt.date.today()
    year = today.year  # HUD typically updates FMR mid-year

    counties = hr_county_fips_5()
    rows: list[dict] = []

    for fips5 in counties:
        try:
            # Try a few entity-id formats — HUD's FMR API accepts both 5-digit
            # county FIPS and 10-digit "county+sub-county" codes, and the
            # response shape has shifted across versions.
            entity_candidates = [f"{fips5}99999", fips5]
            response_json = None
            for entity in entity_candidates:
                r = requests.get(
                    f"{HUD_FMR_API}/{entity}",
                    params={"year": year},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=60,
                )
                if r.status_code == 404:
                    # Older year fallback
                    r = requests.get(
                        f"{HUD_FMR_API}/{entity}",
                        params={"year": year - 1},
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=60,
                    )
                if r.status_code == 200:
                    response_json = r.json()
                    break
            if response_json is None:
                print(f"  [hud_fmr] {fips5} all entity formats 4xx'd")
                continue

            # HUD's response shape varies: sometimes {"data": {"basicdata": {...}, "year": Y}},
            # sometimes a list of those, sometimes the data object directly.
            data: dict = {}
            if isinstance(response_json, dict):
                data = response_json.get("data", response_json)
            elif isinstance(response_json, list) and response_json:
                first = response_json[0]
                if isinstance(first, dict):
                    data = first.get("data", first)

            # `basicdata` may be a dict (single entity) or a list of dicts (multi-county breakdown)
            basic = data.get("basicdata", {})
            if isinstance(basic, list):
                basic = basic[0] if basic else {}

            row = {
                "fips_county_5": fips5,
                "year": data.get("year", year),
                "fmr_efficiency": basic.get("Efficiency") or basic.get("efficiency"),
                "fmr_one_bedroom": basic.get("One-Bedroom") or basic.get("one-bedroom"),
                "fmr_two_bedroom": basic.get("Two-Bedroom") or basic.get("two-bedroom"),
                "fmr_three_bedroom": basic.get("Three-Bedroom") or basic.get("three-bedroom"),
                "fmr_four_bedroom": basic.get("Four-Bedroom") or basic.get("four-bedroom"),
            }
            rows.append(row)
        except (requests.RequestException, ValueError, KeyError, AttributeError) as e:
            print(f"  [hud_fmr] {fips5} failed: {type(e).__name__}: {e}")
            continue

    return pd.DataFrame(rows)
