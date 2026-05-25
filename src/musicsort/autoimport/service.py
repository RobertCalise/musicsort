"""macOS LaunchAgent install / uninstall for the musicsort watcher.

Generates a `.plist` at `~/Library/LaunchAgents/<LABEL>.plist`, configures
`RunAtLoad=true` + `KeepAlive=true` so the watcher starts on user login and
restarts on crash, then loads via `launchctl bootstrap gui/$UID`.

macOS-native rather than Docker: FSEvents works for the watcher, no VM
overhead, native filesystem speed. Docker on macOS would force a fallback
to inotify-style polling and bind-mount filesystems, both of which degrade
the watcher's responsiveness for ~/Music drops.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.robertcalise.musicsort"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs"
STDOUT_LOG = LOG_DIR / "musicsort.out.log"
STDERR_LOG = LOG_DIR / "musicsort.err.log"


class ServiceError(RuntimeError):
    """Raised when install / uninstall / status operations fail."""


def _repo_root() -> Path:
    """Resolve the repo root from this module's location:
    src/musicsort/autoimport/service.py -> repo root."""
    return Path(__file__).resolve().parents[3]


def _musicsort_binary() -> Path:
    """Locate the musicsort entry point installed by `uv sync` in the venv."""
    candidate = _repo_root() / ".venv" / "bin" / "musicsort"
    if not candidate.exists():
        raise ServiceError(f"musicsort binary not found at {candidate}. Run `uv sync` first.")
    return candidate


def render_plist() -> bytes:
    """Build the LaunchAgent plist as bytes. Pure function (apart from
    locating the musicsort binary), suitable for unit testing.

    The LaunchAgent inherits a minimal default PATH (no Homebrew on Apple
    Silicon), so we explicitly inject one that includes `/opt/homebrew/bin`
    and `/usr/local/bin`. pyacoustid's `fpcalc` (installed via `brew install
    chromaprint`) needs to be on PATH for the fingerprinter to work.
    """
    plist_data = {
        "Label": LABEL,
        "ProgramArguments": [str(_musicsort_binary()), "watch"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "WorkingDirectory": str(_repo_root()),
        "StandardOutPath": str(STDOUT_LOG),
        "StandardErrorPath": str(STDERR_LOG),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    return plistlib.dumps(plist_data)


def _gui_target() -> str:
    return f"gui/{os.getuid()}"


def install() -> None:
    """Install (or reinstall) the watcher LaunchAgent and start it.

    Idempotent: if already loaded, unloads first, then loads the freshly
    written plist."""
    if sys.platform != "darwin":
        raise ServiceError("install-service is macOS-only")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(render_plist())

    # Try to bootout first; ignore failure (it's fine if not currently loaded).
    subprocess.run(
        ["launchctl", "bootout", _gui_target(), str(PLIST_PATH)],
        check=False,
        capture_output=True,
    )
    # Bootstrap (load + start).
    result = subprocess.run(
        ["launchctl", "bootstrap", _gui_target(), str(PLIST_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ServiceError(
            f"launchctl bootstrap failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )


def uninstall() -> None:
    """Stop and unregister the watcher LaunchAgent; delete the plist file."""
    if sys.platform != "darwin":
        raise ServiceError("uninstall-service is macOS-only")

    subprocess.run(
        ["launchctl", "bootout", _gui_target(), str(PLIST_PATH)],
        check=False,
        capture_output=True,
    )
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()


def status() -> str:
    """Return a human-readable status string. Read-only."""
    if sys.platform != "darwin":
        return "service commands are macOS-only"
    if not PLIST_PATH.exists():
        return f"not installed (no plist at {PLIST_PATH})"
    result = subprocess.run(
        ["launchctl", "print", f"{_gui_target()}/{LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"plist installed but not loaded (path: {PLIST_PATH})"
    # `launchctl print` output is verbose; surface only state + last exit
    # status if visible.
    state = "unknown"
    last_exit = ""
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("state ="):
            state = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("last exit code ="):
            last_exit = stripped.split("=", 1)[1].strip()
    summary = f"loaded (state: {state}"
    if last_exit:
        summary += f", last exit: {last_exit}"
    summary += ")"
    return summary
