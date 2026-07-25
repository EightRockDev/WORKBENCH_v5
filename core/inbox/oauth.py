"""Per-user mailbox connection via OAuth (Module D, §6.2 + §8.1 SR-2/SR-3).

Security model — **private mailbox, shared pipeline**:

  * A mailbox belongs to exactly ONE user. Tokens live in
    ``mailbox_connections`` scoped to that user and are only reachable through
    :func:`data.pg.user_connection`, whose RLS policy requires BOTH the org and
    the user context. A missing user context returns zero rows (fails closed).
  * **Tokens are encrypted at rest** with Fernet (AES-128-CBC + HMAC) using
    ``ER_TOKEN_KEY``; the database never stores plaintext credentials
    (spec §8.1 SR-2.4 "encryption at rest").
  * Raw messages carry ``owner_user_id`` and are readable only by that user.
    The DEALS extracted from them are org-visible - that is the shared work.

Connection uses the **OAuth device-code flow** (MSAL, already a dependency):
the server shows a short code, the user approves it in a browser on any device,
and the server never handles the password. This works today without a public
redirect URL, so it is the right fit before the domain/HTTPS layer is live.
"""

from __future__ import annotations

import datetime as dt
import os

from data import pg

# Microsoft Graph read-only mail scope. Least privilege: read, never send.
GRAPH_SCOPES = ["Mail.Read", "User.Read"]
GRAPH_AUTHORITY = "https://login.microsoftonline.com/common"


class TokenCipherUnavailable(RuntimeError):
    """ER_TOKEN_KEY is missing/invalid, so tokens cannot be safely stored."""


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------

def _cipher():
    try:
        from cryptography.fernet import Fernet
    except ModuleNotFoundError as e:  # pragma: no cover
        raise TokenCipherUnavailable(
            "The 'cryptography' package is required to store mailbox tokens.") from e
    key = os.environ.get("ER_TOKEN_KEY")
    if not key:
        raise TokenCipherUnavailable(
            "ER_TOKEN_KEY is not set. Generate one with:\n"
            "  uv run python -c \"from cryptography.fernet import Fernet;"
            "print(Fernet.generate_key().decode())\"\n"
            "then add it to .env as ER_TOKEN_KEY=<key>. Without it, mailbox "
            "tokens cannot be encrypted at rest and will not be stored.")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        raise TokenCipherUnavailable(f"ER_TOKEN_KEY is not a valid Fernet key: {e}") from e


def encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return _cipher().encrypt(value.encode()).decode()


def decrypt(blob: str | None) -> str | None:
    if not blob:
        return None
    return _cipher().decrypt(blob.encode()).decode()


def token_key_configured() -> bool:
    try:
        _cipher()
        return True
    except TokenCipherUnavailable:
        return False


def generate_token_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Microsoft Graph device-code flow
# ---------------------------------------------------------------------------

def _msal_app():
    import msal

    client_id = os.environ.get("MS_GRAPH_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "MS_GRAPH_CLIENT_ID is not set. Register a Microsoft Entra app "
            "(public client, 'Allow public client flows' = Yes) and put its "
            "Application (client) ID in .env as MS_GRAPH_CLIENT_ID.")
    return msal.PublicClientApplication(
        client_id, authority=os.environ.get("MS_GRAPH_AUTHORITY", GRAPH_AUTHORITY))


def begin_device_flow() -> dict:
    """Start the device-code flow. Returns the dict MSAL uses to poll, which
    includes ``user_code`` and ``verification_uri`` to show the user."""
    app = _msal_app()
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Could not start device flow: {flow.get('error_description', flow)}")
    return flow


def complete_device_flow(org_id: str, user_id: str, flow: dict) -> dict:
    """Block until the user approves (or the code expires), then store the
    encrypted tokens against THIS user only."""
    app = _msal_app()
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Sign-in failed: {result.get('error_description', result)}")
    account_email = (result.get("id_token_claims") or {}).get("preferred_username")
    save_connection(org_id, user_id, provider="graph",
                    account_email=account_email,
                    access_token=result["access_token"],
                    refresh_token=result.get("refresh_token"),
                    expires_in=result.get("expires_in", 3600),
                    scopes=" ".join(GRAPH_SCOPES))
    return {"account_email": account_email}


def refresh_if_needed(org_id: str, user_id: str) -> str | None:
    """Return a valid access token for this user, refreshing when near expiry."""
    conn_row = get_connection(org_id, user_id)
    if not conn_row:
        return None
    exp = conn_row.get("expires_at")
    if exp and exp > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        return decrypt(conn_row["access_token"])

    refresh = decrypt(conn_row.get("refresh_token"))
    if not refresh:
        _mark_status(org_id, user_id, "expired")
        return None
    app = _msal_app()
    result = app.acquire_token_by_refresh_token(refresh, scopes=GRAPH_SCOPES)
    if "access_token" not in result:
        _mark_status(org_id, user_id, "expired")
        return None
    save_connection(org_id, user_id, provider="graph",
                    account_email=conn_row.get("account_email"),
                    access_token=result["access_token"],
                    refresh_token=result.get("refresh_token") or refresh,
                    expires_in=result.get("expires_in", 3600),
                    scopes=conn_row.get("scopes"))
    return result["access_token"]


# ---------------------------------------------------------------------------
# Storage (user-scoped; RLS enforces the boundary)
# ---------------------------------------------------------------------------

def save_connection(org_id: str, user_id: str, *, provider: str,
                    account_email: str | None, access_token: str,
                    refresh_token: str | None, expires_in: int,
                    scopes: str | None = None) -> None:
    enc_a, enc_r = encrypt(access_token), encrypt(refresh_token)
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(expires_in))
    with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mailbox_connections
                 (org_id, user_id, provider, account_email, access_token,
                  refresh_token, expires_at, scopes, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'connected')
               ON CONFLICT (user_id, provider) DO UPDATE
                 SET account_email=EXCLUDED.account_email,
                     access_token=EXCLUDED.access_token,
                     refresh_token=COALESCE(EXCLUDED.refresh_token,
                                            mailbox_connections.refresh_token),
                     expires_at=EXCLUDED.expires_at, status='connected'""",
            (org_id, user_id, provider, account_email, enc_a, enc_r, expires_at, scopes))
        conn.commit()


def get_connection(org_id: str, user_id: str, provider: str = "graph") -> dict | None:
    with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM mailbox_connections
                        WHERE user_id=%s AND provider=%s""", (user_id, provider))
        row = cur.fetchone()
        return dict(row) if row else None


def disconnect(org_id: str, user_id: str, provider: str = "graph",
               purge_messages: bool = True) -> None:
    """Revoke the connection. By default also deletes that user's stored raw
    messages — their mail should not outlive their consent. Deals already
    created stay: they are org-owned work product, not personal mail."""
    with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mailbox_connections WHERE user_id=%s AND provider=%s",
                    (user_id, provider))
        if purge_messages:
            cur.execute("DELETE FROM inbox_messages WHERE owner_user_id=%s", (user_id,))
        conn.commit()


def _mark_status(org_id: str, user_id: str, status: str) -> None:
    with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
        cur.execute("UPDATE mailbox_connections SET status=%s WHERE user_id=%s",
                    (status, user_id))
        conn.commit()


def touch_sync(org_id: str, user_id: str) -> None:
    with pg.user_connection(org_id, user_id) as conn, conn.cursor() as cur:
        cur.execute("UPDATE mailbox_connections SET last_sync_at=now() WHERE user_id=%s",
                    (user_id,))
        conn.commit()
