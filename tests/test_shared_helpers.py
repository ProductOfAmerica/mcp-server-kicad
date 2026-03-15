"""Tests for shared helper functions in _shared.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import _default_effects, new_schematic
from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Net, Position
from kiutils.items.fpitems import FpCircle, FpLine, FpRect
from kiutils.items.gritems import GrArc, GrLine
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon

from mcp_server_kicad._shared import (
    _board_edge_polygon,
    _check_footprint_keepout_violations,
    _courtyard_bbox,
    _gen_uuid,
    _point_in_polygon,
    _resolve_hierarchy_path,
    _transform_local_to_board,
)


class TestResolveHierarchyPath:
    def test_root_schematic_returns_own_uuid(self, tmp_path: Path):
        """When schematic IS the root, return project name and /{uuid}."""
        sch = new_schematic()
        sch_path = tmp_path / "myproject.kicad_sch"
        sch.filePath = str(sch_path)
        sch.to_file()

        pro_path = str(tmp_path / "myproject.kicad_pro")
        assert sch.uuid is not None
        name, path = _resolve_hierarchy_path(pro_path, str(sch_path), sch.uuid)
        assert name == "myproject"
        assert path == f"/{sch.uuid}"

    def test_sub_sheet_returns_root_uuid_and_sheet_uuid(self, tmp_path: Path):
        """When schematic is a sub-sheet, return root project name and /{root_uuid}/{sheet_uuid}."""
        from kiutils.items.common import Effects, Font, Position, Property
        from kiutils.items.schitems import HierarchicalSheet

        root_sch = new_schematic()
        root_path = tmp_path / "myproject.kicad_sch"
        root_sch.filePath = str(root_path)

        sheet = HierarchicalSheet()
        sheet.uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        sheet.position = Position(X=25.4, Y=25.4)
        sheet.width = 25.4
        sheet.height = 10.16
        sheet.sheetName = Property(
            key="Sheetname",
            value="Power",
            id=0,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=25.4, Y=24.13, angle=0),
        )
        sheet.fileName = Property(
            key="Sheetfile",
            value="power-supply.kicad_sch",
            id=1,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=25.4, Y=36.83, angle=0),
        )
        root_sch.sheets.append(sheet)
        root_sch.to_file()

        child_sch = new_schematic()
        child_path = tmp_path / "power-supply.kicad_sch"
        child_sch.filePath = str(child_path)
        child_sch.to_file()

        pro_path = str(tmp_path / "myproject.kicad_pro")
        assert child_sch.uuid is not None
        assert root_sch.uuid is not None
        name, path = _resolve_hierarchy_path(pro_path, str(child_path), child_sch.uuid)
        assert name == "myproject"
        assert path == f"/{root_sch.uuid}/{sheet.uuid}"


class TestResolveRoot:
    def test_returns_root_from_project_path(self, tmp_path: Path):
        """When project_path is given, derive root .kicad_sch from it."""
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")
        sub_sch = tmp_path / "child.kicad_sch"
        sub_sch.write_text("")

        result = _resolve_root(str(sub_sch), project_path=str(pro))
        assert result == str(root_sch)

    def test_returns_none_when_already_root_via_project(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")

        result = _resolve_root(str(root_sch), project_path=str(pro))
        assert result is None

    def test_falls_back_to_glob_when_no_project_path(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        pro = tmp_path / "myproj.kicad_pro"
        pro.write_text("{}")
        root_sch = tmp_path / "myproj.kicad_sch"
        root_sch.write_text("")
        sub_sch = tmp_path / "child.kicad_sch"
        sub_sch.write_text("")

        result = _resolve_root(str(sub_sch))
        assert result == str(root_sch)

    def test_returns_none_when_no_project_found(self, tmp_path: Path):
        from mcp_server_kicad._shared import _resolve_root

        sch = tmp_path / "standalone.kicad_sch"
        sch.write_text("")

        result = _resolve_root(str(sch))
        assert result is None


@pytest.mark.no_kicad_validation
class TestLoadSchCachesSystemLibSymbols:
    def test_caches_system_lib_symbol_on_load(self, tmp_path, monkeypatch):
        """_load_sch caches raw text for system lib symbols found in the schematic."""
        from mcp_server_kicad._shared import (
            _RAW_LIB_SYMBOLS,
            _load_sch,
        )

        # Create a fake system library file
        lib_content = """(kicad_symbol_lib
  (version 20231120)
  (generator "kicad_symbol_editor")
  (symbol "TestSym"
    (pin_names (offset 0))
    (in_bom yes)
    (on_board yes)
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "TestSym" (at 0 2.54 0) (effects (font (size 1.27 1.27))))
    (symbol "TestSym_0_1"
      (rectangle (start -2.54 -2.54) (end 2.54 2.54)
        (stroke (width 0) (type default))
        (fill (type none))
      )
    )
    (symbol "TestSym_1_1"
      (pin passive line (at -5.08 0 0) (length 2.54)
        (name "A" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )
)"""
        lib_dir = tmp_path / "symbols"
        lib_dir.mkdir()
        lib_file = lib_dir / "TestLib.kicad_sym"
        lib_file.write_text(lib_content)

        # Create a schematic with a lib_symbol "TestSym" and a placed
        # schematicSymbol whose libId is "TestLib:TestSym" (mirrors real KiCad
        # schematics where lib_symbols have bare names and schematicSymbols
        # carry the fully-qualified libId).
        from kiutils.items.common import Position, Property
        from kiutils.items.schitems import SchematicSymbol
        from kiutils.symbol import Symbol

        sch = new_schematic()
        lib_sym = Symbol()
        lib_sym.entryName = "TestSym"
        sch.libSymbols.append(lib_sym)

        placed = SchematicSymbol()
        placed.libId = "TestLib:TestSym"
        placed.position = Position(X=100, Y=100, angle=0)
        placed.uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        placed.unit = 1
        placed.properties = [
            Property(
                key="Reference",
                value="U1",
                id=0,
                effects=_default_effects(),
                position=Position(X=100, Y=97, angle=0),
            ),
        ]
        placed.pins = {"1": "11111111-2222-3333-4444-555555555555"}
        sch.schematicSymbols.append(placed)

        sch_path = tmp_path / "test.kicad_sch"
        sch.filePath = str(sch_path)
        sch.to_file()

        # Monkeypatch system sym dirs to include our fake dir
        monkeypatch.setattr("mcp_server_kicad._shared._SYSTEM_SYM_DIRS", [lib_dir])

        # Clear cache
        _RAW_LIB_SYMBOLS.clear()

        try:
            _load_sch(str(sch_path))
            assert "TestSym" in _RAW_LIB_SYMBOLS
            assert '(symbol "TestSym"' in _RAW_LIB_SYMBOLS["TestSym"]
        finally:
            _RAW_LIB_SYMBOLS.clear()

    def test_does_not_overwrite_existing_cache(self, tmp_path):
        """_load_sch does not overwrite already-cached symbols."""
        from kiutils.items.common import Position, Property
        from kiutils.items.schitems import SchematicSymbol
        from kiutils.symbol import Symbol

        from mcp_server_kicad._shared import (
            _RAW_LIB_SYMBOLS,
            _load_sch,
        )

        sch = new_schematic()
        lib_sym = Symbol()
        lib_sym.entryName = "SomeSym"
        sch.libSymbols.append(lib_sym)

        placed = SchematicSymbol()
        placed.libId = "SomeLib:SomeSym"
        placed.position = Position(X=100, Y=100, angle=0)
        placed.uuid = "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"
        placed.unit = 1
        placed.properties = [
            Property(
                key="Reference",
                value="U1",
                id=0,
                effects=_default_effects(),
                position=Position(X=100, Y=97, angle=0),
            ),
        ]
        placed.pins = {"1": "11111111-2222-3333-4444-666666666666"}
        sch.schematicSymbols.append(placed)

        sch_path = tmp_path / "test2.kicad_sch"
        sch.filePath = str(sch_path)
        sch.to_file()

        sentinel = '(symbol "SomeSym" ORIGINAL_CACHED)'
        _RAW_LIB_SYMBOLS["SomeSym"] = sentinel

        try:
            _load_sch(str(sch_path))
            # Should not have been overwritten
            assert _RAW_LIB_SYMBOLS["SomeSym"] == sentinel
        finally:
            _RAW_LIB_SYMBOLS.clear()


# ---------------------------------------------------------------------------
# _point_in_polygon
# ---------------------------------------------------------------------------

UNIT_SQUARE: list[tuple[float, float]] = [
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0),
]


class TestPointInPolygon:
    def test_inside(self):
        assert _point_in_polygon(0.5, 0.5, UNIT_SQUARE) is True

    def test_outside(self):
        assert _point_in_polygon(2.0, 2.0, UNIT_SQUARE) is False

    def test_empty_polygon(self):
        assert _point_in_polygon(0.5, 0.5, []) is False

    def test_degenerate_one_point(self):
        assert _point_in_polygon(0.0, 0.0, [(0.0, 0.0)]) is False

    def test_degenerate_two_points(self):
        assert _point_in_polygon(0.5, 0.5, [(0.0, 0.0), (1.0, 1.0)]) is False

    def test_on_vertex_no_crash(self):
        # Must not raise; result may be True or False depending on algorithm
        result = _point_in_polygon(0.0, 0.0, UNIT_SQUARE)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _transform_local_to_board
# ---------------------------------------------------------------------------


class TestTransformLocalToBoard:
    def test_zero_rotation(self):
        bx, by = _transform_local_to_board(10, 20, 0, 3, 4)
        assert bx == pytest.approx(13)
        assert by == pytest.approx(24)

    def test_90_degrees(self):
        bx, by = _transform_local_to_board(10, 20, 90, 3, 4)
        # rotation 90: x' = fp_x + (lx*cos90 - ly*sin90) = 10 + (0 - 4) = 6
        #              y' = fp_y + (lx*sin90 + ly*cos90) = 20 + (3 + 0) = 23
        assert bx == pytest.approx(6, abs=0.01)
        assert by == pytest.approx(23, abs=0.01)

    def test_mirrored_zero_rotation(self):
        bx, by = _transform_local_to_board(10, 20, 0, 3, 4, mirrored=True)
        assert bx == pytest.approx(7)
        assert by == pytest.approx(24)

    def test_mirrored_false_unchanged(self):
        bx, by = _transform_local_to_board(10, 20, 0, 3, 4, mirrored=False)
        assert bx == pytest.approx(13)
        assert by == pytest.approx(24)

    def test_mirrored_with_rotation(self):
        bx, by = _transform_local_to_board(10, 20, 90, 3, 4, mirrored=True)
        assert bx == pytest.approx(6, abs=0.01)
        assert by == pytest.approx(17, abs=0.01)


# ---------------------------------------------------------------------------
# _board_edge_polygon
# ---------------------------------------------------------------------------


def _make_board_with_edges(tmp_path, lines):
    """Create a Board with GrLine items on Edge.Cuts from (sx,sy,ex,ey) tuples."""
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
    board.filePath = str(tmp_path / "edge_test.kicad_pcb")
    board.to_file()
    return board


class TestBoardEdgePolygon:
    def test_closed_rectangle(self, tmp_path):
        lines = [
            (0, 0, 50, 0),
            (50, 0, 50, 50),
            (50, 50, 0, 50),
            (0, 50, 0, 0),
        ]
        board = _make_board_with_edges(tmp_path, lines)
        poly = _board_edge_polygon(board)
        assert poly is not None
        assert len(poly) == 4

    def test_no_edges(self, tmp_path):
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        board.filePath = str(tmp_path / "no_edges.kicad_pcb")
        board.to_file()
        poly = _board_edge_polygon(board)
        assert poly is None

    def test_with_arcs(self, tmp_path):
        """Board with GrArc on Edge.Cuts produces a polygon (arcs linearized)."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]

        # 3 lines + 1 arc forming a closed shape
        for sx, sy, ex, ey in [
            (0, 0, 50, 0),
            (50, 0, 50, 50),
            (0, 50, 0, 0),
        ]:
            gl = GrLine()
            gl.start = Position(X=sx, Y=sy)
            gl.end = Position(X=ex, Y=ey)
            gl.layer = "Edge.Cuts"
            gl.width = 0.05
            gl.tstamp = _gen_uuid()
            board.graphicItems.append(gl)

        arc = GrArc()
        arc.start = Position(X=50, Y=50)
        arc.mid = Position(X=25, Y=60)
        arc.end = Position(X=0, Y=50)
        arc.layer = "Edge.Cuts"
        arc.width = 0.05
        arc.tstamp = _gen_uuid()
        board.graphicItems.append(arc)

        board.filePath = str(tmp_path / "arcs.kicad_pcb")
        board.to_file()

        poly = _board_edge_polygon(board)
        assert poly is not None
        assert len(poly) >= 4

    def test_t_junction_no_crash(self, tmp_path):
        """A T-junction on Edge.Cuts does not crash."""
        lines = [
            (0, 0, 50, 0),
            (50, 0, 50, 50),
            (50, 50, 0, 50),
            (0, 50, 0, 0),
            (25, 0, 25, -20),  # T-branch
        ]
        board = _make_board_with_edges(tmp_path, lines)
        # Should not crash
        _board_edge_polygon(board)

    def test_multiple_outlines_no_crash(self, tmp_path):
        """Two separate rectangles on Edge.Cuts: returns one polygon, no crash."""
        lines = [
            (0, 0, 20, 0),
            (20, 0, 20, 20),
            (20, 20, 0, 20),
            (0, 20, 0, 0),
            (100, 100, 120, 100),
            (120, 100, 120, 120),
            (120, 120, 100, 120),
            (100, 120, 100, 100),
        ]
        board = _make_board_with_edges(tmp_path, lines)
        poly = _board_edge_polygon(board)
        assert poly is not None
        assert len(poly) == 4


# ---------------------------------------------------------------------------
# _courtyard_bbox
# ---------------------------------------------------------------------------


class TestCourtyardBbox:
    def test_from_lines(self):
        fp = Footprint()
        fp.entryName = "Test"
        for sx, sy, ex, ey in [
            (-2, -1, 2, -1),
            (2, -1, 2, 1),
            (2, 1, -2, 1),
            (-2, 1, -2, -1),
        ]:
            line = FpLine()
            line.start = Position(X=sx, Y=sy)
            line.end = Position(X=ex, Y=ey)
            line.layer = "F.CrtYd"
            fp.graphicItems.append(line)

        bbox = _courtyard_bbox(fp)
        assert bbox is not None
        assert bbox["layer"] == "F.CrtYd"
        assert bbox["min_x"] == pytest.approx(-2)
        assert bbox["max_x"] == pytest.approx(2)
        assert bbox["min_y"] == pytest.approx(-1)
        assert bbox["max_y"] == pytest.approx(1)

    def test_from_rect(self):
        fp = Footprint()
        fp.entryName = "Test"
        rect = FpRect()
        rect.start = Position(X=-3, Y=-2)
        rect.end = Position(X=3, Y=2)
        rect.layer = "F.CrtYd"
        fp.graphicItems.append(rect)

        bbox = _courtyard_bbox(fp)
        assert bbox is not None
        assert bbox["layer"] == "F.CrtYd"
        assert bbox["min_x"] == pytest.approx(-3)
        assert bbox["max_x"] == pytest.approx(3)
        assert bbox["width"] == pytest.approx(6)
        assert bbox["height"] == pytest.approx(4)

    def test_mixed_layers_returns_first(self):
        """F.CrtYd + B.CrtYd items: returns F.CrtYd (preferred)."""
        fp = Footprint()
        fp.entryName = "Test"

        line_f = FpLine()
        line_f.start = Position(X=-1, Y=-1)
        line_f.end = Position(X=1, Y=1)
        line_f.layer = "F.CrtYd"
        fp.graphicItems.append(line_f)

        line_b = FpLine()
        line_b.start = Position(X=-5, Y=-5)
        line_b.end = Position(X=5, Y=5)
        line_b.layer = "B.CrtYd"
        fp.graphicItems.append(line_b)

        bbox = _courtyard_bbox(fp)
        assert bbox is not None
        assert bbox["layer"] == "F.CrtYd"
        assert bbox["min_x"] == pytest.approx(-1)
        assert bbox["max_x"] == pytest.approx(1)

    def test_none_when_no_courtyard(self):
        fp = Footprint()
        fp.entryName = "Test"
        assert _courtyard_bbox(fp) is None

    def test_from_circle(self):
        fp = Footprint()
        fp.entryName = "Test"
        circle = FpCircle()
        circle.center = Position(X=0, Y=0)
        circle.end = Position(X=5, Y=0)  # radius = 5
        circle.layer = "F.CrtYd"
        fp.graphicItems.append(circle)

        bbox = _courtyard_bbox(fp)
        assert bbox is not None
        assert bbox["min_x"] == pytest.approx(-5)
        assert bbox["max_x"] == pytest.approx(5)
        assert bbox["min_y"] == pytest.approx(-5)
        assert bbox["max_y"] == pytest.approx(5)


# ---------------------------------------------------------------------------
# _check_footprint_keepout_violations — layer mismatch
# ---------------------------------------------------------------------------


class TestCheckKeepoutViolationsLayerMismatch:
    def test_layer_mismatch_no_violation(self, tmp_path):
        """Keepout zone on F.Cu only; checking B.Cu should not violate."""
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

        violations = _check_footprint_keepout_violations(board, 50, 50, "B.Cu")
        assert len(violations) == 0
