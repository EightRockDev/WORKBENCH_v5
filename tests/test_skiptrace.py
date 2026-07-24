"""Acceptance tests for Module A — Skip Trace & POC Intelligence (spec §4).

Covers:
  AC-A1  owner resolution to a named human with >=1 grade-A phone (on a batch).
  AC-A3  ZERO callable=true contacts without a valid, unexpired compliance
         stamp — the invariant the dialer/export relies on.
  AC-A4  cost telemetry accurate; hard budget cap stops paid work (FR-A5).
  Plus:  LLC piercing (FR-A3), waterfall stop-on-grade-A (§4.2 S4), idempotent
         persist (§4.2 S7), and the §4.5 poc_record shape.

Pipeline logic is DB-free (persist=False) except the persistence/budget tests,
which are skipped when Postgres is unavailable.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.skiptrace import pipeline, providers
from data import pg

NOW = dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc)


def _prop(**kw):
    base = dict(property_id="8R-DEMO-002", owner="Ghent Holdings LLC", state="VA",
                owner_address="900 Colonial Ave, Norfolk VA", management_company="Harbor Group")
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Pipeline behaviour (DB-free)
# ---------------------------------------------------------------------------

def test_llc_pierced_to_named_human():
    res = pipeline.resolve_contacts("org", _prop(), persist=False)
    owner = res.pocs[0]
    assert owner["role"] == "principal"          # entity -> principal (FR-A3)
    assert owner["entity_chain"]                  # at least one registry hop
    name = owner["person"]["full_name"]
    assert name and name != "Ghent Holdings LLC"  # resolved to a person, not the LLC
    assert " " in name                            # looks like a human name


def test_individual_owner_role_is_owner():
    res = pipeline.resolve_contacts("org", _prop(owner="Robert Cleghorn"), persist=False)
    assert res.pocs[0]["role"] == "owner"
    assert res.pocs[0]["entity_chain"] == []


def test_ac_a1_owner_has_grade_a_phone():
    # Batch of demo owners: >=80% should resolve to a named human w/ a grade-A phone.
    owners = [f"{n} Holdings LLC" for n in
              ["Ghent", "Granby", "Berkley", "Kempsville", "Greenbrier",
               "Poindexter", "London", "Phoebus", "Warwick", "Main Street"]]
    resolved = 0
    for i, o in enumerate(owners):
        r = pipeline.resolve_contacts("org", _prop(property_id=f"P{i}", owner=o), persist=False)
        if r.owner_resolved and r.grade_a_phones >= 1:
            resolved += 1
    assert resolved / len(owners) >= 0.8


def test_ac_a3_no_callable_without_valid_stamp():
    """The core compliance invariant: every callable phone has a valid, unexpired
    stamp and passes the DNC/litigator gate. Nothing else may be callable."""
    for i in range(40):
        r = pipeline.resolve_contacts("org", _prop(property_id=f"P{i}", owner=f"Owner {i} LLC"),
                                      persist=False)
        for poc in r.pocs:
            for ph in poc["phones"]:
                if ph["callable"]:
                    # must have a stamp, not expired, not federal DNC, not litigator
                    assert ph["dnc"]["scrubbed_at"], "callable without a scrub stamp"
                    exp = dt.datetime.fromisoformat(ph["dnc"]["expires_at"])
                    assert exp > NOW, "callable with an expired stamp"
                    assert ph["dnc"]["federal"] is False
                    assert ph["litigator"] is False
                    assert ph["grade"] in ("A", "B")


def test_grade_f_and_dnc_never_callable():
    seen_noncallable = 0
    for i in range(60):
        r = pipeline.resolve_contacts("org", _prop(property_id=f"Q{i}", owner=f"Entity {i} LLC"),
                                      persist=False)
        for poc in r.pocs:
            for ph in poc["phones"]:
                if ph["grade"] == "F" or ph["dnc"]["federal"] or ph["litigator"]:
                    assert ph["callable"] is False
                    assert ph["reason"] != "ok"
                    seen_noncallable += 1
    assert seen_noncallable > 0   # the fixture actually exercises the blocked path


def test_waterfall_stops_on_grade_a():
    # A resolved owner should not have paid for tier-3 if tier-2 produced grade-A.
    res = pipeline.resolve_contacts("org", _prop(), persist=False)
    vendors = {s["vendor"] for s in res.spend_lines}
    if res.grade_a_phones >= 1:
        assert "mock-enformion" not in vendors   # tier 3 skipped once grade-A found


def test_cost_telemetry_sums_line_items():
    res = pipeline.resolve_contacts("org", _prop(), persist=False)
    assert abs(res.total_cost_usd - round(sum(s["cost_usd"] for s in res.spend_lines), 4)) < 1e-9
    typ, worst = pipeline.estimate_cost(_prop())
    assert 0 < typ <= worst


def test_poc_record_matches_contract_shape():
    poc = pipeline.resolve_contacts("org", _prop(), persist=False).pocs[0]
    for key in ("id", "org_id", "property_id", "portfolio_id", "role", "person",
                "entity_chain", "phones", "emails", "addresses", "relatives",
                "other_properties", "provenance", "compliance"):
        assert key in poc, f"missing §4.5 field: {key}"
    ph = poc["phones"][0]
    for key in ("e164", "line_type", "grade", "name_match", "litigator", "dnc",
                "callable", "reason"):
        assert key in ph
    assert poc["provenance"] and all("cost_usd" in p for p in poc["provenance"])


def test_no_owner_returns_empty():
    res = pipeline.resolve_contacts("org", _prop(owner=""), persist=False)
    assert res.pocs == [] and res.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Persistence + budget (Postgres-backed)
# ---------------------------------------------------------------------------

pg_only = pytest.mark.skipif(not pg.is_configured(), reason="Postgres not configured")


@pytest.fixture()
def org():
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations, poc_records, skiptrace_spend RESTART IDENTITY CASCADE")
        cur.execute("INSERT INTO organizations (name) VALUES ('T') RETURNING id")
        oid = str(cur.fetchone()["id"])
        conn.commit()
    yield oid
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations, poc_records, skiptrace_spend RESTART IDENTITY CASCADE")
        conn.commit()


@pg_only
def test_persist_is_idempotent_and_records_spend(org):
    p = _prop()
    r1 = pipeline.resolve_contacts(org, p)
    stored1 = pipeline.load_pocs(org, p["property_id"])
    assert len(stored1) == len(r1.pocs) and stored1

    r2 = pipeline.resolve_contacts(org, p)          # re-run replaces, no dupes
    stored2 = pipeline.load_pocs(org, p["property_id"])
    assert len(stored2) == len(r2.pocs)

    # AC-A4: spend ledger equals the sum of both runs' line items, to the cent.
    mtd = pipeline.month_to_date_spend(org)
    assert abs(mtd - round(r1.total_cost_usd + r2.total_cost_usd, 4)) < 0.005


@pg_only
def test_ac_a4_budget_cap_blocks_paid_work(org):
    # set a tiny cap so any paid tier trips it (FR-A5 hard stop)
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE organizations SET buy_box_config=%s WHERE id=%s",
                    ('{"skiptrace_budget_usd": 0.01}', org))
        conn.commit()
    with pytest.raises(pipeline.BudgetExceeded):
        pipeline.resolve_contacts(org, _prop())
    # nothing persisted after the block
    assert pipeline.load_pocs(org, _prop()["property_id"]) == []


@pg_only
def test_persisted_callable_invariant(org):
    """AC-A3 at the storage layer: no stored phone is callable without a stamp."""
    for i in range(15):
        pipeline.resolve_contacts(org, _prop(property_id=f"S{i}", owner=f"Owner {i} LLC"))
    with pg.org_connection(org) as conn, conn.cursor() as cur:
        cur.execute("SELECT phones FROM poc_records")
        for row in cur.fetchall():
            for ph in row["phones"]:
                if ph.get("callable"):
                    assert ph["dnc"]["scrubbed_at"] and ph["dnc"]["federal"] is False
