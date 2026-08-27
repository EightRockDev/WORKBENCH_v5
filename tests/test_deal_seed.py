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


# --- The record's own evidence (owner report 2026-08-27) -------------------
# "If I've favorited a property, I should not see this message." The message
# said "no sale or assessed value on this parcel" and "no rent estimate for
# this asset" about a property whose own row carried a last sale, a per-unit
# assessment and an average rent. The seed was reading only the county-
# sourced keys.

RECORD = {"units": 46, "avg_rent": 1159, "last_sold_amount": 4_200_000,
          "last_sold_year": dt.date.today().year - 3,
          "assessed_value_per_unit": 62_000}


def test_the_records_own_last_sale_anchors_the_price(no_sales):
    seed = deal_seed.build_seed(RECORD)
    assert seed.price_basis == "recent_sale" and seed.is_anchored
    # 4.2M trended +3%/yr for 3 years.
    assert seed.purchase_price == pytest.approx(4_589_000, abs=1_000)
    assert "property record" in seed.price_note
    assert "no sale or assessed value" not in deal_seed.seed_caption(seed)


def test_a_rent_on_the_record_is_not_called_a_market_placeholder(no_sales):
    seed = deal_seed.build_seed(RECORD)
    assert seed.rent_basis == "record"
    caption = deal_seed.seed_caption(seed)
    assert "$1,159/mo average rent on the property record" in caption
    # The exact sentence the owner objected to, about a number the record
    # supplied — it must not be reachable from this state.
    assert "no rent estimate for this asset" not in caption


def test_per_unit_assessment_anchors_when_there_is_no_sale(no_sales):
    prop = {k: v for k, v in RECORD.items() if not k.startswith("last_sold")}
    seed = deal_seed.build_seed(prop)
    assert seed.price_basis == "assessed" and seed.is_anchored
    assert seed.purchase_price == pytest.approx(
        46 * 62_000 / deal_seed.ASSESSMENT_RATIO, abs=1_000)
    assert "46 units × $62,000/unit assessed" in seed.price_note


def test_a_stale_or_junk_record_sale_is_still_not_an_anchor(no_sales):
    stale = deal_seed.build_seed(
        {"units": 46, "last_sold_amount": 4_200_000,
         "last_sold_year": dt.date.today().year - deal_seed.MAX_SALE_AGE_Y - 1})
    assert stale.price_basis == "ppu"
    junk = deal_seed.build_seed(
        {"units": 46, "last_sold_amount": 10,
         "last_sold_year": dt.date.today().year})
    assert junk.price_basis == "ppu"
    # A sale year with no amount, and an amount with no year, are both
    # half a fact — neither may seed a price.
    for half in ({"last_sold_year": dt.date.today().year},
                 {"last_sold_amount": 4_200_000}):
        assert deal_seed.build_seed({"units": 46, **half}).price_basis == "ppu"


def test_the_county_deed_index_still_outranks_the_record(monkeypatch):
    """Both are the asset's own sale; the deed has the exact date."""
    year = dt.date.today().year
    monkeypatch.setattr(
        "core.sale_history.sale_history_for",
        lambda prop, db_path=None: [{"date": f"{year}-06-01",
                                     "price": 5_000_000}])
    seed = deal_seed.build_seed(RECORD)
    assert seed.purchase_price == pytest.approx(5_000_000, abs=1_000)
    assert "on the property record" not in seed.price_note


def test_a_property_with_nothing_on_record_still_says_so(no_sales):
    """The warning is right when it IS a constant — don't launder that."""
    caption = deal_seed.seed_caption(deal_seed.build_seed({"units": 46}))
    assert "MARKET PLACEHOLDER" in caption
    assert "no sale or assessed value on this parcel" in caption
    assert "no rent estimate for this asset" in caption


def test_the_matched_county_parcels_assessment_is_used(no_sales, tmp_path):
    """The curated pool has no assessed_value column — the parcel it is
    crosswalked to does, and that row is already pulled nightly."""
    import sqlite3

    db = tmp_path / "workbench.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE properties_8r (property_id TEXT PRIMARY KEY,
                                    assessed_value REAL);
        CREATE TABLE property_crosswalk (legacy_property_id TEXT PRIMARY KEY,
                                         r8_property_id TEXT);
        INSERT INTO properties_8r VALUES ('8R-A', 3400000);
        INSERT INTO property_crosswalk VALUES ('P1', '8R-A');
    """)
    conn.commit()
    conn.close()

    seed = deal_seed.build_seed({"property_id": "P1", "units": 46,
                                 "avg_rent": 1159}, db_path=db)
    assert seed.price_basis == "assessed" and seed.is_anchored
    assert seed.purchase_price == pytest.approx(
        3_400_000 / deal_seed.ASSESSMENT_RATIO, abs=1_000)
    assert "matched county parcel" in seed.price_note

    # An unmatched property falls through to the placeholder, honestly.
    other = deal_seed.build_seed({"property_id": "P9", "units": 46},
                                 db_path=db)
    assert other.price_basis == "ppu"


def test_a_missing_backbone_never_raises(no_sales, tmp_path):
    """Pre-Phase-0 boxes have no crosswalk; seeding must not care."""
    empty = tmp_path / "nothing.db"
    empty.write_bytes(b"")
    assert deal_seed.build_seed({"property_id": "P1", "units": 46},
                                db_path=empty).price_basis == "ppu"
    assert deal_seed.build_seed({"property_id": "P1", "units": 46},
                                db_path=tmp_path / "absent.db"
                                ).price_basis == "ppu"


# --- The screen the owner was looking at ----------------------------------

def test_the_input_tab_banner_states_the_evidence_and_stops_warning():
    import textwrap

    from streamlit.testing.v1 import AppTest

    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from ui.input_tab import render_input
        render_input({"name": "Bayview Terrace", "address": "1 Main St",
                      "city": "Norfolk", "state": "VA", "units": 46,
                      "avg_rent": 1159, "last_sold_amount": 4_200_000,
                      "last_sold_year": %d}, None)
    """) % (str(__import__("pathlib").Path(__file__).resolve().parent.parent),
            dt.date.today().year - 1)
    at = AppTest.from_string(script, default_timeout=60).run()
    assert not at.exception
    # Anchored seed -> an INFO that names its evidence, not a warning.
    assert not at.warning
    banner = " ".join(str(i.value) for i in at.info)
    assert r"last sale \$4,200,000" in banner   # escaped, so it renders
    assert "average rent on the property record" in banner
    assert "MARKET PLACEHOLDER" not in banner
    assert "no rent estimate for this asset" not in banner


def test_money_survives_streamlit_markdown(no_sales):
    """Streamlit reads $...$ as LaTeX and eats both signs — which is how
    the owner's own bug report read "46 units × 130,000 market /unit"."""
    seed = deal_seed.build_seed(RECORD)
    md = deal_seed.seed_caption_md(seed)
    assert r"\$4,200,000" in md and r"\$1,159/mo" in md
    # Every dollar sign escaped: no bare $ can open a maths run.
    assert "$" not in md.replace("\\$", "")
    # Plain-text callers are untouched.
    assert "\\" not in deal_seed.seed_caption(seed)


def test_both_seed_banners_escape_their_money():
    for path in ("ui/input_tab.py", "ui/underwriting.py"):
        src = open(path, encoding="utf-8").read()
        assert "seed_caption_md(_seed)" in src, path
        assert "seed_caption(_seed)" not in src, path
