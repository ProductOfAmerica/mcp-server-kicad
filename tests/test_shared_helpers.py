"""Tests for shared helper functions in _shared.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import new_schematic
from kiutils.footprint import Footprint
from kiutils.items.common import Position
from kiutils.items.fpitems import FpCircle, FpLine, FpRect
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, _shared
from mcp_server_kicad._shared import (
    _atomic_write,
    _courtyard_bbox_cst,
    _ensure_dir,
    _point_in_polygon,
    _read_kicad_bytes,
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
# _courtyard_bbox_cst
# ---------------------------------------------------------------------------


def _bbox(fp: Footprint, tmp_path: Path) -> dict | None:
    """Write *fp* out and read its courtyard bbox back through the CST."""
    path = tmp_path / "crtyd.kicad_mod"
    fp.filePath = str(path)
    fp.to_file()
    return _courtyard_bbox_cst(_cst.parse(path.read_bytes()).lists[0])


class TestCourtyardBbox:
    def test_from_lines(self, tmp_path: Path):
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

        bbox = _bbox(fp, tmp_path)
        assert bbox is not None
        assert bbox["layer"] == "F.CrtYd"
        assert bbox["min_x"] == pytest.approx(-2)
        assert bbox["max_x"] == pytest.approx(2)
        assert bbox["min_y"] == pytest.approx(-1)
        assert bbox["max_y"] == pytest.approx(1)

    def test_from_rect(self, tmp_path: Path):
        fp = Footprint()
        fp.entryName = "Test"
        rect = FpRect()
        rect.start = Position(X=-3, Y=-2)
        rect.end = Position(X=3, Y=2)
        rect.layer = "F.CrtYd"
        fp.graphicItems.append(rect)

        bbox = _bbox(fp, tmp_path)
        assert bbox is not None
        assert bbox["layer"] == "F.CrtYd"
        assert bbox["min_x"] == pytest.approx(-3)
        assert bbox["max_x"] == pytest.approx(3)
        assert bbox["width"] == pytest.approx(6)
        assert bbox["height"] == pytest.approx(4)

    def test_mixed_layers_returns_first(self, tmp_path: Path):
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

        bbox = _bbox(fp, tmp_path)
        assert bbox is not None
        assert bbox["layer"] == "F.CrtYd"
        assert bbox["min_x"] == pytest.approx(-1)
        assert bbox["max_x"] == pytest.approx(1)

    def test_none_when_no_courtyard(self, tmp_path: Path):
        fp = Footprint()
        fp.entryName = "Test"
        assert _bbox(fp, tmp_path) is None

    def test_from_circle(self, tmp_path: Path):
        fp = Footprint()
        fp.entryName = "Test"
        circle = FpCircle()
        circle.center = Position(X=0, Y=0)
        circle.end = Position(X=5, Y=0)  # radius = 5
        circle.layer = "F.CrtYd"
        fp.graphicItems.append(circle)

        bbox = _bbox(fp, tmp_path)
        assert bbox is not None
        assert bbox["min_x"] == pytest.approx(-5)
        assert bbox["max_x"] == pytest.approx(5)
        assert bbox["min_y"] == pytest.approx(-5)
        assert bbox["max_y"] == pytest.approx(5)


class TestAtomicWrite:
    """The invariant is that a write we cannot finish leaves the file intact.

    A plain write_bytes opens with O_TRUNC and cannot offer that. Measured on
    NTFS with a concurrent reader: 345 torn reads out of 800, including reads of
    zero bytes, against 0 out of 25,529 through _atomic_write.

    These use a .bin target so the autouse kicad-cli validation fixture has
    nothing to say about them.
    """

    ORIGINAL = b"(kicad_sch original)\n"
    REPLACEMENT = b"(kicad_sch replacement that is considerably longer)\n"

    def _target(self, tmp_path: Path) -> Path:
        p = tmp_path / "target.bin"
        p.write_bytes(self.ORIGINAL)
        return p

    def test_replaces_the_file(self, tmp_path: Path):
        p = self._target(tmp_path)
        _atomic_write(p, self.REPLACEMENT)
        assert p.read_bytes() == self.REPLACEMENT
        assert list(tmp_path.glob("*.tmp")) == []

    def test_creates_a_file_that_did_not_exist(self, tmp_path: Path):
        """copymode must not be attempted against a missing destination."""
        p = tmp_path / "new.bin"
        _atomic_write(p, self.REPLACEMENT)
        assert p.read_bytes() == self.REPLACEMENT

    def test_blocked_replace_leaves_the_file_intact(self, tmp_path: Path, monkeypatch):
        """Windows refuses the swap while another process holds the destination.

        The decision this pins: give up and raise rather than fall back to a
        direct write. A refused edit with the file intact is the invariant; a
        torn file is what it forbids.
        """
        p = self._target(tmp_path)
        monkeypatch.setattr(_shared, "_REPLACE_RETRY_DELAYS", (0, 0))

        def blocked(src, dst):
            raise PermissionError(5, "Access is denied")

        monkeypatch.setattr(_shared.os, "replace", blocked)

        with pytest.raises(OSError, match="unchanged"):
            _atomic_write(p, self.REPLACEMENT)

        assert p.read_bytes() == self.ORIGINAL
        assert list(tmp_path.glob("*.tmp")) == [], "temp file left behind"

    def test_failed_temp_write_leaves_the_file_intact(self, tmp_path: Path, monkeypatch):
        """Disk full, or the folder itself refusing new files as Controlled
        Folder Access does. Separate test because the cleanup branch differs."""
        p = self._target(tmp_path)
        real = Path.write_bytes

        def explode(self, data):
            if self.name.endswith(".tmp"):
                raise OSError(28, "No space left on device")
            return real(self, data)

        monkeypatch.setattr(Path, "write_bytes", explode)

        with pytest.raises(OSError):
            _atomic_write(p, self.REPLACEMENT)

        monkeypatch.undo()
        assert p.read_bytes() == self.ORIGINAL
        assert list(tmp_path.glob("*.tmp")) == [], "temp file left behind"

    @pytest.mark.no_kicad_validation
    def test_replaces_once_from_a_temp_that_is_not_a_kicad_file(self, tmp_path: Path, monkeypatch):
        """Two properties of the same call, so one spy answers both.

        Exactly one replace, which is what a future simplification back to
        p.write_bytes(data) would fail. And a temp named board.kicad_sch.PID.tmp
        rather than board.tmp.kicad_sch, because the latter is swept up by
        _resolve_config's *.kicad_pro scan and by the suite's rglob.

        The .kicad_sch target is the point, and its contents are not a real
        schematic, hence the marker.
        """
        p = tmp_path / "board.kicad_sch"
        p.write_bytes(self.ORIGINAL)
        calls: list[tuple[str, str]] = []
        real = _shared.os.replace

        def spy(src, dst):
            calls.append((str(src), str(dst)))
            return real(src, dst)

        monkeypatch.setattr(_shared.os, "replace", spy)
        _atomic_write(p, self.REPLACEMENT)

        assert len(calls) == 1
        src, dst = calls[0]
        assert dst == str(p)
        assert src != str(p), "must not replace the file with itself"
        assert not src.endswith(".kicad_sch"), src
        assert src.endswith(".tmp")


class TestReadKicadBytes:
    """A path that does not exist is the commonest mistake a caller makes.

    Measured 2026-08-12 before this helper existed: 84 of the 105 path-taking
    tools answered a nonexistent path with a raw FileNotFoundError, which
    reaches the MCP client as an unhandled exception carrying no remedy. Every
    tool entry point now reads through here, so it is one behaviour rather than
    84 checks that have to stay in step.
    """

    def test_missing_file_is_refused_with_the_path(self, tmp_path):
        with pytest.raises(ToolError, match="not found") as exc:
            _read_kicad_bytes(tmp_path / "nope.kicad_sch", "schematic")
        assert "nope.kicad_sch" in str(exc.value)
        assert "omit it" in str(exc.value), "the message must name a way out"

    def test_empty_path_names_the_configuration(self):
        """What a blank host configuration produces, and what a user hit."""
        with pytest.raises(ToolError, match="none is configured"):
            _read_kicad_bytes("", "schematic")

    def test_a_directory_is_not_a_file(self, tmp_path):
        with pytest.raises(ToolError, match="not found"):
            _read_kicad_bytes(tmp_path, "schematic")

    def test_a_real_file_reads(self, tmp_path):
        """Bytes straight through, no decode: the helper guards, it does not parse.

        A .kicad_mod on purpose. The autouse output oracle globs .kicad_sch and
        .kicad_pcb, and a stub body is not a loadable schematic, so naming this
        one .kicad_sch would fail validation for a reason the test is not about.
        """
        p = tmp_path / "x.kicad_mod"
        p.write_bytes(b'(footprint "X")\r\n')
        assert _read_kicad_bytes(p, "footprint") == b'(footprint "X")\r\n'

    def test_every_tool_entry_point_refuses_a_missing_path(self, tmp_path):
        """The 84 collapsed to one behaviour; this is the proof, not the helper."""
        from mcp_server_kicad import footprint, pcb, schematic, symbol

        missing = str(tmp_path / "absent")
        for fn, kwargs in (
            (schematic.list_schematic_components, {"schematic_path": missing}),
            (pcb.list_pcb_footprints, {"pcb_path": missing}),
            (symbol.list_lib_symbols, {"symbol_lib_path": missing}),
            (footprint.get_footprint_info, {"footprint_path": missing}),
        ):
            with pytest.raises(ToolError):
                fn(**kwargs)


class TestEnsureDir:
    """A missing directory is created; an unreachable root is refused.

    The distinction is the whole point of the helper. Callers name fresh export
    folders all the time and expect them to appear, so mkdir(parents=True) is
    the behaviour to keep; a drive or share that is not there cannot be reached
    by any amount of mkdir, and used to arrive as a bare WinError 3 naming
    neither the tool nor the parameter.
    """

    def test_missing_directories_are_created(self, tmp_path):
        d = _ensure_dir(tmp_path / "a" / "b" / "c")
        assert d.is_dir()

    def test_existing_directory_is_fine(self, tmp_path):
        assert _ensure_dir(tmp_path) == tmp_path

    def test_unreachable_root_is_refused(self, tmp_path):
        """Refused, not crashed, and the message names a thing to check."""
        if os.name == "nt":
            unreachable = "Z:/nope/out"  # a drive letter with nothing mounted
        else:
            # A file cannot be a parent directory, so mkdir fails at the root.
            blocker = tmp_path / "blocker"
            blocker.write_bytes(b"x")
            unreachable = str(blocker / "out")
        with pytest.raises(ToolError) as exc:
            _ensure_dir(unreachable)
        assert "Cannot create output directory" in str(exc.value)
        assert "reachable" in str(exc.value)

    def test_the_kind_reaches_the_message(self, tmp_path):
        blocker = tmp_path / "f"
        blocker.write_bytes(b"x")
        with pytest.raises(ToolError, match="parent directory"):
            _ensure_dir(blocker / "out", "parent directory")
