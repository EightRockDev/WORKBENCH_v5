"""Consent, revocation, internal-DNC and scrub ledgers (spec §4.4 C1/C5).

  C1  Internal do-not-call ledger retained 5 years; DNC scrub valid 31 days and
      auto re-scrubbed on expiry at campaign start.
  C5  Any opt-out via any channel is honored across ALL channels and propagates
      to the tenant's internal DNC list immediately (the FCC allows up to 10
      business days; we honor on write).

All tables are org-private (RLS), so every call goes through
``data.pg.org_connection``.
"""

from __future__ import annotations

import datetime as dt

from data import pg

SCRUB_VALID_DAYS = 31          # C1 — FTC rule
INTERNAL_DNC_RETENTION_YEARS = 5


# ---------------------------------------------------------------------------
# C5 — consent
# ---------------------------------------------------------------------------

def record_consent(org_id: str, e164: str, channel: str = "voice", *,
                   kind: str = "express_written", evidence: str = "",
                   user_id: str | None = None) -> str:
    """Record prior express written consent for a number (C3/C5)."""
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO consent_records (org_id, e164, channel, consent_kind, evidence, created_by)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (org_id, e164, channel, kind, evidence, user_id))
        cid = str(cur.fetchone()["id"])
        conn.commit()
        return cid


def has_consent(org_id: str, e164: str, channel: str = "voice") -> bool:
    """True only for an unrevoked, unexpired express-written consent."""
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM consent_records
                WHERE org_id=%s AND e164=%s AND channel IN (%s,'all')
                  AND consent_kind='express_written'
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > now())
                LIMIT 1""", (org_id, e164, channel))
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# C5 — revocation (opt-out), cross-channel
# ---------------------------------------------------------------------------

def record_revocation(org_id: str, *, e164: str | None = None, email: str | None = None,
                      scope: str = "all", source: str = "manual", note: str = "") -> str:
    """Honor an opt-out immediately: revoke matching consents and add the number
    to the internal DNC list in the same transaction (C5)."""
    if not e164 and not email:
        raise ValueError("revocation needs a phone or an email")
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO revocations (org_id, e164, email, scope, source, note)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (org_id, e164, email, scope, source, note))
        rid = str(cur.fetchone()["id"])
        if e164:
            cur.execute(
                "UPDATE consent_records SET revoked_at = now() "
                " WHERE org_id=%s AND e164=%s AND revoked_at IS NULL", (org_id, e164))
            cur.execute(
                """INSERT INTO internal_dnc (org_id, e164, reason)
                   VALUES (%s,%s,%s) ON CONFLICT (org_id, e164) DO NOTHING""",
                (org_id, e164, f"opt-out via {source}"))
        conn.commit()
        return rid


def is_revoked(org_id: str, *, e164: str | None = None, email: str | None = None,
               channel: str = "voice") -> bool:
    """True if this contact opted out of ``channel`` (or of everything)."""
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        if e164:
            cur.execute(
                """SELECT 1 FROM revocations WHERE org_id=%s AND e164=%s
                     AND scope IN ('all',%s) LIMIT 1""", (org_id, e164, channel))
            if cur.fetchone():
                return True
        if email:
            cur.execute(
                """SELECT 1 FROM revocations WHERE org_id=%s AND email=%s
                     AND scope IN ('all',%s) LIMIT 1""", (org_id, email, channel))
            if cur.fetchone():
                return True
        return False


# ---------------------------------------------------------------------------
# C1 — internal DNC + scrub freshness
# ---------------------------------------------------------------------------

def on_internal_dnc(org_id: str, e164: str) -> bool:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM internal_dnc WHERE org_id=%s AND e164=%s", (org_id, e164))
        return cur.fetchone() is not None


def add_internal_dnc(org_id: str, e164: str, reason: str = "manual") -> None:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO internal_dnc (org_id, e164, reason) VALUES (%s,%s,%s)
               ON CONFLICT (org_id, e164) DO NOTHING""", (org_id, e164, reason))
        conn.commit()


def record_scrub(org_id: str, e164: str, *, federal: bool, states: list[str],
                 litigator: bool, vendor: str = "trestle") -> None:
    """Persist a DNC/litigator scrub result; valid 31 days (C1/C2)."""
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO dnc_scrubs (org_id, e164, federal, states, litigator, vendor,
                                       expires_at)
               VALUES (%s,%s,%s,%s,%s,%s, now() + make_interval(days => %s))""",
            (org_id, e164, federal, states, litigator, vendor, SCRUB_VALID_DAYS))
        conn.commit()


def latest_scrub(org_id: str, e164: str) -> dict | None:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM dnc_scrubs WHERE org_id=%s AND e164=%s
                ORDER BY scrubbed_at DESC LIMIT 1""", (org_id, e164))
        row = cur.fetchone()
        return dict(row) if row else None


def scrub_is_fresh(scrub: dict | None, now: dt.datetime | None = None) -> bool:
    if not scrub:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    exp = scrub.get("expires_at")
    return bool(exp and exp > now)


# ---------------------------------------------------------------------------
# C4 — frequency counting (per person, across ALL campaign types)
# ---------------------------------------------------------------------------

def touches_today(org_id: str, e164: str) -> int:
    """Allowed outbound touches to this number since local midnight (UTC-day
    approximation is intentional here; the quiet-hours rule enforces the
    called-party local window separately)."""
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM outreach_touches
                WHERE org_id=%s AND e164=%s AND allowed = true
                  AND ts >= date_trunc('day', now())""", (org_id, e164))
        return int(cur.fetchone()["n"])


def touches_in_days(org_id: str, e164: str, days: int) -> int:
    with pg.org_connection(org_id) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM outreach_touches
                WHERE org_id=%s AND e164=%s AND allowed = true
                  AND ts >= now() - make_interval(days => %s)""", (org_id, e164, days))
        return int(cur.fetchone()["n"])
