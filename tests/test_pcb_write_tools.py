"""Tests for PCB write tools."""

import json
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Position

from mcp_server_kicad import pcb
from mcp_server_kicad._shared import _fp_ref


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
        result = pcb.add_trace(
            50, 50, 60, 50, width=0.25, layer="F.Cu", net=1, pcb_path=str(scratch_pcb)
        )
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
        result = pcb.add_pcb_text("BOARD V1", 100, 110, layer="F.SilkS", pcb_path=str(scratch_pcb))
        assert "BOARD" in result


class TestAddPcbLine:
    def test_basic(self, scratch_pcb):
        result = pcb.add_pcb_line(80, 80, 120, 80, layer="Edge.Cuts", pcb_path=str(scratch_pcb))
        assert "Line" in result


class TestAutoroutePcb:
    def test_success(self, scratch_pcb, tmp_path):
        """Test full autoroute workflow with mocked external dependencies."""

        def mock_export_dsn(pcb_path, dsn_path):
            Path(dsn_path).touch()
            return None

        def mock_import_ses(pcb_path, ses_path, output_path):
            shutil.copy(pcb_path, output_path)
            board = Board.from_file(output_path)
            for i in range(4):
                seg = Segment()
                seg.start = Position(X=50 + i * 10, Y=50)
                seg.end = Position(X=60 + i * 10, Y=50)
                seg.width = 0.25
                seg.layer = "F.Cu"
                seg.net = 1
                seg.tstamp = str(uuid.uuid4())
                board.traceItems.append(seg)
            for i in range(2):
                via = Via()
                via.position = Position(X=70 + i * 10, Y=50)
                via.size = 0.6
                via.drill = 0.3
                via.net = 1
                via.layers = ["F.Cu", "B.Cu"]
                via.tstamp = str(uuid.uuid4())
                board.traceItems.append(via)
            board.to_file()
            return None

        def mock_ensure_jar():
            return "/fake/freerouting.jar", None

        def mock_check_java():
            return None

        def mock_run_freerouting(**kwargs):
            Path(kwargs.get("ses_path", "/tmp/fake.ses")).touch()
            return None

        with (
            patch("mcp_server_kicad.pcb._check_java", mock_check_java),
            patch("mcp_server_kicad.pcb._ensure_jar", mock_ensure_jar),
            patch("mcp_server_kicad.pcb._export_dsn", mock_export_dsn),
            patch("mcp_server_kicad.pcb._run_freerouting", mock_run_freerouting),
            patch("mcp_server_kicad.pcb._import_ses", mock_import_ses),
        ):
            result = pcb.autoroute_pcb(pcb_path=str(scratch_pcb))
            data = json.loads(result)
            assert "routed_path" in data
            assert data["traces_added"] == 4
            assert data["vias_added"] == 2

    def test_no_java(self, scratch_pcb):
        with patch("mcp_server_kicad.pcb._check_java", return_value="Java not found"):
            result = pcb.autoroute_pcb(pcb_path=str(scratch_pcb))
            data = json.loads(result)
            assert "error" in data
            assert "Java" in data["error"]


class TestFindNet:
    def test_finds_existing_net(self, scratch_pcb):
        board = Board.from_file(str(scratch_pcb))
        net_num, net_name = pcb._find_net(board, "Net1")
        assert net_num == 1
        assert net_name == "Net1"

    def test_raises_for_missing_net(self, scratch_pcb):
        board = Board.from_file(str(scratch_pcb))
        with pytest.raises(ValueError, match="not found"):
            pcb._find_net(board, "NonExistent")


def _board_with_traces(scratch_pcb):
    """Add several traces on different nets/layers for filter testing."""
    board = Board.from_file(str(scratch_pcb))
    for _i, (net, layer, x) in enumerate(
        [
            (1, "F.Cu", 10),
            (1, "B.Cu", 20),
            (2, "F.Cu", 30),
            (2, "B.Cu", 40),
        ]
    ):
        seg = Segment()
        seg.start = Position(X=x, Y=50)
        seg.end = Position(X=x + 5, Y=50)
        seg.width = 0.25
        seg.layer = layer
        seg.net = net
        seg.tstamp = str(uuid.uuid4())
        board.traceItems.append(seg)
    board.to_file()
    return board


class TestFilterSegments:
    def test_filter_by_net(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        result = pcb._filter_segments(
            board, net_name="Net1", layer=None, x_min=None, y_min=None, x_max=None, y_max=None
        )
        assert len(result) == 3  # original scratch trace + 2 new

    def test_filter_by_layer(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        result = pcb._filter_segments(
            board, net_name=None, layer="B.Cu", x_min=None, y_min=None, x_max=None, y_max=None
        )
        assert len(result) == 2

    def test_filter_by_net_and_layer(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        result = pcb._filter_segments(
            board, net_name="Net1", layer="F.Cu", x_min=None, y_min=None, x_max=None, y_max=None
        )
        assert len(result) == 2

    def test_filter_by_bbox(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        result = pcb._filter_segments(
            board, net_name=None, layer=None, x_min=25, y_min=45, x_max=45, y_max=55
        )
        assert len(result) == 2

    def test_no_filters_raises(self, scratch_pcb):
        board = _board_with_traces(scratch_pcb)
        with pytest.raises(ValueError, match="at least one filter"):
            pcb._filter_segments(
                board, net_name=None, layer=None, x_min=None, y_min=None, x_max=None, y_max=None
            )


class TestAddCopperZone:
    def test_basic_zone(self, scratch_pcb):
        result = pcb.add_copper_zone(
            net_name="Net1",
            layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 50, "y": 50}, {"x": 0, "y": 50}],
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["net"] == "Net1"
        assert data["layer"] == "F.Cu"
        assert data["corners"] == 4
        board = Board.from_file(str(scratch_pcb))
        assert len(board.zones) == 1
        zone = board.zones[0]
        assert zone.netName == "Net1"
        assert zone.layers == ["F.Cu"]
        assert zone.clearance == 0.5
        assert len(zone.polygons) == 1
        assert len(zone.polygons[0].coordinates) == 4

    def test_no_thermal_relief(self, scratch_pcb):
        pcb.add_copper_zone(
            net_name="Net1",
            layer="B.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
            thermal_relief=False,
            pcb_path=str(scratch_pcb),
        )
        board = Board.from_file(str(scratch_pcb))
        zone = board.zones[0]
        assert zone.connectPads == "full"

    def test_fewer_than_3_corners(self, scratch_pcb):
        result = pcb.add_copper_zone(
            net_name="Net1",
            layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 10, "y": 0}],
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert "error" in data

    def test_invalid_net(self, scratch_pcb):
        result = pcb.add_copper_zone(
            net_name="NonExistent",
            layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert "error" in data


class TestFillZones:
    def test_no_pcbnew_returns_error(self, scratch_pcb):
        with patch("mcp_server_kicad.pcb._find_pcbnew_python", return_value=(None, None)):
            result = pcb.fill_zones(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data

    def test_success_with_mocked_subprocess(self, scratch_pcb):
        pcb.add_copper_zone(
            net_name="Net1",
            layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 50, "y": 50}, {"x": 0, "y": 50}],
            pcb_path=str(scratch_pcb),
        )
        mock_result = type("Result", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()
        mock_python = ("/usr/bin/python3", None)
        with (
            patch("mcp_server_kicad.pcb._find_pcbnew_python", return_value=mock_python),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = pcb.fill_zones(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["zones_filled"] == 1
        assert data["status"] == "ok"


class TestSetTraceWidth:
    def test_widen_by_net(self, scratch_pcb):
        _board_with_traces(scratch_pcb)
        result = pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["traces_modified"] == 3  # original scratch trace + 2 added on Net1
        assert data["new_width_mm"] == 0.5
        board = Board.from_file(str(scratch_pcb))
        for seg in board.traceItems:
            if isinstance(seg, Segment) and seg.net == 1:
                assert seg.width == 0.5

    def test_no_filters_returns_error(self, scratch_pcb):
        result = pcb.set_trace_width(width=0.5, pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data

    def test_no_matches_returns_zero(self, scratch_pcb):
        result = pcb.set_trace_width(width=0.5, net_name="Net2", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["traces_modified"] == 0


class TestRemoveTraces:
    def test_remove_by_net(self, scratch_pcb):
        _board_with_traces(scratch_pcb)
        result = pcb.remove_traces(net_name="Net2", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["traces_removed"] == 2
        board = Board.from_file(str(scratch_pcb))
        net2_segs = [t for t in board.traceItems if isinstance(t, Segment) and t.net == 2]
        assert len(net2_segs) == 0

    def test_does_not_remove_vias(self, scratch_pcb):
        pcb.add_via(100, 100, net=1, pcb_path=str(scratch_pcb))
        result = pcb.remove_traces(net_name="Net1", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["traces_removed"] == 1
        board = Board.from_file(str(scratch_pcb))
        vias = [t for t in board.traceItems if isinstance(t, Via)]
        assert len(vias) == 1

    def test_no_filters_returns_error(self, scratch_pcb):
        result = pcb.remove_traces(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data
