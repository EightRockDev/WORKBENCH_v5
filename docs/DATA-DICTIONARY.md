# Data Dictionary — field governance (v1, 2026-08-07)

Who may change each field, and who sees the change. Three tiers, enforced by
`core/field_policy.py` (machine-readable single source — the edit form derives
its field set from it; re-tiering a field is a one-line change there, mirrored
here in the same commit).

| Tier | Meaning | Storage | Who edits | Who sees |
|---|---|---|---|---|
| **reference** | Enterprise-locked platform facts (spec §10.1 shared reference layer) | `workbench.db` backbone + muni/public ETL; rebuilt nightly | Nobody — corrections flow through the data pipeline | Everyone (same for all orgs) |
| **org** | Organization-shared working facts | Postgres, org-level RLS | Org roles per preset (§10.3) | Everyone in the org |
| **user** | Personal working values ("my view of this asset") | Postgres `user_property_overrides`, **per-user RLS** | The individual user | **Only that user** |

## Property Card fields

| Field | Tier | Source when not overridden |
|---|---|---|
| Market | reference | City + Census/public data (auto) |
| Submarket | reference | City + Census/public data (auto) |
| Rent / Sqft | reference | Computed: Avg Rent ÷ Avg Sqft |
| Units | user | Rent roll → T-12 → OM → backbone |
| Year Built | user | Backbone (assessor) |
| Last Remodel | user | Documents |
| Class | user | Documents / analyst |
| Type | user | Documents / analyst (dropdown) |
| Occupancy | user | Rent roll → T-12 → OM |
| Avg Sqft | user | Rent roll → OM |
| Avg Rent | user | Rent roll → listings → HUD-FMR blend |
| Owner | user | Backbone (public record) |
| Manager (person) | user | Analyst |
| Mgmt Company | user | Analyst |
| PM Software | user | Analyst (dropdown) |
| Asset/Fee | user | Analyst |

v1 stance: every hand-editable card field is **user-tier** — an analyst's
manual value is a draft assumption, not a house fact. No field is org-tier
yet; promote a field (e.g. Mgmt Company, if the org decides it's shared
knowledge) by moving it in `core/field_policy.py` — the org tier's storage
and role gating ride the existing §10 machinery when first used.

## Backbone (`properties_8r`) — all reference-tier

`property_id, fips, apn, address, city, state, zip, units, year_built, sqft,
use_code, r8_form, r8_market, r8_submarket, assessed_value, owner_name,
lat, lng, est_avg_rent, rent_source, provenance` — plus municipal sale
history (`muni_records`) and everything in the public-data ETL. Global,
org-blind, read-only; rebuilt by the nightly autopilot. An org or user who
disagrees with a backbone value overrides it at their tier; the platform
value is never mutated.

## Org-level configuration (not card fields)

Buy-box thresholds, KPI targets, saved views: `organizations.buy_box_config`
(spec §10.2), org-admin edited, org-visible. Role presets and `field_mask`
per §10.3/§10.4 govern which tiers a role can even see.

## Override resolution order (what a user sees)

1. Their own `user_property_overrides` row (if they ever saved edits —
   `{}` after "Reset" means *their* explicit "use auto everywhere").
2. Legacy shared `property_card_overrides.json` in the deal folder
   (pre-multi-user entries; also the fallback store in ungated dev mode).
3. Auto-pulled: rent roll → T-12 → OM → backbone record.
