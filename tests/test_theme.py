"""Tests for the Appearance panel: per-user theme prefs + website extraction.

The extractor's network fetch is not exercised here — `parse_css` /
`build_palette` take raw CSS, so the role-assignment logic (which is the part
worth testing) runs offline.
"""

from __future__ import annotations

import json

import pytest

import config
from core import palette_extract as px
from core import theme_prefs as tp


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------

def test_hex_roundtrip_and_shorthand():
    assert px.hex_to_rgb("#fff") == (255, 255, 255)
    assert px.hex_to_rgb("#1d4ed8") == (29, 78, 216)
    assert px.rgb_to_hex((29, 78, 216)) == "#1d4ed8"


def test_luminance_and_contrast_extremes():
    assert px.luminance("#000000") == pytest.approx(0.0, abs=1e-6)
    assert px.luminance("#ffffff") == pytest.approx(1.0, abs=1e-6)
    assert px.contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.05)


def test_mix_midpoint():
    assert px.mix("#000000", "#ffffff", 0.5) == "#808080"


def test_ensure_contrast_fixes_low_contrast_text():
    # Pale grey on white is unreadable; it must come back darker and passing.
    fixed = px.ensure_contrast("#d8d8d8", "#ffffff", 4.5)
    assert px.contrast(fixed, "#ffffff") >= 4.5


def test_ensure_contrast_leaves_good_pairs_alone():
    assert px.ensure_contrast("#111111", "#ffffff", 4.5) == "#111111"


@pytest.mark.parametrize("token,expected", [
    ("#fff", "#ffffff"),
    ("rgb(29, 78, 216)", "#1d4ed8"),
    ("rgba(29, 78, 216, 0.9)", "#1d4ed8"),
    ("hsl(0, 100%, 50%)", "#ff0000"),
])
def test_norm_color_parses_every_notation(token, expected):
    assert px._norm_color(token) == expected


def test_norm_color_drops_transparent():
    assert px._norm_color("rgba(0, 0, 0, 0)") is None
    assert px._norm_color("#ffffff00") is None


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------

_LIGHT_CSS = """
body { background-color: #ffffff; color: #1a1a1a; font-family: 'Söhne', sans-serif; }
.page { background: #ffffff; }
.card { background-color: #f6f8fa; border-color: #d0d7de; }
.panel { background-color: #f6f8fa; }
a, .btn { color: #635bff; background-color: #635bff; }
.brand { background: #635bff; }
.ok { color: #1a7f37; }
.bad { color: #cf222e; }
code { font-family: 'IBM Plex Mono', monospace; }
"""

_DARK_CSS = """
body { background-color: #0d1117; color: #e6edf3; }
.card { background-color: #161b22; border-color: #30363d; }
.accent, .btn { background-color: #2f81f7; color: #2f81f7; }
"""


def _palette(css: str, url: str = "https://example.test") -> px.Palette:
    stats = px.ColorStats()
    fonts: dict[str, float] = {}
    px.parse_css(css, stats, fonts)
    return px.build_palette(stats, fonts, url, [])


def test_light_site_is_detected_as_light():
    p = _palette(_LIGHT_CSS)
    assert p.is_dark is False
    assert px.luminance(p.content["bg"]) > 0.8


def test_dark_site_is_detected_as_dark():
    p = _palette(_DARK_CSS)
    assert p.is_dark is True
    assert px.luminance(p.content["bg"]) < 0.2


def test_accent_picks_the_saturated_brand_colour():
    p = _palette(_LIGHT_CSS)
    # #635bff is the only strongly saturated colour used repeatedly.
    assert px.to_hls(p.content["ac"])[2] > 0.4
    assert px._hue_distance(px.to_hls(p.content["ac"])[0],
                            px.to_hls("#635bff")[0]) < 0.06


def test_body_text_clears_wcag_aa_against_the_background():
    for css in (_LIGHT_CSS, _DARK_CSS):
        p = _palette(css)
        assert px.contrast(p.content["tx"], p.content["bg"]) >= 4.5
        assert px.contrast(p.content["tx2"], p.content["bg"]) >= 4.4


def test_semantic_hues_follow_the_site_when_present():
    p = _palette(_LIGHT_CSS)
    # The fixture ships a green and a red; they should be recognised as such.
    assert px._hue_distance(px.to_hls(p.content["gn"])[0], 0.33) < 0.08
    assert px._hue_distance(px.to_hls(p.content["rd"])[0], 0.0) < 0.08


def test_palette_fills_every_shipped_token():
    """A palette must cover every content token, or applying it leaves holes."""
    p = _palette(_LIGHT_CSS)
    assert set(tp.DEFAULT_CONTENT) <= set(p.content)


def test_chrome_stays_dark_and_readable():
    p = _palette(_LIGHT_CSS)
    assert px.luminance(p.chrome["bg"]) < 0.35
    assert px.contrast(p.chrome["tx"], p.chrome["bg"]) >= 7.0


def test_fonts_are_extracted_and_generics_skipped():
    p = _palette(_LIGHT_CSS)
    assert "Söhne" in p.fonts.get("ui", "")
    assert "Mono" in p.fonts.get("mono", "")


def test_empty_css_raises():
    with pytest.raises(ValueError):
        _palette("/* nothing here */")


def test_extract_palette_rejects_a_non_http_url():
    with pytest.raises(ValueError):
        px.extract_palette("ftp://example.test")
    with pytest.raises(ValueError):
        px.extract_palette("   ")


# ---------------------------------------------------------------------------
# Preference storage
# ---------------------------------------------------------------------------

@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    """Point the storage singleton at a scratch dir for one test."""
    from core.storage import reset_storage
    monkeypatch.setenv("ER_STORAGE_BACKEND", "local")
    monkeypatch.setenv("ER_LOCAL_ROOT", str(tmp_path))
    reset_storage()
    yield tmp_path
    reset_storage()


def test_save_load_roundtrip(storage_root):
    tp.save_overrides({"content": {"ac": "#ff0000"}, "chrome": {}, "font": {}},
                      oid="u1")
    assert tp.load_overrides("u1")["content"]["ac"] == "#ff0000"


def test_defaults_are_not_persisted(storage_root):
    """Storing a token at its shipped value is a no-op, not a stored override."""
    default_ac = tp.DEFAULT_CONTENT["ac"]
    tp.save_overrides({"content": {"ac": default_ac}, "chrome": {}, "font": {}},
                      oid="u1")
    assert tp.load_overrides("u1")["content"] == {}


def test_users_are_isolated(storage_root):
    tp.save_overrides({"content": {"ac": "#ff0000"}, "chrome": {}, "font": {}}, oid="u1")
    tp.save_overrides({"content": {"ac": "#00ff00"}, "chrome": {}, "font": {}}, oid="u2")
    assert tp.load_overrides("u1")["content"]["ac"] == "#ff0000"
    assert tp.load_overrides("u2")["content"]["ac"] == "#00ff00"


def test_clear_overrides_removes_only_that_user(storage_root):
    tp.save_overrides({"content": {"ac": "#ff0000"}, "chrome": {}, "font": {}}, oid="u1")
    tp.save_overrides({"content": {"ac": "#00ff00"}, "chrome": {}, "font": {}}, oid="u2")
    tp.clear_overrides("u1")
    assert tp.load_overrides("u1")["content"] == {}
    assert tp.load_overrides("u2")["content"]["ac"] == "#00ff00"


def test_corrupt_prefs_file_degrades_to_defaults(storage_root):
    (storage_root / "_theme_prefs.json").write_text("{not json")
    assert tp.load_overrides("u1") == {"content": {}, "chrome": {}, "font": {}}


def test_saved_file_is_readable_json(storage_root):
    tp.save_overrides({"content": {"bg": "#101010"}, "chrome": {}, "font": {}}, oid="u1")
    data = json.loads((storage_root / "_theme_prefs.json").read_text())
    assert data["u1"]["content"]["bg"] == "#101010"
    assert "updated_at" in data["u1"]


# ---------------------------------------------------------------------------
# Application to the live config
# ---------------------------------------------------------------------------

def test_apply_to_config_is_idempotent():
    """Re-applying must rebuild from defaults, never compound."""
    original = dict(config.COLORS)
    try:
        tp.apply_to_config({"content": {"ac": "#ff0000"}, "chrome": {}, "font": {}})
        assert config.COLORS["ac"] == "#ff0000"
        tp.apply_to_config({"content": {}, "chrome": {}, "font": {}})
        assert config.COLORS["ac"] == tp.DEFAULT_CONTENT["ac"]
        assert config.COLORS == tp.DEFAULT_CONTENT
    finally:
        config.COLORS.clear()
        config.COLORS.update(original)


def test_apply_to_config_touches_chrome_too():
    original = dict(config.DARK_COLORS)
    try:
        tp.apply_to_config({"content": {}, "chrome": {"bg": "#123456"}, "font": {}})
        assert config.DARK_COLORS["bg"] == "#123456"
    finally:
        config.DARK_COLORS.clear()
        config.DARK_COLORS.update(original)


def test_font_css_is_empty_when_nothing_changed():
    assert tp.font_css({"content": {}, "chrome": {}, "font": {}}) == ""


def test_font_css_emits_and_clamps_scale():
    css = tp.font_css({"font": {"ui": "Georgia, serif", "scale": "9.0"}})
    assert "Georgia, serif" in css
    assert "125.0%" in css          # clamped from 9.0 to the 1.25 ceiling


def test_font_css_survives_a_junk_scale():
    css = tp.font_css({"font": {"ui": "Georgia, serif", "scale": "not-a-number"}})
    assert "Georgia, serif" in css


@pytest.mark.parametrize("name,expected", [
    ("Brian McCune", "BM"),
    ("Brian (local dev)", "BM"),
    ("Ada Lovelace Smith", "AL"),
    ("", "BM"),
])
def test_initials(name, expected):
    assert tp.initials_for(name) == expected


def test_every_registry_token_exists_in_the_shipped_palette():
    """The editor must not offer a token the app doesn't actually read."""
    for _title, _help, tokens in tp.TOKEN_GROUPS:
        for scope, key, _label in tokens:
            source = tp.DEFAULT_CHROME if scope == "chrome" else tp.DEFAULT_CONTENT
            assert key in source, f"{scope}.{key} is not a real theme token"
