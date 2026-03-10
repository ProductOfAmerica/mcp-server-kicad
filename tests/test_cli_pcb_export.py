"""Tests for CLI PCB export tools."""

import json
import shutil

import pytest

from mcp_server_kicad import pcb

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
pytestmark = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")


def _parse_result(result: str) -> dict:
    """Parse tool result as JSON, handling both success and error cases."""
    return json.loads(result)


class TestExportGerbers:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_gerbers(str(scratch_pcb), str(tmp_path / "gerbers"))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportGerber:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_gerber(str(scratch_pcb), "F.Cu", str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportPcbPdf:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_pcb(
            format="pdf",
            pcb_path=str(scratch_pcb),
            output_dir=str(tmp_path),
            layers=["F.Cu"],
        )
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportPcbSvg:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_pcb(
            format="svg",
            pcb_path=str(scratch_pcb),
            output_dir=str(tmp_path),
            layers=["F.Cu"],
        )
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportPositions:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_positions(str(scratch_pcb), str(tmp_path))
        data = _parse_result(result)
        assert "path" in data or "error" in data


class TestExportStep:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_3d(format="step", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportStl:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_3d(format="stl", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestExportGlb:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.export_3d(format="glb", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data


class TestRender3d:
    def test_returns_json(self, scratch_pcb, tmp_path):
        result = pcb.render_3d(str(scratch_pcb), str(tmp_path))
        data = _parse_result(result)
        assert "format" in data or "error" in data
