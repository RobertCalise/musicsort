"""Quality scoring for resolving fingerprint-duplicate tracks.

When two files fingerprint-match (same recording, different encoding), the
mover picks a winner using `compare(score(a), score(b))`. The score is a
tuple of (codec_lossless, bitrate_kbps, sample_rate_hz, channels, size_bytes)
compared lexicographically — lossless always beats lossy, then higher bitrate,
then higher sample rate, then more channels, then larger size as the final
tiebreaker. Tuple equality means no meaningful upgrade exists in either
direction and the existing file is kept.
"""

from __future__ import annotations

from dataclasses import dataclass

from musicsort.autoimport.reader import FileInfo


@dataclass(frozen=True)
class QualityScore:
    """Ordered tuple proxy for "which encoding is better." Unknown fields = 0."""

    codec_lossless: bool
    bitrate_kbps: int
    sample_rate_hz: int
    channels: int
    size_bytes: int

    def as_tuple(self) -> tuple[bool, int, int, int, int]:
        return (
            self.codec_lossless,
            self.bitrate_kbps,
            self.sample_rate_hz,
            self.channels,
            self.size_bytes,
        )


def score(info: FileInfo) -> QualityScore:
    return QualityScore(
        codec_lossless=info.codec_lossless,
        bitrate_kbps=info.bitrate_kbps or 0,
        sample_rate_hz=info.sample_rate_hz or 0,
        channels=info.channels or 0,
        size_bytes=info.size_bytes,
    )


def compare(a: QualityScore, b: QualityScore) -> int:
    """Return -1 if a < b, 1 if a > b, 0 on tuple equality (no meaningful upgrade)."""
    ta, tb = a.as_tuple(), b.as_tuple()
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0
