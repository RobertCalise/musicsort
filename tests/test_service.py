"""Tests for the LaunchAgent service module.

Pure-function tests only — actual install/uninstall side-effect the user's
LaunchAgents directory and run launchctl, so they're not exercised here.
The CLI command help is verified separately in test_cli.py."""

from __future__ import annotations

import plistlib
from pathlib import Path

from musicsort.autoimport.service import (
    LABEL,
    PLIST_PATH,
    STDERR_LOG,
    STDOUT_LOG,
    render_plist,
)


def test_label_uses_reverse_dns() -> None:
    assert LABEL.startswith("com."), f"label should follow reverse-DNS convention: {LABEL!r}"
    assert "musicsort" in LABEL.lower()


def test_plist_path_under_library_launchagents() -> None:
    assert PLIST_PATH.parent.name == "LaunchAgents"
    assert PLIST_PATH.parent.parent.name == "Library"
    assert PLIST_PATH.suffix == ".plist"


def test_render_plist_round_trips() -> None:
    """Rendered bytes must parse back as a valid plist dict."""
    raw = render_plist()
    data = plistlib.loads(raw)
    assert data["Label"] == LABEL
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] is True
    assert data["ProcessType"] == "Background"
    # Program arguments: [musicsort binary, "watch"]
    args = data["ProgramArguments"]
    assert len(args) == 2
    assert args[0].endswith("/musicsort"), f"first arg should be the musicsort binary: {args[0]!r}"
    assert args[1] == "watch"
    # Logs point at ~/Library/Logs/
    assert data["StandardOutPath"] == str(STDOUT_LOG)
    assert data["StandardErrorPath"] == str(STDERR_LOG)
    # Working directory is an absolute path (the repo root)
    assert Path(data["WorkingDirectory"]).is_absolute()


def test_plist_program_arguments_first_arg_is_absolute_path() -> None:
    raw = render_plist()
    data = plistlib.loads(raw)
    binary = data["ProgramArguments"][0]
    assert binary.startswith("/"), f"binary path must be absolute for launchd: {binary!r}"
