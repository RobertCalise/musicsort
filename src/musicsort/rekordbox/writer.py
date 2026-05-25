"""Thin pyrekordbox wrapper used by the drain and the playlists subcommand.

Two responsibilities, one open `master.db` session:

  - `import_track` — insert a routed file as a `DjmdContent` row. Used by
    the drain to keep the Rekordbox *collection* in sync with what the
    watcher routes. No playlist side effects.

  - `ensure_*_playlist` + `add_track_to_playlist` — manage the playlist
    tree (`musicsort playlists/Genres/<family>/<genre>` and
    `musicsort playlists/Decades/<NNs>`). Called by the playlists
    subcommand, never by the drain.

Doesn't enforce the "Rekordbox is not running" or "backup taken" rules —
those are the drain's job. `commit()` propagates any RuntimeError raised
by pyrekordbox when Rekordbox started between open and commit, so callers
can fail loudly without partial writes.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Self

from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6.tables import DjmdContent, DjmdPlaylist

from musicsort.autoimport.reader import read_file

# Rekordbox playlist hierarchy uses an integer Attribute column to
# distinguish folders (1) from regular playlists (0).
_ATTRIBUTE_PLAYLIST = 0
_ATTRIBUTE_FOLDER = 1


class ImportOutcome(StrEnum):
    """Per-track result of `RekordboxWriter.import_track`."""

    ALREADY_PRESENT = "already_present"
    INSERTED_NEW = "inserted_new"


class RekordboxWriter:
    """Context-managed wrapper over a single Rekordbox DB session.

    Use inside `with RekordboxWriter(...) as w:` so the underlying
    SQLAlchemy session is always closed, even on partial-import failures.
    Caller is responsible for `commit()`; not calling it discards
    in-memory changes via the `__exit__` close.
    """

    def __init__(
        self,
        master_db: Path | None,
        playlist_parent: str = "musicsort playlists",
        genres_folder: str = "Genres",
        decades_folder: str = "Decades",
    ) -> None:
        self._master_db_path = master_db
        self._playlist_parent_name = playlist_parent
        self._genres_folder_name = genres_folder
        self._decades_folder_name = decades_folder
        self._db: Rekordbox6Database | None = None
        self._parent_folder: DjmdPlaylist | None = None
        self._genres_root: DjmdPlaylist | None = None
        self._decades_root: DjmdPlaylist | None = None
        self._family_folders: dict[str, DjmdPlaylist] = {}
        self._genre_playlists: dict[str, DjmdPlaylist] = {}
        self._decade_playlists: dict[str, DjmdPlaylist] = {}
        # Per-playlist set of member content IDs. Lazily populated on first
        # `add_track_to_playlist` for a given playlist so we don't pay the full
        # `get_playlist_contents(...).all()` materialization on every track
        # during a bulk `rekordbox playlists` sync (was O(N) per add).
        self._playlist_member_ids: dict[str, set[str]] = {}

    def __enter__(self) -> Self:
        if self._master_db_path is not None:
            self._db = Rekordbox6Database(path=self._master_db_path)
        else:
            self._db = Rekordbox6Database()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Collection insert (drain path)
    # ------------------------------------------------------------------

    def import_track(self, library_path: Path) -> ImportOutcome:
        """Insert one routed track as a DjmdContent row.

        Idempotent on `library_path`: if the file is already in Rekordbox
        (matched by FolderPath), returns `ALREADY_PRESENT` without
        touching the database. Otherwise inserts the track with `Title`
        populated from ID3 (other metadata fills in when Rekordbox
        analyses the file). No playlist side effects — `playlists`
        subcommand handles that.
        """
        db = self._require_open()

        existing = db.get_content(FolderPath=str(library_path)).first()
        if existing is not None:
            return ImportOutcome.ALREADY_PRESENT

        kwargs: dict[str, str] = {}
        title = self._read_title(library_path)
        if title:
            kwargs["Title"] = title

        db.add_content(library_path, **kwargs)
        return ImportOutcome.INSERTED_NEW

    # ------------------------------------------------------------------
    # Collection query (playlists path)
    # ------------------------------------------------------------------

    def get_content_for_path(self, library_path: Path) -> DjmdContent | None:
        """Look up a track in the collection by its on-disk path."""
        db = self._require_open()
        return db.get_content(FolderPath=str(library_path)).first()

    # ------------------------------------------------------------------
    # Playlist management (playlists subcommand path)
    # ------------------------------------------------------------------

    def ensure_genre_playlist(
        self,
        genre_name: str,
        family: str | None,
    ) -> DjmdPlaylist:
        """Ensure the playlist exists at
        `<parent>/<genres>/[<family>/]<genre_name>` and return it.

        Family-less categories (Pop, Rock, etc.) nest directly under
        `<parent>/<genres>/`. Categories with a family (Tech House under
        House) nest under `<parent>/<genres>/<family>/<genre_name>`.
        """
        cache_key = f"{family or '_root'}/{genre_name}"
        if cache_key in self._genre_playlists:
            return self._genre_playlists[cache_key]

        genres_root = self._ensure_genres_root()
        parent_for_playlist = (
            self._ensure_family_folder(family, genres_root) if family is not None else genres_root
        )

        existing = self._find_child_playlist(parent_for_playlist, genre_name)
        if existing is not None:
            self._genre_playlists[cache_key] = existing
            return existing

        db = self._require_open()
        created = db.create_playlist(genre_name, parent=parent_for_playlist)
        self._genre_playlists[cache_key] = created
        return created

    def ensure_decade_playlist(self, decade_label: str) -> DjmdPlaylist:
        """Ensure `<parent>/<decades>/<decade_label>` exists; return it."""
        if decade_label in self._decade_playlists:
            return self._decade_playlists[decade_label]

        decades_root = self._ensure_decades_root()
        existing = self._find_child_playlist(decades_root, decade_label)
        if existing is not None:
            self._decade_playlists[decade_label] = existing
            return existing

        db = self._require_open()
        created = db.create_playlist(decade_label, parent=decades_root)
        self._decade_playlists[decade_label] = created
        return created

    def add_track_to_playlist(
        self,
        content: DjmdContent,
        playlist: DjmdPlaylist,
    ) -> bool:
        """Append `content` to `playlist` if not already there.

        Returns True if the track was newly added, False if it was
        already a member of the playlist (idempotent). Membership is
        looked up against a per-playlist cache of content IDs, lazily
        populated on first call — avoids O(members) materialization on
        every add during a bulk `rekordbox playlists` sync.
        """
        db = self._require_open()
        members = self._playlist_member_ids.get(playlist.ID)
        if members is None:
            members = {c.ID for c in db.get_playlist_contents(playlist).all()}
            self._playlist_member_ids[playlist.ID] = members
        if content.ID in members:
            return False
        db.add_to_playlist(playlist, content)
        members.add(content.ID)
        return True

    # ------------------------------------------------------------------
    # Transaction control
    # ------------------------------------------------------------------

    def commit(self) -> None:
        """Flush all queued changes to disk.

        Raises RuntimeError (from pyrekordbox) if Rekordbox is now
        running. Callers should let that propagate so they can fail
        cleanly without partial writes.
        """
        self._require_open().commit()

    def rollback(self) -> None:
        """Discard all pending changes in the current session.

        Used after a per-track failure to wipe partial state so the next
        track in the batch starts clean. Drops all lazy-cached folder /
        playlist objects since they may now refer to detached rows.
        """
        db = self._require_open()
        db.rollback()
        self._parent_folder = None
        self._genres_root = None
        self._decades_root = None
        self._family_folders.clear()
        self._genre_playlists.clear()
        self._decade_playlists.clear()
        self._playlist_member_ids.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_open(self) -> Rekordbox6Database:
        if self._db is None:
            raise RuntimeError(
                "RekordboxWriter must be used as a context manager; "
                "the database session is only open inside `with` block."
            )
        return self._db

    def _ensure_parent_folder(self) -> DjmdPlaylist:
        if self._parent_folder is not None:
            return self._parent_folder
        db = self._require_open()
        existing = db.get_playlist(
            Name=self._playlist_parent_name, Attribute=_ATTRIBUTE_FOLDER
        ).first()
        if existing is not None:
            self._parent_folder = existing
            return existing
        self._parent_folder = db.create_playlist_folder(self._playlist_parent_name)
        return self._parent_folder

    def _ensure_genres_root(self) -> DjmdPlaylist:
        if self._genres_root is not None:
            return self._genres_root
        parent = self._ensure_parent_folder()
        existing = self._find_child_folder(parent, self._genres_folder_name)
        if existing is not None:
            self._genres_root = existing
            return existing
        db = self._require_open()
        self._genres_root = db.create_playlist_folder(self._genres_folder_name, parent=parent)
        return self._genres_root

    def _ensure_decades_root(self) -> DjmdPlaylist:
        if self._decades_root is not None:
            return self._decades_root
        parent = self._ensure_parent_folder()
        existing = self._find_child_folder(parent, self._decades_folder_name)
        if existing is not None:
            self._decades_root = existing
            return existing
        db = self._require_open()
        self._decades_root = db.create_playlist_folder(self._decades_folder_name, parent=parent)
        return self._decades_root

    def _ensure_family_folder(
        self,
        family_name: str,
        genres_root: DjmdPlaylist,
    ) -> DjmdPlaylist:
        if family_name in self._family_folders:
            return self._family_folders[family_name]
        existing = self._find_child_folder(genres_root, family_name)
        if existing is not None:
            self._family_folders[family_name] = existing
            return existing
        db = self._require_open()
        created = db.create_playlist_folder(family_name, parent=genres_root)
        self._family_folders[family_name] = created
        return created

    def _find_child_folder(
        self,
        parent: DjmdPlaylist,
        name: str,
    ) -> DjmdPlaylist | None:
        db = self._require_open()
        return db.get_playlist(Name=name, ParentID=parent.ID, Attribute=_ATTRIBUTE_FOLDER).first()

    def _find_child_playlist(
        self,
        parent: DjmdPlaylist,
        name: str,
    ) -> DjmdPlaylist | None:
        db = self._require_open()
        return db.get_playlist(Name=name, ParentID=parent.ID, Attribute=_ATTRIBUTE_PLAYLIST).first()

    @staticmethod
    def _read_title(library_path: Path) -> str | None:
        info = read_file(library_path)
        title = info.tags.title
        if title is None or not title.strip():
            return None
        return title.strip()
