"""Application configuration. Single Pydantic Settings source of truth.

`library_root` is the master knob: it defaults to `~/Music/Library` and
every library-internal path (`songs_dir`, `quarantine_dir`,
`fingerprint_db_path`, `rekordbox_backup_dir`) derives from it.

`autoimport_folder` is intentionally independent — it's the watcher's
input, conventionally a sibling of `library_root` so dropping files there
doesn't pollute the library tree.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parents[2] from this file:
# src/musicsort/config.py -> src/musicsort -> src -> REPO_ROOT
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ROOT = Path(__file__).resolve().parent

_DEFAULT_LIBRARY_ROOT = Path.home() / "Music" / "Library"


class Settings(BaseSettings):
    """Environment-driven config. Override via env vars or a `.env` file at
    the repo root. All env vars are prefixed `MUSICSORT_`."""

    model_config = SettingsConfigDict(
        env_prefix="MUSICSORT_",
        env_file=(_REPO_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    library_root: Path = Field(
        default=_DEFAULT_LIBRARY_ROOT,
        description="Library base directory. All library-internal paths default to subdirs of this.",
    )
    autoimport_folder: Path = Field(
        default=Path.home() / "Music" / "AutoImport",
        description="Watched folder where incoming files land before routing. Independent of library_root.",
    )
    songs_dir: Path = Field(
        default=_DEFAULT_LIBRARY_ROOT / "Songs",
        description="Destination root for genre-routed tracks. Defaults to library_root/Songs.",
    )
    quarantine_dir: Path = Field(
        default=_DEFAULT_LIBRARY_ROOT / "_Unsorted",
        description="Files that can't be routed land here. Defaults to library_root/_Unsorted.",
    )
    fingerprint_db_path: Path = Field(
        default=_DEFAULT_LIBRARY_ROOT / ".musicsort" / "fingerprints.db",
        description="SQLite cache of sha256 + chromaprint. Defaults to library_root/.musicsort/fingerprints.db.",
    )
    audio_extensions: tuple[str, ...] = Field(
        default=(".mp3", ".wav", ".m4a", ".aif", ".aiff", ".flac"),
        description="Lowercased file extensions treated as audio.",
    )
    taxonomy_path: Path = Field(
        default=_PACKAGE_ROOT / "autoimport" / "genres.yaml",
        description=(
            "Path to the genre-category definitions YAML. Source-specific "
            "alias mapping files are auto-loaded from a `genres/` subdir "
            "next to this file."
        ),
    )
    similarity_threshold: float = Field(
        default=0.95,
        description="Chromaprint similarity above this counts as 'same recording'.",
    )
    watch_settle_seconds: float = Field(
        default=2.0,
        description="Seconds of no events on a path before the watcher processes it.",
    )
    watch_poll_seconds: float = Field(
        default=0.5,
        description="How often the watcher checks the settler for ready paths.",
    )
    rekordbox_enabled: bool = Field(
        default=True,
        description="Master switch for the Rekordbox auto-import stage. Disable to route files only.",
    )
    rekordbox_master_db_path: Path | None = Field(
        default=None,
        description=(
            "Override path to Rekordbox master.db. If None, pyrekordbox autodetects via "
            "Pioneer's options.json (typically ~/Library/Pioneer/rekordbox/master.db)."
        ),
    )
    rekordbox_playlist_parent: str = Field(
        default="musicsort playlists",
        description=(
            "Top-level Rekordbox playlist folder for the auto-generated playlist tree. "
            "Genres and Decades nest under this. Pick a unique name so the user can "
            "identify and remove all auto-generated playlists at the parent level."
        ),
    )
    rekordbox_genres_folder: str = Field(
        default="Genres",
        description="Sub-folder under `rekordbox_playlist_parent` for per-genre playlists.",
    )
    rekordbox_decades_folder: str = Field(
        default="Decades",
        description="Sub-folder under `rekordbox_playlist_parent` for per-decade playlists.",
    )
    rekordbox_playlist_fanout: int = Field(
        default=3,
        description=(
            "Cap on distinct genre playlists per track when `playlists --genres` runs. "
            "Same-category de-duped."
        ),
    )
    rekordbox_batch_size: int | None = Field(
        default=None,
        description=(
            "Max tracks to import per drain cycle. None = unlimited. Set to a small "
            "number only if Rekordbox crashes during analysis on large batches."
        ),
    )
    rekordbox_backup_dir: Path = Field(
        default=_DEFAULT_LIBRARY_ROOT / ".musicsort" / "rekordbox_backups",
        description="Where master.db tarball snapshots are stored before each drain.",
    )
    rekordbox_backup_retention: int = Field(
        default=10,
        description="Keep this many newest backup tarballs; prune older after each drain.",
    )

    @model_validator(mode="after")
    def _derive_library_subpaths(self) -> Settings:
        """If `library_root` was explicitly set but the derived subpaths weren't,
        rebuild the subpaths from the new root. Lets the user override just
        `MUSICSORT_LIBRARY_ROOT` and have everything follow."""
        if "library_root" not in self.model_fields_set:
            return self
        root = self.library_root
        if "songs_dir" not in self.model_fields_set:
            self.songs_dir = root / "Songs"
        if "quarantine_dir" not in self.model_fields_set:
            self.quarantine_dir = root / "_Unsorted"
        if "fingerprint_db_path" not in self.model_fields_set:
            self.fingerprint_db_path = root / ".musicsort" / "fingerprints.db"
        if "rekordbox_backup_dir" not in self.model_fields_set:
            self.rekordbox_backup_dir = root / ".musicsort" / "rekordbox_backups"
        return self


def get_settings() -> Settings:
    """Factory so tests can override env vars before instantiation."""
    return Settings()
