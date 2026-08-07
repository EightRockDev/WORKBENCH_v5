"""API keys + usage metering for the Data API (owner ask 2026-08-07, spec §6.5
Module G: "Stripe billing with usage meters for AI and data pulls").

v1 scope: per-org keys, hashed at rest, with one usage row per request — the
meter Stripe will read when billing lands. No billing yet.

Bootstrap note (mirrors `organizations`): `api_keys` is deliberately NOT under
row-level security — verifying a key is what DISCOVERS the org, so the lookup
runs before any org context exists. The table stores only SHA-256 hashes;
the secret itself is shown once at creation and never persisted. `api_usage`
is written by the API server in the same pre-context position. The admin UI
always filters both by its own org_id in SQL.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import secrets
from dataclasses import dataclass

from data import pg

KEY_PREFIX = "8rk_"
# Per-key daily request cap until real billing tiers exist. Override in .env.
DAILY_CAP = int(os.environ.get("ER_API_DAILY_CAP", "10000"))


@dataclass
class ApiKey:
    id: str
    org_id: str
    label: str
    prefix_hint: str          # first 12 chars, for recognisable listings
    status: str               # active | revoked
    created_at: str | None = None
    requests_today: int = 0


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_key(org_id: str, label: str, created_by: str) -> tuple[ApiKey, str]:
    """Mint a key for an org. Returns (record, THE SECRET) — the secret is
    displayed once and never stored; only its hash lands in Postgres."""
    secret = KEY_PREFIX + secrets.token_urlsafe(32)
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO api_keys (org_id, key_hash, prefix_hint, label, created_by)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id, org_id, label, prefix_hint, status""",
            (org_id, _hash(secret), secret[:12], label or "unnamed", created_by))
        row = cur.fetchone()
        conn.commit()
    return ApiKey(id=str(row["id"]), org_id=str(row["org_id"]),
                  label=row["label"], prefix_hint=row["prefix_hint"],
                  status=row["status"]), secret


def revoke_key(org_id: str, key_id: str) -> bool:
    """Revoke one of the org's keys. org_id is required and checked in SQL —
    an org can never revoke (or probe) another org's keys."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE api_keys SET status = 'revoked' "
            "WHERE id = %s AND org_id = %s AND status = 'active'",
            (key_id, org_id))
        hit = cur.rowcount == 1
        conn.commit()
    return hit


def list_keys(org_id: str) -> list[ApiKey]:
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT k.id, k.org_id, k.label, k.prefix_hint, k.status,
                      k.created_at,
                      COALESCE(u.n, 0) AS requests_today
                 FROM api_keys k
                 LEFT JOIN (SELECT key_id, count(*) AS n FROM api_usage
                             WHERE ts >= date_trunc('day', now())
                             GROUP BY key_id) u ON u.key_id = k.id
                WHERE k.org_id = %s
                ORDER BY k.created_at DESC""", (org_id,))
        return [ApiKey(id=str(r["id"]), org_id=str(r["org_id"]),
                       label=r["label"], prefix_hint=r["prefix_hint"],
                       status=r["status"], created_at=str(r["created_at"]),
                       requests_today=r["requests_today"])
                for r in cur.fetchall()]


def verify_key(secret: str) -> ApiKey | None:
    """Resolve a presented secret to its active key record, or None. Runs
    pre-org-context by design (see module docstring)."""
    if not secret or not secret.startswith(KEY_PREFIX):
        return None
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, org_id, label, prefix_hint, status FROM api_keys
                WHERE key_hash = %s AND status = 'active'""", (_hash(secret),))
        row = cur.fetchone()
    if row is None:
        return None
    return ApiKey(id=str(row["id"]), org_id=str(row["org_id"]),
                  label=row["label"], prefix_hint=row["prefix_hint"],
                  status=row["status"])


def meter(key: ApiKey, endpoint: str, units: int = 1) -> bool:
    """Record one billable request. Returns False when the key is over its
    daily cap (caller responds 429) — the meter row is still written so the
    overage itself is visible in the usage data."""
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM api_usage "
            "WHERE key_id = %s AND ts >= date_trunc('day', now())", (key.id,))
        used = cur.fetchone()["n"]
        cur.execute(
            """INSERT INTO api_usage (org_id, key_id, endpoint, units, over_cap)
               VALUES (%s, %s, %s, %s, %s)""",
            (key.org_id, key.id, endpoint, units, used >= DAILY_CAP))
        conn.commit()
    return used < DAILY_CAP


def usage_summary(org_id: str, days: int = 30) -> list[dict]:
    """Per-day request counts for the org — the admin-tab usage view and the
    shape a Stripe usage record will be built from."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    with pg.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT date_trunc('day', ts) AS day, count(*) AS requests,
                      sum(units) AS units
                 FROM api_usage WHERE org_id = %s AND ts >= %s
                 GROUP BY 1 ORDER BY 1 DESC""", (org_id, since))
        return [{"day": str(r["day"])[:10], "requests": r["requests"],
                 "units": int(r["units"] or 0)} for r in cur.fetchall()]
