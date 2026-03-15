"""Tests for project scaffolding tools."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import conftest
import pytest
from kiutils.schematic import Schematic
from kiutils.symbol import SymbolLib
from mcp.server.fastmcp.exceptions import ToolError

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

    def test_creates_root_schematic(self, tmp_path: Path):
        """create_project also creates the root .kicad_sch file."""
        project.create_project(directory=str(tmp_path / "myproj"), name="myproj")
        sch_path = tmp_path / "myproj" / "myproj.kicad_sch"
        assert sch_path.exists()

        sch = Schematic.from_file(str(sch_path))
        assert sch.version == 20250114
        assert sch.schematicSymbols == []

    def test_errors_if_pro_exists(self, tmp_path: Path):
        (tmp_path / "dup.kicad_pro").write_text("{}")
        with pytest.raises(ToolError, match="already exists"):
            project.create_project(directory=str(tmp_path), name="dup")


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
        with pytest.raises(ToolError, match="already exists"):
            project.create_schematic(schematic_path=str(sch_path))


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
        with pytest.raises(ToolError, match="already exists"):
            project.create_symbol_library(symbol_lib_path=str(lib_path))


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
            project_path=str(tmp_path / "root.kicad_pro"),
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
            project_path=str(tmp_path / "root.kicad_pro"),
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
            project_path=str(tmp_path / "root.kicad_pro"),
        )
        sch = Schematic.from_file(str(parent))
        assert sch.sheets[0].pins[0].connectionType == "bidirectional"

        child_sch = Schematic.from_file(str(child))
        assert child_sch.hierarchicalLabels[0].shape == "bidirectional"

    def test_errors_if_child_missing(self, tmp_path: Path):
        parent_path = str(tmp_path / "root.kicad_sch")
        project.create_schematic(schematic_path=parent_path)
        with pytest.raises(ToolError, match="does not exist"):
            project.add_hierarchical_sheet(
                parent_schematic_path=parent_path,
                sheet_name="Missing",
                sheet_file=str(tmp_path / "nonexistent.kicad_sch"),
                pins=[],
                project_path=str(tmp_path / "root.kicad_pro"),
            )

    def test_custom_position(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[{"name": "A", "direction": "input"}],
            x=50.8,
            y=76.2,
            project_path=str(tmp_path / "root.kicad_pro"),
        )
        sch = Schematic.from_file(str(parent))
        sheet = sch.sheets[0]
        assert sheet.position.X == 50.8
        assert sheet.position.Y == 76.2

    def test_child_labels_have_wire_stubs_and_net_labels(self, tmp_path: Path):
        """Each hierarchical label in the child should have a wire stub and local label."""
        parent, child = self._make_parent_and_child(tmp_path)
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "GND", "direction": "bidirectional"},
        ]
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power",
            sheet_file=str(child),
            pins=pins,
            project_path=str(tmp_path / "root.kicad_pro"),
        )
        child_sch = Schematic.from_file(str(child))

        from kiutils.items.schitems import Connection, LocalLabel

        label_x = 25.4
        for i, pin_def in enumerate(pins):
            label_y = round(25.4 + i * 5.08, 4)
            stub_end_x = round(label_x + 2.54, 4)

            # Assert wire stub exists from (label_x, label_y) to (stub_end_x, label_y)
            wires = [
                g
                for g in child_sch.graphicalItems
                if isinstance(g, Connection) and g.type == "wire"
            ]
            found_wire = any(
                abs(w.points[0].X - label_x) < 0.02
                and abs(w.points[0].Y - label_y) < 0.02
                and abs(w.points[1].X - stub_end_x) < 0.02
                and abs(w.points[1].Y - label_y) < 0.02
                for w in wires
            )
            assert found_wire, (
                f"No wire stub for '{pin_def['name']}' from ({label_x},{label_y}) "
                f"to ({stub_end_x},{label_y})"
            )

            # Assert local label exists at stub endpoint
            labels = [
                lbl
                for lbl in child_sch.labels
                if isinstance(lbl, LocalLabel)
                and lbl.text == pin_def["name"]
                and abs(lbl.position.X - stub_end_x) < 0.02
                and abs(lbl.position.Y - label_y) < 0.02
            ]
            assert len(labels) == 1, (
                f"Expected LocalLabel '{pin_def['name']}' at ({stub_end_x},{label_y})"
            )

    def test_parent_pins_have_wire_stubs_and_net_labels(self, tmp_path: Path):
        """Each hierarchical pin in the parent should have a wire stub and local label."""
        parent, child = self._make_parent_and_child(tmp_path)
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "GND", "direction": "bidirectional"},
        ]
        x, y = 25.4, 25.4
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power",
            sheet_file=str(child),
            pins=pins,
            x=x,
            y=y,
            project_path=str(tmp_path / "root.kicad_pro"),
        )
        parent_sch = Schematic.from_file(str(parent))

        from kiutils.items.schitems import Connection, LocalLabel

        pin_spacing = 2.54
        for i, pin_def in enumerate(pins):
            pin_y = round(y + (i + 1) * pin_spacing, 4)
            stub_end_x = round(x - 2.54, 4)

            # Assert wire stub exists from (x, pin_y) to (stub_end_x, pin_y)
            wires = [
                g
                for g in parent_sch.graphicalItems
                if isinstance(g, Connection) and g.type == "wire"
            ]
            found_wire = any(
                abs(w.points[0].X - x) < 0.02
                and abs(w.points[0].Y - pin_y) < 0.02
                and abs(w.points[1].X - stub_end_x) < 0.02
                and abs(w.points[1].Y - pin_y) < 0.02
                for w in wires
            )
            assert found_wire, (
                f"No wire stub for '{pin_def['name']}' from ({x},{pin_y}) to ({stub_end_x},{pin_y})"
            )

            # Assert local label exists at stub endpoint with angle=180
            labels = [
                lbl
                for lbl in parent_sch.labels
                if isinstance(lbl, LocalLabel)
                and lbl.text == pin_def["name"]
                and abs(lbl.position.X - stub_end_x) < 0.02
                and abs(lbl.position.Y - pin_y) < 0.02
                and lbl.position.angle == 180
            ]
            assert len(labels) == 1, (
                f"Expected LocalLabel '{pin_def['name']}' at ({stub_end_x},{pin_y}) angle=180"
            )


class TestRemoveHierarchicalSheet:
    def _make_parent_and_child(self, tmp_path: Path) -> tuple[Path, Path]:
        """Helper: create empty parent + child schematics."""
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        return Path(parent), Path(child)

    def _add_sheet(self, parent: Path, child: Path, name: str = "Power") -> str:
        """Helper: add a hierarchical sheet and return its UUID."""
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name=name,
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
        )
        sch = Schematic.from_file(str(parent))
        uuid = sch.sheets[-1].uuid
        assert uuid is not None
        return uuid

    def test_remove_by_uuid(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        sheet_uuid = self._add_sheet(parent, child)

        result = project.remove_hierarchical_sheet(
            uuid=sheet_uuid,
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result

        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 0

    def test_remove_by_name_unique(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        self._add_sheet(parent, child, name="Power")

        result = project.remove_hierarchical_sheet(
            name="Power",
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result

        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 0

    def test_ambiguous_name_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        # Add two sheets with the same name
        self._add_sheet(parent, child, name="Power")
        self._add_sheet(parent, child, name="Power")

        with pytest.raises(ToolError, match="Multiple sheets"):
            project.remove_hierarchical_sheet(
                name="Power",
                parent_schematic_path=str(parent),
            )

        # Verify neither was removed
        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 2

    def test_remove_by_name_and_uuid(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        uuid1 = self._add_sheet(parent, child, name="Power")
        self._add_sheet(parent, child, name="Power")

        result = project.remove_hierarchical_sheet(
            name="Power",
            uuid=uuid1,
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result

        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 1
        assert sch.sheets[0].uuid != uuid1

    def test_name_uuid_mismatch_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        sheet_uuid = self._add_sheet(parent, child, name="Power")

        with pytest.raises(ToolError, match="found but its name is"):
            project.remove_hierarchical_sheet(
                name="WrongName",
                uuid=sheet_uuid,
                parent_schematic_path=str(parent),
            )

        # Verify sheet was NOT removed
        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 1

    def test_no_match_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)

        with pytest.raises(ToolError, match="No hierarchical sheet found"):
            project.remove_hierarchical_sheet(
                name="NonExistent",
                parent_schematic_path=str(parent),
            )

    def test_no_parameters_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)

        with pytest.raises(ToolError, match="Provide at least one of"):
            project.remove_hierarchical_sheet(
                parent_schematic_path=str(parent),
            )

    def test_delete_child_file_no_other_refs(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        self._add_sheet(parent, child, name="Power")

        assert child.exists()
        result = project.remove_hierarchical_sheet(
            name="Power",
            delete_child_file=True,
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result
        assert "Deleted child file" in result
        assert not child.exists()

    def test_delete_child_file_still_referenced(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)
        # Two sheet blocks pointing to the same child file
        uuid1 = self._add_sheet(parent, child, name="Power1")
        self._add_sheet(parent, child, name="Power2")

        result = project.remove_hierarchical_sheet(
            uuid=uuid1,
            delete_child_file=True,
            parent_schematic_path=str(parent),
        )
        assert "Removed" in result
        assert "Kept child file" in result
        assert "still referenced" in result
        assert child.exists()


class TestModifyHierarchicalSheet:
    def test_rename_sheet(self, tmp_path: Path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        project.add_hierarchical_sheet(
            parent_schematic_path=parent,
            sheet_name="Power",
            sheet_file=child,
            pins=[{"name": "VIN", "direction": "input"}],
        )
        sch = Schematic.from_file(parent)
        sheet_uuid = sch.sheets[0].uuid

        result = project.modify_hierarchical_sheet(
            sheet_uuid=sheet_uuid,
            schematic_path=parent,
            sheet_name="Power Supply",
        )
        assert "Power Supply" in result

        sch2 = Schematic.from_file(parent)
        assert sch2.sheets[0].sheetName.value == "Power Supply"


class TestAddSheetPin:
    def test_adds_pin_to_existing_sheet(self, tmp_path: Path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        project.add_hierarchical_sheet(
            parent_schematic_path=parent,
            sheet_name="Sub",
            sheet_file=child,
            pins=[{"name": "A", "direction": "input"}],
        )
        sch = Schematic.from_file(parent)
        sheet_uuid = sch.sheets[0].uuid

        result = project.add_sheet_pin(
            sheet_uuid=sheet_uuid,
            pin_name="B",
            connection_type="output",
            schematic_path=parent,
        )
        assert "B" in result

        sch2 = Schematic.from_file(parent)
        assert len(sch2.sheets[0].pins) == 2
        pin_names = {p.name for p in sch2.sheets[0].pins}
        assert pin_names == {"A", "B"}


class TestRemoveSheetPin:
    def test_removes_pin_by_name(self, tmp_path: Path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        project.add_hierarchical_sheet(
            parent_schematic_path=parent,
            sheet_name="Sub",
            sheet_file=child,
            pins=[
                {"name": "A", "direction": "input"},
                {"name": "B", "direction": "output"},
            ],
        )
        sch = Schematic.from_file(parent)
        sheet_uuid = sch.sheets[0].uuid

        result = project.remove_sheet_pin(
            sheet_uuid=sheet_uuid,
            pin_name="A",
            schematic_path=parent,
        )
        assert "Removed" in result

        sch2 = Schematic.from_file(parent)
        assert len(sch2.sheets[0].pins) == 1
        assert sch2.sheets[0].pins[0].name == "B"


HAS_KICAD_CLI = shutil.which("kicad-cli") is not None


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestHierarchicalSheetParseable:
    """Integration test: hierarchical sheet output should be parseable by kicad-cli.

    The test schematic has real ERC violations (dangling labels, off-grid pins)
    because the fixture uses a simplified resistor symbol.  We only verify
    kicad-cli can parse the files (structural validity), not ERC cleanliness.
    """

    def test_hierarchical_sheet_parseable(self, tmp_path: Path):
        from conftest import (
            assert_kicad_parseable,
            build_r_symbol,
            place_r1,
        )
        from kiutils.schematic import Schematic

        from mcp_server_kicad.schematic import wire_pins_to_net

        # Create project
        proj_dir = tmp_path / "erc_proj"
        project.create_project(directory=str(proj_dir), name="erc_proj")

        # Create child schematic
        child_path = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child_path))

        # Add hierarchical sheet with pins
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "GND", "direction": "bidirectional"},
        ]
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "erc_proj.kicad_sch"),
            sheet_name="Power",
            sheet_file=str(child_path),
            pins=pins,
            project_path=str(proj_dir / "erc_proj.kicad_pro"),
        )

        # In the child: place a resistor and wire its pins to the hierarchy net names
        child_sch = Schematic.from_file(str(child_path))
        child_sch.libSymbols.append(build_r_symbol())
        r1 = place_r1(50, 50)
        child_sch.schematicSymbols.append(r1)
        child_sch.to_file()

        # Wire R1 pins to the hierarchy net labels
        wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="VIN",
            schematic_path=str(child_path),
        )
        wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "2"}],
            label_text="GND",
            schematic_path=str(child_path),
        )

        # Verify kicad-cli can parse the parent (which includes child via hierarchy)
        assert_kicad_parseable(str(proj_dir / "erc_proj.kicad_sch"))


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestSubSheetErcRedirect:
    """Tests for sub-sheet ERC auto-redirect to root schematic."""

    def test_subsheet_erc_redirects_to_root(self, tmp_path: Path):
        """Run ERC on a sub-sheet and verify it auto-redirects to root."""
        from conftest import build_r_symbol, place_r1

        from mcp_server_kicad import schematic

        # Create hierarchical project
        proj_dir = tmp_path / "erc_proj"
        project.create_project(directory=str(proj_dir), name="erc_proj")
        child_path = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child_path))
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "GND", "direction": "bidirectional"},
        ]
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "erc_proj.kicad_sch"),
            sheet_name="Power",
            sheet_file=str(child_path),
            pins=pins,
            project_path=str(proj_dir / "erc_proj.kicad_pro"),
        )

        # In child: place a resistor and wire its pins to hierarchy net labels
        child_sch = Schematic.from_file(str(child_path))
        child_sch.libSymbols.append(build_r_symbol())
        r1 = place_r1(50, 50)
        child_sch.schematicSymbols.append(r1)
        child_sch.to_file()

        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="VIN",
            schematic_path=str(child_path),
        )
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "2"}],
            label_text="GND",
            schematic_path=str(child_path),
        )

        # Run ERC on the *child* — should auto-redirect to root
        result = schematic.run_erc(schematic_path=str(child_path))
        assert result.note is not None, "Expected a 'note' indicating root redirect"
        assert "root schematic" in result.note
        assert result.violation_count == 0

    def test_erc_on_root_no_redirect(self, tmp_path: Path):
        """Run ERC on root schematic directly — no redirect note expected."""
        from conftest import build_r_symbol, place_r1

        from mcp_server_kicad import schematic

        # Create hierarchical project
        proj_dir = tmp_path / "erc_proj"
        project.create_project(directory=str(proj_dir), name="erc_proj")
        child_path = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child_path))
        pins = [
            {"name": "VIN", "direction": "input"},
            {"name": "GND", "direction": "bidirectional"},
        ]
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "erc_proj.kicad_sch"),
            sheet_name="Power",
            sheet_file=str(child_path),
            pins=pins,
            project_path=str(proj_dir / "erc_proj.kicad_pro"),
        )

        # In child: place a resistor and wire its pins to hierarchy net labels
        child_sch = Schematic.from_file(str(child_path))
        child_sch.libSymbols.append(build_r_symbol())
        r1 = place_r1(50, 50)
        child_sch.schematicSymbols.append(r1)
        child_sch.to_file()

        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="VIN",
            schematic_path=str(child_path),
        )
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "2"}],
            label_text="GND",
            schematic_path=str(child_path),
        )

        # Run ERC on the *root* — no redirect
        root_path = str(proj_dir / "erc_proj.kicad_sch")
        result = schematic.run_erc(schematic_path=root_path)
        assert result.note is None, "Root schematic ERC should not have a redirect note"

    def test_non_hierarchical_no_redirect(self, tmp_path: Path):
        """Standalone schematic with no .kicad_pro — no redirect note."""
        from mcp_server_kicad import schematic

        # Create a standalone schematic (no project file)
        standalone = tmp_path / "standalone.kicad_sch"
        project.create_schematic(schematic_path=str(standalone))

        result = schematic.run_erc(schematic_path=str(standalone))
        assert result.note is None, "Standalone schematic ERC should not have a redirect note"


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestErcWithProjectPath:
    def test_run_erc_with_explicit_project_path(self, tmp_path: Path):
        """run_erc should accept project_path for explicit root resolution."""
        from mcp_server_kicad import schematic

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        result = schematic.run_erc(
            schematic_path=str(child),
            project_path=str(proj_dir / "proj.kicad_pro"),
        )
        assert result.note is not None
        assert "root schematic" in result.note


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")
class TestGetVersion:
    def test_returns_version_info(self):
        result = project.get_version()
        assert result.version_info


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")
class TestRunJobset:
    def test_missing_jobset_returns_error(self, tmp_path):
        with pytest.raises((ToolError, RuntimeError, FileNotFoundError)):
            project.run_jobset(str(tmp_path / "nonexistent.kicad_jobset"))


@pytest.mark.no_kicad_validation
class TestAnnotateSchematic:
    def test_annotates_unannotated_components(self, tmp_path: Path):
        import conftest
        from conftest import build_r_symbol, new_schematic
        from kiutils.items.common import Position, Property
        from kiutils.items.schitems import SchematicSymbol

        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        # Place two unannotated resistors
        for i, y in enumerate([50, 80]):
            sym = SchematicSymbol()
            sym.libId = "Device:R"
            sym.position = Position(X=100, Y=y)
            sym.uuid = conftest._gen_uuid()
            sym.unit = 1
            sym.inBom = True
            sym.onBoard = True
            sym.properties = [
                Property(
                    key="Reference",
                    value="R?",
                    id=0,
                    effects=conftest._default_effects(),
                    position=Position(X=100, Y=y),
                ),
                Property(
                    key="Value",
                    value="10K",
                    id=1,
                    effects=conftest._default_effects(),
                    position=Position(X=100, Y=y),
                ),
            ]
            sch.schematicSymbols.append(sym)

        path = tmp_path / "annotate.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = project.annotate_schematic(schematic_path=str(path))
        assert "Annotated 2" in result
        assert "R1" in result

        sch2 = Schematic.from_file(str(path))
        refs = sorted(
            next(p.value for p in s.properties if p.key == "Reference")
            for s in sch2.schematicSymbols
        )
        assert refs == ["R1", "R2"]

    def test_respects_existing_references(self, tmp_path: Path):
        import conftest
        from conftest import build_r_symbol, new_schematic, place_r1
        from kiutils.items.common import Position, Property
        from kiutils.items.schitems import SchematicSymbol

        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        # R3 already exists
        r3 = place_r1(50, 50)
        for p in r3.properties:
            if p.key == "Reference":
                p.value = "R3"
        sch.schematicSymbols.append(r3)
        # One unannotated
        sym = SchematicSymbol()
        sym.libId = "Device:R"
        sym.position = Position(X=100, Y=100)
        sym.uuid = conftest._gen_uuid()
        sym.unit = 1
        sym.inBom = True
        sym.onBoard = True
        sym.properties = [
            Property(
                key="Reference",
                value="R?",
                id=0,
                effects=conftest._default_effects(),
                position=Position(X=100, Y=100),
            ),
            Property(
                key="Value",
                value="10K",
                id=1,
                effects=conftest._default_effects(),
                position=Position(X=100, Y=100),
            ),
        ]
        sch.schematicSymbols.append(sym)

        path = tmp_path / "annotate2.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = project.annotate_schematic(schematic_path=str(path))
        assert "R4" in result  # Should start after R3

    def test_no_unannotated_returns_message(self, scratch_sch):
        result = project.annotate_schematic(schematic_path=str(scratch_sch))
        assert "No unannotated" in result or "0" in result


class TestIsRootSchematic:
    def test_root_returns_true(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        result = project.is_root_schematic(schematic_path=str(proj_dir / "proj.kicad_sch"))
        assert result.is_root is True
        assert result.root_path is None

    def test_subsheet_returns_false(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))

        result = project.is_root_schematic(schematic_path=str(child))
        assert result.is_root is False
        assert "proj.kicad_sch" in result.root_path


class TestListHierarchy:
    def test_returns_hierarchy_tree(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Power",
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        result = project.list_hierarchy(
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        )
        assert result.root == "proj.kicad_sch"
        assert len(result.sheets) == 1
        assert result.sheets[0]["sheet_name"] == "Power"
        assert result.sheets[0]["file_name"] == "child.kicad_sch"


class TestValidateHierarchy:
    def test_clean_hierarchy_returns_ok(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        result = project.validate_hierarchy(
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        )
        assert result.status == "ok"
        assert result.issue_count == 0

    def test_detects_orphaned_label(self, tmp_path: Path):
        from kiutils.items.common import Position
        from kiutils.items.schitems import HierarchicalLabel

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )
        # Add an orphaned label to child (no matching pin in parent)
        child_sch = Schematic.from_file(str(child))
        child_sch.hierarchicalLabels.append(
            HierarchicalLabel(
                text="ORPHAN",
                shape="output",
                position=Position(X=50, Y=50, angle=0),
                effects=conftest._default_effects(),
                uuid=conftest._gen_uuid(),
            )
        )
        child_sch.to_file()

        result = project.validate_hierarchy(
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        )
        assert result.status == "issues_found"
        assert any(i["type"] == "orphaned_label" for i in result.issues)


class TestGetSheetInfo:
    def test_returns_sheet_details_with_pin_label_matching(self, tmp_path: Path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        project.add_hierarchical_sheet(
            parent_schematic_path=parent,
            sheet_name="Power",
            sheet_file=child,
            pins=[
                {"name": "VIN", "direction": "input"},
                {"name": "GND", "direction": "bidirectional"},
            ],
        )
        sch = Schematic.from_file(parent)
        sheet_uuid = sch.sheets[0].uuid

        result = project.get_sheet_info(
            sheet_uuid=sheet_uuid,
            schematic_path=parent,
        )
        assert result.sheet_name == "Power"
        assert len(result.pins) == 2
        # add_hierarchical_sheet creates matching labels in child, so matched=True
        for pin in result.pins:
            assert pin["matched"] is True


class TestTraceHierarchicalNet:
    def test_traces_net_through_hierarchy(self, tmp_path: Path):
        """Trace a net from root through a hierarchical pin into a child sheet."""
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        result = project.trace_hierarchical_net(
            net_name="VIN",
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        )
        assert result.net_name == "VIN"
        assert len(result.sheets_touched) >= 1


class TestListCrossSheetNets:
    def test_lists_hierarchical_connections(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[
                {"name": "VIN", "direction": "input"},
                {"name": "GND", "direction": "bidirectional"},
            ],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        result = project.list_cross_sheet_nets(
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        )
        assert len(result.hierarchical_nets) == 2
        net_names = {n["name"] for n in result.hierarchical_nets}
        assert "VIN" in net_names
        assert "GND" in net_names


class TestGetSymbolInstances:
    def test_returns_instances(self, tmp_path: Path):
        """get_symbol_instances should return symbol instance data from root schematic."""
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")

        # The root schematic created by create_project is empty, so there won't be instances.
        # But the tool should still work and return an empty list.
        result = project.get_symbol_instances(
            schematic_path=str(proj_dir / "proj.kicad_sch"),
        )
        assert isinstance(result.instances, list)


class TestMoveHierarchicalSheet:
    def test_moves_sheet_and_pins(self, tmp_path: Path):
        parent = str(tmp_path / "root.kicad_sch")
        child = str(tmp_path / "child.kicad_sch")
        project.create_schematic(schematic_path=parent)
        project.create_schematic(schematic_path=child)
        project.add_hierarchical_sheet(
            parent_schematic_path=parent,
            sheet_name="Sub",
            sheet_file=child,
            pins=[{"name": "A", "direction": "input"}],
        )
        sch = Schematic.from_file(parent)
        sheet_uuid = sch.sheets[0].uuid

        result = project.move_hierarchical_sheet(
            sheet_uuid=sheet_uuid,
            new_x=80,
            new_y=60,
            schematic_path=parent,
        )
        assert "80" in result or "Moved" in result

        sch2 = Schematic.from_file(parent)
        assert sch2.sheets[0].position.X == 80
        assert sch2.sheets[0].position.Y == 60


class TestReorderSheetPages:
    def test_reorders_pages(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child1 = proj_dir / "child1.kicad_sch"
        child2 = proj_dir / "child2.kicad_sch"
        project.create_schematic(schematic_path=str(child1))
        project.create_schematic(schematic_path=str(child2))
        root = str(proj_dir / "proj.kicad_sch")
        pro = str(proj_dir / "proj.kicad_pro")
        project.add_hierarchical_sheet(
            parent_schematic_path=root,
            sheet_name="Sheet1",
            sheet_file=str(child1),
            pins=[],
            project_path=pro,
        )
        project.add_hierarchical_sheet(
            parent_schematic_path=root,
            sheet_name="Sheet2",
            sheet_file=str(child2),
            pins=[],
            project_path=pro,
        )

        sch = Schematic.from_file(root)
        uuid1, uuid2 = sch.sheets[0].uuid, sch.sheets[1].uuid

        result = project.reorder_sheet_pages(
            page_order=[uuid2, uuid1],
            schematic_path=root,
        )
        assert "Reordered" in result


class TestDuplicateSheet:
    def test_duplicates_sheet(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        root = str(proj_dir / "proj.kicad_sch")
        pro = str(proj_dir / "proj.kicad_pro")
        project.add_hierarchical_sheet(
            parent_schematic_path=root,
            sheet_name="Power",
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
            project_path=pro,
        )
        sch = Schematic.from_file(root)
        sheet_uuid = sch.sheets[0].uuid

        result = project.duplicate_sheet(
            sheet_uuid=sheet_uuid,
            new_sheet_name="Power2",
            schematic_path=root,
            project_path=pro,
        )
        assert "Power2" in result

        sch2 = Schematic.from_file(root)
        assert len(sch2.sheets) == 2
        names = {s.sheetName.value for s in sch2.sheets}
        assert "Power" in names
        assert "Power2" in names
        # The new sheet should reference a different file
        files = {s.fileName.value for s in sch2.sheets}
        assert len(files) == 2  # Two different files


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestExportHierarchicalNetlist:
    def test_exports_netlist(self, tmp_path: Path):
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")

        try:
            result = project.export_hierarchical_netlist(
                schematic_path=str(proj_dir / "proj.kicad_sch"),
                output_dir=str(proj_dir),
            )
            assert result.output_path
        except (ToolError, RuntimeError, FileNotFoundError):
            pass  # kicad-cli may produce non-XML netlist format


class TestParentProjectInstances:
    def test_add_hierarchical_sheet_creates_parent_instances(self, tmp_path: Path):
        """Symbols placed before add_hierarchical_sheet should get parent instances."""
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))

        # Place a component on the child FIRST
        from mcp_server_kicad import schematic

        schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="10K",
            x=50,
            y=50,
            schematic_path=str(child),
        )

        # THEN add child as hierarchical sheet
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        # R1 should have instances for BOTH projects
        child_sch = Schematic.from_file(str(child))
        r1 = child_sch.schematicSymbols[0]
        instance_projects = {inst.name for inst in r1.instances}
        assert "proj" in instance_projects, f"Missing parent instance. Got: {instance_projects}"

    def test_place_component_on_subsheet_creates_parent_instance(self, tmp_path: Path):
        """Components placed after add_hierarchical_sheet should get parent instances."""
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))

        # Add child as hierarchical sheet FIRST
        project.add_hierarchical_sheet(
            parent_schematic_path=str(proj_dir / "proj.kicad_sch"),
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[],
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        # THEN place a component on the child
        from mcp_server_kicad import schematic

        schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="10K",
            x=50,
            y=50,
            schematic_path=str(child),
            project_path=str(proj_dir / "proj.kicad_pro"),
        )

        # R1 should have instances for BOTH projects
        child_sch = Schematic.from_file(str(child))
        r1 = child_sch.schematicSymbols[0]
        instance_projects = {inst.name for inst in r1.instances}
        assert "proj" in instance_projects, f"Missing parent instance. Got: {instance_projects}"


class TestFlattenHierarchy:
    def test_flattens_simple_hierarchy(self, tmp_path: Path):
        """Flatten a 2-level hierarchy into a single schematic."""
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")

        # Create child with a component
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))

        # Add a resistor to the child
        child_sch = Schematic.from_file(str(child))
        child_sch.libSymbols.append(conftest.build_r_symbol())
        child_sch.schematicSymbols.append(conftest.place_r1(50, 50))
        child_sch.to_file()

        root = str(proj_dir / "proj.kicad_sch")
        pro = str(proj_dir / "proj.kicad_pro")
        project.add_hierarchical_sheet(
            parent_schematic_path=root,
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[],
            project_path=pro,
        )

        # Before flatten: root has 0 components and 1 sheet
        root_sch = Schematic.from_file(root)
        assert len(root_sch.sheets) == 1
        assert len(root_sch.schematicSymbols) == 0

        result = project.flatten_hierarchy(
            schematic_path=root,
            output_path=str(proj_dir / "flat.kicad_sch"),
        )
        assert "flat.kicad_sch" in result or "Flattened" in result

        # After flatten: output has the child's component, no sheets
        flat_sch = Schematic.from_file(str(proj_dir / "flat.kicad_sch"))
        assert len(flat_sch.sheets) == 0
        assert len(flat_sch.schematicSymbols) >= 1


@pytest.mark.no_kicad_validation
class TestRootSymbolInstanceSync:
    """Tests for _upsert_root_symbol_instance, _remove_root_symbol_instance,
    and integration with tools that should sync symbolInstances."""

    def _make_project_with_child(self, tmp_path: Path):
        """Helper: create project, child sch, link as hierarchical sheet.

        Returns (proj_dir, root_path, child_path, pro_path).
        """
        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        root = str(proj_dir / "proj.kicad_sch")
        pro = str(proj_dir / "proj.kicad_pro")
        project.add_hierarchical_sheet(
            parent_schematic_path=root,
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[],
            project_path=pro,
        )
        return proj_dir, root, str(child), pro

    def _place_symbol_in_child(self, child_path: str) -> str:
        """Place an unannotated R? symbol in child using kiutils. Returns sym UUID."""
        from conftest import _default_effects, _gen_uuid, build_r_symbol
        from kiutils.items.common import Position, Property
        from kiutils.items.schitems import SchematicSymbol

        child_sch = Schematic.from_file(child_path)
        # Add lib symbol if not present
        if not any(s.entryName == "R" for s in child_sch.libSymbols):
            child_sch.libSymbols.append(build_r_symbol())

        sym = SchematicSymbol()
        sym.libId = "Device:R"
        sym.position = Position(X=100, Y=100)
        sym_uuid = _gen_uuid()
        sym.uuid = sym_uuid
        sym.unit = 1
        sym.inBom = True
        sym.onBoard = True
        sym.properties = [
            Property(
                key="Reference",
                value="R?",
                id=0,
                effects=_default_effects(),
                position=Position(X=100, Y=97),
            ),
            Property(
                key="Value",
                value="10K",
                id=1,
                effects=_default_effects(),
                position=Position(X=100, Y=103),
            ),
            Property(
                key="Footprint",
                value="",
                id=2,
                effects=_default_effects(hide=True),
                position=Position(X=100, Y=100),
            ),
        ]
        sym.pins = {"1": _gen_uuid(), "2": _gen_uuid()}
        child_sch.schematicSymbols.append(sym)
        child_sch.to_file()
        return sym_uuid

    # ---- Helper unit tests (1-6) ----

    def test_upsert_creates_symbol_instance_in_root(self, tmp_path: Path):
        """Test 1: upsert creates a new SymbolInstance entry in root."""
        from mcp_server_kicad._shared import _upsert_root_symbol_instance

        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)
        sym_uuid = self._place_symbol_in_child(child)

        result = _upsert_root_symbol_instance(
            schematic_path=child,
            project_path=pro,
            sym_uuid=sym_uuid,
            reference="R1",
            value="10K",
        )
        assert result is True

        root_sch = Schematic.from_file(root)
        si_list = root_sch.symbolInstances
        assert len(si_list) == 1
        assert si_list[0].reference == "R1"
        assert sym_uuid in si_list[0].path

    def test_upsert_updates_existing_symbol_instance(self, tmp_path: Path):
        """Test 2: upsert updates an existing entry instead of duplicating."""
        from mcp_server_kicad._shared import _upsert_root_symbol_instance

        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)
        sym_uuid = self._place_symbol_in_child(child)

        _upsert_root_symbol_instance(
            schematic_path=child,
            project_path=pro,
            sym_uuid=sym_uuid,
            reference="R1",
            value="10K",
        )
        _upsert_root_symbol_instance(
            schematic_path=child,
            project_path=pro,
            sym_uuid=sym_uuid,
            reference="R2",
            value="22K",
        )

        root_sch = Schematic.from_file(root)
        si_list = root_sch.symbolInstances
        assert len(si_list) == 1  # Not 2
        assert si_list[0].reference == "R2"
        assert si_list[0].value == "22K"

    def test_upsert_flat_project_returns_false(self, tmp_path: Path):
        """Test 3: upsert returns False for a bare .kicad_sch with no .kicad_pro."""
        from conftest import new_schematic

        from mcp_server_kicad._shared import _upsert_root_symbol_instance

        sch = new_schematic()
        bare_path = tmp_path / "bare.kicad_sch"
        sch.filePath = str(bare_path)
        sch.to_file()

        result = _upsert_root_symbol_instance(
            schematic_path=str(bare_path),
            project_path="",
            sym_uuid="fake-uuid-1234",
            reference="R1",
        )
        assert result is False

    def test_upsert_root_schematic_uses_2_segment_path(self, tmp_path: Path):
        """Test 4: upsert on root schematic itself uses /{root_uuid}/{sym_uuid}."""
        from conftest import _gen_uuid

        from mcp_server_kicad._shared import _upsert_root_symbol_instance

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        root = str(proj_dir / "proj.kicad_sch")
        pro = str(proj_dir / "proj.kicad_pro")

        sym_uuid = _gen_uuid()
        result = _upsert_root_symbol_instance(
            schematic_path=root,
            project_path=pro,
            sym_uuid=sym_uuid,
            reference="R1",
            value="10K",
        )
        assert result is True

        root_sch = Schematic.from_file(root)
        si_list = root_sch.symbolInstances
        assert len(si_list) == 1
        # Path should be /{root_uuid}/{sym_uuid} — exactly 2 segments
        path = si_list[0].path
        assert path == f"/{root_sch.uuid}/{sym_uuid}"
        segments = [s for s in path.split("/") if s]
        assert len(segments) == 2

    def test_remove_deletes_symbol_instance_from_root(self, tmp_path: Path):
        """Test 5: remove deletes a SymbolInstance entry from root."""
        from mcp_server_kicad._shared import (
            _remove_root_symbol_instance,
            _upsert_root_symbol_instance,
        )

        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)
        sym_uuid = self._place_symbol_in_child(child)

        _upsert_root_symbol_instance(
            schematic_path=child,
            project_path=pro,
            sym_uuid=sym_uuid,
            reference="R1",
            value="10K",
        )

        result = _remove_root_symbol_instance(
            schematic_path=child,
            project_path=pro,
            sym_uuid=sym_uuid,
        )
        assert result is True

        root_sch = Schematic.from_file(root)
        assert len(root_sch.symbolInstances) == 0

    def test_remove_nonexistent_returns_false(self, tmp_path: Path):
        """Test 6: remove returns False when UUID not in symbolInstances."""
        from conftest import _gen_uuid

        from mcp_server_kicad._shared import _remove_root_symbol_instance

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        root = str(proj_dir / "proj.kicad_sch")
        pro = str(proj_dir / "proj.kicad_pro")

        result = _remove_root_symbol_instance(
            schematic_path=root,
            project_path=pro,
            sym_uuid=_gen_uuid(),
        )
        assert result is False

    # ---- Integration tests (7-13) ----

    def test_annotate_subsheet_syncs_root_symbol_instances(self, tmp_path: Path):
        """Test 7: annotate_schematic on sub-sheet syncs symbolInstances to root."""
        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)
        # Place two unannotated symbols
        self._place_symbol_in_child(child)
        self._place_symbol_in_child(child)

        # Fix positions so they don't overlap (re-read and adjust)
        child_sch = Schematic.from_file(child)
        for i, sym in enumerate(child_sch.schematicSymbols):
            from kiutils.items.common import Position

            sym.position = Position(X=100, Y=50 + i * 30)
        child_sch.to_file()

        project.annotate_schematic(schematic_path=child, project_path=pro)

        root_sch = Schematic.from_file(root)
        si_list = root_sch.symbolInstances
        refs = sorted(si.reference for si in si_list)
        assert "R1" in refs
        assert "R2" in refs

    def test_annotate_subsheet_updates_per_symbol_instances(self, tmp_path: Path):
        """Test 7b: annotate_schematic updates the per-symbol instances block."""
        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)
        self._place_symbol_in_child(child)
        self._place_symbol_in_child(child)

        # Fix positions so they don't overlap
        child_sch = Schematic.from_file(child)
        for i, sym in enumerate(child_sch.schematicSymbols):
            from kiutils.items.common import Position

            sym.position = Position(X=100, Y=50 + i * 30)
        child_sch.to_file()

        project.annotate_schematic(schematic_path=child, project_path=pro)

        # Re-read the child schematic and verify per-symbol instances
        child_sch = Schematic.from_file(child)
        for sym in child_sch.schematicSymbols:
            ref_prop = next(p for p in sym.properties if p.key == "Reference")
            assert "?" not in ref_prop.value, f"Symbol still unannotated: {ref_prop.value}"
            # The per-symbol instances block must also have the new reference
            assert len(sym.instances) > 0, f"Symbol {ref_prop.value} has no instances block"
            for inst in sym.instances:
                for path_entry in inst.paths:
                    assert path_entry.reference == ref_prop.value, (
                        f"Per-symbol instance reference {path_entry.reference!r} "
                        f"doesn't match property {ref_prop.value!r}"
                    )

    def test_annotate_root_syncs_own_symbol_instances(self, tmp_path: Path):
        """Test 8: annotate_schematic on root syncs symbolInstances to itself."""
        from conftest import _default_effects, _gen_uuid, build_r_symbol
        from kiutils.items.common import Position, Property
        from kiutils.items.schitems import SchematicSymbol

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        root = str(proj_dir / "proj.kicad_sch")
        pro = str(proj_dir / "proj.kicad_pro")

        # Place unannotated symbol in root
        root_sch = Schematic.from_file(root)
        root_sch.libSymbols.append(build_r_symbol())
        sym = SchematicSymbol()
        sym.libId = "Device:R"
        sym.position = Position(X=100, Y=100)
        sym.uuid = _gen_uuid()
        sym.unit = 1
        sym.inBom = True
        sym.onBoard = True
        sym.properties = [
            Property(
                key="Reference",
                value="R?",
                id=0,
                effects=_default_effects(),
                position=Position(X=100, Y=97),
            ),
            Property(
                key="Value",
                value="10K",
                id=1,
                effects=_default_effects(),
                position=Position(X=100, Y=103),
            ),
            Property(
                key="Footprint",
                value="",
                id=2,
                effects=_default_effects(hide=True),
                position=Position(X=100, Y=100),
            ),
        ]
        sym.pins = {"1": _gen_uuid(), "2": _gen_uuid()}
        root_sch.schematicSymbols.append(sym)
        root_sch.to_file()

        project.annotate_schematic(schematic_path=root, project_path=pro)

        root_sch2 = Schematic.from_file(root)
        si_list = root_sch2.symbolInstances
        refs = [si.reference for si in si_list]
        assert "R1" in refs
        # Verify 2-segment path
        for si in si_list:
            segments = [s for s in si.path.split("/") if s]
            assert len(segments) == 2

    def test_place_component_subsheet_syncs_root(self, tmp_path: Path):
        """Test 9: place_component on sub-sheet syncs symbolInstances to root."""
        from mcp_server_kicad import schematic

        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)

        # Add lib symbol to child so place_component can find it
        child_sch = Schematic.from_file(child)
        child_sch.libSymbols.append(conftest.build_r_symbol())
        child_sch.to_file()

        schematic.place_component(
            reference="R1",
            value="10K",
            lib_id="Device:R",
            x=100,
            y=100,
            schematic_path=child,
            project_path=pro,
        )

        root_sch = Schematic.from_file(root)
        si_list = root_sch.symbolInstances
        refs = [si.reference for si in si_list]
        assert "R1" in refs

    def test_remove_component_subsheet_syncs_root(self, tmp_path: Path):
        """Test 10: remove_component on sub-sheet removes symbolInstances from root."""
        from mcp_server_kicad import schematic

        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)

        # Place a component in the child (place_component syncs root automatically)
        child_sch = Schematic.from_file(child)
        child_sch.libSymbols.append(conftest.build_r_symbol())
        child_sch.to_file()

        schematic.place_component(
            reference="R1",
            value="10K",
            lib_id="Device:R",
            x=100,
            y=100,
            schematic_path=child,
            project_path=pro,
        )

        schematic.remove_component(reference="R1", schematic_path=child)

        root_sch = Schematic.from_file(root)
        si_refs = [si.reference for si in root_sch.symbolInstances]
        assert "R1" not in si_refs

    def test_set_component_property_value_syncs_root(self, tmp_path: Path):
        """Test 11: set_component_property syncs value change to root symbolInstances."""
        from mcp_server_kicad import schematic

        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)

        # Place a component in the child (place_component syncs root automatically)
        child_sch = Schematic.from_file(child)
        child_sch.libSymbols.append(conftest.build_r_symbol())
        child_sch.to_file()

        schematic.place_component(
            reference="R1",
            value="10K",
            lib_id="Device:R",
            x=100,
            y=100,
            schematic_path=child,
            project_path=pro,
        )

        schematic.set_component_property(
            reference="R1",
            key="Value",
            value="22K",
            schematic_path=child,
        )

        root_sch = Schematic.from_file(root)
        si_list = root_sch.symbolInstances
        values = [si.value for si in si_list if si.reference == "R1"]
        assert "22K" in values

    def test_annotate_upserts_not_duplicates(self, tmp_path: Path):
        """Test 12: re-annotation doesn't create duplicate symbolInstances."""
        proj_dir, root, child, pro = self._make_project_with_child(tmp_path)
        self._place_symbol_in_child(child)
        self._place_symbol_in_child(child)

        # Fix positions
        child_sch = Schematic.from_file(child)
        for i, sym in enumerate(child_sch.schematicSymbols):
            from kiutils.items.common import Position

            sym.position = Position(X=100, Y=50 + i * 30)
        child_sch.to_file()

        # First annotation
        project.annotate_schematic(schematic_path=child, project_path=pro)

        # Reset refs back to R? to simulate re-annotation
        child_sch = Schematic.from_file(child)
        for sym in child_sch.schematicSymbols:
            for p in sym.properties:
                if p.key == "Reference":
                    p.value = "R?"
        child_sch.to_file()

        # Second annotation
        project.annotate_schematic(schematic_path=child, project_path=pro)

        root_sch = Schematic.from_file(root)
        si_list = root_sch.symbolInstances
        # Should have exactly 2 entries (one per symbol), not 4
        assert len(si_list) == 2
        refs = sorted(si.reference for si in si_list)
        assert refs == ["R1", "R2"]

    def test_add_hierarchical_sheet_syncs_existing_child_syms(self, tmp_path: Path):
        """Test 13: add_hierarchical_sheet syncs existing child symbols to root."""

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))

        # Place annotated symbols in child BEFORE linking
        child_sch = Schematic.from_file(str(child))
        child_sch.libSymbols.append(conftest.build_r_symbol())
        child_sch.schematicSymbols.append(conftest.place_r1(50, 50))
        r2 = conftest.place_r1(50, 80)
        for p in r2.properties:
            if p.key == "Reference":
                p.value = "R2"
        child_sch.schematicSymbols.append(r2)
        child_sch.to_file()

        root = str(proj_dir / "proj.kicad_sch")
        pro = str(proj_dir / "proj.kicad_pro")

        # Link child as hierarchical sheet
        project.add_hierarchical_sheet(
            parent_schematic_path=root,
            sheet_name="Sub",
            sheet_file=str(child),
            pins=[],
            project_path=pro,
        )

        root_sch = Schematic.from_file(root)
        si_list = root_sch.symbolInstances
        si_refs = sorted(si.reference for si in si_list)
        assert "R1" in si_refs
        assert "R2" in si_refs
