"""AC-A2 — resolution latency: <=60s per property, <=30 min per 1,000.

What can honestly be tested here, and what cannot. The SLA is end-to-end and
most of that budget is spent inside third-party APIs (VA SCC, BatchData,
Trestle) that this environment cannot reach and whose response times are not
ours to control. Asserting a wall-clock number against stubbed vendors would
be theatre — it would measure the stubs.

What IS ours, and what this measures, is the pipeline's own cost: the work
between the vendor calls. Two regressions would blow the SLA regardless of how
fast the vendors are, and neither shows up in a functional test:

  * per-property overhead growing until it eats the 60s budget by itself
  * batch cost growing faster than linearly — an O(n^2) sibling scan or a
    reconnect per property turns 1,000 properties into hours

The vendor time is held fixed and known, so what is left in the measurement is
the pipeline. That makes this a regression guard on the part we own, stated as
such rather than dressed up as SLA verification.
"""

from __future__ import annotations

import time

import pytest

from core.skiptrace import pipeline

# The spec's budgets.
SINGLE_PROPERTY_SLA_S = 60.0
BATCH_1000_SLA_S = 30 * 60.0

# Pipeline overhead allowed per property, excluding vendor time.
#
# Calibrated against the measured cost (~0.36 ms median) rather than against
# the 60s SLA. A threshold set from the SLA looked reassuring and permitted a
# 139x regression before firing — it could not fail, which is the same as not
# existing. 5 ms is ~14x current: wide enough for a loaded CI box, tight
# enough that a real regression trips it.
MAX_OVERHEAD_PER_PROPERTY_S = 0.005


class _CountingSOS:
    """Answers instantly and finds nothing, so S3 terminates on the first hop."""

    def __init__(self, counter):
        self._counter = counter

    def resolve_entity(self, entity_name: str, state: str):
        self._counter.calls += 1
        return None


class _CountingValidation:
    def __init__(self, counter):
        self._counter = counter

    def validate(self, *a, **kw):
        self._counter.calls += 1
        return []

    def __getattr__(self, _name):
        def _noop(*a, **kw):
            self._counter.calls += 1
            return []
        return _noop


class _StubRegistry:
    """A ProviderRegistry whose vendors answer instantly.

    Latency is deliberately ZERO: the point is to isolate the pipeline's own
    cost, not to simulate a vendor. Simulated delay would only add noise to
    the number being measured.
    """

    def __init__(self):
        self.calls = 0
        self.sos = _CountingSOS(self)
        self.validation = _CountingValidation(self)
        self.trace_waterfall = []
        self.status = {"sos": "mock", "validation": "mock"}


def _prop(i: int = 0) -> dict:
    return {
        "property_id": f"8R-51710-{i:012d}",
        "owner": f"Owner {i} LLC",
        "owner_address": f"{100 + i} Granby St, Norfolk, VA 23510",
        "state": "VA",
        "city": "Norfolk",
        "units": 40,
    }


def _time_one(prop: dict) -> float:
    reg = _StubRegistry()
    t0 = time.perf_counter()
    # persist=False: a dry run touches neither the spend ledger nor Postgres,
    # so this measures computation rather than an absent database.
    pipeline.resolve_contacts("org-latency-test", prop, registry=reg,
                              persist=False)
    return time.perf_counter() - t0


def test_single_property_overhead_is_a_rounding_error_against_the_sla():
    """If the pipeline alone approaches 60s, no vendor is fast enough to save
    it."""
    samples = sorted(_time_one(_prop(i)) for i in range(20))
    median = samples[len(samples) // 2]
    assert median < MAX_OVERHEAD_PER_PROPERTY_S, (
        f"pipeline overhead {median:.3f}s per property — the AC-A2 budget is "
        f"{SINGLE_PROPERTY_SLA_S}s end to end, and this is before any vendor "
        f"has been called")


def test_batch_cost_grows_linearly_not_quadratically():
    """The 1,000-property budget is the one a quadratic scan destroys.

    Compared as a RATIO between two batch sizes rather than an absolute time,
    so the test says something about the algorithm rather than about the
    machine it ran on.
    """
    def batch(n: int) -> float:
        reg = _StubRegistry()
        props = [_prop(i) for i in range(n)]
        t0 = time.perf_counter()
        for p in props:
            pipeline.resolve_contacts("org-latency-test", p, registry=reg,
                                      persist=False)
        return time.perf_counter() - t0

    small, large = batch(25), batch(100)
    # 4x the work should cost roughly 4x. Allow 2x headroom for timer noise;
    # a quadratic path would be 16x, so this still has room to catch one.
    assert large < small * 8, (
        f"25 properties took {small:.3f}s, 100 took {large:.3f}s — that is "
        f"{large / small:.1f}x for 4x the work, which points at "
        f"super-linear cost in the batch path")


def test_a_thousand_properties_would_fit_the_budget_on_pipeline_cost_alone():
    """Extrapolated from measured per-property overhead, vendors excluded.

    Not a claim that the SLA is met — only that the part we control leaves
    essentially the whole budget available to the vendors.
    """
    median = sorted(_time_one(_prop(i)) for i in range(20))[10]
    projected = median * 1000
    assert projected < BATCH_1000_SLA_S * 0.1, (
        f"pipeline alone projects {projected:.1f}s for 1,000 properties, "
        f"more than 10% of the {BATCH_1000_SLA_S:.0f}s budget before a single "
        f"vendor call")


def test_a_property_with_no_owner_returns_immediately():
    """The empty-anchor path must not pay for a resolution it cannot do."""
    reg = _StubRegistry()
    t0 = time.perf_counter()
    res = pipeline.resolve_contacts("org-latency-test",
                                    {"property_id": "8R-x", "owner": ""},
                                    registry=reg, persist=False)
    assert time.perf_counter() - t0 < MAX_OVERHEAD_PER_PROPERTY_S
    assert res.stages_run == ["S1"], "no owner should stop after S1"
    assert reg.calls == 0, "a property with no owner must not call a vendor"
