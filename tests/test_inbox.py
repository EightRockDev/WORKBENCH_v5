"""Module D — Inbox -> Deal Engine tests (spec §6.2).

The headline acceptance: **confidence-gated ingest** — high-confidence broker
mail creates a pipeline record automatically; a vague message must queue for
one-click human confirm and must NOT silently write a deal.
"""

from __future__ import annotations

import pytest

from core.inbox import classify as clf
from core.inbox import engine, providers
from core.inbox import extract as ex
from data import pg

CLEAR_BROKER = {
    "external_id": "t-1", "from_email": "jsmith@marcusmillichap.com",
    "from_name": "Jim Smith",
    "subject": "New to Market: Crossroads Townhomes - 26 Units, Norfolk VA",
    "body": ("Please find attached the OM for Crossroads Townhomes, 1200 Ballentine "
             "Blvd, Norfolk, VA 23504. 26 units, asking $2,850,000 at a 7.25% cap."),
    "attachments": [{"filename": "Crossroads-OM.pdf"}],
}
VAGUE_BROKER = {
    "external_id": "t-2", "from_email": "broker@crexi.com", "from_name": "Pat Lang",
    "subject": "Investment opportunity - multifamily",
    "body": "Wanted to flag an off-market multifamily opportunity. Interested?",
    "attachments": [],
}
LENDER = {
    "external_id": "t-3", "from_email": "lending@walkerdunlop.com",
    "subject": "Term Sheet - Crossroads Townhomes",
    "body": ("Indicative pricing: rate 5.85%, 70% LTV, 30 year amortization, "
             "3 years interest-only, 10 year term. Loan amount $1,995,000."),
    "attachments": [{"filename": "term-sheet.pdf"}],
}
NOISE = {
    "external_id": "t-4", "from_email": "newsletter@retailweekly.com",
    "subject": "This week in retail: 5 trends", "body": "Weekly roundup.",
    "attachments": [],
}


# ---------------------------------------------------------------------------
# Classification (DB-free)
# ---------------------------------------------------------------------------

def test_classifies_broker_lender_attorney_and_noise():
    b = clf.classify(from_email=CLEAR_BROKER["from_email"], subject=CLEAR_BROKER["subject"],
                     body=CLEAR_BROKER["body"], attachments=CLEAR_BROKER["attachments"])
    l = clf.classify(from_email=LENDER["from_email"], subject=LENDER["subject"],
                     body=LENDER["body"], attachments=LENDER["attachments"])
    a = clf.classify(from_email="closing@harborlawllp.com", subject="PSA draft for review",
                     body="Attached is the draft purchase and sale agreement. Escrow agent confirmed.")
    n = clf.classify(from_email=NOISE["from_email"], subject=NOISE["subject"],
                     body=NOISE["body"])
    assert b.category == "broker" and b.confidence > 0.6
    assert l.category == "lender" and l.confidence > 0.6
    assert a.category == "attorney"
    assert n.category == "other" and n.confidence == 0.0


def test_vague_broker_mail_gets_low_confidence():
    c = clf.classify(from_email=VAGUE_BROKER["from_email"], subject=VAGUE_BROKER["subject"],
                     body=VAGUE_BROKER["body"])
    assert c.category == "broker" and c.confidence < 0.75


def test_only_deal_categories_drive_pipeline():
    assert clf.is_deal_relevant(clf.Classification("broker", 0.9))
    assert clf.is_deal_relevant(clf.Classification("lender", 0.9))
    assert not clf.is_deal_relevant(clf.Classification("lp", 0.9))
    assert not clf.is_deal_relevant(clf.Classification("other", 0.9))


def test_classifier_is_deterministic():
    kw = dict(from_email=CLEAR_BROKER["from_email"], subject=CLEAR_BROKER["subject"],
              body=CLEAR_BROKER["body"])
    assert clf.classify(**kw).as_dict() == clf.classify(**kw).as_dict()


# ---------------------------------------------------------------------------
# Extraction (DB-free)
# ---------------------------------------------------------------------------

def test_extracts_deal_facts():
    e = ex.extract_deal(subject=CLEAR_BROKER["subject"], body=CLEAR_BROKER["body"],
                        attachments=CLEAR_BROKER["attachments"])
    f = e.fields
    assert f["units"] == 26
    assert f["asking_price"] == 2_850_000
    assert f["cap_rate"] == 0.0725
    assert "Ballentine" in f["address"]
    assert f["city"] == "Norfolk" and f["state"] == "VA"
    assert f["name"] == "Crossroads Townhomes"
    assert e.confidence > 0.8 and e.evidence


def test_extracts_term_sheet():
    e = ex.extract_terms(subject=LENDER["subject"], body=LENDER["body"])
    f = e.fields
    assert f["rate"] == 0.0585 and f["ltv"] == 0.70
    assert f["amort_years"] == 30 and f["io_years"] == 3 and f["term_years"] == 10
    assert f["proceeds"] == 1_995_000


def test_vague_mail_extracts_almost_nothing():
    e = ex.extract_deal(subject=VAGUE_BROKER["subject"], body=VAGUE_BROKER["body"])
    assert e.confidence < engine.AUTO_APPLY_EXTRACT


def test_unlabeled_money_is_low_confidence():
    e = ex.extract_deal(subject="FYI", body="We saw $1,250,000 mentioned somewhere.")
    assert e.confidences.get("asking_price", 1.0) <= 0.5


def test_extractor_rejects_implausible_values():
    e = ex.extract_deal(subject="99999 units!", body="cap rate 95%")
    assert "units" not in e.fields          # 99999 units is out of band
    assert "cap_rate" not in e.fields       # 95% cap is out of band


# ---------------------------------------------------------------------------
# Confidence-gated ingest (Postgres)
# ---------------------------------------------------------------------------

pg_only = pytest.mark.skipif(not pg.is_configured(), reason="Postgres not configured")


@pytest.fixture()
def org_user():
    """(org_id, user_id) — raw mail is per-user private, so tests need both."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations, users RESTART IDENTITY CASCADE")
        cur.execute("INSERT INTO organizations (name) VALUES ('T') RETURNING id")
        oid = str(cur.fetchone()["id"])
        cur.execute("""INSERT INTO users (idp_sub,email,platform_role,status)
                       VALUES ('u|a','a@eight-rock.com','internal','active')
                       RETURNING id""")
        uid = str(cur.fetchone()["id"])
        conn.commit()
    yield oid, uid
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations, users RESTART IDENTITY CASCADE")
        conn.commit()


@pytest.fixture()
def org(org_user):
    return org_user[0]


@pytest.fixture()
def user(org_user):
    return org_user[1]


@pg_only
def test_high_confidence_broker_mail_auto_creates_deal(org, user):
    r = engine.ingest_message(org, CLEAR_BROKER, owner_user_id=user)
    assert r.auto_applied and r.category == "broker" and r.deal_id
    deals = engine.list_deals(org)
    assert len(deals) == 1
    d = deals[0]
    assert d["name"] == "Crossroads Townhomes" and d["units"] == 26
    assert float(d["asking_price"]) == 2_850_000 and d["stage"] == "lead"
    assert d["source"] == "inbox" and d["broker_email"] == CLEAR_BROKER["from_email"]


@pg_only
def test_low_confidence_queues_and_writes_no_deal(org, user):
    """The §6.2 gate: vague mail must NOT silently write a pipeline record."""
    r = engine.ingest_message(org, VAGUE_BROKER, owner_user_id=user)
    assert r.status == "queued" and r.deal_id is None
    assert "below gate" in r.reason
    assert engine.list_deals(org) == []
    assert len(engine.list_queue(org, user)) == 1


@pg_only
def test_one_click_confirm_applies_queued_extraction(org, user):
    r = engine.ingest_message(org, VAGUE_BROKER, owner_user_id=user)
    confirmed = engine.confirm_message(
        org, r.message_id, actor_user_id=user,
        overrides={"name": "Off-Market HR Portfolio", "units": 40, "state": "VA"})
    assert confirmed.deal_id
    deals = engine.list_deals(org)
    assert len(deals) == 1 and deals[0]["name"] == "Off-Market HR Portfolio"
    assert deals[0]["units"] == 40
    assert engine.list_queue(org, user) == []


@pg_only
def test_dismiss_removes_from_queue_without_writing(org, user):
    r = engine.ingest_message(org, VAGUE_BROKER, owner_user_id=user)
    engine.dismiss_message(org, r.message_id, actor_user_id=user)
    assert engine.list_queue(org, user) == [] and engine.list_deals(org) == []


@pg_only
def test_lender_mail_creates_term_sheet_history(org, user):
    engine.ingest_message(org, CLEAR_BROKER, owner_user_id=user)
    r = engine.ingest_message(org, LENDER, owner_user_id=user)
    assert r.category == "lender" and r.term_sheet_id
    ts = engine.list_term_sheets(org)
    assert len(ts) == 1
    assert float(ts[0]["rate"]) == 0.0585 and float(ts[0]["ltv"]) == 0.70
    assert ts[0]["deal_id"] is not None      # attached to the deal


@pg_only
def test_noise_is_recorded_but_creates_nothing(org, user):
    r = engine.ingest_message(org, NOISE, owner_user_id=user)
    assert r.status == "new" and r.deal_id is None
    assert engine.list_deals(org) == [] and engine.list_queue(org, user) == []
    assert len(engine.list_messages(org, user)) == 1


@pg_only
def test_ingest_is_idempotent(org, user):
    engine.ingest_message(org, CLEAR_BROKER, owner_user_id=user)
    engine.ingest_message(org, CLEAR_BROKER, owner_user_id=user)   # same external_id
    assert len(engine.list_messages(org, user)) == 1
    assert len(engine.list_deals(org)) == 1       # no duplicate deal


@pg_only
def test_second_broker_mail_updates_same_deal(org, user):
    engine.ingest_message(org, CLEAR_BROKER, owner_user_id=user)
    followup = dict(CLEAR_BROKER, external_id="t-1b",
                    subject="Price reduction: Crossroads Townhomes - 26 Units",
                    body=("Crossroads Townhomes, 1200 Ballentine Blvd, Norfolk, VA 23504. "
                          "26 units, asking $2,700,000 at a 7.60% cap."))
    engine.ingest_message(org, followup, owner_user_id=user)
    deals = engine.list_deals(org)
    assert len(deals) == 1 and float(deals[0]["asking_price"]) == 2_700_000


@pg_only
def test_contacts_accumulate_into_crm(org, user):
    engine.ingest_message(org, CLEAR_BROKER, owner_user_id=user)
    engine.ingest_message(org, dict(CLEAR_BROKER, external_id="t-1c"), owner_user_id=user)
    with pg.org_connection(org) as conn, conn.cursor() as cur:
        cur.execute("SELECT email, role, message_count FROM crm_contacts WHERE org_id=%s",
                    (org,))
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["role"] == "broker" and rows[0]["message_count"] == 2


@pg_only
def test_full_mock_sync_end_to_end(org, user):
    from core import inbox

    results = inbox.sync_inbox(org, user)
    assert len(results) == 5
    by_status = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)
    assert by_status.get("auto_applied"), "the clear broker mail should auto-apply"
    assert by_status.get("queued"), "the vague broker mail should queue"
    assert engine.list_deals(org), "a pipeline record should exist"
    assert engine.list_term_sheets(org), "the lender term sheet should be captured"


def test_provider_defaults_to_mock_and_reports_status(monkeypatch):
    monkeypatch.delenv("ER_INBOX_PROVIDER", raising=False)
    assert providers.get_provider().name == "mock"
    assert providers.provider_status() == "mock"
    msgs = providers.get_provider().fetch()
    assert len(msgs) == 5 and all("external_id" in m for m in msgs)


def test_graph_provider_selected_when_configured(monkeypatch):
    monkeypatch.setenv("ER_INBOX_PROVIDER", "graph")
    monkeypatch.setenv("MS_GRAPH_TOKEN", "tok")
    p = providers.get_provider()
    assert isinstance(p, providers.GraphMailProvider)
    assert providers.provider_status() == "live (graph)"


def test_graph_falls_back_to_mock_without_token(monkeypatch):
    monkeypatch.setenv("ER_INBOX_PROVIDER", "graph")
    monkeypatch.delenv("MS_GRAPH_TOKEN", raising=False)
    assert providers.get_provider().name == "mock"


# ---------------------------------------------------------------------------
# PRIVACY — private mailbox, shared pipeline (owner requirement)
# ---------------------------------------------------------------------------

@pytest.fixture()
def two_users():
    """One org, two colleagues. Their mail must be mutually invisible."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations, users RESTART IDENTITY CASCADE")
        cur.execute("INSERT INTO organizations (name) VALUES ('T') RETURNING id")
        oid = str(cur.fetchone()["id"])
        cur.execute("""INSERT INTO users (idp_sub,email,platform_role,status) VALUES
                       ('u|brian','brian@eight-rock.com','admin','active'),
                       ('u|peter','peter@eight-rock.com','internal','active')""")
        cur.execute("SELECT id, email FROM users ORDER BY email")
        rows = cur.fetchall()
        conn.commit()
    yield oid, str(rows[0]["id"]), str(rows[1]["id"])   # brian, peter
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations, users RESTART IDENTITY CASCADE")
        conn.commit()


@pg_only
def test_colleague_cannot_read_my_mail(two_users):
    org, brian, peter = two_users
    engine.ingest_message(org, CLEAR_BROKER, owner_user_id=brian)

    assert len(engine.list_messages(org, brian)) == 1     # owner sees it
    assert engine.list_messages(org, peter) == []          # colleague does NOT
    assert engine.list_queue(org, peter) == []


@pg_only
def test_admin_cannot_read_another_users_mail(two_users):
    """Being an org admin does not grant access to a colleague's inbox."""
    org, brian, peter = two_users          # brian is platform_role='admin'
    engine.ingest_message(org, VAGUE_BROKER, owner_user_id=peter)
    assert engine.list_messages(org, brian) == []
    assert engine.list_queue(org, brian) == []


@pg_only
def test_deals_from_my_mail_are_visible_org_wide(two_users):
    """Private mailbox, SHARED pipeline: the deal is the shared work product."""
    org, brian, peter = two_users
    engine.ingest_message(org, CLEAR_BROKER, owner_user_id=brian)
    deals = engine.list_deals(org)                          # org-scoped read
    assert len(deals) == 1 and deals[0]["name"] == "Crossroads Townhomes"


@pg_only
def test_missing_user_context_fails_closed(two_users):
    """No user context must return ZERO rows, never everything."""
    org, brian, _peter = two_users
    engine.ingest_message(org, CLEAR_BROKER, owner_user_id=brian)
    with pg.org_connection(org) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM inbox_messages")
        assert cur.fetchone()["n"] == 0


@pg_only
def test_each_user_syncs_into_their_own_mailbox(two_users):
    from core import inbox

    org, brian, peter = two_users
    inbox.sync_inbox(org, brian)
    inbox.sync_inbox(org, peter)
    assert len(inbox.list_messages(org, brian)) == 5
    assert len(inbox.list_messages(org, peter)) == 5
    # Same fixtures, but each user owns their own copy - no cross-read.
    with pg.user_connection(org, brian) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT owner_user_id FROM inbox_messages")
        owners = [str(r["owner_user_id"]) for r in cur.fetchall()]
    assert owners == [brian]


@pg_only
def test_repeated_sync_does_not_duplicate_term_sheets(two_users):
    """Regression: pressing Sync repeatedly must not stack term sheets."""
    from core import inbox

    org, brian, _ = two_users
    for _ in range(3):
        inbox.sync_inbox(org, brian)
    assert len(engine.list_term_sheets(org)) == 1
    assert len(engine.list_deals(org)) == 1
    assert len(inbox.list_messages(org, brian)) == 5


@pg_only
def test_disconnect_purges_my_mail_but_keeps_deals(two_users):
    from core import inbox
    from core.inbox import oauth

    org, brian, _ = two_users
    inbox.sync_inbox(org, brian)
    assert engine.list_deals(org)
    oauth.disconnect(org, brian, purge_messages=True)
    assert inbox.list_messages(org, brian) == []       # personal mail gone
    assert engine.list_deals(org)                       # org work product stays


# ---------------------------------------------------------------------------
# Token encryption at rest (§8.1 SR-2.4)
# ---------------------------------------------------------------------------

def test_tokens_encrypt_and_roundtrip(monkeypatch):
    from core.inbox import oauth

    monkeypatch.setenv("ER_TOKEN_KEY", oauth.generate_token_key())
    blob = oauth.encrypt("super-secret-access-token")
    assert blob and "super-secret" not in blob
    assert oauth.decrypt(blob) == "super-secret-access-token"


def test_missing_token_key_is_a_clear_error(monkeypatch):
    from core.inbox import oauth

    monkeypatch.delenv("ER_TOKEN_KEY", raising=False)
    assert not oauth.token_key_configured()
    with pytest.raises(oauth.TokenCipherUnavailable) as e:
        oauth.encrypt("x")
    assert "ER_TOKEN_KEY" in str(e.value)


@pg_only
def test_stored_token_is_not_plaintext_in_the_database(two_users, monkeypatch):
    from core.inbox import oauth

    org, brian, peter = two_users
    monkeypatch.setenv("ER_TOKEN_KEY", oauth.generate_token_key())
    oauth.save_connection(org, brian, provider="graph", account_email="b@x.com",
                          access_token="PLAINTEXT-TOKEN-123",
                          refresh_token="PLAINTEXT-REFRESH", expires_in=3600)
    with pg.connection() as conn, conn.cursor() as cur:   # raw, no RLS context
        cur.execute("SELECT access_token, refresh_token FROM mailbox_connections")
        row = cur.fetchone()
    assert row is None or "PLAINTEXT" not in str(row)      # RLS hides it anyway
    stored = oauth.get_connection(org, brian)
    assert "PLAINTEXT" not in stored["access_token"]
    assert oauth.decrypt(stored["access_token"]) == "PLAINTEXT-TOKEN-123"
    # And a colleague cannot read the connection at all.
    assert oauth.get_connection(org, peter) is None
