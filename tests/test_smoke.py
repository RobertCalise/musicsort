"""Smoke test: confirms the package imports and the CLI entry point is wired."""

from __future__ import annotations

from typer.testing import CliRunner

from musicsort.autoimport.cli import app


def test_cli_help_exits_zero() -> None:
    """`musicsort --help` must exit 0 and mention the CLI name."""
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "musicsort" in result.output
