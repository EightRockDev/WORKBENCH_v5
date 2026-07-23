-- ALN multi-state Property Export -> SQLite schema.
-- One row per property (deduped by property_id = ALN API Id UUID).
-- Indexed on city, state, asset_class, units, lat/lng for comp lookups.
-- Column shape matches the loader at data/aln_loader.py (SCHEMA_COLUMNS).

CREATE TABLE IF NOT EXISTS properties (
    property_id        TEXT PRIMARY KEY,    -- ALN "API Id" (UUID, stable)
    aln_id             TEXT,                -- ALN numeric Id (legacy lookups)
    name               TEXT NOT NULL,
    address            TEXT,
    city               TEXT,
    state              TEXT,
    zip                TEXT,
    county             TEXT,
    units              INTEGER,
    year_built         INTEGER,
    last_remodel       INTEGER,
    occupancy_pct      REAL,                -- fraction 0.0-1.0 (NOT 0-100)
    avg_sqft           REAL,
    avg_rent           REAL,                -- $/month
    rent_per_sqft      REAL,
    asset_class        TEXT,                -- 'A' | 'B' | 'C' | 'D'
    property_type      TEXT,                -- ALN building style (Garden/Mid-Rise/...)
    asset_type         TEXT,                -- 'Multifamily' default; precise tag if not
    property_segment   TEXT,                -- Conventional/Affordable/Senior/Student/Military
    market             TEXT,
    market_description TEXT,
    submarket          TEXT,
    latitude           REAL,
    longitude          REAL,
    owner              TEXT,
    owner_address      TEXT,
    owner_phone        TEXT,
    owner_fax          TEXT,
    manager            TEXT,
    area_supervisor    TEXT,
    management_company TEXT,
    corp_mgmt_id       TEXT,
    pm_software        TEXT,
    asset_or_fee       TEXT,                -- 'Asset' or 'Fee'
    lease_terms        TEXT,
    tags               TEXT,
    status             TEXT,
    property_phone     TEXT,
    website            TEXT,
    email              TEXT,
    last_sold_year     INTEGER,
    last_sold_amount   REAL,
    last_sold_per_unit REAL,
    assessed_value_per_unit REAL,
    source_file        TEXT,                -- which ALN export this row came from
    aln_pull_date      TEXT,                -- ISO date when sync ran
    raw_row            TEXT                 -- JSON dump of original ALN row
);

CREATE INDEX IF NOT EXISTS ix_properties_city        ON properties (city);
CREATE INDEX IF NOT EXISTS ix_properties_state       ON properties (state);
CREATE INDEX IF NOT EXISTS ix_properties_county      ON properties (county);
CREATE INDEX IF NOT EXISTS ix_properties_class       ON properties (asset_class);
CREATE INDEX IF NOT EXISTS ix_properties_market      ON properties (market);
CREATE INDEX IF NOT EXISTS ix_properties_units       ON properties (units);
CREATE INDEX IF NOT EXISTS ix_properties_class_city  ON properties (asset_class, city);
CREATE INDEX IF NOT EXISTS ix_properties_state_city  ON properties (state, city);
CREATE INDEX IF NOT EXISTS ix_properties_asset_type  ON properties (asset_type);
CREATE INDEX IF NOT EXISTS ix_properties_latlng      ON properties (latitude, longitude);
