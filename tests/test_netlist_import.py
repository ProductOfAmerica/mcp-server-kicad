"""Tests for _netlist_import: pure functions here, real-pcbnew E2E below.

The pure-function tests run everywhere (importing the module in the venv
is itself the proof that pcbnew stays deferred). The E2E class needs both
kicad-cli and KiCad's Python with pcbnew.
"""

from __future__ import annotations

from xml.etree.ElementTree import ParseError

import pytest

from mcp_server_kicad import _netlist_import as ni
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
