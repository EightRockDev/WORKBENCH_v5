"""Property activity trail (owner ask 2026-08-09: "which users have accessed
and updated which properties").

Writes one row per property VIEW (the UI throttles to once per session per
property — Streamlit reruns the script on every widget tick, so unthrottled
logging would count clicks, not visits) and one per EDIT save (detail carries
the changed field names). Org-scoped RLS; surfaced in Admin → Activity.

Degradation: no Postgres / no signed-in identity → every call is a silent
no-op. An activity trail must never break the page it observes.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from data import pg


def log(org_id: str | None, user_id: str | None, property_key: str,
        action: str, detail: dict[str, Any] | None = None) -> bool:
    if not (org_id and user_id and property_key) or not pg.is_reachable():
        return False
    try:
        with pg.org_connection(org_id) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO property_activity
                       (org_id, user_id, property_key, action, detail)
                   VALUES (%s, %s, %s, %s, %s::jsonb)""",
                (org_id, user_id, property_key, action,
                 json.dumps(detail or {})))
            conn.commit()
        return True
    except Exception:
        return False


def log_view(org_id, user_id, property_key) -> bool:
    return log(org_id, user_id, property_key, "viewed")


def log_edit(org_id, user_id, property_key, changed_fields: list[str]) -> bool:
    return log(org_id, user_id, property_key, "edited",
               {"fields": sorted(changed_fields)})


def recent(org_id: str, days: int = 30, limit: int = 500) -> list[dict]:
    """Newest-first activity rows joined to user identity."""
    if not (org_id and pg.is_reachable()):
        return []
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    try:
        with pg.org_connection(org_id) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT a.ts, u.display_name, u.email, a.property_key,
                          a.action, a.detail
                     FROM property_activity a JOIN users u ON u.id = a.user_id
                    WHERE a.ts >= %s
                    ORDER BY a.ts DESC LIMIT %s""", (since, limit))
            out = []
            for r in cur.fetchall():
                detail = r["detail"]
                if isinstance(detail, str):
                    detail = json.loads(detail or "{}")
                fields = ", ".join((detail or {}).get("fields", []))
                out.append({
                    "when": str(r["ts"])[:16],
                    "user": r["display_name"] or r["email"],
                    "property": r["property_key"],
                    "action": r["action"],
                    "fields": fields,
                })
            return out
    except Exception:
        return []


def by_property(org_id: str, days: int = 90) -> list[dict]:
    """Rollup: per property, who touched it and when — the owner's question
    answered in one table."""
    if not (org_id and pg.is_reachable()):
        return []
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    try:
        with pg.org_connection(org_id) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT a.property_key,
                          count(*) FILTER (WHERE a.action='viewed')  AS views,
                          count(*) FILTER (WHERE a.action='edited')  AS edits,
                          count(DISTINCT a.user_id)                  AS people,
                          max(a.ts)                                  AS last_ts,
                          string_agg(DISTINCT COALESCE(u.display_name, u.email),
                                     ', ')                           AS who
                     FROM property_activity a JOIN users u ON u.id = a.user_id
                    WHERE a.ts >= %s
                    GROUP BY a.property_key
                    ORDER BY max(a.ts) DESC""", (since,))
            return [{"property": r["property_key"], "views": r["views"],
                     "edits": r["edits"], "people": r["people"],
                     "who": r["who"], "last activity": str(r["last_ts"])[:16]}
                    for r in cur.fetchall()]
    except Exception:
        return []


def current_overrides_summary(org_id: str) -> list[dict]:
    """Who holds personal card edits today (covers edits made before the
    activity trail existed — user_property_overrides.updated_at is the
    historical record)."""
    if not (org_id and pg.is_reachable()):
        return []
    try:
        with pg.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT o.property_key, o.overrides, o.updated_at,
                          u.display_name, u.email
                     FROM user_property_overrides o
                     JOIN users u ON u.id = o.user_id
                    WHERE o.org_id = %s
                    ORDER BY o.updated_at DESC""", (org_id,))
            out = []
            for r in cur.fetchall():
                ov = r["overrides"]
                if isinstance(ov, str):
                    ov = json.loads(ov or "{}")
                out.append({
                    "property": r["property_key"],
                    "user": r["display_name"] or r["email"],
                    "fields": ", ".join(sorted((ov or {}).keys())) or "(cleared)",
                    "last saved": str(r["updated_at"])[:16],
                })
            return out
    except Exception:
        return []
