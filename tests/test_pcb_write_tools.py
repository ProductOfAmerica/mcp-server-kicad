"""Tests for PCB write tools."""

import json
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import _default_effects, build_test_footprint
from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Net, Position
from kiutils.items.fpitems import FpText
from kiutils.items.gritems import GrLine
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, pcb
from mcp_server_kicad.models import PointSpec


class TestPlaceFootprint:
    def test_basic(self, scratch_pcb):
        result = pcb.place_footprint("R2", "4.7K", 150, 100, pcb_path=str(scratch_pcb))
        assert "R2" in result
        board = Board.from_file(str(scratch_pcb))
        assert "R2" in [fp.properties.get("Reference") for fp in board.footprints]


class TestMoveFootprint:
    def test_move_existing(self, scratch_pcb):
        result = pcb.move_footprint("R1", 200, 200, pcb_path=str(scratch_pcb))
        assert "Moved" in result
        board = Board.from_file(str(scratch_pcb))
        r1 = next(fp for fp in board.footprints if fp.properties.get("Reference") == "R1")
        assert r1.position.X == 200

    def test_move_missing(self, scratch_pcb):
        with pytest.raises(ToolError, match="not found"):
            pcb.move_footprint("R999", 200, 200, pcb_path=str(scratch_pcb))


class TestRemoveFootprint:
    def test_remove_existing(self, scratch_pcb):
        result = pcb.remove_footprint("R1", str(scratch_pcb))
        assert "Removed" in result
        board = Board.from_file(str(scratch_pcb))
        assert len(board.footprints) == 0

    def test_remove_missing(self, scratch_pcb):
        with pytest.raises(ToolError, match="not found"):
            pcb.remove_footprint("R999", str(scratch_pcb))


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
            patch("mcp_server_kicad.pcb._pcbnew_major", return_value=9),
        ):
            result = pcb.autoroute_pcb(pcb_path=str(scratch_pcb))
            assert result.routed_path
            assert result.traces_added == 4
            assert result.vias_added == 2

    def test_no_java(self, scratch_pcb):
        with (
            patch("mcp_server_kicad.pcb._check_java", return_value="Java not found"),
            patch("mcp_server_kicad.pcb._pcbnew_major", return_value=9),
        ):
            with pytest.raises(ToolError, match="Java"):
                pcb.autoroute_pcb(pcb_path=str(scratch_pcb))

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
            patch("mcp_server_kicad.pcb._pcbnew_major", return_value=9),
        ):
            result = pcb.autoroute_pcb(pcb_path=str(scratch_pcb))
            assert result.routed_path
            assert result.text_fields_fixed > 0

            # Load the routed board and verify text positions are reasonable
            routed_board = Board.from_file(result.routed_path)
            for fp in routed_board.footprints:
                for item in fp.graphicItems:
                    if isinstance(item, FpText) and item.type in ("reference", "value"):
                        # Text should be within 5mm of footprint center (0,0 relative)
                        dist = (item.position.X**2 + item.position.Y**2) ** 0.5
                        assert dist <= 5.0, (
                            f"{item.type} text {item.text!r} is {dist:.1f}mm "
                            f"from center at ({item.position.X}, {item.position.Y})"
                        )

    def test_kicad10_board_routes(self, tmp_path):
        """A KiCad 10 format board runs the whole tool, where the retired
        version guard refused it outright. Every subprocess seam is mocked,
        so this pins the internals, not Java or pcbnew."""
        from test_cst_pcb import _bump_version
        from test_freerouting import _make_board_with_fp_keepout

        pcb_path = _bump_version(_make_board_with_fp_keepout(tmp_path, fp_x=100, fp_y=100))

        def mock_export_dsn(dsn_source, dsn_path):
            # The keepout promotion ran, so the DSN source is its temp copy.
            assert dsn_source != pcb_path
            Path(dsn_path).touch()
            return None

        def mock_run_freerouting(**kwargs):
            Path(kwargs["ses_path"]).touch()
            return None

        def mock_import_ses(source, ses_path, output_path):
            """Splice 4 segments and 2 vias in through the CST: kiutils cannot
            read a KiCad 10 header, and named nets are that board's dialect."""
            tree = _cst.parse(Path(source).read_bytes())
            root = tree.lists[0]
            for i in range(4):
                root.append_child(
                    _cst.parse(
                        f"(segment (start {50 + i * 10} 50) (end {60 + i * 10} 50)"
                        f' (width 0.25) (layer "F.Cu") (net "Net1") (uuid "seg-{i}"))'.encode()
                    ).lists[0],
                    b"\n  ",
                )
            for i in range(2):
                root.append_child(
                    _cst.parse(
                        f"(via (at {70 + i * 10} 50) (size 0.6) (drill 0.3)"
                        f' (layers "F.Cu" "B.Cu") (net "Net1") (uuid "via-{i}"))'.encode()
                    ).lists[0],
                    b"\n  ",
                )
            Path(output_path).write_bytes(_cst.serialize(tree))
            return None

        with (
            patch("mcp_server_kicad.pcb._check_java", lambda: None),
            patch("mcp_server_kicad.pcb._ensure_jar", lambda: ("/fake/freerouting.jar", None)),
            patch("mcp_server_kicad.pcb._export_dsn", mock_export_dsn),
            patch("mcp_server_kicad.pcb._run_freerouting", mock_run_freerouting),
            patch("mcp_server_kicad.pcb._import_ses", mock_import_ses),
            patch("mcp_server_kicad.pcb._pcbnew_major", return_value=10),
        ):
            result = pcb.autoroute_pcb(pcb_path=pcb_path)

        assert (result.traces_added, result.vias_added) == (4, 2)
        assert result.keepouts_promoted == 1
        assert Path(result.routed_path).exists()


def _autoroute_seams(major, export_dsn=None):
    """Every subprocess seam of autoroute_pcb plus the pcbnew era probe.

    The SES import just copies the board over, so nothing is routed; these
    tests are about what the preflight does before and around the pipeline.
    """

    def touch_dsn(dsn_source, dsn_path):
        Path(dsn_path).touch()
        return None

    def touch_ses(**kwargs):
        Path(kwargs["ses_path"]).touch()
        return None

    def copy_board(source, ses_path, output_path):
        shutil.copy(source, output_path)
        return None

    return patch.multiple(
        pcb,
        _check_java=lambda: None,
        _ensure_jar=lambda: ("/fake/freerouting.jar", None),
        _export_dsn=export_dsn or touch_dsn,
        _run_freerouting=touch_ses,
        _import_ses=copy_board,
        _pcbnew_major=lambda: major,
    )


class TestAutoroutePreflight:
    """pcbnew 9 cannot load a KiCad 10 board at all, and pcbnew 10 saves the
    routed copy in the KiCad 10 format whatever the input board was."""

    def _k10(self, scratch_pcb):
        from test_cst_pcb import _bump_version

        return _bump_version(scratch_pcb)

    def test_k10_board_on_pcbnew9_refused(self, scratch_pcb):
        export_dsn = MagicMock(return_value=None)
        with _autoroute_seams(9, export_dsn=export_dsn):
            with pytest.raises(ToolError) as exc:
                pcb.autoroute_pcb(pcb_path=self._k10(scratch_pcb))
        assert "20260206" in str(exc.value)
        assert "pcbnew 9" in str(exc.value)
        assert "KICAD_PYTHON" in str(exc.value)
        # Refused up front, so no board ever reached pcbnew.
        export_dsn.assert_not_called()

    def test_k10_board_on_pcbnew10_routes(self, scratch_pcb):
        with _autoroute_seams(10):
            result = pcb.autoroute_pcb(pcb_path=self._k10(scratch_pcb))
        assert Path(result.routed_path).exists()
        assert result.warnings == []

    def test_k9_board_on_pcbnew10_warns(self, scratch_pcb):
        with _autoroute_seams(10):
            result = pcb.autoroute_pcb(pcb_path=str(scratch_pcb))
        assert len(result.warnings) == 1
        assert "KiCad 10" in result.warnings[0]

    def test_k9_board_on_pcbnew9_quiet(self, scratch_pcb):
        with _autoroute_seams(9):
            result = pcb.autoroute_pcb(pcb_path=str(scratch_pcb))
        assert result.warnings == []

    def test_unknown_major_proceeds(self, scratch_pcb):
        """No answer from the probe means no opinion: the existing pcbnew
        error paths stay the authority."""
        with _autoroute_seams(None):
            result = pcb.autoroute_pcb(pcb_path=self._k10(scratch_pcb))
        assert Path(result.routed_path).exists()
        assert result.warnings == []


_PROPERTY_TEXT_BOARD = """(kicad_pcb (version 20241108) (generator "pcbnew")
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)
    (36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen")
    (44 "Edge.Cuts" user) (48 "B.Fab" user) (49 "F.Fab" user))
  (setup (pad_to_mask_clearance 0))
  (net 0 "")
  (footprint "Test:U"
    (layer "F.Cu")
    (uuid "fp-0001")
    (at 100 100 0)
    (property "Reference" "U1" (at 50 -50 90) (layer "F.SilkS") (uuid "p-1"))
    (property "Value" "TEST" (at -30 40 0) (layer "F.Fab") (uuid "p-2"))
    (property "Datasheet" "" (at 0 0 0) (layer "F.Fab") (uuid "p-3"))
    (property "MPN" "XYZ-1" (at 20 0 0) (layer "F.Fab") (uuid "p-4"))
    (property "Description" "no position at all")
    (fp_text user "note" (at 0 -20) (layer "F.Fab") (uuid "t-1"))
    (fp_text reference "U1" (at 0 -1) (layer "F.SilkS") (uuid "t-2"))
  )
)
"""


class TestFixDisplacedFpText:
    """The widening: pcbnew 7+ stores Reference and Value as (property ...)
    nodes, which the retired kiutils twin never saw."""

    def test_property_nodes_reset_with_rotation_kept(self, tmp_path):
        board = tmp_path / "props.kicad_pcb"
        board.write_text(_PROPERTY_TEXT_BOARD)

        assert pcb._fix_displaced_fp_text(str(board)) == 4

        fp = _cst.parse(board.read_bytes()).lists[0].find("footprint")
        at = {p.atoms[1].text: p.find("at") for p in fp.find_all("property")}
        # Reference and Value land on their defaults; the rotation atom stays.
        assert [a.text for a in at["Reference"].atoms[1:]] == ["0", "-1.5", "90"]
        assert [a.text for a in at["Value"].atoms[1:]] == ["0", "1.5", "0"]
        # Undisplaced text is untouched, unknown keys reset to the origin.
        assert [a.text for a in at["Datasheet"].atoms[1:]] == ["0", "0", "0"]
        assert [a.text for a in at["MPN"].atoms[1:]] == ["0", "0", "0"]
        assert at["Description"] is None
        texts = {t.atoms[1].text: t.find("at") for t in fp.find_all("fp_text")}
        assert [a.text for a in texts["user"].atoms[1:]] == ["0", "0"]
        assert [a.text for a in texts["reference"].atoms[1:]] == ["0", "-1"]

    def test_nothing_displaced_leaves_the_file_alone(self, tmp_path):
        board = tmp_path / "props.kicad_pcb"
        board.write_text(_PROPERTY_TEXT_BOARD)
        pcb._fix_displaced_fp_text(str(board))

        settled = board.read_bytes()
        assert pcb._fix_displaced_fp_text(str(board)) == 0
        assert board.read_bytes() == settled


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


class TestAddCopperZone:
    def test_basic_zone(self, scratch_pcb):
        result = pcb.add_copper_zone(
            net_name="Net1",
            layer="F.Cu",
            corners=[{"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 50, "y": 50}, {"x": 0, "y": 50}],
            pcb_path=str(scratch_pcb),
        )
        assert result.net == "Net1"
        assert result.layer == "F.Cu"
        assert result.corners == 4
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
        assert zone.connectPads == "yes"  # measured: native solid connect token

    def test_fewer_than_3_corners(self, scratch_pcb):
        with pytest.raises(ToolError):
            pcb.add_copper_zone(
                net_name="Net1",
                layer="F.Cu",
                corners=[{"x": 0, "y": 0}, {"x": 10, "y": 0}],
                pcb_path=str(scratch_pcb),
            )

    def test_invalid_net(self, scratch_pcb):
        with pytest.raises(ToolError):
            pcb.add_copper_zone(
                net_name="NonExistent",
                layer="F.Cu",
                corners=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
                pcb_path=str(scratch_pcb),
            )


class TestFillZones:
    def test_no_pcbnew_returns_error(self, scratch_pcb):
        with patch("mcp_server_kicad.pcb._find_pcbnew_python", return_value=(None, None)):
            with pytest.raises(ToolError):
                pcb.fill_zones(pcb_path=str(scratch_pcb))

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
        assert result.zones_filled == 1
        assert result.status == "ok"


_NETLIST_SUMMARY_JSON = (
    '{"status": "ok", "added": ["R1"], "value_updated": [], "fpid_changed": [],'
    ' "stale_footprints": [], "stale_removed": [], "nets_added": 2, "nets_removed": 0,'
    ' "pads_bound": 2, "orphaned_tracks": 0, "orphaned_zones": 0, "skipped": [],'
    ' "warnings": []}'
)


class TestUpdatePcbFromSchematic:
    """The schematic must exist; the board must not have to.

    These mock the pcbnew subprocess away, so the schematic used to be a name
    that was never written and nothing minded. It has to be a real file now,
    because the tool checks its input before spawning anything. The board stays
    a bare path on purpose: creating it when it is missing is this tool's
    documented first use, and the E2E suite starts from exactly that state.
    """

    def test_no_pcbnew_returns_error(self, scratch_sch, tmp_path):
        with patch("mcp_server_kicad.pcb._find_pcbnew_python", return_value=(None, None)):
            with pytest.raises(ToolError, match="pcbnew"):
                pcb.update_pcb_from_schematic(
                    schematic_path=str(scratch_sch),
                    pcb_path=str(tmp_path / "a.kicad_pcb"),
                )

    def test_a_missing_schematic_is_refused(self, tmp_path):
        """Named separately from the empty case, which has its own message."""
        with pytest.raises(ToolError, match="schematic not found"):
            pcb.update_pcb_from_schematic(
                schematic_path=str(tmp_path / "absent.kicad_sch"),
                pcb_path=str(tmp_path / "a.kicad_pcb"),
            )

    def test_empty_paths_rejected(self, scratch_sch):
        with pytest.raises(ToolError, match="No schematic path given"):
            pcb.update_pcb_from_schematic(schematic_path="", pcb_path="x.kicad_pcb")
        # A real schematic, so this reaches the pcb_path check rather than
        # stopping at the schematic one and passing for the wrong reason.
        with pytest.raises(ToolError, match="No PCB path provided"):
            pcb.update_pcb_from_schematic(schematic_path=str(scratch_sch), pcb_path="")

    def _run_mocked(
        self, scratch_sch, tmp_path, delete_stale=False, returncode=0, stdout=None, stderr=""
    ):
        """Run the tool with kicad-cli and the pcbnew subprocess both mocked.

        Returns (result_or_exception, pcbnew_argv).
        """
        (tmp_path / "TestLib.pretty").mkdir(exist_ok=True)

        def fake_cli(args, check=True):
            out = args[args.index("--output") + 1]
            Path(out).write_text('<export version="E"/>')
            return type("R", (), {"returncode": 0, "stderr": ""})()

        mock_proc = type(
            "P",
            (),
            {
                "returncode": returncode,
                "stdout": _NETLIST_SUMMARY_JSON + "\n" if stdout is None else stdout,
                "stderr": stderr,
            },
        )()
        with (
            patch(
                "mcp_server_kicad.pcb._find_pcbnew_python",
                return_value=("/usr/bin/python3", None),
            ),
            patch("mcp_server_kicad.pcb._run_cli", side_effect=fake_cli),
            patch("subprocess.run", return_value=mock_proc) as sub,
        ):
            result = pcb.update_pcb_from_schematic(
                schematic_path=str(scratch_sch),
                pcb_path=str(tmp_path / "a.kicad_pcb"),
                delete_stale=delete_stale,
            )
        return result, sub.call_args[0][0]

    def test_success_mocked(self, scratch_sch, tmp_path):
        result, argv = self._run_mocked(scratch_sch, tmp_path)
        assert result.status == "ok"
        assert result.added == ["R1"]
        assert result.nets_added == 2
        # argv: [python, script, netlist, pcb, --lib-dir, ...]
        assert argv[1].endswith("_netlist_import.py")
        assert argv[2].endswith("netlist.xml")
        assert argv[3].endswith("a.kicad_pcb")
        lib_dirs = [argv[i + 1] for i, a in enumerate(argv) if a == "--lib-dir"]
        assert any(d.endswith("TestLib.pretty") for d in lib_dirs)

    def test_delete_stale_flag_propagates(self, scratch_sch, tmp_path):
        _, argv = self._run_mocked(scratch_sch, tmp_path, delete_stale=True)
        assert "--delete-stale" in argv
        _, argv = self._run_mocked(scratch_sch, tmp_path, delete_stale=False)
        assert "--delete-stale" not in argv

    def test_script_failure_raises(self, scratch_sch, tmp_path):
        with pytest.raises(ToolError, match="boom"):
            self._run_mocked(scratch_sch, tmp_path, returncode=1, stdout="", stderr="boom")

    def test_garbage_stdout_raises(self, scratch_sch, tmp_path):
        with pytest.raises(ToolError, match="no summary"):
            self._run_mocked(scratch_sch, tmp_path, stdout="not json at all\n")


class TestSetTraceWidth:
    def test_widen_by_net(self, scratch_pcb):
        _board_with_traces(scratch_pcb)
        result = pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=str(scratch_pcb))
        assert result.traces_modified == 3  # original scratch trace + 2 added on Net1
        assert result.new_width_mm == 0.5
        board = Board.from_file(str(scratch_pcb))
        for seg in board.traceItems:
            if isinstance(seg, Segment) and seg.net == 1:
                assert seg.width == 0.5

    def test_no_filters_returns_error(self, scratch_pcb):
        with pytest.raises(ToolError):
            pcb.set_trace_width(width=0.5, pcb_path=str(scratch_pcb))

    def test_no_matches_returns_zero(self, scratch_pcb):
        result = pcb.set_trace_width(width=0.5, net_name="Net2", pcb_path=str(scratch_pcb))
        assert result.traces_modified == 0

    def test_consecutive_calls_on_different_nets(self, scratch_pcb):
        """Calling set_trace_width on one net then another must not crash.

        Regression: the first call saves the board via kiutils; the second
        call must be able to re-read the file that kiutils wrote.
        """
        _board_with_traces(scratch_pcb)
        # First call — widen Net1
        r1 = pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=str(scratch_pcb))
        assert r1.traces_modified > 0
        # Second call — widen Net2 (re-reads the file saved by the first call)
        r2 = pcb.set_trace_width(width=0.75, net_name="Net2", pcb_path=str(scratch_pcb))
        assert r2.traces_modified > 0

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
        r1 = pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=str(pcb_file))
        assert r1.traces_modified == 2

        # Second call must NOT crash with IndexError
        r2 = pcb.set_trace_width(width=0.75, net_name="Net2", pcb_path=str(pcb_file))
        assert r2.traces_modified == 1


class TestRemoveTraces:
    def test_remove_by_net(self, scratch_pcb):
        _board_with_traces(scratch_pcb)
        result = pcb.remove_traces(net_name="Net2", pcb_path=str(scratch_pcb))
        assert result.traces_removed == 2
        board = Board.from_file(str(scratch_pcb))
        net2_segs = [t for t in board.traceItems if isinstance(t, Segment) and t.net == 2]
        assert len(net2_segs) == 0

    def test_does_not_remove_vias(self, scratch_pcb):
        pcb.add_via(100, 100, net=1, pcb_path=str(scratch_pcb))
        result = pcb.remove_traces(net_name="Net1", pcb_path=str(scratch_pcb))
        assert result.traces_removed == 1
        board = Board.from_file(str(scratch_pcb))
        vias = [t for t in board.traceItems if isinstance(t, Via)]
        assert len(vias) == 1

    def test_no_filters_returns_error(self, scratch_pcb):
        with pytest.raises(ToolError):
            pcb.remove_traces(pcb_path=str(scratch_pcb))


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
        assert result.vias_added == 4
        assert result.reference == "R1"
        board = Board.from_file(str(scratch_pcb))
        vias = [t for t in board.traceItems if isinstance(t, Via)]
        assert len(vias) == 4

    def test_footprint_not_found(self, scratch_pcb):
        with pytest.raises(ToolError):
            pcb.add_thermal_vias(reference="U99", pcb_path=str(scratch_pcb))

    def test_auto_detect_net_from_pad(self, scratch_pcb):
        """When net_name is not provided, auto-detect from pad."""
        result = pcb.add_thermal_vias(
            reference="R1",
            pad_number="1",
            rows=1,
            cols=1,
            pcb_path=str(scratch_pcb),
        )
        assert result.vias_added == 1
        board = Board.from_file(str(scratch_pcb))
        via = [t for t in board.traceItems if isinstance(t, Via)][0]
        # Pad 1 of R1 should have a net number assigned
        assert via.net >= 0

    def test_pad_not_found(self, scratch_pcb):
        with pytest.raises(ToolError):
            pcb.add_thermal_vias(reference="R1", pad_number="99", pcb_path=str(scratch_pcb))

    def test_invalid_net_name(self, scratch_pcb):
        with pytest.raises(ToolError):
            pcb.add_thermal_vias(
                reference="R1",
                pad_number="1",
                net_name="NonExistent",
                pcb_path=str(scratch_pcb),
            )

    def test_auto_detect_largest_smd_pad(self, scratch_pcb):
        """When no pad_number given, pick largest SMD pad."""
        result = pcb.add_thermal_vias(
            reference="R1",
            rows=1,
            cols=1,
            pcb_path=str(scratch_pcb),
        )
        assert result.vias_added == 1
        # Should have picked one of the pads (both are same size)
        assert result.pad in ("1", "2")


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
        result = pcb.check_placement("R1", 100, 100, pcb_path=str(pcb_path))
        assert result.status == "ok"

    def test_violation_inside_keepout(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path)
        result = pcb.check_placement("R1", 25, 25, pcb_path=str(pcb_path))
        assert result.status == "violations_found"
        assert len(result.keepout_violations) >= 1

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
        result = pcb.check_placement("R1", 50, 50, pcb_path=str(path))
        assert result.status == "violations_found"
        assert len(result.keepout_violations) >= 1
        sources = [v["source"] for v in result.keepout_violations]
        assert any(s.startswith("footprint:") for s in sources)

    def test_outside_board_edge(self, tmp_path):
        pcb_path = _make_keepout_pcb(tmp_path)
        result = pcb.check_placement("R1", 500, 500, pcb_path=str(pcb_path))
        assert result.outside_board_edge is True

    def test_no_edge_cuts(self, tmp_path):
        """Board without Edge.Cuts: board_edge_checked should be false."""
        pcb_path = _make_keepout_pcb(tmp_path, with_edge_cuts=False)
        result = pcb.check_placement("R1", 100, 100, pcb_path=str(pcb_path))
        assert result.board_edge_checked is False


class TestAddKeepoutZone:
    def test_basic_keepout(self, scratch_pcb):
        corners: list[PointSpec] = [
            {"x": 0, "y": 0},
            {"x": 50, "y": 0},
            {"x": 50, "y": 50},
            {"x": 0, "y": 50},
        ]
        result = pcb.add_keepout_zone(corners=corners, pcb_path=str(scratch_pcb))
        assert result.corners == 4
        assert "F.Cu" in result.layers
        assert result.restrictions["footprints"] == "not_allowed"

        # Verify it's actually on the board
        board = Board.from_file(str(scratch_pcb))
        keepout_zones = [z for z in board.zones if z.keepoutSettings is not None]
        assert len(keepout_zones) == 1
        assert len(keepout_zones[0].polygons[0].coordinates) == 4

    def test_too_few_corners(self, scratch_pcb):
        corners: list[PointSpec] = [{"x": 0, "y": 0}, {"x": 10, "y": 0}]
        with pytest.raises(ToolError):
            pcb.add_keepout_zone(corners=corners, pcb_path=str(scratch_pcb))

    def test_custom_restrictions(self, scratch_pcb):
        """no_tracks=False should produce tracks='allowed' in created zone."""
        corners: list[PointSpec] = [
            {"x": 0, "y": 0},
            {"x": 50, "y": 0},
            {"x": 50, "y": 50},
        ]
        result = pcb.add_keepout_zone(
            corners=corners,
            no_tracks=False,
            pcb_path=str(scratch_pcb),
        )
        assert result.restrictions["tracks"] == "allowed"
        assert result.restrictions["vias"] == "not_allowed"

        # Verify on disk
        board = Board.from_file(str(scratch_pcb))
        kz = [z for z in board.zones if z.keepoutSettings is not None][0]
        assert kz.keepoutSettings is not None
        assert kz.keepoutSettings.tracks == "allowed"


class TestSetNetClass:
    """Tests for set_net_class which edits the .kicad_pro project file."""

    import json

    @staticmethod
    def _create_pro(pcb_path: Path, pro_data: dict | None = None) -> Path:
        """Create a .kicad_pro alongside the given .kicad_pcb path."""
        import json

        pro_path = pcb_path.with_suffix(".kicad_pro")
        if pro_data is None:
            pro_data = {"meta": {"filename": pro_path.name, "version": 1}}
        pro_path.write_text(json.dumps(pro_data, indent=2) + "\n")
        return pro_path

    def test_works_without_pcbnew(self, scratch_pcb):
        """set_net_class must work without pcbnew — it edits the .kicad_pro file."""
        import json

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
        assert result.net_class == "Power"
        assert result.nets_assigned == 2
        assert result.track_width_mm == 0.5
        assert result.clearance_mm == 0.3

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
        with pytest.raises(ToolError, match="Project file not found"):
            pcb.set_net_class(
                name="Power",
                nets=["Net1"],
                track_width=0.5,
                pcb_path=str(scratch_pcb),
            )

    def test_success_returns_proper_result(self, scratch_pcb):
        """Return result with net_class, nets_assigned, track_width_mm, clearance_mm."""
        self._create_pro(scratch_pcb)

        result = pcb.set_net_class(
            name="Power",
            nets=["Net1", "Net2"],
            track_width=0.5,
            clearance=0.3,
            pcb_path=str(scratch_pcb),
        )
        assert result.net_class == "Power"
        assert result.nets_assigned == 2
        assert result.track_width_mm == 0.5
        assert result.clearance_mm == 0.3

    def test_updates_existing_net_class(self, scratch_pcb):
        """Updating an existing net class should merge, not duplicate."""
        import json

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
        import json

        self._create_pro(scratch_pcb)

        result = pcb.set_net_class(
            name="Signal",
            nets=["Net1"],
            pcb_path=str(scratch_pcb),
        )
        assert result.net_class == "Signal"
        assert result.track_width_mm is None
        assert result.clearance_mm is None

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
        assert result.tracks_removed >= 1

    def test_preserves_connected_traces(self, scratch_pcb):
        """The scratch board trace connects to R1 pads -- should not be removed."""
        result = pcb.remove_dangling_tracks(pcb_path=str(scratch_pcb))
        assert result.tracks_removed == 0
        board = Board.from_file(str(scratch_pcb))
        segs = [t for t in board.traceItems if isinstance(t, Segment)]
        assert len(segs) == 1

    def test_empty_board(self, scratch_pcb):
        """Board with no traces at all."""
        board = Board.from_file(str(scratch_pcb))
        board.traceItems = []
        board.to_file()
        result = pcb.remove_dangling_tracks(pcb_path=str(scratch_pcb))
        assert result.tracks_removed == 0
        assert result.iterations == 0


class TestProjectFileEncoding:
    """A .kicad_pro is UTF-8. Python's text mode does not assume that.

    With no ``encoding=``, ``read_text`` decodes with
    ``locale.getpreferredencoding(False)``, the ANSI code page on Windows.
    Measured on this box before the fix, on a project file carrying a designer
    name and a units string: both came back mangled, the file was rewritten with
    the mangled text plus escaped code points, and set_net_class returned a normal
    NetClassResult. Silent corruption of a file the tool was only meant to add a
    net class to.

    The whole package had zero ``encoding=`` occurrences at that point, so this
    reached run_erc, run_drc, list_unconnected_pins, export_bom and
    export_positions too. Those read reports rather than user files, which is
    why this test guards the one that writes back.
    """

    AUTHOR = "Andr\u00e9 Amp\u00e8re"
    NOTE = "50 \u03a9 / 25 \u00b0C"

    def _project(self, scratch_pcb: Path) -> Path:
        pro = scratch_pcb.with_suffix(".kicad_pro")
        body = {
            "meta": {"filename": pro.name, "version": 1},
            "text_variables": {"AUTHOR": self.AUTHOR, "NOTE": self.NOTE},
        }
        # Bytes, so the fixture cannot itself be re-encoded by the platform.
        pro.write_bytes(json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8") + b"\n")
        return pro

    def test_non_ascii_survives_a_net_class_edit(self, scratch_pcb):
        pro = self._project(scratch_pcb)
        pcb.set_net_class(name="Power", nets=["Net1"], clearance=0.3, pcb_path=str(scratch_pcb))
        data = json.loads(pro.read_bytes().decode("utf-8"))
        assert data["text_variables"]["AUTHOR"] == self.AUTHOR
        assert data["text_variables"]["NOTE"] == self.NOTE
        assert data["net_settings"]["classes"][-1]["name"] == "Power"

    def test_it_stays_readable_rather_than_escaped(self, scratch_pcb):
        """ensure_ascii would keep the text correct but unreadable.

        An escaped code point round-trips through json fine, so the first test
        passes either
        way. The user's own file should still contain their own alphabet.
        """
        pro = self._project(scratch_pcb)
        pcb.set_net_class(name="Power", nets=["Net1"], clearance=0.3, pcb_path=str(scratch_pcb))
        raw = pro.read_bytes()
        assert self.AUTHOR.encode("utf-8") in raw
        # Raw: the JSON escape sequence itself, not the character it denotes.
        assert rb"\u00e9" not in raw

    def test_a_file_that_is_not_utf8_is_refused_not_traced(self, scratch_pcb):
        """UnicodeDecodeError is a ValueError, so it used to escape the except.

        json.JSONDecodeError subclasses ValueError but is not its parent, so
        catching JSONDecodeError never caught this and the client got a raw
        traceback instead of a message.
        """
        pro = scratch_pcb.with_suffix(".kicad_pro")
        pro.write_bytes(b'{"meta": {"filename": "x", "version": 1}, "n": "\xff\xfe not utf-8"}')
        with pytest.raises(ToolError, match="Failed to read project file"):
            pcb.set_net_class(name="Power", nets=["Net1"], clearance=0.3, pcb_path=str(scratch_pcb))


def scratch_pcb_bytes_source(tmp_path: Path) -> Path:
    """A freshly built board on disk, for tests that need a second one."""
    from kiutils.board import Board
    from kiutils.items.common import Net

    b = Board.create_new()
    b.generator = "pcbnew"
    b.nets = [Net(number=0, name=""), Net(number=1, name="Net1")]
    b.footprints.append(build_test_footprint())
    out = tmp_path / "_source.kicad_pcb"
    b.filePath = str(out)
    b.to_file()
    return out


def _fill_seams(major):
    """fill_zones' pcbnew seam plus the era probe, in _autoroute_seams' shape."""
    return patch.multiple(
        pcb,
        _find_pcbnew_python=lambda: ("/usr/bin/python3", None),
        _pcbnew_major=lambda: major,
    )


def _fake_fill(board: Path, zones: int = 1, upgrade: bool = False):
    """Stand in for the fill subprocess, optionally bumping the version stamp
    the way pcbnew's SaveBoard does."""

    def run(argv, **kwargs):
        if upgrade:
            from test_cst_pcb import _bump_version

            _bump_version(board)
        return type("R", (), {"returncode": 0, "stdout": f"{zones}\n", "stderr": ""})()

    return run


class TestFillZonesPreflight:
    """fill_zones writes the user's board in place, through pcbnew.

    Two different hazards, handled two different ways because only one of them
    is predictable. A KiCad 10 board on pcbnew 9 cannot load at all, and that is
    knowable up front. The format upgrade is not: pcbnew reports its major
    version, never the stamp it writes, and the corruption measured on KiCad 9
    (20241030 -> 20241229 on KiCad's own shipped multichannel_mixer-unrouted,
    dropping 114 footprint property UUIDs) is inside the KiCad 9 era on both
    sides. No comparison of (major, board_version) can see it. Reading the file
    afterward always can.
    """

    def test_k10_board_on_pcbnew9_is_refused_by_name(self, scratch_pcb):
        from test_cst_pcb import _bump_version

        board = _bump_version(scratch_pcb)
        spawned = MagicMock()
        with _fill_seams(9), patch("subprocess.run", spawned):
            with pytest.raises(ToolError) as exc:
                pcb.fill_zones(pcb_path=board)
        assert "20260206" in str(exc.value)
        assert "pcbnew 9" in str(exc.value)
        assert "KICAD_PYTHON" in str(exc.value)
        # Refused before anything was spawned, which is the point: today this
        # reaches pcbnew and comes back as a NoneType AttributeError.
        spawned.assert_not_called()

    def test_a_silent_format_upgrade_is_reported(self, scratch_pcb):
        with _fill_seams(9), patch("subprocess.run", _fake_fill(scratch_pcb, upgrade=True)):
            result = pcb.fill_zones(pcb_path=str(scratch_pcb))
        assert result.status == "ok"
        assert len(result.warnings) == 1
        note = result.warnings[0]
        assert "20211014" in note and "20260206" in note
        assert "version control" in note
        assert "KiCad 9 install can no longer open" in note

    def test_quiet_when_the_stamp_holds(self, scratch_pcb):
        """Guards against a guard that always fires."""
        with _fill_seams(9), patch("subprocess.run", _fake_fill(scratch_pcb)):
            result = pcb.fill_zones(pcb_path=str(scratch_pcb))
        assert result.warnings == []

    def test_an_unknown_major_proceeds(self, scratch_pcb):
        """No answer from the probe means no opinion, as pcbnew_major documents."""
        from test_cst_pcb import _bump_version

        board = _bump_version(scratch_pcb)
        with _fill_seams(None), patch("subprocess.run", _fake_fill(Path(board))):
            assert pcb.fill_zones(pcb_path=board).status == "ok"


class TestUpdatePcbPreflight:
    """Same two hazards as fill_zones, plus one this tool has and that does not.

    update_pcb_from_schematic creates the board when it is missing, which is its
    documented first use and what the whole E2E suite starts from. Both checks
    are therefore conditional on the file existing. The first version of this
    got that wrong in a way the mocked tests caught immediately: the warning
    helper read the board before testing whether there was one to read.
    """

    def test_k10_board_on_pcbnew9_is_refused_before_kicad_cli_runs(self, scratch_sch, tmp_path):
        from test_cst_pcb import _bump_version

        board = tmp_path / "b.kicad_pcb"
        shutil.copy(scratch_pcb_bytes_source(tmp_path), board)
        _bump_version(board)
        cli = MagicMock()
        with patch.object(pcb, "_pcbnew_major", lambda: 9), patch.object(pcb, "_run_cli", cli):
            with pytest.raises(ToolError) as exc:
                pcb.update_pcb_from_schematic(schematic_path=str(scratch_sch), pcb_path=str(board))
        assert "20260206" in str(exc.value) and "pcbnew 9" in str(exc.value)
        # The refusal beats the netlist export, so no kicad-cli process ran.
        cli.assert_not_called()

    def test_a_board_that_does_not_exist_yet_skips_both_checks(self, scratch_sch, tmp_path):
        """The create-if-missing path. This is the one my first attempt broke."""
        missing = tmp_path / "not-yet.kicad_pcb"
        with patch.object(pcb, "_pcbnew_major", lambda: 9):
            # Reaching the pcbnew probe means neither check refused or read.
            with patch.object(pcb, "_find_pcbnew_python", lambda: (None, None)):
                with pytest.raises(ToolError, match="pcbnew Python bindings not found"):
                    pcb.update_pcb_from_schematic(
                        schematic_path=str(scratch_sch), pcb_path=str(missing)
                    )
