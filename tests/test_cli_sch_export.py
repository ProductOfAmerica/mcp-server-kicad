"""Tests for CLI schematic export tools."""

import json
import shutil
from pathlib import Path

import pytest

from mcp_server_kicad import export

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
pytestmark = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")


class TestExportSchematicPdf:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = export.export_schematic_pdf(str(scratch_sch), str(tmp_path))
        data = json.loads(result)
        assert Path(data["path"]).exists()
        assert data["format"] == "pdf"


class TestExportSchematicSvg:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = export.export_schematic_svg(str(scratch_sch), str(tmp_path))
        data = json.loads(result)
        assert data["format"] == "svg"


class TestExportSchematicNetlist:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = export.export_schematic_netlist(str(scratch_sch), str(tmp_path))
        data = json.loads(result)
        assert Path(data["path"]).exists()


class TestExportBom:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = export.export_bom(str(scratch_sch), str(tmp_path))
        data = json.loads(result)
        assert Path(data["path"]).exists()


class TestExportSchematicDxf:
    def test_produces_file(self, scratch_sch, tmp_path):
        result = export.export_schematic_dxf(str(scratch_sch), str(tmp_path))
        data = json.loads(result)
        assert data["format"] == "dxf"


class TestRunErcAnnotation:
    # This test doesn't need kicad-cli — we test the annotation logic directly
    pytestmark = []

    def test_annotates_subsheet_errors(self):
        """ERC result includes annotation for sub-sheet hierarchical label errors."""
        fake_violations = [
            {
                "description": (
                    'Hierarchical label "VIN" in root sheet cannot'
                    " be connected to non-existent parent sheet"
                ),
                "severity": "error",
                "type": "pin_not_connected",
            },
        ]
        annotated = export._annotate_erc_violations(fake_violations)
        assert annotated[0].get("expected_subsheet_issue") is True

    def test_leaves_real_errors_alone(self):
        """Non-subsheet errors should not be annotated."""
        fake_violations = [
            {
                "description": "Pin not connected",
                "severity": "error",
                "type": "pin_not_connected",
            },
        ]
        annotated = export._annotate_erc_violations(fake_violations)
        assert "expected_subsheet_issue" not in annotated[0]


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
        result = export._parse_unconnected_pins(fake_erc_json)
        # Should include the real unconnected pin, not the sub-sheet noise
        assert len(result) == 1
        assert result[0]["description"] == "Pin not connected"

    def test_empty_violations(self):
        """No violations returns empty list."""
        fake_erc_json = {"sheets": [{"violations": []}]}
        result = export._parse_unconnected_pins(fake_erc_json)
        assert result == []
