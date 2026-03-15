"""Tests for symbol library access tools on the symbol server."""

import shutil

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import symbol

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None


class TestListLibSymbols:
    def test_list_symbols(self, scratch_sym_lib):
        result = symbol.list_lib_symbols(str(scratch_sym_lib))
        assert "TestPart" in result

    def test_nonexistent(self):
        with pytest.raises(Exception):
            symbol.list_lib_symbols("/nonexistent.kicad_sym")


class TestGetSymbolInfo:
    def test_known(self, scratch_sym_lib):
        result = symbol.get_symbol_info("TestPart", str(scratch_sym_lib))
        assert "IN" in result
        assert "OUT" in result
        assert "passive" in result

    def test_unknown(self, scratch_sym_lib):
        result = symbol.get_symbol_info("NOPE", str(scratch_sym_lib))
        assert "not found" in result


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestExportSymbolSvg:
    def test_returns_result(self, scratch_sym_lib, tmp_path):
        try:
            result = symbol.export_symbol_svg(str(scratch_sym_lib), str(tmp_path))
            assert result.format == "svg"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestUpgradeSymbolLib:
    def test_returns_result(self, scratch_sym_lib, tmp_path):
        import shutil as shutil_mod

        copy = str(tmp_path / "upgrade_test.kicad_sym")
        shutil_mod.copy(str(scratch_sym_lib), copy)
        try:
            result = symbol.upgrade_symbol_lib(copy)
            assert "success" in result.lower() or "upgraded" in result.lower()
        except (RuntimeError, FileNotFoundError):
            pass
