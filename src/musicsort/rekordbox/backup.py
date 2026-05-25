"""Reversible-backup helpers for Rekordbox's `master.db`.

Snapshotting the encrypted SQLite file before any write is mandatory per
the original feasibility-spike guardrails. We tarball `master.db` plus
its `-wal` / `-shm` sidecars (when present) into `backup_dir`, then keep
only the newest N tarballs so the directory doesn't grow unbounded.

Rekordbox is required to be closed before backups, since SQLite's
write-ahead log can otherwise be mid-flush. The drain orchestrator
enforces that precondition before calling `backup_master_db`.
"""

from __future__ import annotations

import tarfile
from datetime import UTC, datetime
from pathlib import Path

_SIDECAR_SUFFIXES = ("-wal", "-shm")


def backup_master_db(master_db: Path, backup_dir: Path) -> Path:
    """Snapshot `master_db` (+ sidecars) into a timestamped tarball.

    Returns the tarball path. Creates `backup_dir` if missing.
    """
    if not master_db.exists():
        raise FileNotFoundError(f"Rekordbox master.db not found: {master_db}")

    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    tarball = backup_dir / f"master_{stamp}.tar.gz"

    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(master_db, arcname=master_db.name)
        for suffix in _SIDECAR_SUFFIXES:
            sidecar = master_db.with_name(master_db.name + suffix)
            if sidecar.exists():
                tf.add(sidecar, arcname=sidecar.name)

    return tarball


def prune_old_backups(backup_dir: Path, retention: int) -> int:
    """Delete all but the newest `retention` master_*.tar.gz backups.

    Returns the number of files deleted. No-op if the directory has fewer
    backups than the retention limit or doesn't exist.
    """
    if retention < 0:
        raise ValueError(f"retention must be >= 0, got {retention}")
    if not backup_dir.exists():
        return 0

    tarballs = sorted(
        backup_dir.glob("master_*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if len(tarballs) <= retention:
        return 0

    deleted = 0
    for old in tarballs[retention:]:
        old.unlink()
        deleted += 1
    return deleted
