# Hampton Roads ETL

Pulls eight free public data sources relevant to Class C multifamily
underwriting in Hampton Roads, writes everything to a single SQLite file
(`hampton_roads.db`) you can join with the ALN export in the parent workbench.

## Quick start

```
pip install -r requirements.txt
cp .env.example .env
# edit .env, add your free API keys (see below)
python hampton_roads_etl.py
```

The script is idempotent — re-running drops + recreates each table. One run
takes a few minutes (most of it is HMDA, which fetches a 3-year window).

## Sources

### Phase 1 — Macro + demographic context

| Source | Table(s) | Cadence | Key required | Sign-up |
|---|---|---|---|---|
| **Census ACS 5-year** | `census_acs` | annual | `CENSUS_API_KEY` | <https://api.census.gov/data/key_signup.html> |
| **BLS LAUS** (county unemployment, monthly) | `bls_laus` | monthly | `BLS_API_KEY` | <https://data.bls.gov/registrationEngine/> |
| **FRED** (10yr Treasury, mortgage rates, CPI, MSA HPI) | `fred_series` | daily/monthly | `FRED_API_KEY` | <https://fred.stlouisfed.org/docs/api/api_key.html> |
| **HUD FMR** (voucher rent ceilings) | `hud_fmr` | annual | `HUD_API_TOKEN` | <https://www.huduser.gov/portal/dataset/fmr-api.html> |

### Phase 2 — Supply, financing, military floor, LIHTC pipeline

| Source | Table(s) | Cadence | Key required |
|---|---|---|---|
| **Census BPS** (multifamily building permits by city) | `census_bps` | monthly | none |
| **FFIEC HMDA** (every multifamily loan origination, by lender) | `hmda_originations`, `hmda_lender_summary` | annual | none |
| **HUD LIHTC database** (every LIHTC project + compliance dates) | `hud_lihtc` | annual | none |
| **DoD BAH** (military housing allowance by paygrade × ZIP) | `bah_rates`, `bah_zip_mha` | annual | none |

## Hampton Roads scope

The seven independent cities (county FIPS in parentheses):
Norfolk (51710), Virginia Beach (51810), Chesapeake (51550), Portsmouth
(51740), Suffolk (51800), Hampton (51650), Newport News (51700).

MSA: Virginia Beach-Norfolk-Newport News, VA-NC (FIPS 47260).

## Why each source matters for Eight Rock

- **Census BPS** — supply pipeline 18-24 months out. HR has been supply-constrained for years; we want to know the moment that changes.
- **HMDA** — lender competitive intel. Tells us who's actually closing multifamily loans in Norfolk Class C right now, at what spreads. Drives debt sourcing.
- **HUD LIHTC** — deals coming off 15-yr compliance are some of the best off-market value-add ops in the state. Pipeline's scheduled years in advance.
- **BAH** — Norfolk has a meaningful military tenant base. BAH by paygrade × ZIP is a *floor* on what those tenants can pay. If in-place rents are below E-5-with-deps BAH, there's organic pricing power on turnover.
- **Census ACS** — population growth, median income, renter share. Tells us if the tenant base is growing or shrinking under our properties.
- **BLS LAUS** — rising unemployment is an early signal of rent collection risk and vacancy creep, especially in Class C.
- **FRED** — 10yr Treasury, mortgage rates, CPI shelter, HR-MSA HPI feed our exit-cap and rent-growth assumptions.
- **HUD FMR** — voucher rent ceilings; effective rent cap for the voucher-supported share of Class C tenancy.

## Validation queries

After a successful run, paste these into DB Browser for SQLite (or `sqlite3 hampton_roads.db`) to spot-check each table:

```sql
-- Row counts across all tables
SELECT 'census_acs' AS t, COUNT(*) FROM census_acs
UNION ALL SELECT 'bls_laus',           COUNT(*) FROM bls_laus
UNION ALL SELECT 'fred_series',        COUNT(*) FROM fred_series
UNION ALL SELECT 'hud_fmr',            COUNT(*) FROM hud_fmr
UNION ALL SELECT 'census_bps',         COUNT(*) FROM census_bps
UNION ALL SELECT 'hmda_originations',  COUNT(*) FROM hmda_originations
UNION ALL SELECT 'hmda_lender_summary',COUNT(*) FROM hmda_lender_summary
UNION ALL SELECT 'hud_lihtc',          COUNT(*) FROM hud_lihtc
UNION ALL SELECT 'bah_rates',          COUNT(*) FROM bah_rates
UNION ALL SELECT 'bah_zip_mha',        COUNT(*) FROM bah_zip_mha;

-- BAH for Norfolk MHA, current year, E-5 with deps
SELECT mha_code, mha_name, paygrade, monthly_rate, effective_year
FROM bah_rates
WHERE mha_code LIKE 'VA%' AND paygrade = 'E05' AND with_dependents = 1
ORDER BY effective_year DESC, mha_code;

-- 12-month rolling 5+ unit permits, Norfolk
SELECT year, month, units_5punit
FROM census_bps
WHERE place_name LIKE '%Norfolk%'
ORDER BY year DESC, month DESC
LIMIT 24;

-- LIHTC deals in Norfolk hitting initial compliance end in next 5 years
SELECT project_name, n_units, year_placed_in_service, initial_compliance_end
FROM hud_lihtc
WHERE city = 'Norfolk'
  AND initial_compliance_end BETWEEN
      CAST(strftime('%Y','now') AS INTEGER)
      AND CAST(strftime('%Y','now') AS INTEGER) + 5
ORDER BY initial_compliance_end;

-- Top 10 multifamily lenders in Norfolk last 12 months
SELECT lender_name, n_originations, total_loan_amount, median_rate_spread
FROM hmda_lender_summary
WHERE county_code = '51710'
  AND year = (SELECT MAX(year) FROM hmda_lender_summary)
ORDER BY total_loan_amount DESC
LIMIT 10;
```

## Known caveats

- **Government endpoint drift.** All four Phase 2 sources publish files at URLs that have shifted over the years. Each puller tries multiple known patterns; if all 404, the puller logs and the rest of the run continues. Worst case: open the puller, update the URL pattern, re-run.
- **Virginia Housing QAP** (the leading-indicator Phase 2 stretch goal) is not implemented — Virginia Housing's annual allocation list is published as PDF and Excel with format that changes year-to-year. Pull the latest by hand from <https://www.virginiahousing.com/partners/rental-housing/tax-credits> until we build a tolerant parser.
- **HMDA lag.** HMDA data has a 9-12 month lag — the most recent year may be partial. The puller still fetches it and labels accordingly.
- **BAH paygrades** use leading zeros (`E05`, not `E5`). Don't strip them.
- **OneDrive sync.** If you keep this project under OneDrive, exclude `hampton_roads.db` from sync — it gets large and rewrites entirely on each run.

## Next phases (per Brian's priority list, May 2026)

1. ~~Get the Hampton Roads ETL running~~ ← this repo
2. Add county-assessor scrapers (per-city Python scrapers; off-market sourcing)
3. Wire OM auto-extraction into the workbench (Claude Vision + structured-output)
4. Add Virginia Housing QAP parser (annual leading-indicator on subsidized supply)

## Out of scope

No Streamlit, no Airtable, no auth servers, no ML — this is a data layer
only. The parent workbench (in `../python_workbench/`) consumes
`hampton_roads.db` via SQLAlchemy or sqlite3 directly.
