"""Tests for the KPI stat cards.

History: the 2026-08 clickable-KPI feature made Purchase Price a
`?goto=underwriting` link; the owner reported it misbehaving and had it
REMOVED (2026-08-08). The generic `_stat_card_html` builder keeps its href
capability; the stats bar must simply never use it for Purchase Price.
"""

from __future__ import annotations

from ui.v2_theme_05292026 import _stat_card_html


def test_builder_still_supports_href_generically():
    html = _stat_card_html(
        "Anything", "$5.00", "M", "foot", href="?goto=x", title="t")
    assert '<a ' in html and 'href="?goto=x"' in html


def test_purchase_price_card_is_not_a_link():
    """Owner 2026-08-08: the 'Click to edit' link misbehaved — removed. The
    stats bar must render Purchase Price as a plain card (per-unit footer,
    tooltip only)."""
    import inspect
    from ui import v2_theme_05292026 as v2
    src = inspect.getsource(v2.render_v2_stats_bar)
    assert "goto=underwriting" not in src
    assert "Click to edit" not in src and "Set in Underwriting\"" not in src


def test_computed_card_has_a_tooltip_but_no_link():
    html = _stat_card_html(
        "Going-in cap", "7.50", "%", "Computed from underwriting",
        tone="go", title="Computed from your underwriting inputs.")
    assert '<a ' not in html
    assert 'title="Computed from your underwriting inputs."' in html


def test_plain_card_has_neither_link_nor_tooltip():
    html = _stat_card_html("DSCR Stabilized", "1.35", "×", "Stabilized year")
    assert '<a ' not in html
    assert 'title=' not in html
    assert 'v2-stat-link' not in html


def test_title_is_html_escaped():
    html = _stat_card_html("X", "1", None, "f", title='a "b" <c>')
    assert 'a &quot;b&quot; &lt;c&gt;' in html


def test_goto_keys_line_up_with_tab_labels():
    # The ?goto target must be a real tab key so app._sticky_property_tab can
    # map it to a segmented-control label.
    import app
    assert "underwriting" in app._PTAB_KEYS
    assert len(app._PTAB_KEYS) == len(app._PTAB_LABELS_V2)
