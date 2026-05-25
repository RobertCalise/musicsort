"""Tests for the macOS notification wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from musicsort import notifications


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Force Darwin branch and capture subprocess.run calls."""
    monkeypatch.setattr(notifications.platform, "system", lambda: "Darwin")
    mock = MagicMock()
    monkeypatch.setattr(notifications.subprocess, "run", mock)
    return mock


def test_no_op_on_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifications.platform, "system", lambda: "Linux")
    spy = MagicMock()
    monkeypatch.setattr(notifications.subprocess, "run", spy)

    notifications.notify("title", "message")

    spy.assert_not_called()


def test_minimal_call_builds_osascript_command(captured: MagicMock) -> None:
    notifications.notify("musicsort", "queue drained")

    captured.assert_called_once()
    args, kwargs = captured.call_args
    cmd = args[0]
    assert cmd[0] == "osascript"
    assert cmd[1] == "-e"
    assert 'display notification "queue drained"' in cmd[2]
    assert 'with title "musicsort"' in cmd[2]
    assert "subtitle" not in cmd[2]
    assert "sound name" not in cmd[2]
    assert kwargs["check"] is False
    assert kwargs["capture_output"] is True


def test_subtitle_and_sound_render(captured: MagicMock) -> None:
    notifications.notify("t", "m", subtitle="s", sound=True)
    script = captured.call_args[0][0][2]
    assert 'subtitle "s"' in script
    assert 'sound name "Glass"' in script


def test_escapes_quotes_and_backslashes(captured: MagicMock) -> None:
    notifications.notify('a "quoted" name', "path\\to\\file")
    script = captured.call_args[0][0][2]
    assert r"\"quoted\"" in script
    assert r"path\\to\\file" in script


def test_uses_list_form_no_shell_true(captured: MagicMock) -> None:
    notifications.notify("t", "m")
    _, kwargs = captured.call_args
    assert "shell" not in kwargs or kwargs.get("shell") is False
