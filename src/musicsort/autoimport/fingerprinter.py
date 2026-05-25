"""Chromaprint audio fingerprinting via fpcalc subprocess.

We invoke `fpcalc -raw -json` directly rather than `pyacoustid.fingerprint_file`,
because pyacoustid's comparator depends on libchromaprint's Python ctypes
binding — and on Homebrew macOS that binding can't find libchromaprint unless
DYLD_FALLBACK_LIBRARY_PATH is set at process startup (ctypes.CDLL("libchromaprint.1.dylib")
fails on basename lookup, even after a full-path pre-load).

`-raw -json` returns the fingerprint as a list of uint32 integers, which our
`compare()` function (algorithm vendored from pyacoustid's `_match_fingerprints`)
consumes directly without ever needing the C library binding.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# Tuning constants come from chromaprint's reference matcher.
# https://essentia.upf.edu/tutorial_fingerprinting_chromaprint.html
_MAX_BIT_ERROR = 2
_MAX_ALIGN_OFFSET = 120
_FPCALC_TIMEOUT_SEC = 30
_FPCALC_LENGTH_SEC = 120


class Fingerprint(BaseModel):
    """Output of `fingerprint_file()` — duration plus the raw uint32 array."""

    model_config = ConfigDict(frozen=True)

    duration_seconds: float
    fingerprint: tuple[int, ...]


class FingerprinterError(RuntimeError):
    """Raised when fpcalc can't decode a file or the binary is missing."""


def fingerprint_file(path: Path) -> Fingerprint:
    """Compute the chromaprint fingerprint for `path`. Raises FingerprinterError on failure."""
    if shutil.which("fpcalc") is None:
        raise FingerprinterError("fpcalc not installed; brew install chromaprint")
    try:
        completed = subprocess.run(
            [
                "fpcalc",
                "-raw",
                "-json",
                "-length",
                str(_FPCALC_LENGTH_SEC),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_FPCALC_TIMEOUT_SEC,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise FingerprinterError(f"fingerprint failed for {path}: {exc}") from exc

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FingerprinterError(f"could not parse fpcalc output for {path}: {exc}") from exc

    raw = payload.get("fingerprint")
    duration = payload.get("duration")
    if not isinstance(raw, list) or not raw or duration is None:
        raise FingerprinterError(f"empty or invalid fpcalc output for {path}")

    return Fingerprint(duration_seconds=float(duration), fingerprint=tuple(int(x) for x in raw))


def compare(a: Fingerprint, b: Fingerprint) -> float:
    """Similarity between two raw chromaprint fingerprints, in [0, 1].

    Vendored from pyacoustid's `_match_fingerprints` so we don't need the
    libchromaprint Python ctypes binding (which is unfindable on Homebrew Macs)."""
    return _match(a.fingerprint, b.fingerprint)


def serialize(fp: Fingerprint) -> str:
    """Compact string form for storage. Round-trips via `deserialize`."""
    return ",".join(str(x) for x in fp.fingerprint)


def deserialize(duration: float, raw: str) -> Fingerprint:
    return Fingerprint(
        duration_seconds=duration,
        fingerprint=tuple(int(x) for x in raw.split(",")) if raw else (),
    )


def _match(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    asize = len(a)
    bsize = len(b)
    if asize == 0 or bsize == 0:
        return 0.0
    counts = [0] * (asize + bsize + 1)
    for i in range(asize):
        jbegin = max(0, i - _MAX_ALIGN_OFFSET)
        jend = min(bsize, i + _MAX_ALIGN_OFFSET)
        ai = a[i]
        for j in range(jbegin, jend):
            if bin(ai ^ b[j]).count("1") <= _MAX_BIT_ERROR:
                counts[i - j + bsize] += 1
    return max(counts) / min(asize, bsize)
