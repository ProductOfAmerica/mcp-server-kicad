"""Tests for library access tools."""

import pytest
# Cross-server test: symbol library tools are in schematic, footprint library tools are in pcb.
from mcp_server_kicad import schematic
from mcp_server_kicad import pcb


class TestListLibSymbols:
    def test_list_symbols(self, scratch_sym_lib):
        result = schematic.list_lib_symbols(str(scratch_sym_lib))
        assert "TestPart" in result

    def test_nonexistent(self):
        with pytest.raises(Exception):
            schematic.list_lib_symbols("/nonexistent.kicad_sym")


class TestGetSymbolInfo:
    def test_known(self, scratch_sym_lib):
        result = schematic.get_symbol_info("TestPart", str(scratch_sym_lib))
        assert "IN" in result
        assert "OUT" in result
        assert "passive" in result

    def test_unknown(self, scratch_sym_lib):
        result = schematic.get_symbol_info("NOPE", str(scratch_sym_lib))
        assert "not found" in result


class TestListLibFootprints:
    def test_list_from_pretty_dir(self, tmp_path):
        # Create a .pretty dir with one .kicad_mod
        pretty = tmp_path / "TestLib.pretty"
        pretty.mkdir()
        from kiutils.footprint import Footprint
        fp = Footprint()
        fp.entryName = "R_0603"
        fp.filePath = str(pretty / "R_0603.kicad_mod")
        fp.to_file()
        result = pcb.list_lib_footprints(str(pretty))
        assert "R_0603" in result


class TestGetFootprintInfo:
    def test_from_file(self, tmp_path):
        from kiutils.footprint import Footprint, Pad
        from kiutils.items.common import Position
        fp = Footprint()
        fp.entryName = "R_0603"
        pad = Pad()
        pad.number = "1"
        pad.type = "smd"
        pad.shape = "rect"
        pad.position = Position(X=-0.75, Y=0)
        pad.size = Position(X=0.7, Y=0.8)
        pad.layers = ["F.Cu"]
        fp.pads = [pad]
        path = str(tmp_path / "R_0603.kicad_mod")
        fp.filePath = path
        fp.to_file()
        result = pcb.get_footprint_info(path)
        assert "Pad 1" in result or "pad" in result.lower()
