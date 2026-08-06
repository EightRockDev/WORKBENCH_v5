"""Per-user property-card overrides (owner ask 2026-08-07).

"If one user edits a property, only they should see their edits" — an
analyst's manual card values are personal working data, not shared facts.
Storage is Postgres ``user_property_overrides`` behind the same strict
per-user RLS as the Module D inbox (org_id AND user_id must both match; fails
closed with no user context), reached only via ``pg.user_connection``.

Degradation is deliberate and honest: with no Postgres (ungated dev mode,
single-user box) we fall back to the legacy shared folder file
``property_card_overrides.json`` — the pre-multi-user behavior, where there is
only one user to see it anyway. The folder file is also the read-time BASE
under Postgres so the owner's existing manual entries stay visible to
everyone until a user makes their own edit — from then on their profile wins
for them, and nobody else's view moves.

Which fields may be overridden at all is governed by ``core.field_policy``
(the data dictionary), not by this module.
"""

from __future__ import annotations

import json
from typing import Any

from core import field_policy
from data import pg


def _clean(overrides: dict[str, Any]) -> dict[str, Any]:
    """Drop empties (blank = revert to auto) and any field the data dictionary
    does not mark user-editable — a locked field must fail closed even if a
    stale UI submits it."""
    allowed = field_policy.user_editable_fields()
    return {k: v for k, v in overrides.items()
            if k in allowed and v not in (None, "", "—")}


def load_user_overrides(org_id: str | None, user_id: str | None,
                        property_key: str) -> dict[str, Any] | None:
    """This user's saved overrides for one property, or None when there is no
    per-user row (caller then falls back to the legacy folder file). Returns
    None too when Postgres is unavailable — never raises into the page."""
    if not (org_id and user_id and property_key) or not pg.is_reachable():
        return None
    try:
        with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT overrides FROM user_property_overrides
                    WHERE property_key = %s""", (property_key,))
            row = cur.fetchone()
        if row is None:
            return None
        data = row["overrides"]
        if isinstance(data, str):          # psycopg may hand back text
            data = json.loads(data)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_user_overrides(org_id: str | None, user_id: str | None,
                        property_key: str, overrides: dict[str, Any]) -> bool:
    """Upsert this user's overrides for one property. Saving an empty dict
    keeps the row (an explicit 'I cleared my edits' beats delete-and-fallback:
    the user asked for auto values, not for the shared file's values again).
    Returns True when persisted per-user, False when the caller should use the
    legacy folder path instead."""
    if not (org_id and user_id and property_key) or not pg.is_reachable():
        return False
    cleaned = _clean(overrides)
    try:
        with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO user_property_overrides
                       (org_id, user_id, property_key, overrides, updated_at)
                   VALUES (%s, %s, %s, %s::jsonb, now())
                   ON CONFLICT (org_id, user_id, property_key)
                   DO UPDATE SET overrides = EXCLUDED.overrides,
                                 updated_at = now()""",
                (org_id, user_id, property_key, json.dumps(cleaned)))
            conn.commit()
        return True
    except Exception:
        return False
