"""Tests for PCB write tools."""

import json
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import _default_effects
from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Net, Position
from kiutils.items.fpitems import FpText
from kiutils.items.gritems import GrLine
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon

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

    def test_fixes_displaced_text_after_routing(self, scratch_pcb, tmp_path):
        """Freerouting DSN->SES round-trip scrambles footprint text positions.

        After autoroute_pcb imports the SES file, text fields (Reference,
        Value) that have been displaced far from the footprint center should
        be reset to sensible default offsets.
        """

        def mock_export_dsn(pcb_path, dsn_path):
            Path(dsn_path).touch()
            return None

        def mock_import_ses(pcb_path, ses_path, output_path):
            """Simulate Freerouting scrambling text positions."""
            shutil.copy(pcb_path, output_path)
            board = Board.from_file(output_path)
            # Scramble text positions to simulate Freerouting bug:
            # displace reference and value text far from footprint center
            for fp in board.footprints:
                for item in fp.graphicItems:
                    if isinstance(item, FpText) and item.type == "reference":
                        # Move reference to an absurd position (50mm away)
                        item.position = Position(X=50, Y=-50)
                    elif isinstance(item, FpText) and item.type == "value":
                        # Move value to an absurd position (30mm away)
                        item.position = Position(X=-30, Y=40)
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
            assert data.get("text_fields_fixed", 0) > 0

            # Load the routed board and verify text positions are reasonable
            routed_board = Board.from_file(data["routed_path"])
            for fp in routed_board.footprints:
                for item in fp.graphicItems:
                    if isinstance(item, FpText) and item.type in ("reference", "value"):
                        # Text should be within 5mm of footprint center (0,0 relative)
                        dist = (item.position.X**2 + item.position.Y**2) ** 0.5
                        assert dist <= 5.0, (
                            f"{item.type} text for {_fp_ref(fp)} is {dist:.1f}mm "
                            f"from center at ({item.position.X}, {item.position.Y})"
                        )


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

    def test_consecutive_calls_on_different_nets(self, scratch_pcb):
        """Calling set_trace_width on one net then another must not crash.

        Regression: the first call saves the board via kiutils; the second
        call must be able to re-read the file that kiutils wrote.
        """
        _board_with_traces(scratch_pcb)
        # First call — widen Net1
        r1 = json.loads(pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=str(scratch_pcb)))
        assert "error" not in r1
        assert r1["traces_modified"] > 0
        # Second call — widen Net2 (re-reads the file saved by the first call)
        r2 = json.loads(pcb.set_trace_width(width=0.75, net_name="Net2", pcb_path=str(scratch_pcb)))
        assert "error" not in r2
        assert r2["traces_modified"] > 0

    def test_roundtrip_kicad9_uuid_segments(self, tmp_path):
        """KiCad 9 uses ``(uuid ...)`` instead of ``(tstamp ...)`` in segments.

        kiutils 1.4.8 only handles ``tstamp``, so it writes ``(tstamp )``
        with an empty value after loading a KiCad 9 board. The second load
        then crashes with ``IndexError: list index out of range``.

        The tool must handle this gracefully — either by preserving the uuid
        or by generating a valid tstamp so the file remains loadable.
        """
        # Create a board file that uses KiCad 9 format with uuid instead of tstamp
        pcb_content = """(kicad_pcb (version 20241108) (generator "pcbnew")

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

  (segment (start 100 100) (end 110 100) (width 0.25)
    (layer "F.Cu") (net 1) (uuid "aaa-1111-2222"))
  (segment (start 110 100) (end 120 100) (width 0.25)
    (layer "F.Cu") (net 1) (uuid "bbb-1111-2222"))
  (segment (start 120 100) (end 130 100) (width 0.25)
    (layer "F.Cu") (net 2) (uuid "ccc-1111-2222"))

)"""
        pcb_file = tmp_path / "kicad9_board.kicad_pcb"
        pcb_file.write_text(pcb_content)

        # First call should succeed
        r1 = json.loads(pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=str(pcb_file)))
        assert "error" not in r1
        assert r1["traces_modified"] == 2

        # Second call must NOT crash with IndexError
        r2 = json.loads(pcb.set_trace_width(width=0.75, net_name="Net2", pcb_path=str(pcb_file)))
        assert "error" not in r2
        assert r2["traces_modified"] == 1

    def test_load_board_with_empty_tstamp(self, tmp_path):
        """Board files with ``(tstamp )`` (empty value) must load.

        Regression: a previous kiutils round-trip wrote ``(tstamp )``
        for segments whose uuid was unrecognized.  The resulting file
        cannot be re-loaded by kiutils because ``item[1]`` does not
        exist on a single-element list ``['tstamp']``.

        ``_load_board`` must sanitise the file content before parsing.
        """
        pcb_content = """\
(kicad_pcb (version 20241108) (generator "pcbnew")

  (general (thickness 1.6))
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )
  (setup (pad_to_mask_clearance 0))

  (net 0 "")
  (net 1 "Net1")

  (segment (start 100 100) (end 110 100) (width 0.5)
    (layer "F.Cu") (net 1) (tstamp ))
  (segment (start 110 100) (end 120 100) (width 0.5)
    (layer "F.Cu") (net 1) (tstamp ))

)"""
        pcb_file = tmp_path / "corrupted.kicad_pcb"
        pcb_file.write_text(pcb_content)

        # Must not crash with IndexError
        r = json.loads(pcb.set_trace_width(width=0.75, net_name="Net1", pcb_path=str(pcb_file)))
        assert "error" not in r
        assert r["traces_modified"] == 2


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


class TestAddThermalVias:
    def test_basic_grid(self, scratch_pcb):
        """R1 is at (100, 100) with pads. Use explicit pad_number and net_name."""
        result = pcb.add_thermal_vias(
            reference="R1",
            pad_number="1",
            rows=2,
            cols=2,
            spacing=1.0,
            via_size=0.6,
            via_drill=0.3,
            net_name="Net1",
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["vias_added"] == 4
        assert data["reference"] == "R1"
        board = Board.from_file(str(scratch_pcb))
        vias = [t for t in board.traceItems if isinstance(t, Via)]
        assert len(vias) == 4

    def test_footprint_not_found(self, scratch_pcb):
        result = pcb.add_thermal_vias(reference="U99", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data

    def test_auto_detect_net_from_pad(self, scratch_pcb):
        """When net_name is not provided, auto-detect from pad."""
        result = pcb.add_thermal_vias(
            reference="R1",
            pad_number="1",
            rows=1,
            cols=1,
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["vias_added"] == 1
        board = Board.from_file(str(scratch_pcb))
        via = [t for t in board.traceItems if isinstance(t, Via)][0]
        # Pad 1 of R1 should have a net number assigned
        assert via.net >= 0

    def test_pad_not_found(self, scratch_pcb):
        result = pcb.add_thermal_vias(reference="R1", pad_number="99", pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data

    def test_invalid_net_name(self, scratch_pcb):
        result = pcb.add_thermal_vias(
            reference="R1",
            pad_number="1",
            net_name="NonExistent",
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert "error" in data

    def test_auto_detect_largest_smd_pad(self, scratch_pcb):
        """When no pad_number given, pick largest SMD pad."""
        result = pcb.add_thermal_vias(
            reference="R1",
            rows=1,
            cols=1,
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["vias_added"] == 1
        # Should have picked one of the pads (both are same size)
        assert data["pad"] in ("1", "2")


# ---------------------------------------------------------------------------
# Helper: board with keepout zone and edge cuts for move/check tests
# ---------------------------------------------------------------------------


def _make_keepout_pcb(tmp_path, *, with_edge_cuts=True):
    """Build a PCB with a footprint, keepout zone, and optionally Edge.Cuts."""
    board = Board.create_new()
    board.nets = [Net(number=0, name=""), Net(number=1, name="Net1")]

    # Footprint at safe position (100, 100)
    fp = Footprint()
    fp.entryName = "R_0603"
    fp.libId = "Test:R_0603"
    fp.layer = "F.Cu"
    fp.position = Position(X=100, Y=100, angle=0)
    fp.properties = {"Reference": "R1", "Value": "10K"}
    fp.graphicItems = [
        FpText(
            type="reference",
            text="R1",
            layer="F.SilkS",
            effects=_default_effects(),
            position=Position(X=0, Y=-2),
        ),
    ]
    pad1 = Pad()
    pad1.number = "1"
    pad1.type = "smd"
    pad1.shape = "rect"
    pad1.position = Position(X=-0.75, Y=0)
    pad1.size = Position(X=0.7, Y=0.8)
    pad1.layers = ["F.Cu"]
    pad1.net = Net(number=1, name="Net1")
    fp.pads = [pad1]
    board.footprints.append(fp)

    # Keepout zone: (10, 10) to (40, 40) on F.Cu
    kz = Zone()
    kz.net = 0
    kz.netName = ""
    kz.layers = ["F.Cu"]
    kz.tstamp = str(uuid.uuid4())
    kz.hatch = Hatch(style="edge", pitch=0.5)
    kz.keepoutSettings = KeepoutSettings(
        tracks="not_allowed",
        vias="not_allowed",
        pads="not_allowed",
        copperpour="not_allowed",
        footprints="not_allowed",
    )
    poly = ZonePolygon()
    poly.coordinates = [
        Position(X=10, Y=10),
        Position(X=40, Y=10),
        Position(X=40, Y=40),
        Position(X=10, Y=40),
    ]
    kz.polygons = [poly]
    board.zones.append(kz)

    if with_edge_cuts:
        for sx, sy, ex, ey in [
            (0, 0, 200, 0),
            (200, 0, 200, 200),
            (200, 200, 0, 200),
            (0, 200, 0, 0),
        ]:
            gl = GrLine()
            gl.start = Position(X=sx, Y=sy)
            gl.end = Position(X=ex, Y=ey)
            gl.layer = "Edge.Cuts"
            gl.width = 0.05
            gl.tstamp = str(uuid.uuid4())
            board.graphicItems.append(gl)

    path = tmp_path / "keepout_test.kicad_pcb"
    board.filePath = str(path)
    board.to_file()
    return path


class TestMoveFootprintKeepout:
    def test_move_into_keepout_warning(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path)
        result = pcb.move_footprint("R1", 25, 25, pcb_path=str(pcb_path))
        assert "WARNING" in result

    def test_move_outside_board_edge_warning(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path)
        result = pcb.move_footprint("R1", 500, 500, pcb_path=str(pcb_path))
        assert "WARNING" in result

    def test_move_to_safe_position_no_warning(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path)
        result = pcb.move_footprint("R1", 100, 100, pcb_path=str(pcb_path))
        assert "WARNING" not in result
        assert "Moved" in result

    def test_move_succeeds_without_edge_cuts(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path, with_edge_cuts=False)
        result = pcb.move_footprint("R1", 150, 150, pcb_path=str(pcb_path))
        assert "Moved" in result
        assert "WARNING" not in result


class TestCheckPlacement:
    def test_safe_position(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path)
        result = json.loads(pcb.check_placement("R1", 100, 100, pcb_path=str(pcb_path)))
        assert result["status"] == "ok"

    def test_violation_inside_keepout(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path)
        result = json.loads(pcb.check_placement("R1", 25, 25, pcb_path=str(pcb_path)))
        assert result["status"] == "violations_found"
        assert len(result["keepout_violations"]) >= 1

    def test_footprint_embedded_keepout(self, tmp_path):
        """Footprint with embedded keepout zone: placement inside triggers violation."""
        board = Board.create_new()
        board.nets = [Net(number=0, name=""), Net(number=1, name="Net1")]

        # Create an "ESP32" footprint with an embedded keepout zone
        esp = Footprint()
        esp.entryName = "ESP32"
        esp.libId = "Test:ESP32"
        esp.layer = "F.Cu"
        esp.position = Position(X=50, Y=50, angle=0)
        esp.properties = {"Reference": "U1", "Value": "ESP32"}
        esp.graphicItems = [
            FpText(
                type="reference",
                text="U1",
                layer="F.SilkS",
                effects=_default_effects(),
                position=Position(X=0, Y=-2),
            ),
        ]

        # Embedded keepout zone in footprint-local coords: (-10,-10) to (10,10)
        ekz = Zone()
        ekz.net = 0
        ekz.netName = ""
        ekz.layers = ["F.Cu"]
        ekz.tstamp = str(uuid.uuid4())
        ekz.hatch = Hatch(style="edge", pitch=0.5)
        ekz.keepoutSettings = KeepoutSettings(
            tracks="not_allowed",
            vias="not_allowed",
            pads="not_allowed",
            copperpour="not_allowed",
            footprints="not_allowed",
        )
        epoly = ZonePolygon()
        epoly.coordinates = [
            Position(X=-10, Y=-10),
            Position(X=10, Y=-10),
            Position(X=10, Y=10),
            Position(X=-10, Y=10),
        ]
        ekz.polygons = [epoly]
        esp.zones = [ekz]
        board.footprints.append(esp)

        # Create a second footprint to check placement of
        fp2 = Footprint()
        fp2.entryName = "R_0603"
        fp2.libId = "Test:R_0603"
        fp2.layer = "F.Cu"
        fp2.position = Position(X=100, Y=100, angle=0)
        fp2.properties = {"Reference": "R1", "Value": "10K"}
        fp2.graphicItems = [
            FpText(
                type="reference",
                text="R1",
                layer="F.SilkS",
                effects=_default_effects(),
                position=Position(X=0, Y=-1),
            ),
        ]
        board.footprints.append(fp2)

        path = tmp_path / "embedded_keepout.kicad_pcb"
        board.filePath = str(path)
        board.to_file()

        # Check placement at ESP32's center (50, 50) — inside the embedded keepout
        result = json.loads(pcb.check_placement("R1", 50, 50, pcb_path=str(path)))
        assert result["status"] == "violations_found"
        assert len(result["keepout_violations"]) >= 1
        sources = [v["source"] for v in result["keepout_violations"]]
        assert any(s.startswith("footprint:") for s in sources)

    def test_outside_board_edge(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path)
        result = json.loads(pcb.check_placement("R1", 500, 500, pcb_path=str(pcb_path)))
        assert result["outside_board_edge"] is True

    def test_no_edge_cuts(self, tmp_path):
        """Board without Edge.Cuts: board_edge_checked should be false."""
        pcb_path = _make_keepout_pcb(tmp_path, with_edge_cuts=False)
        result = json.loads(pcb.check_placement("R1", 100, 100, pcb_path=str(pcb_path)))
        assert result["board_edge_checked"] is False


class TestAddKeepoutZone:
    def test_basic_keepout(self, scratch_pcb):
        corners = [
            {"x": 0, "y": 0},
            {"x": 50, "y": 0},
            {"x": 50, "y": 50},
            {"x": 0, "y": 50},
        ]
        result = pcb.add_keepout_zone(corners=corners, pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["corners"] == 4
        assert "F.Cu" in data["layers"]
        assert data["restrictions"]["footprints"] == "not_allowed"

        # Verify it's actually on the board
        board = Board.from_file(str(scratch_pcb))
        keepout_zones = [z for z in board.zones if z.keepoutSettings is not None]
        assert len(keepout_zones) == 1
        assert len(keepout_zones[0].polygons[0].coordinates) == 4

    def test_too_few_corners(self, scratch_pcb):
        corners = [{"x": 0, "y": 0}, {"x": 10, "y": 0}]
        result = pcb.add_keepout_zone(corners=corners, pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert "error" in data

    def test_custom_restrictions(self, scratch_pcb):
        """no_tracks=False should produce tracks='allowed' in created zone."""
        corners = [
            {"x": 0, "y": 0},
            {"x": 50, "y": 0},
            {"x": 50, "y": 50},
        ]
        result = pcb.add_keepout_zone(
            corners=corners,
            no_tracks=False,
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["restrictions"]["tracks"] == "allowed"
        assert data["restrictions"]["vias"] == "not_allowed"

        # Verify on disk
        board = Board.from_file(str(scratch_pcb))
        kz = [z for z in board.zones if z.keepoutSettings is not None][0]
        assert kz.keepoutSettings is not None
        assert kz.keepoutSettings.tracks == "allowed"


class TestSetNetClass:
    """Tests for set_net_class which edits the .kicad_pro project file."""

    @staticmethod
    def _create_pro(pcb_path: Path, pro_data: dict | None = None) -> Path:
        """Create a .kicad_pro alongside the given .kicad_pcb path."""
        pro_path = pcb_path.with_suffix(".kicad_pro")
        if pro_data is None:
            pro_data = {"meta": {"filename": pro_path.name, "version": 1}}
        pro_path.write_text(json.dumps(pro_data, indent=2) + "\n")
        return pro_path

    def test_works_without_pcbnew(self, scratch_pcb):
        """set_net_class must work without pcbnew — it edits the .kicad_pro file."""
        pro_path = self._create_pro(scratch_pcb)

        result = pcb.set_net_class(
            name="Power",
            nets=["Net1", "Net2"],
            track_width=0.5,
            clearance=0.3,
            via_size=0.6,
            via_drill=0.3,
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["net_class"] == "Power"
        assert data["nets_assigned"] == 2
        assert data["track_width_mm"] == 0.5
        assert data["clearance_mm"] == 0.3

        # Verify the project file was actually written correctly
        pro_data = json.loads(pro_path.read_text())
        ns = pro_data["net_settings"]

        # Check net class was created
        classes = ns["classes"]
        power_cls = next(c for c in classes if c["name"] == "Power")
        assert power_cls["track_width"] == 0.5
        assert power_cls["clearance"] == 0.3
        assert power_cls["via_diameter"] == 0.6
        assert power_cls["via_drill"] == 0.3

        # Check net assignments
        assignments = ns["netclass_assignments"]
        assert assignments["Net1"] == "Power"
        assert assignments["Net2"] == "Power"

    def test_missing_pro_file_returns_error(self, scratch_pcb):
        """Error when .kicad_pro file does not exist."""
        result = pcb.set_net_class(
            name="Power",
            nets=["Net1"],
            track_width=0.5,
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert "error" in data
        assert "Project file not found" in data["error"]

    def test_success_returns_proper_json(self, scratch_pcb):
        """Return JSON with net_class, nets_assigned, track_width_mm, clearance_mm."""
        self._create_pro(scratch_pcb)

        result = pcb.set_net_class(
            name="Power",
            nets=["Net1", "Net2"],
            track_width=0.5,
            clearance=0.3,
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["net_class"] == "Power"
        assert data["nets_assigned"] == 2
        assert data["track_width_mm"] == 0.5
        assert data["clearance_mm"] == 0.3

    def test_updates_existing_net_class(self, scratch_pcb):
        """Updating an existing net class should merge, not duplicate."""
        pro_data = {
            "meta": {"filename": "scratch.kicad_pro", "version": 1},
            "net_settings": {
                "classes": [{"name": "Power", "track_width": 0.25}],
                "meta": {"version": 4},
                "netclass_assignments": {},
            },
        }
        pro_path = self._create_pro(scratch_pcb, pro_data)

        pcb.set_net_class(
            name="Power",
            nets=["Net1"],
            track_width=0.5,
            clearance=0.3,
            pcb_path=str(scratch_pcb),
        )

        updated = json.loads(pro_path.read_text())
        classes = updated["net_settings"]["classes"]
        # Should still be exactly one "Power" class, not two
        power_classes = [c for c in classes if c["name"] == "Power"]
        assert len(power_classes) == 1
        assert power_classes[0]["track_width"] == 0.5
        assert power_classes[0]["clearance"] == 0.3

    def test_optional_params_omitted(self, scratch_pcb):
        """When optional params are None, they should not appear in the class entry."""
        self._create_pro(scratch_pcb)

        result = pcb.set_net_class(
            name="Signal",
            nets=["Net1"],
            pcb_path=str(scratch_pcb),
        )
        data = json.loads(result)
        assert data["net_class"] == "Signal"
        assert data["track_width_mm"] is None
        assert data["clearance_mm"] is None

        pro_data = json.loads(scratch_pcb.with_suffix(".kicad_pro").read_text())
        signal_cls = next(c for c in pro_data["net_settings"]["classes"] if c["name"] == "Signal")
        assert "track_width" not in signal_cls
        assert "clearance" not in signal_cls
        assert "via_diameter" not in signal_cls
        assert "via_drill" not in signal_cls


class TestRemoveDanglingTracks:
    def test_removes_dangling_segment(self, scratch_pcb):
        """Add a trace that connects to nothing."""
        board = Board.from_file(str(scratch_pcb))
        seg = Segment()
        seg.start = Position(X=200, Y=200)
        seg.end = Position(X=210, Y=200)
        seg.width = 0.25
        seg.layer = "F.Cu"
        seg.net = 1
        seg.tstamp = str(uuid.uuid4())
        board.traceItems.append(seg)
        board.to_file()

        result = pcb.remove_dangling_tracks(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["tracks_removed"] >= 1

    def test_preserves_connected_traces(self, scratch_pcb):
        """The scratch board trace connects to R1 pads -- should not be removed."""
        result = pcb.remove_dangling_tracks(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["tracks_removed"] == 0
        board = Board.from_file(str(scratch_pcb))
        segs = [t for t in board.traceItems if isinstance(t, Segment)]
        assert len(segs) == 1

    def test_empty_board(self, scratch_pcb):
        """Board with no traces at all."""
        board = Board.from_file(str(scratch_pcb))
        board.traceItems = []
        board.to_file()
        result = pcb.remove_dangling_tracks(pcb_path=str(scratch_pcb))
        data = json.loads(result)
        assert data["tracks_removed"] == 0
        assert data["iterations"] == 0
