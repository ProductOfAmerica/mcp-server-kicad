"""Tests for PCB read tools."""

import json

from mcp_server_kicad import pcb


class TestListPcbItems:
    def test_list_footprints(self, scratch_pcb):
        result = json.loads(pcb.list_pcb_items("footprints", str(scratch_pcb)))
        assert isinstance(result, list)
        refs = [fp["reference"] for fp in result]
        assert "R1" in refs

    def test_list_footprints_empty(self, tmp_path):
        from kiutils.board import Board

        b = Board.create_new()
        path = str(tmp_path / "empty.kicad_pcb")
        b.filePath = path
        b.to_file()
        result = json.loads(pcb.list_pcb_items("footprints", path))
        assert result == []

    def test_list_traces(self, scratch_pcb):
        result = json.loads(pcb.list_pcb_items("traces", str(scratch_pcb)))
        assert isinstance(result, list)

    def test_list_traces_empty(self, tmp_path):
        from kiutils.board import Board

        b = Board.create_new()
        path = str(tmp_path / "empty.kicad_pcb")
        b.filePath = path
        b.to_file()
        result = json.loads(pcb.list_pcb_items("traces", path))
        assert result == []

    def test_list_nets(self, scratch_pcb):
        result = json.loads(pcb.list_pcb_items("nets", str(scratch_pcb)))
        assert isinstance(result, list)
        names = [n["name"] for n in result]
        assert "Net1" in names
        assert "Net2" in names

    def test_list_zones(self, scratch_pcb):
        result = json.loads(pcb.list_pcb_items("zones", str(scratch_pcb)))
        assert isinstance(result, list)
        assert result == []

    def test_list_layers(self, scratch_pcb):
        result = json.loads(pcb.list_pcb_items("layers", str(scratch_pcb)))
        assert isinstance(result, list)
        assert len(result) > 0

    def test_list_graphic_items(self, scratch_pcb):
        result = json.loads(pcb.list_pcb_items("graphic_items", str(scratch_pcb)))
        assert isinstance(result, list)

    def test_invalid_item_type(self, scratch_pcb):
        result = json.loads(pcb.list_pcb_items("invalid", str(scratch_pcb)))
        assert "error" in result


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
