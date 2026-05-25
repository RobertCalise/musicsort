"""Integration tests for the mover orchestrator."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from mutagen.mp3 import MP3

from musicsort.autoimport.fingerprint_db import FingerprintDB
from musicsort.autoimport.mover import MoveAction, move_one, plan_move
from musicsort.autoimport.quarantine import Quarantiner
from musicsort.autoimport.taxonomy import load_taxonomy
from musicsort.config import Settings, get_settings


def _settings(library_root: Path) -> Settings:
    """Build Settings rooted at a synthetic library tree."""
    return Settings(
        library_root=library_root,
        autoimport_folder=library_root / "AutoImport",
        songs_dir=library_root / "Songs",
        quarantine_dir=library_root / "_Unsorted",
        fingerprint_db_path=library_root / ".musicsort" / "fingerprints.db",
    )


@pytest.fixture
def setup(populated_library: Path):
    """Wire up Settings, taxonomy, FingerprintDB, and Quarantiner against the library."""
    settings = _settings(populated_library)
    settings.autoimport_folder.mkdir(parents=True, exist_ok=True)
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    db = FingerprintDB(settings.fingerprint_db_path)
    quarantiner = Quarantiner(settings.quarantine_dir)
    yield settings, taxonomy, db, quarantiner
    db.close()


def _drop_into_autoimport(src: Path, autoimport: Path) -> Path:
    autoimport.mkdir(parents=True, exist_ok=True)
    dst = autoimport / src.name
    shutil.copy(src, dst)
    return dst


def test_new_file_with_recognized_genre_is_moved(
    setup, audio_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    """Tag an empty MP3 (different audio content from pre-shelved file) with House and import."""
    settings, taxonomy, db, quarantiner = setup
    src = tmp_path / "new_arrival.mp3"
    src.write_bytes(audio_fixtures["mp3_empty"].read_bytes())
    m = MP3(src)
    if m.tags is None:
        m.add_tags()
    from mutagen.id3 import TCON, TDRC

    m.tags.add(TCON(encoding=3, text="House"))
    m.tags.add(TDRC(encoding=3, text="2010"))
    m.save()
    incoming = _drop_into_autoimport(src, settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert (
        result.action is MoveAction.MOVED
    ), f"expected MOVED but got {result.action.value} ({result.reason}: {result.detail})"
    assert result.dst is not None
    assert result.dst.parent == settings.songs_dir / "House"
    assert result.dst.exists()
    assert not incoming.exists()
    # library_target == dst for the successful-move case.
    assert result.library_target == result.dst


def test_byte_identical_dup_goes_to_quarantine(setup, audio_fixtures: dict[str, Path]) -> None:
    settings, taxonomy, db, quarantiner = setup
    # Drop the exact same file that's already shelved at Songs/House/existing_house.mp3.
    incoming = _drop_into_autoimport(audio_fixtures["mp3_tagged"], settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert result.action is MoveAction.QUARANTINED
    assert result.reason == "duplicate"
    assert result.dst is not None
    assert result.dst.parent == settings.quarantine_dir / "duplicate"
    assert (settings.songs_dir / "House" / "existing_house.mp3").exists()
    # library_target carries the existing library file's path so callers can
    # reference it (e.g. inspect output, downstream tools).
    assert result.library_target == settings.songs_dir / "House" / "existing_house.mp3"


def test_no_genre_file_quarantines(setup, audio_fixtures: dict[str, Path]) -> None:
    settings, taxonomy, db, quarantiner = setup
    incoming = _drop_into_autoimport(audio_fixtures["mp3_empty"], settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert result.action is MoveAction.QUARANTINED
    assert result.reason == "no_genre"
    assert result.dst is not None
    # Action-required quarantine with no pack lands in _Unsorted/<reason>/.
    assert result.dst.parent == settings.quarantine_dir / "no_genre"


def test_unknown_genre_quarantines(setup, audio_fixtures: dict[str, Path], tmp_path: Path) -> None:
    settings, taxonomy, db, quarantiner = setup
    # Copy an mp3 and re-tag with a genre not in the taxonomy.
    src = tmp_path / "polka.mp3"
    src.write_bytes(audio_fixtures["mp3_empty"].read_bytes())
    m = MP3(src)
    if m.tags is None:
        m.add_tags()
    from mutagen.id3 import TCON

    m.tags.add(TCON(encoding=3, text="Polka"))
    m.save()
    incoming = _drop_into_autoimport(src, settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert result.action is MoveAction.QUARANTINED
    assert result.reason == "unknown_genre"


def test_year_gated_genre_without_year_routes_to_missing_year(
    setup, audio_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    """A tag whose categories are all year-gated lands in
    `_Unsorted/missing_year/` so the user knows the fix is adding a year tag,
    not chasing the genre. The `pop` alias maps to both `Pop` (year_gte: 2000)
    and `Pop (80s/90s)` (year_lt: 2000) — with no year tag, both are
    year-blocked."""
    settings, taxonomy, db, quarantiner = setup
    src = tmp_path / "year_less_pop.mp3"
    src.write_bytes(audio_fixtures["mp3_empty"].read_bytes())
    m = MP3(src)
    if m.tags is None:
        m.add_tags()
    from mutagen.id3 import TCON

    m.tags.add(TCON(encoding=3, text="Pop"))
    # Deliberately no TDRC — that's the trigger for this code path.
    m.save()
    incoming = _drop_into_autoimport(src, settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert result.action is MoveAction.QUARANTINED
    assert result.reason == "missing_year"
    assert result.dst is not None
    assert result.dst.parent == settings.quarantine_dir / "missing_year"


def test_manual_only_genre_quarantines(
    setup, audio_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    settings, taxonomy, db, quarantiner = setup
    src = tmp_path / "vocal.mp3"
    src.write_bytes(audio_fixtures["mp3_empty"].read_bytes())
    m = MP3(src)
    if m.tags is None:
        m.add_tags()
    from mutagen.id3 import TCON

    m.tags.add(TCON(encoding=3, text="Acapella"))
    m.save()
    incoming = _drop_into_autoimport(src, settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert result.action is MoveAction.QUARANTINED
    assert result.reason == "manual_only"


def test_unreadable_file_quarantines(setup, audio_fixtures: dict[str, Path]) -> None:
    settings, taxonomy, db, quarantiner = setup
    incoming = _drop_into_autoimport(audio_fixtures["zero"], settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert result.action is MoveAction.QUARANTINED
    assert result.reason == "unreadable"


def test_db_persists_indexed_rows(setup, audio_fixtures: dict[str, Path]) -> None:
    """A second move_one call against the same library reuses indexed rows for
    files whose mtime hasn't changed (the populated_library tracks)."""
    settings, taxonomy, db, quarantiner = setup
    # Pull in a new genre track first to populate the DB.
    drop1 = _drop_into_autoimport(audio_fixtures["mp3_tagged"], settings.autoimport_folder)
    drop1 = drop1.rename(drop1.with_name("first.mp3"))
    move_one(drop1, settings=settings, taxonomy=taxonomy, db=db, quarantiner=quarantiner)

    house_existing = settings.songs_dir / "House" / "existing_house.mp3"
    row_before = db.lookup_by_path(house_existing)
    assert row_before is not None
    indexed_at_before = row_before.indexed_at

    # Second call — nothing about the library changed, so the existing rows must
    # keep their indexed_at timestamp.
    drop2 = _drop_into_autoimport(audio_fixtures["wav_empty"], settings.autoimport_folder)
    move_one(drop2, settings=settings, taxonomy=taxonomy, db=db, quarantiner=quarantiner)

    row_after = db.lookup_by_path(house_existing)
    assert row_after is not None
    assert row_after.indexed_at == indexed_at_before


def test_better_quality_incoming_replaces_existing(setup, encode_mp3, tmp_path: Path) -> None:
    """Pre-shelve a low-bitrate encoding; drop in a high-bitrate encoding of the
    same source pink noise. Existing should move to quarantine; incoming takes
    its slot."""
    settings, taxonomy, db, quarantiner = setup
    techno_dir = settings.songs_dir / "Techno"
    existing = encode_mp3(techno_dir / "shared.mp3", bitrate_k=64, genre="Techno")
    incoming_src = encode_mp3(tmp_path / "shared.mp3", bitrate_k=320, genre="Techno")
    incoming = _drop_into_autoimport(incoming_src, settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert (
        result.action is MoveAction.REPLACED
    ), f"expected REPLACED but got {result.action.value} ({result.reason}: {result.detail})"
    assert result.dst == existing  # incoming takes existing's path
    assert result.dst.exists()
    assert (settings.quarantine_dir / "duplicate" / "shared.mp3").exists()


def test_worse_quality_incoming_loses(setup, encode_mp3, tmp_path: Path) -> None:
    settings, taxonomy, db, quarantiner = setup
    techno_dir = settings.songs_dir / "Techno"
    existing = encode_mp3(techno_dir / "shared.mp3", bitrate_k=320, genre="Techno")
    incoming_src = encode_mp3(tmp_path / "shared.mp3", bitrate_k=64, genre="Techno")
    incoming = _drop_into_autoimport(incoming_src, settings.autoimport_folder)

    result = move_one(
        incoming,
        settings=settings,
        taxonomy=taxonomy,
        db=db,
        quarantiner=quarantiner,
    )
    assert (
        result.action is MoveAction.QUARANTINED
    ), f"expected QUARANTINED but got {result.action.value} ({result.reason}: {result.detail})"
    assert result.reason == "worse_quality"
    assert result.dst is not None
    assert result.dst.parent == settings.quarantine_dir / "duplicate"
    assert existing.exists()  # existing untouched


# ---- plan_move (dry-run predictions) -----------------------------------------


def test_plan_move_predicts_new_file_move(
    setup, audio_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    """A new House-tagged file should predict MOVED with the expected destination,
    and not actually move."""
    settings, taxonomy, db, _quarantiner = setup
    src = tmp_path / "new.mp3"
    src.write_bytes(audio_fixtures["mp3_empty"].read_bytes())
    m = MP3(src)
    if m.tags is None:
        m.add_tags()
    from mutagen.id3 import TCON, TDRC

    m.tags.add(TCON(encoding=3, text="House"))
    m.tags.add(TDRC(encoding=3, text="2010"))
    m.save()
    incoming = _drop_into_autoimport(src, settings.autoimport_folder)

    plan = plan_move(incoming, settings=settings, taxonomy=taxonomy, db=db)
    assert plan.predicted_action is MoveAction.MOVED
    assert plan.predicted_dst == settings.songs_dir / "House" / incoming.name
    assert incoming.exists()
    assert not plan.predicted_dst.exists()


def test_plan_move_predicts_duplicate_quarantine(setup, audio_fixtures: dict[str, Path]) -> None:
    """A byte-identical copy of an existing track should predict QUARANTINED
    with reason 'duplicate', leaving both files in place."""
    settings, taxonomy, db, _quarantiner = setup
    incoming = _drop_into_autoimport(audio_fixtures["mp3_tagged"], settings.autoimport_folder)

    plan = plan_move(incoming, settings=settings, taxonomy=taxonomy, db=db)
    assert plan.predicted_action is MoveAction.QUARANTINED
    assert plan.reason == "duplicate"
    assert plan.predicted_dst == settings.quarantine_dir / "duplicate"
    assert incoming.exists()
    assert (settings.songs_dir / "House" / "existing_house.mp3").exists()
    assert not settings.quarantine_dir.exists()
