"""Tests for PCB read tools."""

import uuid

import pytest
from conftest import _default_effects
from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.common import Net, Position
from kiutils.items.fpitems import FpRect, FpText
from kiutils.items.gritems import GrLine
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import pcb
from mcp_server_kicad.models import (
    BoardValidationResult,
    FootprintBoundsResult,
)


class TestListPcbItems:
    def test_list_footprints(self, scratch_pcb):
        result = pcb.list_pcb_footprints(str(scratch_pcb))
        assert isinstance(result, list)
        refs = [fp.reference for fp in result]
        assert "R1" in refs

    def test_list_footprints_empty(self, tmp_path):
        from kiutils.board import Board

        b = Board.create_new()
        path = str(tmp_path / "empty.kicad_pcb")
        b.filePath = path
        b.to_file()
        result = pcb.list_pcb_footprints(path)
        assert result == []

    def test_list_traces(self, scratch_pcb):
        result = pcb.list_pcb_traces(str(scratch_pcb))
        assert isinstance(result, list)

    def test_list_traces_empty(self, tmp_path):
        from kiutils.board import Board

        b = Board.create_new()
        path = str(tmp_path / "empty.kicad_pcb")
        b.filePath = path
        b.to_file()
        result = pcb.list_pcb_traces(path)
        assert result == []

    def test_list_nets(self, scratch_pcb):
        result = pcb.list_pcb_nets(str(scratch_pcb))
        assert isinstance(result, list)
        names = [n.name for n in result]
        assert "Net1" in names
        assert "Net2" in names

    def test_list_zones(self, scratch_pcb):
        result = pcb.list_pcb_zones(str(scratch_pcb))
        assert isinstance(result, list)
        assert result == []

    def test_list_layers(self, scratch_pcb):
        result = pcb.list_pcb_layers(str(scratch_pcb))
        assert isinstance(result, list)
        assert len(result) > 0

    def test_list_graphic_items(self, scratch_pcb):
        result = pcb.list_pcb_graphic_items(str(scratch_pcb))
        assert isinstance(result, list)


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
        with pytest.raises(ToolError, match="not found"):
            pcb.get_footprint_pads("R999", str(scratch_pcb))


# ---------------------------------------------------------------------------
# Helper to build a board with a keepout zone
# ---------------------------------------------------------------------------


def _make_keepout_board(tmp_path, *, with_copper_zone=False):
    """Create a board with a keepout zone (and optionally a copper zone)."""
    board = Board.create_new()
    board.nets = [Net(number=0, name=""), Net(number=1, name="Net1")]

    # Keepout zone — covers (10,10) to (40,40)
    kz = Zone()
    kz.net = 0
    kz.netName = ""
    kz.layers = ["F.Cu", "B.Cu"]
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

    if with_copper_zone:
        cz = Zone()
        cz.net = 1
        cz.netName = "Net1"
        cz.layers = ["F.Cu"]
        cz.tstamp = str(uuid.uuid4())
        cz.hatch = Hatch(style="edge", pitch=0.5)
        cpoly = ZonePolygon()
        cpoly.coordinates = [
            Position(X=50, Y=50),
            Position(X=80, Y=50),
            Position(X=80, Y=80),
            Position(X=50, Y=80),
        ]
        cz.polygons = [cpoly]
        board.zones.append(cz)

    path = tmp_path / "keepout_board.kicad_pcb"
    board.filePath = str(path)
    board.to_file()
    return path


class TestListZonesKeepout:
    def test_list_zones_includes_keepout_info(self, tmp_path):
        pcb_path = _make_keepout_board(tmp_path)
        result = pcb.list_pcb_zones(str(pcb_path))
        assert len(result) >= 1
        kz = result[0]
        assert kz.is_keepout is True
        assert kz.keepout is not None
        assert kz.keepout["footprints"] == "not_allowed"
        assert kz.polygon is not None

    def test_list_zones_copper_vs_keepout(self, tmp_path):
        pcb_path = _make_keepout_board(tmp_path, with_copper_zone=True)
        result = pcb.list_pcb_zones(str(pcb_path))
        assert len(result) == 2
        keepouts = [z for z in result if z.is_keepout]
        coppers = [z for z in result if not z.is_keepout]
        assert len(keepouts) == 1
        assert len(coppers) == 1
        assert coppers[0].net_name == "Net1"


# ---------------------------------------------------------------------------
# get_footprint_bounds
# ---------------------------------------------------------------------------


def _make_board_with_courtyard_fp(tmp_path, *, rotation=0):
    """Create a board with a single footprint that has a courtyard rect."""
    board = Board.create_new()
    board.nets = [Net(number=0, name=""), Net(number=1, name="Net1")]

    fp = Footprint()
    fp.entryName = "TestFP"
    fp.libId = "Test:TestFP"
    fp.layer = "F.Cu"
    fp.position = Position(X=100, Y=100, angle=rotation)
    fp.properties = {"Reference": "U1", "Value": "Chip"}
    fp.graphicItems = [
        FpText(
            type="reference",
            text="U1",
            layer="F.SilkS",
            effects=_default_effects(),
            position=Position(X=0, Y=-2),
        ),
    ]

    # Add courtyard rectangle: -5 to 5 in both axes (local coords)
    rect = FpRect()
    rect.start = Position(X=-5, Y=-5)
    rect.end = Position(X=5, Y=5)
    rect.layer = "F.CrtYd"
    fp.graphicItems.append(rect)

    pad = Pad()
    pad.number = "1"
    pad.type = "smd"
    pad.shape = "rect"
    pad.position = Position(X=0, Y=0)
    pad.size = Position(X=1, Y=1)
    pad.layers = ["F.Cu"]
    pad.net = Net(number=1, name="Net1")
    fp.pads = [pad]

    board.footprints.append(fp)
    path = tmp_path / "bounds_test.kicad_pcb"
    board.filePath = str(path)
    board.to_file()
    return path


class TestGetFootprintBounds:
    def test_basic_bounds(self, tmp_path):
        pcb_path = _make_board_with_courtyard_fp(tmp_path, rotation=0)
        result = pcb.get_footprint_bounds("U1", pcb_path=str(pcb_path))
        assert isinstance(result, FootprintBoundsResult)
        assert result.reference == "U1"
        assert result.courtyard is not None
        cy = result.courtyard
        # FP at (100,100) with local courtyard -5..5 => board coords 95..105
        assert cy["min_x"] == 95
        assert cy["max_x"] == 105
        assert cy["min_y"] == 95
        assert cy["max_y"] == 105

    def test_rotated_90(self, tmp_path):
        pcb_path = _make_board_with_courtyard_fp(tmp_path, rotation=90)
        result = pcb.get_footprint_bounds("U1", pcb_path=str(pcb_path))
        assert isinstance(result, FootprintBoundsResult)
        assert result.courtyard is not None
        cy = result.courtyard
        # Square courtyard rotated 90 degrees should still be ~95..105
        import pytest as pt

        assert cy["min_x"] == pt.approx(95, abs=0.1)
        assert cy["max_x"] == pt.approx(105, abs=0.1)
        assert cy["min_y"] == pt.approx(95, abs=0.1)
        assert cy["max_y"] == pt.approx(105, abs=0.1)

    def test_no_courtyard(self, tmp_path):
        """Footprint without courtyard items returns courtyard: null."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        fp = Footprint()
        fp.entryName = "Bare"
        fp.libId = "Test:Bare"
        fp.layer = "F.Cu"
        fp.position = Position(X=50, Y=50, angle=0)
        fp.properties = {"Reference": "J1", "Value": "Conn"}
        fp.graphicItems = [
            FpText(
                type="reference",
                text="J1",
                layer="F.SilkS",
                effects=_default_effects(),
                position=Position(X=0, Y=-1),
            ),
        ]
        board.footprints.append(fp)
        path = tmp_path / "no_crtyd.kicad_pcb"
        board.filePath = str(path)
        board.to_file()

        result = pcb.get_footprint_bounds("J1", pcb_path=str(path))
        assert isinstance(result, FootprintBoundsResult)
        assert result.courtyard is None

    def test_not_found(self, tmp_path):
        """Invalid reference raises ToolError."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        path = tmp_path / "empty.kicad_pcb"
        board.filePath = str(path)
        board.to_file()

        with pytest.raises(ToolError, match="not found"):
            pcb.get_footprint_bounds("R999", pcb_path=str(path))


# ---------------------------------------------------------------------------
# validate_board
# ---------------------------------------------------------------------------


def _make_board_for_validation(tmp_path, *, fp_inside_keepout=False):
    """Create a board with Edge.Cuts rectangle and optional keepout violation."""
    board = Board.create_new()
    board.nets = [Net(number=0, name=""), Net(number=1, name="Net1")]

    # Board edge: rectangle from (0,0) to (200,200)
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

    # Keepout zone: (10,10) to (40,40)
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

    # Add a footprint
    fp = Footprint()
    fp.entryName = "R_0603"
    fp.libId = "Test:R_0603"
    fp.layer = "F.Cu"
    if fp_inside_keepout:
        fp.position = Position(X=25, Y=25, angle=0)  # inside keepout
    else:
        fp.position = Position(X=100, Y=100, angle=0)  # safe position
    fp.properties = {"Reference": "R1", "Value": "10K"}
    fp.graphicItems = [
        FpText(
            type="reference",
            text="R1",
            layer="F.SilkS",
            effects=_default_effects(),
            position=Position(X=0, Y=-1),
        ),
    ]
    board.footprints.append(fp)

    path = tmp_path / "validate_test.kicad_pcb"
    board.filePath = str(path)
    board.to_file()
    return path


class TestValidateBoard:
    def test_validate_board_clean(self, tmp_path):
        pcb_path = _make_board_for_validation(tmp_path, fp_inside_keepout=False)
        result = pcb.validate_board(pcb_path=str(pcb_path))
        assert isinstance(result, BoardValidationResult)
        assert result.status == "ok"
        assert len(result.violations) == 0

    def test_validate_board_with_violations(self, tmp_path):
        pcb_path = _make_board_for_validation(tmp_path, fp_inside_keepout=True)
        result = pcb.validate_board(pcb_path=str(pcb_path))
        assert isinstance(result, BoardValidationResult)
        assert result.status != "ok"
        assert len(result.violations) >= 1
        # The violating footprint should be R1
        refs = [v["reference"] for v in result.violations]
        assert "R1" in refs
