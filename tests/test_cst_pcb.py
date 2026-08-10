"""Tests for the CST board substrate: guard-free reads, cache, first writers.

Contract net for output shapes stays in test_pcb_read_tools.py and
test_pcb_write_tools.py; these tests pin the substrate properties:
byte preservation, KiCad 10 readability, cache behavior.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import _pure_insertion, requires_cli
from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
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


# KiCad-10-native-format board, shape measured via the slice-13 probe on the
# K10 runner: no net table, no net numbers anywhere, items reference nets by
# name only, B.Cu ordinal is 2.
_K10_BOARD = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(44 "Edge.Cuts" user)
\t)
\t(footprint "Resistor_SMD:R_0603"
\t\t(layer "F.Cu")
\t\t(uuid "fp-0001")
\t\t(at 100 100)
\t\t(property "Reference" "R1"
\t\t\t(at 0 0 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "10K"
\t\t\t(at 0 2 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at -0.75 0)
\t\t\t(size 0.7 0.8)
\t\t\t(layers "F.Cu" "F.Paste" "F.Mask")
\t\t\t(net "/SIG")
\t\t)
\t\t(pad "2" smd rect
\t\t\t(at 0.75 0)
\t\t\t(size 0.7 0.8)
\t\t\t(layers "F.Cu" "F.Paste" "F.Mask")
\t\t\t(net "/GND")
\t\t)
\t)
\t(segment
\t\t(start 100 100)
\t\t(end 110 100)
\t\t(width 0.25)
\t\t(layer "F.Cu")
\t\t(net "/SIG")
\t\t(uuid "seg-0001")
\t)
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


class TestBoardCache:
    def test_hit_then_mtime_invalidates(self, scratch_pcb):
        p = str(scratch_pcb)
        t1, _, key = pcb._open_pcb_cst(p)
        t2, _, _ = pcb._open_pcb_cst(p)
        assert t2 is t1
        st = os.stat(key)
        os.utime(key, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        t3, _, _ = pcb._open_pcb_cst(p)
        assert t3 is not t1

    def test_kiutils_write_invalidates(self, scratch_pcb):
        p = str(scratch_pcb)
        pcb._open_pcb_cst(p)
        pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=p)
        traces = pcb.list_pcb_traces(p)
        assert traces[0].width == 0.5


class TestAddTraceViaCst:
    def test_scratch_pure_insertion_and_readback(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        pcb.add_trace(10, 20, 30, 20, net=1, pcb_path=p)
        mid = Path(p).read_bytes()
        assert _pure_insertion(before, mid)
        pcb.add_via(20, 20, net=1, pcb_path=p)
        after = Path(p).read_bytes()
        assert _pure_insertion(mid, after)
        board = Board.from_file(p)
        assert sum(1 for t in board.traceItems if isinstance(t, Segment)) == 2
        assert sum(1 for t in board.traceItems if isinstance(t, Via)) == 1
        assert ("via", 1) in [(t.type, t.net) for t in pcb.list_pcb_traces(p)]

    def test_k9_native_numeric_net(self, tmp_path):
        p = _write_board(tmp_path, _K9_BOARD)
        before = Path(p).read_bytes()
        pcb.add_trace(50, 50, 60, 50, net=1, pcb_path=p)
        after = Path(p).read_bytes()
        assert _pure_insertion(before, after)
        i = after.index(b"(start 50 50)")
        span = after[i - 100 : i + 250]
        assert b"(net 1)" in span
        assert b'(net "Net1")' not in span

    def test_k10_named_net(self, tmp_path):
        p = _write_board(tmp_path, _K9_BOARD.replace("(version 20241108)", "(version 20260206)"))
        before = Path(p).read_bytes()
        pcb.add_trace(50, 50, 60, 50, net=1, pcb_path=p)
        after = Path(p).read_bytes()
        assert _pure_insertion(before, after)
        i = after.index(b"(start 50 50)")
        span = after[i - 100 : i + 250]
        assert b'(net "Net1")' in span
        pcb.add_via(55, 50, net=2, pcb_path=p)
        assert b'(net "Net2")' in Path(p).read_bytes()

    def test_k10_unknown_net_refuses_untouched(self, tmp_path):
        p = _write_board(tmp_path, _K9_BOARD.replace("(version 20241108)", "(version 20260206)"))
        pcb._open_pcb_cst(p)
        before = Path(p).read_bytes()
        with pytest.raises(ToolError, match="Net 9 not found"):
            pcb.add_trace(0, 0, 1, 1, net=9, pcb_path=p)
        assert Path(p).read_bytes() == before
        assert str(Path(p).resolve()) not in pcb._BOARD_CACHE

    def test_k10_tableless_derived_nets_and_emission(self, tmp_path):
        """The measured K10 shape: no net table, name-only references.

        Numbers are synthesized from document order, and add_trace resolves
        them back to names for emission.
        """
        p = _write_board(tmp_path, _K10_BOARD)
        assert _cst.serialize(_cst.parse(Path(p).read_bytes())) == Path(p).read_bytes()
        nets = pcb.list_pcb_nets(p)
        assert [(n.number, n.name) for n in nets] == [(1, "/SIG"), (2, "/GND")]
        traces = pcb.list_pcb_traces(p)
        assert [(t.type, t.net) for t in traces] == [("segment", 1)]
        pads = pcb.get_footprint_pads("R1", p)
        assert "net=/SIG" in pads and "net=/GND" in pads
        assert "Nets: 2" in pcb.get_board_info(p)

        before = Path(p).read_bytes()
        pcb.add_trace(50, 50, 60, 50, net=2, pcb_path=p)
        after = Path(p).read_bytes()
        assert _pure_insertion(before, after)
        i = after.index(b"(start 50 50)")
        assert b'(net "/GND")' in after[i - 100 : i + 250]
        assert [(t.type, t.net) for t in pcb.list_pcb_traces(p)] == [
            ("segment", 1),
            ("segment", 2),
        ]

    @requires_cli
    def test_drc_accepts_spliced_board(self, scratch_pcb):
        """KiCad 9 accepts (uuid ...) items inside a tstamp-era board: the
        live oracle for the always-uuid template choice."""
        p = str(scratch_pcb)
        pcb.add_trace(10, 20, 30, 20, net=1, pcb_path=p)
        pcb.add_via(20, 20, net=1, pcb_path=p)
        result = pcb.run_drc(pcb_path=p)
        assert result.violation_count >= 0
