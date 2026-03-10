"""Tests for _resolve_config() and config resolution logic in _shared.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_server_kicad._shared import _resolve_config, _resolve_system_lib

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal KiCad project directory with all sibling files."""
    pro = tmp_path / "myboard.kicad_pro"
    pro.write_text("{}")
    (tmp_path / "myboard.kicad_sch").write_text("")
    (tmp_path / "myboard.kicad_pcb").write_text("")
    (tmp_path / "myboard.kicad_sym").write_text("")
    (tmp_path / "myboard.pretty").mkdir()
    return tmp_path


@pytest.fixture
def tmp_project_partial(tmp_path: Path) -> Path:
    """KiCad project with .kicad_pro but only some siblings."""
    pro = tmp_path / "partial.kicad_pro"
    pro.write_text("{}")
    (tmp_path / "partial.kicad_sch").write_text("")
    # No .kicad_pcb, .kicad_sym, or .pretty
    return tmp_path


# ---------------------------------------------------------------------------
# Auto-detect: single .kicad_pro -> derive paths for existing siblings
# ---------------------------------------------------------------------------


class TestAutoDetectSingleProject:
    def test_derives_all_paths_when_siblings_exist(self, tmp_project: Path) -> None:
        with patch("mcp_server_kicad._shared._cwd", return_value=tmp_project):
            cfg = _resolve_config()

        assert cfg["sch_path"] == str(tmp_project / "myboard.kicad_sch")
        assert cfg["pcb_path"] == str(tmp_project / "myboard.kicad_pcb")
        assert cfg["sym_lib_path"] == str(tmp_project / "myboard.kicad_sym")
        assert cfg["fp_lib_path"] == str(tmp_project / "myboard.pretty")
        assert cfg["output_dir"] == str(tmp_project)


# ---------------------------------------------------------------------------
# Auto-detect: no .kicad_pro -> all empty
# ---------------------------------------------------------------------------


class TestAutoDetectNoProject:
    def test_all_empty_when_no_kicad_pro(self, tmp_path: Path) -> None:
        with patch("mcp_server_kicad._shared._cwd", return_value=tmp_path):
            cfg = _resolve_config()

        assert cfg["sch_path"] == ""
        assert cfg["pcb_path"] == ""
        assert cfg["sym_lib_path"] == ""
        assert cfg["fp_lib_path"] == ""
        assert cfg["output_dir"] == ""


# ---------------------------------------------------------------------------
# Auto-detect: multiple .kicad_pro -> skip, all empty
# ---------------------------------------------------------------------------


class TestAutoDetectMultipleProjects:
    def test_all_empty_when_multiple_kicad_pro(self, tmp_path: Path) -> None:
        (tmp_path / "a.kicad_pro").write_text("{}")
        (tmp_path / "b.kicad_pro").write_text("{}")
        (tmp_path / "a.kicad_sch").write_text("")
        (tmp_path / "b.kicad_sch").write_text("")

        with patch("mcp_server_kicad._shared._cwd", return_value=tmp_path):
            cfg = _resolve_config()

        assert cfg["sch_path"] == ""
        assert cfg["pcb_path"] == ""
        assert cfg["sym_lib_path"] == ""
        assert cfg["fp_lib_path"] == ""
        assert cfg["output_dir"] == ""


# ---------------------------------------------------------------------------
# Auto-detect: .kicad_pro exists but siblings missing -> only set found paths
# ---------------------------------------------------------------------------


class TestAutoDetectPartialSiblings:
    def test_only_existing_siblings_populated(self, tmp_project_partial: Path) -> None:
        with patch("mcp_server_kicad._shared._cwd", return_value=tmp_project_partial):
            cfg = _resolve_config()

        # .kicad_sch exists
        assert cfg["sch_path"] == str(tmp_project_partial / "partial.kicad_sch")
        # these do not exist -> empty
        assert cfg["pcb_path"] == ""
        assert cfg["sym_lib_path"] == ""
        assert cfg["fp_lib_path"] == ""
        # output_dir is always set when a project is detected
        assert cfg["output_dir"] == str(tmp_project_partial)


# ---------------------------------------------------------------------------
# Env vars override auto-detect
# ---------------------------------------------------------------------------


class TestEnvVarOverrides:
    def test_env_vars_override_auto_detected(self, tmp_project: Path) -> None:
        env = {"KICAD_SCH_PATH": "/override/my.kicad_sch"}

        with (
            patch("mcp_server_kicad._shared._cwd", return_value=tmp_project),
            patch.dict("os.environ", env, clear=False),
        ):
            cfg = _resolve_config()

        # Overridden by env var
        assert cfg["sch_path"] == "/override/my.kicad_sch"
        # Others still auto-detected
        assert cfg["pcb_path"] == str(tmp_project / "myboard.kicad_pcb")

    def test_all_five_env_vars_work(self, tmp_path: Path) -> None:
        env = {
            "KICAD_SCH_PATH": "/env/test.kicad_sch",
            "KICAD_PCB_PATH": "/env/test.kicad_pcb",
            "KICAD_SYM_LIB": "/env/test.kicad_sym",
            "KICAD_FP_LIB": "/env/test.pretty",
            "KICAD_OUTPUT_DIR": "/env/output",
        }

        with (
            patch("mcp_server_kicad._shared._cwd", return_value=tmp_path),
            patch.dict("os.environ", env, clear=False),
        ):
            cfg = _resolve_config()

        assert cfg["sch_path"] == "/env/test.kicad_sch"
        assert cfg["pcb_path"] == "/env/test.kicad_pcb"
        assert cfg["sym_lib_path"] == "/env/test.kicad_sym"
        assert cfg["fp_lib_path"] == "/env/test.pretty"
        assert cfg["output_dir"] == "/env/output"

    def test_env_vars_override_empty_autodetect(self, tmp_path: Path) -> None:
        """Env vars work even when auto-detect finds nothing."""
        env = {"KICAD_PCB_PATH": "/env/board.kicad_pcb"}

        with (
            patch("mcp_server_kicad._shared._cwd", return_value=tmp_path),
            patch.dict("os.environ", env, clear=False),
        ):
            cfg = _resolve_config()

        assert cfg["pcb_path"] == "/env/board.kicad_pcb"
        assert cfg["sch_path"] == ""  # no auto-detect, no env var


# ---------------------------------------------------------------------------
# _resolve_system_lib tests
# ---------------------------------------------------------------------------


class TestResolveSystemLib:
    def test_returns_none_for_custom_lib(self):
        """Non-system library prefixes return None."""
        assert _resolve_system_lib("skrimp") is None

    def test_returns_path_for_device(self):
        """'Device' resolves to Device.kicad_sym if KiCad is installed."""
        result = _resolve_system_lib("Device")
        if result is not None:
            assert Path(result).name == "Device.kicad_sym"
            assert Path(result).exists()

    def test_returns_path_for_connector_generic(self):
        """'Connector_Generic' resolves if KiCad is installed."""
        result = _resolve_system_lib("Connector_Generic")
        if result is not None:
            assert Path(result).name == "Connector_Generic.kicad_sym"

    def test_env_override(self, tmp_path, monkeypatch):
        """KICAD_SYMBOL_DIR env var is checked first."""
        fake_lib = tmp_path / "FakeLib.kicad_sym"
        fake_lib.write_text("")
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(tmp_path))
        result = _resolve_system_lib("FakeLib")
        assert result == str(fake_lib)

    def test_env_override_nonexistent_dir(self, tmp_path, monkeypatch):
        """KICAD_SYMBOL_DIR points to nonexistent dir, falls through to system paths."""
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(tmp_path / "nonexistent"))
        # Should not raise, just falls through (and likely returns None)
        result = _resolve_system_lib("Device")
        # If KiCad is installed system-wide it may still resolve; otherwise None
        if result is not None:
            assert Path(result).name == "Device.kicad_sym"

    def test_returns_none_for_empty_string(self):
        """Empty string returns None."""
        assert _resolve_system_lib("") is None
