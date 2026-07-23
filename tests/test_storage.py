"""Tests for core.storage — local backend round-trips + backend switching.

GraphStorage tests would require a live tenant; covered by an integration
test that's run only during the deploy runbook (Step 5 verification curl).
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from core.storage import (
    GraphStorage,
    LocalDiskStorage,
    Storage,
    get_storage,
    reset_storage,
)


@pytest.fixture(autouse=True)
def _clear_storage_cache():
    reset_storage()
    yield
    reset_storage()


# ---------------------------------------------------------------------------
# LocalDiskStorage — basic round-trips
# ---------------------------------------------------------------------------

class TestLocalDiskRoundTrips:
    def test_text_roundtrip(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        s.write_text("foo/bar.txt", "hello world")
        assert s.read_text("foo/bar.txt") == "hello world"

    def test_bytes_roundtrip(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        payload = b"\x00\x01binary\xff"
        s.write_bytes("data/blob.bin", payload)
        assert s.read_bytes("data/blob.bin") == payload

    def test_creates_parent_dirs_on_write(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        s.write_text("a/b/c/d.txt", "x")
        assert (tmp_path / "a" / "b" / "c" / "d.txt").exists()

    def test_exists_is_file_is_dir(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        s.write_text("file.txt", "x")
        s.mkdir("dir")
        assert s.exists("file.txt")
        assert s.is_file("file.txt")
        assert not s.is_dir("file.txt")
        assert s.exists("dir")
        assert s.is_dir("dir")
        assert not s.is_file("dir")
        assert not s.exists("nonexistent")

    def test_list_dir(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        for n in ("a.txt", "b.txt", "c.txt"):
            s.write_text(f"folder/{n}", "x")
        assert s.list_dir("folder") == ["a.txt", "b.txt", "c.txt"]

    def test_list_dir_missing_returns_empty(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        assert s.list_dir("nonexistent") == []

    def test_mtime(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        s.write_text("f.txt", "x")
        m = s.mtime("f.txt")
        assert isinstance(m, dt.datetime)
        # Should be very recent
        assert (dt.datetime.now() - m).total_seconds() < 5

    def test_mtime_missing_returns_none(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        assert s.mtime("nonexistent") is None

    def test_delete_file(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        s.write_text("f.txt", "x")
        assert s.exists("f.txt")
        s.delete("f.txt")
        assert not s.exists("f.txt")

    def test_delete_directory(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        s.write_text("d/a.txt", "x")
        s.write_text("d/b/c.txt", "y")
        s.delete("d")
        assert not s.exists("d")

    def test_mkdir_idempotent(self, tmp_path: Path):
        s = LocalDiskStorage(root=tmp_path)
        s.mkdir("a/b/c")
        s.mkdir("a/b/c")  # should not raise
        assert s.is_dir("a/b/c")

    def test_path_escape_blocked(self, tmp_path: Path):
        """Storage paths must not escape the root via ../ traversal."""
        s = LocalDiskStorage(root=tmp_path)
        with pytest.raises(ValueError, match="escapes storage root"):
            s.read_text("../../../etc/passwd")


# ---------------------------------------------------------------------------
# Factory + backend selection
# ---------------------------------------------------------------------------

class TestFactory:
    def test_default_is_local(self, monkeypatch):
        monkeypatch.delenv("ER_STORAGE_BACKEND", raising=False)
        s = get_storage()
        assert isinstance(s, LocalDiskStorage)
        assert s.backend_label == "local-disk"

    def test_explicit_local(self, monkeypatch):
        monkeypatch.setenv("ER_STORAGE_BACKEND", "local")
        s = get_storage()
        assert isinstance(s, LocalDiskStorage)

    def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setenv("ER_STORAGE_BACKEND", "s3-someday")
        with pytest.raises(ValueError, match="Unknown ER_STORAGE_BACKEND"):
            get_storage()

    def test_singleton_caches(self, monkeypatch):
        monkeypatch.setenv("ER_STORAGE_BACKEND", "local")
        a = get_storage()
        b = get_storage()
        assert a is b

    def test_reset_drops_singleton(self, monkeypatch):
        monkeypatch.setenv("ER_STORAGE_BACKEND", "local")
        a = get_storage()
        reset_storage()
        b = get_storage()
        assert a is not b


# ---------------------------------------------------------------------------
# Graph backend — wired up without making a live request
# ---------------------------------------------------------------------------

class TestGraphStorageStub:
    """We don't hit a real tenant in unit tests. These verify URL construction
    + that init reads env vars correctly."""

    def test_init_reads_env(self, monkeypatch):
        monkeypatch.setenv("ER_GRAPH_TENANT_ID", "tenant-guid")
        monkeypatch.setenv("ER_GRAPH_CLIENT_ID", "client-guid")
        monkeypatch.setenv("ER_GRAPH_CLIENT_SECRET", "secret")
        monkeypatch.setenv("ER_GRAPH_DRIVE_ID", "drive-guid")
        monkeypatch.setenv("ER_GRAPH_ROOT_PATH", "8 Rock Shared Files")
        g = GraphStorage()
        assert g.tenant_id == "tenant-guid"
        assert g.drive_id == "drive-guid"
        assert g.root_path == "8 Rock Shared Files"
        assert g.backend_label == "graph-onedrive"

    def test_drive_item_url_construction(self, monkeypatch):
        monkeypatch.setenv("ER_GRAPH_TENANT_ID", "t")
        monkeypatch.setenv("ER_GRAPH_CLIENT_ID", "c")
        monkeypatch.setenv("ER_GRAPH_CLIENT_SECRET", "s")
        monkeypatch.setenv("ER_GRAPH_DRIVE_ID", "drive123")
        monkeypatch.setenv("ER_GRAPH_ROOT_PATH", "RootDir")
        g = GraphStorage()
        url = g._drive_item_url("Properties/Driftwood/deal.json")
        assert "drives/drive123/root:/RootDir/Properties/Driftwood/deal.json:" in url

    def test_drive_item_url_empty_root(self, monkeypatch):
        monkeypatch.setenv("ER_GRAPH_TENANT_ID", "t")
        monkeypatch.setenv("ER_GRAPH_CLIENT_ID", "c")
        monkeypatch.setenv("ER_GRAPH_CLIENT_SECRET", "s")
        monkeypatch.setenv("ER_GRAPH_DRIVE_ID", "drive123")
        monkeypatch.delenv("ER_GRAPH_ROOT_PATH", raising=False)
        g = GraphStorage()
        url = g._drive_item_url("foo.txt")
        assert "drives/drive123/root:/foo.txt:" in url


# ---------------------------------------------------------------------------
# Protocol conformance — sanity that LocalDiskStorage satisfies Storage
# ---------------------------------------------------------------------------

def test_local_disk_storage_is_storage():
    """Quack-typed: LocalDiskStorage should satisfy the Storage protocol."""
    s: Storage = LocalDiskStorage(root=Path("/tmp"))
    assert hasattr(s, "read_bytes")
    assert hasattr(s, "write_bytes")
    assert hasattr(s, "list_dir")
    assert hasattr(s, "backend_label")
