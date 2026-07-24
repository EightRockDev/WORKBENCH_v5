"""Module C — Forced-Seller Radar v2 acceptance tests (spec §6.1).

Covers the component scorers, the evidence panel behind every score, the new
v5.0 POC signals, and the acceptance bar: top-decile trailing score must capture
>=3x the base rate of subsequent sales.
"""

from __future__ import annotations

import datetime as dt
import random

from core import radar_v2 as rv

TODAY = dt.date(2026, 7, 24)


def _prop(**kw):
    base = dict(property_id="8R-DEMO-001", units=26, state="VA", last_sold_year=2014)
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

def test_loan_maturity_peaks_inside_twelve_months():
    near = rv.score_loan_maturity(dt.date(2027, 3, 1), today=TODAY, loan_type="HUD")
    far = rv.score_loan_maturity(dt.date(2031, 3, 1), today=TODAY)
    none_ = rv.score_loan_maturity(None, today=TODAY)
    assert near.score > far.score > none_.score
    assert "HUD" in near.evidence[0] and "Mar 2027" in near.evidence[0]


def test_matured_loan_is_acute_but_stale_maturity_decays():
    acute = rv.score_loan_maturity(dt.date(2026, 6, 1), today=TODAY)
    stale = rv.score_loan_maturity(dt.date(2024, 1, 1), today=TODAY)
    assert acute.score == 100.0 and stale.score < acute.score


def test_tax_delinquency_scales_and_explains():
    c = rv.score_tax_delinquency(2.5, 48_000, units=26)
    assert c.score > 90 and "delinquent 2.5" in c.evidence[0] and "$1,846/unit" in c.evidence[0]
    assert rv.score_tax_delinquency(0).score == 0


def test_tenure_sell_window():
    assert rv.score_tenure(2014, today_year=2026).score > rv.score_tenure(2025, today_year=2026).score
    assert rv.score_tenure(1985, today_year=2026).score > 50   # legacy/estate hold


def test_permit_decay_flags_no_reinvestment():
    dead = rv.score_permit_decay(0, 2009, today_year=2026)
    active = rv.score_permit_decay(6, 2025, today_year=2026)
    assert dead.score > active.score and "No permits" in dead.evidence[0]


def test_delisted_scores_above_currently_listed():
    delisted = rv.score_listing(False, delisted_within_days=120)
    listed = rv.score_listing(True)
    assert delisted.score > listed.score
    assert "withdrawn" in delisted.evidence[0]


# ---------------------------------------------------------------------------
# NEW v5.0 — POC signals from Module A
# ---------------------------------------------------------------------------

def test_deceased_owner_is_top_signal():
    pocs = [{"person": {"full_name": "Robert Cleghorn", "deceased": True}, "addresses": []}]
    c = rv.score_poc_signals(pocs, property_state="VA")
    assert c.score >= 95 and "deceased" in c.evidence[0]


def test_out_of_state_mailing_address_flags_absentee():
    pocs = [{"person": {"full_name": "J D"}, "addresses":
             [{"formatted": "500 Ocean Dr, Miami FL 33139", "kind": "mailing"}]}]
    c = rv.score_poc_signals(pocs, property_state="VA")
    assert c.score >= 60 and any("out of state" in e for e in c.evidence)


def test_in_state_mailing_address_is_not_flagged():
    pocs = [{"person": {"full_name": "J D"}, "addresses":
             [{"formatted": "12 Main St, Norfolk VA 23504", "kind": "mailing"}]}]
    c = rv.score_poc_signals(pocs, property_state="VA")
    assert not any("out of state" in e for e in c.evidence)


def test_entity_dissolution_flag():
    c = rv.score_poc_signals([], entity_dissolved=True)
    assert c.score >= 90 and "dissolution" in c.evidence[0]


def test_no_signals_is_explained_not_silent():
    c = rv.score_poc_signals([])
    assert c.score == 0 and c.evidence == ["No adverse POC signals"]


# ---------------------------------------------------------------------------
# Fusion + evidence panel
# ---------------------------------------------------------------------------

def test_distressed_property_scores_far_above_healthy_one():
    distressed = rv.score_property(
        _prop(last_sold_year=2006), today=TODAY,
        pocs=[{"person": {"full_name": "X", "deceased": True}, "addresses": []}],
        signals={"loan_maturity": dt.date(2027, 1, 1), "loan_type": "HUD",
                 "years_delinquent": 2.0, "permits_last_5y": 0,
                 "delisted_within_days": 90})
    healthy = rv.score_property(
        _prop(last_sold_year=2025), today=TODAY, pocs=[],
        signals={"loan_maturity": dt.date(2034, 1, 1), "permits_last_5y": 8})
    assert distressed.score > 75 and healthy.score < 30
    assert distressed.band == "ACT" and healthy.band == "MONITOR"


def test_every_score_has_evidence_and_components_sum():
    s = rv.score_property(_prop(), today=TODAY, signals={"loan_maturity": dt.date(2027, 3, 1)})
    assert len(s.components) == 6
    assert abs(sum(c.contribution for c in s.components) - s.score) < 0.15
    assert s.evidence and all(isinstance(e, str) for e in s.evidence)
    assert abs(sum(rv.WEIGHTS.values()) - 1.0) < 1e-9


def test_no_single_signal_alone_reaches_act_band():
    """Distress must be a fusion judgement, not one noisy feed (§6.1)."""
    for sig in ({"loan_maturity": dt.date(2026, 8, 1)}, {"years_delinquent": 3.0},
                {"delisted_within_days": 30}, {"permits_last_5y": 0}):
        s = rv.score_property(_prop(last_sold_year=2025), today=TODAY, pocs=[], signals=sig)
        assert s.score < rv.BAND_GO, f"{sig} alone reached ACT: {s.score}"


def test_score_is_deterministic():
    kw = dict(today=TODAY, signals={"loan_maturity": dt.date(2027, 3, 1),
                                    "years_delinquent": 1.0})
    assert rv.score_property(_prop(), **kw).score == rv.score_property(_prop(), **kw).score


def test_as_dict_shape_for_ui():
    d = rv.score_property(_prop(), today=TODAY).as_dict()
    assert {"property_id", "score", "band", "components"} <= set(d)
    assert all({"key", "score", "weight", "contribution", "evidence"} <= set(c)
               for c in d["components"])


# ---------------------------------------------------------------------------
# §6.1 ACCEPTANCE — top-decile lift >= 3x base rate
# ---------------------------------------------------------------------------

def _synthetic_population(n=600, seed=7):
    """Properties whose true sale propensity is driven by the same distress
    factors the radar reads — the model must rank them, not memorize them."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        distressed = rng.random() < 0.18
        if distressed:
            sig = {"loan_maturity": TODAY + dt.timedelta(days=rng.randint(30, 500)),
                   "loan_type": "HUD",
                   "years_delinquent": rng.choice([0, 1.0, 2.0, 2.5]),
                   "permits_last_5y": rng.choice([0, 0, 1]),
                   "delisted_within_days": rng.choice([None, 120, 300])}
            last_sold = rng.randint(1998, 2014)
            pocs = ([{"person": {"full_name": "O", "deceased": rng.random() < 0.25},
                      "addresses": [{"formatted": "1 Away Rd, Miami FL", "kind": "mailing"}]}]
                    if rng.random() < 0.5 else [])
            traded = rng.random() < 0.45          # distressed trade far more often
        else:
            sig = {"loan_maturity": TODAY + dt.timedelta(days=rng.randint(1200, 3500)),
                   "years_delinquent": 0.0,
                   "permits_last_5y": rng.randint(3, 9),
                   "delisted_within_days": None}
            last_sold = rng.randint(2018, 2025)
            pocs = []
            traded = rng.random() < 0.04
        s = rv.score_property(_prop(property_id=f"P{i}", last_sold_year=last_sold),
                              pocs=pocs, today=TODAY, signals=sig)
        out.append((s.score, traded))
    return out


def test_backtest_top_decile_beats_base_rate_3x():
    res = rv.backtest(_synthetic_population())
    assert res.n == 600
    assert res.passes, (f"top-decile lift {res.lift}x < 3x target "
                        f"(base {res.base_rate}, top {res.top_decile_rate})")


def test_backtest_handles_empty_and_degenerate_input():
    assert rv.backtest([]).n == 0
    flat = rv.backtest([(50.0, False)] * 20)
    assert flat.lift == 0.0 and flat.base_rate == 0.0
