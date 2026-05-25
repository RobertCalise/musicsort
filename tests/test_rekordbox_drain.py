"""Tests for the Rekordbox drain orchestrator.

The real RekordboxWriter requires an encrypted master.db, so these tests
substitute a recording fake that mimics the public surface (`import_track`,
`commit`, `rollback`, context manager). `backup_master_db` and
`prune_old_backups` are also patched to no-ops since their correctness is
covered separately.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self
from unittest.mock import MagicMock

import pytest

import musicsort.rekordbox.drain as drain_mod
from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.config import Settings
from musicsort.rekordbox.drain import drain
from musicsort.rekordbox.writer import ImportOutcome


class FakeWriter:
    """Programmable substitute for RekordboxWriter."""

    def __init__(self, master_db: Path | None) -> None:
        self.master_db = master_db
        self.outcomes: list[ImportOutcome | Exception] = []
        self.raise_on_commit: Exception | None = None
        self.import_calls: list[Path] = []
        self.commit_count = 0
        self.rollback_count = 0
        self._idx = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def import_track(self, library_path: Path) -> ImportOutcome:
        self.import_calls.append(library_path)
        outcome = (
            self.outcomes[self._idx]
            if self._idx < len(self.outcomes)
            else ImportOutcome.INSERTED_NEW
        )
        self._idx += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def commit(self) -> None:
        self.commit_count += 1
        if self.raise_on_commit is not None:
            raise self.raise_on_commit

    def rollback(self) -> None:
        self.rollback_count += 1


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        library_root=tmp_path,
        rekordbox_master_db_path=tmp_path / "fake-master.db",
        rekordbox_backup_dir=tmp_path / "backups",
    )


@pytest.fixture
def db(tmp_path: Path) -> FingerprintDB:
    return FingerprintDB(tmp_path / "fp.db")


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Patch external dependencies of drain() and return the captured mocks."""
    mocks = {
        "rekordbox_running": MagicMock(return_value=False),
        "backup_master_db": MagicMock(return_value=Path("/tmp/fake.tar.gz")),
        "prune_old_backups": MagicMock(return_value=0),
        "notifier": MagicMock(),
    }
    monkeypatch.setattr(drain_mod, "rekordbox_running", mocks["rekordbox_running"])
    monkeypatch.setattr(drain_mod, "backup_master_db", mocks["backup_master_db"])
    monkeypatch.setattr(drain_mod, "prune_old_backups", mocks["prune_old_backups"])
    return mocks


def _patch_writer(monkeypatch: pytest.MonkeyPatch, writer: FakeWriter) -> None:
    monkeypatch.setattr(drain_mod, "RekordboxWriter", lambda master_db: writer)


def test_disabled_short_circuits(settings: Settings, db: FingerprintDB) -> None:
    settings = settings.model_copy(update={"rekordbox_enabled": False})
    report = drain(settings=settings, db=db, notifier=lambda *a, **k: None)
    assert report.skipped_disabled is True
    assert report.attempted == 0


def test_empty_queue_returns_zero(
    settings: Settings, db: FingerprintDB, patched: dict[str, MagicMock]
) -> None:
    report = drain(settings=settings, db=db, notifier=patched["notifier"])
    assert report.attempted == 0
    assert report.queue_remaining == 0
    patched["backup_master_db"].assert_not_called()


def test_rekordbox_open_skips_and_notifies(
    settings: Settings, db: FingerprintDB, patched: dict[str, MagicMock]
) -> None:
    patched["rekordbox_running"].return_value = True
    db.enqueue_rekordbox(Path("/L/Songs/Pop/x.mp3"), "Pop")

    report = drain(settings=settings, db=db, notifier=patched["notifier"])

    assert report.skipped_rekordbox_open is True
    assert report.queue_remaining == 1
    patched["notifier"].assert_called_once()
    patched["backup_master_db"].assert_not_called()


def test_rekordbox_open_with_empty_queue_skips_silently(
    settings: Settings, db: FingerprintDB, patched: dict[str, MagicMock]
) -> None:
    patched["rekordbox_running"].return_value = True

    report = drain(settings=settings, db=db, notifier=patched["notifier"])

    assert report.skipped_rekordbox_open is True
    patched["notifier"].assert_not_called()


def test_successful_drain_inserts_and_marks_done(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    db: FingerprintDB,
    patched: dict[str, MagicMock],
) -> None:
    db.enqueue_rekordbox(Path("/L/Songs/Pop/a.mp3"), "Pop")
    db.enqueue_rekordbox(Path("/L/Songs/House/b.mp3"), "House")

    writer = FakeWriter(None)
    writer.outcomes = [ImportOutcome.INSERTED_NEW, ImportOutcome.INSERTED_NEW]
    _patch_writer(monkeypatch, writer)

    report = drain(settings=settings, db=db, notifier=patched["notifier"])

    assert report.inserted == 2
    assert report.already_present == 0
    assert report.failed == 0
    assert report.queue_remaining == 0
    assert writer.commit_count == 2
    patched["backup_master_db"].assert_called_once()


def test_already_present_counts_separately(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    db: FingerprintDB,
    patched: dict[str, MagicMock],
) -> None:
    db.enqueue_rekordbox(Path("/L/Songs/Pop/a.mp3"), "Pop")
    db.enqueue_rekordbox(Path("/L/Songs/Pop/b.mp3"), "Pop")

    writer = FakeWriter(None)
    writer.outcomes = [ImportOutcome.ALREADY_PRESENT, ImportOutcome.INSERTED_NEW]
    _patch_writer(monkeypatch, writer)

    report = drain(settings=settings, db=db, notifier=patched["notifier"])

    assert report.inserted == 1
    assert report.already_present == 1
    assert report.queue_remaining == 0


def test_per_track_failure_isolates(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    db: FingerprintDB,
    patched: dict[str, MagicMock],
) -> None:
    db.enqueue_rekordbox(Path("/L/Songs/Pop/a.mp3"), "Pop")
    db.enqueue_rekordbox(Path("/L/Songs/Pop/b.mp3"), "Pop")
    db.enqueue_rekordbox(Path("/L/Songs/Pop/c.mp3"), "Pop")

    writer = FakeWriter(None)
    writer.outcomes = [
        ImportOutcome.INSERTED_NEW,
        ValueError("invalid file type"),
        ImportOutcome.INSERTED_NEW,
    ]
    _patch_writer(monkeypatch, writer)

    report = drain(settings=settings, db=db, notifier=patched["notifier"])

    assert report.inserted == 2
    assert report.failed == 1
    assert report.queue_remaining == 0
    failed = db.failed_rekordbox()
    assert len(failed) == 1
    assert "ValueError" in failed[0].last_error
    assert writer.rollback_count == 1
    patched["notifier"].assert_called()


def test_rekordbox_opened_mid_drain_aborts(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    db: FingerprintDB,
    patched: dict[str, MagicMock],
) -> None:
    db.enqueue_rekordbox(Path("/L/Songs/Pop/a.mp3"), "Pop")
    db.enqueue_rekordbox(Path("/L/Songs/Pop/b.mp3"), "Pop")

    writer = FakeWriter(None)
    writer.outcomes = [ImportOutcome.INSERTED_NEW, ImportOutcome.INSERTED_NEW]
    writer.raise_on_commit = RuntimeError("Rekordbox is running")
    _patch_writer(monkeypatch, writer)

    report = drain(settings=settings, db=db, notifier=patched["notifier"])

    assert report.skipped_rekordbox_open is True
    assert report.inserted == 0
    assert db.rekordbox_queue_size(include_failed=False) == 2
    patched["notifier"].assert_called()


def test_batch_size_caps_drain(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    db: FingerprintDB,
    patched: dict[str, MagicMock],
) -> None:
    settings = settings.model_copy(update={"rekordbox_batch_size": 2})
    for i in range(5):
        db.enqueue_rekordbox(Path(f"/L/Songs/Pop/t{i}.mp3"), "Pop")

    writer = FakeWriter(None)
    _patch_writer(monkeypatch, writer)

    report = drain(settings=settings, db=db, notifier=patched["notifier"])

    assert report.attempted == 2
    assert report.queue_remaining == 3
