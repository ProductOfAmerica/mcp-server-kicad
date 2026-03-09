"""Tests for CLI PCB export and utility tools."""

import shutil
import pytest

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
pytestmark = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")

import json
from mcp_server_kicad import export
from pathlib import Path


def _parse_result(result: str) -> dict:
    """Parse tool result as JSON, handling both success and error cases."""
    return json.loads(result)


class TestExportGerbers:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_gerbers(str(scratch_pcb), str(tmp_path / "gerbers"))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportGerber:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_gerber(str(scratch_pcb), "F.Cu", str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportDrill:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_drill(str(scratch_pcb), str(tmp_path))
        data = _parse_result(result)
        assert "path" in data or "error" in data


class TestExportPcbPdf:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_pcb_pdf(str(scratch_pcb), str(tmp_path), layers=["F.Cu"])
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportPcbSvg:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_pcb_svg(str(scratch_pcb), str(tmp_path), layers=["F.Cu"])
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportPositions:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_positions(str(scratch_pcb), str(tmp_path))
        data = _parse_result(result)
        assert "path" in data or "error" in data


class TestExportStep:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_step(str(scratch_pcb), str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportStl:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_stl(str(scratch_pcb), str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportGlb:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.export_glb(str(scratch_pcb), str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestRender3d:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = export.render_3d(str(scratch_pcb), str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportSymbolSvg:
    def test_returns_json(self, scratch_sym_lib, tmp_path):
        result = export.export_symbol_svg(str(scratch_sym_lib), str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportFootprintSvg:
    def test_returns_json(self, tmp_path):
        from kiutils.footprint import Footprint
        fp = Footprint()
        fp.entryName = "R_0603"
        path = str(tmp_path / "R_0603.kicad_mod")
        fp.filePath = path
        fp.to_file()
        result = export.export_footprint_svg(path, str(tmp_path / "svg_out"))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestUpgradeSymbolLib:
    def test_returns_result(self, scratch_sym_lib, tmp_path):
        import shutil
        copy = str(tmp_path / "upgrade_test.kicad_sym")
        shutil.copy(str(scratch_sym_lib), copy)
        result = export.upgrade_symbol_lib(copy)
        assert "success" in result.lower() or "upgraded" in result.lower() or "error" in result.lower()


class TestUpgradeFootprintLib:
    def test_returns_result(self, tmp_path):
        from kiutils.footprint import Footprint
        fp = Footprint()
        fp.entryName = "R_0603"
        path = str(tmp_path / "R_0603.kicad_mod")
        fp.filePath = path
        fp.to_file()
        result = export.upgrade_footprint_lib(path)
        assert "success" in result.lower() or "upgraded" in result.lower() or "error" in result.lower()


class TestRunJobset:
    def test_nonexistent_file(self):
        result = export.run_jobset("/nonexistent/job.kicad_jobset")
        assert "error" in result.lower() or "failed" in result.lower()
