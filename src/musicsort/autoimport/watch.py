"""Filesystem watcher for AutoImport.

A Finder copy of a large WAV produces a `created` event with zero bytes followed
by a stream of `modified` events as bytes arrive — processing on `created` would
feed a half-written file to the reader. The `FileSettler` debounces events so a
path only becomes "ready" after N seconds of no further events.

watchdog's `Observer` runs in its own thread; this module's main loop polls the
settler and calls `move_one` serially. That keeps the worker single-threaded
(no need to lock `db` or `quarantiner`).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from pathlib import Path
from time import monotonic

from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.autoimport.mover import MoveAction, MoveResult, move_one
from musicsort.autoimport.quarantine import Quarantiner
from musicsort.autoimport.taxonomy import Taxonomy
from musicsort.config import Settings

_OS_JUNK_FILENAMES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db", ".localized"})


def cleanup_empty_ancestors(start: Path, stop_at: Path) -> None:
    """Walk up from `start`, removing dirs that are empty (or contain only
    OS-generated junk files like .DS_Store). Stops at (and never removes)
    `stop_at`. Used to clean up pack subfolders in AutoImport after their
    contents route, so the user doesn't accumulate empty dirs."""
    if start == stop_at or not start.is_relative_to(stop_at):
        return
    current = start
    while current != stop_at and current.is_relative_to(stop_at):
        try:
            entries = list(current.iterdir())
        except FileNotFoundError:
            return
        real = [e for e in entries if e.name not in _OS_JUNK_FILENAMES]
        if real:
            return
        for junk in entries:
            try:
                junk.unlink()
            except OSError:
                return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def prune_empty_subdirs(root: Path) -> None:
    """Recursively remove empty subdirs under `root`; `root` itself is preserved.

    OS-generated junk (.DS_Store etc.) is deleted along the way so it doesn't
    keep a dir non-empty. Bottom-up so nested empty trees collapse fully.

    Called by `watch()` at startup to clean up leftover empty pack subfolders
    from prior runs, and shareable with any other CLI that needs the same
    sweep."""
    if not root.is_dir():
        return
    # Sort descending by depth so child dirs are processed before their parents.
    subdirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: -len(p.parts),
    )
    for subdir in subdirs:
        try:
            entries = list(subdir.iterdir())
        except FileNotFoundError:
            continue
        real = [e for e in entries if e.name not in _OS_JUNK_FILENAMES]
        if real:
            continue
        for junk in entries:
            with contextlib.suppress(OSError):
                junk.unlink()
        with contextlib.suppress(OSError):
            subdir.rmdir()


class FileSettler:
    """Debounce filesystem events. Paths become ready after `settle_seconds`
    of no further events; emitted at most once per quiet period."""

    def __init__(self, settle_seconds: float, audio_extensions: tuple[str, ...]) -> None:
        self.settle_seconds = settle_seconds
        self.audio_extensions = audio_extensions
        self._pending: dict[Path, float] = {}
        self._lock = threading.Lock()

    def record_event(self, path: Path) -> None:
        if path.suffix.lower() not in self.audio_extensions:
            return
        with self._lock:
            self._pending[path] = monotonic()

    def ready_paths(self, now: float | None = None) -> list[Path]:
        clock = now if now is not None else monotonic()
        ready: list[Path] = []
        with self._lock:
            for path, last_seen in list(self._pending.items()):
                if not path.exists():
                    del self._pending[path]
                    continue
                if clock - last_seen >= self.settle_seconds:
                    ready.append(path)
                    del self._pending[path]
        return ready


class _Handler(FileSystemEventHandler):
    """Forward watchdog file events into a FileSettler."""

    def __init__(self, settler: FileSettler) -> None:
        self.settler = settler

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self.settler.record_event(Path(event.src_path))

    def on_modified(self, event: FileModifiedEvent) -> None:
        if not event.is_directory:
            self.settler.record_event(Path(event.src_path))

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            self.settler.record_event(Path(event.dest_path))


def watch(
    *,
    settings: Settings,
    taxonomy: Taxonomy,
    db: FingerprintDB,
    quarantiner: Quarantiner,
    settle_seconds: float | None = None,
    poll_seconds: float | None = None,
    on_result: Callable[[MoveResult], None] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Watch settings.autoimport_folder and route every settled file via move_one.

    Blocks until KeyboardInterrupt or `stop_event.set()`. `on_result` (if
    provided) is called once per processed file with the MoveResult — used by
    the CLI to print progress and by tests to assert outcomes.
    """
    settle = settle_seconds if settle_seconds is not None else settings.watch_settle_seconds
    poll = poll_seconds if poll_seconds is not None else settings.watch_poll_seconds
    stop = stop_event if stop_event is not None else threading.Event()

    autoimport = settings.autoimport_folder
    autoimport.mkdir(parents=True, exist_ok=True)

    settler = FileSettler(settle, settings.audio_extensions)
    handler = _Handler(settler)
    observer = Observer()
    observer.schedule(handler, str(autoimport), recursive=True)
    observer.start()

    for path in _audio_files(autoimport, settings.audio_extensions):
        settler.record_event(path)

    # Prune empty pack subfolders left over from previous sessions, so the
    # user doesn't accumulate dead dirs across watcher restarts.
    prune_empty_subdirs(autoimport)

    try:
        while not stop.is_set():
            for path in settler.ready_paths():
                try:
                    result = move_one(
                        path,
                        settings=settings,
                        taxonomy=taxonomy,
                        db=db,
                        quarantiner=quarantiner,
                    )
                except Exception as exc:
                    if on_result is not None:
                        on_result(_failure_result(path, exc))
                    continue
                if on_result is not None:
                    on_result(result)
            stop.wait(poll)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join(timeout=5.0)


def _audio_files(folder: Path, extensions: tuple[str, ...]) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in extensions]


def _failure_result(path: Path, exc: BaseException) -> MoveResult:
    """Wrap an unexpected move_one failure as a MoveResult so output formatting
    has one shape."""
    return MoveResult(
        src=path,
        dst=None,
        action=MoveAction.QUARANTINED,
        reason="error",
        detail=f"{type(exc).__name__}: {exc}",
    )
