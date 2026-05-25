"""Shared pytest fixtures. Session-scoped audio fixture builder uses ffmpeg
to generate tiny tagged files in each format we support, so test runs are
self-contained and don't depend on committed binary fixtures.

Source signal is 3 seconds of pink noise (not silence) so the same fixtures
exercise the chromaprint fingerprinter — silence and pure tones produce
empty fingerprints."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import APIC, GEOB, TALB, TBPM, TCON, TDRC, TIT2, TKEY, TPE1, TPUB
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.wave import WAVE

_SEEDED_PINK_NOISE_LAVFI = "anoisesrc=r=44100:c=pink:d=3:seed=42"


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _gen_audio(out: Path, codec: str, container_args: list[str] | None = None) -> None:
    """Generate ~3s of stereo pink noise via ffmpeg in the chosen codec.

    Pink noise (not silence) is required so the chromaprint fingerprinter has
    enough spectral variance to produce a non-empty fingerprint."""
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anoisesrc=r=44100:c=pink:d=3",
        "-ac",
        "2",
        "-c:a",
        codec,
    ]
    if container_args:
        cmd.extend(container_args)
    cmd.append(str(out))
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


@pytest.fixture(scope="session")
def audio_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build one tagged + one untagged audio file per format we read, plus a
    Serato-frame-decorated WAV and a malformed file. Returns a name -> path map."""
    if not _have_ffmpeg():
        pytest.skip("ffmpeg not available — required to build audio fixtures")

    root = tmp_path_factory.mktemp("audio_fixtures")
    files: dict[str, Path] = {}

    # --- MP3 (tagged + empty) ---------------------------------------------------
    mp3_tagged = root / "tagged.mp3"
    _gen_audio(mp3_tagged, "libmp3lame", ["-b:a", "64k"])
    mp3 = MP3(mp3_tagged)
    if mp3.tags is None:
        mp3.add_tags()
    mp3.tags.add(TIT2(encoding=3, text="Strobe"))
    mp3.tags.add(TPE1(encoding=3, text="deadmau5"))
    mp3.tags.add(TALB(encoding=3, text="For Lack Of A Better Name"))
    mp3.tags.add(TCON(encoding=3, text="House"))
    mp3.tags.add(TDRC(encoding=3, text="2009"))
    mp3.tags.add(TBPM(encoding=3, text="128"))
    mp3.tags.add(TKEY(encoding=3, text="Abm"))
    mp3.tags.add(TPUB(encoding=3, text="mau5trap"))
    mp3.tags.add(
        APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=b"\xff\xd8\xff\xe0")
    )
    mp3.save()
    files["mp3_tagged"] = mp3_tagged

    mp3_empty = root / "empty.mp3"
    _gen_audio(mp3_empty, "libmp3lame", ["-b:a", "64k"])
    files["mp3_empty"] = mp3_empty

    # --- WAV (empty, fully tagged, and Serato-decorated) ------------------------
    wav_empty = root / "empty.wav"
    _gen_audio(wav_empty, "pcm_s16le")
    files["wav_empty"] = wav_empty

    wav_tagged = root / "tagged.wav"
    _gen_audio(wav_tagged, "pcm_s16le")
    wav = WAVE(wav_tagged)
    if wav.tags is None:
        wav.add_tags()
    wav.tags.add(TIT2(encoding=3, text="Got To Be"))
    wav.tags.add(TPE1(encoding=3, text="Tigerblind"))
    wav.tags.add(TCON(encoding=3, text="EDM"))
    wav.tags.add(TBPM(encoding=3, text="132"))
    wav.tags.add(TKEY(encoding=3, text="Bbm"))
    wav.save()
    files["wav_tagged"] = wav_tagged

    wav_serato = root / "serato.wav"
    _gen_audio(wav_serato, "pcm_s16le")
    sw = WAVE(wav_serato)
    if sw.tags is None:
        sw.add_tags()
    sw.tags.add(TBPM(encoding=3, text="128"))
    sw.tags.add(TKEY(encoding=3, text="Am"))
    sw.tags.add(
        GEOB(
            encoding=0,
            mime="application/octet-stream",
            desc="Serato Markers2",
            data=b"\x01\x02\x03fake-serato-markers",
        )
    )
    sw.tags.add(
        GEOB(
            encoding=0,
            mime="application/octet-stream",
            desc="Serato Beatgrid",
            data=b"\x01\x02\x03fake-serato-beatgrid",
        )
    )
    sw.save()
    files["wav_serato"] = wav_serato

    # --- M4A (tagged + empty) ---------------------------------------------------
    m4a_tagged = root / "tagged.m4a"
    _gen_audio(m4a_tagged, "aac", ["-b:a", "64k"])
    m4a = MP4(m4a_tagged)
    m4a["\xa9nam"] = ["m4a title"]
    m4a["\xa9ART"] = ["m4a artist"]
    m4a["\xa9alb"] = ["m4a album"]
    m4a["\xa9gen"] = ["Tech House"]
    m4a["\xa9day"] = ["2022"]
    m4a["tmpo"] = [124]
    m4a["covr"] = [MP4Cover(b"\xff\xd8\xff\xe0", imageformat=MP4Cover.FORMAT_JPEG)]
    m4a.save()
    files["m4a_tagged"] = m4a_tagged

    m4a_empty = root / "empty.m4a"
    _gen_audio(m4a_empty, "aac", ["-b:a", "64k"])
    files["m4a_empty"] = m4a_empty

    # --- AIFF (tagged + empty) --------------------------------------------------
    aiff_tagged = root / "tagged.aif"
    _gen_audio(aiff_tagged, "pcm_s16be")
    af = AIFF(aiff_tagged)
    if af.tags is None:
        af.add_tags()
    af.tags.add(TIT2(encoding=3, text="aiff title"))
    af.tags.add(TPE1(encoding=3, text="aiff artist"))
    af.save()
    files["aiff_tagged"] = aiff_tagged

    aiff_empty = root / "empty.aif"
    _gen_audio(aiff_empty, "pcm_s16be")
    files["aiff_empty"] = aiff_empty

    # --- FLAC (tagged + empty) --------------------------------------------------
    flac_tagged = root / "tagged.flac"
    _gen_audio(flac_tagged, "flac")
    flac = FLAC(flac_tagged)
    flac["title"] = "flac title"
    flac["artist"] = "flac artist"
    flac["album"] = "flac album"
    flac["genre"] = "Techno"
    flac["date"] = "2023"
    flac["bpm"] = "130"
    flac.save()
    files["flac_tagged"] = flac_tagged

    flac_empty = root / "empty.flac"
    _gen_audio(flac_empty, "flac")
    files["flac_empty"] = flac_empty

    # --- Malformed (header truncated) and non-audio --------------------------
    truncated = root / "truncated.mp3"
    _gen_audio(truncated, "libmp3lame", ["-b:a", "64k"])
    truncated.write_bytes(truncated.read_bytes()[:64])
    files["truncated"] = truncated

    not_audio = root / "fake.wav"
    not_audio.write_bytes(b"this is plain text masquerading as wav\n")
    files["not_audio"] = not_audio

    zero = root / "zero.wav"
    zero.write_bytes(b"")
    files["zero"] = zero

    return files


@pytest.fixture(scope="session")
def seeded_pink_noise(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Deterministic 3-second stereo pink noise WAV. Re-encoding this into
    different MP3 bitrates produces fingerprints similar enough to test the
    layer-2 dedup ladder."""
    if not _have_ffmpeg():
        pytest.skip("ffmpeg not available — required for seeded pink noise source")
    src = tmp_path_factory.mktemp("seeded_audio") / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            _SEEDED_PINK_NOISE_LAVFI,
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(src),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return src


def _encode_mp3(source_wav: Path, dst: Path, bitrate_k: int, genre: str) -> Path:
    """Re-encode a WAV source into an MP3 at the given bitrate, tagged with `genre`."""
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_wav),
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{bitrate_k}k",
            str(dst),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    m = MP3(dst)
    if m.tags is None:
        m.add_tags()
    m.tags.add(TCON(encoding=3, text=genre))
    m.save()
    return dst


@pytest.fixture
def encode_mp3(seeded_pink_noise: Path):
    """Test-scoped factory: encode the seeded pink noise into an MP3 at any bitrate."""

    def _factory(dst: Path, *, bitrate_k: int = 320, genre: str = "Techno") -> Path:
        return _encode_mp3(seeded_pink_noise, dst, bitrate_k, genre)

    return _factory


@pytest.fixture
def populated_library(tmp_path: Path, audio_fixtures: dict[str, Path]) -> Path:
    """A fresh library tree at tmp_path/library/ with a few pre-shelved tracks
    in Songs/<Genre>/. Used by mover integration tests."""
    library = tmp_path / "library"
    songs = library / "Songs"
    house = songs / "House"
    techno = songs / "Techno"
    house.mkdir(parents=True)
    techno.mkdir(parents=True)
    shutil.copy(audio_fixtures["mp3_tagged"], house / "existing_house.mp3")
    shutil.copy(audio_fixtures["wav_tagged"], techno / "existing_techno.wav")
    return library


@pytest.fixture
def cli_env(populated_library: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the musicsort CLI's get_settings() at a tmp library tree.

    Sets MUSICSORT_* env vars so get_settings() returns Settings rooted at
    populated_library. Pre-creates the AutoImport folder so test cases can
    drop files in immediately. Returns the library root for test assertions."""
    monkeypatch.setenv("MUSICSORT_LIBRARY_ROOT", str(populated_library))
    monkeypatch.setenv("MUSICSORT_AUTOIMPORT_FOLDER", str(populated_library / "AutoImport"))
    monkeypatch.setenv("MUSICSORT_SONGS_DIR", str(populated_library / "Songs"))
    monkeypatch.setenv("MUSICSORT_QUARANTINE_DIR", str(populated_library / "_Unsorted"))
    monkeypatch.setenv(
        "MUSICSORT_FINGERPRINT_DB_PATH",
        str(populated_library / ".musicsort" / "fingerprints.db"),
    )
    # Disable the Rekordbox drain in CLI tests by default — otherwise the
    # post-routing drain in `once`/`watch` would attempt to open and write to
    # the user's real master.db. Tests that exercise the drain do so via
    # their own monkeypatched fixtures.
    monkeypatch.setenv("MUSICSORT_REKORDBOX_ENABLED", "false")
    (populated_library / "AutoImport").mkdir(parents=True, exist_ok=True)
    return populated_library
