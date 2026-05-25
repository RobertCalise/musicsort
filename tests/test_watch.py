"""Tests for the filesystem watcher."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from mutagen.id3 import TCON, TDRC
from mutagen.mp3 import MP3
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.autoimport.mover import MoveAction, MoveResult
from musicsort.autoimport.quarantine import Quarantiner
from musicsort.autoimport.taxonomy import load_taxonomy
from musicsort.autoimport.watch import (
    FileSettler,
    _Handler,
    cleanup_empty_ancestors,
    prune_empty_subdirs,
    watch,
)
from musicsort.config import Settings, get_settings

EXTENSIONS = (".mp3", ".wav", ".flac")


# ---- FileSettler unit tests --------------------------------------------------


def test_settler_ignores_non_audio_extension(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    settler.record_event(tmp_path / "notes.txt")
    assert settler._pending == {}


def test_settler_does_not_emit_before_settle(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    settler.record_event(p)
    assert settler.ready_paths(now=0.5) == []


def test_settler_emits_after_settle_interval(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    # Inject a known clock: record at t=0, query at t=2.
    settler._pending[p] = 0.0
    ready = settler.ready_paths(now=2.0)
    assert ready == [p]
    assert settler._pending == {}  # consumed


def test_settler_resets_clock_on_subsequent_event(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    settler._pending[p] = 0.0
    # New event at t=0.5 — reset.
    settler._pending[p] = 0.5
    # Query at t=1.0 — should NOT yet be ready (only 0.5 elapsed since last event).
    assert settler.ready_paths(now=1.0) == []
    # Query at t=1.6 — now ready.
    assert settler.ready_paths(now=1.6) == [p]


def test_settler_drops_vanished_paths(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    p = tmp_path / "ghost.mp3"  # never created on disk
    settler._pending[p] = 0.0
    assert settler.ready_paths(now=2.0) == []
    assert settler._pending == {}


# ---- _Handler unit tests -----------------------------------------------------


def test_handler_records_created_event(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    handler = _Handler(settler)
    p = tmp_path / "a.mp3"
    handler.on_created(FileCreatedEvent(str(p)))
    assert p in settler._pending


def test_handler_records_modified_event(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    handler = _Handler(settler)
    p = tmp_path / "a.mp3"
    handler.on_modified(FileModifiedEvent(str(p)))
    assert p in settler._pending


def test_handler_records_move_destination(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    handler = _Handler(settler)
    src = tmp_path / "old.mp3"
    dst = tmp_path / "new.mp3"
    handler.on_moved(FileMovedEvent(str(src), str(dst)))
    assert dst in settler._pending
    assert src not in settler._pending


def test_handler_ignores_directory_events(tmp_path: Path) -> None:
    settler = FileSettler(settle_seconds=1.0, audio_extensions=EXTENSIONS)
    handler = _Handler(settler)
    event = FileCreatedEvent(str(tmp_path / "subdir"))
    event.is_directory = True
    handler.on_created(event)
    assert settler._pending == {}


# ---- prune_empty_subdirs / cleanup_empty_ancestors ---------------------------


def test_prune_empty_subdirs_removes_empty_leaves(tmp_path: Path) -> None:
    root = tmp_path / "AutoImport"
    (root / "PackA").mkdir(parents=True)
    (root / "PackB" / "Inner").mkdir(parents=True)
    prune_empty_subdirs(root)
    assert root.is_dir()  # root preserved
    assert not (root / "PackA").exists()
    assert not (root / "PackB").exists()


def test_prune_empty_subdirs_keeps_dirs_with_content(tmp_path: Path) -> None:
    root = tmp_path / "AutoImport"
    keeper = root / "PackA"
    keeper.mkdir(parents=True)
    (keeper / "track.mp3").write_bytes(b"x")
    (root / "PackB").mkdir()  # this one empty, should go
    prune_empty_subdirs(root)
    assert keeper.is_dir()
    assert (keeper / "track.mp3").exists()
    assert not (root / "PackB").exists()


def test_prune_empty_subdirs_removes_os_junk_along_the_way(tmp_path: Path) -> None:
    root = tmp_path / "AutoImport"
    pack = root / "Pack"
    pack.mkdir(parents=True)
    (pack / ".DS_Store").write_bytes(b"\x00")
    prune_empty_subdirs(root)
    assert not pack.exists()


def test_cleanup_empty_ancestors_walks_up(tmp_path: Path) -> None:
    root = tmp_path / "AutoImport"
    deep = root / "A" / "B" / "C"
    deep.mkdir(parents=True)
    cleanup_empty_ancestors(deep, stop_at=root)
    assert root.is_dir()
    assert not (root / "A").exists()


def test_cleanup_empty_ancestors_preserves_stop_at(tmp_path: Path) -> None:
    root = tmp_path / "AutoImport"
    root.mkdir()
    cleanup_empty_ancestors(root, stop_at=root)
    assert root.is_dir()


# ---- Integration test (real Observer, short settle) --------------------------


def test_watch_routes_settled_file_end_to_end(
    populated_library: Path, audio_fixtures: dict[str, Path]
) -> None:
    """Spin up a real watcher in a background thread; drop a tagged file in;
    confirm it's routed to Songs/<Genre>/ within a few seconds."""
    settings = Settings(
        library_root=populated_library,
        autoimport_folder=populated_library / "AutoImport",
        songs_dir=populated_library / "Songs",
        quarantine_dir=populated_library / "_Unsorted",
        fingerprint_db_path=populated_library / ".musicsort" / "fingerprints.db",
    )
    settings.autoimport_folder.mkdir(parents=True, exist_ok=True)
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    quarantiner = Quarantiner(settings.quarantine_dir)

    results: list[MoveResult] = []
    stop = threading.Event()

    thread = threading.Thread(
        target=watch,
        kwargs={
            "settings": settings,
            "taxonomy": taxonomy,
            "db": db,
            "quarantiner": quarantiner,
            "settle_seconds": 0.5,
            "poll_seconds": 0.1,
            "on_result": results.append,
            "stop_event": stop,
        },
        daemon=True,
    )
    thread.start()

    try:
        # Give the observer a moment to start listening.
        time.sleep(0.2)
        # Drop a House-tagged file into AutoImport.
        src = settings.autoimport_folder / "new_arrival.mp3"
        shutil.copy(audio_fixtures["mp3_empty"], src)
        m = MP3(src)
        if m.tags is None:
            m.add_tags()
        m.tags.add(TCON(encoding=3, text="House"))
        m.tags.add(TDRC(encoding=3, text="2010"))
        m.save()

        # Wait up to ~5 seconds for the watcher to settle and route it.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not results:
            time.sleep(0.1)

        assert len(results) >= 1, "watcher did not produce any results"
        moved = next(r for r in results if r.src == src)
        assert moved.action is MoveAction.MOVED
        assert moved.dst is not None
        assert moved.dst.parent == settings.songs_dir / "House"
        assert moved.dst.exists()
    finally:
        stop.set()
        thread.join(timeout=5.0)
        db.close()
