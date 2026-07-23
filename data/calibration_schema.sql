-- Market Calibration storage. Lives alongside the ALN `properties` table in
-- workbench.db. Survives ALN re-syncs because aln_loader.sync() only drops
-- `properties`, not these tables.
--
-- Two tables:
--   calibration_current — one row per threshold, the latest applied state
--   calibration_history — append-only snapshot every time apply_calibration()
--                         runs; supports 30/90/365-day delta reporting in the UI

CREATE TABLE IF NOT EXISTS calibration_current (
    -- Threshold identity ------------------------------------------------------
    name              TEXT PRIMARY KEY,        -- e.g. "GO_CAP", "PPU_GO_NORFOLK"
    display_label     TEXT NOT NULL,           -- "GO Cap Rate", "Norfolk GO PPU"
    units             TEXT NOT NULL,           -- "pct" | "ratio" | "usd" | "x"
    direction         TEXT NOT NULL,           -- "conservative_up" | "conservative_down"
    category          TEXT NOT NULL,           -- "returns" | "debt" | "ppu" | "operating"

    -- Locked floor ------------------------------------------------------------
    -- The hardcoded value from config.py (Brian's ratified bar). Market
    -- data can only widen the threshold in the conservative direction from
    -- this floor; compression below the floor requires an explicit override.
    floor_value       REAL NOT NULL,

    -- Latest market-derived candidate value, may be NULL if compute failed --
    market_value      REAL,
    market_source     TEXT,                    -- "FRED DGS10 + 300 bps spread"
    market_as_of      TEXT,                    -- ISO date

    -- Explicit override (Brian-approved compression) -------------------------
    override_value    REAL,
    override_reason   TEXT,
    override_set_at   TEXT,                    -- ISO date
    override_set_by   TEXT,                    -- "brian"

    -- Resolved effective value (what consumers actually read) ----------------
    effective_value   REAL NOT NULL,
    effective_source  TEXT NOT NULL,           -- "floor" | "market" | "override"

    -- Audit ------------------------------------------------------------------
    last_compute_at   TEXT NOT NULL,
    last_apply_at     TEXT NOT NULL,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS ix_calibration_current_category
    ON calibration_current (category);


CREATE TABLE IF NOT EXISTS calibration_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    snapshot_at       TEXT NOT NULL,           -- ISO datetime
    market_value      REAL,
    effective_value   REAL NOT NULL,
    effective_source  TEXT NOT NULL,
    market_source     TEXT,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS ix_calibration_history_name_date
    ON calibration_history (name, snapshot_at);
