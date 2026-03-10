"""Tests for project scaffolding tools."""

from __future__ import annotations

import json
from pathlib import Path

from kiutils.schematic import Schematic
from kiutils.symbol import SymbolLib

from mcp_server_kicad import project


class TestCreateProject:
    def test_creates_pro_and_prl(self, tmp_path: Path):
        result = project.create_project(directory=str(tmp_path / "myproj"), name="myproj")
        assert "myproj.kicad_pro" in result

        pro = tmp_path / "myproj" / "myproj.kicad_pro"
        prl = tmp_path / "myproj" / "myproj.kicad_prl"
        assert pro.exists()
        assert prl.exists()

        pro_data = json.loads(pro.read_text())
        assert pro_data["meta"]["filename"] == "myproj.kicad_pro"
        assert pro_data["meta"]["version"] == 1

        prl_data = json.loads(prl.read_text())
        assert prl_data["meta"]["filename"] == "myproj.kicad_prl"
        assert prl_data["meta"]["version"] == 3

    def test_creates_directory_if_missing(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "proj"
        project.create_project(directory=str(target), name="test")
        assert (target / "test.kicad_pro").exists()

    def test_errors_if_pro_exists(self, tmp_path: Path):
        (tmp_path / "dup.kicad_pro").write_text("{}")
        result = project.create_project(directory=str(tmp_path), name="dup")
        assert "already exists" in result


class TestCreateSchematic:
    def test_creates_valid_schematic(self, tmp_path: Path):
        sch_path = str(tmp_path / "test.kicad_sch")
        result = project.create_schematic(schematic_path=sch_path)
        assert "test.kicad_sch" in result

        sch = Schematic.from_file(sch_path)
        assert sch.version == 20250114
        assert sch.generator == "eeschema"
        assert sch.uuid is not None
        assert sch.schematicSymbols == []

    def test_errors_if_exists(self, tmp_path: Path):
        sch_path = tmp_path / "dup.kicad_sch"
        sch_path.write_text("")
        result = project.create_schematic(schematic_path=str(sch_path))
        assert "already exists" in result


class TestCreateSymbolLibrary:
    def test_creates_valid_sym_lib(self, tmp_path: Path):
        lib_path = str(tmp_path / "custom.kicad_sym")
        result = project.create_symbol_library(symbol_lib_path=lib_path)
        assert "custom.kicad_sym" in result

        lib = SymbolLib.from_file(lib_path)
        assert str(lib.version) == "20231120"
        assert lib.generator == "kicad_symbol_editor"
        assert lib.symbols == []

    def test_errors_if_exists(self, tmp_path: Path):
        lib_path = tmp_path / "dup.kicad_sym"
        lib_path.write_text("")
        result = project.create_symbol_library(symbol_lib_path=str(lib_path))
        assert "already exists" in result


class TestCreateSymLibTable:
    def test_creates_table_with_entries(self, tmp_path: Path):
        entries = [
            {"name": "skrimp", "uri": "${KIPRJMOD}/skrimp.kicad_sym"},
            {"name": "power", "uri": "${KICAD8_SYMBOL_DIR}/power.kicad_sym"},
        ]
        result = project.create_sym_lib_table(directory=str(tmp_path), entries=entries)
        assert "2 entries" in result

        content = (tmp_path / "sym-lib-table").read_text()
        assert "(sym_lib_table" in content
        assert '(name "skrimp")' in content
        assert '(uri "${KIPRJMOD}/skrimp.kicad_sym")' in content
        assert '(name "power")' in content

    def test_creates_empty_table(self, tmp_path: Path):
        result = project.create_sym_lib_table(directory=str(tmp_path), entries=[])
        assert "0 entries" in result
        content = (tmp_path / "sym-lib-table").read_text()
        assert "(sym_lib_table" in content

    def test_overwrites_existing(self, tmp_path: Path):
        (tmp_path / "sym-lib-table").write_text("old content")
        entries = [{"name": "new", "uri": "new.kicad_sym"}]
        result = project.create_sym_lib_table(directory=str(tmp_path), entries=entries)
        assert "1 entries" in result
        content = (tmp_path / "sym-lib-table").read_text()
        assert '(name "new")' in content
        assert "old content" not in content


class TestAddHierarchicalSheet:
    def _make_parent_and_child(self, tmp_path: Path) -> tuple[Path, Path]:
        """Helper: create empty parent + child schematics."""
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        return Path(parent), Path(child)

    def test_adds_sheet_to_parent(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "VOUT", "direction": "output"},
            {"name": "GND", "direction": "bidirectional"},
        ]
        result = project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power",
            sheet_file=str(child),
            pins=pins,
        )
        assert "Power" in result
        assert "3 pins" in result

        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 1
        sheet = sch.sheets[0]
        assert sheet.sheetName.value == "Power"
        assert sheet.fileName.value == "child.kicad_sch"
        assert len(sheet.pins) == 3

    def test_adds_labels_to_child(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "VOUT", "direction": "output"},
        ]
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power",
            sheet_file=str(child),
            pins=pins,
        )
        child_sch = Schematic.from_file(str(child))
        assert len(child_sch.hierarchicalLabels) == 2
        label_names = {hl.text for hl in child_sch.hierarchicalLabels}
        assert label_names == {"VIN", "VOUT"}

    def test_pin_directions_match(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        pins = [{"name": "SIG", "direction": "bidirectional"}]
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=pins,
        )
        sch = Schematic.from_file(str(parent))
        assert sch.sheets[0].pins[0].connectionType == "bidirectional"

        child_sch = Schematic.from_file(str(child))
        assert child_sch.hierarchicalLabels[0].shape == "bidirectional"

    def test_errors_if_child_missing(self, tmp_path: Path):
        parent_path = str(tmp_path / "root.kicad_sch")
        project.create_schematic(schematic_path=parent_path)
        result = project.add_hierarchical_sheet(
            parent_schematic_path=parent_path,
            sheet_name="Missing",
            sheet_file=str(tmp_path / "nonexistent.kicad_sch"),
            pins=[],
        )
        assert "not found" in result or "does not exist" in result

    def test_custom_position(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[{"name": "A", "direction": "input"}],
            x=50.8,
            y=76.2,
        )
        sch = Schematic.from_file(str(parent))
        sheet = sch.sheets[0]
        assert sheet.position.X == 50.8
        assert sheet.position.Y == 76.2
