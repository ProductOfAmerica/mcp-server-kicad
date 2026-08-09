"""Tests for _netlist_import: pure functions here, real-pcbnew E2E below.

The pure-function tests run everywhere (importing the module in the venv
is itself the proof that pcbnew stays deferred). The E2E class needs both
kicad-cli and KiCad's Python with pcbnew.
"""

from __future__ import annotations

import re
import subprocess
from xml.etree.ElementTree import ParseError

import pytest
from conftest import HAS_KICAD_CLI

from mcp_server_kicad import _netlist_import as ni
from mcp_server_kicad._freerouting import find_pcbnew_python
from mcp_server_kicad._shared import _find_kicad_cli, _kicad_root, _resolve_system_lib
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


HAS_E2E_ENV = (
    HAS_KICAD_CLI
    and find_pcbnew_python()[0] is not None
    and _resolve_system_lib("Device") is not None
    and _stock_footprints_available()
)
requires_e2e = pytest.mark.skipif(
    not HAS_E2E_ENV, reason="kicad-cli + pcbnew + stock KiCad libraries required"
)


def _kicad_major() -> int:
    """Major version of the installed kicad-cli, or 0 when unknown."""
    cli = _find_kicad_cli()
    if cli is None:
        return 0
    try:
        out = subprocess.run([cli, "version"], capture_output=True, text=True, timeout=30).stdout
        match = re.match(r"(\d+)", out.strip())
        return int(match.group(1)) if match else 0
    except Exception:
        return 0


KICAD_MAJOR = _kicad_major() if HAS_E2E_ENV else 0

# The importer itself works under pcbnew 10 (status ok, pads bound); the
# read-back through the kiutils tools is what fails, because kiutils cannot
# parse the KiCad 10-format boards pcbnew 10's SaveBoard writes. strict=True
# turns these into hard failures the moment a KiCad 10-capable parser lands.
xfail_kicad10_read_gap = pytest.mark.xfail(
    KICAD_MAJOR >= 10,
    reason="#11: kiutils cannot read KiCad 10-format boards; "
    "remove this marker in the #9 fork-adoption PR",
    strict=True,
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
@xfail_kicad10_read_gap
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
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
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
