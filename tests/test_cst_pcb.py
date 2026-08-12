"""Tests for the CST board substrate: guard-free reads, cache, first writers.

Contract net for output shapes stays in test_pcb_read_tools.py and
test_pcb_write_tools.py; these tests pin the substrate properties:
byte preservation, KiCad 10 readability, cache behavior.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import _confined, _pure_insertion, _span_preserved, requires_cli
from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Net, Position
from kiutils.items.gritems import GrArc, GrLine
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, pcb
from mcp_server_kicad._shared import _gen_uuid
from mcp_server_kicad.models import PointSpec

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
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (44 "Edge.Cuts" user)
    (48 "B.Fab" user)
    (49 "F.Fab" user)
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
\t\t(36 "B.SilkS" user "B.Silkscreen")
\t\t(37 "F.SilkS" user "F.Silkscreen")
\t\t(44 "Edge.Cuts" user)
\t\t(48 "B.Fab" user)
\t\t(49 "F.Fab" user)
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
        assert len(pcb.list_pcb_layers(p)) == 7
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

    def test_external_rewrite_invalidates(self, scratch_pcb):
        """A non-CST rewrite (kiutils here, standing in for any external
        editor) moves mtime, so the next read re-parses."""
        p = str(scratch_pcb)
        pcb._open_pcb_cst(p)
        board = Board.from_file(p)
        for t in board.traceItems:
            if isinstance(t, Segment):
                t.width = 0.5
        board.to_file()
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


def _bump_version(path) -> str:
    """Text-swap a kiutils-written board's version token to the KiCad 10 format."""
    p = Path(path)
    text = p.read_text()
    assert "(version 20211014)" in text, "kiutils create-new version moved, fix the swap"
    p.write_text(text.replace("(version 20211014)", "(version 20260206)"))
    return str(p)


def _make_keepout_board_bumped(tmp_path):
    """The shared kiutils keepout board with its version text-swapped to KiCad 10."""
    from test_pcb_write_tools import _make_keepout_pcb

    return _bump_version(_make_keepout_pcb(tmp_path))


class TestFootprintWritersCst:
    def test_place_scratch(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.place_footprint("R2", "4.7K", 50, 60, rotation=90, layer="B.Cu", pcb_path=p)
        assert result == "Placed R2 (4.7K) at (50, 60) on B.Cu"
        assert _pure_insertion(before, Path(p).read_bytes())
        board = Board.from_file(p)
        assert "R2" in [fp.properties.get("Reference") for fp in board.footprints]
        r2 = next(f for f in pcb.list_pcb_footprints(p) if f.reference == "R2")
        assert (r2.x, r2.y, r2.rotation, r2.layer, r2.value) == (50.0, 60.0, 90.0, "B.Cu", "4.7K")

    def test_place_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.place_footprint("R2", "1K", 50, 60, pcb_path=p)
        assert _pure_insertion(before, Path(p).read_bytes())
        assert any(f.reference == "R2" for f in pcb.list_pcb_footprints(p))

    def test_move_k9(self, scratch_pcb):
        p = str(scratch_pcb)
        assert pcb.move_footprint("R1", 42, 24, pcb_path=p) == "Moved R1 to (42, 24)"
        r1 = next(f for f in pcb.list_pcb_footprints(p) if f.reference == "R1")
        assert (r1.x, r1.y) == (42.0, 24.0)
        assert Board.from_file(p).footprints[0].position.X == 42

    def test_move_k10_layer_confined(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.move_footprint("R1", 50, 50, layer="B.Cu", pcb_path=p)
        after = Path(p).read_bytes()
        assert _confined(before, after)
        r1 = next(f for f in pcb.list_pcb_footprints(p) if f.reference == "R1")
        assert (r1.x, r1.y, r1.layer) == (50.0, 50.0, "B.Cu")

    def test_move_k10_rotation_appends_atom(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.move_footprint("R1", 50, 50, rotation=90, pcb_path=p)
        after = Path(p).read_bytes()
        assert b"(at 50 50 90)" in after
        assert b"(at -0.75 0)" in after  # pad offsets stay untouched, kiutils parity

    def test_move_missing_refuses_untouched(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        with pytest.raises(ToolError, match="not found"):
            pcb.move_footprint("R99", 10, 10, pcb_path=p)
        assert Path(p).read_bytes() == before

    def test_move_warnings_survive_k10_header(self, tmp_path):
        """The kiutils warning path silently skipped K10 boards; the CST
        twins warn on any version."""
        p = _make_keepout_board_bumped(tmp_path)
        result = pcb.move_footprint("R1", 25, 25, pcb_path=p)
        assert "WARNING: position is inside a keep-out zone" in result
        result = pcb.move_footprint("R1", 100, 100, pcb_path=p)
        assert "WARNING" not in result

    def test_remove_k9(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        assert pcb.remove_footprint("R1", pcb_path=p) == "Removed R1"
        assert _span_preserved(before, Path(p).read_bytes())
        assert pcb.list_pcb_footprints(p) == []

    def test_remove_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.remove_footprint("R1", pcb_path=p)
        assert _span_preserved(before, Path(p).read_bytes())
        assert pcb.list_pcb_footprints(p) == []


class TestGraphicWritersCst:
    def test_text_scratch(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_pcb_text("BOARD V1", 100, 110, layer="F.SilkS", pcb_path=p)
        assert result == "Text 'BOARD V1' at (100, 110) on F.SilkS"
        assert _pure_insertion(before, Path(p).read_bytes())
        texts = [g for g in pcb.list_pcb_graphic_items(p) if g.type == "text"]
        assert [(t.text, t.x, t.y, t.layer) for t in texts] == [
            ("BOARD V1", 100.0, 110.0, "F.SilkS")
        ]

    def test_line_scratch(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_pcb_line(80, 80, 120, 80, layer="Edge.Cuts", pcb_path=p)
        assert result == "Line: (80, 80) -> (120, 80) on Edge.Cuts"
        assert _pure_insertion(before, Path(p).read_bytes())
        lines = [g for g in pcb.list_pcb_graphic_items(p) if g.type == "line"]
        assert (lines[-1].start_x, lines[-1].end_x) == (80.0, 120.0)

    def test_text_and_line_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.add_pcb_text("note", 10, 10, pcb_path=p)
        pcb.add_pcb_line(0, 0, 50, 0, pcb_path=p)
        assert _confined(before, Path(p).read_bytes(), limit=500)
        kinds = [g.type for g in pcb.list_pcb_graphic_items(p)]
        assert "text" in kinds and "line" in kinds


class TestTraceFiltersCst:
    def test_width_by_net_name_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        result = pcb.set_trace_width(width=0.5, net_name="/SIG", pcb_path=p)
        assert result.traces_modified == 1
        after = Path(p).read_bytes()
        assert _confined(before, after)
        assert b"(width 0.5)" in after

    def test_remove_by_net_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        result = pcb.remove_traces(net_name="/SIG", pcb_path=p)
        assert result.traces_removed == 1
        after = Path(p).read_bytes()
        assert _span_preserved(before, after)
        assert pcb.list_pcb_traces(p) == []

    def test_unknown_net_message_parity(self, scratch_pcb):
        with pytest.raises(ToolError, match=r"Net 'Nope' not found\. Available nets:"):
            pcb.set_trace_width(width=0.5, net_name="Nope", pcb_path=str(scratch_pcb))

    def test_no_filters_message_parity(self, scratch_pcb):
        with pytest.raises(ToolError, match="at least one filter is required"):
            pcb.remove_traces(pcb_path=str(scratch_pcb))


class TestZoneWritersCst:
    _CORNERS: list[PointSpec] = [
        {"x": 10, "y": 10},
        {"x": 40, "y": 10},
        {"x": 40, "y": 40},
        {"x": 10, "y": 40},
    ]

    def test_copper_k9_bytes(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_copper_zone("Net1", "F.Cu", self._CORNERS, priority=1, pcb_path=p)
        assert (result.net, result.layer, result.corners) == ("Net1", "F.Cu", 4)
        after = Path(p).read_bytes()
        assert _pure_insertion(before, after)
        i = after.index(b"(zone")
        span = after[i : i + 700]
        assert b"(net 1)" in span
        assert b'(net_name "Net1")' in span
        assert b"(priority 1)" in span
        zones = pcb.list_pcb_zones(p)
        assert [(z.net_name, z.layers, z.priority, z.is_keepout) for z in zones] == [
            ("Net1", ["F.Cu"], 1, False)
        ]
        polygon = zones[0].polygon
        assert polygon is not None and len(polygon) == 4

    def test_copper_solid_connect(self, scratch_pcb):
        p = str(scratch_pcb)
        pcb.add_copper_zone("Net1", "F.Cu", self._CORNERS, thermal_relief=False, pcb_path=p)
        after = Path(p).read_bytes()
        assert b"(connect_pads yes" in after
        zone = next(z for z in Board.from_file(p).zones)
        assert zone.connectPads == "yes"

    def test_copper_k10_named(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.add_copper_zone("/GND", "F.Cu", self._CORNERS, pcb_path=p)
        after = Path(p).read_bytes()
        assert _pure_insertion(before, after)
        i = after.index(b"(zone")
        span = after[i : i + 700]
        assert b'(net "/GND")' in span
        assert b"net_name" not in span
        assert b"filled_areas_thickness" not in span
        zones = pcb.list_pcb_zones(p)
        assert [(z.net_name, z.is_keepout) for z in zones] == [("/GND", False)]

    def test_copper_k10_unknown_net_refuses_untouched(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        with pytest.raises(ToolError, match="Net 'Nope' not found"):
            pcb.add_copper_zone("Nope", "F.Cu", self._CORNERS, pcb_path=p)
        assert Path(p).read_bytes() == before

    def test_keepout_k9_bytes(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_keepout_zone(self._CORNERS[:3], no_tracks=False, no_vias=True, pcb_path=p)
        assert result.corners == 3
        assert result.restrictions["tracks"] == "allowed"
        assert result.restrictions["vias"] == "not_allowed"
        after = Path(p).read_bytes()
        assert _pure_insertion(before, after)
        i = after.index(b"(keepout")
        span = after[i : i + 250]
        assert b"(tracks allowed)" in span and b"(vias not_allowed)" in span
        kz = next(z for z in pcb.list_pcb_zones(p) if z.is_keepout)
        assert kz.keepout is not None
        assert kz.keepout["tracks"] == "allowed"

    def test_keepout_k10_drops_net_tokens(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.add_keepout_zone(self._CORNERS, pcb_path=p)
        after = Path(p).read_bytes()
        i = after.index(b"(zone")
        span = after[i : i + 700]
        assert b"(net " not in span
        assert b"net_name" not in span
        kz = next(z for z in pcb.list_pcb_zones(p) if z.is_keepout)
        assert kz.keepout is not None
        assert kz.keepout["footprints"] == "not_allowed"


@requires_cli
def test_drc_accepts_all_new_constructs(scratch_pcb):
    """KiCad 9 parses a board carrying every slice-14 construct: the
    property-based footprint template, stroke-form gr_line, both zone
    shapes with the measured connect_pads token, thermal vias."""
    p = str(scratch_pcb)
    pcb.place_footprint("R2", "4.7K", 150, 100, pcb_path=p)
    pcb.move_footprint("R1", 100, 100, rotation=90, pcb_path=p)
    pcb.add_pcb_text("SLICE14", 100, 115, pcb_path=p)
    pcb.add_pcb_line(90, 90, 110, 90, pcb_path=p)
    pcb.add_copper_zone(
        "Net1", "F.Cu", [{"x": 95, "y": 95}, {"x": 105, "y": 95}, {"x": 105, "y": 105}], pcb_path=p
    )
    pcb.add_copper_zone(
        "Net2",
        "B.Cu",
        [{"x": 95, "y": 95}, {"x": 105, "y": 95}, {"x": 105, "y": 105}],
        thermal_relief=False,
        pcb_path=p,
    )
    pcb.add_keepout_zone([{"x": 10, "y": 10}, {"x": 20, "y": 10}, {"x": 20, "y": 20}], pcb_path=p)
    pcb.add_thermal_vias("R1", pad_number="2", rows=2, cols=2, pcb_path=p)
    pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=p)
    pcb.remove_traces(net_name="Net2", pcb_path=p)
    pcb.remove_dangling_tracks(pcb_path=p)
    result = pcb.run_drc(pcb_path=p)
    assert result.violation_count >= 0


class TestDanglingCst:
    def test_k10_removes_dangling(self, tmp_path):
        """The fixture's lone segment touches no pad world position, so it
        is dangling; removal is a clean span deletion on a K10 board."""
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        result = pcb.remove_dangling_tracks(pcb_path=p)
        assert result.tracks_removed == 1
        assert result.iterations == 1
        after = Path(p).read_bytes()
        assert _span_preserved(before, after)
        assert b"(segment" not in after

    def test_noop_is_byte_identical(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.remove_dangling_tracks(pcb_path=p)
        before = Path(p).read_bytes()
        result = pcb.remove_dangling_tracks(pcb_path=p)
        assert result.tracks_removed == 0
        assert Path(p).read_bytes() == before


class TestThermalViasCst:
    def test_k9_grid_pure_insertion(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_thermal_vias("R1", pad_number="1", rows=2, cols=2, pcb_path=p)
        assert result.vias_added == 4
        assert result.net == "Net1"
        assert _pure_insertion(before, Path(p).read_bytes())
        board = Board.from_file(p)
        assert sum(1 for t in board.traceItems if isinstance(t, Via)) == 4

    def test_k10_pad_net_resolution(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        result = pcb.add_thermal_vias("R1", pad_number="1", rows=1, cols=1, pcb_path=p)
        assert result.vias_added == 1
        assert result.net == "/SIG"
        after = Path(p).read_bytes()
        i = after.index(b"(via")
        assert b'(net "/SIG")' in after[i : i + 250]
        vias = [t for t in pcb.list_pcb_traces(p) if t.type == "via"]
        assert len(vias) == 1


def _root_of(path):
    """The CST root of a board file written by a kiutils fixture."""
    return _cst.parse(Path(path).read_bytes()).lists[0]


def _make_board_with_edges(tmp_path, lines, arcs=()):
    """Board with Edge.Cuts lines (sx,sy,ex,ey) and arcs (sx,sy,mx,my,ex,ey)."""
    board = Board.create_new()
    board.nets = [Net(number=0, name="")]
    for sx, sy, ex, ey in lines:
        gl = GrLine()
        gl.start = Position(X=sx, Y=sy)
        gl.end = Position(X=ex, Y=ey)
        gl.layer = "Edge.Cuts"
        gl.width = 0.05
        gl.tstamp = _gen_uuid()
        board.graphicItems.append(gl)
    for sx, sy, mx, my, ex, ey in arcs:
        arc = GrArc()
        arc.start = Position(X=sx, Y=sy)
        arc.mid = Position(X=mx, Y=my)
        arc.end = Position(X=ex, Y=ey)
        arc.layer = "Edge.Cuts"
        arc.width = 0.05
        arc.tstamp = _gen_uuid()
        board.graphicItems.append(arc)
    board.filePath = str(tmp_path / "edge_test.kicad_pcb")
    board.to_file()
    return _root_of(board.filePath)


class TestEdgePolygonCst:
    """Ported from the retired kiutils _board_edge_polygon tests."""

    _RECT = [(0, 0, 50, 0), (50, 0, 50, 50), (50, 50, 0, 50), (0, 50, 0, 0)]

    def test_closed_rectangle(self, tmp_path):
        poly = pcb._edge_polygon_cst(_make_board_with_edges(tmp_path, self._RECT))
        assert poly is not None
        assert len(poly) == 4

    def test_no_edges(self, tmp_path):
        assert pcb._edge_polygon_cst(_make_board_with_edges(tmp_path, [])) is None

    def test_with_arcs(self, tmp_path):
        """Three lines closed by an arc: the arc is linearized, not dropped."""
        root = _make_board_with_edges(
            tmp_path,
            [(0, 0, 50, 0), (50, 0, 50, 50), (0, 50, 0, 0)],
            arcs=[(50, 50, 25, 60, 0, 50)],
        )
        poly = pcb._edge_polygon_cst(root)
        assert poly is not None
        assert len(poly) >= 4

    def test_t_junction_no_crash(self, tmp_path):
        pcb._edge_polygon_cst(_make_board_with_edges(tmp_path, [*self._RECT, (25, 0, 25, -20)]))

    def test_multiple_outlines_no_crash(self, tmp_path):
        """Two separate rectangles on Edge.Cuts: one polygon comes back, no crash."""
        second = [(100, 100, 120, 100), (120, 100, 120, 120), (120, 120, 100, 120)]
        root = _make_board_with_edges(tmp_path, [*self._RECT, *second, (100, 120, 100, 100)])
        poly = pcb._edge_polygon_cst(root)
        assert poly is not None
        assert len(poly) == 4


class TestKeepoutViolationsCst:
    """Ported from the retired kiutils _check_footprint_keepout_violations test."""

    def test_layer_mismatch_no_violation(self, tmp_path):
        """Keepout zone on F.Cu only: a B.Cu query is not a violation."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        zone = Zone()
        zone.net = 0
        zone.netName = ""
        zone.layers = ["F.Cu"]
        zone.tstamp = _gen_uuid()
        zone.hatch = Hatch(style="edge", pitch=0.5)
        zone.keepoutSettings = KeepoutSettings(
            tracks="not_allowed",
            vias="not_allowed",
            pads="not_allowed",
            copperpour="not_allowed",
            footprints="not_allowed",
        )
        poly = ZonePolygon()
        poly.coordinates = [
            Position(X=0, Y=0),
            Position(X=100, Y=0),
            Position(X=100, Y=100),
            Position(X=0, Y=100),
        ]
        zone.polygons = [poly]
        board.zones.append(zone)
        board.filePath = str(tmp_path / "layer_test.kicad_pcb")
        board.to_file()

        root = _root_of(board.filePath)
        assert pcb._keepout_violations_cst(root, 50, 50, "B.Cu") == []
        assert pcb._keepout_violations_cst(root, 50, 50, "F.Cu") == [
            {
                "source": "board",
                "layers": ["F.Cu"],
                "restrictions": dict.fromkeys(
                    ("tracks", "vias", "pads", "copperpour", "footprints"), "not_allowed"
                ),
            }
        ]


def _k9_k10_pair(tmp_path, make):
    """The same kiutils fixture built twice, the second bumped to a K10 version."""
    k9, k10 = tmp_path / "k9", tmp_path / "k10"
    k9.mkdir()
    k10.mkdir()
    return str(make(k9)), _bump_version(make(k10))


class TestGeometryTrioCst:
    """The geometry trio on KiCad-10-header boards, which kiutils refused outright.

    Same geometry either side of the version token, so the K10 result is
    pinned to the K9 one rather than to a hand-copied expectation.
    """

    def test_check_placement(self, tmp_path):
        from test_pcb_write_tools import _make_keepout_pcb

        k9, k10 = _k9_k10_pair(tmp_path, _make_keepout_pcb)
        for x, y in ((100, 100), (25, 25), (500, 500)):
            assert pcb.check_placement("R1", x, y, pcb_path=k10) == pcb.check_placement(
                "R1", x, y, pcb_path=k9
            )
        hit = pcb.check_placement("R1", 25, 25, pcb_path=k10)
        assert hit.status == "violations_found"
        assert [v["source"] for v in hit.keepout_violations] == ["board"]
        assert hit.keepout_violations[0]["layers"] == ["F.Cu"]
        assert hit.keepout_violations[0]["restrictions"]["footprints"] == "not_allowed"
        assert pcb.check_placement("R1", 500, 500, pcb_path=k10).outside_board_edge is True

    def test_check_placement_takes_no_rotation(self, tmp_path):
        """It accepted one and never read it, so the schema promised nothing.

        Both checks are on the origin point, and rotating about the origin
        cannot move the origin, so no angle could ever have changed the answer.
        """
        import inspect

        from test_pcb_write_tools import _make_keepout_pcb

        assert "rotation" not in inspect.signature(pcb.check_placement).parameters
        tool = pcb.mcp._tool_manager._tools["check_placement"]
        assert "rotation" not in tool.parameters.get("properties", {})

        k9, _ = _k9_k10_pair(tmp_path, _make_keepout_pcb)
        with pytest.raises(TypeError):
            pcb.check_placement("R1", 25, 25, rotation=90, pcb_path=k9)  # type: ignore[call-arg]

    def test_move_footprint_reports_a_failed_placement_check(self, tmp_path, monkeypatch):
        """The checks are advisory, but a fault in them must not be silent.

        This branch was `except Exception: pass`, so any defect in the geometry
        helpers stayed invisible for as long as nobody went looking.
        """
        from test_pcb_write_tools import _make_keepout_pcb

        k9, _ = _k9_k10_pair(tmp_path, _make_keepout_pcb)
        monkeypatch.setattr(
            pcb,
            "_keepout_violations_cst",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        before = Path(k9).read_bytes()
        result = pcb.move_footprint("R1", 40, 40, pcb_path=k9)
        assert "Moved R1" in result, "an advisory check must never block the move"
        assert "placement checks could not run" in result
        assert "RuntimeError: boom" in result
        assert Path(k9).read_bytes() != before, "the move itself must still land"

    def test_get_footprint_bounds(self, tmp_path):
        from test_pcb_read_tools import _make_board_with_courtyard_fp

        k9, k10 = _k9_k10_pair(tmp_path, _make_board_with_courtyard_fp)
        result = pcb.get_footprint_bounds("U1", pcb_path=k10)
        assert result == pcb.get_footprint_bounds("U1", pcb_path=k9)
        assert result.position == {"x": 100, "y": 100}
        assert result.rotation == 0
        assert result.layer == "F.Cu"
        # Local courtyard -5..5 around a footprint at (100, 100).
        assert result.courtyard == {
            "min_x": 95,
            "min_y": 95,
            "max_x": 105,
            "max_y": 105,
            "width": 10,
            "height": 10,
        }

    def test_validate_board(self, tmp_path):
        from test_pcb_write_tools import _make_keepout_pcb

        k9, k10 = _k9_k10_pair(tmp_path, _make_keepout_pcb)
        clean = pcb.validate_board(pcb_path=k10)
        assert clean == pcb.validate_board(pcb_path=k9)
        assert (clean.total_footprints, clean.board_edge_checked, clean.status) == (1, True, "ok")

        for path in (k9, k10):
            pcb.move_footprint("R1", 25, 25, pcb_path=path)
        bad = pcb.validate_board(pcb_path=k10)
        assert bad == pcb.validate_board(pcb_path=k9)
        assert bad.status == "1 violations found"
        assert bad.violations == [
            {
                "reference": "R1",
                "position": {"x": 25, "y": 25},
                "layer": "F.Cu",
                "issues": ["keepout_zone"],
            }
        ]


class TestResolveLayer:
    """The board's own stackup is the only statement of what a layer may be.

    An enum cannot express it: the set is per-board and users rename layers.
    Unvalidated, a typo reached the disk verbatim, and measured 2026-08-12
    kicad-cli then refused the whole board with "Failed to load board".
    """

    def _root(self, text: str = _K9_BOARD):
        return _cst.parse(text.encode()).lists[0]

    def test_known_layer_passes_through(self):
        assert pcb._resolve_layer_cst(self._root(), "F.Cu") == "F.Cu"

    def test_unknown_layer_lists_what_the_board_defines(self):
        with pytest.raises(ToolError, match="not defined on this board") as exc:
            pcb._resolve_layer_cst(self._root(), "banana")
        assert "F.Cu" in str(exc.value), "the error must name the legal values"

    def test_display_alias_is_not_a_layer_name(self):
        """(36 "B.SilkS" user "B.Silkscreen") -- only atoms[1] is referenceable."""
        root = self._root()
        assert pcb._resolve_layer_cst(root, "B.SilkS") == "B.SilkS"
        with pytest.raises(ToolError):
            pcb._resolve_layer_cst(root, "B.Silkscreen")

    def test_copper_only_rejects_a_technical_layer(self):
        root = self._root()
        with pytest.raises(ToolError, match="not a copper layer"):
            pcb._resolve_layer_cst(root, "F.SilkS", copper_only=True)
        assert pcb._resolve_layer_cst(root, "B.Cu", copper_only=True) == "B.Cu"

    def test_copper_test_is_the_suffix_not_the_type_atom(self):
        """KiCad writes power/mixed/jumper for inner copper.

        A `type == "signal"` test would reject a legal power plane, so the
        suffix is what decides.
        """
        text = _K9_BOARD.replace('(31 "B.Cu" signal)', '(31 "B.Cu" signal)\n    (1 "In1.Cu" power)')
        assert pcb._resolve_layer_cst(self._root(text), "In1.Cu", copper_only=True) == "In1.Cu"

    def test_k10_board_table_reads_the_same(self):
        root = self._root(_K10_BOARD)
        assert pcb._resolve_layer_cst(root, "B.Cu") == "B.Cu"
        with pytest.raises(ToolError):
            pcb._resolve_layer_cst(root, "banana")
