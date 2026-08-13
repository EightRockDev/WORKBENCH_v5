"""Asset-aware seeding of an untouched property's first numbers
(owner ask 2026-08-13: the seed was units x a fixed $/unit regardless of
what the parcel's own record said)."""

from __future__ import annotations

import datetime as dt

import pytest

from core import deal_seed


@pytest.fixture
def no_sales(monkeypatch):
    """Default: no sale history, so tests opt IN to a sale anchor."""
    monkeypatch.setattr(deal_seed, "_price_from_sale", lambda p, d: None)


def test_recent_sale_wins_and_is_trended(monkeypatch):
    year = dt.date.today().year - 2
    monkeypatch.setattr(
        "core.sale_history.sale_history_for",
        lambda prop, db_path=None: [
            {"date": f"{year}-06-01", "price": 3_000_000},
            {"date": "2009-01-01", "price": 900_000},   # too old to anchor
        ])
    seed = deal_seed.build_seed(
        {"units": 40, "assessed_value": 2_000_000, "avg_rent": 1200})
    assert seed.price_basis == "recent_sale"
    assert seed.is_anchored
    # 3.0M trended +3%/yr for 2 years, rounded to the nearest $1k
    assert seed.purchase_price == pytest.approx(3_183_000, abs=1_000)
    assert "trended" in seed.price_note
    # Sale beats BOTH the assessment and the $/unit constant.
    assert seed.purchase_price != 40 * deal_seed.FALLBACK_PPU


def test_junk_transfers_are_not_anchors(monkeypatch):
    """$1/$10 deed transfers (gifts, LLC reshuffles) must never set price."""
    monkeypatch.setattr(
        "core.sale_history.sale_history_for",
        lambda prop, db_path=None: [{"date": "2026-01-01", "price": 10}])
    seed = deal_seed.build_seed({"units": 20, "assessed_value": 1_700_000})
    assert seed.price_basis == "assessed"


def test_assessed_value_used_when_no_sale(no_sales):
    seed = deal_seed.build_seed({"units": 20, "assessed_value": 1_700_000})
    assert seed.price_basis == "assessed"
    assert seed.purchase_price == pytest.approx(2_000_000, abs=1_000)
    assert "assessment ratio" in seed.price_note


def test_ppu_fallback_is_labelled_a_placeholder(no_sales):
    seed = deal_seed.build_seed({"units": 20})
    assert seed.price_basis == "ppu"
    assert not seed.is_anchored
    assert seed.purchase_price == 20 * deal_seed.FALLBACK_PPU
    assert "PLACEHOLDER" in deal_seed.seed_caption(seed)


def test_unknown_unit_count_no_longer_seeds_a_13m_deal(no_sales):
    """The old code assumed 100 units -> $13,000,000 on a parcel whose size
    is unknown. Assume small and SAY so instead."""
    seed = deal_seed.build_seed({"units": None})
    assert seed.units_assumed
    assert seed.units == deal_seed.ASSUMED_UNITS
    assert seed.purchase_price < 5_000_000
    assert "assumed" in deal_seed.seed_caption(seed)


def test_rent_basis_is_named(no_sales):
    hud = deal_seed.build_seed(
        {"units": 10, "avg_rent": 1400, "rent_source": "hud_fmr"})
    assert hud.rent_basis == "hud_fmr" and "HUD FMR" in hud.rent_note
    live = deal_seed.build_seed(
        {"units": 10, "avg_rent": 1650, "rent_source": "listings"})
    assert live.rent_basis == "listings" and "listings" in live.rent_note
    none = deal_seed.build_seed({"units": 10})
    assert none.rent_basis == "fallback"
    assert none.noi > 0


def test_noi_follows_units_and_rent(no_sales):
    import config
    seed = deal_seed.build_seed(
        {"units": 30, "avg_rent": 1000, "rent_source": "hud_fmr"})
    gpr = 30 * 1000 * 12
    expect = gpr * (1 - config.VACANCY_DEFAULT) - gpr * 0.45
    assert seed.noi == pytest.approx(expect, abs=1)


def test_build_default_deal_uses_the_seed(no_sales):
    """The DealState the UI persists carries the anchored numbers."""
    from ui.underwriting import build_default_deal
    deal = build_default_deal({"units": 20, "assessed_value": 1_700_000})
    assert deal.pp == pytest.approx(2_000_000, abs=1_000)
    assert deal.noi > 0


def test_seed_never_raises_on_a_junk_record(no_sales):
    for bad in ({}, {"units": "??"}, {"units": 12, "assessed_value": "n/a"},
                {"units": 12, "avg_rent": "1,200"}):
        seed = deal_seed.build_seed(bad)
        assert seed.purchase_price > 0
        assert deal_seed.seed_caption(seed)
