"""Tests for the content hasher."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from musicsort.autoimport.hasher import sha256_file

# RFC4634 test vectors:
HELLO_SHA256 = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_known_short_content(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    assert sha256_file(f) == HELLO_SHA256


def test_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    assert sha256_file(f) == EMPTY_SHA256


def test_same_content_different_filename(tmp_path: Path) -> None:
    payload = b"the quick brown fox"
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(payload)
    b.write_bytes(payload)
    assert sha256_file(a) == sha256_file(b)


def test_single_byte_change_diverges(tmp_path: Path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"\x00" * 64)
    b.write_bytes(b"\x00" * 63 + b"\x01")
    assert sha256_file(a) != sha256_file(b)


def test_multi_chunk_path(tmp_path: Path) -> None:
    f = tmp_path / "big.bin"
    payload = os.urandom(3 * (1 << 20) + 17)  # > 1 MB; non-multiple of chunk size
    f.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert sha256_file(f) == expected
