"""Tests for shared helper functions in _shared.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import new_schematic
from kiutils.footprint import Footprint
from kiutils.items.common import Position
from kiutils.items.fpitems import FpCircle, FpLine, FpRect
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, _shared
from mcp_server_kicad._shared import (
    _atomic_write,
    _backup_for_external_write,
    _courtyard_bbox_cst,
    _ensure_dir,
    _point_in_polygon,
    _read_kicad_bytes,
    _resolve_hierarchy_path,
    _run_pcbnew,
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


class TestBackupForExternalWrite:
    """The two library upgrades hand a user file to kicad-cli, which rewrites
    it in place. That is outside both halves of the invariant: not atomic, and
    not reversible. The ADR calls `fp upgrade` the sharpest of the four
    subprocess writers because it rewrites every .kicad_mod in a library at once.

    This reverses a decision recorded 2026-08-10, which rejected a pre-write
    copy as "a backup with no lifecycle owner". Exactly one backup per library,
    overwritten every run, is what answers that: it cannot accumulate, so there
    is no lifecycle to own. These tests pin the bounded part, because that is
    the whole argument.
    """

    def test_a_file_is_copied_beside_itself(self, tmp_path):
        src = tmp_path / "lib.kicad_sym"
        src.write_bytes(b"(kicad_symbol_lib original)\r\n")
        dest = _backup_for_external_write(src, "symbol library")
        assert dest == tmp_path / "lib.kicad_sym.bak"
        # Bytes, not text: a backup that normalises line endings is not a backup.
        assert dest.read_bytes() == b"(kicad_symbol_lib original)\r\n"

    def test_a_second_run_overwrites_rather_than_accumulates(self, tmp_path):
        """The bounded-at-one property, which is the answer to the ADR's
        objection. If this ever fails, the reversal stops being justified."""
        src = tmp_path / "lib.kicad_sym"
        for body in (b"first", b"second", b"third"):
            src.write_bytes(body)
            _backup_for_external_write(src, "symbol library")
        baks = sorted(p.name for p in tmp_path.iterdir() if ".bak" in p.name)
        assert baks == ["lib.kicad_sym.bak"], baks
        assert (tmp_path / "lib.kicad_sym.bak").read_bytes() == b"third"

    def test_a_pretty_directory_is_copied_whole(self, tmp_path):
        pretty = tmp_path / "MyLib.pretty"
        pretty.mkdir()
        (pretty / "R_0603.kicad_mod").write_bytes(b'(footprint "R_0603")')
        (pretty / "C_0402.kicad_mod").write_bytes(b'(footprint "C_0402")')
        dest = _backup_for_external_write(pretty, "footprint library")
        assert dest == tmp_path / "MyLib.pretty.bak"
        assert sorted(p.name for p in dest.iterdir()) == ["C_0402.kicad_mod", "R_0603.kicad_mod"]
        assert (dest / "R_0603.kicad_mod").read_bytes() == b'(footprint "R_0603")'

    def test_a_directory_backup_also_stays_at_one(self, tmp_path):
        pretty = tmp_path / "MyLib.pretty"
        pretty.mkdir()
        (pretty / "a.kicad_mod").write_bytes(b"one")
        _backup_for_external_write(pretty, "footprint library")
        (pretty / "a.kicad_mod").write_bytes(b"two")
        (pretty / "b.kicad_mod").write_bytes(b"new")
        _backup_for_external_write(pretty, "footprint library")
        baks = [p.name for p in tmp_path.iterdir() if ".bak" in p.name]
        assert baks == ["MyLib.pretty.bak"], baks
        dest = tmp_path / "MyLib.pretty.bak"
        assert (dest / "a.kicad_mod").read_bytes() == b"two"
        assert (dest / "b.kicad_mod").exists()
        # No staging directory left behind.
        assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]

    def test_a_failed_backup_refuses_rather_than_proceeding(self, tmp_path, monkeypatch):
        """Proceeding without a backup is the thing this exists to prevent."""
        src = tmp_path / "lib.kicad_sym"
        src.write_bytes(b"x")

        def boom(*a, **k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(_shared, "_atomic_write", boom)
        with pytest.raises(ToolError) as exc:
            _backup_for_external_write(src, "symbol library")
        assert "has not been started" in str(exc.value)
        assert "no undo" in str(exc.value)


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    """A subprocess result carrying only what the code under test reads."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestRunPcbnew:
    """A process that died has to say so, and say it differently from one that
    ran and reported a failure.

    Before this, fill_zones raised ``f"Zone fill failed: {stderr.strip()[:400]}"``.
    A hard crash writes nothing to stderr, so the whole message was "Zone fill
    failed:" and stopped there: no exit code, no indication of whether the board
    or KiCad was at fault, nothing to search for. Three of the nineteen boards
    KiCad 10 ships kill pcbnew's bindings on LoadBoard, so that is a board a user
    can own rather than a hypothetical.

    The gate is the exit code, never the text. That is this package's existing
    rule for _KICAD_STARTUP_CRASH, for two reasons that both apply here: stderr
    is locale-dependent, and a process killed this way often writes none at all.
    """

    def test_a_posix_signal_reads_as_a_crash(self):
        """Negative return codes are how POSIX reports a fatal signal."""
        with patch("subprocess.run", return_value=_completed(-11)):
            with pytest.raises(ToolError) as exc:
                _run_pcbnew(["py"], what="filling zones", timeout=5)
        assert "crashed while filling zones" in str(exc.value)
        assert "-11" in str(exc.value)

    def test_a_windows_ntstatus_reads_as_a_crash(self):
        """0xC0000409 is what the boards that kill pcbnew's bindings exit with."""
        with patch("subprocess.run", return_value=_completed(0xC0000409)):
            with pytest.raises(ToolError) as exc:
                _run_pcbnew(["py"], what="filling zones", timeout=5)
        assert "crashed" in str(exc.value)
        assert str(0xC0000409) in str(exc.value)

    def test_the_message_does_not_blame_the_file(self):
        """A corrupt board crashes pcbnew too, so the tool cannot tell the user
        which it is. It points at the one test that distinguishes them."""
        with patch("subprocess.run", return_value=_completed(0xC0000409)):
            with pytest.raises(ToolError) as exc:
                _run_pcbnew(["py"], what="filling zones", timeout=5)
        assert "open the board in KiCad" in str(exc.value)
        assert "nothing was written" in str(exc.value).lower()

    def test_output_is_carried_through_when_there_is_any(self):
        """A wx assertion sometimes lands on stdout rather than stderr, so both
        are tried. Measured on kicad-cli 9.0.8, which reports "Unable to save
        library" on stdout with stderr empty."""
        with patch("subprocess.run", return_value=_completed(-6, stdout="assert failed")):
            with pytest.raises(ToolError) as exc:
                _run_pcbnew(["py"], what="filling zones", timeout=5)
        assert "assert failed" in str(exc.value)

    def test_an_ordinary_failure_passes_straight_through(self):
        """Load-bearing. Without it, "raise on any non-zero" passes every other
        case in this class while stealing every caller's own error wording."""
        with patch("subprocess.run", return_value=_completed(1, stderr="bad layer")):
            result = _run_pcbnew(["py"], what="filling zones", timeout=5)
        assert result.returncode == 1
        assert result.stderr == "bad layer"

    def test_success_passes_through(self):
        with patch("subprocess.run", return_value=_completed(0, stdout="FILLDUMP[]")):
            assert _run_pcbnew(["py"], what="filling zones", timeout=5).stdout == "FILLDUMP[]"

    def test_a_timeout_names_the_operation_and_the_limit(self):
        """It propagated a raw TimeoutExpired out of the tool before, which names
        neither."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("py", 60)):
            with pytest.raises(ToolError) as exc:
                _run_pcbnew(["py"], what="importing the netlist", timeout=60)
        assert "importing the netlist" in str(exc.value)
        assert "60s" in str(exc.value)


class TestRunCliAbnormalExit:
    """kicad-cli is the fifth site of the same two defects, in the same file."""

    def _patch(self, monkeypatch, **kw):
        monkeypatch.setattr(_shared, "_find_kicad_cli", lambda: "/bin/kicad-cli")
        monkeypatch.setattr(_shared, "_documents_home", None)
        monkeypatch.delenv("KICAD_DOCUMENTS_HOME", raising=False)
        monkeypatch.setattr(subprocess, "run", **kw)

    def test_a_crash_raises_even_when_unchecked(self, monkeypatch):
        """check=False exists for the non-zero exits ERC and DRC report
        violation counts with. A process that died is not a violation count."""
        self._patch(monkeypatch, value=lambda *a, **k: _completed(-11))
        with pytest.raises(ToolError) as exc:
            _shared._run_cli(["pcb", "drc"], check=False)
        assert "crashed" in str(exc.value)

    def test_the_startup_crash_keeps_its_own_message(self, monkeypatch):
        """0xC0000005 is inside the abnormal range, so ordering matters: the
        branch that knows how to repair it has to run first."""
        self._patch(monkeypatch, value=lambda *a, **k: _completed(_shared._KICAD_STARTUP_CRASH))
        with pytest.raises(ToolError) as exc:
            _shared._run_cli(["version"])
        assert "Controlled Folder" in str(exc.value)

    def test_a_timeout_names_the_limit(self, monkeypatch):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired("kicad-cli", 120)

        self._patch(monkeypatch, value=boom)
        with pytest.raises(ToolError) as exc:
            _shared._run_cli(["pcb", "drc"], check=False)
        assert "120s" in str(exc.value)
