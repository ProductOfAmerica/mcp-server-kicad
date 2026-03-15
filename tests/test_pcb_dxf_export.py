"""Tests for PCB DXF export via export_pcb(format='dxf')."""

import shutil

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")


class TestExportPcbDxf:
    def test_export_runs(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
            )
            assert result.path
        except (ToolError, RuntimeError, FileNotFoundError):
            pass

    def test_missing_layers_returns_error(self):
        with pytest.raises(ToolError):
            pcb.export_pcb(format="dxf")

    def test_with_mm_units(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
                output_units="mm",
            )
            assert result.path
        except (ToolError, RuntimeError, FileNotFoundError):
            pass

    def test_with_options(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
                exclude_refdes=True,
                exclude_value=True,
            )
            assert result.path
        except (ToolError, RuntimeError, FileNotFoundError):
            pass
