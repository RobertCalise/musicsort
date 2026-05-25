"""Tests for `musicsort rekordbox …` subcommands.

`rekordbox_drain` and `RekordboxWriter` are patched to recorders in the
cli module so we never touch a real master.db.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Self
from unittest.mock import MagicMock

import pytest
from mutagen.id3 import TCON, TDRC
from mutagen.mp3 import MP3
from typer.testing import CliRunner

from musicsort.autoimport import cli_rekordbox
from musicsort.autoimport.cli import app
from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.rekordbox.drain import DrainReport
from musicsort.rekordbox.writer import ImportOutcome

runner = CliRunner()


class _FakeContent:
    def __init__(self, content_id: str) -> None:
        self.ID = content_id


class _FakePlaylist:
    def __init__(self, playlist_id: str) -> None:
        self.ID = playlist_id


class FakeWriter:
    """Recording substitute for RekordboxWriter used by the playlists tests."""

    def __init__(
        self,
        master_db: Path | None = None,
        playlist_parent: str = "musicsort playlists",
        genres_folder: str = "Genres",
        decades_folder: str = "Decades",
    ) -> None:
        self.master_db = master_db
        self.playlist_parent = playlist_parent
        self.genres_folder = genres_folder
        self.decades_folder = decades_folder
        self.known_paths: set[Path] | None = None
        self.import_calls: list[Path] = []
        self.genre_calls: list[tuple[str, str | None]] = []
        self.decade_calls: list[str] = []
        self.add_calls: list[tuple[str, str]] = []
        self.commit_count = 0
        self.rollback_count = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def import_track(self, library_path: Path) -> ImportOutcome:
        self.import_calls.append(library_path)
        return ImportOutcome.INSERTED_NEW

    def get_content_for_path(self, library_path: Path) -> _FakeContent | None:
        if self.known_paths is not None and library_path not in self.known_paths:
            return None
        return _FakeContent(str(library_path))

    def ensure_genre_playlist(self, genre_name: str, family: str | None) -> _FakePlaylist:
        self.genre_calls.append((genre_name, family))
        return _FakePlaylist(f"genre:{family or '_root'}/{genre_name}")

    def ensure_decade_playlist(self, decade_label: str) -> _FakePlaylist:
        self.decade_calls.append(decade_label)
        return _FakePlaylist(f"decade:{decade_label}")

    def add_track_to_playlist(self, content: _FakeContent, playlist: _FakePlaylist) -> bool:
        self.add_calls.append((content.ID, playlist.ID))
        return True

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


@pytest.fixture
def drain_recorder(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(return_value=DrainReport(attempted=0, queue_remaining=0))
    monkeypatch.setattr(cli_rekordbox, "rekordbox_drain", mock)
    return mock


@pytest.fixture
def empty_cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Like cli_env but with a fresh-empty library (no pre-shelved tracks)."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "AutoImport").mkdir()
    monkeypatch.setenv("MUSICSORT_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUSICSORT_AUTOIMPORT_FOLDER", str(library / "AutoImport"))
    monkeypatch.setenv("MUSICSORT_SONGS_DIR", str(library / "Songs"))
    monkeypatch.setenv("MUSICSORT_QUARANTINE_DIR", str(library / "_Unsorted"))
    monkeypatch.setenv(
        "MUSICSORT_FINGERPRINT_DB_PATH",
        str(library / ".musicsort" / "fingerprints.db"),
    )
    monkeypatch.setenv("MUSICSORT_REKORDBOX_ENABLED", "false")
    return library


# ---- sync ---------------------------------------------------------------------


def test_sync_runs_drain_and_prints_report(cli_env: Path, drain_recorder: MagicMock) -> None:
    drain_recorder.return_value = DrainReport(attempted=2, inserted=2, queue_remaining=0)
    result = runner.invoke(app, ["rekordbox", "sync"])

    assert result.exit_code == 0, result.output
    assert "attempted: 2" in result.output
    drain_recorder.assert_called_once()


def test_sync_reset_errors_clears_failures(cli_env: Path, drain_recorder: MagicMock) -> None:
    fp_db = FingerprintDB(cli_env / ".musicsort" / "fingerprints.db")
    p = cli_env / "Songs" / "Pop" / "x.mp3"
    fp_db.enqueue_rekordbox(p, "Pop")
    fp_db.mark_rekordbox_failed(p, "stale error")
    fp_db.close()

    result = runner.invoke(app, ["rekordbox", "sync", "--reset-errors"])

    assert result.exit_code == 0, result.output
    assert "Cleared 1 sticky error" in result.output


# ---- backfill -----------------------------------------------------------------


def _seed_songs(library: Path, files: list[tuple[str, str]]) -> None:
    for genre, name in files:
        d = library / "Songs" / genre
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"fake-mp3-bytes")


def test_backfill_empty_library_short_circuits(
    empty_cli_env: Path, drain_recorder: MagicMock
) -> None:
    result = runner.invoke(app, ["rekordbox", "backfill"])
    assert result.exit_code == 0, result.output
    assert "No audio files found" in result.output
    drain_recorder.assert_not_called()


def test_backfill_enqueues_and_drains(empty_cli_env: Path, drain_recorder: MagicMock) -> None:
    _seed_songs(empty_cli_env, [("Pop", "a.mp3"), ("Pop", "b.mp3"), ("House", "c.mp3")])

    result = runner.invoke(app, ["rekordbox", "backfill"])

    assert result.exit_code == 0, result.output
    assert "will enqueue 3" in result.output
    drain_recorder.assert_called_once()

    fp_db = FingerprintDB(empty_cli_env / ".musicsort" / "fingerprints.db")
    pending = fp_db.pending_rekordbox()
    fp_db.close()
    assert {(r.library_path.name, r.genre) for r in pending} == {
        ("a.mp3", "Pop"),
        ("b.mp3", "Pop"),
        ("c.mp3", "House"),
    }


def test_backfill_dry_run_does_not_enqueue(empty_cli_env: Path, drain_recorder: MagicMock) -> None:
    _seed_songs(empty_cli_env, [("Pop", "a.mp3"), ("Pop", "b.mp3")])

    result = runner.invoke(app, ["rekordbox", "backfill", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "[dry-run] would enqueue" in result.output
    drain_recorder.assert_not_called()

    fp_db = FingerprintDB(empty_cli_env / ".musicsort" / "fingerprints.db")
    assert fp_db.pending_rekordbox() == []
    fp_db.close()


def test_backfill_limit_caps_enqueue(empty_cli_env: Path, drain_recorder: MagicMock) -> None:
    _seed_songs(empty_cli_env, [("Pop", f"t{i}.mp3") for i in range(5)])

    result = runner.invoke(app, ["rekordbox", "backfill", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert "will enqueue 2" in result.output

    fp_db = FingerprintDB(empty_cli_env / ".musicsort" / "fingerprints.db")
    assert len(fp_db.pending_rekordbox()) == 2
    fp_db.close()


def test_backfill_skips_already_queued(empty_cli_env: Path, drain_recorder: MagicMock) -> None:
    _seed_songs(empty_cli_env, [("Pop", "a.mp3"), ("Pop", "b.mp3")])

    fp_db = FingerprintDB(empty_cli_env / ".musicsort" / "fingerprints.db")
    fp_db.enqueue_rekordbox(empty_cli_env / "Songs" / "Pop" / "a.mp3", "Pop")
    fp_db.close()

    result = runner.invoke(app, ["rekordbox", "backfill"])

    assert result.exit_code == 0, result.output
    assert "will enqueue 1" in result.output


# ---- status -------------------------------------------------------------------


def test_status_empty_queue(cli_env: Path) -> None:
    result = runner.invoke(app, ["rekordbox", "status"])
    assert result.exit_code == 0, result.output
    assert "Queue size:" in result.output
    assert "Enabled:" in result.output
    assert "Recent failures" not in result.output


def test_status_lists_failures(cli_env: Path) -> None:
    fp_db = FingerprintDB(cli_env / ".musicsort" / "fingerprints.db")
    p = cli_env / "Songs" / "Pop" / "broken.mp3"
    fp_db.enqueue_rekordbox(p, "Pop")
    fp_db.mark_rekordbox_failed(p, "ValueError: invalid file type")
    fp_db.close()

    result = runner.invoke(app, ["rekordbox", "status"])

    assert result.exit_code == 0, result.output
    assert "Recent failures:" in result.output
    assert "broken.mp3" in result.output
    assert "ValueError: invalid file type" in result.output


# ---- playlists ---------------------------------------------------------------


@pytest.fixture
def playlists_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audio_fixtures: dict[str, Path],
) -> tuple[Path, list[Path]]:
    """Empty library with two real tagged audio files routed into Songs/."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "AutoImport").mkdir()
    songs = library / "Songs"
    (songs / "House").mkdir(parents=True)
    (songs / "Pop_80s_90s").mkdir(parents=True)

    house = songs / "House" / "track_a.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], house)
    m = MP3(house)
    if m.tags is None:
        m.add_tags()
    m.tags.add(TCON(encoding=3, text="House"))
    m.tags.add(TDRC(encoding=3, text="2010"))
    m.save()

    pop = songs / "Pop_80s_90s" / "track_b.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], pop)
    m = MP3(pop)
    if m.tags is None:
        m.add_tags()
    m.tags.add(TCON(encoding=3, text="Pop"))
    m.tags.add(TDRC(encoding=3, text="1985"))
    m.save()

    monkeypatch.setenv("MUSICSORT_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUSICSORT_AUTOIMPORT_FOLDER", str(library / "AutoImport"))
    monkeypatch.setenv("MUSICSORT_SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSICSORT_QUARANTINE_DIR", str(library / "_Unsorted"))
    monkeypatch.setenv(
        "MUSICSORT_FINGERPRINT_DB_PATH",
        str(library / ".musicsort" / "fingerprints.db"),
    )
    monkeypatch.setenv(
        "MUSICSORT_REKORDBOX_MASTER_DB_PATH",
        str(library / "fake-master.db"),
    )
    monkeypatch.setenv(
        "MUSICSORT_REKORDBOX_BACKUP_DIR",
        str(library / ".musicsort" / "rekordbox_backups"),
    )
    return library, [house, pop]


@pytest.fixture
def playlists_patches(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FakeWriter, MagicMock]:
    fake = FakeWriter()
    monkeypatch.setattr(cli_rekordbox, "RekordboxWriter", lambda **kwargs: fake)
    monkeypatch.setattr(cli_rekordbox, "rekordbox_running", lambda: False)
    monkeypatch.setattr(cli_rekordbox, "backup_master_db", MagicMock(return_value=Path("/tmp/x")))
    monkeypatch.setattr(cli_rekordbox, "prune_old_backups", MagicMock(return_value=0))
    return fake, MagicMock()


def test_playlists_no_flags_noop(empty_cli_env: Path) -> None:
    result = runner.invoke(app, ["rekordbox", "playlists"])
    assert result.exit_code == 0
    assert "Nothing to do" in result.output


def test_playlists_empty_songs_dir_exits(
    empty_cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSICSORT_REKORDBOX_ENABLED", "true")
    monkeypatch.setattr(cli_rekordbox, "rekordbox_running", lambda: False)
    result = runner.invoke(app, ["rekordbox", "playlists", "--genres"])
    assert result.exit_code == 0
    assert "No audio files" in result.output


def test_playlists_rekordbox_open_exits_nonzero(
    empty_cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSICSORT_REKORDBOX_ENABLED", "true")
    monkeypatch.setattr(cli_rekordbox, "rekordbox_running", lambda: True)
    (empty_cli_env / "Songs" / "Rock").mkdir(parents=True)
    (empty_cli_env / "Songs" / "Rock" / "x.mp3").write_bytes(b"x")
    result = runner.invoke(app, ["rekordbox", "playlists", "--genres"])
    assert result.exit_code == 1
    assert "Rekordbox is open" in result.output


def test_playlists_disabled_exits_silent(
    empty_cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSICSORT_REKORDBOX_ENABLED", "false")
    result = runner.invoke(app, ["rekordbox", "playlists", "--genres"])
    assert result.exit_code == 0
    assert "disabled" in result.output


def test_playlists_genres_routes_to_correct_playlists(
    playlists_env: tuple[Path, list[Path]],
    playlists_patches: tuple[FakeWriter, MagicMock],
) -> None:
    fake, _ = playlists_patches
    result = runner.invoke(app, ["rekordbox", "playlists", "--genres"])

    assert result.exit_code == 0, result.output
    assert "Scanned: 2" in result.output
    genre_names = set(fake.genre_calls)
    assert ("House", "House") in genre_names
    assert ("Pop (80s/90s)", None) in genre_names
    assert fake.commit_count == 1


def test_playlists_decades_buckets_correctly(
    playlists_env: tuple[Path, list[Path]],
    playlists_patches: tuple[FakeWriter, MagicMock],
) -> None:
    fake, _ = playlists_patches
    result = runner.invoke(app, ["rekordbox", "playlists", "--decades"])

    assert result.exit_code == 0, result.output
    assert "2010s" in fake.decade_calls
    assert "80s" in fake.decade_calls
    assert fake.commit_count == 1


def test_playlists_dry_run_makes_no_writes(
    playlists_env: tuple[Path, list[Path]],
    playlists_patches: tuple[FakeWriter, MagicMock],
) -> None:
    fake, _ = playlists_patches
    result = runner.invoke(app, ["rekordbox", "playlists", "--genres", "--decades", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output
    assert fake.commit_count == 0
    assert fake.add_calls == []
    assert fake.genre_calls == []
    assert fake.decade_calls == []


def test_playlists_skips_files_not_in_collection(
    playlists_env: tuple[Path, list[Path]],
    playlists_patches: tuple[FakeWriter, MagicMock],
) -> None:
    fake, _ = playlists_patches
    fake.known_paths = set()

    result = runner.invoke(app, ["rekordbox", "playlists", "--genres"])

    assert result.exit_code == 0, result.output
    assert "not in Rekordbox collection: 2" in result.output
    assert fake.genre_calls == []
