"""Quarantine routing for files the mover can't auto-shelf.

Layout:

- `_Unsorted/<reason>/<file>`   - action-required outcomes
- `_Unsorted/duplicate/<file>`  - DUPLICATE / WORSE_QUALITY (just delete)

Every quarantine action writes one JSON line to `<root>/unsorted.log`
recording the original source path, reason, and detail.

By the time a file reaches `_Unsorted/<reason>/` it means every automatic
option failed (categorizer; future AcoustID/MusicBrainz lookup). The
reason subfolder tells the user what to fix.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class QuarantineReason(StrEnum):
    UNREADABLE = "unreadable"
    NO_GENRE = "no_genre"
    UNKNOWN_GENRE = "unknown_genre"
    MANUAL_ONLY = "manual_only"
    AMBIGUOUS = "ambiguous"
    DUPLICATE = "duplicate"
    WORSE_QUALITY = "worse_quality"


_DEDUP_REASONS: frozenset[QuarantineReason] = frozenset(
    {QuarantineReason.DUPLICATE, QuarantineReason.WORSE_QUALITY}
)


class Quarantiner:
    """Move files into reason-keyed quarantine subfolders with a JSONL audit log."""

    def __init__(self, quarantine_dir: Path) -> None:
        self._root = quarantine_dir
        self._log_path = quarantine_dir / "unsorted.log"

    def quarantine(
        self,
        src: Path,
        reason: QuarantineReason,
        detail: str = "",
    ) -> Path:
        dst_dir = self._resolve_dst_dir(reason)
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = _resolve_collision(dst_dir / src.name)
        shutil.move(str(src), str(dst))
        self._log(src, dst, reason, detail)
        return dst

    def _resolve_dst_dir(self, reason: QuarantineReason) -> Path:
        if reason in _DEDUP_REASONS:
            return self._root / "duplicate"
        return self._root / reason.value

    def _log(self, src: Path, dst: Path, reason: QuarantineReason, detail: str) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "src": str(src),
            "dst": str(dst),
            "reason": reason.value,
            "detail": detail,
        }
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def _resolve_collision(dst: Path) -> Path:
    """Return `dst` if free, else `name-1.ext`, `name-2.ext`, ..."""
    if not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    parent = dst.parent
    i = 1
    while True:
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1
