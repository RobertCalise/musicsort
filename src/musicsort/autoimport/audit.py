"""Audit the existing library for routing and dedup issues.

`audit_library()` walks `settings.songs_dir`, runs each file through the
categorizer to find mis-shelved tracks and bad tags, then does layered
dedup (SHA256 + chromaprint) across the whole library to surface duplicate
groups. Read-only — emits an `AuditReport` and never moves files.

The fingerprint pass uses duration bucketing to avoid O(n²) comparisons:
two files with substantially different durations cannot chromaprint-match,
so we only compare pairs whose durations differ by ≤ 1.5 seconds.
"""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from musicsort.autoimport.categorizer import MatchKind, categorize
from musicsort.autoimport.fingerprint_db import FingerprintDB, FingerprintRow
from musicsort.autoimport.fingerprinter import compare, deserialize
from musicsort.autoimport.reader import read_file
from musicsort.autoimport.taxonomy import Taxonomy
from musicsort.config import Settings

_DURATION_BUCKET_SECONDS = 1.5


class AuditIssueKind(StrEnum):
    UNREADABLE = "unreadable"
    NO_GENRE = "no_genre"
    UNKNOWN_GENRE = "unknown_genre"
    AMBIGUOUS = "ambiguous"
    MANUAL_ONLY = "manual_only"
    MIS_SHELVED = "mis_shelved"
    DUPLICATE = "duplicate"
    NEAR_DUPLICATE = "near_duplicate"


class AuditIssue(BaseModel):
    """One audit finding. Single-path for per-file issues, multi-path for groups."""

    model_config = ConfigDict(frozen=True)

    kind: AuditIssueKind
    paths: tuple[Path, ...]
    detail: str = ""


class AuditReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    files_scanned: int
    issues: tuple[AuditIssue, ...]


def audit_library(
    *,
    settings: Settings,
    taxonomy: Taxonomy,
    db: FingerprintDB,
) -> AuditReport:
    """Walk Songs/, surface routing + dedup issues. Read-only."""
    songs_dir = settings.songs_dir
    if not songs_dir.is_dir():
        return AuditReport(files_scanned=0, issues=())

    files = sorted(_audio_files(songs_dir, settings.audio_extensions))
    issues: list[AuditIssue] = []
    indexed_paths: set[Path] = set()

    for path in files:
        per_file = _classify_file(path, songs_dir, taxonomy)
        if per_file is not None:
            issues.append(per_file)
            if per_file.kind is AuditIssueKind.UNREADABLE:
                continue
        db.ensure_indexed(path)
        indexed_paths.add(path)

    rows = [row for row in db.all_rows() if row.path in indexed_paths]

    duplicate_paths = _find_duplicates(rows)
    issues.extend(duplicate_paths.issues)

    issues.extend(
        _find_near_duplicates(
            rows,
            threshold=settings.similarity_threshold,
            already_grouped=duplicate_paths.path_pairs,
        )
    )

    return AuditReport(files_scanned=len(files), issues=tuple(issues))


def _audio_files(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in extensions]


def _classify_file(path: Path, songs_dir: Path, taxonomy: Taxonomy) -> AuditIssue | None:
    """Return one routing-level issue for `path`, or None when the file is correctly placed."""
    info = read_file(path)
    if info.reader == "unreadable":
        return AuditIssue(
            kind=AuditIssueKind.UNREADABLE,
            paths=(path,),
            detail="reader could not parse",
        )

    match = categorize(info.tags, taxonomy)
    if match.kind is MatchKind.MATCHED and match.primary is not None:
        actual_folder = _genre_folder_of(path, songs_dir)
        expected_folder = match.primary.folder
        if actual_folder is not None and actual_folder != expected_folder:
            return AuditIssue(
                kind=AuditIssueKind.MIS_SHELVED,
                paths=(path,),
                detail=(
                    f"expected {expected_folder} (tag: {info.tags.genre!r}), is in {actual_folder}"
                ),
            )
        return None

    if match.kind is MatchKind.MANUAL_ONLY:
        return AuditIssue(
            kind=AuditIssueKind.MANUAL_ONLY,
            paths=(path,),
            detail=match.reason,
        )
    if match.kind is MatchKind.AMBIGUOUS:
        return AuditIssue(
            kind=AuditIssueKind.AMBIGUOUS,
            paths=(path,),
            detail=match.reason,
        )

    # UNMATCHED — distinguish "no genre" from "tag-known-but-ambiguous-without-year"
    # (which the categorizer also reports as UNMATCHED) from truly unknown genres.
    if match.reason == "no genre tag":
        return AuditIssue(
            kind=AuditIssueKind.NO_GENRE,
            paths=(path,),
            detail=match.reason,
        )
    if "year-gated" in match.reason or "year filter eliminated" in match.reason:
        return AuditIssue(
            kind=AuditIssueKind.AMBIGUOUS,
            paths=(path,),
            detail=match.reason,
        )
    return AuditIssue(
        kind=AuditIssueKind.UNKNOWN_GENRE,
        paths=(path,),
        detail=f"genre: {info.tags.genre!r}",
    )


def _genre_folder_of(path: Path, songs_dir: Path) -> str | None:
    """Return the immediate-child folder name under `songs_dir`, or None if path isn't under it."""
    try:
        rel = path.relative_to(songs_dir)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    return parts[0]


class _DuplicateResult:
    """Helper return shape from the SHA256 pass."""

    def __init__(self, issues: list[AuditIssue], path_pairs: set[frozenset[Path]]) -> None:
        self.issues = issues
        self.path_pairs = path_pairs


def _find_duplicates(rows: list[FingerprintRow]) -> _DuplicateResult:
    """Group rows by sha256. Each group with ≥ 2 rows is one DUPLICATE issue."""
    by_sha: dict[str, list[FingerprintRow]] = defaultdict(list)
    for row in rows:
        by_sha[row.sha256].append(row)

    issues: list[AuditIssue] = []
    pairs: set[frozenset[Path]] = set()
    for sha, group in sorted(by_sha.items()):
        if len(group) < 2:
            continue
        paths = tuple(sorted(r.path for r in group))
        issues.append(
            AuditIssue(
                kind=AuditIssueKind.DUPLICATE,
                paths=paths,
                detail=f"sha256: {sha[:16]}...",
            )
        )
        for i, a in enumerate(paths):
            for b in paths[i + 1 :]:
                pairs.add(frozenset({a, b}))
    return _DuplicateResult(issues=issues, path_pairs=pairs)


def _find_near_duplicates(
    rows: list[FingerprintRow],
    *,
    threshold: float,
    already_grouped: set[frozenset[Path]],
) -> list[AuditIssue]:
    """Pairwise chromaprint compare within duration buckets."""
    buckets = _bucket_by_duration(rows)
    issues: list[AuditIssue] = []
    for bucket in buckets:
        n = len(bucket)
        for i in range(n):
            row_a = bucket[i]
            fp_a = deserialize(row_a.duration_seconds, row_a.fingerprint)
            for j in range(i + 1, n):
                row_b = bucket[j]
                pair_key = frozenset({row_a.path, row_b.path})
                if pair_key in already_grouped:
                    continue
                fp_b = deserialize(row_b.duration_seconds, row_b.fingerprint)
                similarity = compare(fp_a, fp_b)
                if similarity >= threshold:
                    issues.append(
                        AuditIssue(
                            kind=AuditIssueKind.NEAR_DUPLICATE,
                            paths=tuple(sorted((row_a.path, row_b.path))),
                            detail=f"similarity: {similarity:.2f}",
                        )
                    )
    return issues


def _bucket_by_duration(rows: list[FingerprintRow]) -> list[list[FingerprintRow]]:
    """Group rows whose durations are within _DURATION_BUCKET_SECONDS of each other.

    Sort by duration, then scan with a window so adjacent close-duration rows
    end up in the same bucket. Far-apart rows can't possibly chromaprint-match."""
    if not rows:
        return []
    by_duration = sorted(rows, key=lambda r: r.duration_seconds)
    buckets: list[list[FingerprintRow]] = [[by_duration[0]]]
    for row in by_duration[1:]:
        last_bucket = buckets[-1]
        if row.duration_seconds - last_bucket[0].duration_seconds <= _DURATION_BUCKET_SECONDS:
            last_bucket.append(row)
        else:
            buckets.append([row])
    return [b for b in buckets if len(b) >= 2]
