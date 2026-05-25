"""Tests for the master.db backup + retention helpers."""

from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

import pytest

from musicsort.rekordbox.backup import backup_master_db, prune_old_backups


def _write_fake_db(dir_: Path, include_sidecars: bool = True) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    master = dir_ / "master.db"
    master.write_bytes(b"fake-sqlite-content")
    if include_sidecars:
        (dir_ / "master.db-wal").write_bytes(b"wal-data")
        (dir_ / "master.db-shm").write_bytes(b"shm-data")
    return master


def test_backup_creates_tarball_with_main_and_sidecars(tmp_path: Path) -> None:
    master = _write_fake_db(tmp_path / "rekordbox")
    backup_dir = tmp_path / "backups"

    tarball = backup_master_db(master, backup_dir)

    assert tarball.exists()
    assert tarball.suffix == ".gz"
    assert tarball.parent == backup_dir
    with tarfile.open(tarball) as tf:
        names = set(tf.getnames())
    assert names == {"master.db", "master.db-wal", "master.db-shm"}


def test_backup_skips_missing_sidecars(tmp_path: Path) -> None:
    master = _write_fake_db(tmp_path / "rekordbox", include_sidecars=False)
    backup_dir = tmp_path / "backups"

    tarball = backup_master_db(master, backup_dir)
    with tarfile.open(tarball) as tf:
        names = set(tf.getnames())
    assert names == {"master.db"}


def test_backup_raises_when_master_db_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        backup_master_db(tmp_path / "nope.db", tmp_path / "backups")


def test_backup_creates_backup_dir(tmp_path: Path) -> None:
    master = _write_fake_db(tmp_path / "rekordbox", include_sidecars=False)
    backup_dir = tmp_path / "deeply" / "nested" / "backups"
    assert not backup_dir.exists()

    backup_master_db(master, backup_dir)

    assert backup_dir.is_dir()


def test_prune_keeps_newest_n(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    paths: list[Path] = []
    for i in range(5):
        p = backup_dir / f"master_2026-05-2{i}T00-00-00.tar.gz"
        p.write_bytes(b"x")
        ts = 1_700_000_000 + i * 100
        os.utime(p, (ts, ts))
        paths.append(p)

    deleted = prune_old_backups(backup_dir, retention=2)

    assert deleted == 3
    remaining = sorted(backup_dir.glob("master_*.tar.gz"))
    assert len(remaining) == 2
    assert paths[-1] in remaining
    assert paths[-2] in remaining


def test_prune_no_op_when_under_retention(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "master_2026-05-20T00-00-00.tar.gz").write_bytes(b"x")
    (backup_dir / "master_2026-05-21T00-00-00.tar.gz").write_bytes(b"x")

    deleted = prune_old_backups(backup_dir, retention=10)

    assert deleted == 0
    assert len(list(backup_dir.glob("master_*.tar.gz"))) == 2


def test_prune_missing_dir_returns_zero(tmp_path: Path) -> None:
    deleted = prune_old_backups(tmp_path / "nope", retention=5)
    assert deleted == 0


def test_prune_rejects_negative_retention(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        prune_old_backups(tmp_path, retention=-1)


def test_backup_filenames_are_unique_per_second(tmp_path: Path) -> None:
    master = _write_fake_db(tmp_path / "rekordbox", include_sidecars=False)
    backup_dir = tmp_path / "backups"

    first = backup_master_db(master, backup_dir)
    time.sleep(1.1)
    second = backup_master_db(master, backup_dir)

    assert first != second
    assert first.exists() and second.exists()
