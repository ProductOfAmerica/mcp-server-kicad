"""Tests for the CST board substrate: guard-free reads, cache, first writers.

Contract net for output shapes stays in test_pcb_read_tools.py and
test_pcb_write_tools.py; these tests pin the substrate properties:
byte preservation, KiCad 10 readability, cache behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import _cst, pcb

# KiCad-9-native-format board (quoted generator, uuid tokens), the dialect
# kicad-cli 9 writes; shape shared with test_pcb_write_tools.py, plus one
# footprint and via so every read tool has a subject.
_K9_BOARD = """(kicad_pcb (version 20241108) (generator "pcbnew")

  (general
    (thickness 1.6)
  )

  (paper "A4")

  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )

  (setup
    (pad_to_mask_clearance 0)
  )

  (net 0 "")
  (net 1 "Net1")
  (net 2 "Net2")

  (footprint "Resistor_SMD:R_0603" (layer "F.Cu")
    (at 100 100 0)
    (uuid "fp-0001")
    (property "Reference" "R1")
    (property "Value" "10K")
    (pad "1" smd rect (at -0.75 0) (size 0.7 0.8) (layers "F.Cu" "F.Paste" "F.Mask")
      (net 1 "Net1"))
    (pad "2" smd rect (at 0.75 0) (size 0.7 0.8) (layers "F.Cu" "F.Paste" "F.Mask")
      (net 2 "Net2"))
  )

  (gr_line (start 0 0) (end 100 0) (layer "Edge.Cuts") (width 0.05) (uuid "gl-0001"))

  (segment (start 100 100) (end 110 100) (width 0.25)
    (layer "F.Cu") (net 1) (uuid "aaa-1111-2222"))

  (via (at 115 100) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "via-0001"))

)"""


def _write_board(tmp_path, content: str) -> str:
    p = tmp_path / "board.kicad_pcb"
    p.write_text(content)
    return str(p)


class TestBoardRoundTrip:
    def test_scratch_pcb(self, scratch_pcb):
        data = Path(scratch_pcb).read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data

    def test_k9_native(self, tmp_path):
        p = _write_board(tmp_path, _K9_BOARD)
        data = Path(p).read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data


class TestBoardReadsCst:
    """The 8 converted read tools on the kiutils fixture dialect."""

    def test_scratch_reads(self, scratch_pcb):
        p = str(scratch_pcb)
        fps = pcb.list_pcb_footprints(p)
        assert [(f.reference, f.value, f.lib_id) for f in fps] == [
            ("R1", "10K", "Resistor_SMD:R_0603")
        ]
        assert (fps[0].x, fps[0].y, fps[0].layer) == (100.0, 100.0, "F.Cu")
        traces = pcb.list_pcb_traces(p)
        assert [(t.type, t.net) for t in traces] == [("segment", 1)]
        nets = pcb.list_pcb_nets(p)
        assert [(n.number, n.name) for n in nets] == [(1, "Net1"), (2, "Net2")]
        layers = pcb.list_pcb_layers(p)
        assert (layers[0].ordinal, layers[0].name, layers[0].type) == (0, "F.Cu", "signal")
        graphics = pcb.list_pcb_graphic_items(p)
        assert [(g.type, g.layer) for g in graphics] == [("line", "Edge.Cuts")]
        info = pcb.get_board_info(p)
        assert info == "Footprints: 1\nTraces: 1\nVias: 0\nNets: 3\nZones: 0\nThickness: 1.6mm"
        pads = pcb.get_footprint_pads("R1", p)
        assert "Pad 1: smd rect @ (-0.75, 0) size=(0.7, 0.8)" in pads
        assert "net=Net1" in pads and "net=Net2" in pads

    def test_unknown_footprint(self, scratch_pcb):
        with pytest.raises(ToolError, match="not found"):
            pcb.get_footprint_pads("R999", str(scratch_pcb))

    def test_kicad10_header_reads_work(self, tmp_path):
        """Converted reads no longer refuse post-KiCad-9 board versions."""
        p = _write_board(tmp_path, _K9_BOARD.replace("(version 20241108)", "(version 20260206)"))
        fps = pcb.list_pcb_footprints(p)
        assert [f.reference for f in fps] == ["R1"]
        traces = pcb.list_pcb_traces(p)
        assert [(t.type, t.net) for t in traces] == [("segment", 1), ("via", 1)]
        via = traces[1]
        assert (via.x, via.y, via.layers) == (115.0, 100.0, ["F.Cu", "B.Cu"])
        assert [n.name for n in pcb.list_pcb_nets(p)] == ["Net1", "Net2"]
        assert len(pcb.list_pcb_layers(p)) == 3
        assert [g.type for g in pcb.list_pcb_graphic_items(p)] == ["line"]
        info = pcb.get_board_info(p)
        assert "Footprints: 1" in info and "Vias: 1" in info
        pads = pcb.get_footprint_pads("R1", p)
        assert "net=Net1" in pads
