"""Tests for Freerouting helper module."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import requires_cli
from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Net, Position, Property
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon
from mcp.server.mcpserver.exceptions import ToolError

import mcp_server_kicad._freerouting as _fr_module
from mcp_server_kicad import _cst
from mcp_server_kicad._freerouting import (
    check_java,
    ensure_jar,
    export_dsn,
    find_jar,
    find_pcbnew_python,
    import_ses,
    jar_java_requirement,
    pcbnew_major,
    run_freerouting,
)
from mcp_server_kicad._shared import _keepout_dict, _xy
from mcp_server_kicad.pcb import _promote_footprint_keepouts, run_drc


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

    def test_pythonhome_stripped_from_probe_and_returned_env(self):
        """uv-trampoline venvs export PYTHONHOME, which breaks KiCad's own
        interpreter; the probe and the returned launch env must both drop it."""
        captured_envs = []

        def fake_run(args, **kwargs):
            captured_envs.append(kwargs.get("env"))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with patch.dict(os.environ, {"KICAD_PYTHON": "/fake/py", "PYTHONHOME": "/fake/venv"}):
            with patch("subprocess.run", side_effect=fake_run):
                python, env = find_pcbnew_python()

        assert python == "/fake/py"
        assert env is not None
        assert "PYTHONHOME" not in env
        # KICAD_PYTHON pins a single probe, and it must run under the same
        # scrubbed dict the caller gets back.
        assert captured_envs == [env]


_FIND_PY = "mcp_server_kicad._freerouting.find_pcbnew_python"


class TestPcbnewMajor:
    @pytest.fixture(autouse=True)
    def _reset_major_cache(self):
        _fr_module._pcbnew_major_cache = None
        yield
        _fr_module._pcbnew_major_cache = None

    def _probe(self, **run_kwargs):
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", **run_kwargs),
        ):
            return pcbnew_major()

    def test_parses_version_string(self):
        """The shape KiCad 9.0.8 actually prints, measured locally."""
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="9.0.8\n", stderr="")
        assert self._probe(return_value=done) == 9

    def test_unparseable_output(self):
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="no idea\n", stderr="")
        assert self._probe(return_value=done) is None

    def test_subprocess_raises(self):
        assert self._probe(side_effect=OSError("boom")) is None

    def test_no_interpreter(self):
        with patch(_FIND_PY, return_value=(None, None)):
            assert pcbnew_major() is None

    def test_real_pcbnew(self):
        """The cross-era proof, unmocked: pcbnew 9 on the Linux runner and
        pcbnew 10 on the gating macOS one both have to answer."""
        if find_pcbnew_python()[0] is None:
            pytest.skip("pcbnew Python bindings not available")
        major = pcbnew_major()
        assert isinstance(major, int)
        assert major >= 9


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

    def test_source_zone_not_mutated(self, tmp_path):
        """The zone copy is deep, the CST twin of the old deep_copy_isolation.

        Transforming the promoted polygon must leave the footprint's own
        zone on its local coordinates, and the source file must not be
        written at all.
        """
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "out.kicad_pcb")
        before = Path(pcb_path).read_bytes()

        _promote_footprint_keepouts(pcb_path, out_path)

        assert Path(pcb_path).read_bytes() == before
        out_root = _cst.parse(Path(out_path).read_bytes()).lists[0]
        fp_zone = out_root.find("footprint").find("zone")
        assert [_xy(p) for p in fp_zone.find("polygon").find("pts").find_all("xy")] == [
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 10.0),
            (0.0, 10.0),
        ]
        # The restrictions travel with the copy rather than being aliased away.
        assert _keepout_dict(out_root.find("zone").find("keepout")) == _keepout_dict(
            fp_zone.find("keepout")
        )

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
        """An unwritable output path surfaces as ToolError, not a raw OSError."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "no_such_dir" / "out.kicad_pcb")

        with pytest.raises(ToolError, match="Failed to prepare PCB for autorouting"):
            _promote_footprint_keepouts(pcb_path, out_path)

    @requires_cli
    def test_kicad_accepts_the_promoted_board(self, tmp_path):
        """The live oracle for the promoted zone: KiCad itself loads the file
        and runs DRC on it, on whichever major the runner has installed. The
        promoted board's only real consumer is pcbnew, so our own parser
        reading it back proves nothing."""
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_angle=45, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "out.kicad_pcb")

        assert _promote_footprint_keepouts(pcb_path, out_path) == 1

        result = run_drc(pcb_path=out_path, output_dir=str(tmp_path))
        assert result.violation_count >= 0

    def test_net_tokens_k9_numeric(self, tmp_path):
        """A KiCad 9 format board gets the numeric (net 0) plus empty net_name."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "out.kicad_pcb")

        assert _promote_footprint_keepouts(pcb_path, out_path) == 1

        zone = _cst.parse(Path(out_path).read_bytes()).lists[0].find("zone")
        assert zone.find("net").atoms[1].text == "0"
        assert zone.find("net_name").atoms[1].text == ""

    def test_net_tokens_absent_on_kicad10(self, tmp_path):
        """A KiCad 10 format board gets no net tokens at all on a rule area:
        numeric references are silently rebound there (ADR-2 guardrail 5)."""
        from test_cst_pcb import _bump_version

        pcb_path = _bump_version(_make_board_with_fp_keepout(tmp_path))
        out_path = str(tmp_path / "out.kicad_pcb")

        assert _promote_footprint_keepouts(pcb_path, out_path) == 1

        zone = _cst.parse(Path(out_path).read_bytes()).lists[0].find("zone")
        assert zone.find("net") is None
        assert zone.find("net_name") is None
        assert zone.find("keepout") is not None


class TestJarJavaRequirement:
    """The Java floor has to come from the jar, because the jar floats.

    _download_jar fetches releases/latest with no pin and no record of what it
    got, so a literal minimum in check_java cannot stay right. Measured
    2026-08-14 on the cached freerouting.jar (build 2026-08-07): its class files
    are major 69, which is Java 25, against a check that accepted anything from
    17 up. Every machine between the two passed the preflight and then died
    inside the router, with the preflight's blessing.
    """

    @staticmethod
    def _jar(tmp_path, major: int, *, mr_major: int | None = None):
        """A jar carrying one class file at *major*, optionally with a
        higher-versioned multi-release copy that must be ignored."""
        import struct
        import zipfile

        def klass(m: int) -> bytes:
            return b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, m) + b"\x00" * 8

        p = tmp_path / "fake.jar"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("app/Main.class", klass(major))
            if mr_major is not None:
                zf.writestr(f"META-INF/versions/{mr_major - 44}/app/Main.class", klass(mr_major))
        return str(p)

    def test_reads_the_major_from_the_class_files(self, tmp_path):
        assert jar_java_requirement(self._jar(tmp_path, 69)) == 25
        assert jar_java_requirement(self._jar(tmp_path, 61)) == 17

    def test_a_multi_release_copy_does_not_raise_the_floor(self, tmp_path):
        """Requiring the highest would refuse a Java the jar runs on fine."""
        assert jar_java_requirement(self._jar(tmp_path, 61, mr_major=69)) == 17

    def test_an_unreadable_jar_is_unknown_not_fatal(self, tmp_path):
        bad = tmp_path / "not.jar"
        bad.write_bytes(b"this is not a zip")
        assert jar_java_requirement(str(bad)) is None
        assert jar_java_requirement(str(tmp_path / "absent.jar")) is None

    def test_the_real_cached_jar_if_present(self):
        """Not a fixture: the actual artifact the tool would run."""
        from mcp_server_kicad._freerouting import find_jar

        jar = find_jar()
        if jar is None:
            pytest.skip("no freerouting jar cached on this machine")
        need = jar_java_requirement(jar)
        assert need is not None and need >= 17, need


class TestCheckJavaAgainstTheJar:
    def _java(self, version: str):
        return patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=f'openjdk version "{version}" 2026-01-01\n'
            ),
        )

    def test_java17_is_refused_for_a_java25_jar(self, tmp_path):
        jar = TestJarJavaRequirement._jar(tmp_path, 69)
        with self._java("17.0.9"):
            msg = check_java(jar)
        assert msg and "17" in msg and "25" in msg
        # The remedy must not be Debian-only; this server ships on three OSes.
        assert "adoptium" in msg

    def test_java25_passes_the_same_jar(self, tmp_path):
        jar = TestJarJavaRequirement._jar(tmp_path, 69)
        with self._java("25.0.1"):
            assert check_java(jar) is None

    def test_no_jar_falls_back_to_the_floor(self):
        with self._java("17.0.9"):
            assert check_java() is None
        with self._java("11.0.2"):
            assert check_java() is not None

    def test_a_wedged_java_is_reported_not_raised(self):
        """TimeoutExpired was uncaught, so it propagated as a raw exception."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("java", 10)):
            msg = check_java()
        assert msg and "did not answer" in msg
