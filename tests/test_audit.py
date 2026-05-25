"""Tests for the library audit pass."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from mutagen.id3 import TCON, TDRC
from mutagen.mp3 import MP3

from musicsort.autoimport.audit import AuditIssueKind, audit_library
from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.autoimport.taxonomy import load_taxonomy
from musicsort.config import Settings, get_settings


def _settings(library_root: Path) -> Settings:
    return Settings(
        library_root=library_root,
        autoimport_folder=library_root / "AutoImport",
        songs_dir=library_root / "Songs",
        quarantine_dir=library_root / "_Unsorted",
        fingerprint_db_path=library_root / ".musicsort" / "fingerprints.db",
    )


@pytest.fixture
def audit_setup(populated_library: Path):
    settings = _settings(populated_library)
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    yield settings, taxonomy, db
    db.close()


def _retag_genre(path: Path, genre: str, year: int | None = 2010) -> None:
    m = MP3(path)
    if m.tags is None:
        m.add_tags()
    m.tags.delall("TCON")
    m.tags.add(TCON(encoding=3, text=genre))
    if year is not None:
        m.tags.delall("TDRC")
        m.tags.add(TDRC(encoding=3, text=str(year)))
    m.save()


def test_empty_library_no_issues(tmp_path: Path) -> None:
    library = tmp_path / "library"
    (library / "Songs").mkdir(parents=True)
    settings = _settings(library)
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    try:
        report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
        assert report.files_scanned == 0
        assert report.issues == ()
    finally:
        db.close()


def test_well_placed_files_emit_no_issues(audit_setup) -> None:
    settings, taxonomy, db = audit_setup
    # populated_library has House/existing_house.mp3 (House, year 2009) and
    # Techno/existing_techno.wav (EDM). The WAV's "EDM" genre tag now routes
    # to Mainstage (Beatport convention), but it's sitting in Techno/ — fix
    # that first so the test reflects "everything in the right place."
    _retag_genre(settings.songs_dir / "House" / "existing_house.mp3", "House")

    # Move the EDM wav to its correct folder for this test.
    mainstage_dir = settings.songs_dir / "Mainstage"
    mainstage_dir.mkdir(exist_ok=True)
    shutil.move(
        str(settings.songs_dir / "Techno" / "existing_techno.wav"),
        str(mainstage_dir / "existing_edm.wav"),
    )

    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    assert report.issues == ()


def test_mis_shelved_file_detected(audit_setup, audio_fixtures: dict[str, Path]) -> None:
    settings, taxonomy, db = audit_setup
    # Drop a House-tagged file into Techno/.
    misshelved = settings.songs_dir / "Techno" / "wrong_folder.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], misshelved)
    _retag_genre(misshelved, "House")

    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    misshelved_issues = [
        i for i in report.issues if i.kind is AuditIssueKind.MIS_SHELVED and misshelved in i.paths
    ]
    assert len(misshelved_issues) == 1
    assert "House" in misshelved_issues[0].detail


def test_no_genre_file_detected(audit_setup, audio_fixtures: dict[str, Path]) -> None:
    settings, taxonomy, db = audit_setup
    untagged = settings.songs_dir / "House" / "untagged.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], untagged)

    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    no_genre = [i for i in report.issues if i.kind is AuditIssueKind.NO_GENRE]
    assert any(untagged in i.paths for i in no_genre)


def test_unknown_genre_detected(audit_setup, audio_fixtures: dict[str, Path]) -> None:
    settings, taxonomy, db = audit_setup
    polka = settings.songs_dir / "House" / "polka.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], polka)
    _retag_genre(polka, "Polka")

    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    unknown = [i for i in report.issues if i.kind is AuditIssueKind.UNKNOWN_GENRE]
    assert any(polka in i.paths for i in unknown)


def test_pop_without_year_is_ambiguous(audit_setup, audio_fixtures: dict[str, Path]) -> None:
    settings, taxonomy, db = audit_setup
    pop = settings.songs_dir / "House" / "pop.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], pop)
    _retag_genre(pop, "Pop", year=None)  # no year → year-gated quarantine

    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    ambiguous = [i for i in report.issues if i.kind is AuditIssueKind.AMBIGUOUS]
    assert any(pop in i.paths for i in ambiguous)


def test_manual_only_genre_in_genre_folder(audit_setup, audio_fixtures: dict[str, Path]) -> None:
    settings, taxonomy, db = audit_setup
    vocal = settings.songs_dir / "House" / "vocal.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], vocal)
    _retag_genre(vocal, "Acapella")

    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    manual = [i for i in report.issues if i.kind is AuditIssueKind.MANUAL_ONLY]
    assert any(vocal in i.paths for i in manual)


def test_sha256_duplicate_within_library_detected(
    audit_setup, audio_fixtures: dict[str, Path]
) -> None:
    settings, taxonomy, db = audit_setup
    # Drop a byte-identical copy of the existing House track into Techno/.
    dup_target = settings.songs_dir / "Techno" / "house_copy.mp3"
    shutil.copy(audio_fixtures["mp3_tagged"], dup_target)

    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    duplicates = [i for i in report.issues if i.kind is AuditIssueKind.DUPLICATE]
    assert len(duplicates) == 1
    assert {p.name for p in duplicates[0].paths} == {"existing_house.mp3", "house_copy.mp3"}


def test_near_duplicate_via_cross_bitrate_encoding(audit_setup, encode_mp3) -> None:
    settings, taxonomy, db = audit_setup
    techno_dir = settings.songs_dir / "Techno"
    # Two encodings of the same seeded pink noise → chromaprint similarity should
    # be ≥ threshold but bytes differ, so NEAR_DUPLICATE not DUPLICATE.
    encode_mp3(techno_dir / "low.mp3", bitrate_k=64, genre="Techno")
    encode_mp3(techno_dir / "high.mp3", bitrate_k=320, genre="Techno")

    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    near = [i for i in report.issues if i.kind is AuditIssueKind.NEAR_DUPLICATE]
    assert len(near) == 1
    assert {p.name for p in near[0].paths} == {"low.mp3", "high.mp3"}


def test_unrelated_files_with_different_durations_no_near_duplicate(
    audit_setup, audio_fixtures: dict[str, Path]
) -> None:
    """Distinct random pink noise files of similar duration may still fingerprint
    differently — we just confirm the audit doesn't flag unrelated pairs."""
    settings, taxonomy, db = audit_setup
    # Both populated_library fixtures are independent random pink noise →
    # similarity should be well below 0.95.
    report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
    near = [i for i in report.issues if i.kind is AuditIssueKind.NEAR_DUPLICATE]
    assert near == []
