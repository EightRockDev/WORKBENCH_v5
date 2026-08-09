"""Shared constants for the Hampton Roads ETL pipeline.

The seven Hampton Roads independent cities — Eight Rock's primary target market.
FIPS codes verified against Virginia code 51 (state).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    name: str
    fips_county: str   # 3-digit county FIPS (Virginia independent cities use county FIPS)
    fips_place: str    # 5-digit place FIPS used by Census BPS
    msa: str = "Virginia Beach-Norfolk-Newport News, VA-NC MSA"


# The seven Hampton Roads independent cities.
# Virginia FIPS county codes for independent cities are documented at:
# https://www.census.gov/library/reference/code-lists/ansi.html
HAMPTON_ROADS: tuple[Place, ...] = (
    Place("Norfolk",        "710", "57000"),
    Place("Virginia Beach", "810", "82000"),
    Place("Chesapeake",     "550", "16000"),
    Place("Portsmouth",     "740", "64000"),
    Place("Suffolk",        "800", "76432"),
    Place("Hampton",        "650", "35000"),
    Place("Newport News",   "700", "56000"),
)

# Convenience: 5-digit FIPS county codes (state + county) — used by HMDA, ACS, etc.
def hr_county_fips_5() -> tuple[str, ...]:
    return tuple(f"51{p.fips_county}" for p in HAMPTON_ROADS)


# Hampton Roads MSA FIPS code (47260, the "Virginia Beach-Norfolk-Newport News" MSA)
HR_MSA_FIPS = "47260"

# Default DB path — sibling of this file
import pathlib
DB_PATH = pathlib.Path(__file__).resolve().parent / "hampton_roads.db"
