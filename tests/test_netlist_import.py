"""Tests for _netlist_import: pure functions here, real-pcbnew E2E below.

The pure-function tests run everywhere (importing the module in the venv
is itself the proof that pcbnew stays deferred). The E2E class needs both
kicad-cli and KiCad's Python with pcbnew.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest
from conftest import HAS_KICAD_CLI
from kiutils.board import Board

import mcp_server_kicad._cst as _cst
from mcp_server_kicad import _netlist_import as ni
from mcp_server_kicad import pcb
from mcp_server_kicad._freerouting import find_pcbnew_python
from mcp_server_kicad._shared import _kicad_root, _resolve_system_lib, _run_cli
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

# Real kicad-cli output (KiCad 10.0.6) for a three-unit SN74LVC2G17 on a child
# sheet, trimmed to the elements parse_netlist reads: one <comp>, three
# space-separated KIIDs (the exporter lists the extra units first and the unit
# it iterated last, so this one reads unit 2, unit 1, unit 3), and a
# single-unit neighbour on the same sheet.
MULTI_UNIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<export version="E">
  <components>
    <comp ref="U1">
      <value>SN74LVC2G17</value>
      <footprint>orb_weaver_smd:DRY6</footprint>
      <sheetpath names="/gpio/" tstamps="/5d835380-29a3-52cb-8ee2-2cc964a09c22/"/>
      <tstamps>f6b885c6-bea8-4f63-a8bf-ed54c18ce440 e310c8b3-8386-4c71-bde8-59dd025d7495 7b072c8a-4743-483b-985c-6774fd37fcc8</tstamps>
    </comp>
    <comp ref="R1">
      <value>10K</value>
      <footprint>Resistor_SMD:R_0603_1608Metric</footprint>
      <sheetpath names="/gpio/" tstamps="/5d835380-29a3-52cb-8ee2-2cc964a09c22/"/>
      <tstamps>0eab7050-ae9a-4b12-9155-20dceb5716cb</tstamps>
    </comp>
  </components>
  <nets>
    <net code="1" name="/gpio/IN">
      <node ref="U1" pin="1"/>
      <node ref="R1" pin="1"/>
    </net>
  </nets>
</export>
"""  # noqa: E501 - the <tstamps> line is kicad-cli's, kept as it writes it

# Real kicad-cli output (KiCad 10.0.6) for a stock Device:R with one user field,
# a second one with DNP set and "In BOM" cleared on a child sheet, and a third
# with "On board" cleared, trimmed to what parse_netlist reads. The exporter
# lists Footprint, Datasheet and Description in <fields> whether or not they
# are empty, repeats the user fields as <property> rows, and writes the three
# flags as value-less <property> markers only when set.
FIELDS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<export version="E">
  <components>
    <comp ref="R1">
      <value>10K</value>
      <footprint>Resistor_SMD:R_0603_1608Metric</footprint>
      <fields>
        <field name="MPN">ERJ-3EKF1002V</field>
        <field name="Footprint">Resistor_SMD:R_0603_1608Metric</field>
        <field name="Datasheet" />
        <field name="Description">Resistor</field>
      </fields>
      <libsource lib="Device" part="R" description="Resistor" />
      <property name="MPN" value="ERJ-3EKF1002V" />
      <property name="Sheetname" value="probe" />
      <property name="Sheetfile" value="probe.kicad_sch" />
      <property name="ki_keywords" value="R res resistor" />
      <property name="ki_fp_filters" value="R_*" />
      <sheetpath names="/" tstamps="/" />
      <tstamps>a84f1b87-4e8e-4688-963e-a7807205b4fb</tstamps>
    </comp>
    <comp ref="R2">
      <value>4.7K</value>
      <footprint>Resistor_SMD:R_0805_2012Metric</footprint>
      <fields>
        <field name="Footprint">Resistor_SMD:R_0805_2012Metric</field>
        <field name="Datasheet" />
        <field name="Description" />
      </fields>
      <property name="Sheetname" value="sub" />
      <property name="Sheetfile" value="sub.kicad_sch" />
      <property name="exclude_from_bom" />
      <property name="dnp" />
      <sheetpath names="/sub/" tstamps="/1111-2222/" />
      <tstamps>8fa8ad0b-5203-467d-9a6c-ee04e1399434</tstamps>
    </comp>
    <comp ref="C1">
      <value>100nF</value>
      <footprint>Capacitor_SMD:C_0402_1005Metric</footprint>
      <property name="exclude_from_board" />
      <sheetpath names="/" tstamps="/" />
      <tstamps>cccc-dddd</tstamps>
    </comp>
  </components>
  <nets>
    <net code="1" name="/SIG">
      <node ref="R1" pin="1"/>
      <node ref="R2" pin="1"/>
      <node ref="C1" pin="1"/>
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

    def test_multi_unit_path_names_the_first_unit(self, tmp_path):
        """A multi-unit symbol lists every unit in <tstamps>; the path takes the first.

        The footprint's path names one symbol, and KiCad's own update takes
        the first listed unit (BOARD_NETLIST_UPDATER::updateFootprintParameters
        pushes GetKIIDs().front()). Writing the whole list produced a path token
        with spaces in it, which pcbnew cannot parse: it substituted a fresh
        random KIID on every load, so the footprint was linked to no unit.
        """
        p = tmp_path / "multi.xml"
        p.write_text(MULTI_UNIT_XML)
        components, _ = ni.parse_netlist(str(p))
        assert [c["ref"] for c in components] == ["U1", "R1"]
        u1, r1 = components
        sheet = "/5d835380-29a3-52cb-8ee2-2cc964a09c22"
        assert u1["path"] == sheet + "/f6b885c6-bea8-4f63-a8bf-ed54c18ce440"
        assert " " not in u1["path"]
        assert r1["path"] == sheet + "/0eab7050-ae9a-4b12-9155-20dceb5716cb"

    def test_nets_ignore_code(self, netlist_file):
        _, nets = ni.parse_netlist(netlist_file)
        assert [n["name"] for n in nets] == ["/SIG", "GND"]
        assert nets[0]["nodes"] == [("R1", "1"), ("J1", "2")]

    def test_malformed_xml_raises(self, tmp_path):
        p = tmp_path / "bad.xml"
        p.write_text("<export><unclosed>")
        with pytest.raises(ParseError):
            ni.parse_netlist(str(p))

    def test_fields_flags_and_sheet_linkage(self, tmp_path):
        """What KiCad's own update copies besides Reference, Value and the
        library id: the fields (Reference, Value and Footprint excluded, the
        empty Datasheet kept), the DNP / BOM / board flags, and the sheet the
        symbol sits on."""
        p = tmp_path / "fields.xml"
        p.write_text(FIELDS_XML)
        components, _ = ni.parse_netlist(str(p))
        r1, r2, c1 = components
        assert r1["fields"] == {"MPN": "ERJ-3EKF1002V", "Datasheet": "", "Description": "Resistor"}
        assert (r1["dnp"], r1["exclude_from_bom"], r1["exclude_from_board"]) == (False,) * 3
        assert (r1["sheetname"], r1["sheetfile"]) == ("/", "probe.kicad_sch")
        assert r2["fields"] == {"Datasheet": "", "Description": ""}
        assert (r2["dnp"], r2["exclude_from_bom"], r2["exclude_from_board"]) == (True, True, False)
        assert (r2["sheetname"], r2["sheetfile"]) == ("/sub/", "sub.kicad_sch")
        assert c1["exclude_from_board"] is True
        assert c1["fields"] == {}

    def test_a_netlist_without_fields_reads_as_empty(self, netlist_file):
        components, _ = ni.parse_netlist(netlist_file)
        r1, j1 = components
        assert r1["fields"] == {} and r1["sheetfile"] == "" and r1["sheetname"] == "/"
        assert (r1["dnp"], r1["exclude_from_bom"], r1["exclude_from_board"]) == (False,) * 3
        assert j1["sheetname"] == "/sub/"


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


def _table(rows: list[str]) -> bytes:
    return ("(fp_lib_table\n\t(version 7)\n" + "".join(f"\t{r}\n" for r in rows) + ")\n").encode()


def _row(name: str, uri: str, kind: str = "KiCad", extra: str = "") -> str:
    return f'(lib (name "{name}") (type "{kind}") (uri "{uri}") (options "") (descr ""){extra})'


class TestFpLibTables:
    """The project's and the user's fp-lib-table, read the way KiCad reads them."""

    def test_project_rows_expand_kiprjmod_and_skip_what_cannot_be_served(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "libs" / "Mine.pretty").mkdir(parents=True)
        (tmp_path / "Other.pretty").mkdir()
        monkeypatch.setenv("MY_LIBS", str(tmp_path / "libs"))
        monkeypatch.delenv("NOWHERE", raising=False)
        table = tmp_path / "fp-lib-table"
        table.write_bytes(
            _table(
                [
                    _row("Mine", "${KIPRJMOD}/libs/Mine.pretty"),
                    _row("ViaEnv", "${MY_LIBS}/Mine.pretty"),
                    _row("Off", "${KIPRJMOD}/Other.pretty", extra=" (disabled)"),
                    _row("Eagle", "${KIPRJMOD}/Other.pretty", kind="Eagle"),
                    _row("Lost", "${NOWHERE}/x.pretty"),
                    _row("Gone", "${KIPRJMOD}/Gone.pretty"),
                ]
            )
        )
        mine = str(tmp_path / "libs" / "Mine.pretty")
        assert pcb._read_fp_lib_table(table, tmp_path) == {"Mine": mine, "ViaEnv": mine}

    def test_a_malformed_or_missing_table_answers_empty(self, tmp_path):
        assert pcb._read_fp_lib_table(tmp_path / "fp-lib-table", tmp_path) == {}
        bad = tmp_path / "bad"
        bad.write_bytes(b"(fp_lib_table (lib (name")
        assert pcb._read_fp_lib_table(bad, tmp_path) == {}
        other = tmp_path / "sym"
        other.write_bytes(b'(sym_lib_table (version 7) (lib (name "X") (uri "x")))')
        assert pcb._read_fp_lib_table(other, tmp_path) == {}

    def test_the_global_table_is_followed_into_the_stock_table_and_shadowed(
        self, tmp_path, monkeypatch
    ):
        """KiCad 10's global table is one (type "Table") row pointing at the
        stock template table, whose rows use ${KICAD10_FOOTPRINT_DIR}; the
        project's own row for the same nickname wins, and a nickname only the
        global table knows still resolves."""
        stock = tmp_path / "stock"
        (stock / "Resistor_SMD.pretty").mkdir(parents=True)
        home = tmp_path / "home"
        (home / "Personal.pretty").mkdir(parents=True)
        proj = tmp_path / "proj"
        (proj / "Resistor_SMD.pretty").mkdir(parents=True)
        (proj / "fp-lib-table").write_bytes(
            _table([_row("Resistor_SMD", "${KIPRJMOD}/Resistor_SMD.pretty")])
        )
        template = tmp_path / "template"
        template.mkdir()
        (template / "fp-lib-table").write_bytes(
            _table([_row("Resistor_SMD", "${KICAD9_FOOTPRINT_DIR}/Resistor_SMD.pretty")])
        )
        config = tmp_path / "config"
        (config / "9.0").mkdir(parents=True)
        (config / "9.0" / "fp-lib-table").write_bytes(
            _table(
                [
                    _row("KiCad", "${KICAD9_TEMPLATE_DIR}/fp-lib-table", kind="Table"),
                    _row("Personal", "${HOME_LIBS}/Personal.pretty"),
                ]
            )
        )
        monkeypatch.setenv("KICAD9_FOOTPRINT_DIR", str(stock))
        monkeypatch.setenv("KICAD9_TEMPLATE_DIR", str(template))
        monkeypatch.setenv("HOME_LIBS", str(home))
        monkeypatch.setenv("KICAD_CONFIG_HOME", str(config))
        monkeypatch.setattr(pcb, "_kicad_cli_major", lambda: 9)

        project_table, global_table = pcb._fp_lib_tables(proj)
        assert project_table == {"Resistor_SMD": str(proj / "Resistor_SMD.pretty")}
        assert global_table == {
            "Resistor_SMD": str(stock / "Resistor_SMD.pretty"),
            "Personal": str(home / "Personal.pretty"),
        }
        board = str(proj / "b.kicad_pcb")
        assert pcb._resolve_pretty_dir("Resistor_SMD", board) == str(proj / "Resistor_SMD.pretty")
        assert pcb._resolve_pretty_dir("Personal", board) == str(home / "Personal.pretty")
        with pytest.raises(Exception, match="3 fp-lib-table row"):
            pcb._resolve_pretty_dir("Nope", board)

    def test_no_kicad_cli_means_no_global_table(self, monkeypatch):
        monkeypatch.setattr(pcb, "_kicad_cli_major", lambda: None)
        assert pcb._global_fp_lib_table() is None

    def test_the_project_dir_is_the_pro_file_s_or_the_first_with_a_table(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "fp-lib-table").write_bytes(_table([]))
        assert (
            pcb._project_dir(
                str(tmp_path / "a" / "x.kicad_pcb"), str(tmp_path / "b" / "x.kicad_sch")
            )
            == tmp_path / "b"
        )
        assert pcb._project_dir(str(tmp_path / "a" / "x.kicad_pcb")) == tmp_path / "a"
        assert pcb._project_dir("", "", str(tmp_path / "b" / "p.kicad_pro")) == tmp_path / "b"


_FP = (
    b'(footprint "Resistor_SMD:R_0603_1608Metric"\n\t\t(layer "F.Cu")\n\t\t(uuid "a")'
    b'\n\t\t(at 10 10 90)\n\t\t(path "/x")\n\t\t(descr "d")'
    b'\n\t\t(property "Reference" "R1"\n\t\t\t(at 0 -1.43 0)\n\t\t\t(layer "F.SilkS")'
    b'\n\t\t\t(uuid "b")\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)'
    b"\n\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)"
    b'\n\t\t(property "Description" "Resistor SMD 0603"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")'
    b'\n\t\t\t(hide yes)\n\t\t\t(uuid "c")\n\t\t\t(effects\n\t\t\t\t(font'
    b"\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)"
    b'\n\t\t(attr smd)\n\t\t(pad "1" smd roundrect\n\t\t\t(at -0.825 0 90)\n\t\t\t(size 0.8 0.95)'
    b'\n\t\t\t(layers "F.Cu" "F.Paste" "F.Mask")\n\t\t\t(uuid "e")\n\t\t)\n\t)'
)


class TestFootprintSync:
    """Fields, attributes and sheet linkage onto one footprint node, as bytes."""

    def _fp(self):
        return _cst.parse(_FP).lists[0]

    def test_a_new_field_is_hidden_on_fab_and_cancels_the_rotation(self):
        fp = self._fp()
        comp = {
            "fields": {"MPN": "ERJ", "Description": "Resistor"},
            "sheetname": "/",
            "sheetfile": "e.kicad_sch",
            "dnp": True,
            "exclude_from_bom": True,
        }
        assert pcb._sync_fp_from_component(fp, comp)
        out = _cst.serialize(fp)
        assert (
            b'(property "MPN" "ERJ"\n\t\t\t(at 0 0 270)\n\t\t\t(unlocked yes)'
            b'\n\t\t\t(layer "F.Fab")\n\t\t\t(hide yes)'
        ) in out, "the footprint sits at 90, so the field reads upright at 270"
        assert b'(property "Description" "Resistor"\n\t\t\t(at 0 0 0)' in out, (
            "an existing field changes its text in place and keeps its position"
        )
        assert b'(path "/x")\n\t\t(sheetname "/")\n\t\t(sheetfile "e.kicad_sch")\n\t\t(descr' in out
        assert b"(attr smd exclude_from_bom dnp)" in out
        assert out.count(b"(uuid ") == 5, "the new field carries a uuid of its own"
        assert not pcb._sync_fp_from_component(fp, comp), "a second pass changes nothing"
        cleared = dict(comp, dnp=False, exclude_from_bom=False)
        assert pcb._sync_fp_from_component(fp, cleared)
        assert b"(attr smd)" in _cst.serialize(fp)
        assert pcb._sync_fp_from_component(fp, dict(cleared, fields={})) is False
        assert b'(property "MPN" "ERJ"' in _cst.serialize(fp), "a field that left the symbol stays"

    def test_board_owned_attributes_are_kept_in_kicad_s_order(self):
        fp = self._fp()
        attr = fp.find("attr")
        node = _cst.parse(b"(attr through_hole exclude_from_pos_files)").lists[0]
        node.sep = attr.sep
        fp.children[fp.children.index(attr)] = node
        assert pcb._sync_fp_attributes(fp, dnp=True, exclude_from_bom=False)
        assert b"(attr through_hole exclude_from_pos_files dnp)" in _cst.serialize(fp)
        assert pcb._sync_fp_attributes(fp, dnp=True, exclude_from_bom=True)
        assert b"(attr through_hole exclude_from_pos_files exclude_from_bom dnp)" in _cst.serialize(
            fp
        )

    def test_a_back_side_footprint_gets_fields_on_b_fab(self):
        fp = self._fp()
        fp.find("layer").atoms[1].set_text("B.Cu")
        assert pcb._sync_fp_fields(fp, {"MPN": "x"})
        assert (
            b'(property "MPN" "x"\n\t\t\t(at 0 0 270)\n\t\t\t(unlocked yes)\n\t\t\t(layer "B.Fab")'
            in (_cst.serialize(fp))
        )

    def test_an_attribute_less_footprint_gains_one_only_when_flagged(self):
        fp = self._fp()
        fp.remove_child(fp.find("attr"))
        assert not pcb._sync_fp_attributes(fp, False, False)
        assert pcb._sync_fp_attributes(fp, True, False)
        assert b'(path "/x")\n\t\t(attr dnp)' in _cst.serialize(fp)
        assert pcb._sync_fp_attributes(fp, False, False)
        assert b"(attr" not in _cst.serialize(fp)


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


def _set_symbol_flags(sch: str, reference: str, **flags: bool) -> None:
    """Tick or untick a placed symbol's dnp / in_bom / on_board boxes in the file.

    No tool sets these yet, and a test editing the schematic's own bytes is
    exactly what a user ticking the box in eeschema produces.
    """
    path = Path(sch)
    tree = _cst.parse(path.read_bytes())
    root = tree.lists[0]
    for sym in root.find_all("symbol"):
        if any(
            p.atoms[1].text == "Reference" and p.atoms[2].text == reference
            for p in sym.find_all("property")
        ):
            for key, value in flags.items():
                sym.find(key).atoms[1].set_text("yes" if value else "no")
    path.write_bytes(_cst.serialize(tree))


def _position_refs(pcb_path: str, *flags: str) -> set[str]:
    """References in KiCad's own position export: the DNP oracle."""
    out = Path(pcb_path).with_name(f"pos{len(flags)}.csv")
    _run_cli(["pcb", "export", "pos", "--format", "csv", "--output", str(out), *flags, pcb_path])
    return {line.split(",")[0].strip('"') for line in out.read_text().splitlines()[1:]}


@requires_e2e
class TestUpdatePcbFieldsE2E:
    def test_fields_flags_and_sheet_linkage_follow_the_symbol(self, tmp_path):
        from mcp_server_kicad.pcb import update_pcb_from_schematic
        from mcp_server_kicad.schematic import set_component_property

        sch, pcb_path = _make_project(tmp_path)
        set_component_property("R1", "MPN", "ERJ-3EKF1002V", schematic_path=sch)
        _set_symbol_flags(sch, "R2", dnp=True, in_bom=False)
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.status == "ok" and sorted(result.added) == ["R1", "R2"]
        assert result.excluded_from_board == [] and result.fields_updated == []

        board = Board.from_file(pcb_path)  # kiutils reads the result back as an oracle
        by_ref = {f.properties.get("Reference"): f for f in board.footprints}
        assert by_ref["R1"].properties["MPN"] == "ERJ-3EKF1002V"
        assert by_ref["R1"].properties["Datasheet"] == ""
        assert "MPN" not in by_ref["R2"].properties
        assert by_ref["R2"].attributes.excludeFromBom is True
        assert by_ref["R1"].attributes.excludeFromBom is False
        raw = Path(pcb_path).read_bytes()
        assert b"(attr smd exclude_from_bom dnp)" in raw
        assert raw.count(b'(sheetname "/")') == 2 and raw.count(b'(sheetfile "e2e.kicad_sch")') == 2
        assert b'(property "MPN" "ERJ-3EKF1002V"\n\t\t\t(at 0 0 0)\n\t\t\t(unlocked yes)' in raw
        # DNP through KiCad itself: the position export drops R2 only when asked to.
        assert {"R1", "R2"} <= _position_refs(pcb_path)
        assert "R2" not in _position_refs(pcb_path, "--exclude-dnp")
        assert "R1" in _position_refs(pcb_path, "--exclude-dnp")

        once = Path(pcb_path).read_bytes()
        again = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert Path(pcb_path).read_bytes() == once, "nothing changed, so nothing may change"
        assert again.fields_updated == []

    def test_a_field_edit_and_a_cleared_flag_reach_an_existing_footprint(self, tmp_path):
        from mcp_server_kicad.pcb import (
            list_pcb_footprints,
            move_footprint,
            update_pcb_from_schematic,
        )
        from mcp_server_kicad.schematic import set_component_property

        sch, pcb_path = _make_project(tmp_path)
        set_component_property("R1", "MPN", "ERJ-3EKF1002V", schematic_path=sch)
        _set_symbol_flags(sch, "R2", dnp=True)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        move_footprint("R1", 42, 24, pcb_path=pcb_path)

        set_component_property("R1", "MPN", "RC0603FR-0710KL", schematic_path=sch)
        _set_symbol_flags(sch, "R2", dnp=False)
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert sorted(result.fields_updated) == ["R1", "R2"]
        assert result.value_updated == [] and result.added == []
        raw = Path(pcb_path).read_bytes()
        assert b'(property "MPN" "RC0603FR-0710KL"' in raw
        # The flag leaves the footprint, and nothing else about (attr ...)
        # moves. Not `b"dnp" not in raw`: the board's own setup carries
        # KiCad's sketchdnponfab/crossoutdnponfab/hidednponfab keys.
        assert b"(attr smd dnp)" not in raw and raw.count(b"(attr smd)") == 2
        assert "R2" in _position_refs(pcb_path, "--exclude-dnp"), "KiCad itself sees it cleared"
        r1 = next(f for f in list_pcb_footprints(pcb_path=pcb_path) if f.reference == "R1")
        assert (r1.x, r1.y) == (42, 24), "the user's placement is not the schematic's to move"

    def test_an_off_board_symbol_is_never_placed_and_its_footprint_goes_stale(self, tmp_path):
        from mcp_server_kicad.pcb import list_pcb_footprints, update_pcb_from_schematic

        sch, pcb_path = _make_project(tmp_path)
        update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        _set_symbol_flags(sch, "R2", on_board=False)
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.excluded_from_board == ["R2"]
        assert result.stale_footprints == ["R2"] and result.stale_removed == []
        assert not any("R2" in w for w in result.warnings), result.warnings
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path, delete_stale=True)
        assert result.stale_removed == ["R2"]
        assert [f.reference for f in list_pcb_footprints(pcb_path=pcb_path)] == ["R1"]

    def test_the_project_fp_lib_table_resolves_a_nickname_the_search_cannot(self, tmp_path):
        from mcp_server_kicad.pcb import (
            list_pcb_footprints,
            place_footprint,
            update_pcb_from_schematic,
        )
        from mcp_server_kicad.schematic import set_component_property

        sch, pcb_path = _make_project(tmp_path)
        stock = pcb._resolve_pretty_dir("Resistor_SMD")
        mine = tmp_path / "libs" / "Mine.pretty"
        mine.mkdir(parents=True)
        shutil.copy(Path(stock) / "R_0603_1608Metric.kicad_mod", mine)
        set_component_property("R1", "Footprint", "Mine:R_0603_1608Metric", schematic_path=sch)

        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.skipped == [{"ref": "R1", "reason": "footprint_lib_not_found:Mine"}], (
            "libs/Mine.pretty is not beside the board, so nothing finds it without the table"
        )
        (tmp_path / "fp-lib-table").write_bytes(
            _table([_row("Mine", "${KIPRJMOD}/libs/Mine.pretty")])
        )
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.skipped == [] and result.added == ["R1"]
        r1 = next(f for f in list_pcb_footprints(pcb_path=pcb_path) if f.reference == "R1")
        assert r1.lib_id == "Mine:R_0603_1608Metric"
        # place_footprint reads the same table, so a hand-placed part agrees.
        place_footprint(
            "R9", "1k", 60, 60, library="Mine", footprint="R_0603_1608Metric", pcb_path=pcb_path
        )
        r9 = next(f for f in list_pcb_footprints(pcb_path=pcb_path) if f.reference == "R9")
        assert r9.lib_id == "Mine:R_0603_1608Metric"


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
