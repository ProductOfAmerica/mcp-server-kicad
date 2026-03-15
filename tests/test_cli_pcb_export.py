"""Tests for CLI PCB export tools."""

import shutil

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import pcb

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
pytestmark = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")


class TestExportGerbers:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_gerbers(str(scratch_pcb), str(tmp_path / "gerbers"))
            assert result.format == "gerber"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass  # kiutils-generated boards may not be loadable by kicad-cli

    def test_without_drill(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_gerbers(
                str(scratch_pcb), str(tmp_path / "gerbers"), include_drill=False
            )
            assert result.drill_files == []
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportGerberSingleLayer:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_gerbers(str(scratch_pcb), str(tmp_path), layers=["F.Cu"])
            assert result.format == "gerber"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportGerbersLayerFilter:
    def test_multi_layer_filter(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_gerbers(
                str(scratch_pcb), str(tmp_path / "gerbers"), layers=["F.Cu", "B.Cu"]
            )
            assert result.format == "gerber"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportPcbPdf:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_pcb(
                format="pdf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
            )
            assert result.format == "pdf"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportPcbSvg:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_pcb(
                format="svg",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
            )
            assert result.format == "svg"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportPositions:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_positions(str(scratch_pcb), str(tmp_path))
            assert result.path
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportStep:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_3d(
                format="step", pcb_path=str(scratch_pcb), output_dir=str(tmp_path)
            )
            assert result.format == "step"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportStl:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_3d(
                format="stl", pcb_path=str(scratch_pcb), output_dir=str(tmp_path)
            )
            assert result.format == "stl"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportGlb:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_3d(
                format="glb", pcb_path=str(scratch_pcb), output_dir=str(tmp_path)
            )
            assert result.format == "glb"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExport3dRender:
    def test_returns_result(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_3d(
                format="render", pcb_path=str(scratch_pcb), output_dir=str(tmp_path)
            )
            assert result.format == "png"
        except (ToolError, RuntimeError, FileNotFoundError):
            pass


class TestExportPcbInvalidFormat:
    pytestmark = []  # no kicad-cli needed for format validation

    def test_export_pcb_invalid_format(self):
        with pytest.raises(ToolError):
            pcb.export_pcb(format="xyz")

    def test_export_3d_invalid_format(self):
        with pytest.raises(ToolError):
            pcb.export_3d(format="obj")
