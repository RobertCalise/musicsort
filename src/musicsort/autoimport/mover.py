"""Orchestrator that routes a single incoming file.

The routing logic is split in two so the CLI's `inspect` (dry-run) and
`once` (execute) share one decision tree:

  _decide(src, ...) -> Decision          # read + categorize + dedup check, no file moves
  _execute(decision, ...) -> MoveResult  # carry out the decision

`move_one` is `_decide` followed by `_execute`. `plan_move` is `_decide`
wrapped as a `MovePlan` and never executed.

`_decide` walks the library to populate the fingerprint cache (a side
effect on the DB, not on user files) — both real moves and dry-run
inspections need an indexed Songs/ tree to reason about dedup.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from musicsort.autoimport.categorizer import MatchKind, categorize
from musicsort.autoimport.fingerprint_db import FingerprintDB, FingerprintRow
from musicsort.autoimport.fingerprinter import (
    Fingerprint,
    FingerprinterError,
    fingerprint_file,
)
from musicsort.autoimport.fingerprinter import (
    compare as compare_fingerprints,
)
from musicsort.autoimport.fingerprinter import (
    deserialize as deserialize_fp,
)
from musicsort.autoimport.fingerprinter import (
    serialize as serialize_fp,
)
from musicsort.autoimport.hasher import sha256_file
from musicsort.autoimport.quality import compare, score
from musicsort.autoimport.quarantine import _DEDUP_REASONS, Quarantiner, QuarantineReason
from musicsort.autoimport.reader import read_file
from musicsort.autoimport.taxonomy import Taxonomy
from musicsort.config import Settings


class MoveAction(StrEnum):
    MOVED = "moved"
    REPLACED = "replaced"
    QUARANTINED = "quarantined"


class MoveResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    src: Path
    dst: Path | None
    action: MoveAction
    reason: str
    detail: str = ""
    library_target: Path | None = None
    """The Songs/ path this import represents, regardless of where the incoming
    file actually landed. Set for MOVED (= dst), REPLACED (= dst), DUPLICATE
    (= existing library match), and WORSE_QUALITY (= existing winner). None
    for other quarantine reasons since the eventual library destination is
    unknown until the user retags and re-drops."""


class MovePlan(BaseModel):
    """Prediction of what `move_one` would do, produced by `plan_move`.

    `predicted_dst` for a QUARANTINED prediction is the *parent folder*
    (e.g. `_Unsorted/<reason>/` or `_Unsorted/duplicate/`), not the final
    post-collision filename — those are resolved at execution time."""

    model_config = ConfigDict(frozen=True)

    src: Path
    predicted_action: MoveAction
    predicted_dst: Path | None
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class Decision:
    """Output of the read+categorize+dedup decision tree.

    Carries everything `_execute` needs to perform the move, plus enough
    detail to render a useful inspect-mode preview.
    """

    action: MoveAction
    reason: str
    detail: str
    destination: Path | None = None
    existing_to_replace: Path | None = None
    quarantine_reason: QuarantineReason | None = None
    incoming_sha: str | None = None
    incoming_fp: Fingerprint | None = None
    library_match: Path | None = None
    """For DUPLICATE / WORSE_QUALITY quarantines, the existing library file
    that matched. `_execute` forwards this to MoveResult.library_target so
    the playlist writer can reference it."""


def move_one(
    src: Path,
    *,
    settings: Settings,
    taxonomy: Taxonomy,
    db: FingerprintDB,
    quarantiner: Quarantiner,
) -> MoveResult:
    """Route one incoming file end-to-end."""
    decision = _decide(src, settings=settings, taxonomy=taxonomy, db=db)
    result = _execute(src, decision, db=db, quarantiner=quarantiner)
    if settings.rekordbox_enabled and result.library_target is not None:
        # Genre is the immediate parent folder name under Songs/<Genre>/.
        # Covers MOVED, REPLACED, and dedup-match quarantines uniformly —
        # the drain dedups via DjmdContent.FolderPath if already in Rekordbox.
        db.enqueue_rekordbox(
            result.library_target,
            result.library_target.parent.name,
        )
    return result


def plan_move(
    src: Path,
    *,
    settings: Settings,
    taxonomy: Taxonomy,
    db: FingerprintDB,
) -> MovePlan:
    """Predict what `move_one` would do, without moving any files."""
    decision = _decide(src, settings=settings, taxonomy=taxonomy, db=db)
    predicted_dst = _predicted_dst(src, decision, settings.quarantine_dir)
    return MovePlan(
        src=src,
        predicted_action=decision.action,
        predicted_dst=predicted_dst,
        reason=decision.reason,
        detail=decision.detail,
    )


def _decide(
    src: Path,
    *,
    settings: Settings,
    taxonomy: Taxonomy,
    db: FingerprintDB,
) -> Decision:
    info = read_file(src)
    if info.reader == "unreadable":
        return Decision(
            action=MoveAction.QUARANTINED,
            reason=QuarantineReason.UNREADABLE.value,
            detail="reader could not parse",
            quarantine_reason=QuarantineReason.UNREADABLE,
        )

    match = categorize(info.tags, taxonomy)
    if match.kind is not MatchKind.MATCHED or match.primary is None:
        reason = _reason_for_match(match.kind, match.reason)
        return Decision(
            action=MoveAction.QUARANTINED,
            reason=reason.value,
            detail=match.reason,
            quarantine_reason=reason,
        )

    _refresh_index(settings.songs_dir, db, settings.audio_extensions)

    incoming_sha = sha256_file(src)
    for existing in db.lookup_by_sha256(incoming_sha):
        if existing.path.exists():
            return Decision(
                action=MoveAction.QUARANTINED,
                reason=QuarantineReason.DUPLICATE.value,
                detail=f"sha256 match: {existing.path}",
                quarantine_reason=QuarantineReason.DUPLICATE,
                library_match=existing.path,
            )

    try:
        incoming_fp = fingerprint_file(src)
    except FingerprinterError as exc:
        return Decision(
            action=MoveAction.QUARANTINED,
            reason=QuarantineReason.UNREADABLE.value,
            detail=str(exc),
            quarantine_reason=QuarantineReason.UNREADABLE,
        )

    best = _best_fingerprint_match(
        incoming_fp=incoming_fp,
        db=db,
        threshold=settings.similarity_threshold,
    )
    if best is not None:
        existing_info = read_file(best.path)
        if compare(score(info), score(existing_info)) <= 0:
            return Decision(
                action=MoveAction.QUARANTINED,
                reason=QuarantineReason.WORSE_QUALITY.value,
                detail=f"existing wins: {best.path}",
                quarantine_reason=QuarantineReason.WORSE_QUALITY,
                library_match=best.path,
            )
        return Decision(
            action=MoveAction.REPLACED,
            reason="quality upgrade",
            detail=f"replaces {best.path}",
            destination=best.path,
            existing_to_replace=best.path,
            incoming_sha=incoming_sha,
            incoming_fp=incoming_fp,
        )

    destination = _avoid_collision(settings.songs_dir / match.primary.folder / src.name)
    return Decision(
        action=MoveAction.MOVED,
        reason=match.reason,
        detail="",
        destination=destination,
        incoming_sha=incoming_sha,
        incoming_fp=incoming_fp,
    )


def _execute(
    src: Path,
    decision: Decision,
    *,
    db: FingerprintDB,
    quarantiner: Quarantiner,
) -> MoveResult:
    if decision.action is MoveAction.QUARANTINED:
        assert decision.quarantine_reason is not None
        dst = quarantiner.quarantine(src, decision.quarantine_reason, decision.detail)
        return MoveResult(
            src=src,
            dst=dst,
            action=MoveAction.QUARANTINED,
            reason=decision.reason,
            detail=decision.detail,
            library_target=decision.library_match,
        )

    if decision.action is MoveAction.REPLACED:
        assert decision.existing_to_replace is not None
        quarantiner.quarantine(
            decision.existing_to_replace,
            QuarantineReason.WORSE_QUALITY,
            f"replaced by upgrade: {src}",
        )
        db.delete(decision.existing_to_replace)

    destination = decision.destination
    assert destination is not None
    destination.parent.mkdir(parents=True, exist_ok=True)
    _move_file(src, destination)
    assert decision.incoming_fp is not None and decision.incoming_sha is not None
    db.upsert(
        FingerprintRow(
            path=destination,
            sha256=decision.incoming_sha,
            fingerprint=serialize_fp(decision.incoming_fp),
            duration_seconds=decision.incoming_fp.duration_seconds,
            size_bytes=destination.stat().st_size,
            mtime_ns=destination.stat().st_mtime_ns,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    return MoveResult(
        src=src,
        dst=destination,
        action=decision.action,
        reason=decision.reason,
        detail=decision.detail,
        library_target=destination,
    )


def _predicted_dst(
    src: Path,
    decision: Decision,
    quarantine_dir: Path,
) -> Path | None:
    """Render a human-readable predicted destination for inspect output."""
    if decision.action is MoveAction.QUARANTINED:
        reason = decision.quarantine_reason
        if reason is None:
            return None
        if reason in _DEDUP_REASONS:
            return quarantine_dir / "duplicate"
        return quarantine_dir / reason.value
    return decision.destination


def _refresh_index(songs_dir: Path, db: FingerprintDB, audio_extensions: tuple[str, ...]) -> None:
    """Walk Songs/, ensure each audio file is indexed, prune rows for paths that vanished."""
    if not songs_dir.is_dir():
        return
    on_disk: set[Path] = set()
    for path in songs_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in audio_extensions:
            continue
        on_disk.add(path)
        db.ensure_indexed(path)
    for row in list(db.all_rows()):
        if row.path not in on_disk and not row.path.exists():
            db.delete(row.path)


def _best_fingerprint_match(
    *,
    incoming_fp: Fingerprint,
    db: FingerprintDB,
    threshold: float,
) -> FingerprintRow | None:
    """Return the highest-similarity row above `threshold`, or None."""
    best: FingerprintRow | None = None
    best_score = 0.0
    for row in db.all_rows():
        candidate = deserialize_fp(row.duration_seconds, row.fingerprint)
        score_value = compare_fingerprints(incoming_fp, candidate)
        if score_value > threshold and score_value > best_score:
            best = row
            best_score = score_value
    return best


def _reason_for_match(kind: MatchKind, match_reason: str) -> QuarantineReason:
    if kind is MatchKind.UNMATCHED:
        if match_reason == "no genre tag":
            return QuarantineReason.NO_GENRE
        # The categorizer formats year-gated misses as "...matches year-gated
        # categor{y,ies} ... but year tag is missing". Route those to their
        # own subfolder so the user knows to add a year tag rather than chase
        # the genre.
        if "year-gated" in match_reason:
            return QuarantineReason.MISSING_YEAR
        return QuarantineReason.UNKNOWN_GENRE
    if kind is MatchKind.MANUAL_ONLY:
        return QuarantineReason.MANUAL_ONLY
    return QuarantineReason.AMBIGUOUS


def _avoid_collision(dst: Path) -> Path:
    if not dst.exists():
        return dst
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    i = 1
    while True:
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _move_file(src: Path, dst: Path) -> None:
    """Same-volume rename when possible; fall back to shutil.move for cross-volume."""
    try:
        src.rename(dst)
    except OSError:
        shutil.move(str(src), str(dst))
