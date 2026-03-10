"""Tests for symbol library access tools on the symbol server."""

import pytest

from mcp_server_kicad import symbol


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
