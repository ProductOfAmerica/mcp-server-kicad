"""Tests for Freerouting helper module."""

import os
import subprocess
from unittest.mock import patch

import pytest

import mcp_server_kicad._freerouting as _fr_module
from mcp_server_kicad._freerouting import (
    check_java,
    ensure_jar,
    export_dsn,
    find_jar,
    find_pcbnew_python,
    import_ses,
    run_freerouting,
)


class TestCheckJava:
    def test_java_found_valid_version(self):
        mock_result = subprocess.CompletedProcess(
            args=["java", "-version"],
            returncode=0,
            stdout="",
            stderr='openjdk version "21.0.1" 2023-10-17',
        )
        with patch("subprocess.run", return_value=mock_result):
            result = check_java()
            assert result is None

    def test_java_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = check_java()
            assert result is not None
            assert "Java" in result
            assert "apt install" in result

    def test_java_too_old(self):
        mock_result = subprocess.CompletedProcess(
            args=["java", "-version"],
            returncode=0,
            stdout="",
            stderr='openjdk version "11.0.2" 2019-01-15',
        )
        with patch("subprocess.run", return_value=mock_result):
            result = check_java()
            assert result is not None
            assert "17" in result


class TestFindJar:
    def test_env_var_override(self, tmp_path):
        jar = tmp_path / "custom.jar"
        jar.touch()
        with patch.dict(os.environ, {"FREEROUTING_JAR": str(jar)}):
            assert find_jar() == str(jar)

    def test_env_var_missing_file(self):
        with patch.dict(os.environ, {"FREEROUTING_JAR": "/nonexistent/fr.jar"}):
            assert find_jar() is None

    def test_cached_jar(self, tmp_path):
        cache_dir = tmp_path / ".local" / "share" / "mcp-server-kicad"
        cache_dir.mkdir(parents=True)
        jar = cache_dir / "freerouting.jar"
        jar.touch()
        with patch("mcp_server_kicad._freerouting._cache_dir", return_value=cache_dir):
            assert find_jar() == str(jar)

    def test_no_jar(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with (
            patch.dict(os.environ, {"FREEROUTING_JAR": ""}, clear=False),
            patch("mcp_server_kicad._freerouting._cache_dir", return_value=empty_dir),
        ):
            assert find_jar() is None


class TestEnsureJar:
    def test_already_exists(self, tmp_path):
        jar = tmp_path / "freerouting.jar"
        jar.touch()
        with patch("mcp_server_kicad._freerouting.find_jar", return_value=str(jar)):
            path, err = ensure_jar()
            assert path == str(jar)
            assert err is None

    def test_download_success(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        jar_path = str(cache_dir / "freerouting.jar")

        with (
            patch("mcp_server_kicad._freerouting.find_jar", side_effect=[None, jar_path]),
            patch("mcp_server_kicad._freerouting._download_jar", return_value=jar_path),
        ):
            path, err = ensure_jar()
            assert path == jar_path
            assert err is None

    def test_download_failure(self, tmp_path):
        with (
            patch("mcp_server_kicad._freerouting.find_jar", return_value=None),
            patch(
                "mcp_server_kicad._freerouting._download_jar",
                side_effect=RuntimeError("Network error"),
            ),
        ):
            path, err = ensure_jar()
            assert path is None
            assert err is not None
            assert "Network error" in err


class TestFindPcbnewPython:
    @pytest.fixture(autouse=True)
    def _reset_pcbnew_cache(self):
        _fr_module._pcbnew_cache = None
        yield
        _fr_module._pcbnew_cache = None

    def test_direct_import_works(self):
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            python, env = find_pcbnew_python()
            assert python is not None

    def test_no_pcbnew_available(self):
        with patch("subprocess.run", side_effect=Exception("fail")):
            python, env = find_pcbnew_python()
            assert python is None


_FIND_PY = "mcp_server_kicad._freerouting.find_pcbnew_python"


class TestExportDsn:
    def test_success(self, tmp_path):
        pcb_path = str(tmp_path / "board.kicad_pcb")
        dsn_path = str(tmp_path / "board.dsn")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = export_dsn(pcb_path, dsn_path)
            assert err is None

    def test_pcbnew_not_found(self, tmp_path):
        with patch(_FIND_PY, return_value=(None, None)):
            err = export_dsn(str(tmp_path / "b.kicad_pcb"), str(tmp_path / "b.dsn"))
            assert err is not None
            assert "pcbnew" in err.lower() or "KiCad" in err


class TestImportSes:
    def test_success(self, tmp_path):
        pcb_path = str(tmp_path / "board.kicad_pcb")
        ses_path = str(tmp_path / "board.ses")
        out_path = str(tmp_path / "board_routed.kicad_pcb")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = import_ses(pcb_path, ses_path, out_path)
            assert err is None

    def test_subprocess_fails(self, tmp_path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="pcbnew error"
        )
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = import_ses(
                str(tmp_path / "b.kicad_pcb"),
                str(tmp_path / "b.ses"),
                str(tmp_path / "b_routed.kicad_pcb"),
            )
            assert err is not None


class TestRunFreerouting:
    def test_success(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        ses = tmp_path / "board.ses"
        dsn.touch()
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Route complete", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(ses),
            )
            assert err is None

    def test_timeout(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        dsn.touch()
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="java", timeout=600),
        ):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(tmp_path / "board.ses"),
                timeout=600,
            )
            assert err is not None
            assert "timeout" in err.lower() or "timed out" in err.lower()

    def test_nonzero_exit(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        dsn.touch()
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Error")
        with patch("subprocess.run", return_value=mock_result):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(tmp_path / "board.ses"),
            )
            assert err is not None
