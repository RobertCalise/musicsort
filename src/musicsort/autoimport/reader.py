"""Audio file reader: format + duration + tags.

`read_file(path)` opens a file via mutagen, falls back to ffprobe for files
mutagen can't parse, and always returns a `FileInfo` rather than raising —
unreadable inputs surface as `FileInfo` with `None` durations and empty
`TrackTags`, leaving the quarantine decision to the caller.

The `TrackTags` dataclass produced here is the same shape the Phase 1
categorizer consumes (it imports `TrackTags` from this module).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE
from pydantic import BaseModel, ConfigDict

_LOSSLESS_FORMATS = frozenset({"wav", "wave", "aif", "aiff", "flac"})

# ID3 frame -> TrackTags field name. Year is handled separately (TDRC/TYER/TDOR).
_ID3_TO_FIELD: dict[str, str] = {
    "TIT2": "title",
    "TPE1": "artist",
    "TALB": "album",
    "TCON": "genre",
    "TBPM": "bpm",
    "TKEY": "key",
    "TPUB": "label",
}

# MP4 atom -> TrackTags field name. Year handled separately (\xa9day).
_MP4_TO_FIELD: dict[str, str] = {
    "\xa9nam": "title",
    "\xa9ART": "artist",
    "\xa9alb": "album",
    "\xa9gen": "genre",
    "tmpo": "bpm",
    "----:com.apple.iTunes:initialkey": "key",
    "\xa9pub": "label",
}

# Vorbis comment (FLAC) keys are lowercase by spec.
_VORBIS_TO_FIELD: dict[str, str] = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "genre": "genre",
    "bpm": "bpm",
    "key": "key",
    "initialkey": "key",
    "label": "label",
    "publisher": "label",
}

_YEAR_RE = re.compile(r"^(\d{4})")


@dataclass(frozen=True)
class TrackTags:
    """Normalized tag view shared by the reader (producer) and the categorizer
    (consumer). All fields optional — every reader path can produce a partial
    view, and the categorizer only consults `genre` and `year`."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    year: int | None = None
    bpm: float | None = None
    key: str | None = None
    label: str | None = None


class FileInfo(BaseModel):
    """Format + acoustic metadata + tags for one audio file."""

    model_config = ConfigDict(frozen=True)

    path: Path
    format: str
    size_bytes: int
    duration_seconds: float | None = None
    bitrate_kbps: int | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    codec_lossless: bool = False
    reader: str = "unreadable"
    tags: TrackTags = TrackTags()


def read_file(path: Path) -> FileInfo:
    """Read format/duration/tags from one audio file. Never raises on bad input."""
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

    size_bytes = path.stat().st_size
    fmt = _format_from_suffix(path.suffix)
    codec_lossless = fmt in _LOSSLESS_FORMATS

    try:
        if fmt == "mp3":
            return _read_mp3(path, fmt, size_bytes, codec_lossless)
        if fmt in {"wav", "wave"}:
            return _read_wav(path, fmt, size_bytes, codec_lossless)
        if fmt in {"aif", "aiff"}:
            return _read_aiff(path, fmt, size_bytes, codec_lossless)
        if fmt == "m4a":
            return _read_m4a(path, fmt, size_bytes, codec_lossless)
        if fmt == "flac":
            return _read_flac(path, fmt, size_bytes, codec_lossless)
        return _read_generic(path, fmt, size_bytes, codec_lossless)
    except Exception:
        return _read_via_ffprobe(path, fmt, size_bytes, codec_lossless)


def _format_from_suffix(suffix: str) -> str:
    return suffix.lstrip(".").lower() or "unknown"


def _read_mp3(path: Path, fmt: str, size_bytes: int, lossless: bool) -> FileInfo:
    audio = MP3(path)
    tags = _id3_to_tags(audio.tags)
    return _finalize(path, fmt, size_bytes, audio.info, tags, lossless, reader="mutagen")


def _read_wav(path: Path, fmt: str, size_bytes: int, lossless: bool) -> FileInfo:
    audio = WAVE(path)
    tags = _id3_to_tags(audio.tags)
    return _finalize(path, fmt, size_bytes, audio.info, tags, lossless, reader="mutagen")


def _read_aiff(path: Path, fmt: str, size_bytes: int, lossless: bool) -> FileInfo:
    audio = AIFF(path)
    tags = _id3_to_tags(audio.tags)
    return _finalize(path, fmt, size_bytes, audio.info, tags, lossless, reader="mutagen")


def _read_m4a(path: Path, fmt: str, size_bytes: int, lossless: bool) -> FileInfo:
    audio = MP4(path)
    tags = _mp4_to_tags(audio.tags)
    return _finalize(path, fmt, size_bytes, audio.info, tags, lossless, reader="mutagen")


def _read_flac(path: Path, fmt: str, size_bytes: int, lossless: bool) -> FileInfo:
    audio = FLAC(path)
    tags = _vorbis_to_tags(audio.tags)
    return _finalize(path, fmt, size_bytes, audio.info, tags, lossless, reader="mutagen")


def _read_generic(path: Path, fmt: str, size_bytes: int, lossless: bool) -> FileInfo:
    audio = MutagenFile(path)
    if audio is None:
        return _read_via_ffprobe(path, fmt, size_bytes, lossless)
    tags = _id3_to_tags(audio.tags) if isinstance(audio.tags, ID3) else TrackTags()
    return _finalize(path, fmt, size_bytes, audio.info, tags, lossless, reader="mutagen")


def _read_via_ffprobe(path: Path, fmt: str, size_bytes: int, lossless: bool) -> FileInfo:
    """Fallback: shell to ffprobe for duration + best-effort tags."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        meta: dict[str, Any] = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return FileInfo(path=path, format=fmt, size_bytes=size_bytes)

    fmt_section = meta.get("format") or {}
    streams = meta.get("streams") or []
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    duration_seconds = _parse_float(fmt_section.get("duration"))
    bitrate_kbps = _bps_to_kbps(_parse_int(fmt_section.get("bit_rate")))
    sample_rate_hz = _parse_int(audio_stream.get("sample_rate"))
    channels = _parse_int(audio_stream.get("channels"))

    raw_tags = fmt_section.get("tags") or {}
    tags = _ffprobe_to_tags(raw_tags)

    return FileInfo(
        path=path,
        format=fmt,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        bitrate_kbps=bitrate_kbps,
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        codec_lossless=lossless,
        reader="ffprobe",
        tags=tags,
    )


def _finalize(
    path: Path,
    fmt: str,
    size_bytes: int,
    info: Any,
    tags: TrackTags,
    lossless: bool,
    *,
    reader: str,
) -> FileInfo:
    duration_seconds = getattr(info, "length", None) if info is not None else None
    bitrate_kbps = _bps_to_kbps(getattr(info, "bitrate", None))
    sample_rate_hz = getattr(info, "sample_rate", None)
    channels = getattr(info, "channels", None)
    return FileInfo(
        path=path,
        format=fmt,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        bitrate_kbps=bitrate_kbps,
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        codec_lossless=lossless,
        reader=reader,
        tags=tags,
    )


def _id3_to_tags(id3: ID3 | None) -> TrackTags:
    if id3 is None:
        return TrackTags()
    fields: dict[str, Any] = {}
    for key, frame in id3.items():
        frame_name = key.split(":", 1)[0]
        field = _ID3_TO_FIELD.get(frame_name)
        if field is None:
            continue
        text = _frame_to_text(frame)
        if text is None:
            continue
        fields[field] = _coerce(field, text)
    fields["year"] = _id3_year(id3)
    return TrackTags(**fields)


def _mp4_to_tags(tags: dict[str, Any] | None) -> TrackTags:
    if not tags:
        return TrackTags()
    fields: dict[str, Any] = {}
    for atom, field in _MP4_TO_FIELD.items():
        if atom not in tags:
            continue
        value = tags[atom]
        if atom == "tmpo":
            fields[field] = float(value[0]) if value else None
        elif atom == "----:com.apple.iTunes:initialkey":
            raw = value[0] if value else None
            if isinstance(raw, bytes):
                fields[field] = raw.decode("utf-8", errors="ignore")
            elif raw is not None:
                fields[field] = str(raw)
        else:
            fields[field] = ", ".join(str(v) for v in value) if value else None
    fields["year"] = _mp4_year(tags)
    return TrackTags(**fields)


def _vorbis_to_tags(tags: Any) -> TrackTags:
    """Read Vorbis comments. Iterating a VComment yields (key, value) tuples
    where `value` is a single string per occurrence; the same key may repeat."""
    if tags is None:
        return TrackTags()
    fields: dict[str, Any] = {}
    for key, value in tags:
        field = _VORBIS_TO_FIELD.get(key.lower())
        if field is None or value is None:
            continue
        fields[field] = _coerce(field, value)
    fields["year"] = _vorbis_year(tags)
    return TrackTags(**fields)


def _ffprobe_to_tags(raw: dict[str, Any]) -> TrackTags:
    lower = {str(k).lower(): str(v) for k, v in raw.items()}
    return TrackTags(
        title=lower.get("title"),
        artist=lower.get("artist"),
        album=lower.get("album"),
        genre=lower.get("genre"),
        year=_year_from_text(lower.get("date") or lower.get("year")),
        bpm=_parse_float(lower.get("bpm") or lower.get("tbpm")),
        key=lower.get("key") or lower.get("initialkey"),
        label=lower.get("label") or lower.get("publisher"),
    )


def _frame_to_text(frame: Any) -> str | None:
    text = getattr(frame, "text", None)
    if text is None:
        return None
    if isinstance(text, list):
        return ", ".join(str(t) for t in text) if text else None
    return str(text)


def _coerce(field: str, raw: str) -> Any:
    if field == "bpm":
        return _parse_float(raw)
    return raw


def _id3_year(id3: ID3) -> int | None:
    for frame_name in ("TDRC", "TYER", "TDOR"):
        frame = id3.get(frame_name)
        if frame is None:
            continue
        year = _year_from_text(_frame_to_text(frame))
        if year is not None:
            return year
    return None


def _mp4_year(tags: dict[str, Any]) -> int | None:
    raw = tags.get("\xa9day")
    if not raw:
        return None
    return _year_from_text(", ".join(str(v) for v in raw))


def _vorbis_year(tags: Any) -> int | None:
    if not hasattr(tags, "get"):
        return None
    for key in ("date", "year"):
        values = tags.get(key)
        if values:
            return _year_from_text(values[0])
    return None


def _year_from_text(text: str | None) -> int | None:
    if not text:
        return None
    match = _YEAR_RE.match(str(text).strip())
    if not match:
        return None
    return int(match.group(1))


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bps_to_kbps(bps: int | None) -> int | None:
    if bps is None or bps <= 0:
        return None
    return bps // 1000
