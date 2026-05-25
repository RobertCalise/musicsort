"""Tests for Settings — defaults + derive-from-library_root behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from musicsort.config import Settings


def test_default_library_root_is_music_library() -> None:
    s = Settings()
    assert s.library_root == Path.home() / "Music" / "Library"


def test_subpaths_default_under_library_root() -> None:
    s = Settings()
    root = Path.home() / "Music" / "Library"
    assert s.songs_dir == root / "Songs"
    assert s.quarantine_dir == root / "_Unsorted"
    assert s.fingerprint_db_path == root / ".musicsort" / "fingerprints.db"


def test_autoimport_folder_independent_of_library_root() -> None:
    s = Settings()
    # AutoImport is a sibling of Library by convention, NOT a subdir.
    assert s.autoimport_folder == Path.home() / "Music" / "AutoImport"


def test_overriding_library_root_derives_subpaths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSICSORT_LIBRARY_ROOT", "/tmp/MyLib")
    s = Settings()
    assert s.library_root == Path("/tmp/MyLib")
    assert s.songs_dir == Path("/tmp/MyLib/Songs")
    assert s.quarantine_dir == Path("/tmp/MyLib/_Unsorted")
    assert s.fingerprint_db_path == Path("/tmp/MyLib/.musicsort/fingerprints.db")
    # AutoImport unaffected
    assert s.autoimport_folder == Path.home() / "Music" / "AutoImport"


def test_explicit_subpath_override_wins_over_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting LIBRARY_ROOT and SONGS_DIR independently — explicit wins."""
    monkeypatch.setenv("MUSICSORT_LIBRARY_ROOT", "/tmp/MyLib")
    monkeypatch.setenv("MUSICSORT_SONGS_DIR", "/tmp/Different/Songs")
    s = Settings()
    assert s.songs_dir == Path("/tmp/Different/Songs")
    # The others still derive from library_root
    assert s.quarantine_dir == Path("/tmp/MyLib/_Unsorted")
    assert s.fingerprint_db_path == Path("/tmp/MyLib/.musicsort/fingerprints.db")


def test_explicit_construction_with_kwargs_also_derives() -> None:
    """Programmatic construction with library_root only should also derive."""
    s = Settings(library_root=Path("/tmp/MyLib"))
    assert s.songs_dir == Path("/tmp/MyLib/Songs")
    assert s.quarantine_dir == Path("/tmp/MyLib/_Unsorted")
