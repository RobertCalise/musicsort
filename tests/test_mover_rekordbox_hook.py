"""Tests for the Rekordbox-queue enqueue hook in `move_one`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from mutagen.id3 import TCON, TDRC
from mutagen.mp3 import MP3

from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.autoimport.mover import MoveAction, move_one
from musicsort.autoimport.quarantine import Quarantiner
from musicsort.autoimport.taxonomy import load_taxonomy
from musicsort.config import Settings, get_settings


def _settings(library_root: Path, *, rekordbox_enabled: bool = True) -> Settings:
    return Settings(
        library_root=library_root,
        autoimport_folder=library_root / "AutoImport",
        songs_dir=library_root / "Songs",
        quarantine_dir=library_root / "_Unsorted",
        fingerprint_db_path=library_root / ".musicsort" / "fingerprints.db",
        rekordbox_enabled=rekordbox_enabled,
    )


@pytest.fixture
def setup(populated_library: Path):
    settings = _settings(populated_library)
    settings.autoimport_folder.mkdir(parents=True, exist_ok=True)
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    quarantiner = Quarantiner(settings.quarantine_dir)
    yield settings, taxonomy, db, quarantiner
    db.close()


def _drop(src: Path, autoimport: Path) -> Path:
    autoimport.mkdir(parents=True, exist_ok=True)
    dst = autoimport / src.name
    shutil.copy(src, dst)
    return dst


def _tag(path: Path, genre: str, year: int = 2010) -> None:
    m = MP3(path)
    if m.tags is None:
        m.add_tags()
    m.tags.add(TCON(encoding=3, text=genre))
    m.tags.add(TDRC(encoding=3, text=str(year)))
    m.save()


def test_successful_move_enqueues_with_genre(
    setup, audio_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    settings, taxonomy, db, quarantiner = setup
    src = tmp_path / "fresh.mp3"
    src.write_bytes(audio_fixtures["mp3_empty"].read_bytes())
    _tag(src, "House")
    incoming = _drop(src, settings.autoimport_folder)

    move_one(incoming, settings=settings, taxonomy=taxonomy, db=db, quarantiner=quarantiner)

    pending = db.pending_rekordbox()
    assert len(pending) == 1
    assert pending[0].library_path == settings.songs_dir / "House" / "fresh.mp3"
    assert pending[0].genre == "House"


def test_duplicate_quarantine_still_enqueues_existing_library_file(
    setup, audio_fixtures: dict[str, Path]
) -> None:
    settings, taxonomy, db, quarantiner = setup
    incoming = _drop(audio_fixtures["mp3_tagged"], settings.autoimport_folder)

    result = move_one(
        incoming, settings=settings, taxonomy=taxonomy, db=db, quarantiner=quarantiner
    )

    assert result.action is MoveAction.QUARANTINED
    assert result.reason == "duplicate"
    pending = db.pending_rekordbox()
    assert len(pending) == 1
    assert pending[0].library_path == settings.songs_dir / "House" / "existing_house.mp3"
    assert pending[0].genre == "House"


def test_naked_quarantine_does_not_enqueue(
    setup, audio_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    settings, taxonomy, db, quarantiner = setup
    src = tmp_path / "no_genre.mp3"
    src.write_bytes(audio_fixtures["mp3_empty"].read_bytes())
    incoming = _drop(src, settings.autoimport_folder)

    result = move_one(
        incoming, settings=settings, taxonomy=taxonomy, db=db, quarantiner=quarantiner
    )

    assert result.action is MoveAction.QUARANTINED
    assert result.library_target is None
    assert db.pending_rekordbox() == []


def test_rekordbox_disabled_skips_enqueue(
    populated_library: Path, audio_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    settings = _settings(populated_library, rekordbox_enabled=False)
    settings.autoimport_folder.mkdir(parents=True, exist_ok=True)
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    quarantiner = Quarantiner(settings.quarantine_dir)
    try:
        src = tmp_path / "fresh.mp3"
        src.write_bytes(audio_fixtures["mp3_empty"].read_bytes())
        _tag(src, "House")
        incoming = _drop(src, settings.autoimport_folder)

        result = move_one(
            incoming, settings=settings, taxonomy=taxonomy, db=db, quarantiner=quarantiner
        )

        assert result.action is MoveAction.MOVED
        assert db.pending_rekordbox() == []
    finally:
        db.close()
