"""Tests for lib symbol tools: add_lib_symbol and related operations."""

from pathlib import Path

import pytest
from conftest import reparse
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import schematic


class TestAddLibSymbol:
    def test_add_symbol(self, scratch_sch: Path, scratch_sym_lib: Path) -> None:
        result = schematic.add_lib_symbol(str(scratch_sym_lib), "TestPart", str(scratch_sch))
        assert "Added" in result

        sch = reparse(scratch_sch)
        lib_names = [ls.entryName for ls in sch.libSymbols]
        assert "TestPart" in lib_names

    def test_duplicate_symbol(self, scratch_sch: Path, scratch_sym_lib: Path) -> None:
        """Adding the same symbol twice should report it already exists."""
        first = schematic.add_lib_symbol(str(scratch_sym_lib), "TestPart", str(scratch_sch))
        assert "Added" in first

        with pytest.raises(ToolError, match="already in"):
            schematic.add_lib_symbol(str(scratch_sym_lib), "TestPart", str(scratch_sch))

        # Verify only one copy exists
        sch = reparse(scratch_sch)
        count = sum(1 for ls in sch.libSymbols if ls.entryName == "TestPart")
        assert count == 1

    def test_unknown_symbol(self, scratch_sch: Path, scratch_sym_lib: Path) -> None:
        """Requesting a symbol that doesn't exist in the lib should report not found."""
        with pytest.raises(ToolError, match="not found"):
            schematic.add_lib_symbol(str(scratch_sym_lib), "NOPE", str(scratch_sch))
