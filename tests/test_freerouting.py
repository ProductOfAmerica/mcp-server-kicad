"""Tests for Freerouting helper module."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Net, Position, Property
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon
from mcp.server.fastmcp.exceptions import ToolError

import mcp_server_kicad._freerouting as _fr_module
from mcp_server_kicad._freerouting import (
    check_java,
    ensure_jar,
    export_dsn,
    find_jar,
    find_pcbnew_python,
    import_ses,
    run_freerouting,
)
from mcp_server_kicad._shared import _promote_footprint_keepouts


class TestCheckJava:
    def test_java_found_valid_version(self):
        mock_result = subprocess.CompletedProcess(
            args=["java", "-version"],
            returncode=0,
            stdout="",
            stderr='openjdk version "21.0.1" 2023-10-17',
        )
        with patch("subprocess.run", return_value=mock_result):
            result = check_java()
            assert result is None

    def test_java_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = check_java()
            assert result is not None
            assert "Java" in result
            assert "apt install" in result

    def test_java_too_old(self):
        mock_result = subprocess.CompletedProcess(
            args=["java", "-version"],
            returncode=0,
            stdout="",
            stderr='openjdk version "11.0.2" 2019-01-15',
        )
        with patch("subprocess.run", return_value=mock_result):
            result = check_java()
            assert result is not None
            assert "17" in result


class TestFindJar:
    def test_env_var_override(self, tmp_path):
        jar = tmp_path / "custom.jar"
        jar.touch()
        with patch.dict(os.environ, {"FREEROUTING_JAR": str(jar)}):
            assert find_jar() == str(jar)

    def test_env_var_missing_file(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with (
            patch.dict(os.environ, {"FREEROUTING_JAR": "/nonexistent/fr.jar"}),
            patch("mcp_server_kicad._freerouting._cache_dir", return_value=empty_dir),
        ):
            assert find_jar() is None

    def test_cached_jar(self, tmp_path):
        cache_dir = tmp_path / ".local" / "share" / "mcp-server-kicad"
        cache_dir.mkdir(parents=True)
        jar = cache_dir / "freerouting.jar"
        jar.touch()
        with patch("mcp_server_kicad._freerouting._cache_dir", return_value=cache_dir):
            assert find_jar() == str(jar)

    def test_no_jar(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with (
            patch.dict(os.environ, {"FREEROUTING_JAR": ""}, clear=False),
            patch("mcp_server_kicad._freerouting._cache_dir", return_value=empty_dir),
        ):
            assert find_jar() is None


class TestEnsureJar:
    def test_already_exists(self, tmp_path):
        jar = tmp_path / "freerouting.jar"
        jar.touch()
        with patch("mcp_server_kicad._freerouting.find_jar", return_value=str(jar)):
            path, err = ensure_jar()
            assert path == str(jar)
            assert err is None

    def test_download_success(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        jar_path = str(cache_dir / "freerouting.jar")

        with (
            patch("mcp_server_kicad._freerouting.find_jar", side_effect=[None, jar_path]),
            patch("mcp_server_kicad._freerouting._download_jar", return_value=jar_path),
        ):
            path, err = ensure_jar()
            assert path == jar_path
            assert err is None

    def test_download_failure(self, tmp_path):
        with (
            patch("mcp_server_kicad._freerouting.find_jar", return_value=None),
            patch(
                "mcp_server_kicad._freerouting._download_jar",
                side_effect=RuntimeError("Network error"),
            ),
        ):
            path, err = ensure_jar()
            assert path is None
            assert err is not None
            assert "Network error" in err


class TestFindPcbnewPython:
    @pytest.fixture(autouse=True)
    def _reset_pcbnew_cache(self):
        _fr_module._pcbnew_cache = None
        yield
        _fr_module._pcbnew_cache = None

    def test_direct_import_works(self):
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            python, env = find_pcbnew_python()
            assert python is not None

    def test_no_pcbnew_available(self):
        with patch("subprocess.run", side_effect=Exception("fail")):
            python, env = find_pcbnew_python()
            assert python is None


_FIND_PY = "mcp_server_kicad._freerouting.find_pcbnew_python"


class TestExportDsn:
    def test_success(self, tmp_path):
        pcb_path = str(tmp_path / "board.kicad_pcb")
        dsn_path = str(tmp_path / "board.dsn")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = export_dsn(pcb_path, dsn_path)
            assert err is None

    def test_pcbnew_not_found(self, tmp_path):
        with patch(_FIND_PY, return_value=(None, None)):
            err = export_dsn(str(tmp_path / "b.kicad_pcb"), str(tmp_path / "b.dsn"))
            assert err is not None
            assert "pcbnew" in err.lower() or "KiCad" in err


class TestImportSes:
    def test_success(self, tmp_path):
        pcb_path = str(tmp_path / "board.kicad_pcb")
        ses_path = str(tmp_path / "board.ses")
        out_path = str(tmp_path / "board_routed.kicad_pcb")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = import_ses(pcb_path, ses_path, out_path)
            assert err is None

    def test_subprocess_fails(self, tmp_path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="pcbnew error"
        )
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = import_ses(
                str(tmp_path / "b.kicad_pcb"),
                str(tmp_path / "b.ses"),
                str(tmp_path / "b_routed.kicad_pcb"),
            )
            assert err is not None


class TestRunFreerouting:
    def test_success(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        ses = tmp_path / "board.ses"
        dsn.touch()
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Route complete", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(ses),
            )
            assert err is None

    def test_timeout(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        dsn.touch()
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="java", timeout=600),
        ):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(tmp_path / "board.ses"),
                timeout=600,
            )
            assert err is not None
            assert "timeout" in err.lower() or "timed out" in err.lower()

    def test_nonzero_exit(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        dsn.touch()
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Error")
        with patch("subprocess.run", return_value=mock_result):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(tmp_path / "board.ses"),
            )
            assert err is not None


# ---------------------------------------------------------------------------
# Helpers for _promote_footprint_keepouts tests
# ---------------------------------------------------------------------------


def _make_board_with_fp_keepout(tmp_path, fp_angle=0, fp_layer="F.Cu", fp_x=100, fp_y=100):
    """Create a minimal board with one footprint containing a keepout zone."""
    board = Board.create_new()
    board.nets = [Net(number=0, name="")]

    fp = Footprint()
    fp.entryName = "TestPkg:TestFP"
    fp.layer = fp_layer
    fp.position = Position(X=fp_x, Y=fp_y, angle=fp_angle)
    fp.reference = Property(key="Reference", value="U1")
    fp.value = Property(key="Value", value="TEST")

    keepout_zone = Zone()
    keepout_zone.net = 0
    keepout_zone.netName = ""
    keepout_zone.layers = ["F.Cu", "B.Cu"]
    keepout_zone.hatch = Hatch(style="edge", pitch=0.5)
    keepout_zone.keepoutSettings = KeepoutSettings(
        tracks="not_allowed",
        vias="not_allowed",
        pads="not_allowed",
        copperpour="not_allowed",
        footprints="not_allowed",
    )
    poly = ZonePolygon()
    poly.coordinates = [
        Position(X=0, Y=0),
        Position(X=10, Y=0),
        Position(X=10, Y=10),
        Position(X=0, Y=10),
    ]
    keepout_zone.polygons = [poly]
    fp.zones = [keepout_zone]

    board.footprints = [fp]
    pcb_path = str(tmp_path / "test.kicad_pcb")
    board.filePath = pcb_path
    board.to_file()
    return pcb_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPromoteFootprintKeepouts:
    def test_happy_path(self, tmp_path):
        """FP keepout promoted with correct board-space coords.

        FP at (100,100), zone vertices at (0,0),(10,0),(10,10),(0,10)
        should become (100,100),(110,100),(110,110),(100,110).
        """
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 1
        assert Path(out_path).exists()

        out_board = Board.from_file(out_path)
        assert len(out_board.zones) == 1
        coords = out_board.zones[0].polygons[0].coordinates
        xs = [round(c.X, 3) for c in coords]
        ys = [round(c.Y, 3) for c in coords]
        assert xs == [100.0, 110.0, 110.0, 100.0]
        assert ys == [100.0, 100.0, 110.0, 110.0]

    def test_no_keepouts_returns_zero(self, tmp_path):
        """Board with no FP keepouts returns 0, no output file created."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        fp = Footprint()
        fp.entryName = "TestPkg:Plain"
        fp.layer = "F.Cu"
        fp.position = Position(X=50, Y=50, angle=0)
        fp.reference = Property(key="Reference", value="R1")
        fp.value = Property(key="Value", value="10k")
        fp.zones = []
        board.footprints = [fp]
        pcb_path = str(tmp_path / "no_keepout.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 0
        assert not Path(out_path).exists()

    def test_rotated_footprint(self, tmp_path):
        """FP at 90 degrees; verify coords are correctly rotated."""
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_angle=90, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 1
        out_board = Board.from_file(out_path)
        coords = out_board.zones[0].polygons[0].coordinates
        # At 90 deg, (10, 0) local -> board (100, 110) (Y increases)
        # Rotation formula: bx = fp_x + lx*cos(a) - ly*sin(a)
        #                   by = fp_y + lx*sin(a) + ly*cos(a)
        # (10,0) at 90 deg: bx=100+0-0=100, by=100+10+0=110
        xs = [round(c.X, 3) for c in coords]
        ys = [round(c.Y, 3) for c in coords]
        # (0,0)->100,100  (10,0)->100,110  (10,10)->90,110  (0,10)->90,100
        assert xs[0] == pytest.approx(100.0, abs=0.01)
        assert ys[0] == pytest.approx(100.0, abs=0.01)
        assert xs[1] == pytest.approx(100.0, abs=0.01)
        assert ys[1] == pytest.approx(110.0, abs=0.01)

    def test_multiple_polygons(self, tmp_path):
        """Zone with 2 polygons produces count=2 and 2 board-level keepout zones."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]

        fp = Footprint()
        fp.entryName = "TestPkg:Multi"
        fp.layer = "F.Cu"
        fp.position = Position(X=0, Y=0, angle=0)
        fp.reference = Property(key="Reference", value="U2")
        fp.value = Property(key="Value", value="Multi")

        keepout_zone = Zone()
        keepout_zone.net = 0
        keepout_zone.netName = ""
        keepout_zone.layers = ["F.Cu"]
        keepout_zone.hatch = Hatch(style="edge", pitch=0.5)
        keepout_zone.keepoutSettings = KeepoutSettings(
            tracks="not_allowed",
            vias="not_allowed",
            pads="not_allowed",
            copperpour="not_allowed",
            footprints="not_allowed",
        )
        poly1 = ZonePolygon()
        poly1.coordinates = [
            Position(X=0, Y=0),
            Position(X=10, Y=0),
            Position(X=10, Y=10),
            Position(X=0, Y=10),
        ]
        poly2 = ZonePolygon()
        poly2.coordinates = [
            Position(X=20, Y=20),
            Position(X=30, Y=20),
            Position(X=30, Y=30),
        ]
        keepout_zone.polygons = [poly1, poly2]
        fp.zones = [keepout_zone]
        board.footprints = [fp]
        pcb_path = str(tmp_path / "multi.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 2
        out_board = Board.from_file(out_path)
        assert len(out_board.zones) == 2
        # First polygon: (0,0) fp-local -> (0,0) board
        first_coords = out_board.zones[0].polygons[0].coordinates
        assert round(first_coords[0].X, 3) == 0.0
        assert round(first_coords[0].Y, 3) == 0.0
        # Second polygon: (20,20) fp-local -> (20,20) board
        second_coords = out_board.zones[1].polygons[0].coordinates
        assert round(second_coords[0].X, 3) == 20.0
        assert round(second_coords[0].Y, 3) == 20.0

    def test_back_side_footprint_keepout(self, tmp_path):
        """FP on B.Cu; verify X coords are mirrored.

        FP at (100,100), vertex (10,0) should become (90,100) due to X negation.
        """
        pcb_path = _make_board_with_fp_keepout(
            tmp_path, fp_angle=0, fp_layer="B.Cu", fp_x=100, fp_y=100
        )
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 1
        out_board = Board.from_file(out_path)
        coords = out_board.zones[0].polygons[0].coordinates
        xs = [round(c.X, 3) for c in coords]
        ys = [round(c.Y, 3) for c in coords]
        # (0,0)->100,100  (10,0)->90,100 (mirrored X)  (10,10)->90,110  (0,10)->100,110
        assert xs[0] == pytest.approx(100.0, abs=0.01)
        assert ys[0] == pytest.approx(100.0, abs=0.01)
        assert xs[1] == pytest.approx(90.0, abs=0.01)
        assert ys[1] == pytest.approx(100.0, abs=0.01)

    def test_fp_position_none_skipped(self, tmp_path):
        """FP with position=None is skipped, returns 0."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]

        fp = Footprint()
        fp.entryName = "TestPkg:NoPos"
        fp.layer = "F.Cu"
        fp.position = None
        fp.reference = Property(key="Reference", value="U3")
        fp.value = Property(key="Value", value="NOPOS")

        keepout_zone = Zone()
        keepout_zone.net = 0
        keepout_zone.netName = ""
        keepout_zone.layers = ["F.Cu"]
        keepout_zone.hatch = Hatch(style="edge", pitch=0.5)
        keepout_zone.keepoutSettings = KeepoutSettings(
            tracks="not_allowed",
            vias="not_allowed",
            pads="not_allowed",
            copperpour="not_allowed",
            footprints="not_allowed",
        )
        poly = ZonePolygon()
        poly.coordinates = [Position(X=0, Y=0), Position(X=5, Y=0), Position(X=5, Y=5)]
        keepout_zone.polygons = [poly]
        fp.zones = [keepout_zone]
        board.footprints = [fp]
        pcb_path = str(tmp_path / "nopos.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 0
        assert not Path(out_path).exists()

    def test_deep_copy_isolation(self, tmp_path):
        """Modifying promoted zone's keepoutSettings doesn't affect source."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "out.kicad_pcb")

        _promote_footprint_keepouts(pcb_path, out_path)

        # Reload original and mutate promoted zone keepoutSettings
        out_board = Board.from_file(out_path)
        out_board.zones[0].keepoutSettings.tracks = "allowed"

        # Reload source and confirm it wasn't mutated
        src_board = Board.from_file(pcb_path)
        src_fp_zone = src_board.footprints[0].zones[0]
        assert src_fp_zone.keepoutSettings.tracks == "not_allowed"

    def test_dsn_source_branching_with_keepouts(self, tmp_path):
        """count > 0 means out_path is written."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count > 0
        assert Path(out_path).exists()

    def test_dsn_source_branching_without_keepouts(self, tmp_path):
        """count == 0 means out_path is NOT written."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        pcb_path = str(tmp_path / "empty.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 0
        assert not Path(out_path).exists()

    def test_save_failure_raises_tool_error(self, tmp_path):
        """Mock Board.to_file to raise OSError; verify ToolError is raised."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "out.kicad_pcb")

        with patch.object(Board, "to_file", side_effect=OSError("disk full")):
            with pytest.raises(ToolError, match="disk full"):
                _promote_footprint_keepouts(pcb_path, out_path)
