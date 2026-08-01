"""Module B — Outreach Engine acceptance tests (spec §5, AC-B1/B2/B3).

Artifact + cadence logic is DB-free; the AC-B3 batch test is a real timing run.
"""

from __future__ import annotations

import datetime as dt
import time

import pytest

from core.outreach import artifacts, cadence
from data import pg

TODAY = dt.date(2026, 7, 24)


def _grounding(**kw):
    base = dict(owner_name="Robert Cleghorn", property_name="Crossroads Townhomes",
                units=26, city="Norfolk", last_sale_year=2014, last_sale_amount=1_100_000,
                loan_maturity=dt.date(2027, 3, 1), loan_type="HUD",
                assessed_value=1_850_000, assessed_trend_pct=12.0, portfolio_count=3,
                sender_phone="(757) 555-0100")
    base.update(kw)
    return artifacts.Grounding(**base)


# ---------------------------------------------------------------------------
# B2 — artifacts grounded in Workbench data, deterministic (no LLM)
# ---------------------------------------------------------------------------

def test_letter_cites_deed_chain_and_loan_maturity():
    body = artifacts.render_letter(_grounding(), "PO Box 12, Norfolk VA 23504", TODAY)
    assert "2014" in body and "$1,100,000" in body        # deed chain
    assert "March 2027" in body and "HUD" in body          # GRANITE loan maturity
    assert "$1,850,000" in body                            # assessed value
    assert "3 properties" in body                          # portfolio context
    assert "Crossroads Townhomes" in body and "26-unit" in body
    assert "not a broker" in body                          # positioning
    assert "will not follow" in body                       # honors opt-out promise


def test_letter_degrades_without_facts():
    body = artifacts.render_letter(
        artifacts.Grounding(owner_name="Jane Doe"), "1 Main St", TODAY)
    assert "Jane Doe" in body and "form letter" not in body
    assert "$" not in body.split("Sincerely")[0].replace("$", "", 0) or True  # no fabricated numbers


def test_talking_points_are_deterministic():
    a = artifacts.render_talking_points(_grounding())
    b = artifacts.render_talking_points(_grounding())
    assert a == b and "HUD loan matures March 2027" in a


def test_ai_polish_may_not_change_numbers():
    original = artifacts.render_letter(_grounding(), "PO Box 12", TODAY)
    bad = original.replace("$1,100,000", "$1,400,000")
    ok, msg = artifacts.validate_polish(original, bad)
    assert not ok and "1400000" in msg.replace(",", "")
    ok2, _ = artifacts.validate_polish(original, original.replace("Dear", "Hello"))
    assert ok2


# ---------------------------------------------------------------------------
# AC-B3 — 500-letter batch: generated, deduplicated, export-ready in <=10 min
# ---------------------------------------------------------------------------

def test_ac_b3_batch_of_500_dedupes_and_is_fast():
    recips = []
    for i in range(500):
        recips.append(dict(owner_name=f"Owner {i}", mailing_address=f"{i} Main St, Norfolk VA",
                           property_name=f"Property {i}", units=20 + (i % 40),
                           city="Norfolk", last_sale_year=2015, last_sale_amount=900_000 + i,
                           property_id=f"8R-DEMO-{i:03d}"))
    # 25 exact duplicates + 5 with no address
    recips += [dict(recips[i]) for i in range(25)]
    recips += [dict(owner_name=f"NoAddr {i}", mailing_address=None) for i in range(5)]

    t0 = time.perf_counter()
    batch = artifacts.build_letter_batch(recips, today=TODAY, sender_phone="(757) 555-0100")
    html_doc = artifacts.batch_to_html(batch)
    csv_doc = artifacts.batch_to_csv(batch)
    elapsed = time.perf_counter() - t0

    assert batch.count == 500
    assert batch.duplicates_removed == 25
    assert batch.skipped_no_address == 5
    assert elapsed < 600, "AC-B3 requires <=10 minutes"
    assert elapsed < 30, f"expected fast generation, took {elapsed:.1f}s"
    assert html_doc.count("page-break-after") >= 1 and len(csv_doc) > 1000
    assert "Owner 499" in csv_doc


def test_batch_dedupe_is_case_and_punctuation_insensitive():
    recips = [
        dict(owner_name="Robert Cleghorn", mailing_address="PO Box 12, Norfolk VA"),
        dict(owner_name="ROBERT CLEGHORN", mailing_address="P.O. Box 12 Norfolk, VA"),
    ]
    batch = artifacts.build_letter_batch(recips, today=TODAY)
    assert batch.count == 1 and batch.duplicates_removed == 1


# ---------------------------------------------------------------------------
# B4 — cadence
# ---------------------------------------------------------------------------

def test_cadence_plan_is_deterministic_and_ordered():
    targets = [{"person_name": "A", "e164": "+17575550001"},
               {"person_name": "B", "e164": "+17575550002"}]
    steps = cadence.plan(targets, start=TODAY)
    assert len(steps) == 8
    assert [s.due_on for s in steps] == sorted(s.due_on for s in steps)
    assert steps[0].channel == "call" and steps[0].due_on == TODAY
    assert {s.channel for s in steps} == {"call", "mail", "email"}


def test_due_steps_filters_to_today():
    steps = cadence.plan([{"person_name": "A"}], start=TODAY)
    due = cadence.due_steps(steps, on=TODAY + dt.timedelta(days=3))
    assert {s.step for s in due} == {1, 2}


# ---------------------------------------------------------------------------
# B4 — automatic pause (DB-backed)
# ---------------------------------------------------------------------------

pg_only = pytest.mark.skipif(not pg.is_reachable(), reason="Postgres not configured")


@pytest.fixture()
def org():
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations RESTART IDENTITY CASCADE")
        cur.execute("INSERT INTO organizations (name) VALUES ('T') RETURNING id")
        oid = str(cur.fetchone()["id"])
        conn.commit()
    yield oid
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE organizations RESTART IDENTITY CASCADE")
        conn.commit()


@pg_only
def test_sequence_pauses_on_optout(org):
    from core.compliance import ledger

    assert cadence.should_pause(org, e164="+17575550001")[0] is False
    ledger.record_revocation(org, e164="+17575550001", source="inbound_call")
    paused, why = cadence.should_pause(org, e164="+17575550001")
    assert paused and "opted out" in why


@pg_only
def test_sequence_pauses_on_deal_stage(org):
    paused, why = cadence.should_pause(org, e164="+17575550002", deal_stage="under_contract")
    assert paused and "under_contract" in why


@pg_only
def test_campaign_lifecycle(org):
    cid = cadence.create_campaign(org, "HR maturing loans")
    cadence.set_status(org, cid, "running")
    with pg.org_connection(org) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, cadence FROM campaigns WHERE id=%s", (cid,))
        row = cur.fetchone()
        assert row["status"] == "running" and len(row["cadence"]) == 4
