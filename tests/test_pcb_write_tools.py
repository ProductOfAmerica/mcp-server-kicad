"""Tests for PCB write tools."""

import pytest
from mcp_server_kicad import pcb
from mcp_server_kicad._shared import _fp_ref
from kiutils.board import Board
from kiutils.items.brditems import Segment, Via


class TestPlaceFootprint:
    def test_basic(self, scratch_pcb):
        result = pcb.place_footprint("R2", "4.7K", 150, 100, pcb_path=str(scratch_pcb))
        assert "R2" in result
        board = Board.from_file(str(scratch_pcb))
        # Check using _fp_ref since that's what the tools use
        refs = [_fp_ref(fp) for fp in board.footprints]
        assert "R2" in refs


class TestMoveFootprint:
    def test_move_existing(self, scratch_pcb):
        result = pcb.move_footprint("R1", 200, 200, pcb_path=str(scratch_pcb))
        assert "Moved" in result
        board = Board.from_file(str(scratch_pcb))
        r1 = next(fp for fp in board.footprints if _fp_ref(fp) == "R1")
        assert r1.position.X == 200

    def test_move_missing(self, scratch_pcb):
        result = pcb.move_footprint("R999", 200, 200, pcb_path=str(scratch_pcb))
        assert "not found" in result


class TestRemoveFootprint:
    def test_remove_existing(self, scratch_pcb):
        result = pcb.remove_footprint("R1", str(scratch_pcb))
        assert "Removed" in result
        board = Board.from_file(str(scratch_pcb))
        assert len(board.footprints) == 0

    def test_remove_missing(self, scratch_pcb):
        result = pcb.remove_footprint("R999", str(scratch_pcb))
        assert "not found" in result


class TestAddTrace:
    def test_basic(self, scratch_pcb):
        result = pcb.add_trace(50, 50, 60, 50, width=0.25,
                                     layer="F.Cu", net=1, pcb_path=str(scratch_pcb))
        assert "Trace" in result
        board = Board.from_file(str(scratch_pcb))
        segs = [t for t in board.traceItems if isinstance(t, Segment)]
        assert len(segs) >= 2


class TestAddVia:
    def test_basic(self, scratch_pcb):
        result = pcb.add_via(100, 100, pcb_path=str(scratch_pcb))
        assert "Via" in result
        board = Board.from_file(str(scratch_pcb))
        vias = [t for t in board.traceItems if isinstance(t, Via)]
        assert len(vias) == 1


class TestAddPcbText:
    def test_basic(self, scratch_pcb):
        result = pcb.add_pcb_text("BOARD V1", 100, 110,
                                        layer="F.SilkS", pcb_path=str(scratch_pcb))
        assert "BOARD" in result


class TestAddPcbLine:
    def test_basic(self, scratch_pcb):
        result = pcb.add_pcb_line(80, 80, 120, 80,
                                        layer="Edge.Cuts", pcb_path=str(scratch_pcb))
        assert "Line" in result
