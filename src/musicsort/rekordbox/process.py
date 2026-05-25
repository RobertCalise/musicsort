"""Detect whether the Rekordbox application is currently running.

Isolated so the drain orchestrator can refuse to write while Rekordbox
holds the encrypted `master.db` open, and so tests can monkeypatch the
single function instead of `pyrekordbox.utils.get_rekordbox_pid` directly.
"""

from __future__ import annotations

from pyrekordbox.utils import get_rekordbox_pid


def rekordbox_running() -> bool:
    """True if a Rekordbox process is currently running."""
    return get_rekordbox_pid() != 0
