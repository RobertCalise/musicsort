"""Tests for the chromaprint fingerprinter."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from musicsort.autoimport.fingerprinter import (
    Fingerprint,
    FingerprinterError,
    compare,
    deserialize,
    fingerprint_file,
    serialize,
)

pytestmark = pytest.mark.skipif(
    shutil.which("fpcalc") is None,
    reason="fpcalc (chromaprint) not installed",
)


def test_fingerprint_mp3_tagged(audio_fixtures: dict[str, Path]) -> None:
    fp = fingerprint_file(audio_fixtures["mp3_tagged"])
    assert isinstance(fp, Fingerprint)
    assert fp.duration_seconds > 0
    assert len(fp.fingerprint) > 0


def test_fingerprint_is_deterministic(audio_fixtures: dict[str, Path]) -> None:
    a = fingerprint_file(audio_fixtures["mp3_tagged"])
    b = fingerprint_file(audio_fixtures["mp3_tagged"])
    assert a.fingerprint == b.fingerprint
    assert a.duration_seconds == b.duration_seconds


def test_fingerprint_distinguishes_audio_content(audio_fixtures: dict[str, Path]) -> None:
    """Two fixture files with independent random pink noise produce different fingerprints."""
    mp3 = fingerprint_file(audio_fixtures["mp3_tagged"]).fingerprint
    wav = fingerprint_file(audio_fixtures["wav_tagged"]).fingerprint
    assert mp3 != wav


def test_compare_identical_fingerprints_is_one(audio_fixtures: dict[str, Path]) -> None:
    fp = fingerprint_file(audio_fixtures["mp3_tagged"])
    assert compare(fp, fp) == pytest.approx(1.0)


def test_compare_distinct_fingerprints_is_below_threshold(
    audio_fixtures: dict[str, Path],
) -> None:
    a = fingerprint_file(audio_fixtures["mp3_tagged"])
    b = fingerprint_file(audio_fixtures["wav_tagged"])
    assert compare(a, b) < 0.95


def test_serialize_round_trips(audio_fixtures: dict[str, Path]) -> None:
    fp = fingerprint_file(audio_fixtures["mp3_tagged"])
    roundtrip = deserialize(fp.duration_seconds, serialize(fp))
    assert roundtrip.fingerprint == fp.fingerprint
    assert roundtrip.duration_seconds == fp.duration_seconds


def test_fingerprint_raises_on_zero_byte(audio_fixtures: dict[str, Path]) -> None:
    with pytest.raises(FingerprinterError):
        fingerprint_file(audio_fixtures["zero"])


def test_fingerprint_raises_on_non_audio(audio_fixtures: dict[str, Path]) -> None:
    with pytest.raises(FingerprinterError):
        fingerprint_file(audio_fixtures["not_audio"])
