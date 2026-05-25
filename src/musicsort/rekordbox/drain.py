"""Orchestrates one Rekordbox-import drain cycle.

Called from three places: the `watch` daemon (at startup and after every
routed file), the `once` command (after its routing loop completes), and
the manual `musicsort rekordbox sync` CLI. Each call processes whatever
is currently sitting in the `rekordbox_queue` table, capped by
`settings.rekordbox_batch_size`.

Guardrails enforced here, not in the writer:
  - Skip the drain entirely if Rekordbox is currently running, since
    pyrekordbox would refuse to commit anyway.
  - Snapshot `master.db` to a tarball before any write, and prune to the
    configured retention.
  - Commit per-track instead of all-at-end, so a mid-loop Rekordbox launch
    or per-track failure isolates the blast radius.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pyrekordbox.config import get_config

from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.config import Settings
from musicsort.notifications import notify as default_notify
from musicsort.rekordbox.backup import backup_master_db, prune_old_backups
from musicsort.rekordbox.process import rekordbox_running
from musicsort.rekordbox.writer import ImportOutcome, RekordboxWriter

Notifier = Callable[..., None]


@dataclass(frozen=True)
class DrainReport:
    """Summary of one drain cycle. Used by CLI for human output + tests."""

    attempted: int = 0
    inserted: int = 0
    already_present: int = 0
    failed: int = 0
    queue_remaining: int = 0
    skipped_rekordbox_open: bool = False
    skipped_disabled: bool = False

    def human(self) -> str:
        """One-line summary suitable for typer.echo."""
        if self.skipped_disabled:
            return "Rekordbox integration disabled (rekordbox_enabled=False)."
        if self.skipped_rekordbox_open:
            return f"Rekordbox is open — drain skipped ({self.queue_remaining} track(s) pending)."
        return (
            f"attempted: {self.attempted}, inserted: {self.inserted}, "
            f"already present: {self.already_present}, "
            f"failed: {self.failed}, queue remaining: {self.queue_remaining}"
        )


def drain(
    *,
    settings: Settings,
    db: FingerprintDB,
    notifier: Notifier = default_notify,
) -> DrainReport:
    """Process up to `settings.rekordbox_batch_size` pending queue rows."""
    if not settings.rekordbox_enabled:
        return DrainReport(skipped_disabled=True)

    pending_total = db.rekordbox_queue_size(include_failed=False)

    if rekordbox_running():
        if pending_total > 0:
            notifier(
                "musicsort",
                f"Rekordbox is open — {pending_total} track(s) pending import",
                subtitle="Close Rekordbox to drain the queue",
            )
        return DrainReport(
            skipped_rekordbox_open=True,
            queue_remaining=pending_total,
        )

    pending = db.pending_rekordbox(limit=settings.rekordbox_batch_size)
    if not pending:
        return DrainReport(queue_remaining=0)

    master_db = _resolve_master_db_path(settings)
    backup_master_db(master_db, settings.rekordbox_backup_dir)
    prune_old_backups(settings.rekordbox_backup_dir, settings.rekordbox_backup_retention)

    inserted = 0
    already_present = 0
    failed: list[tuple[Path, str]] = []
    rekordbox_opened_mid_drain = False

    with RekordboxWriter(master_db=settings.rekordbox_master_db_path) as writer:
        for row in pending:
            try:
                outcome = writer.import_track(row.library_path)
                writer.commit()
            except RuntimeError as exc:
                # pyrekordbox's commit() raises RuntimeError when it detects
                # Rekordbox running. Discard in-flight state and bail —
                # leaving the queue row as pending (no mark_done call).
                writer.rollback()
                notifier(
                    "musicsort",
                    "Rekordbox opened during import — drain aborted",
                    subtitle=str(exc),
                    sound=True,
                )
                rekordbox_opened_mid_drain = True
                break
            except Exception as exc:
                writer.rollback()
                failed.append((row.library_path, _format_error(exc)))
                continue

            if outcome == ImportOutcome.INSERTED_NEW:
                inserted += 1
            else:
                already_present += 1
            db.mark_rekordbox_done(row.library_path)

    for library_path, error in failed:
        db.mark_rekordbox_failed(library_path, error)

    if failed:
        notifier(
            "musicsort",
            f"{len(failed)} track(s) failed to import into Rekordbox",
            subtitle="Run `musicsort rekordbox status` for details",
            sound=True,
        )

    attempted = inserted + already_present + len(failed)
    queue_remaining = db.rekordbox_queue_size(include_failed=False)

    return DrainReport(
        attempted=attempted,
        inserted=inserted,
        already_present=already_present,
        failed=len(failed),
        queue_remaining=queue_remaining,
        skipped_rekordbox_open=rekordbox_opened_mid_drain,
    )


def _resolve_master_db_path(settings: Settings) -> Path:
    """Settings override wins; otherwise pyrekordbox's options.json autodetect."""
    if settings.rekordbox_master_db_path is not None:
        return settings.rekordbox_master_db_path
    cfg = get_config("rekordbox7")
    return Path(cfg["db_path"])


def _format_error(exc: BaseException) -> str:
    """Short error string for queue row. Type-prefixed so the user can recognize
    well-known errors (FileNotFoundError, ValueError) at a glance."""
    return f"{type(exc).__name__}: {exc}"
