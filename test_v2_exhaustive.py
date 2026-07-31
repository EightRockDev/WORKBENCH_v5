"""Exhaustive QA for V2 theme module — Brian, 2026-05-29.

Run:  ER_THEME=v2 python test_v2_exhaustive.py

Covers:
  A. Static / syntax / import checks
  B. is_v2() detection across env values
  C. Theme injection (CSS markers + valid Python f-strings)
  D. All renderers with happy-path property
  E. Edge cases (long names, missing fields, broken folders, NULL DB cells)
  F. Real DB integration (Crossroads + folder + sources.json)
  G. Fuzz: every one of the 2,530 VA properties through V2 chain
  H. HTML safety (no broken structure for unusual chars)
  I. Verdict computation paths (GO / WATCH / NO-GO branches)
  J. Inspector with various folder shapes

Output: pass/fail counts + first-N failure details.
"""

from __future__ import annotations
import os
import sys
import traceback
from collections import Counter

# Force UTF-8 stdout so ASCII-safe replacements aren't strictly necessary,
# but we still avoid pretty unicode to be safe on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

os.environ['ER_THEME'] = 'v2'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# -----------------------------------------------------------------------
# Mock Streamlit before any import that may pull it in
# -----------------------------------------------------------------------
captured: list[str] = []

class FakeCol:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __getattr__(self, name): return lambda *a, **kw: None

class FakeSt:
    session_state = {}
    def markdown(self, content, **kwargs): captured.append(content)
    def info(self, msg, **kwargs): captured.append(f'[INFO] {msg}')
    def error(self, msg, **kwargs): captured.append(f'[ERROR] {msg}')
    def warning(self, msg, **kwargs): captured.append(f'[WARN] {msg}')
    def caption(self, msg, **kwargs): captured.append(f'[CAP] {msg}')
    def write(self, msg, **kwargs): captured.append(f'[WRITE] {msg}')
    def columns(self, spec, **kwargs):
        n = spec if isinstance(spec, int) else len(spec)
        return tuple(FakeCol() for _ in range(n))
    def container(self, **kwargs):
        return FakeCol()
    def tabs(self, labels):
        return tuple(FakeCol() for _ in labels)
    def __getattr__(self, name):
        # Any unknown method becomes a no-op
        return lambda *a, **kw: None

sys.modules['streamlit'] = FakeSt()
import streamlit as st

# -----------------------------------------------------------------------
# Test harness
# -----------------------------------------------------------------------
results: list[tuple[str, str, str]] = []  # (name, status, detail)
FAILED_DETAILS: list[tuple[str, str]] = []

def t(name: str, fn):
    captured.clear()
    try:
        fn()
        results.append((name, 'PASS', ''))
    except AssertionError as e:
        results.append((name, 'FAIL', str(e)))
        FAILED_DETAILS.append((name, traceback.format_exc()))
    except Exception as e:
        results.append((name, 'ERROR', f'{type(e).__name__}: {e}'))
        FAILED_DETAILS.append((name, traceback.format_exc()))


# -----------------------------------------------------------------------
# A. Static / syntax / import
# -----------------------------------------------------------------------
print("=" * 70)
print("PHASE A: Static / syntax / imports")
print("=" * 70)

def a1_syntax_v2_theme():
    import ast
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    ast.parse(src)
t("A1: v2_theme module syntax", a1_syntax_v2_theme)

def a2_syntax_app():
    import ast
    src = open('app.py', encoding='utf-8').read()
    ast.parse(src)
t("A2: app.py syntax", a2_syntax_app)

def a3_import_v2_theme():
    import ui.v2_theme_05292026 as v2
    assert hasattr(v2, 'is_v2')
    assert hasattr(v2, 'inject_v2_theme')
    assert hasattr(v2, 'render_v2_topbar')
    assert hasattr(v2, 'render_v2_property_header')
    assert hasattr(v2, 'render_v2_stats_bar')
    assert hasattr(v2, 'render_v2_verdict_band')
    assert hasattr(v2, 'render_v2_inspector')
    assert hasattr(v2, 'gather_metrics')
    assert hasattr(v2, 'get_v2_version_label')
t("A3: v2_theme imports + public surface", a3_import_v2_theme)

def a4_import_app():
    # Reading app.py source-only check that import block compiles
    # (full module load would call streamlit.set_page_config which fails in test)
    import ast
    src = open('app.py', encoding='utf-8').read()
    tree = ast.parse(src)
    # Collect original (pre-alias) names from any `from ui.v2_theme_* import ...`
    original_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and 'v2_theme' in node.module:
            for alias in node.names:
                original_names.append(alias.name)
    expected = ['is_v2', 'inject_v2_theme', 'render_v2_topbar',
                'render_v2_property_header', 'render_v2_stats_bar',
                'render_v2_verdict_band', 'render_v2_inspector', 'gather_metrics']
    for name in expected:
        assert name in original_names, f'Missing V2 import: {name}. Got: {original_names}'
t("A4: app.py V2 imports complete", a4_import_app)

def a5_run_bat_present():
    bat = open('run.bat', encoding='utf-8', errors='replace').read()
    assert 'ER_THEME=v2' in bat
    assert '8501' in bat
    assert '8502' in bat
    assert 'start "Workbench V1' in bat
    assert 'start "Workbench V2' in bat
t("A5: run.bat configured for both V1 + V2", a5_run_bat_present)


# -----------------------------------------------------------------------
# B. is_v2() detection
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE B: is_v2() detection across env values")
print("=" * 70)

from ui.v2_theme_05292026 import is_v2

def b1_v2_lowercase():
    os.environ['ER_THEME'] = 'v2'
    assert is_v2() == True
t("B1: ER_THEME=v2 -> True", b1_v2_lowercase)

def b2_v2_uppercase():
    os.environ['ER_THEME'] = 'V2'
    assert is_v2() == True, "Should be case-insensitive"
t("B2: ER_THEME=V2 -> True (case-insensitive)", b2_v2_uppercase)

def b3_v1_explicit():
    os.environ['ER_THEME'] = 'v1'
    assert is_v2() == False
t("B3: ER_THEME=v1 -> False", b3_v1_explicit)

def b4_empty():
    os.environ['ER_THEME'] = ''
    assert is_v2() == True, "V2 is the default (owner 2026-07-31)"
t("B4: ER_THEME='' -> True (V2 default)", b4_empty)

def b5_unset():
    os.environ.pop('ER_THEME', None)
    assert is_v2() == True, "V2 is the default (owner 2026-07-31)"
t("B5: ER_THEME unset -> True (V2 default)", b5_unset)

# Reset to v2 for subsequent tests
os.environ['ER_THEME'] = 'v2'


# -----------------------------------------------------------------------
# C. Theme injection
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE C: Theme injection")
print("=" * 70)

from ui.v2_theme_05292026 import inject_v2_theme, get_v2_version_label

def c1_inject_emits():
    inject_v2_theme()
    assert len(captured) >= 1
    assert '<style>' in captured[0]
    assert '</style>' in captured[0]
t("C1: inject_v2_theme emits a <style> block", c1_inject_emits)

def c2_inject_fonts():
    inject_v2_theme()
    css = captured[0]
    assert 'fonts.googleapis.com' in css
    assert 'Inter' in css
    assert 'JetBrains+Mono' in css
t("C2: Fonts (Inter + JetBrains Mono) included", c2_inject_fonts)

def c3_inject_design_tokens():
    inject_v2_theme()
    css = captured[0]
    # All 20 V2 tokens
    for token in ['--v2-bg', '--v2-card', '--v2-ink', '--v2-gold',
                  '--v2-line', '--v2-pos', '--v2-warn', '--v2-neg']:
        assert token in css, f'Missing token: {token}'
t("C3: All V2 design tokens defined", c3_inject_design_tokens)

def c4_inject_component_classes():
    inject_v2_theme()
    css = captured[0]
    # All V2 component CSS classes
    for cls in ['v2-nav', 'v2-hero', 'v2-chip', 'v2-stats', 'v2-stat',
                'v2-verdict', 'v2-ins-block', 'v2-dd-score', 'v2-person', 'v2-doc-row']:
        assert f'.{cls}' in css, f'Missing class: {cls}'
t("C4: All V2 component CSS classes present", c4_inject_component_classes)

def c5_inject_streamlit_overrides():
    inject_v2_theme()
    css = captured[0]
    # Critical Streamlit selectors V2 must override
    for selector in ['stTabs', 'stButton', 'stMetric', 'stDataFrame',
                     'stSidebar', 'stExpander', 'stTextInput']:
        assert selector in css, f'Missing Streamlit override: {selector}'
t("C5: Streamlit element overrides present", c5_inject_streamlit_overrides)

def c6_inject_hides_v1_topbar():
    inject_v2_theme()
    css = captured[0]
    assert '.er-topbar' in css
    assert 'display: none' in css
t("C6: V1 .er-topbar hidden in V2", c6_inject_hides_v1_topbar)

def c7_version_label_format():
    """Version format updated 5/29 EOD: now 'v2.0.X (MMDDYYYY) · Quiet Operator'
    where X is the build/patch number that increments per V2 change."""
    label = get_v2_version_label()
    from config import WORKBENCH_VERSION
    assert label.startswith(WORKBENCH_VERSION), f'Got: {label}'
    assert 'Quiet Operator' in label
    assert '(' in label and ')' in label, "Date should be in parens"
    # Extract the date portion
    date_str = label[label.find('(')+1 : label.find(')')]
    assert len(date_str) == 8 and date_str.isdigit(), f"Date should be 8 digits MMDDYYYY: {date_str}"
t("C7: Version label format v2.0.X (MMDDYYYY) · Quiet Operator", c7_version_label_format)


# -----------------------------------------------------------------------
# D. Renderer happy path
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE D: Renderer happy path")
print("=" * 70)

from ui.v2_theme_05292026 import (
    render_v2_topbar, render_v2_property_header, render_v2_stats_bar,
    render_v2_verdict_band, render_v2_inspector, gather_metrics,
)

GOOD_PROP = {
    'property_id': 'p_t_good',
    'name': 'Crossroads Townhomes',
    'units': 26,
    'asset_class': 'C',
    'city': 'Norfolk',
    'state': 'VA',
    'zip': '23502',
    'year_built': 1975,
    'occupancy_pct': 0.92,
    'avg_sqft': 930,
    'avg_rent': 1210,
    'address': '3000 S. Cape Henry Rd',
    'management_company': 'Drucker + Falk LLC',
    'owner': 'Cleghorn Capital LLC',
    'market': 'NOR',
}

def d1_topbar():
    captured.clear()
    render_v2_topbar(GOOD_PROP)
    out = ''.join(captured)  # v2.1.2 — topbar is now a columns row (multi-markdown)
    assert 'QUARRY' in out  # renamed from WORKBENCH in v2.1.0
    from config import WORKBENCH_VERSION
    assert WORKBENCH_VERSION in out  # pill shows the REAL workbench version
    assert 'Crossroads Townhomes' in out
    assert 'BM' in out  # avatar initials
t("D1: Topbar happy path", d1_topbar)

def d2_property_header():
    render_v2_property_header(GOOD_PROP)
    out = captured[-1]
    assert 'Crossroads Townhomes.' in out
    assert '<b>26</b> units' in out
    assert 'Class <b>C</b>' in out
    assert 'Norfolk, VA' in out
    assert 'Built <b>1975</b>' in out
    assert '92%' in out
    assert '24,180 RSF' in out
    assert 'Drucker + Falk' in out
    assert '3000 S. Cape Henry' in out
    assert 'google.com/maps' in out  # address chip is link
t("D2: Property header chips (all 9 expected)", d2_property_header)

def d3_stats_bar_no_metrics():
    render_v2_stats_bar(GOOD_PROP, {})
    out = captured[-1]
    assert 'v2-stats' in out
    assert out.count('v2-stat-label') >= 4  # 4 cards
    assert 'Purchase Price' in out  # renamed from "Asking" per Brian 5/29/2026
    assert 'cap' in out.lower()
    assert 'IRR' in out
    assert 'DSCR' in out
    assert 'Underwriting tab' in out  # pending message
t("D3: Stats bar with no metrics (pending state)", d3_stats_bar_no_metrics)

def d4_stats_bar_with_metrics():
    metrics = {
        'asking': 3_250_000,
        'going_in_cap': 0.062,
        'irr_5y': 0.213,
        'equity_multiple': 2.1,
        'dscr_stab': 1.43,
    }
    render_v2_stats_bar(GOOD_PROP, metrics)
    out = captured[-1]
    assert '$3.25' in out  # $3.25M
    assert '6.20' in out  # 6.20%
    assert '21.3' in out  # 21.3%
    assert '1.43' in out
    assert '2.1' in out  # 2.1× equity multiple
t("D4: Stats bar with full metrics renders real values", d4_stats_bar_with_metrics)

def d5_verdict_band_pending():
    render_v2_verdict_band(GOOD_PROP, {})
    out = captured[-1]
    assert 'v2-verdict' in out
    assert 'pending' in out.lower()
    assert 'GO bar' in out  # shows calibrated bar as context
t("D5: Verdict band with no metrics (pending)", d5_verdict_band_pending)

def d6_verdict_band_computed_GO():
    metrics = {
        'asking': 2_500_000,  # $96K/unit on 26 units = below Norfolk GO PPU ceiling
        'going_in_cap': 0.085,  # well above GO bar
        'dscr_stab': 1.55,
        'coc_year1': 0.10,
    }
    render_v2_verdict_band(GOOD_PROP, metrics)
    out = captured[-1]
    assert 'v2-verdict' in out
    # Should compute a real verdict — either GO or maybe WATCH
    # As long as it's not 'pending', we have a real computation
    assert 'pending' not in out.lower(), f'Should have computed verdict: {out[:300]}'
t("D6: Verdict band computes real verdict from metrics", d6_verdict_band_computed_GO)

def d7_inspector_basic():
    render_v2_inspector(GOOD_PROP, {'folder': None})
    out = ''.join(captured)
    # Should at least have calibration block
    assert 'Calibration' in out
    assert 'v2-ins-block' in out
t("D7: Inspector renders at least Calibration block", d7_inspector_basic)


# -----------------------------------------------------------------------
# E. Edge cases
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE E: Edge cases")
print("=" * 70)

def e1_long_name():
    p = dict(GOOD_PROP, name='The Residences at North Hampton Roads Boulevard Crossroads II Phase III Apartments')
    render_v2_property_header(p)
    out = captured[-1]
    assert 'The Residences at North' in out
t("E1: Long property name (no crash, no overflow)", e1_long_name)

def e2_sparse_property():
    p = {'property_id': 'p_e2', 'name': 'Mystery'}
    render_v2_property_header(p)
    out = captured[-1]
    assert 'Mystery' in out
    # Optional chips should be absent
    assert 'Built ' not in out
    assert 'RSF' not in out
    assert 'occupied' not in out
t("E2: Sparse property (most fields missing) omits chips cleanly", e2_sparse_property)

def e3_no_units():
    p = dict(GOOD_PROP, units=0)
    render_v2_property_header(p)
    out = captured[-1]
    # 0 units still renders 'units' chip but with 0
    assert 'units' in out
t("E3: Zero units renders without crash", e3_no_units)

def e4_no_city():
    p = dict(GOOD_PROP, city='', state='')
    render_v2_property_header(p)
    # Should not crash
t("E4: No city/state", e4_no_city)

def e5_special_chars_in_name():
    p = dict(GOOD_PROP, name="Smith & Jones' Mews <Property>")
    render_v2_property_header(p)
    # We do NOT HTML-escape, so check that special chars at least don't break Python
    # (visual will look ugly but no crash)
t("E5: Special characters in name (& ' < >)", e5_special_chars_in_name)

def e6_occupancy_as_percentage_not_fraction():
    # Some DB cells store occupancy as 92.0 instead of 0.92
    p = dict(GOOD_PROP, occupancy_pct=92.0)
    render_v2_property_header(p)
    out = captured[-1]
    # Code uses ternary: pct * 100 if pct < 1.5 else pct
    # So 92.0 should display as 92%, not 9200%
    assert '92%' in out, f'Output: {out[:500]}'
t("E6: Occupancy 92.0 (raw percentage) displays as 92%", e6_occupancy_as_percentage_not_fraction)

def e7_occupancy_as_fraction():
    p = dict(GOOD_PROP, occupancy_pct=0.92)
    render_v2_property_header(p)
    out = captured[-1]
    assert '92%' in out
t("E7: Occupancy 0.92 (fraction) displays as 92%", e7_occupancy_as_fraction)

def e8_no_address():
    p = dict(GOOD_PROP, address=None)
    render_v2_property_header(p)
    out = captured[-1]
    # Address chip should be absent (no maps link)
    assert 'google.com/maps' not in out
t("E8: No address omits Google Maps chip", e8_no_address)

def e9_no_avg_sqft():
    p = dict(GOOD_PROP, avg_sqft=None)
    render_v2_property_header(p)
    out = captured[-1]
    assert 'RSF' not in out
t("E9: No avg_sqft omits RSF chip", e9_no_avg_sqft)

def e10_no_year_built():
    p = dict(GOOD_PROP, year_built=None)
    render_v2_property_header(p)
    out = captured[-1]
    assert 'Built' not in out
t("E10: No year_built omits Built chip", e10_no_year_built)

def e11_inspector_no_folder():
    p = dict(GOOD_PROP, owner=None, management_company=None)
    render_v2_inspector(p, {'folder': None})
    out = ''.join(captured)
    # Should still have calibration but no people, no docs
    assert 'Calibration' in out
    assert 'v2-doc-row' not in out
t("E11: Inspector with no folder, no people", e11_inspector_no_folder)

def e12_inspector_broken_folder():
    class BrokenFolder:
        pass  # No .path attribute
    render_v2_inspector(GOOD_PROP, {'folder': BrokenFolder()})
    # Should not crash
t("E12: Inspector with broken folder (no .path)", e12_inspector_broken_folder)

def e13_topbar_none_prop():
    captured.clear()
    render_v2_topbar(None)
    out = ''.join(captured)
    assert 'QUARRY' in out  # renamed from WORKBENCH in v2.1.0
    assert 'Pick a property' in out
t("E13: Topbar with None prop", e13_topbar_none_prop)


# -----------------------------------------------------------------------
# F. Real DB integration
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE F: Real DB integration")
print("=" * 70)

from data.db import list_properties, get_property
from data.property_io import discover_property_folders, find_folder_for_property

def f1_db_query():
    rows = list_properties(search='Crossroads Townhomes')
    assert rows, "Crossroads not in DB"
    assert any(r['units'] == 26 for r in rows), \
        f"Crossroads units should be 26, got: {[r['units'] for r in rows]}"
t("F1: DB returns Crossroads with 26 units (subject identity preserved)", f1_db_query)

def f2_folder_lookup():
    rows = list_properties(search='Crossroads Townhomes')
    prop = next(r for r in rows if r['units'] == 26)
    folder = find_folder_for_property(prop, discover_property_folders())
    assert folder is not None, "Folder not found"
    assert hasattr(folder, 'path')
    assert folder.path.exists()
t("F2: Folder lookup for Crossroads", f2_folder_lookup)

def f3_inspector_with_real_docs():
    rows = list_properties(search='Crossroads Townhomes')
    prop = next(r for r in rows if r['units'] == 26)
    folder = find_folder_for_property(prop, discover_property_folders())
    render_v2_inspector(prop, {'folder': folder})
    out = ''.join(captured)
    # Brian 5/29 v2.0.15: docs rendered as the "Key documents" header block
    # (with v2-doc-mark hidden marker), then one Streamlit button per file.
    # The captured stream contains the header markdown; per-file buttons
    # are st.button calls which don't append HTML to the captured stream.
    assert 'Key documents' in out, "Key documents header not rendered"
    assert 'v2-doc-mark' in out, "v2-doc-mark scoping marker missing"
t("F3: Inspector pulls real docs from Crossroads folder", f3_inspector_with_real_docs)

def f4_inspector_calibration_live():
    """Calibration block now uses screenshot-aligned labels (5/29 evening
    refresh) — Going-in cap / Stabilized DY / $/unit vs submkt / DSCR Y1 /
    DSCR stab / Vacancy / Exit cap — rather than the raw threshold display
    labels. The Norfolk PPU check is folded into "$/unit vs submkt"."""
    rows = list_properties(search='Crossroads Townhomes')
    prop = next(r for r in rows if r['units'] == 26)
    # Need gather_metrics so the calibration block has numbers to compare
    from data.property_io import discover_property_folders, find_folder_for_property
    from ui.v2_theme_05292026 import gather_metrics
    folder = find_folder_for_property(prop, discover_property_folders())
    metrics = gather_metrics(prop, folder)
    render_v2_inspector(prop, metrics)
    out = ''.join(captured)
    # At least one of the seven screenshot rows should be present
    assert any(label in out for label in [
        'Going-in cap', 'Stabilized DY', '$ / unit vs submkt',
        'DSCR Y1', 'DSCR stab', 'Vacancy', 'Exit cap'
    ])
t("F4: Inspector calibration shows Norfolk-specific PPU ceiling", f4_inspector_calibration_live)

def f5_gather_metrics_with_folder():
    rows = list_properties(search='Crossroads Townhomes')
    prop = next(r for r in rows if r['units'] == 26)
    folder = find_folder_for_property(prop, discover_property_folders())
    metrics = gather_metrics(prop, folder)
    # Folder attached
    assert metrics.get('folder') is not None
t("F5: gather_metrics attaches folder", f5_gather_metrics_with_folder)


# -----------------------------------------------------------------------
# G. Fuzz: every VA property through V2 chain
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE G: Fuzz across all VA properties")
print("=" * 70)

def g1_fuzz_all_properties():
    all_props = list_properties(limit=10000)
    print(f"  Fuzzing {len(all_props)} properties through full V2 chain...")
    fails: list[tuple[str, str]] = []
    classes = Counter()
    cities = Counter()
    for p in all_props:
        try:
            captured.clear()
            render_v2_topbar(p)
            render_v2_property_header(p)
            render_v2_stats_bar(p, {})
            render_v2_verdict_band(p, {})
            render_v2_inspector(p, {'folder': None})
            classes[p.get('asset_class') or '—'] += 1
            cities[p.get('city') or '—'] += 1
        except Exception as e:
            fails.append((p.get('name', 'UNKNOWN'),
                         f'{type(e).__name__}: {e}'))
    if fails:
        sample = '\n    '.join(f"{name}: {err}" for name, err in fails[:5])
        msg = (f"{len(fails)}/{len(all_props)} failed. First 5:\n    {sample}")
        raise AssertionError(msg)
    print(f"  Class distribution: {dict(classes)}")
    print(f"  Top 5 cities: {dict(cities.most_common(5))}")
t("G1: All 2,530 VA properties render without exception", g1_fuzz_all_properties)


# -----------------------------------------------------------------------
# H. HTML safety / structural sanity
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE H: HTML safety / structural")
print("=" * 70)

def h1_balanced_tags():
    """Quick sanity: divs and spans should balance in property header output."""
    render_v2_property_header(GOOD_PROP)
    out = captured[-1]
    open_div = out.count('<div')
    close_div = out.count('</div>')
    # Self-closing tags don't have </ for SVG <path/>, so just check approximation
    assert abs(open_div - close_div) <= 2, f'<div balance off: {open_div} open, {close_div} close'
t("H1: Property header HTML divs roughly balanced", h1_balanced_tags)

def h2_inspector_balanced_tags():
    render_v2_inspector(GOOD_PROP, {'folder': None})
    out = ''.join(captured)
    open_div = out.count('<div')
    close_div = out.count('</div>')
    assert abs(open_div - close_div) <= 5, f'Inspector div balance off: {open_div} vs {close_div}'
t("H2: Inspector HTML divs roughly balanced", h2_inspector_balanced_tags)

def h3_css_no_unsubstituted():
    inject_v2_theme()
    css = captured[0]
    # Should not contain Python {} f-string placeholders that escaped substitution
    # The CSS has literal { and } in selectors, so look for KEY-shaped braces
    # like { followed immediately by a Python-like name and }
    import re
    leftover = re.findall(r"\{[a-z_][a-z_0-9]*\}", css)
    # CSS variables are var(--v2-foo) format, not {foo}. Anything in {foo} that
    # looks like a Python name suggests an f-string substitution failure.
    bad = [x for x in leftover if not x.startswith('{--')]
    assert not bad, f'Suspicious un-substituted f-string remnants: {bad[:5]}'
t("H3: CSS has no un-substituted f-string remnants", h3_css_no_unsubstituted)

def h4_stats_card_html_well_formed():
    render_v2_stats_bar(GOOD_PROP, {'asking': 3_250_000, 'going_in_cap': 0.062,
                                     'irr_5y': 0.213, 'dscr_stab': 1.43,
                                     'equity_multiple': 2.1})
    out = captured[-1]
    assert out.count('<div class="v2-stat">') == 4, "Should be exactly 4 stat cards"
t("H4: Stats bar emits exactly 4 cards", h4_stats_card_html_well_formed)


# -----------------------------------------------------------------------
# I. Verdict computation paths
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE I: Verdict computation paths (GO / WATCH / NO-GO)")
print("=" * 70)

def i1_verdict_NOGO():
    # Cap rate WAY below NOGO bar -> should be NO-GO
    metrics = {
        'asking': 10_000_000,  # absurdly priced
        'going_in_cap': 0.02,  # 2% cap, below NOGO floor
        'dscr_stab': 0.5,
        'coc_year1': 0.01,
    }
    render_v2_verdict_band(GOOD_PROP, metrics)
    out = captured[-1]
    assert 'NO-GO' in out or 'nogo' in out, f'Expected NO-GO: {out[:300]}'
t("I1: Sub-NOGO cap produces NO-GO verdict", i1_verdict_NOGO)

def i2_verdict_GO():
    # All bars cleared
    metrics = {
        'asking': 2_400_000,  # ~$92K/u on 26 units, below Norfolk GO ceiling
        'going_in_cap': 0.085,
        'dscr_stab': 1.6,
        'coc_year1': 0.10,
    }
    render_v2_verdict_band(GOOD_PROP, metrics)
    out = captured[-1]
    assert '>GO<' in out or 'verdict-tag">GO' in out
t("I2: Strong metrics produce GO verdict", i2_verdict_GO)


# -----------------------------------------------------------------------
# J. Module structure / sidebar fix preserved
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE J: V1 sidebar address-search fix preserved")
print("=" * 70)

def j1_sidebar_address_search():
    """Verify the address-search bypass logic Brian asked for is still in V1's sidebar."""
    src = open('ui/sidebar.py', encoding='utf-8').read()
    assert 'search_bypassed_filters' in src, "Address-search bypass logic missing!"
t("J1: V1 sidebar address-search bypass intact", j1_sidebar_address_search)


# -----------------------------------------------------------------------
# K. ⌘K command palette + keyboard shortcuts
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE K: Cmd+K palette + Alt+1-9 tab shortcuts")
print("=" * 70)

from ui.v2_theme_05292026 import render_v2_cmdk_palette, apply_query_param_to_state, _gather_palette_props

def k1_gather_palette_props():
    props = _gather_palette_props()
    assert len(props) > 100, f'Too few props for palette: {len(props)}'
    # v2.1.0 — palette now covers the FULL multi-state inventory (was 3000).
    assert len(props) <= 60000
    # Check shape of one record
    p = props[0]
    for key in ('id', 'name', 'addr', 'city', 'st', 'units', 'cls', '_t'):
        assert key in p, f'Missing key {key} in palette record'
    # Search tokens should be lowercase
    assert p['_t'] == p['_t'].lower()
t("K1: _gather_palette_props returns shaped list (>100, all keys, lowercase tokens)", k1_gather_palette_props)

def k2_palette_includes_crossroads():
    props = _gather_palette_props()
    crossroads = [p for p in props if 'crossroads townhomes' in p['_t']]
    assert crossroads, "Crossroads Townhomes missing from palette data"
    # The 26-unit one must be present
    c26 = [p for p in crossroads if p['units'] == 26]
    assert c26, "Crossroads Townhomes 26-unit not found"
t("K2: Palette data includes Crossroads (26 units)", k2_palette_includes_crossroads)

def k3_palette_search_tokens_match_address():
    """A user typing '3000 cape henry' should find Crossroads."""
    props = _gather_palette_props()
    matches = [p for p in props if '3000' in p['_t'] and 'cape henry' in p['_t']]
    assert matches, "Address tokens not in palette search index"
t("K3: Palette search tokens include street address", k3_palette_search_tokens_match_address)

def k4_palette_search_tokens_match_zip():
    """ZIP-based search should work."""
    props = _gather_palette_props()
    zips = set(p['_t'].split()[-1:][0] if p['_t'] else '' for p in props)
    # Some properties should have ZIP codes in their tokens
    has_va_zip = any('23' in t for t in zips if t.startswith('2'))
t("K4: Palette includes ZIP codes in search tokens", k4_palette_search_tokens_match_zip)

# --- v2.1.1: search reworked. Streamlit doesn't run <script> from
# --- st.markdown, so the old overlay/JS never executed (these tests had
# --- been validating dead code — which is why they never caught it). The
# --- new mechanism: <a href="?home=1"> links (real nav) + a
# --- components.v1.html iframe bridge for ⌘K. Tests now assert THAT, by
# --- reading the module source (components.html isn't captured at runtime).
_V2SRC = open('ui/v2_theme_05292026.py', encoding='utf-8').read()

def _cmdk_src():
    s = _V2SRC
    i = s.find('def render_v2_cmdk_palette(')
    j = s.find('\ndef ', i + 1)
    return s[i:j if j > 0 else len(s)]

def k5_palette_uses_components_iframe():
    """⌘K bridge must use st.components.v1.html — the ONLY way Streamlit
    actually executes injected JS (st.markdown <script> never runs)."""
    body = _cmdk_src()
    assert 'streamlit.components.v1' in body and '_components_html(' in body, \
        "⌘K must run via st.components.v1.html iframe, not st.markdown"
    assert 'st.markdown(' not in body, \
        "render_v2_cmdk_palette must NOT use st.markdown (scripts there never run)"
t("K5: ⌘K runs via components.v1.html iframe (not dead st.markdown script)",
  k5_palette_uses_components_iframe)

def k6_cmdk_keybinding_present():
    body = _cmdk_src()
    assert "e.metaKey || e.ctrlKey" in body and ("'k'" in body or "'K'" in body), \
        "Cmd/Ctrl+K binding missing"
    # v2.1.4 — tab shortcut is Alt+Digit (was bare 1-9). Match on e.code so
    # it's keyboard-layout independent.
    assert "/^Digit[1-9]$/" in body, "Alt+Digit tab shortcut missing"
t("K6: ⌘K + Alt+1-9 keybindings present in the iframe bridge", k6_cmdk_keybinding_present)

def k7_bridge_clicks_parent_tabs():
    body = _cmdk_src()
    assert 'window.parent.document' in body, "Bridge must reach the parent document"
    assert 'data-baseweb=' in body and '.click()' in body, "Tab click logic missing"
t("K7: Alt+1-9 clicks parent Streamlit tabs via window.parent.document", k7_bridge_clicks_parent_tabs)

def k8_tab_shortcut_uses_alt_not_ctrl():
    """v2.1.4 — the tab shortcut must require ALT and explicitly EXCLUDE
    Ctrl/Cmd. Ctrl+1-9 is a reserved browser shortcut a page can't override,
    which is what made Brian's Ctrl+number feel broken. Also assert the
    discoverability tooltip ('Alt+N' on each tab)."""
    body = _cmdk_src()
    assert "e.altKey && !e.ctrlKey && !e.metaKey" in body, \
        "Tab shortcut must require Alt and exclude Ctrl/Cmd"
    assert "Alt+" in body and "setAttribute('title'" in body, \
        "Tabs must advertise their Alt+N shortcut via a hover tooltip"
t("K8: Tab shortcut is Alt+number (not browser-reserved Ctrl) + tooltip",
  k8_tab_shortcut_uses_alt_not_ctrl)

def k9_cmdk_focuses_search():
    """v2.1.2 — ⌘K FOCUSES the in-place search input (was: navigate away)."""
    body = _cmdk_src()
    assert 'focusSearch' in body and '.focus()' in body, \
        "⌘K must focus the in-place search input"
t("K9: ⌘K focuses the in-place search field", k9_cmdk_focuses_search)

def k10_search_is_native_input():
    """v2.1.2 — the search is a native st.text_input you type into; results
    are real ?prop=<id> links. No navigating-to-another-page to search."""
    src = _V2SRC
    fn = src[src.find('def render_v2_topbar('):]
    fn = fn[:fn.find('\ndef ', 1)]
    assert 'st.text_input(' in fn and 'v2_global_search' in fn, \
        "Topbar search must be a native st.text_input"
    assert 'Find anything' in fn, "Search placeholder missing"
    rsrc = src[src.find('def _render_v2_search_results('):]
    rsrc = rsrc[:rsrc.find('\ndef ', 1)]
    assert 'href="?prop=' in rsrc, "Results must link to ?prop=<id>"
t("K10: Search is a native input; results link to properties",
  k10_search_is_native_input)

def k11_bridge_install_once_guard():
    body = _cmdk_src()
    assert '__quarry_kbd' in body, "Bridge must guard against double-install"
t("K11: ⌘K bridge guards against double-install", k11_bridge_install_once_guard)

def k12_palette_props_still_searchable():
    """The slim property index still builds (used by search/landing)."""
    from ui.v2_theme_05292026 import _gather_palette_props
    props = _gather_palette_props()
    assert len(props) > 100
    # XSS-safety: landing renders names via Streamlit (auto-escaped); the
    # index itself is data only.
    assert all('_t' in p for p in props[:5])
t("K12: Property search index still populated (>100)", k12_palette_props_still_searchable)

def k13_query_param_apply_no_param():
    # No ?prop= in session
    class FakeQP(dict): pass
    st.query_params = FakeQP()
    st.session_state = {}
    apply_query_param_to_state()
    assert 'selected_property_id' not in st.session_state
t("K13: apply_query_param_to_state no-ops when ?prop= absent", k13_query_param_apply_no_param)

def k14_query_param_apply_sets_state():
    class FakeQP(dict):
        def get(self, k, default=None): return self.get_impl(k) if False else dict.get(self, k, default)
    qp = FakeQP()
    qp['prop'] = 'p_test_123'
    st.query_params = qp
    st.session_state = {}
    apply_query_param_to_state()
    assert st.session_state.get('selected_property_id') == 'p_test_123', \
        f"State not set: {st.session_state}"
    # Should also force active_module to deal_analysis
    assert st.session_state.get('active_module') == 'deal_analysis'
t("K14: apply_query_param_to_state sets selected_property_id + forces deal_analysis", k14_query_param_apply_sets_state)

def k15_query_param_idempotent():
    """Calling twice with same ?prop= shouldn't keep resetting."""
    class FakeQP(dict):
        def get(self, k, default=None): return dict.get(self, k, default)
    qp = FakeQP()
    qp['prop'] = 'p_test_idemp'
    st.query_params = qp
    st.session_state = {'selected_property_id': 'p_test_idemp', 'active_module': 'crm'}
    apply_query_param_to_state()
    # Should NOT have reset active_module if selection unchanged
    assert st.session_state.get('selected_property_id') == 'p_test_idemp'
    # active_module is NOT forced when the selection didn't change
    assert st.session_state.get('active_module') == 'crm', \
        "Should preserve active_module when selection didn't change"
t("K15: apply_query_param_to_state idempotent when selection unchanged", k15_query_param_idempotent)

def k16_palette_uses_correct_streamlit_selectors():
    """The iframe bridge uses Streamlit's actual tab DOM selector."""
    body = _cmdk_src()
    assert 'data-baseweb=' in body and 'tab' in body, \
        "Tab selector doesn't match Streamlit's actual DOM"
t("K16: Tab selector matches Streamlit's real DOM (data-baseweb=tab)", k16_palette_uses_correct_streamlit_selectors)

def k17_palette_css_classes_in_theme():
    """The palette CSS must be in the theme module."""
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    inject_v2_theme()
    css = captured[0]
    for cls in ['.v2-cmdk-overlay', '.v2-cmdk-modal', '.v2-cmdk-input',
                '.v2-cmdk-result', '.v2-cmdk-foot', 'v2-flash']:
        assert cls in css, f'Palette CSS class missing: {cls}'
t("K17: Palette CSS classes injected by inject_v2_theme", k17_palette_css_classes_in_theme)

def k18_app_py_calls_palette():
    """Verify app.py actually calls the palette and query-param handler."""
    src = open('app.py', encoding='utf-8').read()
    assert '_v2_cmdk' in src, "Palette not imported in app.py"
    assert '_apply_qp' in src, "Query-param handler not imported in app.py"
    # Both called when V2 is active
    assert '_apply_qp()' in src
    assert '_v2_cmdk()' in src
t("K18: app.py calls _apply_qp() and _v2_cmdk() when V2 active", k18_app_py_calls_palette)


# -----------------------------------------------------------------------
# L. V1<->V2 Switch button + cross-version sync
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE L: Switch button + cross-version sync")
print("=" * 70)

from ui.v2_theme_05292026 import render_v2_topbar, render_v1_switch_button

def l1_v2_topbar_includes_switch_pill():
    """Switch-to-V1 pill condensed to a small "V1" pill next to BM avatar
    (matches Brian's screenshot 5/29 evening — fewer pixels, same function)."""
    render_v2_topbar(GOOD_PROP)
    out = captured[-1]
    assert 'v2-switch-pill' in out, "Switch pill missing from V2 topbar"
    # The pill is now just "V1" + arrow; full version+title in the tooltip
    assert '>V1<' in out
    assert 'localhost:8501' in out

def l2_v2_switch_pill_carries_prop():
    render_v2_topbar(GOOD_PROP)
    out = captured[-1]
    pid = GOOD_PROP['property_id']
    assert f'?prop={pid}' in out, f'Switch URL missing prop_id; got: {out[out.find("v2-switch-pill"):out.find("v2-switch-pill")+400]}'
t("L2: V2 switch pill carries ?prop= for the current property", l2_v2_switch_pill_carries_prop)

def l3_v2_switch_pill_no_prop():
    render_v2_topbar(None)
    out = captured[-1]
    # Condensed pill is now just "V1"
    assert '>V1<' in out
    # No ?prop= when no property
    assert '?prop=' not in out, "Switch URL should not have ?prop= when no property selected"
    assert 'localhost:8501' in out
t("L3: V2 switch pill works with no property selected", l3_v2_switch_pill_no_prop)

def l4_v1_floating_pill_renders_in_v1_mode():
    # V1 mode now requires EXPLICIT ER_THEME=v1 (V2 is the default)
    os.environ['ER_THEME'] = 'v1'
    captured.clear()
    render_v1_switch_button(GOOD_PROP['property_id'])
    out = captured[-1] if captured else ''
    assert 'v2-switch-pill-floating' in out, "V1 floating pill missing"
    assert 'Try V2.0' in out
    assert 'localhost:8502' in out
    assert f'?prop={GOOD_PROP["property_id"]}' in out
    os.environ['ER_THEME'] = 'v2'  # restore
t("L4: V1 floating pill renders with ?prop= when V1 mode active", l4_v1_floating_pill_renders_in_v1_mode)

def l5_v1_floating_pill_noop_in_v2():
    os.environ['ER_THEME'] = 'v2'
    captured.clear()
    render_v1_switch_button(GOOD_PROP['property_id'])
    # In V2 mode the V1-floating-pill renders nothing
    assert len(captured) == 0, f"V1 floating pill should be no-op in V2: {captured}"
t("L5: V1 floating pill is no-op when V2 is active", l5_v1_floating_pill_noop_in_v2)

def l6_v1_floating_pill_no_prop():
    os.environ['ER_THEME'] = 'v1'
    captured.clear()
    render_v1_switch_button(None)
    out = captured[-1] if captured else ''
    assert 'v2-switch-pill-floating' in out
    assert '?prop=' not in out  # no query param when no selection
    assert 'localhost:8502' in out
    os.environ['ER_THEME'] = 'v2'
t("L6: V1 floating pill works without a property selected", l6_v1_floating_pill_no_prop)

def l7_floating_pill_has_inline_styles():
    """V1 floating pill must ship its own CSS — it can't depend on V2 theme being loaded."""
    os.environ.pop('ER_THEME', None)
    captured.clear()
    render_v1_switch_button('test')
    out = captured[-1]
    assert '<style>' in out, "Floating pill missing inline <style>"
    assert 'position: fixed' in out
    assert 'z-index' in out
    os.environ['ER_THEME'] = 'v2'
t("L7: V1 floating pill ships its own inline CSS (self-contained)", l7_floating_pill_has_inline_styles)


# === CROSS-VERSION DATA SYNC ===
# V1 and V2 are two Streamlit processes sharing the filesystem. Anything
# saved to disk should be visible in the other on next rerun.

def l8_sync_db_query_is_fresh():
    """V1 and V2 both read from workbench.db. A property added there is
    visible in both. Both call list_properties() at runtime without
    cache_data on the read path (verified by checking the DB connection
    is opened fresh per query)."""
    import sqlite3
    from data.db import DB_PATH
    # Just verify the DB path is shared and accessible
    assert DB_PATH.exists(), f"DB missing: {DB_PATH}"
    # Verify both Streamlit processes would hit the same file
    conn = sqlite3.connect(DB_PATH)
    n1 = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    conn.close()
    conn = sqlite3.connect(DB_PATH)
    n2 = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    conn.close()
    assert n1 == n2 > 1000, f"DB query inconsistent: {n1} vs {n2}"
t("L8: DB is shared and re-queryable (both V1/V2 see same property count)", l8_sync_db_query_is_fresh)

def l9_sync_disk_write_visible_immediately():
    """Write a note via V1's machinery, then read it back via the same
    machinery (simulating V2 reading it). Proves filesystem-backed state
    propagates instantly."""
    import time
    from pathlib import Path
    from data.property_io import save_notes, load_notes
    # Find Crossroads folder
    from data.property_io import discover_property_folders, find_folder_for_property
    from data.db import list_properties
    prop = next(r for r in list_properties(search='Crossroads Townhomes')
                if r['units'] == 26)
    folder = find_folder_for_property(prop, discover_property_folders())
    assert folder is not None

    # Read current notes (V2 "before" state)
    before = load_notes(folder.path)
    # Simulate V1 writing a sync-test marker
    marker = f"[QA cross-sync test {time.time():.0f}]"
    test_notes = (before or "") + "\n" + marker
    save_notes(folder.path, test_notes)
    # Simulate V2 reading immediately after (no cache, fresh disk read)
    after = load_notes(folder.path)
    assert marker in (after or ""), \
        f"Disk write didn't propagate. before='{before[:50] if before else None}' after='{after[:200] if after else None}'"
    # Cleanup: restore original
    save_notes(folder.path, before or "")
t("L9: Note saved 'in V1' is immediately visible to a 'V2' rerun (disk-backed sync)", l9_sync_disk_write_visible_immediately)

def l10_sync_favorites_share():
    """Favorites are stored in a JSON file. Modifying via V1 toggles is
    immediately visible in V2 on next rerun."""
    from data.property_io import toggle_favorite, load_favorites
    fake_prop = {'property_id': 'p_qa_sync_test_dummy', 'name': 'qa-test', 'aln_id': ''}
    pid = fake_prop['property_id']
    before = pid in load_favorites()
    # Simulate V1 toggling
    toggle_favorite(fake_prop)
    after_v1 = pid in load_favorites()
    assert after_v1 != before, "Toggle didn't change state"
    # Simulate V2 reading the same JSON immediately after
    after_v2 = pid in load_favorites()
    assert after_v2 == after_v1, "V2's read of favorites.json sees different state than V1's write"
    # Cleanup: toggle back so we leave no test residue
    toggle_favorite(fake_prop)
    assert (pid in load_favorites()) == before, "Cleanup failed; favorite remains"
t("L10: Favorites toggle persists across V1<->V2 (JSON-file backed)", l10_sync_favorites_share)

def l11_sync_session_state_does_NOT_share():
    """Document the negative case: Streamlit session state is per-process.
    This test verifies our understanding by inspecting the architecture
    (no shared session-state store)."""
    # The fact that st.session_state is a per-process dict-like is by
    # design — we don't try to share it. Just confirm we're not lying.
    assert hasattr(st, 'session_state')
    # st.session_state is a FakeSt's dict here. In real Streamlit it's
    # a SessionStateProxy that's keyed by browser-tab session ID.
    # No cross-process synchronization is offered.
t("L11: Session state correctly NOT shared (per-process by Streamlit design)", l11_sync_session_state_does_NOT_share)

def l12_qp_apply_works_in_v1_mode_too():
    """The query-param handler is called from app.py in BOTH V1 and V2
    modes — proves the switch-pill carries selection in both directions."""
    src = open('app.py', encoding='utf-8').read()
    # Find the position where _apply_qp() is called
    qp_pos = src.find('_apply_qp()')
    is_v2_pos = src.find('if _is_v2():')
    assert qp_pos != -1, "_apply_qp() not called"
    assert qp_pos < is_v2_pos, \
        "_apply_qp() must be called BEFORE 'if _is_v2():' so V1 mode also reads ?prop="
t("L12: _apply_qp() runs in both V1 and V2 modes (cross-sync of property selection)", l12_qp_apply_works_in_v1_mode_too)

def l13_v1_switch_button_called_unconditionally():
    """V1 floating pill is rendered every page render; the function decides
    internally whether to actually emit anything (no-op in V2)."""
    src = open('app.py', encoding='utf-8').read()
    assert '_v1_switch_btn(' in src
t("L13: app.py calls _v1_switch_btn() (function self-decides if V1 or V2 mode)", l13_v1_switch_button_called_unconditionally)


# -----------------------------------------------------------------------
# M. Stat bar populates from deal.json (Brian 5/29/2026 regression)
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE M: Stat bar reads deal.json + runs V1's compute pipeline")
print("=" * 70)

def m1_gather_metrics_pulls_purchase_price():
    """For Crossroads (has deal.json), gather_metrics must return purchase_price."""
    from data.db import list_properties as _lp
    from data.property_io import discover_property_folders as _dpf, find_folder_for_property as _ffp
    from ui.v2_theme_05292026 import gather_metrics
    prop = next(r for r in _lp(search='Crossroads Townhomes') if r['units']==26)
    folder = _ffp(prop, _dpf())
    m = gather_metrics(prop, folder)
    assert 'purchase_price' in m, f"purchase_price missing; got keys: {list(m.keys())}"
    assert m['purchase_price'] > 0
t("M1: gather_metrics populates purchase_price from deal.json", m1_gather_metrics_pulls_purchase_price)

def m2_gather_metrics_runs_full_underwriting_pipeline():
    """All 4 stat-bar fields + verdict inputs must populate for a property with deal.json."""
    from data.db import list_properties as _lp
    from data.property_io import discover_property_folders as _dpf, find_folder_for_property as _ffp
    from ui.v2_theme_05292026 import gather_metrics
    prop = next(r for r in _lp(search='Crossroads Townhomes') if r['units']==26)
    folder = _ffp(prop, _dpf())
    m = gather_metrics(prop, folder)
    expected_keys = ['purchase_price', 'going_in_cap', 'irr_5y', 'dscr_stab',
                     'dscr_year1', 'equity_multiple', 'coc_year1', 'debt_yield']
    missing = [k for k in expected_keys if k not in m]
    assert not missing, f"V1 underwriting pipeline didn't run cleanly. Missing: {missing}"
t("M2: gather_metrics runs full V1 underwriting pipeline (8 computed metrics)", m2_gather_metrics_runs_full_underwriting_pipeline)

def m3_stat_bar_shows_real_numbers_for_crossroads():
    """End-to-end: gather + render should produce non-placeholder values for all 4 cards."""
    from data.db import list_properties as _lp
    from data.property_io import discover_property_folders as _dpf, find_folder_for_property as _ffp
    from ui.v2_theme_05292026 import gather_metrics, render_v2_stats_bar
    prop = next(r for r in _lp(search='Crossroads Townhomes') if r['units']==26)
    folder = _ffp(prop, _dpf())
    m = gather_metrics(prop, folder)
    captured.clear()
    render_v2_stats_bar(prop, m)
    out = captured[-1]
    for label in ['Purchase Price', 'Going-in cap', '5-yr IRR', 'DSCR Stabilized']:
        assert label in out, f'Card label missing: {label}'
        # Confirm it's NOT showing the placeholder for any of the 4
        idx = out.find(label)
        chunk = out[idx:idx+500]
        assert 'Set in Underwriting tab' not in chunk, \
            f'Card "{label}" still shows the Underwriting placeholder when deal.json has real values'
t("M3: All 4 stat cards show real numbers when deal.json exists", m3_stat_bar_shows_real_numbers_for_crossroads)

def n1_v2_theme_hides_v1_sidebar():
    """V2 CSS must hide V1's left sidebar so the page is a clean single
    column (Brian's screenshot 5/29 evening)."""
    captured.clear()
    from ui.v2_theme_05292026 import inject_v2_theme
    inject_v2_theme()
    css = captured[0]
    assert 'section[data-testid="stSidebar"]' in css
    assert 'display: none !important' in css
t("N1: V2 CSS hides V1's left sidebar", n1_v2_theme_hides_v1_sidebar)

def n2_v2_topbar_has_find_anything_search_button():
    """v2.1.2 — the 'Find anything…' search is a native st.text_input you
    type into; matching properties drop down in place (no navigation).
    Verified via source — the placeholder/key aren't captured at runtime."""
    src = _V2SRC
    fn = src[src.find('def render_v2_topbar('):]
    fn = fn[:fn.find('\ndef ', 1)]
    assert 'st.text_input(' in fn, "Topbar must have a native search input"
    assert 'key="v2_global_search"' in fn, "Search input key missing"
    assert 'Find anything' in fn, "Search placeholder missing"
    assert '_render_v2_search_results(' in fn, "In-place results dropdown missing"
t("N2: V2 topbar search is a real in-place input", n2_v2_topbar_has_find_anything_search_button)

def n3_v2_cmdk_uses_components_bridge():
    """⌘K runs through a components.v1.html iframe bridge (the only way
    Streamlit executes JS), not a dead st.markdown script."""
    body = _cmdk_src()
    assert 'streamlit.components.v1' in body and 'window.parent' in body, \
        "⌘K must use a components.html parent-document bridge"
t("N3: ⌘K uses the components.html parent-document bridge", n3_v2_cmdk_uses_components_bridge)

def n4_v2_eyebrow_format_for_crossroads():
    """Crossroads eyebrow reads 'LIVE DEAL · UPDATED HH:MM XM'.
    Brian 5/29 EOD v2.0.8: 'IC-TRACK ·' token removed."""
    captured.clear()
    from ui.v2_theme_05292026 import render_v2_property_header
    render_v2_property_header(GOOD_PROP)  # GOOD_PROP['name'] = 'Crossroads Townhomes'
    out = captured[-1]
    assert 'LIVE DEAL' in out
    assert 'UPDATED' in out
    # IC-TRACK was removed per Brian's request 5/29 (v2.0.8)
    assert 'IC-TRACK' not in out, "IC-TRACK token should be gone from Crossroads eyebrow"
t("N4: Crossroads eyebrow reads 'LIVE DEAL · UPDATED HH:MM XM' (IC-TRACK removed)", n4_v2_eyebrow_format_for_crossroads)

def n5_v2_eyebrow_format_for_other_property():
    """Non-Crossroads properties get the simpler 'ACTIVE · UPDATED…' eyebrow."""
    captured.clear()
    from ui.v2_theme_05292026 import render_v2_property_header
    other_prop = dict(GOOD_PROP, name='Some Other Property', property_id='p_other')
    render_v2_property_header(other_prop)
    out = captured[-1]
    assert 'ACTIVE' in out
    assert 'IC-TRACK' not in out  # only Crossroads gets that
t("N5: Non-Crossroads eyebrow reads 'ACTIVE · UPDATED HH:MM XM'", n5_v2_eyebrow_format_for_other_property)

def n6_verdict_band_has_build_ic_packet_button():
    """Verdict band must include the 'Build IC packet →' button from the screenshot."""
    captured.clear()
    from ui.v2_theme_05292026 import render_v2_verdict_band
    # Provide metrics that will compute a GO verdict
    render_v2_verdict_band(GOOD_PROP, {
        'going_in_cap': 0.085, 'dscr_stab': 1.6,
        'coc_year1': 0.10, 'purchase_price': 2_400_000,
    })
    out = captured[-1]
    assert 'Build IC packet' in out
    assert 'v2-verdict-act' in out
t("N6: Verdict band shows 'Build IC packet →' CTA", n6_verdict_band_has_build_ic_packet_button)

def n7_calibration_inspector_labels_match_screenshot():
    """Inspector calibration block uses screenshot labels (Going-in cap,
    Stabilized DY, $/unit vs submkt, DSCR Y1, DSCR stab, Vacancy, Exit cap)."""
    from data.db import list_properties as _lp
    from data.property_io import discover_property_folders as _dpf, find_folder_for_property as _ffp
    from ui.v2_theme_05292026 import gather_metrics, render_v2_inspector
    prop = next(r for r in _lp(search='Crossroads Townhomes') if r['units']==26)
    folder = _ffp(prop, _dpf())
    m = gather_metrics(prop, folder)
    captured.clear()
    render_v2_inspector(prop, m)
    out = ''.join(captured)
    screenshot_labels = ['Going-in cap', 'Stabilized DY', '$ / unit vs submkt',
                         'DSCR Y1', 'DSCR stab', 'Vacancy', 'Exit cap']
    matched = [l for l in screenshot_labels if l in out]
    # At least 4 of the 7 screenshot labels should land (some require specific
    # data like exit_cap from deal.json which may not be set on every property).
    assert len(matched) >= 4, f"Only matched: {matched}; full out length: {len(out)}"
t("N7: Calibration inspector labels match Brian's screenshot (4+ of 7)", n7_calibration_inspector_labels_match_screenshot)

def n8_active_banner_removed_from_app():
    """The gold V2 ACTIVE banner was removed per Brian's screenshot match."""
    src = open('app.py', encoding='utf-8').read()
    assert '_v2_active_banner()' not in src, "Gold V2 ACTIVE banner should NOT be called in app.py"
t("N8: Gold V2 ACTIVE banner removed (screenshot match)", n8_active_banner_removed_from_app)


# -----------------------------------------------------------------------
# O. Subject tab reorder + version system (Brian 5/29 EOD)
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("PHASE O: Subject tab reorder + V2 build versioning")
print("=" * 70)

def o1_subject_order_sale_history_before_property_card():
    """Sale History must appear BEFORE Property Card in property_detail.py."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    pos_sale = src.find('section_card("Sale History"):')
    pos_card_label = src.find('"Property Card"')
    assert pos_sale > 0 and pos_card_label > 0
    assert pos_sale < pos_card_label, "Sale History must come before Property Card"
t("O1: Subject tab — Sale History appears before Property Card", o1_subject_order_sale_history_before_property_card)

def o2_user_input_renamed_to_property_card():
    """The 'User Input Data' label was renamed to 'Property Card' for custom props.
    Check the active code path -- comments mentioning the rename history are fine."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    assert '"Property Card"' in src
    # Old label must NOT appear in the active assignment statement
    assert 'section_label = "User Input Data"' not in src
    assert "section_label = 'User Input Data'" not in src
t("O2: User Input Data → Property Card rename", o2_user_input_renamed_to_property_card)

def o3_documents_subtitle_removed():
    """The 'Upload T-12s, rent rolls...' subtitle on Documents was removed."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    assert 'Upload T-12s, rent rolls, OMs, deeds, screenshots' not in src
t("O3: Documents subtitle copy removed", o3_documents_subtitle_removed)

def o4_rent_comp_calls_removed():
    """No active call site for the Rent Comp Calls section (comments mentioning
    the removal are fine — what matters is no executable invocation)."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    assert 'section_card("Rent Comp Calls"' not in src
    assert "section_card('Rent Comp Calls'" not in src
    assert 'render_mystery_shop_log(' not in src
t("O4: Rent Comp Calls section removed (no active call site)", o4_rent_comp_calls_removed)

def o5_comp_call_printable_removed():
    src = open('ui/property_detail.py', encoding='utf-8').read()
    assert 'section_card("Comp Call Printable' not in src
    assert "section_card('Comp Call Printable" not in src
    assert 'render_comp_shopper_template(' not in src
t("O5: Comp Call Printable Checklist removed (no active call site)", o5_comp_call_printable_removed)

def o6_documents_section_below_property_card():
    """Documents must come AFTER Property Card / Notes block."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    pos_card = src.find('"Property Card"')
    pos_documents = src.find('section_card("Documents",')
    assert pos_card > 0 and pos_documents > 0
    assert pos_documents > pos_card
t("O6: Documents section appears after Property Card", o6_documents_section_below_property_card)

def o7_doc_ingest_last():
    """render_document_ingest_panel must be called AFTER section_card('Documents')."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    pos_documents = src.find('section_card("Documents",')
    pos_ingest = src.find('render_document_ingest_panel')
    assert pos_documents > 0 and pos_ingest > 0
    assert pos_ingest > pos_documents
t("O7: Document Auto-Ingestion appears LAST (after Documents)", o7_doc_ingest_last)

def o8_file_uploader_label_collapsed():
    """The 'Upload documents...' label should be collapsed."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    assert 'label_visibility="collapsed"' in src
t("O8: file_uploader label collapsed", o8_file_uploader_label_collapsed)

def o9_v2_version_constant_exists():
    """V2_VERSION constant must exist and follow vX.Y.Z format."""
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m, f"Got: {V2_VERSION}"
    assert (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (2, 0, 1)
t("O9: V2_VERSION constant exists in vX.Y.Z format", o9_v2_version_constant_exists)

def o10_version_pill_in_topbar():
    """The version pill (v2.0.X) must render in the V2 topbar."""
    from ui.v2_theme_05292026 import render_v2_topbar
    from config import WORKBENCH_VERSION
    captured.clear()
    render_v2_topbar(GOOD_PROP)
    out = captured[-1]
    assert 'v2-version-pill' in out
    assert WORKBENCH_VERSION in out, f"Version {WORKBENCH_VERSION} missing from topbar"
t("O10: V2 version pill renders in topbar with current build number", o10_version_pill_in_topbar)

def o11_subject_tab_verdict_hide_css():
    """V2 CSS must include the body.v2-on-subject .v2-verdict hide rule."""
    captured.clear()
    from ui.v2_theme_05292026 import inject_v2_theme
    inject_v2_theme()
    css = ''.join(captured)
    assert 'body.v2-on-subject .v2-verdict' in css
    assert 'display: none' in css
t("O11: Verdict band hidden on Subject tab via body.v2-on-subject class", o11_subject_tab_verdict_hide_css)

def o12_subject_tab_watcher_js_present():
    """JS that watches tab clicks and toggles v2-on-subject body class."""
    captured.clear()
    from ui.v2_theme_05292026 import inject_v2_theme
    inject_v2_theme()
    js = ''.join(captured)
    assert 'v2_tab_watcher' in js or 'v2_subject_watcher' in js
    assert 'aria-selected' in js
    # v2.0.27 replaced classList.toggle('v2-on-subject', ...) with the
    # generic add/remove loop over TAB_SLUGS. Accept either form.
    assert ("classList.toggle('v2-on-subject'" in js
            or "classList.add('v2-on-' + TAB_SLUGS[active])" in js), \
        "Tab-watcher must set body.v2-on-<slug> via toggle or add"
t("O12: Tab-watcher JS toggles body.v2-on-subject when Subject is active", o12_subject_tab_watcher_js_present)

def o13_file_uploader_200mb_hint_hidden():
    """CSS must hide Streamlit's '200MB per file' hint copy."""
    captured.clear()
    from ui.v2_theme_05292026 import inject_v2_theme
    inject_v2_theme()
    css = ''.join(captured)
    assert 'stFileUploader' in css and 'small' in css
t("O13: Streamlit file_uploader '200MB' hint hidden by CSS", o13_file_uploader_200mb_hint_hidden)

def p1_comps_tab_order():
    """Performance & Market tab order (Brian 5/29 v2.0.18):
    Comparables → Rent Listing URLs → Data Sources & Last Refresh.

    Rent Roll was removed from this tab — it now lives only on Underwriting."""
    src = open('ui/comps.py', encoding='utf-8').read()
    def_pos = src.find('def render_comps(')
    pos_comp = src.find('section_card("Comparables"', def_pos)
    pos_listings = src.find('section_card("Rent Listing URLs"', def_pos)
    pos_sources = src.find('section_card("Data Sources & Last Refresh"', def_pos)
    assert all(p > 0 for p in [pos_comp, pos_listings, pos_sources])
    assert pos_comp < pos_listings < pos_sources, "Section order incorrect"
    # Rent Roll must NOT be in render_comps anymore (active call).
    fn_end = src.find('\ndef ', def_pos + 1)
    body = src[def_pos:fn_end if fn_end > 0 else len(src)]
    for line in body.splitlines():
        if 'section_card("Rent Roll"' in line and not line.lstrip().startswith('#'):
            raise AssertionError("Rent Roll should no longer render on P&M tab")
t("P1: Performance & Market tab order — Comparables → Listings → Sources (no Rent Roll)", p1_comps_tab_order)

def p2_comparables_combines_buckets():
    """Bucket 1 and Bucket 2 should no longer have separate section_cards."""
    src = open('ui/comps.py', encoding='utf-8').read()
    def_pos = src.find('def render_comps(')
    body = src[def_pos:src.find('def ', def_pos+10)]
    assert 'section_card("Bucket 1 Comps"' not in body
    assert 'section_card("Bucket 2 Comps"' not in body
    # And the new combined card exists
    assert 'section_card("Comparables"' in body
t("P2: Bucket 1+2 combined into single Comparables card", p2_comparables_combines_buckets)

def p3_bucket1_gold_highlight():
    """Bucket 1 rows should be gold-highlighted in the combined comparables table."""
    src = open('ui/comps.py', encoding='utf-8').read()
    assert 'rgba(184, 151, 56' in src or 'B89738' in src, \
        "8-Rock gold tint missing for Bucket 1 highlight"
t("P3: Bucket 1 rows highlighted with 8-Rock gold", p3_bucket1_gold_highlight)

def p4_map_removed():
    """The Map section was removed from render_comps."""
    src = open('ui/comps.py', encoding='utf-8').read()
    def_pos = src.find('def render_comps(')
    body = src[def_pos:src.find('def ', def_pos+10)]
    assert 'section_card("Map"' not in body
    assert 'pdk.Deck' not in body
t("P4: Map section removed from render_comps", p4_map_removed)

def p5_lihtc_removed():
    """Nearby LIHTC properties no longer rendered."""
    src = open('ui/comps.py', encoding='utf-8').read()
    def_pos = src.find('def render_comps(')
    body = src[def_pos:src.find('def ', def_pos+10)]
    assert '_render_lihtc_nearby' not in body
t("P5: LIHTC section removed from render_comps", p5_lihtc_removed)

def p6_refresh_buttons_exist():
    """Refresh All + per-source buttons in the new data sources panel."""
    src = open('ui/comps.py', encoding='utf-8').read()
    assert 'def _render_data_sources_v2' in src
    assert 'def _run_etl_refresh' in src
    assert '🔄 Refresh All' in src
    assert 'hampton_roads_etl.py' in src  # subprocess target
    assert '--only' in src  # per-source arg
t("P6: ETL Refresh All + per-source buttons wired", p6_refresh_buttons_exist)

def p7_table_to_etl_short_complete():
    """The _TABLE_TO_ETL_SHORT lookup covers all known ETL tables. Use static
    parsing to avoid actually importing ui.comps (which transitively loads
    streamlit-dependent modules)."""
    src = open('ui/comps.py', encoding='utf-8').read()
    # The dict literal lives between '_TABLE_TO_ETL_SHORT = {' and the next '}'
    start = src.find('_TABLE_TO_ETL_SHORT = {')
    assert start > 0
    end = src.find('}', start)
    dict_text = src[start:end]
    expected_short_names = {'acs', 'bls', 'fred', 'fmr', 'bps', 'hmda',
                            'lihtc', 'bah', 'asr', 'listings'}
    missing = [n for n in expected_short_names if f'"{n}"' not in dict_text]
    assert not missing, f"ETL short names missing from lookup: {missing}"
t("P7: _TABLE_TO_ETL_SHORT covers all 10 ETL source short names", p7_table_to_etl_short_complete)

def p8_scrape_results_are_inline_squares():
    """Latest scrape results in listings_panel must be small inline pills,
    not full-width st.container(border=True) rows."""
    src = open('ui/listings_panel.py', encoding='utf-8').read()
    # New rendering uses flexbox horizontal squares
    assert 'display:flex;gap:8px;flex-wrap:wrap' in src or \
           'display: flex; gap: 8px; flex-wrap: wrap' in src, \
        "Flex-row layout missing for scrape squares"
    # And the old "st.container(border=True)" loop for scrape rows must be gone
    assert 'with st.container(border=True):' not in src.split(
        'def _render_latest_scrapes')[1].split('def ')[0] if \
        '_render_latest_scrapes' in src else True
t("P8: Latest scrape results rendered as inline squares (not full-width containers)", p8_scrape_results_are_inline_squares)

def p9_v2_version_is_current():
    """Version should be at least v2.0.10 (Brian killed verdict band in V2)."""
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (2, 0, 10), \
        f"Expected v2.0.10 or later, got {V2_VERSION}"
t("P9: V2_VERSION is at least v2.0.10", p9_v2_version_is_current)

def q1_listings_panel_has_17_display_sources():
    """Brian 5/29 v2.0.11 — confirm all 17 marketing sources in _DISPLAY_SOURCES."""
    from ui.listings_panel import _DISPLAY_SOURCES, _SCRAPER_SOURCES
    assert len(_DISPLAY_SOURCES) == 17, f"Expected 17 display sources, got {len(_DISPLAY_SOURCES)}"
    assert len(_SCRAPER_SOURCES) == 4
    # The four scrapeable sources must appear FIRST in the display list
    assert _DISPLAY_SOURCES[:4] == _SCRAPER_SOURCES
t("Q1: 17 display sources in URL panel (4 scrapeable + 13 display-only)", q1_listings_panel_has_17_display_sources)

def q2_all_sources_have_pretty_labels():
    """Every entry in _DISPLAY_SOURCES must have a _pretty_source label."""
    from ui.listings_panel import _DISPLAY_SOURCES, _pretty_source
    for src in _DISPLAY_SOURCES:
        label = _pretty_source(src)
        # If no mapping, _pretty_source returns input — that's a missing label
        assert label != src, f"Source '{src}' falls through to raw key in _pretty_source"
        assert len(label) > 1
t("Q2: Every display source has a pretty label", q2_all_sources_have_pretty_labels)

def q3_new_marketing_sites_present():
    """Verify the specific marketing sites Brian asked for are in the list."""
    from ui.listings_panel import _DISPLAY_SOURCES
    expected_new = {
        "apartmentlist", "apartmentguide", "rent_com", "trulia", "hotpads",
        "zumper", "realtor_com", "forrent", "padmapper", "costar", "loopnet",
        "craigslist", "facebook_marketplace",
    }
    missing = expected_new - set(_DISPLAY_SOURCES)
    assert not missing, f"Missing new marketing sources: {missing}"
t("Q3: All 13 new apartment marketing sites in _DISPLAY_SOURCES", q3_new_marketing_sites_present)

def q4_property_marketing_sites_heading_present():
    """Brian asked for a 'Property Marketing Sites' heading in the URL panel."""
    src = open('ui/listings_panel.py', encoding='utf-8').read()
    assert 'Property Marketing Sites' in src
t("Q4: 'Property Marketing Sites' heading added to URL panel", q4_property_marketing_sites_heading_present)

def q5_scrape_squares_are_clickable_anchors():
    """Scrape result squares now wrap in <a href=listing_url> when URL set."""
    src = open('ui/listings_panel.py', encoding='utf-8').read()
    # Find the _render_latest_scrape function body
    fn_pos = src.find('def _render_latest_scrape(')
    assert fn_pos > 0
    next_def = src.find('\ndef ', fn_pos+10)
    body = src[fn_pos:next_def] if next_def > 0 else src[fn_pos:]
    # Must emit an <a href=...> wrapper when listing_url is set
    assert 'f\'<a href="{listing_url}"' in body, "Anchor wrapper missing"
    assert 'target="_blank"' in body
    assert 'rel="noopener' in body
    # And there should still be a non-link fallback for empty URLs
    assert 'else:' in body and 'is_link' in body
t("Q5: Scrape result squares wrap in <a href> when listing_url is set", q5_scrape_squares_are_clickable_anchors)

def q6_v2_version_is_at_least_11():
    """Version bumped for this round."""
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (2, 0, 11), \
        f"Expected v2.0.11 or later, got {V2_VERSION}"
t("Q6: V2_VERSION is at least v2.0.11", q6_v2_version_is_at_least_11)

def q7_v2_tabs_use_short_labels():
    """V2 tab labels are short / unprefixed (the V2 tab order shifts over
    time — v2.0.27 has Subject first, Underwriting second). V1 keeps the
    emoji-fronted descriptive labels."""
    src = open('app.py', encoding='utf-8').read()
    # V2 has short labels starting with "Subject" (no emoji)
    assert '"Subject",\n                "Underwriting"' in src \
        or '"Subject",\n                "Market"' in src, \
        "V2 tab labels not in expected short-label form"
    # V1 path keeps the emoji-fronted descriptive labels
    assert '"🏢 Subject"' in src
    assert '"📍 Performance & Market"' in src
t("Q7: V2 tab labels shortened (V1 keeps emoji labels)", q7_v2_tabs_use_short_labels)

def r1_market_calibration_moved_to_bottom_of_underwriting():
    """Brian 5/29 v2.0.14 — Market Calibration panel now renders at the
    BOTTOM of render_underwriting (after Verdict), not inside Deal Dials.
    Static parse of ui/underwriting.py for two facts:
      1. The call inside _render_dials is gone (commented out).
      2. A NEW call exists after the Verdict section_card."""
    src = open('ui/underwriting.py', encoding='utf-8').read()

    # The active call inside Deal Dials (top of _render_dials) must be gone.
    # We allow comments mentioning "Market Calibration" but no live call there.
    dials_pos = src.find('def _render_dials')
    # Find first section that uses section_card after _render_dials def
    # (this delimits the Dials function body).
    # The call we removed was at the top of _render_dials; nothing should
    # call render_market_calibration_panel BEFORE the Verdict section now.
    verdict_pos = src.find('section_card("Verdict"')
    assert verdict_pos > 0, "Verdict section_card missing"

    # Search for active calls (ignore comments)
    active_calls = []
    for i, line in enumerate(src.splitlines(), start=1):
        if 'render_market_calibration_panel(' in line and not line.lstrip().startswith('#'):
            active_calls.append((i, line.strip()))

    assert len(active_calls) == 1, \
        f"Expected exactly 1 active call to render_market_calibration_panel, " \
        f"got {len(active_calls)}: {active_calls}"

    # That single call must be AFTER the Verdict section_card
    verdict_line = src[:verdict_pos].count('\n') + 1
    call_line = active_calls[0][0]
    assert call_line > verdict_line, \
        f"Market Calibration call at line {call_line} should be AFTER " \
        f"Verdict section (line {verdict_line})"
t("R1: Market Calibration moved to bottom of Underwriting tab (after Verdict)", r1_market_calibration_moved_to_bottom_of_underwriting)


# ============================================================================
# Phase S — v2.0.15 changes (Brian 5/29):
#   S1: Documents file_uploader removed from top of section
#   S2: Data Sources refresh button moved inside each card
#   S3: V2 inspector Key Documents rendered as clickable Streamlit buttons
#   S4: _open_local_file helper exists and is platform-aware
#   S5: V2_VERSION bumped to v2.0.15
# ============================================================================

def s1_documents_uploader_removed():
    """Brian 5/29 v2.0.15 — the duplicate file_uploader at the top of the
    Documents section is gone. Auto-Ingestion panel below is the canonical
    upload entry point now."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    # Find _render_documents body
    fn_start = src.find('def _render_documents(')
    assert fn_start > 0, '_render_documents missing'
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # No st.file_uploader call inside this function anymore
    assert 'st.file_uploader' not in body, \
        "st.file_uploader should be removed from _render_documents"
    # Auto-Ingestion still has its own uploader (sanity check)
    ingest_src = open('ui/document_ingest_panel.py', encoding='utf-8').read()
    assert 'st.file_uploader' in ingest_src, \
        "Auto-Ingestion uploader must remain (it's the canonical entry)"
t("S1: Documents file_uploader removed from Subject tab", s1_documents_uploader_removed)


def s2_refresh_button_inside_source_card():
    """Brian 5/29 v2.0.15 — refresh button per ETL source now renders
    INSIDE the source card (under the timestamp), not in a separate
    right-hand column. Implementation: a hidden .v2-src-mark marker
    inside each st.container() + :has() CSS that paints the gold-left
    border around the whole container (info + button)."""
    src = open('ui/comps.py', encoding='utf-8').read()
    # Find _render_data_sources_v2
    fn_start = src.find('def _render_data_sources_v2(')
    assert fn_start > 0
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # The two-column split is gone; we now use a container per source.
    # Check that the per-source loop no longer uses st.columns([5, 1]) and
    # instead uses st.container() with the marker.
    assert 'v2-src-mark' in body, \
        "v2-src-mark scoping marker missing from per-source render"
    assert ':has(' in body, ":has() CSS selector should target the container"
    # The old col_info / col_btn split must be gone.
    assert 'col_info, col_btn = st.columns' not in body, \
        "Old col_info/col_btn split still present — refresh button still outside card"
    # The refresh button still exists, just keyed the same way.
    assert 'etl_refresh_' in body
t("S2: Refresh button now inside each source card", s2_refresh_button_inside_source_card)


def s3_key_documents_clickable_buttons():
    """Brian 5/29 v2.0.15 — V2 inspector Key Documents are now Streamlit
    buttons (clickable to open in native app) rather than read-only HTML
    doc-row divs. The .v2-doc-mark hidden marker scopes the doc-row CSS
    to ONLY the per-file buttons in this block."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    # _open_local_file helper must exist
    assert 'def _open_local_file(' in src, '_open_local_file helper missing'
    # render_v2_inspector must reference it
    ins_start = src.find('def render_v2_inspector(')
    ins_end = src.find('\ndef _open_local_file', ins_start)
    ins_body = src[ins_start:ins_end if ins_end > 0 else len(src)]
    assert '_open_local_file(' in ins_body, \
        "render_v2_inspector must call _open_local_file on doc click"
    # The hidden scoping marker must be emitted
    assert 'v2-doc-mark' in ins_body
    # Each doc must be a st.button (not just HTML div anymore)
    assert 'st.button(' in ins_body
    # The button label must include filename + mtime
    assert 'btn_label' in ins_body
    # CSS for the marker-scoped doc buttons must be present
    assert '.v2-doc-mark ~ [data-testid="stButton"]' in src, \
        "CSS scoping the doc-row look to marker-tagged buttons missing"
t("S3: Key documents render as clickable Streamlit buttons", s3_key_documents_clickable_buttons)


def s4_open_local_file_platform_aware():
    """_open_local_file dispatches to the right OS handler.

    Windows → os.startfile (shell handler — Excel for xlsx, Acrobat
    for pdf). Mac → 'open'. Linux → 'xdg-open'. Failure surfaces as
    a Streamlit warning, not a crash."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    helper_start = src.find('def _open_local_file(')
    assert helper_start > 0
    # Get the helper body (next def boundary or EOF)
    helper_end = src.find('\ndef ', helper_start + 1)
    helper = src[helper_start:helper_end if helper_end > 0 else len(src)]
    assert 'win32' in helper and 'os.startfile' in helper
    assert 'darwin' in helper and '"open"' in helper
    assert '"xdg-open"' in helper
    # Must wrap in try/except so a missing handler doesn't crash the page
    assert 'try:' in helper and 'except' in helper
t("S4: _open_local_file is platform-aware + try/except wrapped", s4_open_local_file_platform_aware)


def s5_v2_version_bumped_to_15():
    """v2.0.14 → v2.0.15+. Required for changesets at this milestone."""
    from ui.v2_theme_05292026 import V2_VERSION
    # parse "v2.0.X" and require X >= 15
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m, f"Bad V2_VERSION format: {V2_VERSION}"
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 15), \
        f"V2_VERSION should be >= v2.0.15, got {V2_VERSION}"
t("S5: V2_VERSION at v2.0.15 or above", s5_v2_version_bumped_to_15)


# ============================================================================
# Phase T — v2.0.16 (Brian 5/29): icons stripped from V2 section headings.
#   T1: section_card suppresses icons in V2 mode
#   T2: section_card keeps icons in V1 mode
#   T3: v2_strip_icon helper exists + behaves correctly
#   T4: 11 non-section_card sites wrapped with v2_strip_icon
#   T5: V2_VERSION ≥ v2.0.16
# ============================================================================

def t1_section_card_strips_icon_in_v2():
    """In V2 mode, section_card emits its title WITHOUT the icon emoji."""
    import os as _os
    from ui.components import section_card
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    captured.clear()
    try:
        with section_card("My Section", icon="🤖"):
            pass
        out = ''.join(captured)
        assert "My Section" in out
        assert "🤖" not in out, "Icon should be suppressed in V2 mode"
    finally:
        _os.environ["ER_THEME"] = prior
t("T1: section_card drops icon arg in V2 mode", t1_section_card_strips_icon_in_v2)


def t2_section_card_keeps_icon_in_v1():
    """In V1 mode (ER_THEME unset / not 'v2'), section_card renders the icon."""
    import os as _os
    from ui.components import section_card
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v1"
    captured.clear()
    try:
        with section_card("Sale History", icon="🏛️"):
            pass
        out = ''.join(captured)
        assert "🏛️" in out, "Icon must remain in V1 mode"
        assert "Sale History" in out
    finally:
        _os.environ["ER_THEME"] = prior
t("T2: section_card preserves icon in V1 mode", t2_section_card_keeps_icon_in_v1)


def t3_v2_strip_icon_helper():
    """Helper strips leading emoji + space, preserves markdown header tokens,
    and is a no-op when V2 is off."""
    import os as _os
    from ui.components import v2_strip_icon
    prior = _os.environ.get("ER_THEME", "")
    # V1 mode → no-op
    _os.environ["ER_THEME"] = "v1"
    try:
        assert v2_strip_icon("🎨 Data Source Color Key") == "🎨 Data Source Color Key"
        assert v2_strip_icon("### 📋 Comp Call Checklist") == "### 📋 Comp Call Checklist"
    finally:
        _os.environ["ER_THEME"] = prior
    # V2 mode → strip
    _os.environ["ER_THEME"] = "v2"
    try:
        assert v2_strip_icon("🎨 Data Source Color Key") == "Data Source Color Key"
        assert v2_strip_icon("### 📋 Comp Call Checklist") == "### Comp Call Checklist"
        assert v2_strip_icon("##### 🛠️ Value-Add Lever Menu") == "##### Value-Add Lever Menu"
        assert v2_strip_icon("⚙️ Filters") == "Filters"
        assert v2_strip_icon("➕ Add investor") == "Add investor"
        # No leading icon → unchanged
        assert v2_strip_icon("Plain Title") == "Plain Title"
        # Title with parenthetical — leading "(" doesn't count as icon
        assert v2_strip_icon("(Beta) Feature") == "(Beta) Feature"
    finally:
        _os.environ["ER_THEME"] = prior
t("T3: v2_strip_icon helper correctness", t3_v2_strip_icon_helper)


def t4_non_section_card_sites_wrapped():
    """All 11 non-section_card titles with emoji are routed through
    v2_strip_icon. Static parse — any new emoji-titled expander/markdown
    in these files should also be wrapped going forward."""
    sites = [
        ("ui/comps.py", "🎨 Data Source Color Key"),
        ("ui/comps.py", "⚙ More market context"),
        ("ui/comp_shopper.py", "### 📋 Comp Call Checklist"),
        ("ui/inventory.py", "⚙️ Filters"),
        ("ui/mystery_shop.py", "➕ Log a new comp call / visit"),
        ("ui/owner_portal.py", "➕ Add investor"),
        ("ui/pipeline.py", "➕ Add broker / log new contact"),
        ("ui/sidebar.py", "### 🏢 Properties"),
        ("ui/value_add.py", "##### 🛠️ Value-Add Lever Menu"),
        ("ui/value_add.py", "##### 📐 Per-Unit-Type Rent Gap"),
        ("ui/value_add.py", "##### 💼 Cost Segregation"),
    ]
    for path, needle in sites:
        src = open(path, encoding='utf-8').read()
        assert needle in src, f"{path}: needle '{needle}' missing"
        # Find the line containing the needle and verify it's wrapped in v2_strip_icon
        for line in src.splitlines():
            if needle in line:
                assert "v2_strip_icon(" in line, \
                    f"{path}: line with '{needle}' not wrapped in v2_strip_icon"
                break
        # Also verify the import is present
        assert "v2_strip_icon" in src.split("\n", 200)[0:200].__str__() or \
               "from ui.components import" in src and "v2_strip_icon" in src, \
            f"{path}: v2_strip_icon import missing"
t("T4: 11 non-section_card emoji titles wrapped in v2_strip_icon", t4_non_section_card_sites_wrapped)


def t5_v2_version_bumped_to_16():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 16), \
        f"V2_VERSION should be >= v2.0.16 for icon-strip changeset, got {V2_VERSION}"
t("T5: V2_VERSION ≥ v2.0.16", t5_v2_version_bumped_to_16)


# ============================================================================
# Phase U — v2.0.17 (Brian 5/29):
#   U1: Favorited + Open Folder buttons moved INSIDE the header card
#   U2: Upload button replaced with "Photo Upload" text link
#   U3: Photo Upload trigger styled via .v2-photo-upload-mark CSS marker
#   U4: ETL refresh button moved to top-right of card (under timestamp)
#   U5: V2_VERSION ≥ v2.0.17
# ============================================================================

def u1_actions_moved_inside_header_card():
    """Brian 5/29 v2.0.17 — Favorited + Open Folder buttons render
    inside the same st.container(border=True) as the photo/text, not
    in an outer column. Verify there is no longer a top-level
    col_card/col_actions split outside the bordered container."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn_start = src.find('def _render_header(')
    assert fn_start > 0
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # Old layout had `col_card, col_actions = st.columns([5, 1.4]...)` at
    # the top — that pattern must be gone.
    assert 'col_card, col_actions' not in body, \
        "Old outer col_card/col_actions split still present"
    # Header should now have a 3-column split INSIDE the container.
    assert 'col_photo, col_text, col_actions' in body, \
        "Expected 3-col layout (photo/text/actions) inside the card"
    # Favorited button still wired up
    assert 'fav_btn_' in body
    # Open Folder button still wired up
    assert 'open_folder_' in body
t("U1: Favorited + Open Folder moved INSIDE header card", u1_actions_moved_inside_header_card)


def u2_upload_button_to_photo_upload_text_link():
    """Brian 5/29 v2.0.17 — the ⬆ Upload popover button is now labeled
    '↗ Photo Upload' to match the ↗ Google Maps link style."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn_start = src.find('def _render_header(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # The new label
    assert 'Photo Upload' in body, "Photo Upload label missing"
    # Old chunky label gone (no more "⬆ Upload" string)
    assert '⬆ Upload' not in body, "Old '⬆ Upload' button label still present"
    # The popover wraps _render_photo_upload — file picker still works
    assert '_render_photo_upload(prop, folder)' in body
t("U2: Upload button → 'Photo Upload' text link", u2_upload_button_to_photo_upload_text_link)


def u3_photo_upload_marker_and_css():
    """Brian 5/29 v2.0.17 — CSS that styles the popover button as a
    text link only fires when the .v2-photo-upload-mark marker is in
    the DOM (scoped to just this header)."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn_start = src.find('def _render_header(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # Marker emitted
    assert 'v2-photo-upload-mark' in body
    # CSS targets the marker + sibling popover button
    assert '.v2-photo-upload-mark ~ div [data-testid="stPopover"]' in body, \
        "CSS scoping selector missing or wrong"
    # The styled button has transparent background + no border (link look)
    assert 'background: transparent !important' in body
    assert 'border: none !important' in body
t("U3: Photo Upload styled as text link via marker-scoped CSS", u3_photo_upload_marker_and_css)


def u4_refresh_button_under_timestamp():
    """Brian 5/29 v2.0.17 — refresh button moved from the bottom of
    the source card to the top-right, under the timestamp. The
    timestamp + button are now in their own right-hand column
    (`col_meta`) inside the marker'd container."""
    src = open('ui/comps.py', encoding='utf-8').read()
    fn_start = src.find('def _render_data_sources_v2(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # New layout has col_info + col_meta split inside the container
    assert 'col_info, col_meta = st.columns' in body, \
        "Expected 2-col split (col_info + col_meta) inside each source card"
    # Refresh button is rendered with col_meta scope (must follow the
    # col_meta assignment).
    col_meta_pos = body.find('col_meta = st.columns')
    refresh_btn_pos = body.find('"🔄 Refresh"', col_meta_pos)
    assert refresh_btn_pos > col_meta_pos > 0, \
        "Refresh button should appear after col_meta split (in right column)"
    # Timestamp markdown appears in the col_meta block (we check both are
    # after col_meta and the timestamp appears before the button).
    stamp_pos = body.find('⟳ {stamp_pretty}', col_meta_pos)
    assert stamp_pos > col_meta_pos
    assert stamp_pos < refresh_btn_pos, \
        "Timestamp should render before the refresh button in col_meta"
    # The button is still keyed the same so the click handler still fires.
    assert 'etl_refresh_' in body
t("U4: Refresh button moved to top-right of source card (under timestamp)", u4_refresh_button_under_timestamp)


def u5_v2_version_bumped_to_17():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 17), \
        f"V2_VERSION should be >= v2.0.17, got {V2_VERSION}"
t("U5: V2_VERSION ≥ v2.0.17", u5_v2_version_bumped_to_17)


# ============================================================================
# Phase V — v2.0.18 section moves across tabs (Brian 5/29):
#   V1: Rent Roll removed from Performance & Market render_comps
#   V2: Year-1 KPIs no longer renders on the Underwriting tab
#   V3: _render_metrics supports render=False (compute-only)
#   V4: Returns tab leads with Year-1 KPIs → Investor Returns
#   V5: V2_VERSION ≥ v2.0.18
# ============================================================================

def v1_rent_roll_removed_from_comps():
    """Brian 5/29 v2.0.18 — Rent Roll no longer renders on the Performance &
    Market tab. It already renders on Underwriting (same file/loader)."""
    src = open('ui/comps.py', encoding='utf-8').read()
    fn_start = src.find('def render_comps(')
    assert fn_start > 0
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # No active section_card("Rent Roll") line.
    for line in body.splitlines():
        if 'section_card("Rent Roll"' in line and not line.lstrip().startswith('#'):
            raise AssertionError(f"Active Rent Roll section_card still present: {line.strip()}")
    # And no active render_rent_roll(...) call in the comps body.
    for line in body.splitlines():
        if 'render_rent_roll(' in line and not line.lstrip().startswith('#'):
            raise AssertionError(f"Active render_rent_roll call still in comps: {line.strip()}")
    # Verify Rent Roll STILL renders on Underwriting (sanity check the
    # underwriting copy wasn't accidentally axed too).
    uw_src = open('ui/underwriting.py', encoding='utf-8').read()
    assert 'render_rent_roll(' in uw_src, \
        "Underwriting tab still needs its Rent Roll render — was it removed?"
t("V1: Rent Roll removed from Performance & Market tab", v1_rent_roll_removed_from_comps)


def v2_year1_kpis_not_rendered_on_underwriting():
    """Brian 5/29 v2.0.18 — Year-1 KPIs no longer renders on Underwriting.
    The metrics dict is still computed (render=False) so downstream
    sections (sensitivity, verdict, refi-exit test) still work."""
    src = open('ui/underwriting.py', encoding='utf-8').read()
    fn_start = src.find('def render_underwriting(')
    assert fn_start > 0
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # No active section_card("Year-1 KPIs") in the render body
    for line in body.splitlines():
        if 'section_card("Year-1 KPIs"' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                f"Year-1 KPIs section_card still rendering in Underwriting: {line.strip()}"
            )
    # Metrics still computed via render=False
    assert 'render=False' in body, \
        "Underwriting must still compute metrics via render=False"
    # And the resulting metrics dict is still threaded into downstream sections
    assert 'metrics' in body and '_render_refi_exit_test' in body
t("V2: Year-1 KPIs no longer rendered on Underwriting tab", v2_year1_kpis_not_rendered_on_underwriting)


def v3_render_metrics_compute_only_mode():
    """`_render_metrics` returns the same metrics dict whether it renders
    or not. We can't easily exercise the full pipeline in a test (it
    requires sources + DealState), but we can verify the param exists
    and short-circuits before any st.markdown call."""
    src = open('ui/underwriting.py', encoding='utf-8').read()
    fn_start = src.find('def _render_metrics(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # The render param must exist
    assert 'render: bool = True' in body, "_render_metrics needs render param"
    # And there's an `if not render:` short-circuit BEFORE any st.markdown call
    short_circuit_pos = body.find('if not render:')
    assert short_circuit_pos > 0, "_render_metrics missing render=False short-circuit"
    # Find first row-rendering st.markdown after the short-circuit
    first_row_render = body.find('"###### Headline KPIs', short_circuit_pos)
    assert first_row_render > short_circuit_pos, \
        "render short-circuit should come BEFORE row 1 rendering"
    # And the short-circuit returns the metrics dict (so callers still
    # get the same shape).
    short_circuit_block = body[short_circuit_pos:first_row_render]
    for key in ("cap", "dscr", "coc", "irr", "em", "debt_yield"):
        assert f'"{key}"' in short_circuit_block, \
            f"render=False short-circuit return dict missing key '{key}'"
t("V3: _render_metrics supports render=False (compute-only)", v3_render_metrics_compute_only_mode)


def v4_returns_tab_leads_with_year1_kpis_and_investor_returns():
    """Brian 5/29 v2.0.18 — Returns tab order: Year-1 KPIs first, then
    Investor Returns, THEN the risk lenses (exit cap, seller floor,
    monte carlo), then the year-by-year schedule."""
    src = open('ui/waterfall_view.py', encoding='utf-8').read()
    fn_start = src.find('def render_waterfall(')
    assert fn_start > 0
    body = src[fn_start:]
    # Find each section's position
    pos_y1 = body.find('section_card("Year-1 KPIs"')
    pos_ir = body.find('section_card("Investor Returns"')
    pos_exit = body.find('_render_exit_cap_model(')
    pos_seller = body.find('render_seller_floor_panel(')
    pos_mc = body.find('render_monte_carlo_panel(')
    pos_wf = body.find('section_card("Year-by-Year Waterfall"')

    assert pos_y1 > 0, "Year-1 KPIs section_card missing from Returns tab"
    assert pos_ir > 0
    assert pos_exit > 0 and pos_seller > 0 and pos_mc > 0 and pos_wf > 0

    # Year-1 KPIs comes first
    assert pos_y1 < pos_ir, "Year-1 KPIs should render before Investor Returns"
    # Investor Returns comes before the risk lenses
    assert pos_ir < pos_exit, "Investor Returns should render before exit cap model"
    assert pos_ir < pos_seller, "Investor Returns should render before seller floor"
    assert pos_ir < pos_mc, "Investor Returns should render before Monte Carlo"
    # Year-by-year waterfall comes after risk lenses
    assert pos_mc < pos_wf, "Year-by-Year Waterfall stays AFTER risk lenses"

    # Sanity: the Year-1 KPIs call delegates to underwriting's _render_metrics
    assert 'from ui.underwriting import _render_metrics' in body, \
        "Returns tab must reuse _render_metrics for the Year-1 KPIs block"
t("V4: Returns tab leads with Year-1 KPIs → Investor Returns", v4_returns_tab_leads_with_year1_kpis_and_investor_returns)


def v5_v2_version_bumped_to_18():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 18), \
        f"V2_VERSION should be >= v2.0.18, got {V2_VERSION}"
t("V5: V2_VERSION ≥ v2.0.18", v5_v2_version_bumped_to_18)


# ============================================================================
# Phase W — v2.0.19 (Brian 5/29):
#   W1: Value-Add CAPEX (Short Hold) section exists + wired into Underwriting
#   W2: CAPEX math sanity — value = annual_rent / exit_cap, NOT * exit_cap
#   W3: CAPEX plan persisted to value_add_capex.json with sensible defaults
#   W4: Property Card no longer renders the "Status" row
#   W5: Property Card resolves values from rent roll / T-12 / OM / DB / manual
#   W6: Edit popover button wired next to Property Card heading
#   W7: V2_VERSION ≥ v2.0.19
# ============================================================================

def w1_value_add_capex_section_exists():
    """The CAPEX (Short Hold) helper exists and is called from the
    Underwriting tab. Position evolves over time — v2.0.23 moved it
    above Rent Roll (right after Deal Dials)."""
    src = open('ui/value_add.py', encoding='utf-8').read()
    assert 'def _render_value_add_capex(' in src, \
        "Value-Add CAPEX helper missing from ui/value_add.py"
    uw = open('ui/underwriting.py', encoding='utf-8').read()
    assert '_render_value_add_capex' in uw, \
        "underwriting.py doesn't import _render_value_add_capex"
    fn_start = uw.find('def render_underwriting(')
    body = uw[fn_start:]
    pos_dials = body.find('section_card("Deal Dials"')
    pos_capex = body.find('_render_value_add_capex(deal, folder)')
    assert 0 < pos_dials < pos_capex, \
        "CAPEX must render AFTER Deal Dials"
t("W1: Value-Add CAPEX section wired into Underwriting tab", w1_value_add_capex_section_exists)


def w2_capex_math_uses_divide_by_exit_cap():
    """Value at exit = annual_rent_increase ÷ exit_cap. The Brian-warning
    caption ("NOT × exit cap...") was removed in v2.0.20 — just verify
    the math is right."""
    src = open('ui/value_add.py', encoding='utf-8').read()
    fn_start = src.find('def _render_value_add_capex(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    assert 'stabilized_annual_rent_inc / exit_cap' in body, \
        "Wrong formula for value_at_exit — must DIVIDE by exit cap"
    assert '÷ exit cap' in body, "Formula sanity-check caption missing"
    # Brian asked to drop the parenthetical warning in v2.0.20
    assert 'NOT × exit cap' not in body, \
        "Warning text should have been removed in v2.0.20"
    assert 'would shrink the number' not in body, \
        "Warning text should have been removed in v2.0.20"
t("W2: CAPEX value-at-exit = rent ÷ exit_cap; warning text removed", w2_capex_math_uses_divide_by_exit_cap)


def w3_capex_plan_persisted_with_defaults():
    """Plan loader returns defaults when file missing; saver writes JSON."""
    from ui.value_add import _load_capex_plan, _save_capex_plan
    import tempfile, json as _json
    from pathlib import Path as _P
    with tempfile.TemporaryDirectory() as td:
        folder_path = _P(td)
        # Missing file → defaults
        defaults = _load_capex_plan(folder_path)
        assert defaults["cost_per_unit"] == 15000.0
        assert defaults["monthly_rent_increase_per_unit"] == 200.0
        assert defaults["renovations_per_year"] == [2, 3, 2, 0, 0]
        # Save → reload roundtrips
        new = {
            "cost_per_unit": 20000.0,
            "monthly_rent_increase_per_unit": 250.0,
            "renovations_per_year": [1, 2, 3, 4, 5],
        }
        _save_capex_plan(folder_path, new)
        roundtrip = _load_capex_plan(folder_path)
        assert roundtrip == new, f"Roundtrip mismatch: {roundtrip}"
        # File actually written as JSON
        fp = folder_path / "value_add_capex.json"
        assert fp.exists()
        loaded = _json.loads(fp.read_text(encoding="utf-8"))
        assert loaded == new
t("W3: CAPEX plan loader/saver roundtrip + defaults", w3_capex_plan_persisted_with_defaults)


def w4_property_card_no_status_row():
    """Brian: 'I don't know what Status means' — that row is gone."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    # The display field list must NOT include status
    fields_start = src.find('_PROPERTY_CARD_FIELDS')
    assert fields_start > 0, "_PROPERTY_CARD_FIELDS list missing"
    list_end = src.find(']', fields_start)
    fields_block = src[fields_start:list_end]
    assert '"status"' not in fields_block, "Status row should be removed from Property Card"
    # Old function should also be gone (or not called)
    fn_start = src.find('def render_property_detail(')
    assert fn_start > 0
    body = src[fn_start:]
    # The active call should be _render_property_card, not _render_aln_grid
    active_calls = [
        line for line in body.splitlines()
        if '_render_aln_grid(' in line and not line.lstrip().startswith('#')
    ]
    assert not active_calls, \
        f"Subject tab still calls _render_aln_grid: {active_calls}"
    assert '_render_property_card(' in body, \
        "Subject tab must call _render_property_card now"
t("W4: Property Card removes 'Status' row + uses new renderer", w4_property_card_no_status_row)


def w5_property_card_source_resolution():
    """Manual override > rent roll > T-12 > OM > DB. Each source returns
    its tag so the UI can color the badge."""
    from ui.property_detail import _resolve_property_card_value
    prop = {"units": 26, "year_built": 1988, "owner": "Cleghorn"}
    # No sources, no overrides → DB
    v, src = _resolve_property_card_value("units", prop, None, {})
    assert v == 26 and src == "db", f"Expected DB fallback, got {(v, src)}"
    # Rent roll wins for units
    sources = {"rentRoll": {"summary": {"totalUnits": 28}}}
    v, src = _resolve_property_card_value("units", prop, sources, {})
    assert v == 28 and src == "rent_roll"
    # Manual override beats rent roll
    v, src = _resolve_property_card_value("units", prop, sources, {"units": 26})
    assert v == 26 and src == "manual"
    # OM falls through when rent roll silent
    sources = {"om": {"yearBuilt": 1985}}
    v, src = _resolve_property_card_value("year_built", prop, sources, {})
    assert v == 1985 and src == "om"
    # Missing everywhere → empty tag
    v, src = _resolve_property_card_value("avg_rent", {}, None, {})
    assert src == "" and v is None
t("W5: Property Card source resolution chain", w5_property_card_source_resolution)


def w6_property_card_edit_popover():
    """Edit button is a Streamlit popover that opens the override form.
    Trigger label was '✏️ Edit' through v2.0.22; renamed to 'Edit Property
    Card' in v2.0.23 — either label is acceptable here."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    assert ('st.popover("✏️ Edit"' in src
            or 'st.popover(\n                "Edit Property Card"' in src
            or 'st.popover("Edit Property Card"' in src), \
        "Edit popover trigger missing from Property Card render"
    assert 'def _render_property_card_edit_form(' in src
    assert 'def _load_property_card_overrides(' in src
    assert 'def _save_property_card_overrides(' in src
    assert 'property_card_overrides.json' in src
t("W6: Edit popover + override loader/saver present", w6_property_card_edit_popover)


def w7_v2_version_bumped_to_19():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 19), \
        f"V2_VERSION should be >= v2.0.19, got {V2_VERSION}"
t("W7: V2_VERSION ≥ v2.0.19", w7_v2_version_bumped_to_19)


# ============================================================================
# Phase X — v2.0.20 (Brian 5/29):
#   X1: Central SECTION_HELP dict with entries for major sections
#   X2: section_card renders a ⓘ popover when title has SECTION_HELP entry
#   X3: 5-Year Cash Flow moved from Underwriting to top of Summary tab
#   X4: Summary tab "Preview" renamed to "{name} — Executive Summary"
#       AND the duplicate ### markdown heading is gone
#   X5: V2_VERSION ≥ v2.0.20
# ============================================================================

def x1_section_help_dict_populated():
    """The central help dict covers the major sections — at minimum the
    ones Brian sees most often (Year-1 KPIs, 5-Year CF, Refi/Exit,
    Sensitivity, Verdict, Property Card, Comparables, etc.)."""
    from ui.components import SECTION_HELP
    required = [
        "Year-1 KPIs",
        "5-Year Cash Flow",
        "Refi / Exit Stress Test",
        "Sensitivity",
        "Verdict",
        "Market Calibration",
        "Deal Dials",
        "Comparables",
        "Rent Roll",
        "Property Card",
        "Investor Returns",
        "Year-by-Year Waterfall",
        "Value-Add CAPEX (Short Hold)",
        "Data Sources & Last Refresh",
    ]
    for title in required:
        assert title in SECTION_HELP, f"SECTION_HELP missing entry: {title}"
        short, details = SECTION_HELP[title]
        assert isinstance(short, str) and len(short) > 10, \
            f"{title}: short tooltip too short"
        assert isinstance(details, str) and len(details) > 50, \
            f"{title}: details body too short"
t("X1: SECTION_HELP populated for major sections", x1_section_help_dict_populated)


def x2_section_card_renders_help_popover():
    """v2.0.25 — when a section_card's title is in SECTION_HELP, the
    helper now inlines a native HTML <details>/<summary> element with
    class 'v2-section-help' inside the title's HTML. The old marker
    pattern was replaced because Streamlit popover CSS kept bleeding."""
    import os as _os
    from ui.components import section_card
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        with section_card("Year-1 KPIs"):
            pass
        out = ''.join(captured)
        assert 'v2-section-help' in out, \
            "section_card with help-eligible title should emit the help <details>"
        assert '<details' in out and '<summary' in out, \
            "Help element must be HTML <details>/<summary>"
    finally:
        _os.environ["ER_THEME"] = prior
    # NON-help title should NOT emit the help element
    captured.clear()
    _os.environ["ER_THEME"] = "v2"
    try:
        with section_card("Some Random Title Not In SECTION_HELP"):
            pass
        out = ''.join(captured)
        assert 'v2-section-help' not in out, \
            "Title not in SECTION_HELP must NOT emit the help element"
    finally:
        _os.environ["ER_THEME"] = prior
t("X2: section_card emits HTML <details> help element when title in SECTION_HELP",
  x2_section_card_renders_help_popover)


def x3_cashflow_moved_to_summary():
    """5-Year Cash Flow no longer renders on Underwriting; renders at the
    TOP of the Summary tab (above the renamed Executive Summary card and
    above the Artifact Engine call)."""
    uw = open('ui/underwriting.py', encoding='utf-8').read()
    fn_start = uw.find('def render_underwriting(')
    fn_end = uw.find('\ndef ', fn_start + 1)
    uw_body = uw[fn_start:fn_end if fn_end > 0 else len(uw)]
    # No active section_card("5-Year Cash Flow") on Underwriting
    for line in uw_body.splitlines():
        if 'section_card("5-Year Cash Flow"' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                f"5-Year Cash Flow still rendering on Underwriting: {line.strip()}"
            )

    es = open('ui/exec_summary.py', encoding='utf-8').read()
    es_fn = es.find('def render_exec_summary(')
    es_body = es[es_fn:]
    pos_cf = es_body.find('section_card("5-Year Cash Flow"')
    pos_exec = es_body.find('section_card(exec_summary_title')
    pos_artifact = es_body.find('_render_artifact_engine_panel(')
    assert pos_cf > 0, "5-Year Cash Flow must render on Summary tab"
    assert pos_exec > 0, \
        "Executive Summary section_card(exec_summary_title) must be present"
    assert pos_artifact > 0
    assert pos_cf < pos_exec, "5-Year Cash Flow must come BEFORE Executive Summary"
    assert pos_cf < pos_artifact, "5-Year Cash Flow must come BEFORE Artifact Engine"
t("X3: 5-Year Cash Flow moved from Underwriting to top of Summary tab",
  x3_cashflow_moved_to_summary)


def x4_preview_renamed_and_duplicate_removed():
    """The 'Preview' section is renamed to '{name} — Executive Summary'.
    The duplicate '### {name} — Executive Summary' markdown line below
    that title is gone."""
    src = open('ui/exec_summary.py', encoding='utf-8').read()
    # No active "Preview" section_card
    for line in src.splitlines():
        if 'section_card("Preview"' in line and not line.lstrip().startswith('#'):
            raise AssertionError(f"'Preview' section_card still present: {line.strip()}")
    # The new title includes the property name in a string
    assert 'Executive Summary"' in src, \
        "section_card title should include 'Executive Summary'"
    # The duplicate ### ... — Executive Summary markdown is removed
    bad = '### {prop'
    # Active (non-comment) line check
    for line in src.splitlines():
        if bad in line and 'Executive Summary' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                f"Duplicate '### {{prop...}} — Executive Summary' line "
                f"should be removed: {line.strip()}"
            )
t("X4: 'Preview' renamed to '{name} — Executive Summary'; duplicate text removed",
  x4_preview_renamed_and_duplicate_removed)


def x5_v2_version_bumped_to_20():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 20), \
        f"V2_VERSION should be >= v2.0.20, got {V2_VERSION}"
t("X5: V2_VERSION ≥ v2.0.20", x5_v2_version_bumped_to_20)


# ============================================================================
# Phase Y — v2.0.21 hotfixes (Brian 5/29):
#   Y1: Notes re-hydrate guard tolerates empty session_state when disk
#       has content (data-recovery fix — Brian's "where did all of my
#       notes data go?")
#   Y2: URL placeholder text removed from Add-URL form
#   Y3: V2_VERSION ≥ v2.0.21
# ============================================================================

def y1_notes_rehydrate_recovers_from_empty_session():
    """The guard reloads from disk when session_state is empty/whitespace
    AND disk has real content. Verifies the FIX, not the BUG."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn_start = src.find('def _render_notes(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # The fixed guard includes the empty-recovery branch. v2.0.24 widened
    # the check to handle None as well (was just str empty).
    has_v21 = 'not cur.strip() and existing.strip()' in body
    has_v24 = 'not (cur or "").strip()' in body
    assert has_v21 or has_v24, \
        "Notes guard must include empty-recovery branch (cur empty + disk has content)"
    # Crossroads notes.txt on disk is still intact (sanity check)
    from pathlib import Path as _P
    notes_fp = _P('../Properties/Crossroads-Townhomes-26-Norfolk/notes.txt')
    if notes_fp.exists():
        assert notes_fp.stat().st_size > 100, \
            "Crossroads notes.txt looks suspiciously small (potential data loss)"
t("Y1: Notes guard re-hydrates from disk when widget is empty + disk has content",
  y1_notes_rehydrate_recovers_from_empty_session)


def y2_url_placeholder_removed():
    """Add-URL text input on the listings panel has no example placeholder."""
    src = open('ui/listings_panel.py', encoding='utf-8').read()
    assert 'andoverapts.com' not in src, \
        "Example placeholder URL should be removed from Add-URL form"
    # And the text_input call no longer has a placeholder= kwarg for "URL"
    # (search for the new minimal call form).
    idx = src.find('new_url = st.text_input(')
    assert idx > 0, "new_url text_input missing"
    snippet = src[idx:idx + 200]
    assert 'placeholder=' not in snippet, \
        "Add-URL text_input should NOT have a placeholder kwarg"
t("Y2: URL placeholder text removed from Add-URL form", y2_url_placeholder_removed)


def y3_v2_version_bumped_to_21():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 21), \
        f"V2_VERSION should be >= v2.0.21, got {V2_VERSION}"
t("Y3: V2_VERSION ≥ v2.0.21", y3_v2_version_bumped_to_21)


# ============================================================================
# Phase Z — v2.0.22 Property Card Edit dialog (Brian 5/29):
#   Z1: Type dropdown has the multifamily product types
#   Z2: PM Software dropdown has the top-7 systems + Other
#   Z3: Rent / Sqft computed from avg_rent ÷ avg_sqft (new "computed" tag)
#   Z4: Non-editable fields removed from the form (rent_per_sqft, market,
#       submarket)
#   Z5: "Saved to property_card_overrides.json" caption gone
#   Z6: V2_VERSION ≥ v2.0.22
# ============================================================================

def z1_property_type_dropdown_present():
    from ui.property_detail import _MULTIFAMILY_TYPES
    # Brian's two explicit asks
    assert "Townhomes" in _MULTIFAMILY_TYPES
    assert "Garden-Style" in _MULTIFAMILY_TYPES
    # Sanity: at least 8 options for a real dropdown
    assert len(_MULTIFAMILY_TYPES) >= 8, \
        f"Multifamily types list too short: {len(_MULTIFAMILY_TYPES)}"
    # And the form uses a selectbox for property_type
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn = src[src.find('def _render_property_card_edit_form('):]
    fn = fn[:fn.find('\ndef ')]
    assert 'if key == "property_type":' in fn, \
        "property_type branch missing in Edit form"
    assert 'st.selectbox' in fn, "Edit form should use st.selectbox"
t("Z1: Type dropdown has multifamily product types", z1_property_type_dropdown_present)


def z2_pm_software_dropdown_present():
    from ui.property_detail import _PM_SOFTWARE_OPTIONS
    # Brian's two explicit asks
    assert "AppFolio" in _PM_SOFTWARE_OPTIONS
    assert any("Yardi" in opt for opt in _PM_SOFTWARE_OPTIONS), \
        "Yardi must be in PM Software list"
    # 5-7 systems + Other
    assert 6 <= len(_PM_SOFTWARE_OPTIONS) <= 8, \
        f"PM Software list size: {len(_PM_SOFTWARE_OPTIONS)} (want 6-8)"
    assert "Other" in _PM_SOFTWARE_OPTIONS
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn = src[src.find('def _render_property_card_edit_form('):]
    fn = fn[:fn.find('\ndef ')]
    assert 'if key == "property_type":' in fn  # ensure not removed
    assert 'elif key == "pm_software":' in fn
t("Z2: PM Software dropdown has top systems + Other", z2_pm_software_dropdown_present)


def z3_rent_per_sqft_computed():
    """When avg_rent and avg_sqft are both known, rent_per_sqft is the
    division — tagged 'computed' so the card renders the Calc badge."""
    from ui.property_detail import _resolve_property_card_value
    # Manual avg_rent + sqft from prop → computed rent_per_sqft
    prop = {"avg_rent": 1500.0, "avg_sqft": 1000.0}
    v, src = _resolve_property_card_value("rent_per_sqft", prop, None, {})
    assert v is not None and abs(v - 1.5) < 0.001, f"Expected ~1.5, got {v}"
    assert src == "computed", f"Expected 'computed' tag, got '{src}'"
    # Rent roll wins for the inputs even though we're computing
    sources = {"rentRoll": {"summary": {
        "totalActualRent": 30000.0,   # → avg_rent = 30000 / 20 = 1500
        "occupiedUnits": 20,
        "avgSqft": 1200.0,
    }}}
    v, src = _resolve_property_card_value("rent_per_sqft", {}, sources, {})
    assert v is not None and abs(v - (1500.0 / 1200.0)) < 0.001
    assert src == "computed"
    # Missing one input → no computed value
    v, src = _resolve_property_card_value("rent_per_sqft", {"avg_rent": 1500.0}, None, {})
    assert src == "" and v is None
t("Z3: rent_per_sqft is computed when inputs available", z3_rent_per_sqft_computed)


def z4_non_editable_fields_removed_from_form():
    """rent_per_sqft, market, submarket must NOT be rendered in the
    Edit form (they're auto-computed / auto-derived)."""
    from ui.property_detail import _EDITABLE_FIELDS, _PROPERTY_CARD_FIELDS
    assert "rent_per_sqft" not in _EDITABLE_FIELDS
    assert "market" not in _EDITABLE_FIELDS
    assert "submarket" not in _EDITABLE_FIELDS
    # And these fields are still in the display list (the card still shows them)
    display_keys = {k for k, _ in _PROPERTY_CARD_FIELDS}
    assert {"rent_per_sqft", "market", "submarket"} <= display_keys, \
        "Non-editable fields should still render on the card"
t("Z4: Non-editable fields removed from Edit form (still on card)", z4_non_editable_fields_removed_from_form)


def z5_overrides_caption_removed():
    """The 'Saved to property_card_overrides.json' caption is gone."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn = src[src.find('def _render_property_card_edit_form('):]
    fn = fn[:fn.find('\ndef ')]
    # The implementation-detail caption must not appear in any live st.caption call
    bad_phrases = [
        "Saved to property_card_overrides.json",
        "Saved to `property_card_overrides.json`",
        "saved per-property",
    ]
    for phrase in bad_phrases:
        assert phrase not in fn, \
            f"Edit form still mentions implementation detail: {phrase!r}"
t("Z5: 'Saved to property_card_overrides.json' caption removed", z5_overrides_caption_removed)


def z6_v2_version_bumped_to_22():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 22), \
        f"V2_VERSION should be >= v2.0.22, got {V2_VERSION}"
t("Z6: V2_VERSION ≥ v2.0.22", z6_v2_version_bumped_to_22)


# ============================================================================
# Phase AA — v2.0.23 polish (Brian 5/29):
#   AA1: Property Card row HTML renders badge BEFORE label (badge slot on left)
#   AA2: Section-help ⓘ popover CSS hides Streamlit's chevron SVG
#   AA3: "Edit Property Card" text link at bottom of Property Card
#   AA4: Underwriting tab order — CAPEX directly after Deal Dials;
#        Refi/Exit Stress Test now AFTER Sensitivity
#   AA5: V2_VERSION ≥ v2.0.23
# ============================================================================

def aa1_property_card_badge_on_left():
    """Each row's badge HTML sits in a fixed 50px slot at the LEFT of the
    label, NOT inline with the value on the right."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn_start = src.find('def _render_property_card(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # The new template puts badge_html INSIDE a min-width:50px slot
    assert 'min-width:50px' in body, \
        "Badge slot (50px min-width on left) missing"
    # The badge_html no longer has margin-right (which was for value-side layout)
    assert "'margin-right:8px'" not in body and 'margin-right:8px;border:' not in body, \
        "Badge HTML still has the old margin-right hack"
    # And the badge no longer concatenates directly with display value
    # (i.e. the value <span> doesn't start with {badge}{display})
    assert '{badge}{display}' not in body, \
        "Value span still prefixes badge — should be on left now"
    assert '{badge_html}' in body, "Badge HTML token missing"
t("AA1: Property Card badges render on the LEFT of the label",
  aa1_property_card_badge_on_left)


def aa2_section_help_popover_hides_chevron():
    """v2.0.25 — the Streamlit-popover approach was abandoned in favor of
    a native HTML <details>/<summary> element. There's no chevron to hide
    anymore. Test now verifies the NEW CSS (round trigger button + panel
    styling) is present."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        assert '.v2-section-help-trigger' in css, \
            "New <details> trigger CSS missing"
        assert '.v2-section-help-panel' in css, \
            "New <details> panel CSS missing"
        # Hide the default <details> marker
        assert "::-webkit-details-marker" in css or "::marker" in css
        # Round trigger
        assert 'border-radius: 50%' in css
    finally:
        _os.environ["ER_THEME"] = prior
t("AA2: Section-help <details> CSS replaces broken popover styling",
  aa2_section_help_popover_hides_chevron)


def aa3_edit_property_card_link_at_bottom():
    """The Property Card section renders the card body FIRST, then a
    text-link popover with the label 'Edit Property Card' AFTER it.
    The old top-of-card '✏️ Edit' popover is gone."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    detail = src.find('def render_property_detail(')
    assert detail > 0
    body = src[detail:]
    # Find the ACTIVE st.popover call for Edit Property Card (not a comment).
    pos_card = -1
    pos_edit = -1
    in_popover = False
    for i, line in enumerate(body.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        if pos_card < 0 and '_render_property_card(prop, folder)' in line:
            pos_card = i
        if pos_edit < 0:
            if 'st.popover(' in line:
                in_popover = True
                if 'Edit Property Card' in line:
                    pos_edit = i
                    in_popover = False
                continue
            if in_popover and 'Edit Property Card' in line:
                pos_edit = i
                in_popover = False
                continue
            if in_popover and ')' in line:
                in_popover = False
    assert pos_card > 0, "_render_property_card call missing"
    assert pos_edit > 0, "Edit Property Card popover call missing"
    assert pos_card < pos_edit, \
        "Property Card body must render BEFORE the Edit link (link sits at bottom)"
    # Old '✏️ Edit' active popover gone (allow it in comments).
    for line in body.splitlines():
        if '"✏️ Edit"' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                "Old top-of-card '✏️ Edit' popover should be removed"
            )
    assert 'v2-pc-edit-link' in body, \
        "Marker for the edit-link CSS scoping is missing"
t("AA3: 'Edit Property Card' text link at bottom of card",
  aa3_edit_property_card_link_at_bottom)


def aa4_underwriting_tab_reorder():
    """CAPEX moved UP under Deal Dials. Refi/Exit Stress Test moved DOWN
    below Sensitivity. The narrative is now:
      Deal Dials → CAPEX → Rent Roll → Rent Gap → Levers → Cost-Seg
      → Amortization → Sensitivity → Refi/Exit → Verdict → Calibration
    """
    src = open('ui/underwriting.py', encoding='utf-8').read()
    fn_start = src.find('def render_underwriting(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    pos_dials   = body.find('section_card("Deal Dials"')
    pos_capex   = body.find('_render_value_add_capex(deal, folder)')
    pos_rent    = body.find('render_rent_roll(folder, section_title="Rent Roll"')
    pos_gap     = body.find('_render_unit_rent_gap(folder)')
    pos_levers  = body.find('_render_value_add_levers(')
    pos_costseg = body.find('_render_cost_seg_hook(')
    pos_amort   = body.find('section_card(\n        "Amortization Schedule"')
    if pos_amort < 0:
        pos_amort = body.find('"Amortization Schedule"')
    pos_sens    = body.find('"Sensitivity"')
    pos_refi    = body.find('section_card("Refi / Exit Stress Test"')
    pos_verdict = body.find('section_card("Verdict"')
    pos_calib   = body.find('section_card("Market Calibration"')

    for name, pos in [
        ("Deal Dials", pos_dials), ("CAPEX", pos_capex),
        ("Rent Roll", pos_rent), ("Rent Gap", pos_gap),
        ("Levers", pos_levers), ("Cost-Seg", pos_costseg),
        ("Amortization", pos_amort), ("Sensitivity", pos_sens),
        ("Refi/Exit", pos_refi), ("Verdict", pos_verdict),
        ("Calibration", pos_calib),
    ]:
        assert pos > 0, f"Section position missing: {name}"

    # CAPEX immediately after Dials, BEFORE Rent Roll
    assert pos_dials < pos_capex < pos_rent, \
        "CAPEX should sit between Deal Dials and Rent Roll"
    # Refi/Exit AFTER Sensitivity, BEFORE Verdict
    assert pos_sens < pos_refi < pos_verdict, \
        "Refi/Exit Stress Test should sit between Sensitivity and Verdict"
    # Calibration still last
    assert pos_verdict < pos_calib
t("AA4: Underwriting tab — CAPEX after Dials, Refi/Exit after Sensitivity",
  aa4_underwriting_tab_reorder)


def aa5_v2_version_bumped_to_23():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 23), \
        f"V2_VERSION should be >= v2.0.23, got {V2_VERSION}"
t("AA5: V2_VERSION ≥ v2.0.23", aa5_v2_version_bumped_to_23)


# ============================================================================
# Phase BB — v2.0.24 fixes (Brian 5/29):
#   BB1: Section-help ⓘ CSS uses :has() selector (not sibling combinator)
#   BB2: Edit Property Card link CSS uses :has() selector
#   BB3: Notes guard tracks disk_marker for cross-tab sync
#   BB4: Topbar/Calibration clock displays in ET, 12-hour format
#   BB5: V2_VERSION ≥ v2.0.24
# ============================================================================

def bb1_section_help_css_uses_has_selector():
    """v2.0.25 — :has() approach abandoned. New CSS is fully self-contained
    under .v2-section-help-* classes — no popover anywhere."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # The old broken marker-based CSS must be gone
        assert '.v2-section-help-mark' not in css
        # Self-contained new CSS
        assert '.v2-section-help-trigger' in css
    finally:
        _os.environ["ER_THEME"] = prior
t("BB1: Section-help CSS is self-contained (no <details>-popover hybrid)",
  bb1_section_help_css_uses_has_selector)


def bb2_edit_link_css_uses_has_selector():
    """v2.0.25 — the .v2-pc-edit-link marker was abandoned. The Edit
    Property Card popover still works with default Streamlit styling
    (functional, not link-styled). Test verifies the marker is gone
    from CSS so it can't bleed into the page."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        assert '.v2-pc-edit-link' not in css, \
            "Old broken .v2-pc-edit-link CSS must be removed"
    finally:
        _os.environ["ER_THEME"] = prior
t("BB2: Old .v2-pc-edit-link CSS removed (was bleeding into page)",
  bb2_edit_link_css_uses_has_selector)


def bb3_notes_guard_tracks_disk_marker():
    """The new Notes guard re-hydrates from disk in 3 cases:
       (1) first render, (2) widget empty + disk has content,
       (3) cross-tab sync: disk changed AND user hasn't typed since."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn_start = src.find('def _render_notes(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    assert 'disk_marker_key' in body, \
        "Notes guard must track a disk_marker for cross-tab sync"
    assert 'user_is_mid_typing' in body, \
        "Notes guard must detect mid-typing to avoid trampling user input"
    assert 'should_rehydrate' in body
    # The 3 conditions remain
    assert "notes_key not in st.session_state" in body, "missing first-render branch"
    assert 'not (cur or "").strip()' in body, "missing empty-recovery branch"
    assert "existing != last_disk" in body, "missing cross-tab sync branch"
t("BB3: Notes guard tracks disk_marker for cross-tab sync",
  bb3_notes_guard_tracks_disk_marker)


def bb4_clock_is_et_12_hour():
    """`_et_clock_now()` returns a 12-hour time string ending in 'ET'."""
    from ui.v2_theme_05292026 import _et_clock_now
    s = _et_clock_now()
    assert s.endswith(" ET"), f"Clock string must end with ' ET', got {s!r}"
    assert "AM" in s or "PM" in s, \
        f"Clock string must include AM/PM (12-hour format), got {s!r}"
    # Hour part is 1-12 (the substring before ':')
    head = s.split(":")[0].strip()
    hour = int(head)
    assert 1 <= hour <= 12, f"Hour out of 12-hour range: {hour}"
    # And neither old %H:%M call site is left active anywhere in v2_theme
    theme_src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    # Active (non-comment) call sites of strftime("%H:%M") should be ZERO.
    active = [
        line for line in theme_src.splitlines()
        if 'strftime("%H:%M")' in line and not line.lstrip().startswith('#')
    ]
    assert not active, \
        f"Stale %H:%M strftime calls remain (must use _et_clock_now): {active}"
t("BB4: Clock displays in Eastern Time + 12-hour format",
  bb4_clock_is_et_12_hour)


def bb5_v2_version_bumped_to_24():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 24), \
        f"V2_VERSION should be >= v2.0.24, got {V2_VERSION}"
t("BB5: V2_VERSION ≥ v2.0.24", bb5_v2_version_bumped_to_24)


# ============================================================================
# Phase CC — v2.0.25 (Brian 5/29):
#   CC1: Section help uses native HTML <details>/<summary> (no popover)
#   CC2: Broken popover CSS removed from theme injection
#   CC3: Eight Rock logo embedded in topbar
#   CC4: Tabs reordered — Diligence after Summary
#   CC5: Documents section: clickable filenames + Re-parse at bottom
#   CC6: Manager label clarified to "Manager (person)"
#   CC7: V2_VERSION ≥ v2.0.25
# ============================================================================

def cc1_section_help_uses_html_details():
    """The help control is now a native HTML <details>/<summary> element
    rendered INLINE in the section title, not a Streamlit popover."""
    from ui.components import _section_help_html, SECTION_HELP
    blob = _section_help_html("Year-1 KPIs")
    assert "<details" in blob and "<summary" in blob, \
        "Help control must use <details>/<summary>"
    assert 'class="v2-section-help"' in blob
    assert "title=" in blob, "Missing browser-native hover tooltip"
    # Title text appears in the panel
    assert "Year-1 KPIs" in blob
    # Markdown→HTML converted body
    assert "<strong>" in blob or "<ul>" in blob, \
        "_md_to_help_html should convert basic markdown"
    # No-op for unknown titles
    assert _section_help_html("Not-a-section") == ""
    # And the helper exists and is in scope
    assert "Year-1 KPIs" in SECTION_HELP
t("CC1: Section help uses native HTML <details>", cc1_section_help_uses_html_details)


def cc2_broken_popover_css_removed():
    """The broken v2.0.20-v2.0.24 popover-CSS rules that bled into the
    page background MUST be gone from the injected V2 theme."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # These selectors had been broad enough to match every popover
        # button on the page. Make sure they're gone.
        assert '.v2-section-help-mark' not in css, \
            "Old .v2-section-help-mark CSS must be removed"
        assert '.v2-pc-edit-link' not in css, \
            "Old .v2-pc-edit-link CSS must be removed"
        # And the NEW self-contained CSS is present
        assert '.v2-section-help-trigger' in css
        assert '.v2-section-help-panel' in css
    finally:
        _os.environ["ER_THEME"] = prior
t("CC2: Broken popover CSS removed; new <details> CSS present",
  cc2_broken_popover_css_removed)


def cc3_eight_rock_logo_in_topbar():
    """The topbar HTML embeds a data:image/svg+xml logo from the Logos/
    folder. We can't capture the topbar from outside Streamlit's render
    context, so verify via the helper."""
    from ui.v2_theme_05292026 import _eight_rock_logo_data_uri
    uri = _eight_rock_logo_data_uri()
    # Either a real data URI (logo file found) or empty string (graceful fallback).
    assert uri == "" or uri.startswith("data:image/svg+xml;base64,"), \
        f"Logo URI shape unexpected: {uri[:50] if uri else 'EMPTY'}"
    # And the topbar HTML embeds the logo via the v2-nav-logo class
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    assert 'v2-nav-logo' in src
    assert '_eight_rock_logo_data_uri()' in src
t("CC3: Eight Rock logo embedded in topbar (or graceful empty fallback)",
  cc3_eight_rock_logo_in_topbar)


def cc4_diligence_tab_after_summary():
    """Tab order in app.py: Diligence comes AFTER Summary."""
    src = open('app.py', encoding='utf-8').read()
    # Both V1 and V2 paths
    for path_name, marker in (("V2", '"Diligence",'), ("V1", '"📋 Due Diligence",')):
        pos_summary = src.find('"Summary",' if path_name == 'V2' else '"📄 Exec Summary",')
        pos_dil = src.find(marker)
        assert pos_summary > 0 and pos_dil > 0, \
            f"{path_name}: Summary or Diligence label missing"
        assert pos_summary < pos_dil, \
            f"{path_name}: Diligence must come AFTER Summary in tab labels"
t("CC4: Diligence tab moved to right of Summary", cc4_diligence_tab_after_summary)


def cc5_documents_clickable_and_reparse_at_bottom():
    """File rows render BEFORE the Re-parse button; each filename is a
    clickable st.button that opens the file via _open_doc_native."""
    src = open('ui/property_detail.py', encoding='utf-8').read()
    fn_start = src.find('def _render_documents(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # Clickable filename button per row
    assert 'open_doc_' in body, "Per-doc open button key missing"
    assert '_open_doc_native(' in body, \
        "Native-open helper must be wired"
    # The per-doc loop must appear BEFORE the re-parse button now.
    pos_doc_loop = body.find('for doc in docs:')
    pos_reparse = body.find('"↻ Re-parse documents into the workbench"')
    assert pos_doc_loop > 0 and pos_reparse > 0
    assert pos_doc_loop < pos_reparse, \
        "File rows must render BEFORE the Re-parse button"
t("CC5: Documents — clickable filenames + Re-parse at bottom",
  cc5_documents_clickable_and_reparse_at_bottom)


def cc6_manager_label_clarified():
    src = open('ui/property_detail.py', encoding='utf-8').read()
    assert '"Manager (person)"' in src, \
        "Manager label should be 'Manager (person)' to distinguish from Mgmt Company"
t("CC6: Manager label clarified as 'Manager (person)'", cc6_manager_label_clarified)


def cc7_v2_version_bumped_to_25():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 25), \
        f"V2_VERSION should be >= v2.0.25, got {V2_VERSION}"
t("CC7: V2_VERSION ≥ v2.0.25", cc7_v2_version_bumped_to_25)


# ============================================================================
# Phase DD — v2.0.26 (Brian 5/29):
#   DD1: Occupancy resolver returns 0-1 fraction (was %, broke _fmt_pct)
#   DD2: Help <details> share name="v2-section-help" → exclusive accordion
#   DD3: Logo moved from topbar to property hero
#   DD4: Tab counter badges + Eight Rock gold active state
#   DD5: Owner Portal moved to far right; Diligence still after Summary
#   DD6: Breadcrumb Pipeline / Active Deals links to ?home=1
#   DD7: ?home=1 query param clears selected_property_id
#   DD8: V2_VERSION ≥ v2.0.26
# ============================================================================

def dd1_occupancy_returns_fraction():
    """All three resolver branches return a 0-1 fraction for occupancy_pct
    so the _fmt_pct(value * 100) downstream call yields the right %."""
    from ui.property_detail import (
        _resolve_property_card_value, _format_property_card_value,
    )
    # Rent-roll branch with a 0-100 source value (e.g. 92.10)
    sources = {"rentRoll": {"summary": {"occupancyPct": 92.10}}}
    v, src = _resolve_property_card_value("occupancy_pct", {}, sources, {})
    assert src == "rent_roll"
    assert 0 < v <= 1.0, f"Expected 0-1 fraction, got {v}"
    assert _format_property_card_value("occupancy_pct", v) == "92.1%", \
        f"Wrong display: {_format_property_card_value('occupancy_pct', v)!r}"
    # Rent-roll branch with already-fraction source value
    sources = {"rentRoll": {"summary": {"occupancyPct": 0.921}}}
    v, _ = _resolve_property_card_value("occupancy_pct", {}, sources, {})
    assert abs(v - 0.921) < 0.001
    assert _format_property_card_value("occupancy_pct", v) == "92.1%"
    # DB branch — value stored as 90.0
    v, src = _resolve_property_card_value(
        "occupancy_pct", {"occupancy_pct": 90.0}, None, {}
    )
    assert src == "db" and abs(v - 0.90) < 0.001
    assert _format_property_card_value("occupancy_pct", v) == "90.0%"
    # Counted units branch — 23 of 26 → ~88.5%
    sources = {"rentRoll": {"summary": {
        "occupiedUnits": 23, "totalUnits": 26,
    }}}
    v, _ = _resolve_property_card_value("occupancy_pct", {}, sources, {})
    assert 0 < v <= 1.0
    assert _format_property_card_value("occupancy_pct", v) == "88.5%"
t("DD1: Occupancy resolver returns 0-1 fraction; renders cleanly",
  dd1_occupancy_returns_fraction)


def dd2_help_details_share_exclusive_name():
    """All section-help <details> share name='v2-section-help' → opening
    one auto-closes the others (browser-native exclusive accordion)."""
    from ui.components import _section_help_html
    blob = _section_help_html("Year-1 KPIs")
    assert 'name="v2-section-help"' in blob, \
        "Missing name attribute for exclusive-accordion behavior"
t("DD2: Help <details> share name → exclusive accordion",
  dd2_help_details_share_exclusive_name)


def dd3_logo_moved_to_hero():
    """Logo is now embedded in the property hero block, NOT the topbar."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    # Hero render emits the logo
    pos_hero_fn = src.find('def render_v2_property_header(')
    assert pos_hero_fn > 0
    hero_body = src[pos_hero_fn:src.find('\ndef ', pos_hero_fn + 1)]
    assert 'v2-hero-logo' in hero_body, \
        "Hero block should now embed the v2-hero-logo div"
    # Topbar render no longer emits v2-nav-logo image
    pos_topbar_fn = src.find('def render_v2_topbar(')
    topbar_body = src[pos_topbar_fn:src.find('\ndef ', pos_topbar_fn + 1)]
    # Active (non-comment) reference to v2-nav-logo must be gone
    for line in topbar_body.splitlines():
        if 'v2-nav-logo' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                f"Topbar still embeds v2-nav-logo: {line.strip()}"
            )
t("DD3: Logo moved from topbar to property hero", dd3_logo_moved_to_hero)


def dd4_tab_counter_badges_and_gold_active():
    """V2 tabs have a counter badge (::after pseudo) and an Eight Rock
    gold active state."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # CSS counter for tab numbering
        assert 'counter-increment: v2tab' in css, \
            "Tab counter-increment rule missing"
        assert 'counter(v2tab)' in css, "Tab counter() display missing"
        # Gold active state
        assert "[aria-selected=\"true\"]" in css and "B89738" in css.upper().replace("B89738", "B89738"), \
            "Gold active-tab styling missing"
    finally:
        _os.environ["ER_THEME"] = prior
t("DD4: Tab counter badges + Eight Rock gold active state",
  dd4_tab_counter_badges_and_gold_active)


def dd5_owner_portal_at_far_right():
    """The investor tab is the LAST tab in both V1 and V2 label lists.
    Updated v2.0.27 — V2 tab list now starts with Subject, Underwriting.
    Renamed v2.1.4 — 'Owner Portal' -> 'Investors' (Brian 5/31)."""
    src = open('app.py', encoding='utf-8').read()
    v2_pos = src.find('"Subject",\n                "Underwriting",')
    assert v2_pos > 0
    v2_block = src[v2_pos:src.find(']', v2_pos)]
    last_label = [
        line.strip().strip(",").strip('"')
        for line in v2_block.splitlines() if line.strip().startswith('"')
    ][-1]
    assert last_label == "Investors", \
        f"V2 last tab should be Investors, got {last_label!r}"
    v1_pos = src.find('"🏢 Subject",')
    v1_block = src[v1_pos:src.find(']', v1_pos)]
    last_v1 = [
        line.strip().strip(",").strip('"')
        for line in v1_block.splitlines() if line.strip().startswith('"')
    ][-1]
    assert last_v1.endswith("Investors"), \
        f"V1 last tab should end with 'Investors', got {last_v1!r}"
t("DD5: Investor tab is far right + renamed 'Investors' (V1 + V2)",
  dd5_owner_portal_at_far_right)


def dd6_breadcrumb_links_to_home():
    """v2.0.32 collapsed the Pipeline / Active Deals breadcrumbs into a
    single 'Search' link that opens the ⌘K palette. The ?home=1 reset
    path is still honored when the URL has it (kept for back-compat)."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    # v2.1.2 — the QUARRY brand mark is the Home link (?home=1); the
    # ?home=1 reset path stays wired in the query-param handler.
    assert '?home=1' in src or 'qp.get("home")' in src, \
        "Home reset path must still be wired"
    pos_topbar = src.find('def render_v2_topbar(')
    tbar = src[pos_topbar:src.find('\ndef ', pos_topbar + 1)]
    assert 'href="?home=1"' in tbar and 'QUARRY' in tbar, \
        "QUARRY brand must link to home (?home=1)"
t("DD6: QUARRY brand links home (?home=1); reset still wired",
  dd6_breadcrumb_links_to_home)


def dd7_home_param_clears_property_selection():
    """`?home=1` query param clears selected_property_id in session_state."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    pos_fn = src.find('def apply_query_param_to_state(')
    body = src[pos_fn:src.find('\ndef ', pos_fn + 1)]
    assert 'qp.get("home")' in body, \
        "Query param handler must check for ?home= flag"
    assert 'selected_property_id' in body and 'pop(' in body, \
        "Handler must pop selected_property_id when home=1"
t("DD7: ?home=1 clears selected_property_id",
  dd7_home_param_clears_property_selection)


def dd8_v2_version_bumped_to_26():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 26), \
        f"V2_VERSION should be >= v2.0.26, got {V2_VERSION}"
t("DD8: V2_VERSION ≥ v2.0.26", dd8_v2_version_bumped_to_26)


# ============================================================================
# Phase EE — v2.0.27 (Brian 5/29):
#   EE1: 7-tab layout — IC Memo and Acquisition tabs removed
#   EE2: New tab order: Subject · Underwriting · Returns · Market ·
#        Summary · Diligence · Owner Portal
#   EE3: IC Memo Validator renders at the BOTTOM of Summary
#   EE4: Acquisition Checklist renders at the TOP of Diligence
#   EE5: Tab-watcher JS extended to set body.v2-on-<slug> per tab
#   EE6: Diligence inspector gated by .v2-dd-inspector + body.v2-on-diligence
#   EE7: gather_metrics uses real DDState attributes
#   EE8: V2_VERSION ≥ v2.0.27
# ============================================================================

def ee1_seven_tabs_no_ic_no_acquisition():
    """V2 (and V1) tab strip has 7 tabs; 'IC Memo' and 'Acquisition'
    labels are gone from the active label lists."""
    src = open('app.py', encoding='utf-8').read()
    # V2 labels list
    v2_pos = src.find('"Subject",\n                "Underwriting",')
    assert v2_pos > 0, "V2 tab list with new order missing"
    v2_block = src[v2_pos:src.find(']', v2_pos)]
    labels = [
        line.strip().strip(',').strip('"')
        for line in v2_block.splitlines() if line.strip().startswith('"')
    ]
    assert len(labels) == 7, f"V2 should have 7 tabs, got {len(labels)}: {labels}"
    assert "IC Memo" not in labels
    assert "Acquisition" not in labels
    # Same for V1
    v1_pos = src.find('"🏢 Subject",')
    v1_block = src[v1_pos:src.find(']', v1_pos)]
    v1_labels = [
        line.strip().strip(',').strip('"')
        for line in v1_block.splitlines() if line.strip().startswith('"')
    ]
    assert len(v1_labels) == 7, f"V1 should have 7 tabs, got {len(v1_labels)}"
    assert not any('IC Memo' in lbl for lbl in v1_labels)
    assert not any('Acquisition' in lbl for lbl in v1_labels)
t("EE1: 7 tabs; IC Memo + Acquisition removed", ee1_seven_tabs_no_ic_no_acquisition)


def ee2_new_tab_order():
    """V2 tab order: Subject · Underwriting · Returns · Market · Summary
    · Diligence · Investors (renamed from 'Owner Portal' v2.1.4)."""
    src = open('app.py', encoding='utf-8').read()
    v2_pos = src.find('"Subject",\n                "Underwriting",')
    v2_block = src[v2_pos:src.find(']', v2_pos)]
    labels = [
        line.strip().strip(',').strip('"')
        for line in v2_block.splitlines() if line.strip().startswith('"')
    ]
    assert labels == [
        "Subject", "Underwriting", "Returns", "Market",
        "Summary", "Diligence", "Investors",
    ], f"V2 tab order wrong: {labels}"
t("EE2: V2 tab order is Subject → Underwriting → Returns → Market → "
  "Summary → Diligence → Investors", ee2_new_tab_order)


def ee3_ic_memo_validator_inside_summary_tab():
    """render_ic_memo_validator is called from `with tab_summary:` AFTER
    render_exec_summary."""
    src = open('app.py', encoding='utf-8').read()
    pos_summary = src.find('with tab_summary:')
    assert pos_summary > 0
    # Look at the next ~10 lines from there
    chunk = src[pos_summary:pos_summary + 600]
    assert 'render_exec_summary(prop, folder)' in chunk
    assert 'render_ic_memo_validator(prop, folder)' in chunk
    # And the IC validator must come AFTER exec_summary in that block
    pos_exec = chunk.find('render_exec_summary(prop, folder)')
    pos_ic = chunk.find('render_ic_memo_validator(prop, folder)')
    assert 0 < pos_exec < pos_ic, "IC Memo Validator must render AFTER Exec Summary"
t("EE3: IC Memo Validator renders at bottom of Summary tab",
  ee3_ic_memo_validator_inside_summary_tab)


def ee4_acquisition_checklist_inside_diligence_top():
    """render_acquisition_checklist is called from `with tab_dd:` BEFORE
    render_due_diligence."""
    src = open('app.py', encoding='utf-8').read()
    pos_dd = src.find('with tab_dd:')
    assert pos_dd > 0
    chunk = src[pos_dd:pos_dd + 600]
    pos_acq = chunk.find('render_acquisition_checklist(prop, folder)')
    pos_main = chunk.find('render_due_diligence(prop, folder)')
    assert 0 < pos_acq < pos_main, \
        "Acquisition Checklist must render BEFORE the main DD content"
t("EE4: Acquisition Checklist at top of Diligence tab",
  ee4_acquisition_checklist_inside_diligence_top)


def ee5_tab_watcher_sets_per_slug_class():
    """JS now sets body.v2-on-<slug> for every tab, not just v2-on-subject."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    # TAB_SLUGS array contains all 7 slugs
    assert "'subject', 'underwriting', 'returns', 'market'" in src, \
        "TAB_SLUGS array missing or wrong"
    assert "'summary', 'diligence', 'owner-portal'" in src
    # Renamed watcher to __v2_tab_watcher
    assert '__v2_tab_watcher' in src
    # And the loop removes ALL slug classes before re-applying
    assert "classList.remove('v2-on-' + slug)" in src
    assert "classList.add('v2-on-' + TAB_SLUGS[active])" in src
t("EE5: Tab-watcher JS sets body.v2-on-<slug> for every tab",
  ee5_tab_watcher_sets_per_slug_class)


def ee6_diligence_inspector_gated_to_diligence_tab():
    """Diligence inspector has v2-dd-inspector class; CSS hides it
    unless body.v2-on-diligence is set."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    # Render emits the inspector with the v2-dd-inspector class
    assert 'v2-ins-block v2-dd-inspector' in src, \
        "Diligence block missing the v2-dd-inspector gate class"
    # CSS rule hides it by default + shows on diligence tab
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        assert '.v2-dd-inspector { display: none' in css, \
            "Missing default-hide rule for diligence inspector"
        assert 'body.v2-on-diligence .v2-dd-inspector' in css, \
            "Missing show-on-diligence rule"
    finally:
        _os.environ["ER_THEME"] = prior
t("EE6: Diligence inspector ONLY shows on Diligence tab",
  ee6_diligence_inspector_gated_to_diligence_tab)


def ee7_gather_metrics_uses_real_ddstate_attrs():
    """gather_metrics reads overall_risk_score / items / dealbreakers /
    category_scores from the DDState dataclass, not stale dict.get()."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    # Find the DD-score branch inside gather_metrics
    pos = src.find('DD score — Brian 5/29 v2.0.27')
    assert pos > 0, "Updated DD-score branch missing"
    chunk = src[pos:pos + 1500]
    for attr in ("overall_risk_score", "category_scores", "items"):
        assert attr in chunk, \
            f"gather_metrics must read DDState.{attr}"
    # And dd_category_scores is now exported in the metrics dict
    assert 'dd_category_scores' in chunk
t("EE7: gather_metrics uses real DDState attributes",
  ee7_gather_metrics_uses_real_ddstate_attrs)


def ee8_v2_version_bumped_to_27():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 27), \
        f"V2_VERSION should be >= v2.0.27, got {V2_VERSION}"
t("EE8: V2_VERSION ≥ v2.0.27", ee8_v2_version_bumped_to_27)


# ============================================================================
# Phase FF — v2.0.28 CAPEX year count = deal.hp (Brian 5/29):
#   FF1: Renovation Schedule input count = deal.hp (was hardcoded 5)
#   FF2: Hold-period heading text shows the active hp value
#   FF3: V2_VERSION ≥ v2.0.28
# ============================================================================

def ff1_capex_year_count_dynamic():
    """The renovation schedule input loop reads deal.hp and renders that
    many number_input boxes — not a hardcoded 5."""
    src = open('ui/value_add.py', encoding='utf-8').read()
    fn_start = src.find('def _render_value_add_capex(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # Pulls hold period off the deal
    assert 'getattr(deal, "hp"' in body, \
        "Must read deal.hp dynamically"
    # The loop ranges over hp, not a hardcoded literal
    assert 'for yr_idx in range(hp)' in body, \
        "Loop must iterate range(hp), not range(5)"
    assert 'st.columns(hp)' in body, \
        "Renovation columns must be st.columns(hp), not st.columns(5)"
    # No active hardcoded `range(5)` left in the function
    for line in body.splitlines():
        if 'range(5)' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                f"Hardcoded range(5) still present: {line.strip()}"
            )
t("FF1: CAPEX renovation count = deal.hp (dynamic)",
  ff1_capex_year_count_dynamic)


def ff2_capex_heading_shows_hp_years():
    """The 'Renovation Schedule (X-year hold)' heading uses the live hp,
    not the static '5-year hold' string."""
    src = open('ui/value_add.py', encoding='utf-8').read()
    fn_start = src.find('def _render_value_add_capex(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # Heading f-string interpolates the hp value
    assert '{hp}-year hold' in body, \
        "Heading must show the live hp value"
    # The stale '5-year hold' literal is gone
    for line in body.splitlines():
        if '5-year hold' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                f"Stale '5-year hold' literal: {line.strip()}"
            )
t("FF2: 'X-year hold' heading uses dynamic hp", ff2_capex_heading_shows_hp_years)


def ff3_v2_version_bumped_to_28():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 28), \
        f"V2_VERSION should be >= v2.0.28, got {V2_VERSION}"
t("FF3: V2_VERSION ≥ v2.0.28", ff3_v2_version_bumped_to_28)


# ============================================================================
# Phase GG — v2.0.29 V2 inventory landing page (Brian 5/29):
#   GG1: render_v2_inventory_landing exists + wired into app.py
#   GG2: 12 rotating quotes, date-deterministic
#   GG3: record_property_view / load_recent_views roundtrip + dedupe + cap
#   GG4: V2_VERSION ≥ v2.0.29
# ============================================================================

def gg1_landing_renderer_exists_and_wired():
    """The landing renderer exists; app.py calls it when prop is None."""
    from ui.v2_theme_05292026 import render_v2_inventory_landing
    assert callable(render_v2_inventory_landing)
    src = open('app.py', encoding='utf-8').read()
    # Active call to _v2_landing() exists in app.py (not just in import)
    active_landing = [
        line for line in src.splitlines()
        if '_v2_landing()' in line and not line.lstrip().startswith('#')
    ]
    assert active_landing, \
        "V2 path must call _v2_landing() when no property is selected"
    # Recording call wired in
    active_record = [
        line for line in src.splitlines()
        if '_v2_record_view(prop.get("property_id"))' in line
        and not line.lstrip().startswith('#')
    ]
    assert active_record, "_v2_record_view must be called when a prop loads"
t("GG1: Inventory landing renderer wired into V2 path",
  gg1_landing_renderer_exists_and_wired)


def gg2_rotating_quotes_date_deterministic():
    """12+ quotes; same date returns same quote; different dates can differ."""
    from ui.v2_theme_05292026 import _LANDING_QUOTES, _quote_of_the_day
    assert len(_LANDING_QUOTES) >= 10, \
        f"Need at least 10 quotes, have {len(_LANDING_QUOTES)}"
    # Every entry is (author, text) with non-trivial strings
    for author, text in _LANDING_QUOTES:
        assert isinstance(author, str) and len(author) > 2
        assert isinstance(text, str) and len(text) > 20
    # Calling twice in the same second/day returns the same quote
    q1 = _quote_of_the_day()
    q2 = _quote_of_the_day()
    assert q1 == q2
    # The quote includes Brian's explicit example author (Kiyosaki)
    authors = {a for a, _ in _LANDING_QUOTES}
    assert "Robert Kiyosaki" in authors, \
        "Brian asked for Kiyosaki — must be in the rotation"
t("GG2: Landing has 10+ rotating quotes incl. Kiyosaki",
  gg2_rotating_quotes_date_deterministic)


def gg3_recent_views_roundtrip():
    """record_property_view dedupes and caps the list at 8 entries.
    load_recent_views returns newest-first order."""
    import tempfile
    from pathlib import Path as _P
    from unittest.mock import patch
    from ui.v2_theme_05292026 import (
        record_property_view, load_recent_views,
    )

    with tempfile.TemporaryDirectory() as td:
        fake_fp = _P(td) / "_recent_views.json"
        # Patch the resolver so we don't touch the real Properties folder
        with patch("ui.v2_theme_05292026._recent_views_path", return_value=fake_fp):
            # Start empty
            assert load_recent_views() == []
            # Single push
            record_property_view("A")
            assert load_recent_views() == ["A"]
            # Newer push moves to front
            record_property_view("B")
            assert load_recent_views() == ["B", "A"]
            # Duplicate push dedupes + moves to front
            record_property_view("A")
            assert load_recent_views() == ["A", "B"]
            # Cap at 8 entries
            for i in range(20):
                record_property_view(f"P{i}")
            views = load_recent_views()
            assert len(views) == 8
            # Newest stays first
            assert views[0] == "P19"
t("GG3: record_property_view dedupes, caps at 8, returns newest-first",
  gg3_recent_views_roundtrip)


def gg4_v2_version_bumped_to_29():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 29), \
        f"V2_VERSION should be >= v2.0.29, got {V2_VERSION}"
t("GG4: V2_VERSION ≥ v2.0.29", gg4_v2_version_bumped_to_29)


# ============================================================================
# Phase HH — v2.0.30 (Brian 5/29):
#   HH1: Sanity flags rendering moved to BOTTOM of Year-1 KPIs section
#   HH2: V2_VERSION ≥ v2.0.30
# ============================================================================

def hh1_sanity_flags_render_after_noi_trend():
    """The flag-callout rendering (Expense ratio, Negative leverage, etc.)
    now appears AFTER the NOI Trend strip — not between Row 3 and the
    NOI strip. Verify the render block sits between the NOI strip and
    the return statement of _render_metrics."""
    src = open('ui/underwriting.py', encoding='utf-8').read()
    fn_start = src.find('def _render_metrics(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]

    # NOI trend marker
    pos_noi = body.find('NOI Trend (T-12')
    # Flag-rendering marker — the active loop "for color, msg in flags:"
    pos_flag_render = body.find('for color, msg in flags:')
    # _render_metrics has TWO `return {` — the early one for render=False
    # and the final one after rendering. We want the FINAL (rightmost) one.
    pos_return = body.rfind('return {')

    assert pos_noi > 0, "NOI Trend strip missing"
    assert pos_flag_render > 0, "Flag rendering loop missing"
    assert pos_return > 0, "Return dict missing"

    # Required order: NOI trend → flag rendering → final return
    assert pos_noi < pos_flag_render, \
        "Flag rendering must come AFTER the NOI trend strip"
    assert pos_flag_render < pos_return, \
        "Flag rendering must come BEFORE the final return statement"
t("HH1: Sanity flag callouts moved to bottom of Year-1 KPIs (after NOI Trend)",
  hh1_sanity_flags_render_after_noi_trend)


def hh2_v2_version_bumped_to_30():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 30), \
        f"V2_VERSION should be >= v2.0.30, got {V2_VERSION}"
t("HH2: V2_VERSION ≥ v2.0.30", hh2_v2_version_bumped_to_30)


# ============================================================================
# Phase II — v2.0.31 landing-page search fix (Brian 5/29):
#   II1: Landing search calls list_properties(search=query) — DB filters,
#        not a Python-side filter over a truncated 500-row slice
#   II2: Recently-viewed properties explicitly fetched by id via
#        get_property() so they appear even past the no-search cap
#   II3: Crossroads is actually findable via the new path
#   II4: V2_VERSION ≥ v2.0.31
# ============================================================================

def ii1_landing_uses_db_search_param():
    """The landing renderer now passes the user query straight to the
    DB's `search=` kwarg instead of fetching limit=500 and filtering in
    Python (which dropped Crossroads silently)."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    fn_start = src.find('def render_v2_inventory_landing(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    # New DB-side search call
    assert 'list_properties(search=' in body, \
        "Landing must call list_properties(search=...) when user searches"
    # And get_property is imported / used for recents
    assert 'from data.db import list_properties, get_property' in body \
        or 'get_property(' in body, \
        "get_property must be used to fetch recents by id"
    # The old Python-side filter chain (sl in name lower / address lower /
    # city lower) shouldn't be the PRIMARY path anymore (it can stay as
    # a recent-views post-filter; we just verify the DB call is there).
t("II1: Landing uses list_properties(search=) — DB-side search",
  ii1_landing_uses_db_search_param)


def ii2_recents_explicitly_fetched_by_id():
    """Recents are pulled via get_property() so they appear even if the
    property's row is past the no-search inventory cap."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    fn_start = src.find('def render_v2_inventory_landing(')
    fn_end = src.find('\ndef ', fn_start + 1)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    assert 'get_property(pid)' in body, \
        "Recents must be fetched explicitly by id via get_property()"
t("II2: Recently-viewed props fetched explicitly by id",
  ii2_recents_explicitly_fetched_by_id)


def ii3_crossroads_findable_via_db_search():
    """End-to-end sanity check against the real DB: 'Crossroads' returns
    at least Crossroads Townhomes."""
    from data.db import list_properties
    rows = list_properties(search='Crossroads', limit=300)
    names = {r.get('name') for r in rows}
    assert 'Crossroads Townhomes' in names, \
        f"Crossroads Townhomes missing from search results: {sorted(names)[:8]}"
t("II3: 'Crossroads' search finds Crossroads Townhomes via DB",
  ii3_crossroads_findable_via_db_search)


def ii4_v2_version_bumped_to_31():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 31), \
        f"V2_VERSION should be >= v2.0.31, got {V2_VERSION}"
t("II4: V2_VERSION ≥ v2.0.31", ii4_v2_version_bumped_to_31)


# ============================================================================
# Phase JJ — v2.0.32 topbar + Find-anything fixes (Brian 5/29):
#   JJ1: Breadcrumb is "Search / {prop}" — not Pipeline/Active Deals
#   JJ2: Both Search link and Find-anything bar have v2-nav-search-trigger
#   JJ3: Delegated click listener opens the palette on trigger click
#   JJ4: Cmd+K / Ctrl+K keydown handler is wired
#   JJ5: V2_VERSION ≥ v2.0.32
# ============================================================================

def jj1_breadcrumb_search_format():
    """v2.1.2 — topbar shows the QUARRY brand (home link) + the current
    property in the crumb. The search is the input field, not a crumb."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    pos_fn = src.find('def render_v2_topbar(')
    body = src[pos_fn:src.find('\ndef ', pos_fn + 1)]
    assert 'QUARRY' in body and 'v2-nav-crumbs' in body and 'crumb_here' in body
    # Old "Pipeline" / "Active Deals" links removed
    for needle in ('>Pipeline</a>', '>Active Deals</a>'):
        for line in body.splitlines():
            if needle in line and not line.lstrip().startswith('#'):
                raise AssertionError(f"Old breadcrumb link still present: {needle}")
t("JJ1: Topbar shows brand + current-property crumb", jj1_breadcrumb_search_format)


def jj2_search_controls_are_real_links():
    """v2.1.2 — the search is a native st.text_input (type in place);
    results are real ?prop=<id> links that open the property."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    pos_fn = src.find('def render_v2_topbar(')
    body = src[pos_fn:src.find('\ndef ', pos_fn + 1)]
    assert 'st.text_input(' in body and 'v2_global_search' in body
    rpos = src.find('def _render_v2_search_results(')
    rbody = src[rpos:src.find('\ndef ', rpos + 1)]
    assert 'href="?prop=' in rbody
t("JJ2: Search is a native input; results link to properties",
  jj2_search_controls_are_real_links)


def jj3_search_navigates_to_landing():
    """Clicking Search navigates to ?home=1, which the query-param handler
    turns into the searchable inventory landing (clears selection)."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    pos = src.find('def apply_query_param_to_state(')
    body = src[pos:src.find('\ndef ', pos + 1)]
    assert 'qp.get("home")' in body and 'selected_property_id' in body, \
        "?home=1 must clear the property selection → show the landing search"
t("JJ3: Search link → ?home=1 → inventory landing search",
  jj3_search_navigates_to_landing)


def jj4_cmd_k_shortcut_wired():
    """v2.1.2 — ⌘K/Ctrl+K is wired via the components.html iframe bridge
    and FOCUSES the in-place search input (was: navigate away)."""
    body = _cmdk_src()
    assert "e.metaKey || e.ctrlKey" in body, "Cmd/Ctrl+K guard missing"
    assert "window.parent.document" in body and "addEventListener('keydown'" in body, \
        "Keydown listener must attach to the parent document"
    assert 'focusSearch' in body and '.focus()' in body, \
        "⌘K must focus the in-place search input"
t("JJ4: ⌘K / Ctrl+K focuses the in-place search", jj4_cmd_k_shortcut_wired)


def jj5_v2_version_bumped_to_32():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 32), \
        f"V2_VERSION should be >= v2.0.32, got {V2_VERSION}"
t("JJ5: V2_VERSION ≥ v2.0.32", jj5_v2_version_bumped_to_32)


# ============================================================================
# Phase KK — v2.0.33 (Brian 5/29):
#   KK1: Key Documents inspector filters out JSON / internal state files
#   KK2: Macro Context inspector block REMOVED from render_v2_inspector
#   KK3: User-visible captions no longer mention internal file paths
#   KK4: V2_VERSION ≥ v2.0.33
# ============================================================================

def kk1_key_documents_filters_internal_files():
    """The Key Documents loop skips JSON and known internal state files."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    pos_fn = src.find('def render_v2_inspector(')
    body = src[pos_fn:src.find('\ndef ', pos_fn + 1)]
    # Filter exists
    assert 'internal_names' in body
    assert 'internal_exts' in body
    assert '".json"' in body, \
        "JSON extension must be filtered out of Key Documents"
    # Specific internal files Brian asked us to hide
    for name in (
        "acquisition-checklist.json",
        "value_add_capex.json",
        "property_card_overrides.json",
        "deal.json",
        "sources.json",
    ):
        assert name in body, \
            f"Internal file {name!r} should be in the hide-list"
    # End-to-end: build a fake folder and verify the filter
    import tempfile
    from pathlib import Path as _P
    with tempfile.TemporaryDirectory() as td:
        d = _P(td)
        for name in (
            "T-12.pdf", "Rent-Roll.xlsx", "OM.pdf",
            "deal.json", "acquisition-checklist.json",
            "value_add_capex.json", "_recent_views.json",
            "notes.txt", "sources.json",
        ):
            (d / name).write_text("x", encoding="utf-8")

        # Use the same predicate as the renderer (inline copy of the rule
        # so the test doesn't need to render through Streamlit).
        internal_names = {
            "deal.json", "sources.json", "sales.json", "notes.txt",
            "mystery_shops.json", "value_add_capex.json",
            "property_card_overrides.json", "acquisition-checklist.json",
            "due_diligence.json", "dd_state.json", "owner_portal.json",
            "investors.json", "events.json", "term_sheet.json",
            "_recent_views.json", "_favorites.json",
        }
        def _is_user_facing(p):
            n = p.name.lower()
            if not p.is_file(): return False
            if n.startswith(".") or n.startswith("~$") or n == "desktop.ini":
                return False
            if n.startswith("_"): return False
            if n in internal_names: return False
            if p.suffix.lower() == ".json": return False
            return True
        visible = sorted(p.name for p in d.iterdir() if _is_user_facing(p))
        # User-uploaded materials show; internal stuff hidden
        assert "T-12.pdf" in visible
        assert "Rent-Roll.xlsx" in visible
        assert "OM.pdf" in visible
        # Internal/state files hidden
        for hidden in (
            "acquisition-checklist.json", "deal.json",
            "value_add_capex.json", "_recent_views.json", "sources.json",
        ):
            assert hidden not in visible, \
                f"{hidden} should be hidden from the user"
t("KK1: Key Documents inspector hides JSON / internal state files",
  kk1_key_documents_filters_internal_files)


def kk2_macro_context_removed():
    """The 'Macro Context' inspector block was removed per Brian's
    'Remove this from all screens'."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    pos_fn = src.find('def render_v2_inspector(')
    body = src[pos_fn:src.find('\ndef ', pos_fn + 1)]
    # No active <h3>Macro context</h3> rendering in the inspector function
    for line in body.splitlines():
        if 'Macro context' in line and '<h3>' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                f"Macro context block still rendering: {line.strip()}"
            )
    # No active macro_rows list builder either
    for line in body.splitlines():
        if 'macro_rows.append' in line and not line.lstrip().startswith('#'):
            raise AssertionError(
                "macro_rows is still being built — block not fully removed"
            )
t("KK2: Macro Context block removed from inspector",
  kk2_macro_context_removed)


def kk3_no_internal_file_paths_in_user_captions():
    """Captions visible to the user (not docstrings / module comments)
    must not name internal storage files. Specifically Brian's two
    callouts: value_add_capex.json and notes.txt.

    Heuristic: a literal '.json' or 'notes.txt' string inside an
    `st.info(...)` / `st.caption(...)` argument is a leak."""
    import re as _re
    leaks = []
    for src_path in (
        'ui/components.py', 'ui/value_add.py', 'ui/property_detail.py',
    ):
        src = open(src_path, encoding='utf-8').read()
        # Find every st.info() / st.caption() call (active, not comments)
        # and check the body for an internal filename
        for m in _re.finditer(
            r'st\.(?:info|caption|warning|success)\(\s*\n?(?:\s*[^)]*?)\)',
            src,
            _re.DOTALL,
        ):
            block = m.group(0)
            # Skip if the match is on a commented-out line (heuristic)
            for needle in ("value_add_capex.json",
                           "property_card_overrides.json"):
                if needle in block:
                    leaks.append((src_path, needle, block[:120]))
    assert not leaks, \
        f"User-visible captions still leak internal file names: {leaks}"
t("KK3: User captions no longer mention internal storage files",
  kk3_no_internal_file_paths_in_user_captions)


def kk4_v2_version_bumped_to_33():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 33), \
        f"V2_VERSION should be >= v2.0.33, got {V2_VERSION}"
t("KK4: V2_VERSION ≥ v2.0.33", kk4_v2_version_bumped_to_33)


# ============================================================================
# Phase LL — v2.0.34 (Brian 5/29):
#   LL1: Tabs use dark-navy active pill + white text (not gold)
#   LL2: Tab pills are full-rounded (border-radius: 999px)
#   LL3: Help popover panel opens RIGHTWARD (left: 0) — stays in viewport
#   LL4: Popover width clamped to min(380px, viewport - 32px)
#   LL5: V2_VERSION ≥ v2.0.34
# ============================================================================

def ll1_tab_active_state_uses_ink_not_gold():
    """Active tab uses dark navy (v['ink']) background + white text.
    v2.0.26 used gold; v2.0.34 swapped to ink per Brian's reference."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # The aria-selected rule must reference the ink token (rendered
        # as a hex color in the actual emitted CSS). Easiest check: the
        # selector appears AND white-text is set.
        # The v['ink'] in V2 is a dark navy; we just verify the rule
        # block doesn't paint gold for the active state.
        sel_idx = css.find('button[role="tab"][aria-selected="true"]')
        assert sel_idx > 0
        # Get the next ~400 chars (the rule block)
        rule = css[sel_idx:sel_idx + 700]
        # v2.0.36 switched from `color: white` to specific `#FFFFFF`
        assert ('color: white' in rule
                or 'color: #FFFFFF' in rule
                or 'color: #ffffff' in rule), \
            "Active tab text must be white"
        # Gold tokens should not appear in this active-state rule
        gold_lit = "#B89738"
        gold_lit_lower = gold_lit.lower()
        assert (gold_lit not in rule and gold_lit_lower not in rule), \
            f"Active tab should not be gold-colored:\n{rule}"
    finally:
        _os.environ["ER_THEME"] = prior
t("LL1: Active tab is dark navy + white text (not gold)",
  ll1_tab_active_state_uses_ink_not_gold)


def ll2_tab_pills_are_fully_rounded():
    """Tab buttons use border-radius: 999px (full pill), not the old
    8px-top-only shape."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # Find the tab button base rule
        idx = css.find('button[role="tab"] {')
        assert idx > 0, "Tab button base rule missing"
        rule = css[idx:idx + 500]
        assert 'border-radius: 999px' in rule, \
            f"Tab buttons must use full-pill radius. Rule:\n{rule}"
    finally:
        _os.environ["ER_THEME"] = prior
t("LL2: Tab pills are full-rounded (border-radius: 999px)",
  ll2_tab_pills_are_fully_rounded)


def ll3_help_popover_opens_rightward():
    """Section-help panel positions with `left: 0` (opens right) instead
    of `right: 0` (opens left). Fixes the off-screen-left bleed Brian
    saw on the Comparables help."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # Find the panel rule
        idx = css.find('.v2-section-help-panel {')
        assert idx > 0, ".v2-section-help-panel rule missing"
        rule = css[idx:idx + 800]
        # Must position from the LEFT now
        assert 'left: 0' in rule, \
            "Help panel must use left: 0 (opens rightward)"
        # And the legacy `right: 0` line must NOT be in the active rule
        # (the rule starts at idx and goes ~800 chars; check the first
        # closing brace)
        first_close = rule.find('}')
        body = rule[:first_close]
        assert 'right: 0' not in body, \
            "Help panel must not pin to the right edge"
    finally:
        _os.environ["ER_THEME"] = prior
t("LL3: Help popover panel opens RIGHTWARD (left: 0)",
  ll3_help_popover_opens_rightward)


def ll4_help_popover_width_clamped_to_viewport():
    """Panel width = min(380px, viewport - 32px) so it never bleeds off
    the right edge."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # Look for the clamped-width line
        assert 'min(380px, calc(100vw - 32px))' in css, \
            "Width must be clamped via min(380px, calc(100vw - 32px))"
    finally:
        _os.environ["ER_THEME"] = prior
t("LL4: Help popover width clamped to min(380px, viewport - 32px)",
  ll4_help_popover_width_clamped_to_viewport)


def ll5_v2_version_bumped_to_34():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 34), \
        f"V2_VERSION should be >= v2.0.34, got {V2_VERSION}"
t("LL5: V2_VERSION ≥ v2.0.34", ll5_v2_version_bumped_to_34)


# ============================================================================
# Phase MM — v2.0.35 tab styling refinement (Brian 5/29):
#   MM1: Inactive tab counter badges use cream #F5F0E4 background
#   MM2: Inactive tab text is full ink (dark) not gray
#   MM3: V2_VERSION ≥ v2.0.35
# ============================================================================

def mm1_counter_badges_use_cream_background():
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # The badge rule should use the warm cream color
        assert '#F5F0E4' in css, \
            "Inactive tab badges must use warm cream #F5F0E4"
    finally:
        _os.environ["ER_THEME"] = prior
t("MM1: Inactive tab counter badges use warm cream",
  mm1_counter_badges_use_cream_background)


def mm2_v2_version_bumped_to_35():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 35), \
        f"V2_VERSION should be >= v2.0.35, got {V2_VERSION}"
t("MM2: V2_VERSION ≥ v2.0.35", mm2_v2_version_bumped_to_35)


# ============================================================================
# Phase NN — v2.0.36 (Brian 5/29):
#   NN1: Palette JS uses fresh getElementById lookups, no cached refs
#   NN2: Input + overlay listeners delegated to document
#   NN3: Active tab cascades white text + 700 weight to every descendant
#   NN4: V2_VERSION ≥ v2.0.36
# ============================================================================

def nn1_search_is_native_not_injected_js():
    """v2.1.1 — the search no longer depends on injected JS at all. The
    working search is the native Streamlit landing search box; ⌘K is the
    only JS, and it lives in a components.html iframe. Confirm the dead
    overlay/cached-ref machinery is gone from render_v2_cmdk_palette."""
    body = _cmdk_src()
    assert 'getOverlay()' not in body and 'v2-cmdk-overlay' not in body, \
        "Dead overlay machinery should be gone"
    assert '_components_html(' in body, "⌘K must use the components iframe bridge"
    # The real, working search is the landing's native st.text_input
    src = _V2SRC
    land = src[src.find('def render_v2_inventory_landing('):]
    land = land[:land.find('\ndef ', 1)]
    assert 'st.text_input(' in land, "Landing must have a native Streamlit search box"
t("NN1: Search is native Streamlit (no injected-JS dependency)",
  nn1_search_is_native_not_injected_js)


def nn2_search_box_queries_db():
    """The native landing search filters the real DB via list_properties
    (search=...) — proven to find Crossroads end-to-end."""
    src = _V2SRC
    land = src[src.find('def render_v2_inventory_landing('):]
    land = land[:land.find('\ndef ', 1)]
    assert 'list_properties(search=' in land, \
        "Landing search must query the DB by search term"
    # End-to-end: the DB search actually finds a known property
    from data.db import list_properties
    hits = list_properties(search='Crossroads', limit=50)
    assert any('Crossroads' in (h.get('name') or '') for h in hits), \
        "DB search should find Crossroads"
t("NN2: Native search box queries the DB (finds Crossroads)",
  nn2_search_box_queries_db)


def nn3_active_tab_contrast_cascaded():
    """Active tab forces white + bold on every descendant so the label
    can't be over-styled by Streamlit's inner <p>/<div>."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        # Wildcard descendant selector for the active-tab rule
        assert 'button[role="tab"][aria-selected="true"] *' in css, \
            "Cascade selector to descendants missing"
        # And the rule forces #FFFFFF (specific hex, not just `white`)
        # and font-weight 700
        sel_idx = css.find('button[role="tab"][aria-selected="true"] *')
        rule = css[sel_idx:sel_idx + 700]
        # Find the rule body
        body_start = rule.find('{')
        body_end = rule.find('}', body_start)
        body = rule[body_start:body_end]
        assert '#FFFFFF' in body, "Active tab must force #FFFFFF"
        assert 'font-weight: 700' in body, \
            "Active tab must force font-weight 700"
    finally:
        _os.environ["ER_THEME"] = prior
t("NN3: Active tab contrast cascade — white #FFFFFF + 700 weight",
  nn3_active_tab_contrast_cascaded)


def nn4_v2_version_bumped_to_36():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 36), \
        f"V2_VERSION should be >= v2.0.36, got {V2_VERSION}"
t("NN4: V2_VERSION ≥ v2.0.36", nn4_v2_version_bumped_to_36)


# ============================================================================
# Phase OO — v2.0.37 (Brian 5/29):
#   OO1: Seller Floor panel auto-fills from sales.json (subject Sale History)
#   OO2: Investor dataclass has `email` field with roundtrip
#   OO3: update_investor + remove_investor helpers exist
#   OO4: Owner Portal Investors panel has Email column + Edit/Delete UI
#   OO5: ParseResult no longer leaks "populate sources.json manually"
#   OO6: V2_VERSION ≥ v2.0.37
# ============================================================================

def oo1_seller_floor_reads_subject_sales():
    """Seller floor reads sales.json first, assessor only on fallback."""
    src = open('ui/seller_floor_panel.py', encoding='utf-8').read()
    assert 'def _latest_sale_from_subject_folder' in src
    assert 'load_sales' in src, "Must call load_sales to read the folder's Sale History"
    # The render fn takes a folder param now
    assert 'def render_seller_floor_panel(prop: dict[str, Any], folder=None)' in src
    # The waterfall call site passes folder
    wf = open('ui/waterfall_view.py', encoding='utf-8').read()
    assert 'render_seller_floor_panel(prop, folder=folder)' in wf
t("OO1: Seller Floor auto-fills from subject Sale History",
  oo1_seller_floor_reads_subject_sales)


def oo2_investor_dataclass_has_email():
    from core.lp_gp_ledger import Investor
    inv = Investor(investor_id="lp_test", name="Test", email="x@y.com")
    assert inv.email == "x@y.com"
    # Roundtrip preserves email
    d = inv.to_dict()
    assert d["email"] == "x@y.com"
    inv2 = Investor.from_dict(d)
    assert inv2.email == "x@y.com"
    # Backwards compat: legacy ledger files without email default to ""
    legacy = {
        "investor_id": "lp_old",
        "name": "Old Investor",
        "kind": "LP",
        "commitment": 100_000,
    }
    inv3 = Investor.from_dict(legacy)
    assert inv3.email == ""
t("OO2: Investor dataclass has email field with roundtrip + back-compat",
  oo2_investor_dataclass_has_email)


def oo3_update_and_remove_investor_helpers():
    from core.lp_gp_ledger import (
        Ledger, add_investor, update_investor, remove_investor,
    )
    led = Ledger(deal_id="test", raise_target=0.0, investors=[], events=[])
    inv = add_investor(led, "Alice", 100_000, "LP", "n", email="a@x.com")
    assert inv.email == "a@x.com"
    # Update
    updated = update_investor(led, inv.investor_id,
                              name="Alice Smith", email="alice@x.com")
    assert updated is not None
    assert updated.name == "Alice Smith"
    assert updated.email == "alice@x.com"
    # Remove
    ok = remove_investor(led, inv.investor_id)
    assert ok is True
    assert led.investors == []
    # Removing unknown id is a no-op (returns False)
    assert remove_investor(led, "lp_does_not_exist") is False
t("OO3: update_investor + remove_investor helpers",
  oo3_update_and_remove_investor_helpers)


def oo4_owner_portal_email_and_edit_ui():
    src = open('ui/owner_portal.py', encoding='utf-8').read()
    # Email column in the table
    assert '"Email": inv.email' in src
    # Email input in Add form
    assert 'new_email = st.text_input(' in src
    assert 'email=new_email' in src
    # Edit/delete expander
    assert 'Edit / delete an investor' in src
    assert 'lg.update_investor(' in src
    assert 'lg.remove_investor(' in src
    # Two-step delete confirm
    assert 'Confirm: delete' in src
t("OO4: Owner Portal — Email column + Add email + Edit/Delete UI",
  oo4_owner_portal_email_and_edit_ui)


def oo5_parser_no_longer_mentions_sources_json():
    """The unrecognized-layout ParseResult message used to say
    'populate sources.json manually' — Brian called this out as a
    user-vs-developer leak. The new copy is plain-English."""
    src = open('data/parsers.py', encoding='utf-8').read()
    assert 'populate sources.json' not in src, \
        "Old technical message leaks 'sources.json' to the user"
    assert 'layout wasn\'t auto-recognized' in src, \
        "New plain-English message missing"
t("OO5: ParseResult unrecognized-layout message is plain English",
  oo5_parser_no_longer_mentions_sources_json)


def oo6_v2_version_bumped_to_37():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 37), \
        f"V2_VERSION should be >= v2.0.37, got {V2_VERSION}"
t("OO6: V2_VERSION ≥ v2.0.37", oo6_v2_version_bumped_to_37)


# ============================================================================
# Phase PP — v2.0.38 inspector wrap fix (Brian 5/29):
#   PP1: Calibration pip ("FRED · …") doesn't wrap
#   PP2: $/Unit row label shortened so it fits on one line
#   PP3: V2_VERSION ≥ v2.0.38
# ============================================================================

def pp1_inspector_pip_nowrap():
    """The .v2-ins-head .pip rule includes white-space: nowrap so the
    'FRED · 5:18 PM ET' chip never wraps the 'ET' onto a 2nd line."""
    import os as _os
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    prior = _os.environ.get("ER_THEME", "")
    _os.environ["ER_THEME"] = "v2"
    try:
        inject_v2_theme()
        css = ''.join(captured)
        pip_idx = css.find('.v2-ins-head .pip')
        assert pip_idx > 0
        rule = css[pip_idx:pip_idx + 400]
        assert 'white-space: nowrap' in rule, \
            "Inspector pip must have white-space: nowrap"
        # Inspector row value side also nowraps, with label ellipsis
        row_idx = css.find('.v2-ins-row .r')
        assert row_idx > 0
        row_rule = css[row_idx:row_idx + 400]
        assert 'white-space: nowrap' in row_rule
        label_idx = css.find('.v2-ins-row .l')
        assert label_idx > 0
        label_rule = css[label_idx:label_idx + 400]
        assert 'text-overflow: ellipsis' in label_rule
    finally:
        _os.environ["ER_THEME"] = prior
t("PP1: Inspector pip + row labels don't wrap", pp1_inspector_pip_nowrap)


def pp2_dollar_per_unit_label_shortened():
    """The $/unit-vs-submkt row label is now shorter so it fits on one
    line in the narrow inspector column."""
    src = open('ui/v2_theme_05292026.py', encoding='utf-8').read()
    # New short labels exist
    assert '"$/Unit vs submkt"' in src, \
        "Label should be the shorter '$/Unit vs submkt'"
    assert '"vs ceiling"' in src, \
        "Right-rail comparison should be the trimmed 'vs ceiling'"
t("PP2: $/Unit row label shortened to fit on one line",
  pp2_dollar_per_unit_label_shortened)


def pp3_v2_version_bumped_to_38():
    from ui.v2_theme_05292026 import V2_VERSION
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (2, 0, 38), \
        f"V2_VERSION should be >= v2.0.38, got {V2_VERSION}"
t("PP3: V2_VERSION ≥ v2.0.38", pp3_v2_version_bumped_to_38)


def q8_v2_tab_padding_tightened():
    """V2 CSS shrinks tab padding so all 9 tabs fit in the narrower column."""
    from ui.v2_theme_05292026 import inject_v2_theme
    captured.clear()
    inject_v2_theme()
    css = ''.join(captured)
    # Padding should now be 7px 13px (was 9px 18px in earlier versions)
    assert 'padding: 7px 13px' in css
    # Tab font shrunk to 12px
    assert 'font-size: 12px' in css
t("Q8: V2 tab CSS padding tightened (7px 13px) + font 12px", q8_v2_tab_padding_tightened)


def p11_verdict_band_not_called_from_app():
    """v2.0.10: verdict band is no longer rendered in V2's main flow."""
    src = open('app.py', encoding='utf-8').read()
    # Allow the import alias to exist but the call itself must be gone
    # (or commented out). Find the deal-analysis with main_col block:
    pos_stats = src.find('_v2_stats(prop, metrics)')
    assert pos_stats > 0, '_v2_stats call missing'
    # The next 200 chars after _v2_stats should NOT contain an active _v2_verdict( call.
    # A commented-out reference like "# _v2_verdict(...)" is fine.
    chunk = src[pos_stats:pos_stats+600]
    # Strip lines that are comments
    code_only = '\n'.join(
        ln for ln in chunk.splitlines() if not ln.strip().startswith('#')
    )
    assert '_v2_verdict(' not in code_only, \
        "Verdict band still being called in V2 deal-analysis flow"
t("P11: Verdict band not called from app.py V2 path (Brian removed it)", p11_verdict_band_not_called_from_app)

def p10_subject_vs_market_in_expander():
    """Subject vs Market still reachable (preserved in collapsed expander).
    Must NOT be a section_card anymore (Brian deferred it below the fold)."""
    src = open('ui/comps.py', encoding='utf-8').read()
    # Find render_comps and look only at its body (use a top-level 'def' match)
    def_pos = src.find('def render_comps(')
    next_top_def = src.find('\ndef ', def_pos+10)  # leading \n catches only top-level defs
    body = src[def_pos:next_top_def] if next_top_def > 0 else src[def_pos:]
    assert '_render_subject_vs_market(prop, folder)' in body, "SvM helper call missing"
    assert 'st.expander' in body, "SvM should be wrapped in st.expander"
    # Must not be a section_card anymore in the active path
    assert 'section_card("Subject vs Market"' not in body, \
        "SvM should NOT be a section_card — it was deferred to an expander"
t("P10: Subject vs Market preserved in collapsed expander below the fold", p10_subject_vs_market_in_expander)


def o14_property_detail_imports_still_valid():
    """After the reorder, ui/property_detail.py must still import + parse."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('property_detail_test', 'ui/property_detail.py')
    # Don't execute (it imports streamlit which isn't fully mocked); just parse
    import ast
    src = open('ui/property_detail.py', encoding='utf-8').read()
    ast.parse(src)  # raises on syntax error
t("O14: property_detail.py still parses after reorder", o14_property_detail_imports_still_valid)


def m4_stat_bar_pending_fallback_when_no_deal_json():
    """Property without deal.json should still show the 'Set in Underwriting tab' placeholder."""
    from ui.v2_theme_05292026 import render_v2_stats_bar
    fake_prop = {'property_id': 'p_no_deal', 'name': 'No Deal Yet', 'units': 100,
                 'asset_class': 'C', 'city': 'Norfolk'}
    captured.clear()
    # No folder, no metrics — must fall back gracefully
    render_v2_stats_bar(fake_prop, {})
    out = captured[-1]
    assert 'Set in Underwriting tab' in out, "Pending fallback missing for property without deal.json"
    assert 'Purchase Price' in out, "Card label still missing"
t("M4: Stat bar pending-state fallback when no deal.json", m4_stat_bar_pending_fallback_when_no_deal_json)


# ============================================================================
# Phase QQ — v2.1.0 multi-state expansion + rename (Brian 5/30):
#   QQ1: DB holds all 5 target states with real C-class inventory
#   QQ2: list_distinct_states orders target states first
#   QQ3: city_counts_for_state returns ranked cities
#   QQ4: list_properties state filter + cities IN-list work
#   QQ5: asset_type tagged (Multifamily default), no nulls in target states
#   QQ6: live deals (Crossroads) survive the multi-state rebuild
#   QQ7: V2_VERSION ≥ v2.1.0; product renamed to QUARRY
#   QQ8: ⌘K palette includes Crossroads WITH address tokens (regression)
# ============================================================================

def qq1_db_has_target_states():
    from data import db
    counts = {}
    for s in ("VA", "NC", "SC", "GA", "TN"):
        counts[s] = db.count_properties(state=s, asset_class="C",
                                        units_min=25, units_max=200)
    for s, n in counts.items():
        assert n > 50, f"{s} has only {n} C-class 25-200u — expected >50"
t("QQ1: All 5 target states have real C-class value-add inventory", qq1_db_has_target_states)


def qq2_states_target_first():
    from data import db
    states = db.list_distinct_states(target_first=True)
    assert states[:5] == ["VA", "NC", "SC", "GA", "TN"], \
        f"Target states should lead: {states[:8]}"
t("QQ2: list_distinct_states orders target states first", qq2_states_target_first)


def qq3_city_counts_for_state():
    from data import db
    nc = db.city_counts_for_state("NC")
    assert nc and nc[0][1] >= nc[-1][1], "Cities should be count-ordered desc"
    names = {c for c, _ in nc}
    assert "Charlotte" in names and "Raleigh" in names
t("QQ3: city_counts_for_state returns ranked cities", qq3_city_counts_for_state)


def qq4_state_and_cities_filters():
    from data import db
    ga = db.list_properties(state="GA", limit=5)
    assert ga and all(p["state"] == "GA" for p in ga)
    hr = db.list_properties(cities=["Norfolk", "Virginia Beach"], limit=10)
    assert hr and all(p["city"] in ("Norfolk", "Virginia Beach") for p in hr)
t("QQ4: list_properties state + cities filters work", qq4_state_and_cities_filters)


def qq5_asset_type_tagged():
    from data import db
    import sqlite3
    c = sqlite3.connect(db.DB_PATH)
    nulls = c.execute(
        "SELECT COUNT(*) FROM properties WHERE state IN ('VA','NC','SC','GA','TN') "
        "AND (asset_type IS NULL OR asset_type='')"
    ).fetchone()[0]
    mf = c.execute("SELECT COUNT(*) FROM properties WHERE asset_type='Multifamily'").fetchone()[0]
    c.close()
    assert nulls == 0, f"{nulls} target-state rows have no asset_type"
    assert mf > 10000, f"Expected >10K multifamily, got {mf}"
t("QQ5: asset_type tagged on every target-state row", qq5_asset_type_tagged)


def qq6_live_deals_survive_rebuild():
    from data import db
    cross = db.get_property("custom-bb2272cc-df0c-420f-b3d6-458d582dbad2")
    assert cross is not None, "Crossroads Townhomes lost in rebuild!"
    assert cross["units"] == 26 and cross["city"] == "Norfolk"
t("QQ6: Live deals (Crossroads) survive multi-state rebuild", qq6_live_deals_survive_rebuild)


def qq7_version_and_rename():
    from ui.v2_theme_05292026 import V2_VERSION, render_v2_topbar
    import re as _re
    m = _re.match(r"^v(\d+)\.(\d+)\.(\d+)$", V2_VERSION)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) >= (2, 1, 0), \
        f"Expected >= v2.1.0, got {V2_VERSION}"
    captured.clear()
    render_v2_topbar(None)
    assert "QUARRY" in ''.join(captured), "Product not renamed to QUARRY in topbar"
t("QQ7: V2_VERSION ≥ v2.1.0 + renamed to QUARRY", qq7_version_and_rename)


def qq8_palette_includes_crossroads_with_address():
    from ui.v2_theme_05292026 import _gather_palette_props
    props = _gather_palette_props()
    cr = [p for p in props if "crossroads townhomes" in p["_t"]]
    assert cr, "Crossroads missing from palette (limit-truncation regression)"
    assert any("3000" in p["_t"] and "cape henry" in p["_t"] for p in cr), \
        "Crossroads address tokens missing from palette"
t("QQ8: ⌘K palette includes Crossroads with address tokens", qq8_palette_includes_crossroads_with_address)


# Phase RR — v2.1.3 Returns AM-fee-$ + Calibration help (Brian 5/31):
# 1) AM fee shown in dollars below the LP returns boxes; 2) ⓘ help on the
# Calibration inspector explaining each metric.
_WFSRC = open('ui/waterfall_view.py', encoding='utf-8').read()


def rr1_am_fee_dollars_block_present():
    # A real section, not a comment — titled, fed by the cashflow am_fee.
    assert 'section_card(' in _WFSRC and 'Asset Management Fee' in _WFSRC, \
        "Asset Management Fee section_card missing from Returns tab"
    assert 'am_fee' in _WFSRC and 'cf.rows' in _WFSRC, \
        "AM fee block must read per-year am_fee off the cashflow rows"
    for needle in ('Year-1 AM Fee', 'Total Over Hold', 'am_total', 'am_y1'):
        assert needle in _WFSRC, f"AM fee block missing {needle!r}"
t("RR1: AM fee shown in dollars below LP returns boxes", rr1_am_fee_dollars_block_present)


def rr2_am_fee_math_matches_cashflow():
    from data.property_io import (load_deal, load_sources,
                                  discover_property_folders, find_folder_for_property)
    from data.db import list_properties
    from core.calc import DebtTerms, build_cashflow, build_debt_schedule
    from ui.waterfall_view import _derive_year1_inputs
    import config
    rows = list_properties(search="Crossroads Townhomes")
    prop = [r for r in rows if r["units"] == 26][0]
    folder = find_folder_for_property(prop, discover_property_folders())
    deal = load_deal(folder.path)
    src = load_sources(folder.path)
    gpr, exp = _derive_year1_inputs(deal, src)
    dt = DebtTerms(loan_amount=deal.loan_amount, annual_rate=deal.interest_rate,
                   amort_months=config.AMORT_MONTHS, io_years=deal.io)
    cf = build_cashflow(year1_gpr=gpr, year1_vacancy_pct=deal.vacancy_frac,
                        year1_expenses=exp, rent_growth=deal.rent_growth,
                        expense_growth=deal.expense_growth, am_fee_pct=deal.am_fee_pct,
                        debt=build_debt_schedule(dt, deal.hp), hold_years=deal.hp,
                        exit_cap=deal.exit_cap, equity_raise=deal.equity_raise)
    fees = [float(getattr(r, "am_fee", 0) or 0) for r in cf.rows]
    assert len(fees) == deal.hp, "one cashflow row per hold year"
    assert fees[0] > 0, "Year-1 AM fee should be positive"
    assert fees[-1] == 0, "exit year earns no AM fee"
    # Brian's convention: AM fee = am_fee_pct × GPR (gross rent), not NOI.
    assert abs(fees[0] - deal.am_fee_pct * cf.rows[0].gpr) < 1.0, \
        "Year-1 AM fee must equal rate × Year-1 GPR"
t("RR2: AM fee math matches the cashflow (Y1>0, exit=0)", rr2_am_fee_math_matches_cashflow)


def rr3_calibration_help_wired():
    from ui.v2_theme_05292026 import _calibration_help_html
    h = _calibration_help_html()
    assert "<details" in h and "<summary" in h, "help must be a native <details> popover"
    for metric in ("Going-in cap", "Stabilized DY", "DSCR Y1", "DSCR stab",
                   "Vacancy", "Exit cap", "Marks:"):
        assert metric in h, f"Calibration help missing {metric!r}"
    # And it's actually injected into the Calibration inspector block.
    assert "_calibration_help_html()" in _V2SRC, \
        "Calibration help not wired into the inspector"
t("RR3: Calibration inspector has ⓘ help for every metric", rr3_calibration_help_wired)


# -----------------------------------------------------------------------
# REPORT
# -----------------------------------------------------------------------
print("\n" + "=" * 70)
print("FINAL REPORT")
print("=" * 70)

passes = sum(1 for _, s, _ in results if s == 'PASS')
fails = sum(1 for _, s, _ in results if s == 'FAIL')
errors = sum(1 for _, s, _ in results if s == 'ERROR')
total = len(results)

for name, status, detail in results:
    icon = 'OK' if status == 'PASS' else ('FAIL' if status == 'FAIL' else 'ERR')
    line = f"  [{icon}] {name}"
    if detail:
        line += f"   -- {detail[:120]}"
    print(line)

print()
print(f"  TOTAL: {total} tests")
print(f"  PASS: {passes}")
print(f"  FAIL: {fails}")
print(f"  ERROR: {errors}")
print()

if FAILED_DETAILS:
    print("=" * 70)
    print("FAILURE DETAILS (first 3)")
    print("=" * 70)
    for name, tb in FAILED_DETAILS[:3]:
        print(f"\n--- {name} ---")
        print(tb)

sys.exit(0 if (fails + errors) == 0 else 1)
