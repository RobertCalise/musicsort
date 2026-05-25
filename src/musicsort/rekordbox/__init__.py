"""Rekordbox auto-import stage for routed tracks.

The orchestrator lives in `musicsort.rekordbox.drain` — pull pending tracks
from the musicsort queue, snapshot Rekordbox's `master.db`, insert each
track as a `DjmdContent` row. Refuses to run while Rekordbox is open.

Callers should import `drain` from its module to avoid shadowing the
submodule's name at the package level:

    from musicsort.rekordbox.drain import drain as rekordbox_drain
"""

from musicsort.rekordbox.drain import DrainReport
from musicsort.rekordbox.process import rekordbox_running

__all__ = ["DrainReport", "rekordbox_running"]
