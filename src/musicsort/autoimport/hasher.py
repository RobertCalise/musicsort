"""SHA256 content hashing for the dedup ladder's first rung.

Full-file SHA256 over 1 MB chunks. Apple Silicon hardware-accelerated SHA
makes partial schemes not worth the false-positive risk on master / extended
track variants that share head/tail bytes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

_CHUNK_BYTES = 1 << 20  # 1 MB


def sha256_file(path: Path) -> str:
    """SHA256 hex digest over the full file content."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in _chunks(fh):
            h.update(chunk)
    return h.hexdigest()


def _chunks(fh: BinaryIO) -> Iterator[bytes]:
    while chunk := fh.read(_CHUNK_BYTES):
        yield chunk
