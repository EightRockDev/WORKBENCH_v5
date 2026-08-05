"""Tests for the clickable-KPI feature (first-user feedback 2026-08).

"When they click a main input, show them where to change it." Editable cards
(Purchase Price) become links to `?goto=underwriting`; computed cards get a
tooltip saying they're derived. The card HTML builder is pure, so we assert on
its output directly.
"""

from __future__ import annotations

from ui.v2_theme_05292026 import _stat_card_html


def test_editable_card_is_a_link_to_the_underwriting_tab():
    html = _stat_card_html(
        "Purchase Price", "$5.00", "M", "✏️ Click to edit",
        href="?goto=underwriting",
        title="This is an input — click to edit it in the Underwriting tab.")
    assert '<a ' in html
    assert 'href="?goto=underwriting"' in html
    assert 'v2-stat-link' in html
    assert 'target="_self"' in html


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
