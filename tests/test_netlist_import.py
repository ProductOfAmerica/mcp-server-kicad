"""Tests for _netlist_import: pure functions here, real-pcbnew E2E below.

The pure-function tests run everywhere (importing the module in the venv
is itself the proof that pcbnew stays deferred). The E2E class needs both
kicad-cli and KiCad's Python with pcbnew.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest
from conftest import HAS_KICAD_CLI

from mcp_server_kicad import _netlist_import as ni
from mcp_server_kicad._freerouting import find_pcbnew_python
from mcp_server_kicad._shared import _kicad_root, _resolve_system_lib
from mcp_server_kicad.models import UpdatePcbResult

NETLIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<export version="E">
  <components>
    <comp ref="R1">
      <value>10K</value>
      <footprint>Resistor_SMD:R_0603_1608Metric</footprint>
      <sheetpath names="/" tstamps="/"/>
      <tstamps>aaaa-bbbb</tstamps>
    </comp>
    <comp ref="J1">
      <value>Conn</value>
      <sheetpath names="/sub/" tstamps="/1111-2222/"/>
      <tstamps>cccc-dddd</tstamps>
    </comp>
  </components>
  <nets>
    <net code="1" name="/SIG">
      <node ref="R1" pin="1"/>
      <node ref="J1" pin="2"/>
    </net>
    <net code="2" name="GND">
      <node ref="R1" pin="2"/>
    </net>
  </nets>
</export>
"""


@pytest.fixture
def netlist_file(tmp_path):
    p = tmp_path / "test.xml"
    p.write_text(NETLIST_XML)
    return str(p)


class TestParseNetlist:
    def test_components(self, netlist_file):
        components, _ = ni.parse_netlist(netlist_file)
        assert [c["ref"] for c in components] == ["R1", "J1"]
        r1 = components[0]
        assert r1["value"] == "10K"
        assert r1["footprint"] == "Resistor_SMD:R_0603_1608Metric"

    def test_empty_footprint_field(self, netlist_file):
        components, _ = ni.parse_netlist(netlist_file)
        assert components[1]["footprint"] == ""

    def test_kiid_paths(self, netlist_file):
        """Root component: /<uuid>; sub-sheet component: /<sheet>/<uuid>."""
        components, _ = ni.parse_netlist(netlist_file)
        assert components[0]["path"] == "/aaaa-bbbb"
        assert components[1]["path"] == "/1111-2222/cccc-dddd"

    def test_nets_ignore_code(self, netlist_file):
        _, nets = ni.parse_netlist(netlist_file)
        assert [n["name"] for n in nets] == ["/SIG", "GND"]
        assert nets[0]["nodes"] == [("R1", "1"), ("J1", "2")]

    def test_malformed_xml_raises(self, tmp_path):
        p = tmp_path / "bad.xml"
        p.write_text("<export><unclosed>")
        with pytest.raises(ParseError):
            ni.parse_netlist(str(p))


class TestResolvePretty:
    def test_basename_hit(self, tmp_path):
        pretty = tmp_path / "TestLib.pretty"
        pretty.mkdir()
        assert ni.resolve_pretty("TestLib", [str(pretty)]) == str(pretty)

    def test_parent_dir_hit(self, tmp_path):
        """System-style dir containing many .pretty subdirs."""
        (tmp_path / "Resistor_SMD.pretty").mkdir()
        got = ni.resolve_pretty("Resistor_SMD", [str(tmp_path)])
        assert got == str(tmp_path / "Resistor_SMD.pretty")

    def test_miss(self, tmp_path):
        assert ni.resolve_pretty("Nope", [str(tmp_path)]) is None


class TestGridSlot:
    def test_deterministic_and_wraps(self):
        assert ni.grid_slot(0, 100, 200, 10) == (100, 200)
        assert ni.grid_slot(9, 100, 200, 10) == (190, 200)
        assert ni.grid_slot(10, 100, 200, 10) == (100, 210)
        assert ni.grid_slot(0, 100, 200, 10) == ni.grid_slot(0, 100, 200, 10)


class TestSummarySchema:
    def test_matches_update_pcb_result(self):
        """Drift guard: the script's summary IS the tool's result model."""
        summary = ni.new_summary()
        assert set(summary) == set(UpdatePcbResult.model_fields)
        result = UpdatePcbResult(**summary)
        assert result.status == "ok"


# ===========================================================================
# E2E — real kicad-cli + pcbnew + stock libraries (first real-pcbnew tests
# in the suite; they run locally with KiCad installed and on the Linux
# kicad-suite / macOS discovery CI runners)
# ===========================================================================


def _stock_footprints_available() -> bool:
    root = _kicad_root()
    if root is None:
        return False
    return any(
        (root / sub).is_dir() for sub in ("share/kicad/footprints", "SharedSupport/footprints")
    )


# pcbnew is deliberately NOT in this gate any more. update_pcb_from_schematic
# runs on the CST in-process, so it needs kicad-cli for the netlist export and
# the stock libraries for the footprints, and nothing else. Leaving pcbnew in
# would skip the whole class on a machine that can run every test in it, which
# is how coverage quietly disappears.
HAS_E2E_ENV = (
    HAS_KICAD_CLI and _resolve_system_lib("Device") is not None and _stock_footprints_available()
)
requires_e2e = pytest.mark.skipif(
    not HAS_E2E_ENV, reason="kicad-cli + stock KiCad libraries required"
)
# fill_zones still asks pcbnew to compute the fills, so the one test that fills
# a zone keeps its own gate.
requires_pcbnew = pytest.mark.skipif(
    find_pcbnew_python()[0] is None, reason="pcbnew Python bindings required"
)


def _make_project(tmp_path):
    """Two stock resistors wired into /SIG and /GND, footprints assigned."""
    from mcp_server_kicad.project import create_project
    from mcp_server_kicad.schematic import (
        place_component,
        set_component_property,
        wire_pins_to_net,
    )

    create_project(str(tmp_path), "e2e")
    sch = str(tmp_path / "e2e.kicad_sch")
    place_component("Device:R", "R1", "10K", 100, 80, schematic_path=sch)
    place_component("Device:R", "R2", "4.7K", 130, 80, schematic_path=sch)
    set_component_property("R1", "Footprint", "Resistor_SMD:R_0603_1608Metric", schematic_path=sch)
    set_component_property("R2", "Footprint", "Resistor_SMD:R_0805_2012Metric", schematic_path=sch)
    wire_pins_to_net(
        [{"reference": "R1", "pin": "1"}, {"reference": "R2", "pin": "1"}],
        "SIG",
        schematic_path=sch,
    )
    wire_pins_to_net(
        [{"reference": "R1", "pin": "2"}, {"reference": "R2", "pin": "2"}],
        "GND",
        schematic_path=sch,
    )
    return sch, str(tmp_path / "e2e.kicad_pcb")


@requires_e2e
class TestUpdatePcbE2E:
    def test_initial_import(self, tmp_path):
        from mcp_server_kicad.pcb import (
            get_footprint_pads,
            list_pcb_nets,
            update_pcb_from_schematic,
        )

        sch, pcb_path = _make_project(tmp_path)
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.status == "ok"
        assert sorted(result.added) == ["R1", "R2"]
        assert result.pads_bound == 4
        assert result.skipped == []
        net_names = {n.name for n in list_pcb_nets(pcb_path=pcb_path)}
        assert net_names == {"/SIG", "/GND"}
        pads = get_footprint_pads("R1", pcb_path=pcb_path)
        assert "/SIG" in pads
        assert "/GND" in pads

    def test_reimport_idempotent(self, tmp_path):
        from mcp_server_kicad.pcb import (
            list_pcb_footprints,
            list_pcb_nets,
            update_pcb_from_schematic,
        )

        sch, pcb_path = _make_project(tmp_path)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        once = Path(pcb_path).read_bytes()
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        # Byte-identical, not merely equivalent. This assertion could not exist
        # while pcbnew did the writing: it rewrote the whole file every run, so a
        # second import moved the format stamp, dropped coincident shapes and,
        # on Windows, converted every line ending. Nothing is asked to change
        # here, so nothing may change.
        assert Path(pcb_path).read_bytes() == once
        assert result.added == []
        assert result.fpid_changed == []
        assert result.nets_added == 0
        assert len(list_pcb_footprints(pcb_path=pcb_path)) == 2
        names = sorted(n.name for n in list_pcb_nets(pcb_path=pcb_path))
        assert names == ["/GND", "/SIG"]

    def test_value_update_preserves_position(self, tmp_path):
        from mcp_server_kicad.pcb import (
            list_pcb_footprints,
            move_footprint,
            update_pcb_from_schematic,
        )
        from mcp_server_kicad.schematic import set_component_property

        sch, pcb_path = _make_project(tmp_path)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        move_footprint("R1", 42, 24, pcb_path=pcb_path)
        set_component_property("R1", "Value", "22K", schematic_path=sch)
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.value_updated == ["R1"]
        r1 = next(f for f in list_pcb_footprints(pcb_path=pcb_path) if f.reference == "R1")
        assert (r1.x, r1.y) == (42, 24)
        assert r1.value == "22K"

    def test_net_rename_orphans_traces(self, tmp_path):
        from mcp_server_kicad.pcb import (
            add_trace,
            list_pcb_nets,
            list_pcb_traces,
            update_pcb_from_schematic,
        )
        from mcp_server_kicad.schematic import (
            add_label,
            list_schematic_labels,
            remove_label,
        )

        sch, pcb_path = _make_project(tmp_path)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        sig = next(n for n in list_pcb_nets(pcb_path=pcb_path) if n.name == "/SIG")
        add_trace(10, 10, 20, 10, net=sig.number, pcb_path=pcb_path)

        # Rename the net in the schematic: same points, new label text.
        for lbl in list_schematic_labels(schematic_path=sch):
            if lbl.text == "SIG":
                add_label("SIG2", lbl.x, lbl.y, schematic_path=sch)
        remove_label("SIG", schematic_path=sch)

        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.orphaned_tracks == 1
        names = {n.name for n in list_pcb_nets(pcb_path=pcb_path)}
        assert "/SIG" not in names
        assert "/SIG2" in names
        seg = next(t for t in list_pcb_traces(pcb_path=pcb_path) if t.type == "segment")
        assert seg.net == 0

    def test_delete_stale(self, tmp_path):
        from mcp_server_kicad.pcb import (
            list_pcb_footprints,
            place_footprint,
            update_pcb_from_schematic,
        )

        sch, pcb_path = _make_project(tmp_path)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        place_footprint("R99", "ghost", 55, 55, pcb_path=pcb_path)

        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.stale_footprints == ["R99"]
        assert result.stale_removed == []
        assert any(f.reference == "R99" for f in list_pcb_footprints(pcb_path=pcb_path))

        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path, delete_stale=True)
        assert result.stale_removed == ["R99"]
        assert not any(f.reference == "R99" for f in list_pcb_footprints(pcb_path=pcb_path))

    def test_geometry_trio_on_imported_board(self, tmp_path):
        """The geometry reads answer on a board pcbnew itself wrote, with real
        stock footprints supplying the courtyards. On the KiCad 10 runner this
        is the whole point of the slice: the kiutils path refused the file."""
        from mcp_server_kicad.pcb import (
            check_placement,
            get_footprint_bounds,
            update_pcb_from_schematic,
            validate_board,
        )

        sch, pcb_path = _make_project(tmp_path)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)

        # No Edge.Cuts on an imported board, so board_edge_checked is False
        # and every footprint is clean.
        validation = validate_board(pcb_path=pcb_path)
        assert validation.total_footprints == 2
        assert validation.violations == []
        assert validation.status == "ok"

        bounds = get_footprint_bounds("R1", pcb_path=pcb_path)
        assert bounds.layer == "F.Cu"
        assert bounds.courtyard is not None
        assert bounds.courtyard["width"] > 0
        assert bounds.courtyard["height"] > 0

        placement = check_placement(
            "R1", bounds.position["x"], bounds.position["y"], pcb_path=pcb_path
        )
        assert placement.status == "ok"
        assert placement.keepout_violations == []

    @requires_pcbnew
    def test_zone_fill_acceptance(self, tmp_path):
        """Copper zone, keepout and thermal vias spliced by the CST writers,
        then pcbnew's ZONE_FILLER loads, fills and rewrites the board: the
        live acceptance oracle for the zone dialect on both majors."""
        from mcp_server_kicad.pcb import (
            add_copper_zone,
            add_keepout_zone,
            add_thermal_vias,
            fill_zones,
            list_pcb_traces,
            list_pcb_zones,
            update_pcb_from_schematic,
        )

        sch, pcb_path = _make_project(tmp_path)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        add_copper_zone(
            "/GND",
            "F.Cu",
            [{"x": 80, "y": 60}, {"x": 150, "y": 60}, {"x": 150, "y": 100}, {"x": 80, "y": 100}],
            pcb_path=pcb_path,
        )
        add_keepout_zone(
            [{"x": 160, "y": 60}, {"x": 180, "y": 60}, {"x": 180, "y": 80}],
            pcb_path=pcb_path,
        )
        add_thermal_vias("R1", pad_number="2", rows=2, cols=2, pcb_path=pcb_path)

        before = Path(pcb_path).read_bytes()
        result = fill_zones(pcb_path=pcb_path)
        assert result.status == "ok"
        # One copper zone, not two. The keepout added above is a rule area and
        # never fills; the old count was "zones handed to the filler", which
        # included it. This counts zones that actually received copper.
        assert result.zones_filled == 1

        # The point of the splice: pcbnew computed the fill, this server wrote
        # it, and everything outside the zones is the caller's own bytes.
        after = Path(pcb_path).read_bytes()
        assert b"filled_polygon" in after and b"filled_polygon" not in before
        import mcp_server_kicad._cst as _cst_mod

        def _without_zones(raw: bytes) -> bytes:
            tree = _cst_mod.parse(raw)
            root = tree.lists[0]
            return b"".join(_cst_mod.serialize(c) for c in root.lists if c.head != "zone")

        assert _without_zones(after) == _without_zones(before), (
            "filling zones changed something outside a zone"
        )
        # And the format stamp is the caller's, because pcbnew never saved.
        v_before = _cst_mod.parse(before).lists[0].find("version").atoms[1].text
        v_after = _cst_mod.parse(after).lists[0].find("version").atoms[1].text
        assert v_after == v_before

        zones = list_pcb_zones(pcb_path=pcb_path)
        assert any(z.net_name == "/GND" and not z.is_keepout for z in zones)
        assert any(z.is_keepout for z in zones)
        vias = [t for t in list_pcb_traces(pcb_path=pcb_path) if t.type == "via"]
        assert len(vias) == 4

    def test_add_trace_net_binding_survives_reimport(self, tmp_path):
        """Live trap-#1 gate: on the KiCad 10 runner the imported board is
        K10-format, so add_trace/add_via must emit the name-based net
        dialect there; a load-order rebind or a rejected file fails here.
        On KiCad 9 the same test pins the numeric dialect.
        """
        from mcp_server_kicad.pcb import (
            add_trace,
            add_via,
            list_pcb_nets,
            list_pcb_traces,
            run_drc,
            update_pcb_from_schematic,
        )

        sch, pcb_path = _make_project(tmp_path)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        sig = next(n for n in list_pcb_nets(pcb_path=pcb_path) if n.name == "/SIG")
        add_trace(100, 80, 130, 80, net=sig.number, pcb_path=pcb_path)
        add_via(115, 80, net=sig.number, pcb_path=pcb_path)

        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.status == "ok"
        assert result.orphaned_tracks == 0
        sig2 = next(n for n in list_pcb_nets(pcb_path=pcb_path) if n.name == "/SIG")
        seg = next(
            t
            for t in list_pcb_traces(pcb_path=pcb_path)
            if t.type == "segment" and t.start_x == 100 and t.start_y == 80
        )
        assert seg.net == sig2.number
        via = next(t for t in list_pcb_traces(pcb_path=pcb_path) if t.type == "via")
        assert via.net == sig2.number
        drc = run_drc(pcb_path=pcb_path)
        assert drc.violation_count >= 0


class TestWxAppPrelude:
    """Every pcbnew subprocess gets a wxApp before it touches the board.

    pcbnew is a GUI library driven here with no GUI. Anything reaching
    wxStandardPaths::Get() without a wxApp asserts, and the handler that runs is
    the raw C++ one, because wxPython installs its own through wxApp. That is a
    modal "wxWidgets Debug Alert" on Windows, on the user's desktop, which the
    subprocess has nobody to dismiss, so the call blocks to its timeout; on
    macOS it kills the process.

    Seen first as an intermittent macOS CI failure on the --delete-stale run,
    twice, both cleared by re-running, which is exactly what made it read as
    flake. It was not: the process exits non-zero and the stderr carries no
    Python traceback, because nothing Python raised.

    A scan, not an execution. Reproducing the assert means provoking a modal
    dialog on whatever machine runs the suite, which is not a thing a test
    should do to the person running it.
    """

    def test_every_inline_pcbnew_script_creates_the_app_first(self):
        """Any new `-c "import pcbnew..."` has to carry the prelude too."""
        import mcp_server_kicad

        src_dir = Path(mcp_server_kicad.__file__).parent
        offenders = []
        for path in sorted(src_dir.glob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if '"import pcbnew; "' not in line:
                    continue
                if "wx_app_prelude()" not in line:
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        assert offenders == [], (
            "these run pcbnew with no wxApp, so a path lookup raises a modal"
            " dialog the subprocess cannot dismiss. Prefix the script with"
            " _freerouting.wx_app_prelude():\n  " + "\n  ".join(offenders)
        )

    def test_the_prelude_is_a_valid_prefix(self):
        """It is concatenated onto a script, so it must end in a separator."""
        from mcp_server_kicad._freerouting import wx_app_prelude

        prelude = wx_app_prelude()
        assert prelude.endswith("; "), prelude
        assert "_ensure_wx_app()" in prelude
        compile(prelude + "import pcbnew", "<prelude>", "exec")

    def test_the_module_the_prelude_imports_needs_only_the_stdlib(self):
        """KiCad's interpreter has no site-packages of ours to import from."""
        import ast

        import mcp_server_kicad

        src = (Path(mcp_server_kicad.__file__).parent / "_netlist_import.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = []
        for node in top_level:
            if isinstance(node, ast.Import):
                names += [a.name.split(".")[0] for a in node.names]
            elif node.module:
                names.append(node.module.split(".")[0])
        assert set(names) <= {"__future__", "argparse", "json", "os", "sys", "xml"}, names

    def test_a_display_less_host_never_reaches_wx(self, monkeypatch):
        """The check that broke Linux, pinned.

        The first version left this to a try/except on the assumption that
        wx would raise something catchable when it could not open a display.
        It does not: wxPython exits the process with "Unable to access the X
        Display, is $DISPLAY set properly?", so nothing catches anything and
        eight passing E2E tests went red on the Linux runner. The guard has to
        run before wx is imported at all, which is what this asserts.
        """
        import ast

        import mcp_server_kicad

        src = (Path(mcp_server_kicad.__file__).parent / "_netlist_import.py").read_text(
            encoding="utf-8"
        )
        fn = next(
            n
            for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef) and n.name == "_ensure_wx_app"
        )
        body = [n for n in fn.body if not isinstance(n, ast.Expr)]
        first = body[0]
        assert isinstance(first, ast.If), "the display check must come first"
        assert any(isinstance(x, ast.Return) for x in first.body), (
            "the display check must return, not fall through to importing wx"
        )
        names = {n.id for n in ast.walk(first) if isinstance(n, ast.Name)}
        assert {"sys", "os"} <= names, "it should read sys.platform and the display env"
        # wx must not be imported before that guard has had its say.
        imports_before = [
            n
            for n in ast.walk(ast.Module(body=body[: body.index(first)], type_ignores=[]))
            if isinstance(n, (ast.Import, ast.ImportFrom))
        ]
        assert imports_before == [], imports_before
