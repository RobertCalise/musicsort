"""musicsort CLI entry point.

Subcommands:
- `musicsort once`              — route every audio file in AutoImport once.
- `musicsort inspect`           — show what `once` would do, without moving anything.
- `musicsort watch`             — long-running watcher; routes new arrivals as they settle.
- `musicsort audit`             — surface duplicates / mis-shelved files in Songs/ (read-only).
- `musicsort install`   — install the watcher as a macOS LaunchAgent.
- `musicsort uninstall` — remove the LaunchAgent.
- `musicsort status`    — show LaunchAgent state.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from musicsort.autoimport.audit import AuditIssueKind, AuditReport, audit_library
from musicsort.autoimport.cli_rekordbox import rekordbox_app
from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.autoimport.mover import (
    MoveAction,
    MovePlan,
    MoveResult,
    move_one,
    plan_move,
)
from musicsort.autoimport.quarantine import _DEDUP_REASONS, Quarantiner
from musicsort.autoimport.service import (
    PLIST_PATH,
    STDERR_LOG,
    STDOUT_LOG,
    ServiceError,
)
from musicsort.autoimport.service import install as service_install
from musicsort.autoimport.service import status as service_status
from musicsort.autoimport.service import uninstall as service_uninstall
from musicsort.autoimport.taxonomy import load_taxonomy
from musicsort.autoimport.watch import cleanup_empty_ancestors
from musicsort.autoimport.watch import watch as watch_loop
from musicsort.config import get_settings
from musicsort.notifications import notify
from musicsort.rekordbox.drain import drain as rekordbox_drain

# Quarantine reasons that don't deserve a popup — these are expected outcomes
# of dedup detection rather than "something went wrong, please look."
_QUIET_QUARANTINE_REASONS: frozenset[str] = frozenset(reason.value for reason in _DEDUP_REASONS)

app = typer.Typer(
    name="musicsort",
    help=(
        "AutoImport-style watcher: tag-driven routing, fingerprint dedup, "
        "and quality-upgrade replacement for ~/Music/Library/."
    ),
    no_args_is_help=True,
)
app.add_typer(rekordbox_app, name="rekordbox")


SourceOpt = Annotated[
    Path | None,
    typer.Option(
        "--source",
        "-s",
        help="Folder to scan. Defaults to MUSICSORT_AUTOIMPORT_FOLDER.",
    ),
]


@app.command()
def once(source: SourceOpt = None) -> None:
    """Route every audio file in the source folder once. Prints per-file results."""
    settings = get_settings()
    scan_root = source or settings.autoimport_folder
    files = _audio_files(scan_root, settings.audio_extensions)
    if not files:
        typer.echo(f"No audio files found in {scan_root}")
        return

    taxonomy = load_taxonomy(settings.taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    quarantiner = Quarantiner(settings.quarantine_dir)
    try:
        typer.echo(f"Routing {len(files)} file(s) from {scan_root} ...")
        results: list[MoveResult] = []
        for i, f in enumerate(files, start=1):
            result = move_one(
                f,
                settings=settings,
                taxonomy=taxonomy,
                db=db,
                quarantiner=quarantiner,
            )
            typer.echo(_format_result(i, len(files), result))
            cleanup_empty_ancestors(f.parent, scan_root)
            results.append(result)
        typer.echo("")
        typer.echo(_format_summary(results))
        if settings.rekordbox_enabled:
            report = rekordbox_drain(settings=settings, db=db)
            typer.echo("")
            typer.echo(f"Rekordbox: {report.human()}")
    finally:
        db.close()


@app.command()
def inspect(source: SourceOpt = None) -> None:
    """Show what `once` would do, without moving any files.

    Populates the fingerprint cache as a side effect (the dedup prediction
    needs an indexed Songs/ tree). User files and quarantine are untouched.
    """
    settings = get_settings()
    scan_root = source or settings.autoimport_folder
    files = _audio_files(scan_root, settings.audio_extensions)
    if not files:
        typer.echo(f"No audio files found in {scan_root}")
        return

    taxonomy = load_taxonomy(settings.taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    try:
        typer.echo(f"Inspecting {len(files)} file(s) from {scan_root} ...")
        for i, f in enumerate(files, start=1):
            predicted = plan_move(f, settings=settings, taxonomy=taxonomy, db=db)
            typer.echo(_format_plan(i, len(files), predicted))
        typer.echo("")
        typer.echo("(no files moved; fingerprint cache may have been populated)")
    finally:
        db.close()


@app.command()
def watch(
    settle: Annotated[
        float | None,
        typer.Option(
            "--settle",
            help=(
                "Seconds of no events on a path before processing. "
                "Defaults to MUSICSORT_WATCH_SETTLE_SECONDS (2.0)."
            ),
        ),
    ] = None,
) -> None:
    """Watch AutoImport and route new files as they arrive. Ctrl-C to stop.

    Drains any files already in AutoImport at startup, then processes new
    arrivals as they settle. Run as a launchd job for unattended operation.
    """
    settings = get_settings()
    taxonomy = load_taxonomy(settings.taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    quarantiner = Quarantiner(settings.quarantine_dir)
    settle_value = settle if settle is not None else settings.watch_settle_seconds
    typer.echo(
        f"Watching {settings.autoimport_folder} (settle: {settle_value:.1f}s, Ctrl-C to stop)"
    )

    if settings.rekordbox_enabled:
        # Catch up any pending queue from previous runs / Rekordbox-open windows.
        startup_report = rekordbox_drain(settings=settings, db=db)
        typer.echo(f"Rekordbox (startup): {startup_report.human()}")

    def on_result(result: MoveResult) -> None:
        typer.echo(_format_watch_line(result))
        cleanup_empty_ancestors(result.src.parent, settings.autoimport_folder)
        if (
            result.action == MoveAction.QUARANTINED
            and result.reason not in _QUIET_QUARANTINE_REASONS
        ):
            notify(
                "musicsort",
                f"{result.reason}: {result.detail}" if result.detail else result.reason,
                subtitle=f"Couldn't sort {result.src.name}",
                sound=True,
            )
        if settings.rekordbox_enabled:
            rekordbox_drain(settings=settings, db=db)

    try:
        watch_loop(
            settings=settings,
            taxonomy=taxonomy,
            db=db,
            quarantiner=quarantiner,
            settle_seconds=settle_value,
            on_result=on_result,
        )
    finally:
        db.close()


@app.command(name="install")
def install_service_cmd() -> None:
    """Install the watcher as a macOS LaunchAgent.

    Writes a `.plist` to `~/Library/LaunchAgents/`, configures it to start
    on login and auto-restart on crash, then bootstraps it immediately so
    the watcher is running by the time this command returns. Idempotent —
    re-running reloads the plist with current settings.
    """
    try:
        service_install()
    except ServiceError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Installed: {PLIST_PATH}")
    typer.echo(f"Logs:      {STDOUT_LOG}")
    typer.echo(f"           {STDERR_LOG}")
    typer.echo("Watcher started. Will auto-start on login + restart on crash.")


@app.command(name="uninstall")
def uninstall_service_cmd() -> None:
    """Stop and remove the watcher LaunchAgent."""
    try:
        service_uninstall()
    except ServiceError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Uninstalled. Removed: {PLIST_PATH}")


@app.command(name="status")
def service_status_cmd() -> None:
    """Show the LaunchAgent state for the watcher."""
    typer.echo(service_status())


@app.command()
def audit() -> None:
    """Audit the existing library: surface duplicates, mis-shelved tracks,
    and bad-tag files in Songs/. Read-only; populates the fingerprint cache
    as a side effect."""
    settings = get_settings()
    if not settings.songs_dir.is_dir():
        typer.echo(f"Songs directory does not exist: {settings.songs_dir}")
        return

    taxonomy = load_taxonomy(settings.taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    try:
        typer.echo(f"Auditing files in {settings.songs_dir} ...")
        typer.echo("(indexing fingerprint cache — first run may take a minute)")
        report = audit_library(settings=settings, taxonomy=taxonomy, db=db)
        typer.echo(_format_audit_report(report))
    finally:
        db.close()


def _audio_files(folder: Path, extensions: tuple[str, ...]) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in extensions)


def _format_result(idx: int, total: int, result: MoveResult) -> str:
    action = result.action.value.upper()
    dst = str(result.dst) if result.dst is not None else "-"
    line = f"[{idx}/{total}] {result.src.name:<40} → {action:<11} → {dst}"
    if result.reason and result.action is MoveAction.QUARANTINED:
        line += f" ({result.reason})"
    if result.detail and result.action is MoveAction.REPLACED:
        line += f"\n{'':>{len(f'[{idx}/{total}] ')}}{'':<40}   ({result.detail})"
    return line


def _format_watch_line(result: MoveResult) -> str:
    timestamp = datetime.now().isoformat(timespec="seconds")
    action = result.action.value.upper()
    dst = str(result.dst) if result.dst is not None else "-"
    line = f"{timestamp}  {result.src.name}  → {action}  → {dst}"
    if result.reason and result.action is MoveAction.QUARANTINED:
        line += f" ({result.reason})"
    return line


def _format_plan(idx: int, total: int, plan: MovePlan) -> str:
    verb_by_action = {
        MoveAction.MOVED: "would MOVE",
        MoveAction.REPLACED: "would REPLACE",
        MoveAction.QUARANTINED: "would QUARANTINE",
    }
    verb = verb_by_action[plan.predicted_action]
    dst = str(plan.predicted_dst) if plan.predicted_dst is not None else "-"
    line = f"[{idx}/{total}] {plan.src.name:<40} → {verb:<17} → {dst}"
    if plan.reason and plan.predicted_action is MoveAction.QUARANTINED:
        line += f" ({plan.reason})"
    if plan.detail and plan.predicted_action is MoveAction.REPLACED:
        line += f" ({plan.detail})"
    return line


_AUDIT_SECTIONS: list[tuple[AuditIssueKind, str]] = [
    (AuditIssueKind.MIS_SHELVED, "Mis-shelved tracks"),
    (AuditIssueKind.NO_GENRE, "No-genre files"),
    (AuditIssueKind.UNKNOWN_GENRE, "Unknown-genre files"),
    (AuditIssueKind.AMBIGUOUS, "Ambiguous-tag files"),
    (AuditIssueKind.MANUAL_ONLY, "Manual-only files in genre folders"),
    (AuditIssueKind.UNREADABLE, "Unreadable files"),
    (AuditIssueKind.DUPLICATE, "Duplicate SHA256"),
    (AuditIssueKind.NEAR_DUPLICATE, "Near-duplicate fingerprint"),
]


def _format_audit_report(report: AuditReport) -> str:
    by_kind: dict[AuditIssueKind, list] = {kind: [] for kind, _ in _AUDIT_SECTIONS}
    for issue in report.issues:
        by_kind[issue.kind].append(issue)

    lines: list[str] = ["", f"Scanned {report.files_scanned} file(s)."]
    for kind, heading in _AUDIT_SECTIONS:
        section = by_kind.get(kind) or []
        if not section:
            continue
        lines.append("")
        lines.append(f"{heading} ({len(section)}):")
        if kind in {AuditIssueKind.DUPLICATE, AuditIssueKind.NEAR_DUPLICATE}:
            for i, issue in enumerate(section, start=1):
                lines.append(f"  Group {i}:")
                for p in issue.paths:
                    lines.append(f"    {p}")
                if issue.detail:
                    lines.append(f"    ({issue.detail})")
        else:
            for issue in section:
                line = f"  {issue.paths[0]}"
                if issue.detail:
                    line += f" — {issue.detail}"
                lines.append(line)

    lines.append("")
    lines.append(f"Summary: {len(report.issues)} issue(s) across {report.files_scanned} file(s).")
    return "\n".join(lines)


def _format_summary(results: list[MoveResult]) -> str:
    by_action: Counter[MoveAction] = Counter(r.action for r in results)
    quarantine_reasons: Counter[str] = Counter(
        r.reason for r in results if r.action is MoveAction.QUARANTINED
    )
    lines = [f"Summary: {len(results)} file(s) processed"]
    for action in (MoveAction.MOVED, MoveAction.REPLACED, MoveAction.QUARANTINED):
        if action in by_action:
            line = f"  {action.value}: {by_action[action]}"
            if action is MoveAction.QUARANTINED and quarantine_reasons:
                breakdown = ", ".join(f"{r}={c}" for r, c in sorted(quarantine_reasons.items()))
                line += f" ({breakdown})"
            lines.append(line)
    return "\n".join(lines)
