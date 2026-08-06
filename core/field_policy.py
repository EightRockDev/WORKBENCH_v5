"""Field governance — the machine-readable data dictionary (owner ask 2026-08-07).

Every property-card field is classified into exactly one tier, answering "who
may change this and who sees the change":

- ``reference`` — enterprise-locked. Platform-maintained facts from the shared
  reference layer (spec 10.1): the 8R backbone, assessor/muni records, public
  ETL, and values computed from them. Read-only to every org and user;
  corrections flow through the data pipeline, never through an edit box.
- ``org`` — organization-shared. Facts an org maintains about an asset that
  every colleague in that org should see (none by default in v1; fields get
  promoted here deliberately, with org-admin gating, when the owner calls it).
  Org *configuration* (buy-box, KPI targets) already lives on
  ``organizations.buy_box_config`` per spec 10.2 and is not a card field.
- ``user`` — personal working values. Saved to the editing user's profile and
  visible only to them (per-user RLS, same invariant as Module D inbox): one
  analyst's draft assumptions never silently become a colleague's facts.

The human-readable companion (full provenance per field) is
``docs/DATA-DICTIONARY.md``. The UI derives its editable set from THIS module —
one shared predicate, never a second copy of the list (CLAUDE.md rule).
"""

from __future__ import annotations

TIER_REFERENCE = "reference"
TIER_ORG = "org"
TIER_USER = "user"

# Property-card fields (ui/property_detail._PROPERTY_CARD_FIELDS) -> tier.
FIELD_TIERS: dict[str, str] = {
    # Enterprise-locked: auto-derived or computed; spec + owner 5/29 v2.0.22
    # already removed these from the edit form.
    "market":             TIER_REFERENCE,   # from city + Census/public data
    "submarket":          TIER_REFERENCE,   # from city + Census/public data
    "rent_per_sqft":      TIER_REFERENCE,   # computed: avg_rent / avg_sqft

    # Personal working values: each user's own view of the asset while they
    # work it. Overlay only — the backbone/auto-pulled value is never mutated.
    "units":              TIER_USER,
    "year_built":         TIER_USER,
    "last_remodel":       TIER_USER,
    "asset_class":        TIER_USER,
    "property_type":      TIER_USER,
    "occupancy_pct":      TIER_USER,
    "avg_sqft":           TIER_USER,
    "avg_rent":           TIER_USER,
    "owner":              TIER_USER,
    "manager":            TIER_USER,
    "management_company": TIER_USER,
    "pm_software":        TIER_USER,
    "asset_or_fee":       TIER_USER,
}

# Backbone (properties_8r) columns are reference-tier wholesale: apn, fips,
# address, city, state, zip, lat/lng, use_code, r8_form, assessed_value,
# owner_name (public record), est_avg_rent, provenance — plus muni sale
# history. Enumerated in docs/DATA-DICTIONARY.md; nothing in the UI edits them.


def tier_of(field: str) -> str:
    """Tier for a card field. Unknown fields are reference (locked) — a field
    someone forgot to classify must fail closed, not open."""
    return FIELD_TIERS.get(field, TIER_REFERENCE)


def user_editable_fields() -> set[str]:
    """Card fields a signed-in user may override for themselves."""
    return {f for f, t in FIELD_TIERS.items() if t == TIER_USER}


def org_editable_fields() -> set[str]:
    """Card fields an org admin may set org-wide (empty in v1 by design)."""
    return {f for f, t in FIELD_TIERS.items() if t == TIER_ORG}
