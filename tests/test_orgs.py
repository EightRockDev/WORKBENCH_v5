

# ---------------------------------------------- org switcher (2026-08-09)

def test_preferred_org_wins_only_for_active_members(monkeypatch):
    """The admin-tab org switcher's choice is honored when the user is an
    active member of it, and silently ignored otherwise — a stale or forged
    session preference must never grant org access."""
    from core import orgs, session, user_admin

    user = user_admin.AdminUser(
        id="u1", idp_sub="s", email="a@b.c", display_name="A",
        platform_role="internal", status="active", last_login=None)

    monkeypatch.setattr(orgs, "user_orgs", lambda uid, active_only=True: [
        {"org_id": "org-default", "name": "Default", "role_preset": "principal",
         "status": "active"},
        {"org_id": "org-two", "name": "Two", "role_preset": "analyst",
         "status": "active"},
    ])
    monkeypatch.setattr(orgs, "get_permissions", lambda uid, oid: f"perms:{oid}")

    # No preference -> first org.
    assert session.resolve_org_context(user)[0] == "org-default"
    # Valid preference -> honored.
    org_id, perms = session.resolve_org_context(user, preferred_org_id="org-two")
    assert org_id == "org-two" and perms == "perms:org-two"
    # Preference for an org the user is NOT in -> fall back, never grant.
    assert session.resolve_org_context(
        user, preferred_org_id="org-forged")[0] == "org-default"
