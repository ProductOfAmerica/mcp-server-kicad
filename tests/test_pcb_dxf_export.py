"""Tests for PCB DXF export via export_pcb(format='dxf')."""

import pytest
from conftest import requires_cli
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import pcb


class TestExportPcbDxf:
    @requires_cli
    def test_export_runs(self, scratch_pcb, tmp_path):
        result = pcb.export_pcb(
            format="dxf",
            pcb_path=str(scratch_pcb),
            output_dir=str(tmp_path),
            layers=["F.Cu"],
        )
        assert result.path

    def test_missing_layers_returns_error(self):
        with pytest.raises(ToolError):
            pcb.export_pcb(format="dxf")

    @requires_cli
    def test_with_mm_units(self, scratch_pcb, tmp_path):
        result = pcb.export_pcb(
            format="dxf",
            pcb_path=str(scratch_pcb),
            output_dir=str(tmp_path),
            layers=["F.Cu"],
            output_units="mm",
        )
        assert result.path

    @requires_cli
    def test_with_options(self, scratch_pcb, tmp_path):
        result = pcb.export_pcb(
            format="dxf",
            pcb_path=str(scratch_pcb),
            output_dir=str(tmp_path),
            layers=["F.Cu"],
            exclude_refdes=True,
            exclude_value=True,
        )
        assert result.path
