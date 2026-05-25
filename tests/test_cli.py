"""Tests for the musicsort CLI subcommands `once` and `inspect`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from mutagen.id3 import TCON, TDRC
from mutagen.mp3 import MP3
from typer.testing import CliRunner

from musicsort.autoimport.cli import app

runner = CliRunner()


def _tag_house(src: Path) -> Path:
    m = MP3(src)
    if m.tags is None:
        m.add_tags()
    m.tags.add(TCON(encoding=3, text="House"))
    m.tags.add(TDRC(encoding=3, text="2010"))  # House requires year>=2000
    m.save()
    return src


# ---- once --------------------------------------------------------------------


def test_once_empty_autoimport(cli_env: Path) -> None:
    result = runner.invoke(app, ["once"])
    assert result.exit_code == 0
    assert "No audio files found" in result.output


def test_once_routes_tagged_file(cli_env: Path, audio_fixtures: dict[str, Path]) -> None:
    incoming = cli_env / "AutoImport" / "fresh.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], incoming)
    _tag_house(incoming)

    result = runner.invoke(app, ["once"])
    assert result.exit_code == 0, result.output
    assert "MOVED" in result.output
    assert "Summary" in result.output
    assert (cli_env / "Songs" / "House" / "fresh.mp3").exists()
    assert not incoming.exists()


def test_once_mixed_batch(cli_env: Path, audio_fixtures: dict[str, Path]) -> None:
    # File 1: new House track → MOVED.
    fresh = cli_env / "AutoImport" / "fresh.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], fresh)
    _tag_house(fresh)

    # File 2: byte-identical copy of the pre-shelved Songs/House/existing_house.mp3
    #   → QUARANTINED duplicate.
    dup = cli_env / "AutoImport" / "dup.mp3"
    shutil.copy(audio_fixtures["mp3_tagged"], dup)

    # File 3: no genre → QUARANTINED no_genre.
    untagged = cli_env / "AutoImport" / "untagged.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], untagged)

    result = runner.invoke(app, ["once"])
    assert result.exit_code == 0, result.output
    assert "MOVED" in result.output
    assert "QUARANTINED" in result.output
    assert "moved: 1" in result.output
    assert "quarantined: 2" in result.output
    assert "duplicate=1" in result.output
    assert "no_genre=1" in result.output


def test_once_source_override(
    cli_env: Path, audio_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    """--source overrides MUSICSORT_AUTOIMPORT_FOLDER."""
    alt_source = tmp_path / "alternate"
    alt_source.mkdir()
    incoming = alt_source / "from_alt.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], incoming)
    _tag_house(incoming)

    result = runner.invoke(app, ["once", "--source", str(alt_source)])
    assert result.exit_code == 0, result.output
    assert "MOVED" in result.output
    assert (cli_env / "Songs" / "House" / "from_alt.mp3").exists()


# ---- inspect -----------------------------------------------------------------


def test_inspect_empty_autoimport(cli_env: Path) -> None:
    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 0
    assert "No audio files found" in result.output


def test_inspect_predicts_move_without_moving(
    cli_env: Path, audio_fixtures: dict[str, Path]
) -> None:
    incoming = cli_env / "AutoImport" / "fresh.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], incoming)
    _tag_house(incoming)

    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 0, result.output
    assert "would MOVE" in result.output
    # Crucially: file did NOT move.
    assert incoming.exists()
    assert not (cli_env / "Songs" / "House" / "fresh.mp3").exists()


def test_inspect_predicts_quarantine_for_no_genre(
    cli_env: Path, audio_fixtures: dict[str, Path]
) -> None:
    incoming = cli_env / "AutoImport" / "untagged.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], incoming)

    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 0, result.output
    assert "would QUARANTINE" in result.output
    assert "no_genre" in result.output
    assert incoming.exists()
    assert not (cli_env / "_Unsorted").exists()


def test_inspect_is_idempotent(cli_env: Path, audio_fixtures: dict[str, Path]) -> None:
    incoming = cli_env / "AutoImport" / "fresh.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], incoming)
    _tag_house(incoming)

    first = runner.invoke(app, ["inspect"])
    second = runner.invoke(app, ["inspect"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "would MOVE" in first.output
    assert "would MOVE" in second.output
    # File still in AutoImport after two inspect calls.
    assert incoming.exists()


# ---- empty-folder cleanup ----------------------------------------------------


def test_once_removes_empty_subfolder_after_route(
    cli_env: Path, audio_fixtures: dict[str, Path]
) -> None:
    """When a routed file's parent dir under AutoImport becomes empty,
    the dir gets cleaned up (but AutoImport root is preserved)."""
    sub_dir = cli_env / "AutoImport" / "tmp_subdir"
    sub_dir.mkdir(parents=True)
    src = sub_dir / "track.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], src)
    _tag_house(src)

    result = runner.invoke(app, ["once"])
    assert result.exit_code == 0, result.output
    assert not sub_dir.exists()
    assert (cli_env / "AutoImport").is_dir()


def test_once_removes_subfolder_even_with_ds_store(
    cli_env: Path, audio_fixtures: dict[str, Path]
) -> None:
    sub_dir = cli_env / "AutoImport" / "tmp_subdir"
    sub_dir.mkdir(parents=True)
    src = sub_dir / "track.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], src)
    _tag_house(src)
    # Simulate a Finder-leftover .DS_Store.
    (sub_dir / ".DS_Store").write_bytes(b"\x00")

    result = runner.invoke(app, ["once"])
    assert result.exit_code == 0, result.output
    assert not sub_dir.exists()


def test_once_preserves_subfolder_with_non_audio_sibling(
    cli_env: Path, audio_fixtures: dict[str, Path]
) -> None:
    """A subfolder containing a non-audio non-junk file should survive cleanup."""
    sub_dir = cli_env / "AutoImport" / "tmp_subdir"
    sub_dir.mkdir(parents=True)
    src = sub_dir / "track.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], src)
    _tag_house(src)
    notes = sub_dir / "notes.txt"
    notes.write_text("some non-audio sibling", encoding="utf-8")

    result = runner.invoke(app, ["once"])
    assert result.exit_code == 0, result.output
    # Notes file and subdir both survive; only the audio file moved.
    assert notes.exists()
    assert sub_dir.is_dir()
    assert not src.exists()  # the audio routed out


# ---- audit -------------------------------------------------------------------


def test_audit_empty_songs_dir(cli_env: Path) -> None:
    # populated_library already exists with House/ and Techno/ pre-shelved.
    # Strip those for the "empty" case.
    shutil.rmtree(cli_env / "Songs" / "House")
    shutil.rmtree(cli_env / "Songs" / "Techno")
    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0, result.output
    assert "Scanned 0 file(s)" in result.output


def test_audit_reports_mis_shelved(cli_env: Path, audio_fixtures: dict[str, Path]) -> None:
    misshelved = cli_env / "Songs" / "Techno" / "wrong.mp3"
    shutil.copy(audio_fixtures["mp3_empty"], misshelved)
    m = MP3(misshelved)
    if m.tags is None:
        m.add_tags()
    m.tags.delall("TCON")
    m.tags.add(TCON(encoding=3, text="House"))
    m.tags.add(TDRC(encoding=3, text="2010"))
    m.save()

    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0, result.output
    assert "Mis-shelved" in result.output
    assert "wrong.mp3" in result.output


# ---- help / wiring -----------------------------------------------------------


@pytest.mark.parametrize(
    "subcmd",
    [
        "once",
        "inspect",
        "audit",
        "watch",
        "install",
        "uninstall",
        "status",
    ],
)
def test_subcommand_help_exits_zero(subcmd: str) -> None:
    result = runner.invoke(app, [subcmd, "--help"])
    assert result.exit_code == 0
    assert subcmd in result.output


def test_watch_help_mentions_settle() -> None:
    result = runner.invoke(app, ["watch", "--help"])
    assert result.exit_code == 0
    assert "settle" in result.output.lower()
