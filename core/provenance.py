"""Source-provenance color system.

Every value in the workbench has a provenance — where it came from. This
module is the single source of truth for how that's rendered visually so
every tab uses the same color key. Brian can glance at any tile and know
whether it's live (rent roll) vs. stale (property record) vs. typed (user input).

Color key (config.COLORS):
  🟢 src_rr      — Rent Roll (live)
  🟠 src_t12     — T-12 financials
  🟡 src_8r     — Eight Rock self-sourced backbone (may be stale)
  🟣 src_etl     — Public ETL data (BLS, FRED, HUD, Census, DoD, FFIEC)
  🥇 src_user    — User input / dial values
  🔵 src_calc    — Computed / derived
  ⚪ src_unknown — Unknown / TBD
"""

from __future__ import annotations

from typing import Literal

import config

ProvenanceKey = Literal["rr", "t12", "8r", "etl", "user", "calc",
                        "unknown"]

# Retired provenance keys -> their replacement. The vendor-survey tier was
# folded into the self-sourced backbone in the Phase-0 de-identification;
# stored records and deal.json files written before that still carry the old
# key, so every lookup normalizes through here rather than raising.
_RETIRED: dict[str, ProvenanceKey] = {"aln": "8r"}


def canonical(key: str) -> ProvenanceKey:
    """Map any historical provenance key onto a live one."""
    k = str(key or "").lower()
    return _RETIRED.get(k, k)  # type: ignore[return-value]

# Friendly display labels for each provenance type
_LABELS: dict[ProvenanceKey, str] = {
    "rr":      "Rent Roll",
    "t12":     "T-12",
    "8r":      "8R Backbone",
    "etl":     "Public Data (ETL)",
    "user":    "User Input",
    "calc":    "Computed",
    "unknown": "Unknown",
}

# Short descriptions used in the Data Source Key expander
_DESCRIPTIONS: dict[ProvenanceKey, str] = {
    "rr": (
        "Live rent roll uploaded by the analyst. Most current snapshot of "
        "who pays what, who's vacant, who's on notice. Gold standard."
    ),
    "t12": (
        "Trailing 12-month financial statement from the seller (or analyst-"
        "built from owner-provided data). Best estimate of stabilized "
        "operating performance."
    ),
    "8r": (
        "Eight Rock's self-sourced property backbone: municipal assessor "
        "and parcel records, permits, and HUD/listings rent signal, "
        "rebuilt nightly. Replaces the vendor survey per the Phase-0 "
        "cutover plan."
    ),
    "etl": (
        "Public-sector data refreshed by `hampton-roads-etl/`. "
        "Sources: BLS LAUS (unemployment), FRED (10Y/30Y/HPI), HUD FMR + "
        "LIHTC, Census BPS + ACS, DoD BAH, FFIEC HMDA. See the Data Sources "
        "& Last Refresh expander on the Comps tab for per-source timestamps."
    ),
    "user": (
        "Typed by the analyst — a custom property entry, a dial setting, "
        "or a manual override. Stored in the property's deal.json or "
        "_custom_props.json."
    ),
    "calc": (
        "Computed by the workbench from other inputs (cap rate, IRR, equity "
        "multiple, DSCR, etc.). Recompute live every time a dial changes."
    ),
    "unknown": (
        "Value not yet entered or source not recorded."
    ),
}


def color_for(key: ProvenanceKey) -> str:
    """Return the hex color string for a provenance type."""
    k = canonical(key)
    return config.COLORS.get(f"src_{k}", config.COLORS["src_unknown"])


def label_for(key: ProvenanceKey) -> str:
    return _LABELS.get(canonical(key), "Unknown")


def description_for(key: ProvenanceKey) -> str:
    return _DESCRIPTIONS.get(canonical(key), "")


def all_keys() -> list[ProvenanceKey]:
    return ["rr", "t12", "8r", "etl", "user", "calc", "unknown"]
