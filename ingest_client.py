"""
Eight Rock Workbench — ingest API client.

Drop-in wrapper so any Workbench module can read and write deal records without
re-implementing HTTP calls or auth.

    from ingest_client import IngestClient

    api = IngestClient.from_env()
    for deal in api.pending_deals():
        st.write(deal["deal_name"], deal["units"])

In Streamlit, cache the client and the reads:

    @st.cache_resource
    def _api():
        return IngestClient.from_env()

    @st.cache_data(ttl=60)
    def _pending():
        return _api().pending_deals()

Config comes from the environment (.env):
    EIGHT_ROCK_INGEST_URL     default http://127.0.0.1:8600  (api_server.py)
    EIGHT_ROCK_INGEST_TOKEN   required

Every method raises IngestError on a non-2xx response, with the status code and
the server's message attached. Nothing raises on an empty result — an empty
list is an empty list.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any, Iterable, Literal

import httpx

__all__ = ["IngestClient", "IngestError", "DealRecord"]

Kind = Literal["rentRoll", "t12", "om", "other"]
Status = Literal["pending", "merged", "ignored", "all"]

DEFAULT_URL = "http://127.0.0.1:8600"
DEFAULT_TIMEOUT = 30.0


def _load_dotenv() -> None:
    """Read .env from this folder into the environment, so the client picks up
    the same password the service uses without anything being passed in."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()




class IngestError(RuntimeError):
    """Non-2xx from the ingest API."""

    def __init__(self, status_code: int, detail: Any, path: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.path = path
        super().__init__(f"{status_code} on {path}: {detail}")


class DealRecord(dict):
    """A deal from the inbox. A plain dict — indexing works as normal — with
    convenience accessors for the fields Workbench pages reach for most."""

    @property
    def metrics(self) -> dict[str, Any]:
        return (self.get("payload") or {}).get("metrics") or {}

    @property
    def documents(self) -> list[dict[str, Any]]:
        return (self.get("payload") or {}).get("documents") or []

    @property
    def is_underwritable(self) -> bool:
        """All three of rent roll, T-12 and OM are present."""
        return self.get("completeness") == "complete"

    def metric(self, name: str, default: Any = None) -> Any:
        """Read one metric. Returns `default` when the sweep could not
        establish it — a missing metric never comes back as zero."""
        value = self.metrics.get(name)
        return default if value is None else value


class IngestClient:
    """Synchronous client for the Workbench ingest API."""

    def __init__(self, base_url: str = DEFAULT_URL, token: str = "",
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        if not token:
            raise ValueError(
                "No ingest token. Pass token=..., or set "
                "EIGHT_ROCK_INGEST_TOKEN and use IngestClient.from_env().")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-Ingest-Token": token},
            timeout=timeout,
        )

    @classmethod
    def from_env(cls, timeout: float = DEFAULT_TIMEOUT) -> "IngestClient":
        return cls(
            base_url=os.environ.get("EIGHT_ROCK_INGEST_URL", DEFAULT_URL),
            token=os.environ.get("EIGHT_ROCK_INGEST_TOKEN", "").strip(),
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "IngestClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- plumbing --------------------------------------------------------

    def _call(self, method: str, path: str, **kw) -> dict[str, Any]:
        resp = self._client.request(method, path, **kw)
        if resp.status_code >= 300:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise IngestError(resp.status_code, detail, path)
        return resp.json()

    # -- health ----------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Liveness + inbox counts by status. Needs no token."""
        return httpx.get(f"{self.base_url}/v1/ingest/health", timeout=10).json()

    def is_up(self) -> bool:
        """True when the service answers and reports healthy. Use this to grey
        out a Workbench tab rather than letting it throw."""
        try:
            return bool(self.health().get("ok"))
        except Exception:
            return False

    # -- deals -----------------------------------------------------------

    def push_deal(self, deal: dict[str, Any]) -> dict[str, Any]:
        """Upsert one deal. Returns action: created | updated | unchanged."""
        return self._call("POST", "/v1/ingest/deal", json=deal)

    def push_deals(self, deals: Iterable[dict[str, Any]],
                   run_id: str | None = None) -> dict[str, Any]:
        """Upsert up to 500 deals in one transaction — all land or none do."""
        return self._call("POST", "/v1/ingest/batch",
                          json={"deals": list(deals), "run_id": run_id})

    def deals(self, status: Status = "pending", limit: int = 100,
              include_payload: bool = True) -> list[DealRecord]:
        """Deals from the inbox, newest first.

        include_payload=True brings the metrics and document list along, which
        is what a Workbench page almost always wants.
        """
        out = self._call("GET", "/v1/ingest/inbox", params={
            "status_filter": status, "limit": limit,
            "include_payload": include_payload})
        return [DealRecord(d) for d in out["deals"]]

    def pending_deals(self, limit: int = 100) -> list[DealRecord]:
        return self.deals("pending", limit)

    def underwritable_deals(self, limit: int = 200) -> list[DealRecord]:
        """Deals with a rent roll, a T-12 and an OM all present."""
        return [d for d in self.deals("all", limit) if d.is_underwritable]

    def deal(self, deal_key: str) -> DealRecord | None:
        """One deal by its SharePoint folder id, or None."""
        for d in self.deals("all", limit=500):
            if d.get("deal_key") == deal_key:
                return d
        return None

    def set_status(self, row_id: int,
                   status: Literal["pending", "merged", "ignored"]) -> dict[str, Any]:
        """Mark a deal merged once you've imported it into a property record —
        that stops it reappearing in the pending list."""
        return self._call("POST", f"/v1/ingest/inbox/{row_id}/status",
                          params={"new_status": status})

    def mark_merged(self, row_id: int) -> dict[str, Any]:
        return self.set_status(row_id, "merged")

    # -- documents -------------------------------------------------------

    def documents(self, deal_key: str) -> list[dict[str, Any]]:
        """Files stored for a deal. Each carries rel_path — join it to
        EIGHT_ROCK_DOCS_ROOT to open the file."""
        return self._call("GET", f"/v1/ingest/deal/{deal_key}/documents")["documents"]

    def document_path(self, deal_key: str, filename: str,
                      docs_root: str | Path | None = None) -> Path | None:
        """Absolute path to one stored document, or None if it isn't there."""
        root = Path(docs_root or os.environ.get(
            "EIGHT_ROCK_DOCS_ROOT", "data/deal_docs"))
        for d in self.documents(deal_key):
            if d["filename"] == filename:
                p = root / d["rel_path"]
                return p if p.exists() else None
        return None

    def manifest(self) -> dict[str, list[dict[str, Any]]]:
        """Every stored document hash, keyed by deal — one call."""
        return self._call("GET", "/v1/ingest/documents/manifest")["manifest"]

    def upload_document(self, deal_key: str, path: str | Path,
                        kind: Kind = "other",
                        source_item_id: str | None = None) -> dict[str, Any]:
        """Send a file's bytes to the API, which writes it to the docs root.

        For files already on this machine prefer doc_sync.py — it copies
        locally and calls register_document, which avoids the encode/decode.
        """
        path = Path(path)
        blob = path.read_bytes()
        return self._call("POST", f"/v1/ingest/deal/{deal_key}/document", json={
            "kind": kind, "filename": path.name,
            "content_b64": base64.b64encode(blob).decode(),
            "source_item_id": source_item_id})

    def register_document(self, deal_key: str, path: str | Path, kind: Kind,
                          rel_path: str,
                          source_item_id: str | None = None) -> dict[str, Any]:
        """Record a file you already copied into the docs root yourself.

        The API verifies the file exists and its size matches before accepting,
        so a half-finished copy is rejected rather than indexed.
        """
        path = Path(path)
        blob = path.read_bytes()
        return self._call("POST", f"/v1/ingest/deal/{deal_key}/document/register",
                          json={"kind": kind, "filename": path.name,
                                "rel_path": rel_path, "size_bytes": len(blob),
                                "sha256": hashlib.sha256(blob).hexdigest(),
                                "source_item_id": source_item_id})

    # -- run reports -----------------------------------------------------

    def push_digest(self, digest: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/v1/ingest/digest", json=digest)

    def digests(self, limit: int = 30,
                only_changes: bool = False) -> list[dict[str, Any]]:
        """Sweep run reports, newest first. only_changes=True skips quiet days."""
        return self._call("GET", "/v1/ingest/digest", params={
            "limit": limit, "only_changes": only_changes})["runs"]

    def last_run(self) -> dict[str, Any] | None:
        runs = self.digests(limit=1)
        return runs[0] if runs else None


def _self_test() -> int:
    """Run with:  python ingest_client.py

    Checks config, reachability, auth, and reads, then says exactly what is
    wrong if anything is.
    """
    print("Eight Rock ingest client — self test\n")

    url = os.environ.get("EIGHT_ROCK_INGEST_URL", DEFAULT_URL)
    token = os.environ.get("EIGHT_ROCK_INGEST_TOKEN", "").strip()
    print(f"  URL    {url}")
    print(f"  token  {'set (' + str(len(token)) + ' chars)' if token else 'MISSING'}")

    if not token:
        print("\n  FAIL — EIGHT_ROCK_INGEST_TOKEN is not set.")
        print("  Add it to .env, or set it for this shell:")
        print('     set EIGHT_ROCK_INGEST_TOKEN=<your token>')
        return 1

    try:
        health = httpx.get(f"{url.rstrip('/')}/v1/ingest/health", timeout=10).json()
    except Exception as exc:
        print(f"\n  FAIL — cannot reach {url} ({type(exc).__name__}).")
        print("  The client is only half the pair: ingest_api.py has to be")
        print("  running for any of this to work. Start it with ingest_api.bat")
        print("  and run this again.")
        return 1

    print(f"  health ok={health.get('ok')} version={health.get('version')}")
    print(f"  db     {health.get('db')}")
    print(f"  inbox  {health.get('inbox') or 'empty'}")

    api = IngestClient(base_url=url, token=token)
    try:
        pending = api.pending_deals(limit=5)
        underwritable = api.underwritable_deals(limit=200)
        last = api.last_run()
    except IngestError as exc:
        if exc.status_code == 401:
            print("\n  FAIL - the API rejected this password (401).")
            print("  The API is running with a different password than the one")
            print("  in .env - almost always because it was started before the")
            print("  password was added.")
            print("  FIX: close the run-api.bat window, then double-click")
            print("       run-api.bat again. That is usually all it takes.")
        else:
            print(f"\n  FAIL — {exc}")
        return 1

    print(f"\n  pending deals      {len(pending)}")
    print(f"  underwritable      {len(underwritable)}")
    print(f"  last sweep         {last['ran_at_utc'] + ' — ' + last['summary'] if last else 'none recorded yet'}")
    for d in pending[:5]:
        print(f"     {d.get('state') or '--'}  {d['deal_name'][:46]:<46} "
              f"{d.get('completeness')}  units={d.metric('units', '?')}")

    print("\n  PASS — client reaches the API, auth is good, reads work.")
    if not pending and not underwritable:
        print("  Inbox is empty. Nothing has been pushed yet; that is not a fault.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
