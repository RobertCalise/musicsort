"""Tests for the quarantine router."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from musicsort.autoimport.quarantine import Quarantiner, QuarantineReason


@pytest.fixture
def quarantine_dir(tmp_path: Path) -> Path:
    return tmp_path / "_Unsorted"


def _make_file(p: Path, content: bytes = b"x") -> Path:
    p.write_bytes(content)
    return p


_ACTION_REQUIRED_REASONS = [
    QuarantineReason.NO_GENRE,
    QuarantineReason.UNKNOWN_GENRE,
    QuarantineReason.AMBIGUOUS,
    QuarantineReason.MANUAL_ONLY,
    QuarantineReason.UNREADABLE,
]
_DEDUP_REASONS = [QuarantineReason.DUPLICATE, QuarantineReason.WORSE_QUALITY]


@pytest.mark.parametrize("reason", _ACTION_REQUIRED_REASONS)
def test_action_required_lands_in_reason_subfolder(
    tmp_path: Path, quarantine_dir: Path, reason: QuarantineReason
) -> None:
    q = Quarantiner(quarantine_dir)
    f = _make_file(tmp_path / f"{reason.value}.mp3")
    dst = q.quarantine(f, reason)
    assert dst.parent == quarantine_dir / reason.value
    assert dst.exists()
    assert not f.exists()


@pytest.mark.parametrize("reason", _DEDUP_REASONS)
def test_dedup_reasons_land_in_duplicate_subfolder(
    tmp_path: Path, quarantine_dir: Path, reason: QuarantineReason
) -> None:
    q = Quarantiner(quarantine_dir)
    f = _make_file(tmp_path / f"{reason.value}.mp3")
    dst = q.quarantine(f, reason)
    assert dst.parent == quarantine_dir / "duplicate"


def test_filename_collision_appends_suffix(tmp_path: Path, quarantine_dir: Path) -> None:
    q = Quarantiner(quarantine_dir)
    sources = []
    for i in range(3):
        d = tmp_path / f"src_{i}"
        d.mkdir()
        sources.append(_make_file(d / "a.mp3", f"contents {i}".encode()))

    dsts = [q.quarantine(s, QuarantineReason.DUPLICATE) for s in sources]
    assert [d.name for d in dsts] == ["a.mp3", "a-1.mp3", "a-2.mp3"]


def test_jsonl_log_records_each_action(tmp_path: Path, quarantine_dir: Path) -> None:
    q = Quarantiner(quarantine_dir)
    f1 = _make_file(tmp_path / "one.mp3")
    f2 = _make_file(tmp_path / "two.mp3")
    q.quarantine(f1, QuarantineReason.UNREADABLE, detail="bad header")
    q.quarantine(f2, QuarantineReason.DUPLICATE, detail="sha256 match")

    log_path = quarantine_dir / "unsorted.log"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    e1, e2 = json.loads(lines[0]), json.loads(lines[1])
    assert e1["reason"] == "unreadable"
    assert e1["detail"] == "bad header"
    assert e2["reason"] == "duplicate"
    assert e2["detail"] == "sha256 match"
    assert all("timestamp" in e and "src" in e and "dst" in e for e in (e1, e2))


def test_reason_subfolder_created_lazily(tmp_path: Path, quarantine_dir: Path) -> None:
    q = Quarantiner(quarantine_dir)
    assert not quarantine_dir.exists()
    f = _make_file(tmp_path / "x.mp3")
    q.quarantine(f, QuarantineReason.AMBIGUOUS)
    assert (quarantine_dir / "ambiguous").is_dir()
    assert not (quarantine_dir / "duplicate").exists()
