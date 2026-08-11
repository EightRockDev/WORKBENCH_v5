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

-- ===========================================================================
-- MODULE D §6.2 — INBOX -> DEAL ENGINE
-- ---------------------------------------------------------------------------
-- Classify inbound broker/lender/attorney mail, extract deal facts, and
-- auto-create/update pipeline records with ZERO manual entry. Confidence-gated:
-- below-threshold extractions queue for one-click human confirm rather than
-- silently writing (spec §6.2).
-- ===========================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS inbox_messages (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    provider      text NOT NULL DEFAULT 'mock',     -- 'graph' | 'gmail' | 'mock'
    external_id   text NOT NULL,                    -- provider message id (idempotency)
    from_email    text,
    from_name     text,
    subject       text,
    body          text,
    received_at   timestamptz NOT NULL DEFAULT now(),
    attachments   jsonb NOT NULL DEFAULT '[]'::jsonb,
    category      text,                             -- broker|lender|attorney|lp|other
    confidence    numeric(4,3) NOT NULL DEFAULT 0,
    classifier    text NOT NULL DEFAULT 'deterministic',   -- or 'ai'
    status        text NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','auto_applied','queued','confirmed','dismissed')),
    extracted     jsonb NOT NULL DEFAULT '{}'::jsonb,
    deal_id       uuid,
    ingested_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, provider, external_id)
);
CREATE INDEX IF NOT EXISTS ix_inbox_org_status ON inbox_messages(org_id, status, received_at DESC);

-- Pipeline records auto-created/updated from inbound mail (§6.2).
CREATE TABLE IF NOT EXISTS deals (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    property_id   text,
    name          text NOT NULL,
    address       text,
    city          text,
    state         text,
    units         integer,
    asking_price  numeric(14,2),
    cap_rate      numeric(6,4),
    stage         text NOT NULL DEFAULT 'lead'
                    CHECK (stage IN ('lead','screening','loi','under_contract','closed','dead','no_go')),
    source        text,                             -- 'inbox','manual','radar'
    broker_email  text,
    row_version   integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_deals_org_stage ON deals(org_id, stage);
DROP TRIGGER IF EXISTS trg_deals_rowver ON deals;
CREATE TRIGGER trg_deals_rowver BEFORE UPDATE ON deals
    FOR EACH ROW EXECUTE FUNCTION bump_row_version();

-- Term-sheet history captured from lender mail (§6.2).
CREATE TABLE IF NOT EXISTS term_sheets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    deal_id     uuid REFERENCES deals(id) ON DELETE CASCADE,
    message_id  uuid REFERENCES inbox_messages(id) ON DELETE SET NULL,
    lender      text,
    rate        numeric(6,4),
    ltv         numeric(5,4),
    amort_years integer,
    io_years    integer,
    term_years  integer,
    proceeds    numeric(14,2),
    received_at timestamptz NOT NULL DEFAULT now(),
    raw         jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_terms_org_deal ON term_sheets(org_id, deal_id, received_at DESC);

-- CRM contacts accumulated from inbound mail; feeds the Module B graph (B5).
CREATE TABLE IF NOT EXISTS crm_contacts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email       text NOT NULL,
    name        text,
    company     text,
    role        text,                               -- broker|lender|attorney|lp|other
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    message_count integer NOT NULL DEFAULT 1,
    UNIQUE (org_id, email)
);

COMMIT;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['inbox_messages','deals','term_sheets','crm_contacts']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %I', t);
        EXECUTE format('CREATE POLICY org_isolation ON %I USING (org_id = current_org_id()) '
                       'WITH CHECK (org_id = current_org_id())', t);
    END LOOP;
END $$;

-- ===========================================================================
-- MODULE D — PER-USER MAILBOX PRIVACY  (owner request, 2026-07-24)
-- ---------------------------------------------------------------------------
-- Security model: **private mailbox, shared pipeline.**
--   * A connected mailbox belongs to ONE user. Raw messages are visible only to
--     that user - not to colleagues in the same org, not to org admins.
--   * The DEALS / term sheets / contacts extracted from those messages ARE
--     org-visible: that is the point of the module (pipeline is shared work).
-- Enforced at the database layer with RLS keyed to app.current_user_id, so a
-- missing user context fails CLOSED (no rows) rather than leaking.
-- ===========================================================================

BEGIN;

CREATE OR REPLACE FUNCTION current_user_id() RETURNS uuid AS $$
    SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

-- Per-user OAuth mailbox connections. Tokens are stored ENCRYPTED by the
-- application (Fernet, key in ER_TOKEN_KEY) - the DB never sees plaintext.
CREATE TABLE IF NOT EXISTS mailbox_connections (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider       text NOT NULL CHECK (provider IN ('graph','gmail')),
    account_email  text,
    access_token   text,          -- encrypted blob
    refresh_token  text,          -- encrypted blob
    expires_at     timestamptz,
    scopes         text,
    status         text NOT NULL DEFAULT 'connected'
                     CHECK (status IN ('connected','expired','revoked')),
    last_sync_at   timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider)
);
CREATE INDEX IF NOT EXISTS ix_mailbox_user ON mailbox_connections(user_id);

-- Messages become owned by the connecting user.
ALTER TABLE inbox_messages ADD COLUMN IF NOT EXISTS owner_user_id uuid
    REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_inbox_owner ON inbox_messages(owner_user_id, received_at DESC);

-- One term sheet per source message (fixes duplicate rows on repeated sync).
--
-- The dedupe MUST bypass RLS. term_sheets is FORCE ROW LEVEL SECURITY, and a
-- migration has no tenant context (app.current_org_id is unset), so
-- current_org_id() is NULL and a plain DELETE matches ZERO rows and silently
-- no-ops. CREATE UNIQUE INDEX, by contrast, is NOT RLS-filtered: it sees every
-- row and fails with "duplicate keys exist" on databases that accumulated
-- duplicates before this index existed. Toggling FORCE off around the DELETE is
-- the fix; the migration role owns the table and FORCE is restored immediately.
-- If the block raises, the whole DO rolls back, so RLS can never be left off.
DO $$
BEGIN
    ALTER TABLE term_sheets NO FORCE ROW LEVEL SECURITY;
    DELETE FROM term_sheets a USING term_sheets b
     WHERE a.message_id IS NOT NULL AND a.message_id = b.message_id AND a.id > b.id;
    ALTER TABLE term_sheets FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN insufficient_privilege THEN
    -- Not the table owner: leave RLS exactly as it was. The index creation
    -- below will then report the duplicates loudly rather than hiding them.
    NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS ux_term_sheets_message
    ON term_sheets(message_id) WHERE message_id IS NOT NULL;

COMMIT;

-- Strict per-user RLS on raw mail + mailbox connections; fails closed when the
-- user context is unset. Deals/term_sheets/crm_contacts keep ORG-level RLS.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['inbox_messages','mailbox_connections']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS user_isolation ON %I', t);
    END LOOP;
    EXECUTE 'CREATE POLICY user_isolation ON inbox_messages '
            'USING (org_id = current_org_id() AND owner_user_id = current_user_id()) '
            'WITH CHECK (org_id = current_org_id() AND owner_user_id = current_user_id())';
    EXECUTE 'CREATE POLICY user_isolation ON mailbox_connections '
            'USING (org_id = current_org_id() AND user_id = current_user_id()) '
            'WITH CHECK (org_id = current_org_id() AND user_id = current_user_id())';
END $$;

-- Idempotency must be PER USER: two colleagues can each receive the same
-- message id in their own mailbox, and neither may collide with (or update)
-- the other's row. Replace the org-wide key with an owner-scoped one.
BEGIN;
ALTER TABLE inbox_messages DROP CONSTRAINT IF EXISTS inbox_messages_org_id_provider_external_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_inbox_owner_msg
    ON inbox_messages(org_id, owner_user_id, provider, external_id);
COMMIT;

-- ---------------------------------------------------------------------------
-- Per-user property-card overrides (owner ask 2026-08-07): "if one user edits
-- a property, only they should see their edits." Personal working values —
-- an overlay per (user, property); the shared backbone/folder data is never
-- mutated. Field tiers (which fields may land here at all) are governed by
-- core/field_policy.py + docs/DATA-DICTIONARY.md.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_property_overrides (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_key  text NOT NULL,           -- folder name (deal folders are the shared property identity)
    overrides     jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, property_key)
);
CREATE INDEX IF NOT EXISTS ix_upo_user ON user_property_overrides(org_id, user_id);

-- Same strict per-user RLS as inbox_messages: fails closed when the user
-- context is unset; one analyst's draft never leaks to a colleague.
DO $$
BEGIN
    EXECUTE 'ALTER TABLE user_property_overrides ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE user_property_overrides FORCE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS org_isolation ON user_property_overrides';
    EXECUTE 'DROP POLICY IF EXISTS user_isolation ON user_property_overrides';
    EXECUTE 'CREATE POLICY user_isolation ON user_property_overrides '
            'USING (org_id = current_org_id() AND user_id = current_user_id()) '
            'WITH CHECK (org_id = current_org_id() AND user_id = current_user_id())';
END $$;

-- ---------------------------------------------------------------------------
-- Data API (owner ask 2026-08-07; spec 6.5 Module G usage meters).
-- api_keys is deliberately NOT RLS-protected: verifying a key is what
-- DISCOVERS the org (same bootstrap rationale as `organizations`). Only
-- SHA-256 hashes are stored; the secret is shown once and never persisted.
-- api_usage is the raw meter billing will read; written pre-org-context by
-- the API server. The admin UI filters both by org_id in SQL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key_hash     text NOT NULL UNIQUE,
    prefix_hint  text NOT NULL DEFAULT '',
    label        text NOT NULL DEFAULT 'unnamed',
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    created_by   uuid,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_api_keys_org ON api_keys(org_id);

CREATE TABLE IF NOT EXISTS api_usage (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id    uuid NOT NULL,
    key_id    uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    endpoint  text NOT NULL,
    units     integer NOT NULL DEFAULT 1,
    over_cap  boolean NOT NULL DEFAULT false,
    ts        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_api_usage_key_day ON api_usage(key_id, ts);
CREATE INDEX IF NOT EXISTS ix_api_usage_org_day ON api_usage(org_id, ts);

-- ---------------------------------------------------------------------------
-- Property activity trail (owner ask 2026-08-09: "which users have accessed
-- and updated which properties"). One row per view (throttled to once per
-- session per property) and per edit-save (detail = changed field names).
-- Org-level RLS: activity is org-private; the Admin > Activity tab reads it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_activity (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_key  text NOT NULL,
    action        text NOT NULL CHECK (action IN ('viewed','edited')),
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
    ts            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_pact_org_ts ON property_activity(org_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_pact_prop ON property_activity(org_id, property_key, ts DESC);

DO $$
BEGIN
    EXECUTE 'ALTER TABLE property_activity ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE property_activity FORCE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS org_isolation ON property_activity';
    EXECUTE 'CREATE POLICY org_isolation ON property_activity '
            'USING (org_id = current_org_id()) '
            'WITH CHECK (org_id = current_org_id())';
END $$;

-- ---------------------------------------------------------------------------
-- (2026-08-11) property_email_intel briefly lived here - an org-visible
-- per-message intel card store. Owner corrected the same day: ingest email
-- facts as DATA (muni_records kind='assessor-email', merged by the spine),
-- do not display individual emails. The table was never deployed.
-- ---------------------------------------------------------------------------

-- Metadata-only enumeration for the background O365 sync job: which
-- (org, user) pairs have a connected mailbox. SECURITY DEFINER because
-- mailbox_connections is strict per-user RLS (fails closed with no user
-- context) and the hourly job has none - it then syncs each pair through
-- the SAME user-scoped path the UI button uses. No tokens leave the table.
CREATE OR REPLACE FUNCTION connected_mailboxes()
RETURNS TABLE (org_id uuid, user_id uuid, provider text, last_sync_at timestamptz)
LANGUAGE sql SECURITY DEFINER STABLE AS $$
    SELECT org_id, user_id, provider, last_sync_at
      FROM mailbox_connections WHERE status = 'connected'
$$;
