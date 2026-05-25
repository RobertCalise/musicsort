"""macOS-native user notifications via `osascript`.

Used to surface things the LaunchAgent-run watcher would otherwise hide in
log files: files landing in `_Unsorted`, Rekordbox-side import failures,
and "Rekordbox is open, drain skipped" events.

No-ops on non-Darwin so unit tests on Linux CI don't have to mock anything
unless they specifically assert the call shape. Notification delivery is
best-effort: failures from `osascript` are swallowed since logging through
the notification subsystem itself would be circular.
"""

from __future__ import annotations

import platform
import subprocess


def notify(
    title: str,
    message: str,
    *,
    subtitle: str = "",
    sound: bool = False,
) -> None:
    """Display a native macOS notification.

    Args:
        title: Bold first line of the notification.
        message: Body text below the title.
        subtitle: Optional second line, smaller text between title and body.
        sound: Play the default "Glass" alert sound when delivered.
    """
    if platform.system() != "Darwin":
        return

    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    if subtitle:
        script += f' subtitle "{_escape(subtitle)}"'
    if sound:
        script += ' sound name "Glass"'

    subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
    )


def _escape(text: str) -> str:
    """Escape backslashes and double-quotes for safe inclusion in an
    AppleScript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')
