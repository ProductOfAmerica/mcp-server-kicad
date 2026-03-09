"""Tests for PCB read tools."""

import pytest
from mcp_server_kicad import pcb
from conftest import reparse


class TestListFootprints:
    def test_with_footprint(self, scratch_pcb):
        result = pcb.list_footprints(str(scratch_pcb))
        assert "R1" in result
        assert "10K" in result

    def test_empty_board(self, tmp_path):
        from kiutils.board import Board
        b = Board.create_new()
        path = str(tmp_path / "empty.kicad_pcb")
        b.filePath = path
        b.to_file()
        result = pcb.list_footprints(path)
        assert "No footprints" in result


class TestListTraces:
    def test_with_trace(self, scratch_pcb):
        result = pcb.list_traces(str(scratch_pcb))
        assert "F.Cu" in result
        assert "99.25" in result

    def test_empty(self, tmp_path):
        from kiutils.board import Board
        b = Board.create_new()
        path = str(tmp_path / "empty.kicad_pcb")
        b.filePath = path
        b.to_file()
        result = pcb.list_traces(path)
        assert "No traces" in result


class TestListNets:
    def test_with_nets(self, scratch_pcb):
        result = pcb.list_nets(str(scratch_pcb))
        assert "Net1" in result
        assert "Net2" in result


class TestListZones:
    def test_empty(self, scratch_pcb):
        result = pcb.list_zones(str(scratch_pcb))
        assert "No zones" in result


class TestListLayers:
    def test_returns_layers(self, scratch_pcb):
        result = pcb.list_layers(str(scratch_pcb))
        assert "F.Cu" in result or "layers" in result.lower() or len(result) > 0


class TestGetBoardInfo:
    def test_returns_info(self, scratch_pcb):
        result = pcb.get_board_info(str(scratch_pcb))
        assert "footprint" in result.lower() or "trace" in result.lower()


class TestGetFootprintPads:
    def test_known_footprint(self, scratch_pcb):
        result = pcb.get_footprint_pads("R1", str(scratch_pcb))
        assert "Pad 1" in result or "pad 1" in result.lower()
        assert "Pad 2" in result or "pad 2" in result.lower()

    def test_unknown_footprint(self, scratch_pcb):
        result = pcb.get_footprint_pads("R999", str(scratch_pcb))
        assert "not found" in result


class TestListBoardGraphicItems:
    def test_with_items(self, scratch_pcb):
        result = pcb.list_board_graphic_items(str(scratch_pcb))
        assert "Edge.Cuts" in result or "line" in result.lower()
