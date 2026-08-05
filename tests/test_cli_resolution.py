"""Tests for kicad-cli executable resolution."""

import os
import subprocess
from pathlib import Path

import pytest

from mcp_server_kicad._shared import _find_kicad_cli, _run_cli


@pytest.fixture(autouse=True)
def _clear_cli_cache():
    """Reset the resolver cache around every test."""
    _find_kicad_cli.cache_clear()
    yield
    _find_kicad_cli.cache_clear()


# ── Resolution order ──────────────────────────────────────────────


def test_env_override_takes_precedence(tmp_path, monkeypatch):
    """KICAD_CLI_PATH wins over PATH."""
    fake = tmp_path / "kicad-cli"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)

    monkeypatch.setenv("KICAD_CLI_PATH", str(fake))
    monkeypatch.setattr("shutil.which", lambda _: "/somewhere/else/kicad-cli")

    assert _find_kicad_cli() == str(fake)


def test_env_override_ignored_when_not_executable(tmp_path, monkeypatch):
    """A KICAD_CLI_PATH that isn't a runnable file falls through to PATH."""
    monkeypatch.setenv("KICAD_CLI_PATH", str(tmp_path / "does-not-exist"))
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/kicad-cli")

    assert _find_kicad_cli() == "/usr/bin/kicad-cli"


def test_falls_back_to_path(monkeypatch):
    """With no env override, PATH is consulted."""
    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/kicad-cli")

    assert _find_kicad_cli() == "/usr/bin/kicad-cli"


def test_falls_back_to_platform_location(tmp_path, monkeypatch):
    """When kicad-cli is absent from PATH, standard install dirs are searched."""
    bundled = tmp_path / "kicad-cli"
    bundled.write_text("#!/bin/sh\nexit 0\n")
    bundled.chmod(0o755)

    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("mcp_server_kicad._shared._SYSTEM_CLI_PATHS", [bundled])

    assert _find_kicad_cli() == str(bundled)


def test_returns_none_when_missing(monkeypatch):
    """No env var, not on PATH, no install dir -> None."""
    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("mcp_server_kicad._shared._SYSTEM_CLI_PATHS", [])

    assert _find_kicad_cli() is None


def test_skips_nonexistent_platform_candidates(tmp_path, monkeypatch):
    """Missing candidates are skipped rather than returned blindly."""
    missing = tmp_path / "nope" / "kicad-cli"
    present = tmp_path / "kicad-cli"
    present.write_text("#!/bin/sh\nexit 0\n")
    present.chmod(0o755)

    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("mcp_server_kicad._shared._SYSTEM_CLI_PATHS", [missing, present])

    assert _find_kicad_cli() == str(present)


# ── The property that actually matters ────────────────────────────


def test_resolved_path_is_absolute(monkeypatch):
    """kicad-cli must be invoked by absolute path.

    KiCad resolves its stock symbol/footprint libraries relative to the
    executable's own directory (<exe_dir>/../SharedSupport). Invoking a
    bare name found via a symlink elsewhere on PATH makes it search the
    wrong SharedSupport and emit spurious "library not found" DRC/ERC
    violations.
    """
    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/kicad-cli")

    resolved = _find_kicad_cli()
    assert resolved is not None
    assert Path(resolved).is_absolute()


# ── _run_cli wiring ───────────────────────────────────────────────


def test_run_cli_invokes_resolved_absolute_path(monkeypatch):
    """_run_cli must exec the resolved path, not the bare name."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "mcp_server_kicad._shared._find_kicad_cli", lambda: "/opt/kicad/bin/kicad-cli"
    )
    monkeypatch.setattr("subprocess.run", fake_run)

    _run_cli(["version"])

    assert captured["cmd"][0] == "/opt/kicad/bin/kicad-cli"
    assert captured["cmd"][1:] == ["version"]


def test_run_cli_raises_clear_error_when_cli_missing(monkeypatch):
    """A missing kicad-cli produces an actionable message, not FileNotFoundError."""
    monkeypatch.setattr("mcp_server_kicad._shared._find_kicad_cli", lambda: None)

    with pytest.raises(RuntimeError, match="kicad-cli not found"):
        _run_cli(["version"])


def test_run_cli_error_mentions_env_override(monkeypatch):
    """The error should tell the user how to fix it."""
    monkeypatch.setattr("mcp_server_kicad._shared._find_kicad_cli", lambda: None)

    with pytest.raises(RuntimeError, match="KICAD_CLI_PATH"):
        _run_cli(["version"])


# ── Caching ───────────────────────────────────────────────────────


def test_result_is_cached(monkeypatch):
    """Resolution is cached so every CLI call doesn't re-stat the filesystem."""
    calls = {"n": 0}

    def counting_which(_):
        calls["n"] += 1
        return "/usr/bin/kicad-cli"

    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setattr("shutil.which", counting_which)

    _find_kicad_cli()
    _find_kicad_cli()
    _find_kicad_cli()

    assert calls["n"] == 1


# ── Real environment (skipped when KiCad absent) ──────────────────


@pytest.mark.skipif(_find_kicad_cli() is None, reason="kicad-cli not installed on this machine")
def test_real_cli_runs_and_reports_version():
    """End-to-end: the resolved binary actually executes."""
    _find_kicad_cli.cache_clear()
    result = _run_cli(["version"], check=False)
    assert result.returncode == 0
    assert result.stdout.strip()


@pytest.mark.skipif(
    not Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli").exists(),
    reason="macOS KiCad.app not installed",
)
def test_macos_bundle_found_without_path(monkeypatch):
    """The macOS .app bundle is discoverable with an empty PATH.

    KiCad's macOS installer does not add kicad-cli to PATH, so this is the
    default state on a stock install.
    """
    monkeypatch.delenv("KICAD_CLI_PATH", raising=False)
    monkeypatch.setenv("PATH", "")
    _find_kicad_cli.cache_clear()

    resolved = _find_kicad_cli()
    assert resolved is not None
    assert resolved.endswith("KiCad.app/Contents/MacOS/kicad-cli")
    assert os.access(resolved, os.X_OK)
