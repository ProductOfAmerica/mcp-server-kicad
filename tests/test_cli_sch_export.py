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
