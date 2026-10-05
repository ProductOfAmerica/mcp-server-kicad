"""Tests for _netlist_import: pure functions here, real-pcbnew E2E below.

The pure-function tests run everywhere (importing the module in the venv
is itself the proof that pcbnew stays deferred). The E2E class needs both
kicad-cli and KiCad's Python with pcbnew.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from xml.etree.ElementTree import ParseError

import pytest
from conftest import HAS_KICAD_CLI
from kiutils.board import Board

import mcp_server_kicad._cst as _cst
from mcp_server_kicad import _netlist_import as ni
from mcp_server_kicad import pcb
from mcp_server_kicad._freerouting import find_pcbnew_python
from mcp_server_kicad._shared import (
    _find_kicad_cli,
    _kicad_cli_major,
    _kicad_root,
    _resolve_system_lib,
    _run_cli,
)
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
        <field name="Component Class">Power</field>
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
        library id: the fields (Reference, Value, Footprint and Component Class
        excluded, the empty Datasheet kept), the footprint filters, the DNP /
        BOM / board flags, and the sheet the symbol sits on."""
        p = tmp_path / "fields.xml"
        p.write_text(FIELDS_XML)
        components, _ = ni.parse_netlist(str(p))
        r1, r2, c1 = components
        assert r1["fields"] == {"MPN": "ERJ-3EKF1002V", "Datasheet": "", "Description": "Resistor"}
        assert r1["fp_filters"] == "R_*" and r2["fp_filters"] == ""
        assert r1["exclude_from_pos_files"] is None, "no <design><tool>: not a KiCad 10 netlist"
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
        assert r1["fp_filters"] == "" and r1["exclude_from_pos_files"] is None
        assert j1["sheetname"] == "/sub/"

    def test_kicad_10_says_whether_a_symbol_is_in_the_position_files(self, tmp_path):
        """KiCad 10's exporter marks a symbol excluded from position files and
        its update sets and clears the footprint's flag from the marker. KiCad
        9 writes no marker, so a KiCad 9 netlist answers None and the flag
        stays the board's own."""

        def netlist(tool: str) -> list[dict]:
            p = tmp_path / "pos.xml"
            p.write_text(
                f'<export version="E"><design><tool>Eeschema {tool}</tool></design>'
                '<components><comp ref="H1"><value>MountingHole</value>'
                '<property name="exclude_from_pos_files"/></comp>'
                '<comp ref="R1"><value>10K</value></comp></components>'
                "<nets/></export>"
            )
            return ni.parse_netlist(str(p))[0]

        h1, r1 = netlist("10.0.6")
        assert (h1["exclude_from_pos_files"], r1["exclude_from_pos_files"]) == (True, False)
        h1, r1 = netlist("9.0.9")
        assert h1["exclude_from_pos_files"] is None and r1["exclude_from_pos_files"] is None


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
        monkeypatch.setenv("KICAD_DOCUMENTS_HOME", str(tmp_path / "docs"))
        # The install's stock footprints, where Personal also exists: the
        # global row wins over stock, for the import and place_footprint alike.
        root = tmp_path / "root"
        (root / "share/kicad/footprints/Personal.pretty").mkdir(parents=True)
        (root / "share/kicad/footprints/Stock.pretty").mkdir()
        monkeypatch.setattr(pcb, "_kicad_root", lambda: root)

        board = str(proj / "b.kicad_pcb")
        resolver = pcb._FpLibResolver(proj, pcb._fp_search_dirs(board))
        assert resolver.project_table == {"Resistor_SMD": str(proj / "Resistor_SMD.pretty")}
        assert resolver.global_table == {
            "Resistor_SMD": str(stock / "Resistor_SMD.pretty"),
            "Personal": str(home / "Personal.pretty"),
        }
        assert resolver.resolve("Resistor_SMD") == str(proj / "Resistor_SMD.pretty")
        assert resolver.resolve("Personal") == str(home / "Personal.pretty")
        assert resolver.resolve("Stock") == str(root / "share/kicad/footprints/Stock.pretty")
        assert pcb._resolve_pretty_dir("Resistor_SMD", board) == str(proj / "Resistor_SMD.pretty")
        assert pcb._resolve_pretty_dir("Personal", board) == str(home / "Personal.pretty")
        assert pcb._resolve_pretty_dir("Stock", board) == resolver.resolve("Stock")
        with pytest.raises(Exception, match="3 fp-lib-table row"):
            pcb._resolve_pretty_dir("Nope", board)

    def test_the_global_table_is_read_only_after_the_local_lookups_miss(
        self, tmp_path, monkeypatch
    ):
        """A project library or one beside the board answers without the
        global table being read at all (about 15 ms for the stock table's 155
        nested rows), and a miss reads it once per resolver."""
        proj = tmp_path / "proj"
        (proj / "Mine.pretty").mkdir(parents=True)
        (proj / "Beside.pretty").mkdir()
        (proj / "fp-lib-table").write_bytes(_table([_row("Mine", "${KIPRJMOD}/Mine.pretty")]))
        reads: list[str] = []

        def counted():
            reads.append("global")
            return None

        monkeypatch.setattr(pcb, "_global_fp_lib_table", counted)
        monkeypatch.setattr(pcb, "_kicad_cli_major", lambda: None)
        resolver = pcb._FpLibResolver(proj, pcb._fp_search_dirs(str(proj / "b.kicad_pcb")))
        assert resolver.resolve("Mine") == str(proj / "Mine.pretty")
        assert resolver.resolve("Beside") == str(proj / "Beside.pretty")
        assert reads == []
        assert resolver.resolve("Nope") is None and resolver.resolve("Nope2") is None
        assert reads == ["global"]

    def test_configure_paths_and_pcm_libraries_resolve(self, tmp_path, monkeypatch):
        """Two kinds of global row KiCad resolves and the table file alone
        cannot: a ${VAR} set in Preferences > Configure Paths (kicad_common.json,
        the OS environment winning), and the libraries the Plugin and Content
        Manager installed, which KiCad adds while loading the table from
        ${KICAD<N>_3RD_PARTY}/footprints/<package>/<lib>.pretty, a root it
        computes at run time, as <prefix><lib>, or <prefix><lib>_1 when the
        table already has that nickname."""
        config = tmp_path / "config" / "9.0"
        config.mkdir(parents=True)
        mine = tmp_path / "mine" / "Mine.pretty"
        mine.mkdir(parents=True)
        override = tmp_path / "override" / "Over.pretty"
        override.mkdir(parents=True)
        docs = tmp_path / "docs"
        third_party = docs / pcb._KICAD_PATH_STR / "9.0" / "3rdparty"
        pkg = third_party / "footprints" / "com_github_x_pkg"
        (pkg / "Pkg.pretty").mkdir(parents=True)
        (pkg / "Also.pretty").mkdir()
        (pkg / "notes.pretty").write_text("a file, not a library")
        (third_party / "footprints" / "Loose.pretty").mkdir()
        own = tmp_path / "own" / "Pkg.pretty"
        own.mkdir(parents=True)
        configured = {
            "MYLIBS": str(tmp_path / "mine"),
            "KICAD9_FOOTPRINT_DIR": str(override.parent),
        }
        (config / "kicad_common.json").write_text(json.dumps({"environment": {"vars": configured}}))
        (config / "fp-lib-table").write_bytes(
            _table(
                [
                    _row("Mine", "${MYLIBS}/Mine.pretty"),
                    _row("Over", "${KICAD9_FOOTPRINT_DIR}/Over.pretty"),
                    _row("PCM_Pkg", "${OWN}/Pkg.pretty"),
                ]
            )
        )
        monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("KICAD_DOCUMENTS_HOME", str(docs))
        monkeypatch.setenv("OWN", str(own.parent))
        for name in ("MYLIBS", "KICAD9_FOOTPRINT_DIR", "KICAD9_3RD_PARTY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(pcb, "_kicad_cli_major", lambda: 9)

        assert pcb._kicad_var("MYLIBS", None) == str(tmp_path / "mine")
        assert pcb._kicad_var("KICAD9_3RD_PARTY", None) == str(third_party)
        monkeypatch.setenv("MYLIBS", str(tmp_path / "elsewhere"))
        assert pcb._kicad_var("MYLIBS", None) == str(tmp_path / "elsewhere"), "the environment wins"
        monkeypatch.delenv("MYLIBS")

        assert pcb._FpLibResolver(None, []).global_table == {
            "Mine": str(mine),
            "Over": str(override),
            "PCM_Pkg": str(own),
            "PCM_Also": str(pkg / "Also.pretty"),
            "PCM_Pkg_1": str(pkg / "Pkg.pretty"),
        }, (
            "the table's own PCM_Pkg row keeps its nickname and the package's Pkg.pretty"
            " takes PCM_Pkg_1; Loose.pretty is in no package"
        )

        (config / "kicad.json").write_text(json.dumps({"pcm": {"lib_prefix": "X_"}}))
        assert pcb._FpLibResolver(None, []).global_table["X_Pkg"] == str(pkg / "Pkg.pretty")
        (config / "kicad.json").write_text(json.dumps({"pcm": {"lib_auto_add": False}}))
        table = pcb._FpLibResolver(None, []).global_table
        assert "X_Pkg" not in table and "PCM_Also" not in table and table["PCM_Pkg"] == str(own)

    def test_the_pcm_scan_skips_listed_libraries_and_numbers_clashes(self, tmp_path, monkeypatch):
        """KiCad's own rules for the libraries it adds (PCM_FP_LIB_TRAVERSER
        in 9.0.9, PCM_LIB_TRAVERSER in 10.0.6): any depth inside a package; a
        library whose ${KICAD<N>_3RD_PARTY} URI is already a row stays as that
        row has it, so a renamed one gains no alias and a disabled one stays
        unloadable; a nickname already taken, by a row or by a library added
        before, gets _1, _2 and so on."""
        config = tmp_path / "config" / "9.0"
        config.mkdir(parents=True)
        docs = tmp_path / "docs"
        footprints = docs / pcb._KICAD_PATH_STR / "9.0" / "3rdparty" / "footprints"
        for lib in ("a_pkg/Renamed", "a_pkg/Off", "b_pkg/sub/Deep", "c_pkg/Dup", "d_pkg/Dup"):
            (footprints / f"{lib}.pretty").mkdir(parents=True)
        (config / "fp-lib-table").write_bytes(
            _table(
                [
                    _row("Renamed", "${KICAD9_3RD_PARTY}/footprints/a_pkg/Renamed.pretty"),
                    _row(
                        "PCM_Off",
                        "${KICAD9_3RD_PARTY}/footprints/a_pkg/Off.pretty",
                        extra=" (disabled)",
                    ),
                ]
            )
        )
        monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("KICAD_DOCUMENTS_HOME", str(docs))
        monkeypatch.delenv("KICAD9_3RD_PARTY", raising=False)
        monkeypatch.setattr(pcb, "_kicad_cli_major", lambda: 9)
        monkeypatch.setattr(pcb, "_kicad_root", lambda: None)

        resolver = pcb._FpLibResolver(None, [])
        assert resolver.global_table == {
            "Renamed": str(footprints / "a_pkg" / "Renamed.pretty"),
            "PCM_Deep": str(footprints / "b_pkg" / "sub" / "Deep.pretty"),
            "PCM_Dup": str(footprints / "c_pkg" / "Dup.pretty"),
            "PCM_Dup_1": str(footprints / "d_pkg" / "Dup.pretty"),
        }
        assert resolver.resolve("PCM_Off") is None, "KiCad will not load a disabled library"

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


class TestKicadDocumentsHome:
    """The default of ${KICAD<N>_3RD_PARTY}, the root the Plugin and Content
    Manager installs into: KiCad's PATHS::GetDefault3rdPartyPath."""

    def test_the_override_takes_kicad_s_folder_name_as_well(self, tmp_path, monkeypatch):
        """KiCad appends its own folder name after KICAD_DOCUMENTS_HOME just as
        it does after the documents folder (PATHS::getUserDocumentPath), so the
        override names the folder that holds KiCad/<N>.0, not <N>.0 itself."""
        monkeypatch.setenv("KICAD_DOCUMENTS_HOME", str(tmp_path))
        assert pcb._kicad_documents_home() == tmp_path / pcb._KICAD_PATH_STR

    @pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
    def test_the_3rd_party_default_is_the_folder_kicad_cli_creates(self, tmp_path, monkeypatch):
        """kicad-cli creates PATHS::GetDefault3rdPartyPath() as it starts, under
        KICAD_DOCUMENTS_HOME when that is set, and that folder is the default of
        ${KICAD<N>_3RD_PARTY}. So KiCad itself names the root, on every platform
        the suite runs on and with its own folder name's capitalisation."""
        docs = tmp_path / "docs"
        docs.mkdir()
        cli = _find_kicad_cli()
        assert cli is not None
        env = {**os.environ, "KICAD_DOCUMENTS_HOME": str(docs)}
        subprocess.run([cli, "version"], env=env, capture_output=True, check=True, timeout=120)
        major = _kicad_cli_major()
        assert major is not None
        monkeypatch.setenv("KICAD_DOCUMENTS_HOME", str(docs))
        monkeypatch.delenv(f"KICAD{major}_3RD_PARTY", raising=False)
        default = pcb._kicad_var(f"KICAD{major}_3RD_PARTY", None, configured={})
        assert default is not None
        assert sorted(docs.rglob("3rdparty")) == [Path(default)]

    @pytest.mark.skipif(os.name != "nt", reason="the shell's Documents folder is a Windows notion")
    def test_windows_documents_is_the_folder_the_shell_names(self, monkeypatch):
        """KiCad asks wxWidgets, which asks the shell (SHGetFolderPath with
        CSIDL_PERSONAL), so OneDrive folder backup and folder redirection move
        it, and ~/Documents follows neither. The oracle is .NET's
        Environment.GetFolderPath, which puts the same question to the shell
        through code of its own."""
        # Base64 keeps the answer clear of the console code page, and of the
        # Console.OutputEncoding setter, which throws where there is no console.
        script = (
            "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("
            "[Environment]::GetFolderPath('MyDocuments')))"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            check=True,
            timeout=60,
        )
        documents = Path(base64.b64decode(out.stdout.strip()).decode("utf-8"))
        monkeypatch.delenv("KICAD_DOCUMENTS_HOME", raising=False)
        assert pcb._kicad_documents_home() == documents / "KiCad"

    def test_a_shell_that_cannot_answer_falls_back_to_home_documents(self, monkeypatch):
        def no_answer():
            raise OSError("no Documents folder from the shell")

        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(pcb, "_windows_documents_dir", no_answer)
        monkeypatch.delenv("KICAD_DOCUMENTS_HOME", raising=False)
        assert pcb._kicad_documents_home() == Path.home() / "Documents" / pcb._KICAD_PATH_STR


_FP = (
    b'(footprint "Resistor_SMD:R_0603_1608Metric"\n\t\t(layer "F.Cu")\n\t\t(uuid "a")'
    b'\n\t\t(at 10 10 90)\n\t\t(descr "d")'
    b'\n\t\t(property "Reference" "R1"\n\t\t\t(at 0 -1.43 0)\n\t\t\t(layer "F.SilkS")'
    b'\n\t\t\t(uuid "b")\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)'
    b"\n\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)"
    b'\n\t\t(property "Description" "Resistor SMD 0603"\n\t\t\t(at 0 0 0)\n\t\t\t(layer "F.Fab")'
    b'\n\t\t\t(hide yes)\n\t\t\t(uuid "c")\n\t\t\t(effects\n\t\t\t\t(font'
    b"\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)"
    b'\n\t\t(path "/x")\n\t\t(attr smd)'
    b'\n\t\t(pad "1" smd roundrect\n\t\t\t(at -0.825 0 90)\n\t\t\t(size 0.8 0.95)'
    b'\n\t\t\t(layers "F.Cu" "F.Paste" "F.Mask")\n\t\t\t(uuid "e")\n\t\t)\n\t)'
)


class TestFootprintSync:
    """Fields, filters, attributes and sheet linkage onto one footprint node, as bytes."""

    def _fp(self):
        return _cst.parse(_FP).lists[0]

    def test_a_new_field_is_hidden_on_fab_at_the_footprint_s_angle(self):
        fp = self._fp()
        comp = {
            "fields": {"MPN": "ERJ", "Description": "Resistor"},
            "fp_filters": "R_*",
            "sheetname": "/",
            "sheetfile": "e.kicad_sch",
            "dnp": True,
            "exclude_from_bom": True,
        }
        assert pcb._sync_fp_from_component(fp, comp)
        out = _cst.serialize(fp)
        assert (
            b'(property "MPN" "ERJ"\n\t\t\t(at 0 0 90)\n\t\t\t(unlocked yes)'
            b'\n\t\t\t(layer "F.Fab")\n\t\t\t(hide yes)'
        ) in out, "the footprint sits at 90, and KiCad writes a new field at the footprint's angle"
        assert b'(property "Description" "Resistor"\n\t\t\t(at 0 0 0)' in out, (
            "an existing field changes its text in place and keeps its position"
        )
        assert b'(property ki_fp_filters "R_*")\n\t\t(path "/x")' in out, (
            "filters just before the path"
        )
        assert out.index(b'"MPN"') < out.index(b"ki_fp_filters"), "user fields before the filters"
        assert (
            b'(path "/x")\n\t\t(sheetname "/")\n\t\t(sheetfile "e.kicad_sch")'
            b"\n\t\t(attr smd exclude_from_bom dnp)"
        ) in out
        assert out.count(b"(uuid ") == 5, "the new field carries a uuid of its own"
        assert not pcb._sync_fp_from_component(fp, comp), "a second pass changes nothing"
        cleared = dict(comp, dnp=False, exclude_from_bom=False)
        assert pcb._sync_fp_from_component(fp, cleared)
        assert b"(attr smd)" in _cst.serialize(fp)
        assert pcb._sync_fp_from_component(fp, dict(cleared, fields={})) is False
        assert b'(property "MPN" "ERJ"' in _cst.serialize(fp), "a field that left the symbol stays"
        assert pcb._sync_fp_from_component(fp, dict(cleared, fp_filters="", sheetfile=""))
        out = _cst.serialize(fp)
        assert b"ki_fp_filters" not in out and b"(sheetfile" not in out, (
            "filters and sheet linkage are whatever the netlist says, empty included"
        )

    def test_a_field_s_angle_is_the_absolute_one_kicad_stores(self):
        """Measured on a board KiCad's own update wrote: a footprint at -90
        carries its new field at 270 and one at 90 at 90. The writer stores a
        text's absolute board angle and the parser reads it back as such; a
        180-degree sample cannot tell this from a cancelling angle."""
        fp = self._fp()
        _cst._fill_at(fp, 10, 10, -90)
        assert pcb._sync_fp_fields(fp, {"MPN": "x"})
        assert b'(property "MPN" "x"\n\t\t\t(at 0 0 270)' in _cst.serialize(fp)

    def test_a_new_field_lands_before_the_filters_on_a_board_kicad_wrote(self):
        fp = self._fp()
        pcb._set_fp_filters(fp, "R_*")
        assert pcb._sync_fp_fields(fp, {"MPN": "x"})
        out = _cst.serialize(fp)
        assert out.index(b'"Description"') < out.index(b'"MPN"') < out.index(b"ki_fp_filters")
        assert out.index(b"ki_fp_filters") < out.index(b"(path ")

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

    def test_a_hand_ordered_attr_is_left_alone_until_a_flag_changes(self):
        fp = self._fp()
        attr = fp.find("attr")
        node = _cst.parse(b"(attr dnp through_hole)").lists[0]
        node.sep = attr.sep
        fp.children[fp.children.index(attr)] = node
        before = _cst.serialize(fp)
        assert not pcb._sync_fp_attributes(fp, dnp=True, exclude_from_bom=False)
        assert _cst.serialize(fp) == before, "same flags: not rewritten, not reported"
        assert pcb._sync_fp_attributes(fp, dnp=False, exclude_from_bom=False)
        assert b"(attr through_hole)" in _cst.serialize(fp)

    def test_the_position_file_flag_follows_a_kicad_10_netlist_and_stays_on_9(self):
        fp = self._fp()
        attr = fp.find("attr")
        node = _cst.parse(b"(attr smd exclude_from_pos_files)").lists[0]
        node.sep = attr.sep
        fp.children[fp.children.index(attr)] = node
        assert not pcb._sync_fp_attributes(fp, False, False), "KiCad 9: the board's own flag"
        assert not pcb._sync_fp_attributes(fp, False, False, exclude_from_pos_files=True)
        assert pcb._sync_fp_attributes(fp, False, False, exclude_from_pos_files=False)
        assert b"(attr smd)" in _cst.serialize(fp)
        assert pcb._sync_fp_attributes(fp, True, False, exclude_from_pos_files=True)
        assert b"(attr smd exclude_from_pos_files dnp)" in _cst.serialize(fp)
        comp = {"fields": {}, "dnp": True, "exclude_from_pos_files": None}
        assert not pcb._sync_fp_from_component(fp, comp), "None keeps the token as found"

    def test_a_back_side_footprint_gets_mirrored_fields_on_b_fab(self):
        fp = self._fp()
        fp.find("layer").atoms[1].set_text("B.Cu")
        assert pcb._sync_fp_fields(fp, {"MPN": "x"})
        out = _cst.serialize(fp)
        assert (
            b'(property "MPN" "x"\n\t\t\t(at 0 0 90)\n\t\t\t(unlocked yes)\n\t\t\t(layer "B.Fab")'
            in out
        )
        assert b"(thickness 0.15)\n\t\t\t\t)\n\t\t\t\t(justify mirror)\n\t\t\t)\n\t\t)" in out, (
            "KiCad's StyleFromSettings mirrors a text on a back layer"
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
                node = sym.find(key)
                if node is None:
                    # KiCad 10's in_pos_files: a file this server wrote has no
                    # such token, and KiCad 10 reads its absence as yes.
                    node = _cst.parse(f"({key} yes)".encode()).lists[0]
                    sym.insert_after(sym.find("dnp"), node)
                node.atoms[1].set_text("yes" if value else "no")
    path.write_bytes(_cst.serialize(tree))


def _netlist_components(sch: str) -> dict[str, dict]:
    """The schematic's components as kicad-cli exports them: the oracle for
    what KiCad's own update would copy verbatim."""
    out = Path(sch).with_name("oracle.xml")
    _run_cli(["sch", "export", "netlist", "--format", "kicadxml", "--output", str(out), sch])
    return {c["ref"]: c for c in ni.parse_netlist(str(out))[0]}


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
        # Verbatim from the symbol, as KiCad's own update copies it: Device:R's
        # Datasheet is "~" in KiCad 9's library and "" in KiCad 10's.
        assert (
            by_ref["R1"].properties["Datasheet"]
            == _netlist_components(sch)["R1"]["fields"]["Datasheet"]
        )
        assert "MPN" not in by_ref["R2"].properties
        assert by_ref["R2"].attributes.excludeFromBom is True
        assert by_ref["R1"].attributes.excludeFromBom is False
        raw = Path(pcb_path).read_bytes()
        assert b"(attr smd exclude_from_bom dnp)" in raw
        assert raw.count(b'(sheetname "/")') == 2 and raw.count(b'(sheetfile "e2e.kicad_sch")') == 2
        assert b'(property "MPN" "ERJ-3EKF1002V"\n\t\t\t(at 0 0 0)\n\t\t\t(unlocked yes)' in raw
        assert raw.count(b'(property ki_fp_filters "R_*")') == 2, "the symbol's footprint filters"
        assert raw.index(b'"MPN"') < raw.index(b"ki_fp_filters"), "user fields before the filters"
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

    def test_kicad_10_s_position_file_box_reaches_the_footprint(self, tmp_path):
        """KiCad 10's netlist marks "Exclude from position files" and its update
        sets and clears the footprint's flag from it; so does this import, with
        KiCad's own position export as the oracle. KiCad 9's netlist has no
        marker, so there the flag stays the board's own (unit-tested above)."""
        from mcp_server_kicad.pcb import update_pcb_from_schematic

        if (_kicad_cli_major() or 0) < 10:
            pytest.skip("KiCad 10's in_pos_files box; KiCad 9 has none")
        sch, pcb_path = _make_project(tmp_path)
        _set_symbol_flags(sch, "R1", in_pos_files=False)
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert sorted(result.added) == ["R1", "R2"]
        raw = Path(pcb_path).read_bytes()
        assert b"(attr smd exclude_from_pos_files)" in raw and raw.count(b"(attr smd)") == 1
        assert _position_refs(pcb_path) == {"R2"}, "KiCad's own export leaves R1 out"
        _set_symbol_flags(sch, "R1", in_pos_files=True)
        result = update_pcb_from_schematic(schematic_path=sch, pcb_path=pcb_path)
        assert result.fields_updated == ["R1"]
        assert b"exclude_from_pos_files" not in Path(pcb_path).read_bytes()
        assert _position_refs(pcb_path) == {"R1", "R2"}

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
