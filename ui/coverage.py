"""Coverage page (spec §15) — multifamily records by state and city.

Exactly the owner's asked-for shape:

    VIRGINIA (125,000 doors)
      Norfolk (15,000)
      Richmond (Coming soon)
      ...

All 50 rollout metros always render, in deployment order; a metro is marked
Coming soon until its backbone rows exist. Counts are 10+ door properties
from `properties_8r` — the page can never claim coverage the comp engine
doesn't have.
"""

from __future__ import annotations

import streamlit as st

import config
from core import rollout
from data.db import DB_PATH


@st.cache_data(ttl=600, show_spinner=False)
def _coverage_cached():
    """st.tabs renders EVERY tab's body on EVERY rerun, so an uncached
    GROUP BY over the backbone taxed every widget interaction in the whole
    CRM module. Ten minutes matches the nightly-cycle granularity."""
    return rollout.coverage(DB_PATH)


def render_coverage() -> None:
    c = config.COLORS
    rows = _coverage_cached()
    live = [r for r in rows if r.live]
    total_doors = sum(r.doors for r in live)
    total_records = sum(r.records for r in live)

    st.markdown(
        f'<div style="font-size:15px;color:{c["tx"]};margin-bottom:2px">'
        f'<b>Market coverage</b> — {len(live)} of {len(rows)} metros live · '
        f'{total_records:,} multifamily properties · {total_doors:,} doors '
        f'(10+ door properties)</div>'
        f'<div style="font-size:12px;color:{c["tx3"]};margin-bottom:12px">'
        f'Counts come from the Eight Rock backbone (properties_8r), updated '
        f'by the nightly cycle. Metros marked Coming soon follow the spec '
        f'§15 rollout order.</div>',
        unsafe_allow_html=True)

    for state, doors, records, metros in rollout.by_state(rows):
        n_live = sum(1 for m in metros if m.live)
        state_head = (f"{doors:,} doors" if n_live
                      else "Coming soon")
        st.markdown(
            f'<div style="font-size:14px;font-weight:700;color:{c["tx"]};'
            f'text-transform:uppercase;letter-spacing:.04em;'
            f'margin:14px 0 4px">{state} '
            f'<span style="color:{c["tx2"]};font-weight:600">'
            f'({state_head})</span></div>',
            unsafe_allow_html=True)
        for m in metros:
            if m.live:
                line = (f'{m.metro} '
                        f'<span style="color:{c["tx2"]}">({m.doors:,} doors '
                        f'· {m.records:,} properties)</span>')
            else:
                line = (f'<span style="color:{c["tx3"]}">{m.metro} '
                        f'(Coming soon)</span>')
            st.markdown(
                f'<div style="font-size:13px;color:{c["tx"]};'
                f'margin:1px 0 1px 18px">{line}</div>',
                unsafe_allow_html=True)
