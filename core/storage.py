"""Storage abstraction — local disk vs Microsoft Graph (OneDrive).

The workbench was built to run against Brian's locally-synced OneDrive
folder. When we deploy to Azure App Service, the container has no OneDrive
sync; it must read/write the same files via the Microsoft Graph API
against the same OneDrive drive Brian's desktop syncs. This module is the
single seam that hides the difference.

Both backends expose the same `Storage` protocol. Consumer code reads:

    from core.storage import get_storage
    storage = get_storage()
    text = storage.read_text("Properties/Driftwood-140-Virginia-Beach/deal.json")

Backend is selected at runtime via the ``ER_STORAGE_BACKEND`` env var:

    ER_STORAGE_BACKEND=local   (default — Brian's desktop, dev machines)
    ER_STORAGE_BACKEND=graph   (Azure App Service deployment)

When graph mode is active, Graph credentials are read from:

    ER_GRAPH_TENANT_ID         Entra tenant GUID
    ER_GRAPH_CLIENT_ID         Service-principal client (app reg) GUID
    ER_GRAPH_CLIENT_SECRET     Client secret from Entra app reg
    ER_GRAPH_DRIVE_ID          The OneDrive drive ID containing Properties/
                               (lookup via `GET /me/drive` or `/users/{id}/drive`)
    ER_GRAPH_ROOT_PATH         Root inside the drive (default: "")

The storage layer is intentionally narrow — read/write/list/exists/mtime/
delete. It does NOT abstract SQLite connections; those continue to use
`sqlite3.connect(path)` directly against an Azure Files mount at
``/mnt/workbench-data/`` in the App Service container (see Dockerfile +
infra/main.bicep). SQLite over a Graph REST API would be a disaster.

Migration strategy
------------------
Existing code that does ``Path(...)`` against the OneDrive folder is being
migrated incrementally. See ``docs/STORAGE_MIGRATION.md`` for the plan and
file checklist. This module ships first so the abstraction is reviewed
before the 192-call refactor starts.
"""

from __future__ import annotations

import datetime as dt
import io
import os
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


# ---------------------------------------------------------------------------
# Storage Protocol — the interface every backend implements
# ---------------------------------------------------------------------------

class Storage(Protocol):
    """The minimum contract for any backend.

    `path` arguments are POSIX-style relative paths rooted at the workbench
    root (e.g. ``Properties/Driftwood-140-Virginia-Beach/deal.json``).
    Backends translate to their own native addressing.
    """

    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def read_text(self, path: str, encoding: str = "utf-8") -> str: ...
    def write_text(self, path: str, data: str, encoding: str = "utf-8") -> None: ...
    def exists(self, path: str) -> bool: ...
    def is_file(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def list_dir(self, path: str) -> list[str]: ...
    def mtime(self, path: str) -> dt.datetime | None: ...
    def delete(self, path: str) -> None: ...
    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None: ...

    @property
    def backend_label(self) -> str:
        """Short identifier used by the sidebar status indicator."""
        ...


# ---------------------------------------------------------------------------
# Local disk backend — what Brian's desktop uses today
# ---------------------------------------------------------------------------

class LocalDiskStorage:
    """Pass-through to `pathlib.Path` rooted at a local directory.

    Default root is the APPLICATION root — the folder holding ``app.py``,
    ``core/`` and ``Properties/``. Configurable via ``ER_LOCAL_ROOT`` or the
    ``root`` constructor argument (tests use this to point at a tmp dir).

    This default was wrong from the v5 split until 2026-08-15, and it is
    worth spelling out because the symptom never once looked like a path
    bug. The line read ``parent.parent.parent`` with the comment
    "python_workbench/core/storage.py -> workbench root" — correct for the
    v1 layout, where this file sat one directory deeper. In v5 the file is
    ``<app>/core/storage.py``, so three parents up lands ONE LEVEL ABOVE the
    app: ``C:\\`` instead of ``C:\\WORKBENCH_V5``.

    Every deal-folder read goes through here as the relative key
    ``Properties/...``, so they were all resolving against ``C:\\Properties``
    — a directory that does not exist. ``discover_property_folders()``
    returned an empty list, no property ever matched a folder, no
    ``sales.json`` was ever opened, and Sale History fell through to county
    records and blamed a nightly data feed for a file sitting on disk.

    It hid for as long as it did because it USED to be right by accident:
    under the old sibling layout the deal folders really did live beside the
    app, so "one level above the app" and "where the folders are" were the
    same directory. Moving the app into ``C:\\WORKBENCH_V5`` separated them
    and silently severed every folder read.

    ``test_storage_root_matches_property_io`` pins the invariant that
    actually matters: this root and ``data.property_io._WB_ROOT`` must name
    the same directory, because one composes the keys the other resolves.
    """

    backend_label = "local-disk"

    def __init__(self, root: Path | str | None = None) -> None:
        if root is None:
            env_root = os.getenv("ER_LOCAL_ROOT")
            if env_root:
                root = Path(env_root)
            else:
                # <app>/core/storage.py -> <app>
                root = Path(__file__).resolve().parent.parent
        self.root = Path(root).resolve()

    def _resolve(self, path: str) -> Path:
        """Translate a user-supplied path key into a Path.

        Policy:
          - Absolute paths: trusted, used as-is. This is required because
            ``data.property_io._rel()`` falls back to absolute paths when
            callers pass a folder outside the local workbench root (e.g.
            test ``tmp_path`` fixtures).
          - Relative paths: resolved against ``self.root`` and checked for
            ``..``-style traversal escapes (which are a real attack vector
            on user-controlled input).
        """
        as_path = Path(path)
        if as_path.is_absolute():
            return as_path.resolve()
        p = (self.root / path).resolve()
        # Prevent escapes via ../ — paths must stay rooted at self.root
        if not str(p).startswith(str(self.root)):
            raise ValueError(f"Path escapes storage root: {path}")
        return p

    def read_bytes(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        """Atomic write via tempfile + rename — same crash-safety as the
        existing property_io.py pattern. A mid-write power-down can't
        produce a half-written file at `path`; the temp file gets cleaned
        up on next boot."""
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile(
            "wb", dir=str(p.parent), delete=False, suffix=".tmp",
        ) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        tmp_path.replace(p)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return self._resolve(path).read_text(encoding=encoding)

    def write_text(self, path: str, data: str, encoding: str = "utf-8") -> None:
        """Atomic write via tempfile + rename (see write_bytes)."""
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile(
            "w", encoding=encoding, dir=str(p.parent),
            delete=False, suffix=".tmp",
        ) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        tmp_path.replace(p)

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def is_file(self, path: str) -> bool:
        return self._resolve(path).is_file()

    def is_dir(self, path: str) -> bool:
        return self._resolve(path).is_dir()

    def list_dir(self, path: str) -> list[str]:
        p = self._resolve(path)
        if not p.is_dir():
            return []
        return sorted(child.name for child in p.iterdir())

    def mtime(self, path: str) -> dt.datetime | None:
        p = self._resolve(path)
        if not p.exists():
            return None
        return dt.datetime.fromtimestamp(p.stat().st_mtime)

    def delete(self, path: str) -> None:
        p = self._resolve(path)
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            # Walk and remove — Path.rmtree doesn't exist
            for child in sorted(p.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            p.rmdir()

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        self._resolve(path).mkdir(parents=parents, exist_ok=exist_ok)


# ---------------------------------------------------------------------------
# Microsoft Graph backend — reads/writes OneDrive via REST
# ---------------------------------------------------------------------------

class GraphStorage:
    """Microsoft Graph API client for OneDrive Properties/ access.

    Uses **app-only auth** (client credentials flow) — the App Service
    container has no user logged in. The Entra app registration needs:

      Microsoft Graph application permissions:
        - Files.ReadWrite.All       (read/write any file in the tenant)

    Granted with admin consent. ``ER_GRAPH_DRIVE_ID`` pins which drive the
    workbench reads from (Brian's OneDrive, not a shared SharePoint site,
    unless we later move Properties/ to SharePoint — easier than it sounds).

    Caching: file metadata + small reads are cached in-memory for
    ``ER_GRAPH_CACHE_TTL_SECONDS`` (default 300). Calibration cron + UI
    paint both hit lots of repeated `is_file()` calls; without caching
    each becomes a Graph round-trip.

    Throttling: Graph imposes per-app and per-mailbox throttles. We
    retry-on-429 with the Retry-After header. Aggressive use of `list_dir`
    on the Properties/ root (26 folders) on every UI paint would hit
    limits — consumers should cache at their layer too.
    """

    backend_label = "graph-onedrive"
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        drive_id: str | None = None,
        root_path: str | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        # Lazy import — keep msal/requests out of import cost when running local
        try:
            import msal  # noqa: F401
            import requests  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "GraphStorage requires `msal` and `requests` — install via "
                "`uv sync` after updating pyproject.toml"
            ) from e

        self.tenant_id = tenant_id or os.environ["ER_GRAPH_TENANT_ID"]
        self.client_id = client_id or os.environ["ER_GRAPH_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["ER_GRAPH_CLIENT_SECRET"]
        self.drive_id = drive_id or os.environ["ER_GRAPH_DRIVE_ID"]
        self.root_path = (root_path or os.getenv("ER_GRAPH_ROOT_PATH", "")).strip("/")
        self.cache_ttl = int(
            cache_ttl_seconds if cache_ttl_seconds is not None
            else os.getenv("ER_GRAPH_CACHE_TTL_SECONDS", "300")
        )

        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    # ---- Auth ----
    def _get_token(self) -> str:
        import msal
        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token
        app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
        )
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"],
        )
        if "access_token" not in result:
            raise RuntimeError(
                f"Graph token acquisition failed: "
                f"{result.get('error_description') or result.get('error')}"
            )
        self._token = result["access_token"]
        self._token_expires_at = now + int(result.get("expires_in", 3600))
        return self._token

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self._get_token()}"}
        if extra:
            h.update(extra)
        return h

    def _full_path(self, path: str) -> str:
        """Combine root + user-supplied path, normalized for Graph URLs."""
        p = path.replace("\\", "/").strip("/")
        if self.root_path:
            p = f"{self.root_path}/{p}" if p else self.root_path
        return p

    def _drive_item_url(self, path: str) -> str:
        full = self._full_path(path)
        if not full:
            return f"{self.GRAPH_BASE}/drives/{self.drive_id}/root"
        # Graph wants colon-encoded paths: /drives/{id}/root:/folder/file:/...
        return f"{self.GRAPH_BASE}/drives/{self.drive_id}/root:/{full}:"

    def _request(
        self, method: str, url: str,
        *, params: dict | None = None,
        data: bytes | None = None, json: dict | None = None,
        headers: dict | None = None, retry: int = 3,
    ):
        import requests
        for attempt in range(retry):
            r = requests.request(
                method, url,
                headers=self._headers(headers),
                params=params, data=data, json=json,
                timeout=60,
            )
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "5"))
                time.sleep(min(wait, 30))
                continue
            if r.status_code >= 500 and attempt < retry - 1:
                time.sleep(2 ** attempt)
                continue
            return r
        return r  # type: ignore[possibly-unbound]

    # ---- Cache helpers ----
    def _cache_get(self, key: str):
        with self._lock:
            ent = self._cache.get(key)
            if not ent:
                return None
            ts, val = ent
            if time.time() - ts > self.cache_ttl:
                self._cache.pop(key, None)
                return None
            return val

    def _cache_set(self, key: str, val) -> None:
        with self._lock:
            self._cache[key] = (time.time(), val)

    def _cache_invalidate(self, key_prefix: str) -> None:
        with self._lock:
            for k in list(self._cache):
                if k.startswith(key_prefix):
                    self._cache.pop(k, None)

    # ---- Protocol implementation ----
    def read_bytes(self, path: str) -> bytes:
        cached = self._cache_get(f"bytes:{path}")
        if cached is not None:
            return cached  # type: ignore[return-value]
        url = f"{self._drive_item_url(path)}/content"
        r = self._request("GET", url)
        r.raise_for_status()
        self._cache_set(f"bytes:{path}", r.content)
        return r.content

    def write_bytes(self, path: str, data: bytes) -> None:
        # Small files (<4MB) use simple upload; larger files need upload sessions
        if len(data) < 4 * 1024 * 1024:
            url = f"{self._drive_item_url(path)}/content"
            r = self._request(
                "PUT", url, data=data,
                headers={"Content-Type": "application/octet-stream"},
            )
            r.raise_for_status()
        else:
            self._upload_large(path, data)
        self._cache_invalidate(f"bytes:{path}")
        self._cache_invalidate(f"meta:{path}")
        self._cache_invalidate(f"list:{Path(path).parent.as_posix()}")

    def _upload_large(self, path: str, data: bytes) -> None:
        import requests
        # Create upload session, then chunk
        session_url = (
            f"{self._drive_item_url(path)}/createUploadSession"
        )
        r = self._request("POST", session_url, json={"item": {
            "@microsoft.graph.conflictBehavior": "replace",
        }})
        r.raise_for_status()
        upload_url = r.json()["uploadUrl"]
        chunk_size = 5 * 1024 * 1024  # 5 MB chunks; multiple of 320 KiB
        total = len(data)
        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total) - 1
            chunk = data[start:end + 1]
            headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {start}-{end}/{total}",
            }
            cr = requests.put(upload_url, headers=headers, data=chunk, timeout=120)
            if cr.status_code not in (200, 201, 202):
                cr.raise_for_status()

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(path).decode(encoding)

    def write_text(self, path: str, data: str, encoding: str = "utf-8") -> None:
        self.write_bytes(path, data.encode(encoding))

    def _stat(self, path: str) -> dict | None:
        cached = self._cache_get(f"meta:{path}")
        if cached is not None:
            return cached  # type: ignore[return-value]
        r = self._request("GET", self._drive_item_url(path))
        if r.status_code == 404:
            self._cache_set(f"meta:{path}", None)
            return None
        r.raise_for_status()
        meta = r.json()
        self._cache_set(f"meta:{path}", meta)
        return meta

    def exists(self, path: str) -> bool:
        return self._stat(path) is not None

    def is_file(self, path: str) -> bool:
        meta = self._stat(path)
        return meta is not None and "file" in meta

    def is_dir(self, path: str) -> bool:
        meta = self._stat(path)
        return meta is not None and "folder" in meta

    def list_dir(self, path: str) -> list[str]:
        cached = self._cache_get(f"list:{path}")
        if cached is not None:
            return cached  # type: ignore[return-value]
        # /children endpoint with pagination
        url = f"{self._drive_item_url(path)}/children"
        if path == "" or path == ".":
            url = f"{self.GRAPH_BASE}/drives/{self.drive_id}/root/children"
        names: list[str] = []
        while url:
            r = self._request("GET", url)
            r.raise_for_status()
            body = r.json()
            names.extend(item["name"] for item in body.get("value", []))
            url = body.get("@odata.nextLink")
        names.sort()
        self._cache_set(f"list:{path}", names)
        return names

    def mtime(self, path: str) -> dt.datetime | None:
        meta = self._stat(path)
        if meta is None:
            return None
        # ISO-8601 like "2026-05-26T14:33:09Z"
        ts = meta.get("lastModifiedDateTime")
        if not ts:
            return None
        try:
            return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None

    def delete(self, path: str) -> None:
        r = self._request("DELETE", self._drive_item_url(path))
        if r.status_code not in (204, 200, 404):
            r.raise_for_status()
        self._cache_invalidate(f"bytes:{path}")
        self._cache_invalidate(f"meta:{path}")
        self._cache_invalidate(f"list:{Path(path).parent.as_posix()}")

    def mkdir(self, path: str, parents: bool = True, exist_ok: bool = True) -> None:
        # Graph creates folders by POSTing to the parent's children endpoint
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if self.exists(sub):
                if not exist_ok:
                    raise FileExistsError(sub)
                continue
            if not parents and i < len(parts):
                raise FileNotFoundError(
                    f"Parent does not exist and parents=False: {sub}"
                )
            parent = "/".join(parts[:i - 1])
            parent_url = (
                f"{self.GRAPH_BASE}/drives/{self.drive_id}/root/children"
                if not parent
                else f"{self._drive_item_url(parent)}/children"
            )
            r = self._request("POST", parent_url, json={
                "name": parts[i - 1],
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail" if not exist_ok else "replace",
            })
            r.raise_for_status()
        self._cache_invalidate(f"list:{Path(path).parent.as_posix()}")


# ---------------------------------------------------------------------------
# Factory — single entry point for consumer code
# ---------------------------------------------------------------------------

_storage_singleton: Storage | None = None
_singleton_lock = threading.Lock()


def get_storage() -> Storage:
    """Return the configured storage backend.

    Reads ``ER_STORAGE_BACKEND`` once at first call and caches the resulting
    instance — backends hold connection/credential state that's expensive
    to recreate per call. Call `reset_storage()` in tests to clear.
    """
    global _storage_singleton
    if _storage_singleton is not None:
        return _storage_singleton
    with _singleton_lock:
        if _storage_singleton is not None:
            return _storage_singleton
        backend = (os.getenv("ER_STORAGE_BACKEND") or "local").lower()
        if backend == "local":
            _storage_singleton = LocalDiskStorage()
        elif backend == "graph":
            _storage_singleton = GraphStorage()
        else:
            raise ValueError(
                f"Unknown ER_STORAGE_BACKEND={backend!r}. "
                "Expected 'local' or 'graph'."
            )
    return _storage_singleton


def reset_storage() -> None:
    """Drop the cached singleton — used by tests to swap backends mid-process."""
    global _storage_singleton
    with _singleton_lock:
        _storage_singleton = None
