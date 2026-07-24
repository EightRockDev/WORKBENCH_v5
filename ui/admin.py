"""Admin page — user administration (spec Section 9.4, FR-9.4.2).

Rendered only for an active admin (gate with core.user_admin.require_admin).
Surfaces the pilot-scale identity controls: approve pending signups, assign and
change roles, suspend/reactivate, and view each user's last login. RBAC is
enforced server-side in ``core.user_admin`` — this page is only the surface.

The full organization model, role-preset library, and permission matrix
(Section 10) build on top of this and are a later work order.
"""

from __future__ import annotations

from core import user_admin
from core.user_admin import AdminUser


def render_admin_page(st, current_user: AdminUser, org_id: str | None = None) -> None:
    # Server-side gate — not just hiding the nav item (FR-9.4.4 / SR-3.2).
    try:
        user_admin.require_admin(current_user)
    except PermissionError:
        st.error("Admins only.")
        st.stop()
        return

    tab_users, tab_org = st.tabs(["👤 Users", "🏢 Organization & roles"])
    with tab_users:
        _render_users(st, current_user)
    with tab_org:
        _render_org(st, current_user, org_id)


def _render_users(st, current_user: AdminUser) -> None:
    st.header("User administration")
    st.caption("Approve signups, assign roles, and manage access. "
               "Every change is written to the append-only audit log.")

    users = user_admin.list_users()
    pending = [u for u in users if u.is_pending]
    active = [u for u in users if not u.is_pending]

    # --- Pending approvals first (the daily task) ---------------------------
    if pending:
        st.subheader(f"Pending approval ({len(pending)})")
        for u in pending:
            c1, c2, c3 = st.columns([4, 2, 2])
            c1.write(f"**{u.display_name or u.email}**  \n{u.email}")
            c2.write(u.platform_role)
            if c3.button("Approve", key=f"approve-{u.id}", type="primary"):
                user_admin.approve_user(current_user.id, u.id)
                st.rerun()
    else:
        st.success("No pending approvals.")

    # --- Active users -------------------------------------------------------
    st.subheader(f"Users ({len(active)})")
    for u in active:
        c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
        c1.write(f"**{u.display_name or u.email}**  \n{u.email}")
        last = u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "—"
        c1.caption(f"last login: {last}")

        # Self-guard: an admin can't lock themselves out by accident.
        is_self = u.id == current_user.id
        new_role = c2.selectbox(
            "role", user_admin.PLATFORM_ROLES,
            index=user_admin.PLATFORM_ROLES.index(u.platform_role),
            key=f"role-{u.id}", disabled=is_self, label_visibility="collapsed")
        if new_role != u.platform_role:
            user_admin.set_role(current_user.id, u.id, new_role)
            st.rerun()

        if not is_self:
            if u.status == "active" and c3.button("Suspend", key=f"susp-{u.id}"):
                user_admin.suspend_user(current_user.id, u.id)
                st.rerun()
            if u.status == "suspended" and c3.button("Reactivate", key=f"react-{u.id}"):
                user_admin.set_status(current_user.id, u.id, "active")
                st.rerun()
        c4.write("🟢 active" if u.status == "active" else f"⚪ {u.status}")


def _render_org(st, current_user: AdminUser, org_id: str | None) -> None:
    """Point-and-click org & role management (Section 10.5).

    Assign each member one preset from the curated library — no per-user
    permission wiring. Every change is audit-logged; offboarding revokes access
    while the member's org-owned work stays attributable.
    """
    from core import orgs

    if not org_id:
        st.info("No organization context yet. It is created automatically on "
                "first admin login.")
        return

    presets = orgs.list_presets()
    preset_keys = [p["key"] for p in presets]
    preset_label = {p["key"]: p["label"] for p in presets}

    st.header("Organization & roles")
    st.caption("Assign each person one role preset from the library — permissions, "
               "field masks, and scope come pre-wired. Nothing is lost when people leave.")

    members = orgs.list_members(org_id)
    member_ids = {m.user_id for m in members}

    # --- Current members ---------------------------------------------------
    st.subheader(f"Members ({len(members)})")
    for m in members:
        c1, c2, c3, c4 = st.columns([3, 3, 2, 2])
        c1.write(f"**{m.display_name or m.email}**  \n{m.email}")
        is_self = m.user_id == current_user.id
        new_preset = c2.selectbox(
            "role", preset_keys, index=preset_keys.index(m.role_preset),
            format_func=lambda k: preset_label.get(k, k),
            key=f"mp-{m.user_id}", disabled=is_self, label_visibility="collapsed")
        if new_preset != m.role_preset:
            orgs.set_member_preset(current_user.id, m.user_id, org_id, new_preset)
            st.rerun()
        c3.write("🟢 active" if m.status == "active" else f"⚪ {m.status}")
        if not is_self and m.status == "active":
            if c4.button("Offboard", key=f"off-{m.user_id}"):
                orgs.offboard_member(current_user.id, m.user_id, org_id)
                st.rerun()
        elif not is_self and m.status != "active":
            if c4.button("Reactivate", key=f"re-{m.user_id}"):
                orgs.activate_member(current_user.id, m.user_id, org_id)
                st.rerun()

    # --- Add an existing platform user to the org --------------------------
    st.subheader("Add member")
    candidates = [u for u in user_admin.list_users() if u.id not in member_ids]
    if not candidates:
        st.caption("Everyone who has signed in is already a member. New people "
                   "appear here after they sign in and are approved.")
        return
    a1, a2, a3 = st.columns([4, 3, 1])
    pick = a1.selectbox(
        "person", [u.id for u in candidates],
        format_func=lambda uid: next((u.email for u in candidates if u.id == uid), uid),
        key="add-user", label_visibility="collapsed")
    preset = a2.selectbox(
        "preset", preset_keys, format_func=lambda k: preset_label.get(k, k),
        key="add-preset", label_visibility="collapsed")
    if a3.button("Add", type="primary"):
        orgs.add_member(current_user.id, pick, org_id, preset)
        orgs.activate_member(current_user.id, pick, org_id)
        st.rerun()
