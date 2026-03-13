"""Tests for project scaffolding tools."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
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
        result = project.add_hierarchical_sheet(
            parent_schematic_path=parent_path,
            sheet_name="Missing",
            sheet_file=str(tmp_path / "nonexistent.kicad_sch"),
            pins=[],
            project_path=str(tmp_path / "root.kicad_pro"),
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

        result = project.remove_hierarchical_sheet(
            name="Power",
            parent_schematic_path=str(parent),
        )
        assert "Multiple sheets" in result
        assert "uuid=" in result
        assert "disambiguate" in result

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

        result = project.remove_hierarchical_sheet(
            name="WrongName",
            uuid=sheet_uuid,
            parent_schematic_path=str(parent),
        )
        assert "found but its name is" in result
        assert "'Power'" in result
        assert "'WrongName'" in result

        # Verify sheet was NOT removed
        sch = Schematic.from_file(str(parent))
        assert len(sch.sheets) == 1

    def test_no_match_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)

        result = project.remove_hierarchical_sheet(
            name="NonExistent",
            parent_schematic_path=str(parent),
        )
        assert "No hierarchical sheet found" in result

    def test_no_parameters_error(self, tmp_path: Path):
        parent, child = self._make_parent_and_child(tmp_path)

        result = project.remove_hierarchical_sheet(
            parent_schematic_path=str(parent),
        )
        assert "Provide at least one of" in result


HAS_KICAD_CLI = shutil.which("kicad-cli") is not None


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestHierarchicalSheetErcClean:
    """Integration test: hierarchical sheet should produce zero ERC violations."""

    def test_hierarchical_sheet_erc_clean(self, tmp_path: Path):
        from conftest import (
            assert_erc_clean,
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

        # Run ERC on the parent (which includes child via hierarchy)
        assert_erc_clean(str(proj_dir / "erc_proj.kicad_sch"))


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
        data = json.loads(result)
        assert "note" in data, "Expected a 'note' key indicating root redirect"
        assert "root schematic" in data["note"]
        assert data["violation_count"] == 0

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
        data = json.loads(result)
        assert "note" not in data, "Root schematic ERC should not have a redirect note"

    def test_non_hierarchical_no_redirect(self, tmp_path: Path):
        """Standalone schematic with no .kicad_pro — no redirect note."""
        from mcp_server_kicad import schematic

        # Create a standalone schematic (no project file)
        standalone = tmp_path / "standalone.kicad_sch"
        project.create_schematic(schematic_path=str(standalone))

        result = schematic.run_erc(schematic_path=str(standalone))
        data = json.loads(result)
        assert "note" not in data, "Standalone schematic ERC should not have a redirect note"


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")
class TestGetVersion:
    def test_returns_version_info(self):
        result = json.loads(project.get_version())
        assert "version_info" in result or "error" in result


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")
class TestRunJobset:
    def test_missing_jobset_returns_error(self, tmp_path):
        result = project.run_jobset(str(tmp_path / "nonexistent.kicad_jobset"))
        assert "failed" in result.lower() or "error" in result.lower()
