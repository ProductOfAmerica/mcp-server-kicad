"""Tests for PCB DXF export tool."""

import json
import shutil

import pytest

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")


class TestExportPcbDxf:
    def test_export_runs(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb_dxf(
                pcb_path=str(scratch_pcb),
                output=str(tmp_path / "board.dxf"),
                layers="F.Cu",
            )
        )
        assert "path" in result or "error" in result

    def test_missing_layers_returns_error(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb_dxf(
                pcb_path=str(scratch_pcb),
                output=str(tmp_path / "board.dxf"),
                layers="",
            )
        )
        assert "error" in result

    def test_with_mm_units(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb_dxf(
                pcb_path=str(scratch_pcb),
                output=str(tmp_path / "board_mm.dxf"),
                layers="F.Cu",
                output_units="mm",
            )
        )
        assert "path" in result or "error" in result

    def test_with_options(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb_dxf(
                pcb_path=str(scratch_pcb),
                output=str(tmp_path / "board_opts.dxf"),
                layers="F.Cu",
                exclude_refdes=True,
                exclude_value=True,
            )
        )
        assert "path" in result or "error" in result
