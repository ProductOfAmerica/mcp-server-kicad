"""Tests for kicad-cli executable resolution."""

import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_server_kicad._shared import _find_kicad_cli, _run_cli


def test_which_result_is_made_absolute(monkeypatch):
    """Windows shutil.which searches the current directory before PATH."""
    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: "./kicad-cli")
    _find_kicad_cli.cache_clear()
    resolved = _find_kicad_cli()
    _find_kicad_cli.cache_clear()
    assert resolved is not None
    assert Path(resolved).is_absolute()


def test_macos_bundle_used_when_not_on_path(tmp_path, monkeypatch):
    """The reported bug: kicad-cli absent from PATH on a stock macOS install."""
    bundled = tmp_path / "kicad-cli"
    bundled.write_text("")
    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr("mcp_server_kicad._shared._KICAD_APP", str(bundled))
    _find_kicad_cli.cache_clear()
    assert _find_kicad_cli() == str(bundled.resolve())
    _find_kicad_cli.cache_clear()


def test_missing_cli_raises_actionable_error(monkeypatch):
    """Registering CLI tools unconditionally is only safe if the failure names the fix."""
    monkeypatch.setattr("mcp_server_kicad._shared._find_kicad_cli", lambda: None)
    with pytest.raises(RuntimeError, match="Install KiCad, or set KICAD_CLI_PATH"):
        _run_cli(["version"])


def test_failure_without_stderr_reports_exit_code(monkeypatch):
    """kicad-cli can exit non-zero with nothing on stderr (see #6, OneDrive)."""
    monkeypatch.setattr("mcp_server_kicad._shared._find_kicad_cli", lambda: "/bin/kicad-cli")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 3221225477, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="exit code 3221225477"):
        _run_cli(["version"])
