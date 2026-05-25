"""Tests for the audio file reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from musicsort.autoimport.categorizer import MatchKind, categorize
from musicsort.autoimport.reader import FileInfo, TrackTags, read_file
from musicsort.autoimport.taxonomy import load_taxonomy
from musicsort.config import get_settings


def test_read_mp3_tagged(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["mp3_tagged"])
    assert isinstance(info, FileInfo)
    assert info.format == "mp3"
    assert info.reader == "mutagen"
    assert info.size_bytes > 0
    assert info.duration_seconds is not None and info.duration_seconds > 0
    assert info.bitrate_kbps is not None
    assert info.sample_rate_hz == 44100
    assert info.channels == 2
    assert info.codec_lossless is False
    assert info.tags == TrackTags(
        title="Strobe",
        artist="deadmau5",
        album="For Lack Of A Better Name",
        genre="House",
        year=2009,
        bpm=128.0,
        key="Abm",
        label="mau5trap",
    )


def test_read_mp3_empty(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["mp3_empty"])
    assert info.format == "mp3"
    assert info.reader == "mutagen"
    assert info.tags == TrackTags()


def test_read_wav_tagged(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["wav_tagged"])
    assert info.format == "wav"
    assert info.reader == "mutagen"
    assert info.codec_lossless is True
    assert info.tags.title == "Got To Be"
    assert info.tags.artist == "Tigerblind"
    assert info.tags.genre == "EDM"
    assert info.tags.bpm == 132.0
    assert info.tags.key == "Bbm"


def test_read_wav_empty(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["wav_empty"])
    assert info.format == "wav"
    assert info.codec_lossless is True
    assert info.tags == TrackTags()


def test_read_wav_serato_ignores_geob_frames(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["wav_serato"])
    assert info.format == "wav"
    assert info.reader == "mutagen"
    assert info.tags.bpm == 128.0
    assert info.tags.key == "Am"


def test_read_m4a_tagged(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["m4a_tagged"])
    assert info.format == "m4a"
    assert info.reader == "mutagen"
    assert info.codec_lossless is False
    assert info.tags.title == "m4a title"
    assert info.tags.artist == "m4a artist"
    assert info.tags.genre == "Tech House"
    assert info.tags.year == 2022
    assert info.tags.bpm == 124.0


def test_read_m4a_empty(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["m4a_empty"])
    assert info.format == "m4a"
    assert info.tags == TrackTags()


def test_read_aiff_tagged(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["aiff_tagged"])
    assert info.format == "aif"
    assert info.reader == "mutagen"
    assert info.codec_lossless is True
    assert info.tags.title == "aiff title"
    assert info.tags.artist == "aiff artist"


def test_read_aiff_empty(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["aiff_empty"])
    assert info.format == "aif"
    assert info.codec_lossless is True
    assert info.tags == TrackTags()


def test_read_flac_tagged(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["flac_tagged"])
    assert info.format == "flac"
    assert info.reader == "mutagen"
    assert info.codec_lossless is True
    assert info.tags.title == "flac title"
    assert info.tags.artist == "flac artist"
    assert info.tags.album == "flac album"
    assert info.tags.genre == "Techno"
    assert info.tags.year == 2023
    assert info.tags.bpm == 130.0


def test_read_flac_empty(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["flac_empty"])
    assert info.format == "flac"
    assert info.codec_lossless is True
    assert info.tags == TrackTags()


def test_read_truncated_does_not_raise(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["truncated"])
    assert info.format == "mp3"
    assert info.reader in {"ffprobe", "unreadable"}
    assert info.tags == TrackTags()


def test_read_non_audio_file_does_not_raise(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["not_audio"])
    assert info.format == "wav"
    assert info.size_bytes > 0
    assert info.reader in {"ffprobe", "unreadable"}


def test_read_zero_byte_file_does_not_raise(audio_fixtures: dict[str, Path]) -> None:
    info = read_file(audio_fixtures["zero"])
    assert info.format == "wav"
    assert info.size_bytes == 0
    assert info.reader in {"ffprobe", "unreadable"}


def test_read_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_file(tmp_path / "nope.mp3")


def test_reader_to_categorizer_integration(audio_fixtures: dict[str, Path]) -> None:
    """End-to-end: real file -> read -> categorize -> matched destination."""
    taxonomy = load_taxonomy(get_settings().taxonomy_path)
    info = read_file(audio_fixtures["mp3_tagged"])
    match = categorize(info.tags, taxonomy)
    assert match.kind is MatchKind.MATCHED
    assert match.primary is not None
    assert match.primary.folder == "House"
