"""Tests for the Rekordbox-import queue extension to FingerprintDB."""

from __future__ import annotations

from pathlib import Path

import pytest

from musicsort.autoimport.fingerprint_db import FingerprintDB, QueueRow


@pytest.fixture
def db(tmp_path: Path) -> FingerprintDB:
    return FingerprintDB(tmp_path / "fp.db")


def _enqueue(db: FingerprintDB, name: str, genre: str = "House") -> Path:
    p = Path(f"/Library/Songs/{genre}/{name}")
    db.enqueue_rekordbox(p, genre)
    return p


def test_enqueue_creates_pending_row(db: FingerprintDB) -> None:
    p = _enqueue(db, "track-a.mp3", "Pop")
    pending = db.pending_rekordbox()
    assert len(pending) == 1
    row = pending[0]
    assert row.library_path == p
    assert row.genre == "Pop"
    assert row.last_error is None
    assert row.attempts == 0


def test_enqueue_is_idempotent(db: FingerprintDB) -> None:
    _enqueue(db, "track-a.mp3")
    _enqueue(db, "track-a.mp3")
    assert db.rekordbox_queue_size() == 1


def test_pending_orders_by_queued_at(db: FingerprintDB) -> None:
    a = _enqueue(db, "a.mp3")
    b = _enqueue(db, "b.mp3")
    c = _enqueue(db, "c.mp3")
    pending = db.pending_rekordbox()
    assert [r.library_path for r in pending] == [a, b, c]


def test_pending_respects_limit(db: FingerprintDB) -> None:
    for i in range(5):
        _enqueue(db, f"t{i}.mp3")
    pending = db.pending_rekordbox(limit=2)
    assert len(pending) == 2


def test_mark_done_removes_row(db: FingerprintDB) -> None:
    p = _enqueue(db, "track.mp3")
    db.mark_rekordbox_done(p)
    assert db.pending_rekordbox() == []
    assert db.rekordbox_queue_size() == 0


def test_mark_failed_marks_row_and_excludes_from_pending(db: FingerprintDB) -> None:
    p = _enqueue(db, "track.mp3")
    db.mark_rekordbox_failed(p, "FileNotFoundError: gone")

    assert db.pending_rekordbox() == []
    failed_rows: list[QueueRow] = db.failed_rekordbox()
    assert len(failed_rows) == 1
    assert failed_rows[0].last_error == "FileNotFoundError: gone"
    assert failed_rows[0].attempts == 1


def test_mark_failed_increments_attempts(db: FingerprintDB) -> None:
    p = _enqueue(db, "track.mp3")
    db.mark_rekordbox_failed(p, "err 1")
    db.mark_rekordbox_failed(p, "err 2")
    failed = db.failed_rekordbox()
    assert failed[0].attempts == 2
    assert failed[0].last_error == "err 2"


def test_retry_failed_includes_failed_rows(db: FingerprintDB) -> None:
    p = _enqueue(db, "track.mp3")
    db.mark_rekordbox_failed(p, "boom")
    assert db.pending_rekordbox(retry_failed=False) == []
    retried = db.pending_rekordbox(retry_failed=True)
    assert len(retried) == 1


def test_clear_errors_resets_failure(db: FingerprintDB) -> None:
    p1 = _enqueue(db, "a.mp3")
    p2 = _enqueue(db, "b.mp3")
    db.mark_rekordbox_failed(p1, "err")
    db.mark_rekordbox_failed(p2, "err")

    cleared = db.clear_rekordbox_errors()
    assert cleared == 2
    pending = db.pending_rekordbox()
    assert {r.library_path for r in pending} == {p1, p2}


def test_queue_size_include_vs_exclude_failed(db: FingerprintDB) -> None:
    p1 = _enqueue(db, "a.mp3")
    _enqueue(db, "b.mp3")
    db.mark_rekordbox_failed(p1, "err")
    assert db.rekordbox_queue_size(include_failed=True) == 2
    assert db.rekordbox_queue_size(include_failed=False) == 1
