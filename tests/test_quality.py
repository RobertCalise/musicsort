"""Tests for quality scoring."""

from __future__ import annotations

from pathlib import Path

from musicsort.autoimport.quality import QualityScore, compare, score
from musicsort.autoimport.reader import FileInfo, TrackTags


def _info(
    *,
    fmt: str = "mp3",
    size: int = 1_000_000,
    bitrate: int | None = 320,
    sample_rate: int | None = 44100,
    channels: int | None = 2,
    lossless: bool = False,
) -> FileInfo:
    return FileInfo(
        path=Path("/tmp/x." + fmt),
        format=fmt,
        size_bytes=size,
        duration_seconds=180.0,
        bitrate_kbps=bitrate,
        sample_rate_hz=sample_rate,
        channels=channels,
        codec_lossless=lossless,
        reader="mutagen",
        tags=TrackTags(),
    )


def test_lossless_beats_lossy_regardless_of_bitrate() -> None:
    lossless = score(_info(fmt="wav", lossless=True, bitrate=1411))
    lossy_high = score(_info(fmt="mp3", lossless=False, bitrate=320))
    assert compare(lossless, lossy_high) == 1
    assert compare(lossy_high, lossless) == -1


def test_among_lossy_higher_bitrate_wins() -> None:
    lo = score(_info(bitrate=128))
    hi = score(_info(bitrate=320))
    assert compare(hi, lo) == 1
    assert compare(lo, hi) == -1


def test_sample_rate_is_tiebreaker_after_bitrate() -> None:
    base = score(_info(bitrate=320, sample_rate=44100))
    upgrade = score(_info(bitrate=320, sample_rate=48000))
    assert compare(upgrade, base) == 1


def test_channels_is_tiebreaker_after_sample_rate() -> None:
    mono = score(_info(bitrate=320, channels=1))
    stereo = score(_info(bitrate=320, channels=2))
    assert compare(stereo, mono) == 1


def test_size_is_final_tiebreaker() -> None:
    small = score(_info(bitrate=320, size=1_000_000))
    large = score(_info(bitrate=320, size=2_000_000))
    assert compare(large, small) == 1


def test_equal_quality_returns_zero() -> None:
    a = score(_info(bitrate=320))
    b = score(_info(bitrate=320))
    assert compare(a, b) == 0


def test_none_fields_treated_as_zero() -> None:
    missing = score(_info(bitrate=None, sample_rate=None, channels=None))
    populated = score(_info(bitrate=64))
    assert compare(populated, missing) == 1


def test_qualityscore_tuple_shape() -> None:
    q = QualityScore(
        codec_lossless=True,
        bitrate_kbps=1411,
        sample_rate_hz=44100,
        channels=2,
        size_bytes=12345,
    )
    assert q.as_tuple() == (True, 1411, 44100, 2, 12345)
