"""One honest empty-state for panels that need the Hampton Roads ETL database.

These panels used to tell the operator to `run python hampton_roads_etl.py from
hampton-roads-etl/` — a dead end, because that standalone ETL project is not
part of the v5 deployment. The notice below only offers steps that actually
work: point the app at an existing `hampton_roads.db`, or wait for the 8R data
spine (Phase 0) to populate it.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core import etl_db


def render_etl_missing_notice(feature: str) -> None:
    """Explain why `feature` is empty and how to fill it.

    `feature` completes the sentence "... so <feature> has nothing to show" —
    e.g. "the multifamily inventory and alerts". Phrased that way so it reads
    correctly whether the feature name is singular or plural.
    """
    st.info(
        f"**Hampton Roads market data isn't loaded**, so {feature} has nothing "
        "to show. Nothing is broken - this panel reads a prepared "
        "`hampton_roads.db` that this deployment doesn't ship with."
    )
    st.caption(
        "Upgraded from the previous workbench? That install almost certainly "
        "still has this file. Let the app find it and put it in place for you."
    )
    _render_find_it(key_suffix=feature)
    st.caption(
        "If it isn't on this machine at all, the public-source refresh (FRED / "
        "BLS / HUD FMR / permits) is being rebuilt into the 8R data spine and "
        "will populate this automatically when Phase 0 lands."
    )
    with st.expander("Where the app looked", expanded=False):
        for path in etl_db.candidates():
            st.caption(f"`{path}` — {'found' if path.is_file() else 'not found'}")


def _render_find_it(key_suffix: str) -> None:
    """Scan the host for an existing hampton_roads.db and offer to adopt it.

    Copying a file into the app directory is an operator action, so it is gated
    to admins - in a multi-tenant deployment an ordinary analyst must not be
    able to write to the app's data directory.
    """
    from core import etl_locate

    key = "".join(ch for ch in key_suffix if ch.isalnum())[:24]
    user = st.session_state.get("user")
    if user is not None and not getattr(user, "is_admin", False):
        st.caption("Ask an administrator to load the market dataset.")
        return

    if st.button("🔎 Find it on this machine", key=f"etl_find_{key}"):
        with st.spinner("Searching the usual places…"):
            st.session_state["_etl_hits"] = [str(p) for p in etl_locate.find_existing_db()]

    hits = st.session_state.get("_etl_hits")
    if hits is None:
        return
    if not hits:
        st.warning(
            "No `hampton_roads.db` found on this machine. Nothing to load yet.")
        return

    choice = st.selectbox(
        "Found these — pick the one to load",
        hits,
        format_func=lambda p: f"{p}  ({Path(p).stat().st_size / 1e6:,.1f} MB)",
        key=f"etl_pick_{key}",
    )
    if st.button("✅ Use this one", type="primary", key=f"etl_adopt_{key}"):
        try:
            target = etl_locate.adopt(Path(choice))
        except OSError as exc:
            st.error(f"Could not copy the file: {exc}")
            return
        st.success(f"Loaded. Copied to `{target}` — the original was left in place.")
        st.rerun()
