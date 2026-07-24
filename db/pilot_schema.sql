-- ===========================================================================
-- Eight Rock Workbench v5.0 — Pilot / Multi-Tenant Schema (PostgreSQL 16)
-- ---------------------------------------------------------------------------
-- Implements the NEW data contracts introduced by the v5.0 spec:
--   * Section 9.4  Authentication & user administration (users, admin page)
--   * Section 9.3  Optimistic concurrency + soft locks (row_version, edit_locks)
--   * Section 10.2 Organization & user taxonomy (orgs, memberships, role_presets)
--   * Section 10.1 Tenancy model — shared reference layer, org-private deals (RLS)
--   * Section 4.5  poc_record data contract (Skip Trace / POC Intelligence)
--   * Section 8.1  Append-only audit log (SR-3.1)
--
-- This is ADDITIVE. The existing v2.4.1 underwriting/ETL tables migrate from
-- SQLite into this database during the V5-P0.5 cutover (Section 9.2); this file
-- stands up the tenancy, identity, concurrency and POC spine those tables plug
-- into. Everything here maps 1:1 onto the SaaS RLS schema (Module G) so nothing
-- is thrown away at the multi-tenancy cutover.
--
-- Apply:  psql "$DATABASE_URL" -f db/pilot_schema.sql
-- Idempotent: safe to re-run.
-- ===========================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- Reusable: row_version auto-increment (FR-9.3.1 optimistic concurrency).
-- Any org-private, user-editable table gets a `row_version integer` column and
-- this trigger. Saves run  UPDATE ... WHERE id=? AND row_version=?  so a stale
-- write updates zero rows and the app raises the conflict dialog (FR-9.3.2).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION bump_row_version() RETURNS trigger AS $$
BEGIN
    NEW.row_version := COALESCE(OLD.row_version, 0) + 1;
    NEW.updated_at  := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ===========================================================================
-- SECTION 10.2 — Organizations & user taxonomy
-- ===========================================================================

CREATE TABLE IF NOT EXISTS organizations (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name           text        NOT NULL,
    type           text        NOT NULL DEFAULT 'sponsor'
                     CHECK (type IN ('sponsor','pm_arm','construction_arm')),
    parent_org_id  uuid        REFERENCES organizations(id) ON DELETE SET NULL,
    plan_tier      text        NOT NULL DEFAULT 'solo'
                     CHECK (plan_tier IN ('solo','operator','firm')),
    buy_box_config jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- org-owned thresholds/KPIs (10.5)
    ai_enabled     boolean     NOT NULL DEFAULT true,          -- Section 11 per-org LLM flag
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- One identity per human, independent of org (Section 10.2 / 9.4.1).
CREATE TABLE IF NOT EXISTS users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    idp_sub       text UNIQUE NOT NULL,             -- provider subject id (Auth0/Entra)
    email         text UNIQUE NOT NULL,
    display_name  text,
    -- Platform-level role for the single-org pilot (9.4.1). Org-scoped rights
    -- live on memberships.role_preset below.
    platform_role text        NOT NULL DEFAULT 'trial'
                    CHECK (platform_role IN ('admin','internal','lp','trial')),
    status        text        NOT NULL DEFAULT 'invited'
                    CHECK (status IN ('invited','active','suspended')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login    timestamptz
);

-- Platform-maintained role library (10.3). The org admin only ever picks a key.
CREATE TABLE IF NOT EXISTS role_presets (
    key               text PRIMARY KEY,
    label             text  NOT NULL,
    maps_to           text,                         -- human aliases (10.3)
    module_grants     text[] NOT NULL DEFAULT '{}', -- 10.4 modules the preset can open
    field_mask        text[] NOT NULL DEFAULT '{}', -- 10.4 sensitive fields stripped server-side
    action_grants     text[] NOT NULL DEFAULT '{}', -- 10.4 gated verbs
    default_dashboard text,
    kpi_set           jsonb NOT NULL DEFAULT '{}'::jsonb,
    default_scope     text  NOT NULL DEFAULT 'org_all'
);

-- The permission-bearing row (10.2): a user may belong to several orgs with a
-- different preset in each. This row, not the user, carries the permissions.
CREATE TABLE IF NOT EXISTS memberships (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role_preset text NOT NULL REFERENCES role_presets(key),
    -- scope: org_all | portfolio:[ids] | deal:[ids] | own_only | single_deal:id
    scope       text NOT NULL DEFAULT 'org_all',
    status      text NOT NULL DEFAULT 'invited'
                  CHECK (status IN ('invited','active','suspended')),
    invited_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    expires_at  timestamptz,                        -- time-boxed guest access (10.3 Broker/Vendor)
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, org_id)
);
CREATE INDEX IF NOT EXISTS ix_memberships_org  ON memberships(org_id);
CREATE INDEX IF NOT EXISTS ix_memberships_user ON memberships(user_id);

-- ===========================================================================
-- SECTION 8.1 (SR-3.1) — Append-only audit log
-- ===========================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id            bigserial PRIMARY KEY,
    org_id        uuid REFERENCES organizations(id) ON DELETE SET NULL,
    actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    action        text NOT NULL,
    target        text,
    before        jsonb,
    after         jsonb,
    reason        text,
    ts            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_org_ts ON audit_log(org_id, ts DESC);
-- Append-only: block UPDATE/DELETE at the DB layer.
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (SR-3.1): % not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_log;
CREATE TRIGGER trg_audit_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

-- ===========================================================================
-- SECTION 9.3 — Presence / soft advisory locks (FR-9.3.3)
-- Short-lived, heartbeat-refreshed, auto-expiring TTL ~5 min. Never hard-blocks
-- read-only viewing; drives the "🔒 Jane is editing" banner.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS edit_locks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    record_kind text NOT NULL,                      -- 'property' | 'deal' | 'underwrite' | 'poc'
    record_id   text NOT NULL,
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL DEFAULT now() + interval '5 minutes',
    UNIQUE (org_id, record_kind, record_id)
);
CREATE INDEX IF NOT EXISTS ix_edit_locks_expiry ON edit_locks(expires_at);

-- ===========================================================================
-- SECTION 4.5 — poc_record (Skip Trace & POC Intelligence, org-private)
-- Core scalar columns are first-class; the nested arrays (entity_chain, phones,
-- emails, addresses, relatives, provenance, compliance) are JSONB, matching the
-- abridged contract. row_version + updated_at support optimistic concurrency.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS poc_records (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    property_id   text NOT NULL,                    -- 8R-{FIPS}-{parcel-hash} (Section 7.2)
    portfolio_id  text,
    role          text NOT NULL
                    CHECK (role IN ('owner','principal','pm','lender','agent','prior_owner')),
    person        jsonb NOT NULL DEFAULT '{}'::jsonb, -- { full_name, age_band, deceased }
    entity_chain  jsonb NOT NULL DEFAULT '[]'::jsonb, -- [{ entity_name, jurisdiction, filing_id, officers[], confidence }]
    phones        jsonb NOT NULL DEFAULT '[]'::jsonb, -- [{ e164, line_type, grade, dnc{}, callable, reason }]
    emails        jsonb NOT NULL DEFAULT '[]'::jsonb,
    addresses     jsonb NOT NULL DEFAULT '[]'::jsonb,
    relatives     jsonb NOT NULL DEFAULT '[]'::jsonb,
    other_properties jsonb NOT NULL DEFAULT '[]'::jsonb,
    provenance    jsonb NOT NULL DEFAULT '[]'::jsonb, -- [{ field, vendor, query_id, cost_usd, retrieved_at }] (FR-A7)
    compliance    jsonb NOT NULL DEFAULT '{}'::jsonb, -- { stamped_at, expires_at, revocations[] } (4.4)
    row_version   integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_poc_org_property ON poc_records(org_id, property_id);
CREATE INDEX IF NOT EXISTS ix_poc_portfolio    ON poc_records(org_id, portfolio_id);
DROP TRIGGER IF EXISTS trg_poc_rowver ON poc_records;
CREATE TRIGGER trg_poc_rowver BEFORE UPDATE ON poc_records
    FOR EACH ROW EXECUTE FUNCTION bump_row_version();

-- Per-tenant skip-trace spend telemetry + hard budget cap (FR-A5, AC-A4).
CREATE TABLE IF NOT EXISTS skiptrace_spend (
    id          bigserial PRIMARY KEY,
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    vendor      text NOT NULL,
    query_id    text,
    cost_usd    numeric(12,4) NOT NULL DEFAULT 0,
    poc_id      uuid REFERENCES poc_records(id) ON DELETE SET NULL,
    ts          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_spend_org_ts ON skiptrace_spend(org_id, ts DESC);

-- ===========================================================================
-- SECTION 10.1 — Row-Level Security: org-private tables isolated by org_id.
-- Cross-org read is impossible at the DB layer, not just hidden in the UI
-- (SR-2.1). The app sets  SET app.current_org_id = '<uuid>'  per request.
-- ===========================================================================
CREATE OR REPLACE FUNCTION current_org_id() RETURNS uuid AS $$
    SELECT NULLIF(current_setting('app.current_org_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

DO $$
DECLARE t text;
BEGIN
    -- Org-PRIVATE DATA tables: cross-org read is impossible at the DB layer.
    FOREACH t IN ARRAY ARRAY['poc_records','edit_locks','skiptrace_spend']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %I', t);
        EXECUTE format(
          'CREATE POLICY org_isolation ON %I USING (org_id = current_org_id()) '
          'WITH CHECK (org_id = current_org_id())', t);
    END LOOP;

    -- memberships is a CONTROL-PLANE table (user<->org<->role mapping). It must
    -- be readable before an org context exists (to resolve which org a user
    -- belongs to at login), so it is NOT under org_isolation RLS; the
    -- application layer (core/orgs.py) governs who may read/write it. Explicitly
    -- clear any policy from an earlier schema version so re-apply is clean.
    EXECUTE 'DROP POLICY IF EXISTS org_isolation ON memberships';
    EXECUTE 'ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE memberships DISABLE ROW LEVEL SECURITY';
END $$;

COMMIT;

-- ===========================================================================
-- Seed: the curated role-preset library (Section 10.3). Platform-maintained;
-- the admin only ever picks a key. field_mask entries are stripped server-side
-- (10.4) so a masked field never leaves the API.
-- ===========================================================================
INSERT INTO role_presets (key,label,maps_to,module_grants,field_mask,action_grants,default_scope) VALUES
 ('principal','Principal / Owner','Principal, Managing Partner, CEO, GP',
   '{underwriting,comps,granite,ops,rent_roll,capex,accounting,waterfall,lp_portal,documents,outreach,skip_trace,admin}',
   '{}',
   '{advance_stage,commit_go_nogo,edit_underwriting,edit_actuals,edit_waterfall,approve_distribution,manage_users,invite_guest,run_skiptrace,send_outreach}',
   'org_all'),
 ('president_coo','President / COO','President, COO, Chief of Staff',
   '{underwriting,comps,granite,ops,rent_roll,capex,accounting,waterfall,lp_portal,documents,outreach,skip_trace}',
   '{}','{advance_stage,edit_underwriting,edit_actuals,run_skiptrace,send_outreach}','org_all'),
 ('head_acq','Head of Acquisitions','CIO, VP/Director Acquisitions',
   '{underwriting,comps,granite,documents,outreach,skip_trace}',
   '{}','{edit_underwriting,advance_stage,run_skiptrace,send_outreach}','org_all'),
 ('analyst','Analyst / Associate','Acquisitions Analyst/Associate',
   '{underwriting,comps,granite,documents,skip_trace}',
   '{lp_pii}','{edit_underwriting,run_skiptrace}','org_all'),
 ('capital_markets','Capital Markets / Debt','Capital Markets, VP Corp Finance',
   '{underwriting,waterfall,documents}','{}','{edit_underwriting}','org_all'),
 ('asset_manager','Asset Manager','Dir/VP Asset Mgmt, Portfolio Mgr',
   '{ops,accounting,capex,documents}','{}','{edit_actuals}','portfolio'),
 ('regional_ops','Regional / Ops Manager','Regional Manager, VP Operations',
   '{ops,rent_roll,accounting}','{purchase_price,returns_irr,waterfall_promote}','{edit_actuals}','portfolio'),
 ('property_manager','Property Manager','Property Manager, Assistant PM',
   '{ops,rent_roll}','{purchase_price,returns_irr,waterfall_promote,lp_pii,debt_terms}','{}','deal'),
 ('leasing_agent','Leasing Agent','Leasing Consultant/Agent',
   '{rent_roll}','{purchase_price,returns_irr,waterfall_promote,lp_pii,debt_terms}','{}','deal'),
 ('maintenance','Maintenance','Maint. Supervisor, Technician, Custodian',
   '{ops}','{purchase_price,returns_irr,waterfall_promote,lp_pii,debt_terms}','{}','deal'),
 ('construction','Construction / CapEx','Pres./VP Construction, Reno PM, Estimator',
   '{capex,ops}','{purchase_price,waterfall_promote,lp_pii}','{}','portfolio'),
 ('controller','Controller / CFO','CFO, Controller, CAO, Treasury',
   '{accounting,waterfall,ops,documents}','{}','{edit_actuals,edit_waterfall,approve_distribution}','org_all'),
 ('bookkeeper','Bookkeeper / AP-AR','Property/Staff Accountant, AP Clerk',
   '{accounting}','{waterfall_promote,lp_pii}','{edit_actuals}','org_all'),
 ('investor_relations','Investor Relations','Director Investor Relations',
   '{lp_portal,accounting}','{}','{}','org_all'),
 ('exec_assistant','Executive Assistant','EA, Office Manager, Admin Assistant',
   '{documents}','{purchase_price,returns_irr,waterfall_promote,lp_pii,debt_terms}','{}','org_all'),
 ('it_admin','Platform / IT Admin','CTO, IT, IT-delegate',
   '{admin}','{purchase_price,returns_irr,waterfall_promote,lp_pii,debt_terms}','{manage_users}','org_all'),
 ('lp_investor','LP Investor (external)','Limited Partner',
   '{lp_portal}','{waterfall_promote,purchase_price,debt_terms}','{}','own_only'),
 ('guest','Broker / Vendor / Guest (external)','Broker, GC, appraiser, lender',
   '{documents}','{purchase_price,returns_irr,waterfall_promote,lp_pii,debt_terms}','{}','single_deal')
ON CONFLICT (key) DO UPDATE SET
   label=EXCLUDED.label, maps_to=EXCLUDED.maps_to, module_grants=EXCLUDED.module_grants,
   field_mask=EXCLUDED.field_mask, action_grants=EXCLUDED.action_grants,
   default_scope=EXCLUDED.default_scope;

-- ===========================================================================
-- MODULE A §4.4 COMPLIANCE GATE (C1-C7) + MODULE B §5 OUTREACH
-- ---------------------------------------------------------------------------
-- Defensive architecture: federal TCPA exposure is $500-$1,500 per call/text,
-- uncapped; state mini-TCPAs add $5K-$11K. These tables make the compliant path
-- the only path the software allows.
-- ===========================================================================

BEGIN;

-- C5 CONSENT LEDGER — prior express written consent, required before any
-- prerecorded/AI voice or ringless voicemail to a cell, and before SMS (C3).
CREATE TABLE IF NOT EXISTS consent_records (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    e164         text NOT NULL,
    channel      text NOT NULL CHECK (channel IN ('voice','sms','email','all')),
    consent_kind text NOT NULL DEFAULT 'express_written'
                   CHECK (consent_kind IN ('express_written','express_oral','inquiry')),
    evidence     text,                       -- how/where consent was captured
    captured_at  timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,                -- NULL = no expiry
    revoked_at   timestamptz,                -- set by a revocation (C5)
    created_by   uuid REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_consent_org_phone ON consent_records(org_id, e164);

-- C5 REVOCATION LEDGER — opt-out via ANY channel, honored across ALL channels.
-- Propagates to the tenant internal DNC list immediately.
CREATE TABLE IF NOT EXISTS revocations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    e164        text,
    email       text,
    scope       text NOT NULL DEFAULT 'all' CHECK (scope IN ('all','voice','sms','email','mail')),
    source      text,                        -- 'inbound_call','sms_stop','email_unsub',...
    received_at timestamptz NOT NULL DEFAULT now(),
    honored_at  timestamptz NOT NULL DEFAULT now(),   -- immediate; FCC allows <=10 business days
    note        text,
    CHECK (e164 IS NOT NULL OR email IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_revoke_org_phone ON revocations(org_id, e164);
CREATE INDEX IF NOT EXISTS ix_revoke_org_email ON revocations(org_id, email);

-- C1 INTERNAL DO-NOT-CALL LEDGER — retained 5 years per FTC rule.
CREATE TABLE IF NOT EXISTS internal_dnc (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    e164       text NOT NULL,
    reason     text,
    added_at   timestamptz NOT NULL DEFAULT now(),
    retain_until timestamptz NOT NULL DEFAULT now() + interval '5 years',
    UNIQUE (org_id, e164)
);

-- C1 DNC SCRUB RUNS — federal + six state registries (IN, LA, MO, PA, TX, WY).
-- A scrub is valid 31 days; campaign start auto re-scrubs on expiry.
CREATE TABLE IF NOT EXISTS dnc_scrubs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    e164        text NOT NULL,
    federal     boolean NOT NULL DEFAULT false,
    states      text[] NOT NULL DEFAULT '{}',
    litigator   boolean NOT NULL DEFAULT false,
    vendor      text,
    scrubbed_at timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL DEFAULT now() + interval '31 days'
);
CREATE INDEX IF NOT EXISTS ix_scrub_org_phone ON dnc_scrubs(org_id, e164, scrubbed_at DESC);

-- §5 / AC-B2 OUTREACH TOUCH LOG — 100% of outbound touches logged with channel,
-- timestamp, the rule-evaluation trace (which compliance checks passed), and
-- outcome. Append-only; audit-exportable.
CREATE TABLE IF NOT EXISTS outreach_touches (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    property_id  text,
    poc_id       uuid REFERENCES poc_records(id) ON DELETE SET NULL,
    person_name  text,
    channel      text NOT NULL CHECK (channel IN ('call','voicemail','sms','email','mail')),
    subtype      text,                       -- 'manual_dial','prerecorded','rvm','letter',...
    e164         text,
    email        text,
    allowed      boolean NOT NULL,           -- did the gate permit it?
    rule_trace   jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{rule,passed,detail}] (AC-B2)
    outcome      text,                       -- 'connected','voicemail','no_answer','sent',...
    campaign_id  uuid,
    actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    ts           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_touch_org_ts    ON outreach_touches(org_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_touch_org_phone ON outreach_touches(org_id, e164, ts DESC);
CREATE OR REPLACE FUNCTION touches_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'outreach_touches is append-only (AC-B2): % not permitted', TG_OP; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_touch_immutable ON outreach_touches;
CREATE TRIGGER trg_touch_immutable BEFORE UPDATE OR DELETE ON outreach_touches
    FOR EACH ROW EXECUTE FUNCTION touches_immutable();

-- §5 B4 CAMPAIGNS / cadence orchestration
CREATE TABLE IF NOT EXISTS campaigns (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name       text NOT NULL,
    cadence    jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{step,channel,offset_days}]
    status     text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','running','paused','done')),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- §5 B5 RELATIONSHIP GRAPH — touches/responses/referrals accumulate per org.
CREATE TABLE IF NOT EXISTS relationship_edges (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    from_kind  text NOT NULL,   -- 'user','poc','lender','broker'
    from_id    text NOT NULL,
    to_kind    text NOT NULL,
    to_id      text NOT NULL,
    edge       text NOT NULL,   -- 'contacted','responded','referred','closed_with'
    weight     numeric(8,2) NOT NULL DEFAULT 1,
    last_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_edges_org ON relationship_edges(org_id, from_id);

COMMIT;

-- RLS for the new org-private tables (§10.1 — cross-org read impossible at DB layer)
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['consent_records','revocations','internal_dnc','dnc_scrubs',
                             'outreach_touches','campaigns','relationship_edges']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %I', t);
        EXECUTE format('CREATE POLICY org_isolation ON %I USING (org_id = current_org_id()) '
                       'WITH CHECK (org_id = current_org_id())', t);
    END LOOP;
END $$;
