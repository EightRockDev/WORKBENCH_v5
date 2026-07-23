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


def render_admin_page(st, current_user: AdminUser) -> None:
    # Server-side gate — not just hiding the nav item (FR-9.4.4 / SR-3.2).
    try:
        user_admin.require_admin(current_user)
    except PermissionError:
        st.error("Admins only.")
        st.stop()
        return

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
