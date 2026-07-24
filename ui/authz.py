"""UI-side authorization helpers — module gating + field masking (spec §10.4).

The permission model lives in ``core/permissions.py`` (tested by AC-10.2/10.3);
this module is where the deal screens APPLY it:

  * :func:`guard_module` — gate a whole tab/module. A preset without the module
    grant gets a lock notice instead of the content ("a Maintenance preset has
    no financial module grant — it cannot see the purchase price").
  * :func:`mask` / :func:`scrub` — field-level masking for shared surfaces.
  * :func:`render_preview_picker` / :func:`apply_preview` — admin-only "Preview
    as role" so an org admin can see exactly what each preset sees. The preview
    swaps the *effective* permissions only; the real membership is untouched and
    the admin panel stays reachable.

Ungated/legacy mode (no Postgres, ``perms is None``): everything passes — the
deterministic core keeps running standalone (Section 11).
"""

from __future__ import annotations

import streamlit as st

from core.permissions import MASK_PLACEHOLDER, Permissions

# Tab/module → human label for the lock notice.
_LOCK_MSG = (
    "🔒 **{label}** is not included in your role (`{preset}`).\n\n"
    "Access to modules, fields, and actions is set by your organization's "
    "role presets (admin → Organization & roles)."
)


def perms() -> Permissions | None:
    """The effective permissions for this session (None = ungated/legacy)."""
    p = st.session_state.get("perms")
    return p if isinstance(p, Permissions) else None


def guard_module(module: str, label: str) -> bool:
    """True if the current role may open ``module``; otherwise render the lock
    notice and return False. Enforcement is server-side: the content renderer is
    simply never called for an unauthorized role (§10.4, FR-9.4.4)."""
    p = perms()
    if p is None or p.can_open(module):
        return True
    st.info(_LOCK_MSG.format(label=label, preset=p.role_preset))
    return False


def mask(field: str, value, placeholder: str = MASK_PLACEHOLDER):
    """Mask a single sensitive value for the current role (no-op when ungated)."""
    p = perms()
    return value if p is None else p.mask(field, value, placeholder)


def scrub(record: dict) -> dict:
    """Strip all masked fields from a record before display/serialization."""
    p = perms()
    return record if p is None else p.scrub(record)


# ---------------------------------------------------------------------------
# Admin "Preview as role" (spec §10.3/10.4 made visible)
# ---------------------------------------------------------------------------

def render_preview_picker(user, org_id: str | None) -> None:
    """Sidebar selectbox (admins only): preview the app as any role preset."""
    if user is None or not getattr(user, "is_admin", False) or not org_id:
        return
    from core import orgs

    options = ["(my real role)"] + [p["key"] for p in orgs.list_presets()]
    labels = {p["key"]: p["label"] for p in orgs.list_presets()}
    with st.sidebar:
        pick = st.selectbox(
            "👁 Preview as role", options,
            format_func=lambda k: labels.get(k, k),
            key="_preview_role",
            help="See the workbench exactly as this role preset sees it. "
                 "Your real permissions are unchanged.")
    if pick and pick != "(my real role)":
        st.warning(f"Previewing as **{labels.get(pick, pick)}** — "
                   "modules and fields are restricted exactly as that role "
                   "would see them. Switch back to *(my real role)* to exit.",
                   icon="👁")


def apply_preview(org_id: str | None, real_perms):
    """Return the effective Permissions after any admin preview override."""
    pick = st.session_state.get("_preview_role")
    if not pick or pick == "(my real role)" or not org_id:
        return real_perms
    from core import orgs

    preset = orgs.get_preset(pick)
    if preset is None:
        return real_perms
    return Permissions.from_preset_row(org_id, preset)
