"""Tests for CLI schematic export tools."""

import json
import shutil
from pathlib import Path

import pytest

from mcp_server_kicad import schematic

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
pytestmark = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")


class TestExportSchematicPdf:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = schematic.export_schematic(
            format="pdf", schematic_path=str(scratch_sch), output_dir=str(tmp_path)
        )
        data = json.loads(result)
        assert Path(data["path"]).exists()
        assert data["format"] == "pdf"


class TestExportSchematicSvg:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = schematic.export_schematic(
            format="svg", schematic_path=str(scratch_sch), output_dir=str(tmp_path)
        )
        data = json.loads(result)
        assert data["format"] == "svg"


class TestExportSchematicNetlist:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = schematic.export_netlist(schematic_path=str(scratch_sch), output_dir=str(tmp_path))
        data = json.loads(result)
        assert Path(data["path"]).exists()


class TestExportBom:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = schematic.export_bom(schematic_path=str(scratch_sch), output_dir=str(tmp_path))
        data = json.loads(result)
        assert Path(data["path"]).exists()


class TestExportSchematicDxf:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = schematic.export_schematic(
            format="dxf", schematic_path=str(scratch_sch), output_dir=str(tmp_path)
        )
        data = json.loads(result)
        assert data["format"] == "dxf"


class TestExportSchematicInvalidFormat:
    pytestmark = []  # no kicad-cli needed for format validation

    def test_invalid_format(self):
        result = json.loads(schematic.export_schematic(format="xyz"))
        assert "error" in result


class TestFindRootSchematic:
    """Test _find_root_schematic helper used by ERC auto-redirect."""

    pytestmark = []

    def test_returns_none_for_root(self, tmp_path):
        """Root schematic returns None (no redirect needed)."""
        from mcp_server_kicad import project

        project.create_project(directory=str(tmp_path / "proj"), name="proj")
        root = str(tmp_path / "proj" / "proj.kicad_sch")
        assert schematic._find_root_schematic(root) is None

    def test_returns_root_for_subsheet(self, tmp_path):
        """Sub-sheet returns path to root schematic."""
        from mcp_server_kicad import project

        proj_dir = tmp_path / "proj"
        project.create_project(directory=str(proj_dir), name="proj")
        child = proj_dir / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        result = schematic._find_root_schematic(str(child))
        assert result is not None
        assert result.endswith("proj.kicad_sch")


class TestListUnconnectedPins:
    # This test doesn't need kicad-cli — tests the parsing logic
    pytestmark = []

    def test_parses_unconnected_violations(self):
        """Extracts pin info from ERC violations."""
        fake_erc_json = {
            "sheets": [
                {
                    "violations": [
                        {
                            "type": "pin_not_connected",
                            "description": "Pin not connected",
                            "severity": "error",
                            "items": [
                                {
                                    "description": 'Pin "EN" of component "U1"',
                                    "pos": {"x": 100.0, "y": 50.0},
                                },
                            ],
                        },
                        {
                            "type": "pin_not_connected",
                            "description": (
                                "Hierarchical label cannot be"
                                " connected to non-existent"
                                " parent sheet"
                            ),
                            "severity": "error",
                            "items": [],
                        },
                    ],
                },
            ],
        }
        result = schematic._parse_unconnected_pins(fake_erc_json)
        # Should include the real unconnected pin, not the sub-sheet noise
        assert len(result) == 1
        assert result[0]["description"] == "Pin not connected"

    def test_empty_violations(self):
        """No violations returns empty list."""
        fake_erc_json = {"sheets": [{"violations": []}]}
        result = schematic._parse_unconnected_pins(fake_erc_json)
        assert result == []
