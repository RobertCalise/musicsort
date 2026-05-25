"""`musicsort rekordbox …` subcommands.

Manual-trigger surface for the Rekordbox stage: drain pending tracks
into the collection, bulk-backfill an already-routed library, inspect
queue status, and (opt-in) sync genre/decade playlists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pyrekordbox.config import get_config

from musicsort.autoimport.categorizer import MatchKind, categorize
from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.autoimport.reader import read_file
from musicsort.autoimport.taxonomy import Category, load_taxonomy
from musicsort.config import Settings, get_settings
from musicsort.rekordbox.backup import backup_master_db, prune_old_backups
from musicsort.rekordbox.drain import drain as rekordbox_drain
from musicsort.rekordbox.process import rekordbox_running
from musicsort.rekordbox.writer import RekordboxWriter

rekordbox_app = typer.Typer(
    name="rekordbox",
    help=(
        "Manage the Rekordbox auto-import stage: drain pending tracks, "
        "backfill an existing library, inspect queue status, sync playlists."
    ),
    no_args_is_help=True,
)


@rekordbox_app.command()
def sync(
    reset_errors: Annotated[
        bool,
        typer.Option(
            "--reset-errors",
            help="Clear sticky failure markers before draining; retries all failed rows.",
        ),
    ] = False,
) -> None:
    """Run one drain cycle against whatever is currently queued."""
    settings = get_settings()
    db = FingerprintDB(settings.fingerprint_db_path)
    try:
        if reset_errors:
            cleared = db.clear_rekordbox_errors()
            typer.echo(f"Cleared {cleared} sticky error(s); re-queued for retry.")
        report = rekordbox_drain(settings=settings, db=db)
        typer.echo(report.human())
    finally:
        db.close()


@rekordbox_app.command()
def backfill(
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help=(
                "Max tracks to enqueue this invocation. Use to graduate large "
                "backfills (e.g. --limit 25, observe, then --limit 100)."
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="List what would be enqueued without modifying the queue or Rekordbox.",
        ),
    ] = False,
) -> None:
    """Scan Library/Songs/ and enqueue files not already tracked, then drain."""
    settings = get_settings()
    candidates = _scan_songs(settings)
    if not candidates:
        typer.echo(f"No audio files found under {settings.songs_dir}")
        return

    db = FingerprintDB(settings.fingerprint_db_path)
    try:
        already_queued = _already_queued_paths(db)
        new_paths = [p for p in candidates if p not in already_queued]
        if not new_paths:
            typer.echo(
                f"All {len(candidates)} file(s) under Songs/ are already queued or failed. "
                "Nothing to enqueue."
            )
            if not dry_run:
                report = rekordbox_drain(settings=settings, db=db)
                typer.echo(report.human())
            return

        to_enqueue = new_paths[:limit] if limit is not None else new_paths
        typer.echo(
            f"Found {len(candidates)} file(s) under Songs/; "
            f"{len(new_paths)} not yet queued; will enqueue {len(to_enqueue)}."
        )

        if dry_run:
            typer.echo("\n[dry-run] would enqueue:")
            for path in to_enqueue:
                typer.echo(f"  {path.parent.name:<20}  {path.name}")
            return

        for path in to_enqueue:
            db.enqueue_rekordbox(path, path.parent.name)
        typer.echo(f"Enqueued {len(to_enqueue)} track(s).")

        report = rekordbox_drain(settings=settings, db=db)
        typer.echo("")
        typer.echo(report.human())
    finally:
        db.close()


@rekordbox_app.command()
def status() -> None:
    """Show queue depth, pending count, and most recent failures."""
    settings = get_settings()
    db = FingerprintDB(settings.fingerprint_db_path)
    try:
        pending = db.rekordbox_queue_size(include_failed=False)
        total = db.rekordbox_queue_size(include_failed=True)
        failed_rows = db.failed_rekordbox(limit=10)

        typer.echo(f"Queue size:     {total}")
        typer.echo(f"  pending:      {pending}")
        typer.echo(f"  failed:       {len(failed_rows)} (showing latest 10)")
        typer.echo(f"Library path:   {settings.songs_dir}")
        typer.echo(f"Master DB:      {settings.rekordbox_master_db_path or '(autodetect)'}")
        typer.echo(f"Playlist root:  {settings.rekordbox_playlist_parent}")
        typer.echo(
            f"Batch size cap: "
            f"{settings.rekordbox_batch_size if settings.rekordbox_batch_size else 'unlimited'}"
        )
        typer.echo(f"Enabled:        {settings.rekordbox_enabled}")
        typer.echo(f"As of:          {datetime.now(UTC).isoformat(timespec='seconds')}")

        if failed_rows:
            typer.echo("")
            typer.echo("Recent failures:")
            for row in failed_rows:
                typer.echo(
                    f"  {row.library_path.name}\n"
                    f"    attempts: {row.attempts}, last_attempt: {row.last_attempt}\n"
                    f"    error: {row.last_error}"
                )
    finally:
        db.close()


@rekordbox_app.command()
def playlists(
    genres: Annotated[
        bool,
        typer.Option(
            "--genres",
            help="Sync per-genre playlists under <parent>/Genres/[<family>/]<genre>.",
        ),
    ] = False,
    decades: Annotated[
        bool,
        typer.Option(
            "--decades",
            help="Sync per-decade playlists under <parent>/Decades/<NNs>.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="List what would change without writing to Rekordbox.",
        ),
    ] = False,
) -> None:
    """Build / sync genre and decade playlists from Library/Songs/.

    Opt-in by flag: nothing happens without `--genres` and/or `--decades`.
    Walks Library/Songs/, looks up each file's collection row in Rekordbox,
    runs the categorizer, and ensures the track appears in up to
    `rekordbox_playlist_fanout` genre playlists and the appropriate decade
    playlist (if `--decades` and the file has a year tag). Add-only.
    """
    if not genres and not decades:
        typer.echo("Nothing to do. Pass --genres and/or --decades.")
        raise typer.Exit(0)

    settings = get_settings()
    if not settings.rekordbox_enabled:
        typer.echo("Rekordbox integration disabled (rekordbox_enabled=False).")
        raise typer.Exit(0)

    if not dry_run and rekordbox_running():
        typer.echo("Rekordbox is open — close it before running playlist sync.")
        raise typer.Exit(1)

    songs = _scan_songs(settings)
    if not songs:
        typer.echo(f"No audio files under {settings.songs_dir}")
        raise typer.Exit(0)

    taxonomy = load_taxonomy(settings.taxonomy_path)
    master_db = settings.rekordbox_master_db_path or Path(get_config("rekordbox7")["db_path"])

    if not dry_run:
        backup_master_db(master_db, settings.rekordbox_backup_dir)
        prune_old_backups(settings.rekordbox_backup_dir, settings.rekordbox_backup_retention)

    counters = {
        "scanned": 0,
        "not_in_collection": 0,
        "unmatched": 0,
        "manual_only": 0,
        "ambiguous": 0,
        "genre_added": 0,
        "genre_already": 0,
        "decade_added": 0,
        "decade_already": 0,
        "no_year": 0,
    }

    with RekordboxWriter(
        # Use the resolved master_db so the writer and the backup above always
        # target the same DB file (override may be None → autodetect).
        master_db=master_db,
        playlist_parent=settings.rekordbox_playlist_parent,
        genres_folder=settings.rekordbox_genres_folder,
        decades_folder=settings.rekordbox_decades_folder,
    ) as writer:
        for path in songs:
            counters["scanned"] += 1
            content = writer.get_content_for_path(path)
            if content is None:
                counters["not_in_collection"] += 1
                continue

            info = read_file(path)
            result = categorize(info.tags, taxonomy)

            if result.kind is MatchKind.UNMATCHED:
                counters["unmatched"] += 1
                continue
            if result.kind is MatchKind.MANUAL_ONLY:
                counters["manual_only"] += 1
                continue
            if result.kind is MatchKind.AMBIGUOUS:
                counters["ambiguous"] += 1
                continue
            if result.primary is None:
                continue

            if genres:
                ranked: list[Category] = [result.primary, *result.secondaries]
                for cat in ranked[: settings.rekordbox_playlist_fanout]:
                    if cat.manual_only:
                        continue
                    if dry_run:
                        counters["genre_added"] += 1
                        continue
                    playlist = writer.ensure_genre_playlist(cat.name, cat.family)
                    if writer.add_track_to_playlist(content, playlist):
                        counters["genre_added"] += 1
                    else:
                        counters["genre_already"] += 1

            if decades:
                if info.tags.year is None:
                    counters["no_year"] += 1
                else:
                    label = _decade_label(info.tags.year)
                    if dry_run:
                        counters["decade_added"] += 1
                        continue
                    playlist = writer.ensure_decade_playlist(label)
                    if writer.add_track_to_playlist(content, playlist):
                        counters["decade_added"] += 1
                    else:
                        counters["decade_already"] += 1

        if not dry_run:
            writer.commit()

    _print_playlists_report(counters, dry_run=dry_run, genres=genres, decades=decades)


def _scan_songs(settings: Settings) -> list[Path]:
    """Walk `songs_dir` and return all audio files, sorted."""
    if not settings.songs_dir.is_dir():
        return []
    return sorted(
        p
        for p in settings.songs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in settings.audio_extensions
    )


def _already_queued_paths(db: FingerprintDB) -> set[Path]:
    """Snapshot of all paths currently in the queue (pending OR failed)."""
    rows = db.pending_rekordbox(retry_failed=True)
    return {row.library_path for row in rows}


def _decade_label(year: int) -> str:
    """`80s` / `90s` pre-2000, `2000s` / `2010s` / etc. for 2000+."""
    decade_start = (year // 10) * 10
    if decade_start < 2000:
        return f"{decade_start % 100}s"
    return f"{decade_start}s"


def _print_playlists_report(
    counters: dict[str, int],
    *,
    dry_run: bool,
    genres: bool,
    decades: bool,
) -> None:
    prefix = "[dry-run] would " if dry_run else ""
    typer.echo(f"Scanned: {counters['scanned']} file(s) under Library/Songs/")
    if counters["not_in_collection"]:
        typer.echo(
            f"  not in Rekordbox collection: {counters['not_in_collection']} "
            "(run `musicsort rekordbox sync` first)"
        )
    if counters["unmatched"] + counters["manual_only"] + counters["ambiguous"]:
        typer.echo(
            f"  skipped: {counters['unmatched']} unmatched, "
            f"{counters['manual_only']} manual_only, "
            f"{counters['ambiguous']} ambiguous"
        )
    if genres:
        typer.echo(
            f"  Genres: {prefix}add {counters['genre_added']} "
            f"({counters['genre_already']} already present)"
        )
    if decades:
        suffix = f" ({counters['no_year']} skipped — no year tag)" if counters["no_year"] else ""
        typer.echo(
            f"  Decades: {prefix}add {counters['decade_added']} "
            f"({counters['decade_already']} already present){suffix}"
        )
