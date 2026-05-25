"""Tests for the SQLite fingerprint cache."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from musicsort.autoimport.fingerprint_db import (
    SCHEMA_VERSION,
    FingerprintDB,
    FingerprintRow,
)


def _row(path: Path, sha: str = "abc", fp: str = "FPSTR") -> FingerprintRow:
    stat = path.stat()
    return FingerprintRow(
        path=path,
        sha256=sha,
        fingerprint=fp,
        duration_seconds=180.0,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        indexed_at=datetime.now(UTC).isoformat(),
    )


def test_schema_and_user_version(tmp_path: Path) -> None:
    db_path = tmp_path / "fp.db"
    FingerprintDB(db_path).close()
    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "fingerprints" in tables


def test_upsert_and_lookup_by_path(tmp_path: Path) -> None:
    f = tmp_path / "x.mp3"
    f.write_bytes(b"hello")
    db = FingerprintDB(tmp_path / "fp.db")
    row = _row(f, sha="hash1")
    db.upsert(row)
    fetched = db.lookup_by_path(f)
    assert fetched is not None
    assert fetched.sha256 == "hash1"
    assert fetched.path == f


def test_lookup_by_sha256_returns_all_matches(tmp_path: Path) -> None:
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    db = FingerprintDB(tmp_path / "fp.db")
    db.upsert(_row(a, sha="shared"))
    db.upsert(_row(b, sha="shared"))
    rows = db.lookup_by_sha256("shared")
    paths = {r.path for r in rows}
    assert paths == {a, b}


def test_upsert_updates_existing(tmp_path: Path) -> None:
    f = tmp_path / "x.mp3"
    f.write_bytes(b"hello")
    db = FingerprintDB(tmp_path / "fp.db")
    db.upsert(_row(f, sha="old"))
    db.upsert(_row(f, sha="new"))
    fetched = db.lookup_by_path(f)
    assert fetched is not None
    assert fetched.sha256 == "new"
    assert len(db.lookup_by_sha256("old")) == 0


def test_delete_removes_row(tmp_path: Path) -> None:
    f = tmp_path / "x.mp3"
    f.write_bytes(b"hello")
    db = FingerprintDB(tmp_path / "fp.db")
    db.upsert(_row(f))
    db.delete(f)
    assert db.lookup_by_path(f) is None


def test_ensure_indexed_first_call_computes_and_stores(
    tmp_path: Path, audio_fixtures: dict[str, Path]
) -> None:
    db = FingerprintDB(tmp_path / "fp.db")
    f = audio_fixtures["mp3_tagged"]
    row = db.ensure_indexed(f)
    assert row.sha256
    assert row.fingerprint
    assert db.lookup_by_path(f) is not None


def test_ensure_indexed_cached_avoids_recomputation(
    tmp_path: Path, audio_fixtures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    db = FingerprintDB(tmp_path / "fp.db")
    f = audio_fixtures["mp3_tagged"]
    first = db.ensure_indexed(f)

    sentinel_called = {"hasher": 0, "fingerprinter": 0}

    def fake_hasher(p: Path) -> str:
        sentinel_called["hasher"] += 1
        return "should-not-be-called"

    def fake_fingerprinter(p: Path):
        sentinel_called["fingerprinter"] += 1
        raise RuntimeError("should not be called")

    monkeypatch.setattr("musicsort.autoimport.fingerprint_db.sha256_file", fake_hasher)
    monkeypatch.setattr("musicsort.autoimport.fingerprint_db.fingerprint_file", fake_fingerprinter)

    second = db.ensure_indexed(f)
    assert second.sha256 == first.sha256
    assert sentinel_called == {"hasher": 0, "fingerprinter": 0}


def test_ensure_indexed_reindexes_when_mtime_changes(
    tmp_path: Path, audio_fixtures: dict[str, Path]
) -> None:
    db = FingerprintDB(tmp_path / "fp.db")
    # Copy fixture so we can mutate mtime without disturbing the session fixture.
    f = tmp_path / "tagged.mp3"
    f.write_bytes(audio_fixtures["mp3_tagged"].read_bytes())
    first = db.ensure_indexed(f)

    time.sleep(0.01)
    # Bump mtime by re-touching.
    now = time.time()
    os.utime(f, (now, now + 60))
    second = db.ensure_indexed(f)
    assert second.mtime_ns != first.mtime_ns


def test_all_rows_iterates_every_entry(tmp_path: Path) -> None:
    db = FingerprintDB(tmp_path / "fp.db")
    paths = []
    for i in range(3):
        f = tmp_path / f"f{i}.mp3"
        f.write_bytes(b"x" * (i + 1))
        db.upsert(_row(f, sha=f"sha{i}"))
        paths.append(f)
    rows = list(db.all_rows())
    assert {r.path for r in rows} == set(paths)
