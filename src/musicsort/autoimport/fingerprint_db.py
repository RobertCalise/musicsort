"""SQLite cache of (sha256, chromaprint) keyed by absolute path, plus the
Rekordbox-import queue.

The mover queries this DB on every incoming file to find layer-1 (byte-identical)
and layer-2 (chromaprint-similar) duplicates without rehashing/refingerprinting
the entire library each time.

The same file also carries a `rekordbox_queue` table that the watcher
populates after each successful route — the Rekordbox drain pulls from
this queue when Rekordbox is closed.

Schema is version-tracked via PRAGMA user_version.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from musicsort.autoimport.fingerprinter import deserialize, fingerprint_file, serialize
from musicsort.autoimport.hasher import sha256_file

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fingerprints (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sha256 ON fingerprints(sha256);

CREATE TABLE IF NOT EXISTS rekordbox_queue (
    library_path TEXT PRIMARY KEY,
    genre        TEXT NOT NULL,
    queued_at    TEXT NOT NULL,
    last_attempt TEXT,
    last_error   TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class FingerprintRow:
    path: Path
    sha256: str
    fingerprint: str
    duration_seconds: float
    size_bytes: int
    mtime_ns: int
    indexed_at: str


@dataclass(frozen=True)
class QueueRow:
    """One entry in the Rekordbox-import queue.

    `last_error` non-None indicates the row failed on its most recent drain
    attempt and is skipped by the default `pending_rekordbox()` call until
    `clear_rekordbox_errors()` resets it or `pending_rekordbox(retry_failed=True)`
    is used."""

    library_path: Path
    genre: str
    queued_at: str
    last_attempt: str | None
    last_error: str | None
    attempts: int


class FingerprintDB:
    """Persistent (sha256, chromaprint) cache + Rekordbox import queue."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False lets the watcher use the connection from its
        # worker thread (created on the CLI main thread). Writes are serialized
        # by the single-worker loop, so SQLite's own locking is sufficient.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def lookup_by_path(self, path: Path) -> FingerprintRow | None:
        row = self._conn.execute(
            "SELECT path, sha256, fingerprint, duration_seconds, size_bytes, mtime_ns, indexed_at "
            "FROM fingerprints WHERE path = ?",
            (str(path),),
        ).fetchone()
        return _row_from_sql(row)

    def lookup_by_sha256(self, sha256: str) -> list[FingerprintRow]:
        rows = self._conn.execute(
            "SELECT path, sha256, fingerprint, duration_seconds, size_bytes, mtime_ns, indexed_at "
            "FROM fingerprints WHERE sha256 = ?",
            (sha256,),
        ).fetchall()
        return [r for r in (_row_from_sql(row) for row in rows) if r is not None]

    def all_rows(self) -> Iterator[FingerprintRow]:
        for row in self._conn.execute(
            "SELECT path, sha256, fingerprint, duration_seconds, size_bytes, mtime_ns, indexed_at "
            "FROM fingerprints"
        ):
            parsed = _row_from_sql(row)
            if parsed is not None:
                yield parsed

    def upsert(self, row: FingerprintRow) -> None:
        self._conn.execute(
            "INSERT INTO fingerprints (path, sha256, fingerprint, duration_seconds, "
            "size_bytes, mtime_ns, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, "
            "fingerprint=excluded.fingerprint, duration_seconds=excluded.duration_seconds, "
            "size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns, "
            "indexed_at=excluded.indexed_at",
            (
                str(row.path),
                row.sha256,
                row.fingerprint,
                row.duration_seconds,
                row.size_bytes,
                row.mtime_ns,
                row.indexed_at,
            ),
        )
        self._conn.commit()

    def delete(self, path: Path) -> None:
        self._conn.execute("DELETE FROM fingerprints WHERE path = ?", (str(path),))
        self._conn.commit()

    # ---- Rekordbox-import queue ----

    def enqueue_rekordbox(self, library_path: Path, genre: str) -> None:
        """Idempotently add a routed file to the Rekordbox import queue."""
        self._conn.execute(
            "INSERT OR IGNORE INTO rekordbox_queue (library_path, genre, queued_at) "
            "VALUES (?, ?, ?)",
            (str(library_path), genre, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def pending_rekordbox(
        self,
        limit: int | None = None,
        *,
        retry_failed: bool = False,
    ) -> list[QueueRow]:
        """Return queue rows ready for drain, oldest first.

        By default skips rows whose `last_error` is set. Pass
        `retry_failed=True` to include them — e.g. for `sync --reset-errors`.
        """
        sql = (
            "SELECT library_path, genre, queued_at, last_attempt, last_error, attempts "
            "FROM rekordbox_queue"
        )
        if not retry_failed:
            sql += " WHERE last_error IS NULL"
        sql += " ORDER BY queued_at ASC"
        if limit is not None:
            sql += " LIMIT ?"
            cursor = self._conn.execute(sql, (limit,))
        else:
            cursor = self._conn.execute(sql)
        return [_queue_row_from_sql(row) for row in cursor.fetchall()]

    def mark_rekordbox_done(self, library_path: Path) -> None:
        """Remove a successfully-imported track from the queue."""
        self._conn.execute(
            "DELETE FROM rekordbox_queue WHERE library_path = ?",
            (str(library_path),),
        )
        self._conn.commit()

    def mark_rekordbox_failed(self, library_path: Path, error: str) -> None:
        """Record a drain-attempt failure for a queue row."""
        self._conn.execute(
            "UPDATE rekordbox_queue "
            "SET last_attempt = ?, last_error = ?, attempts = attempts + 1 "
            "WHERE library_path = ?",
            (datetime.now(UTC).isoformat(), error, str(library_path)),
        )
        self._conn.commit()

    def clear_rekordbox_errors(self) -> int:
        """Reset `last_error` on all queue rows so they re-enter the active queue.

        Returns the number of rows reset.
        """
        cursor = self._conn.execute(
            "UPDATE rekordbox_queue SET last_error = NULL WHERE last_error IS NOT NULL"
        )
        self._conn.commit()
        return cursor.rowcount

    def rekordbox_queue_size(self, *, include_failed: bool = True) -> int:
        """Total queue depth, optionally excluding rows with sticky failures."""
        sql = "SELECT COUNT(*) FROM rekordbox_queue"
        if not include_failed:
            sql += " WHERE last_error IS NULL"
        return self._conn.execute(sql).fetchone()[0]

    def failed_rekordbox(self, limit: int | None = None) -> list[QueueRow]:
        """Queue rows with sticky failures, most recent first. For `status`."""
        sql = (
            "SELECT library_path, genre, queued_at, last_attempt, last_error, attempts "
            "FROM rekordbox_queue WHERE last_error IS NOT NULL "
            "ORDER BY last_attempt DESC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            cursor = self._conn.execute(sql, (limit,))
        else:
            cursor = self._conn.execute(sql)
        return [_queue_row_from_sql(row) for row in cursor.fetchall()]

    def ensure_indexed(self, path: Path) -> FingerprintRow:
        """Return a current row for `path`, refreshing if the cached row is stale."""
        stat = path.stat()
        cached = self.lookup_by_path(path)
        if (
            cached is not None
            and cached.size_bytes == stat.st_size
            and cached.mtime_ns == stat.st_mtime_ns
        ):
            return cached

        sha = sha256_file(path)
        fp = fingerprint_file(path)
        row = FingerprintRow(
            path=path,
            sha256=sha,
            fingerprint=serialize(fp),
            duration_seconds=fp.duration_seconds,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            indexed_at=datetime.now(UTC).isoformat(),
        )
        self.upsert(row)
        return row

    @staticmethod
    def row_to_fingerprint(row: FingerprintRow):
        """Deserialize a row's stored fingerprint string back into a Fingerprint."""
        return deserialize(row.duration_seconds, row.fingerprint)


def _row_from_sql(row: tuple | None) -> FingerprintRow | None:
    if row is None:
        return None
    path_str, sha, fp, duration, size, mtime, indexed_at = row
    return FingerprintRow(
        path=Path(path_str),
        sha256=sha,
        fingerprint=fp,
        duration_seconds=duration,
        size_bytes=size,
        mtime_ns=mtime,
        indexed_at=indexed_at,
    )


def _queue_row_from_sql(row: tuple) -> QueueRow:
    library_path, genre, queued_at, last_attempt, last_error, attempts = row
    return QueueRow(
        library_path=Path(library_path),
        genre=genre,
        queued_at=queued_at,
        last_attempt=last_attempt,
        last_error=last_error,
        attempts=attempts,
    )
