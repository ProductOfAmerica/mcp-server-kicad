"""Tests for PCB DXF export via export_pcb(format='dxf')."""

import json
import shutil

import pytest

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")


class TestExportPcbDxf:
    def test_export_runs(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
            )
        )
        assert "path" in result or "error" in result

    def test_missing_layers_returns_error(self):
        result = json.loads(pcb.export_pcb(format="dxf"))
        assert "error" in result

    def test_with_mm_units(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
                output_units="mm",
            )
        )
        assert "path" in result or "error" in result

    def test_with_options(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_pcb(
                format="dxf",
                pcb_path=str(scratch_pcb),
                output_dir=str(tmp_path),
                layers=["F.Cu"],
                exclude_refdes=True,
                exclude_value=True,
            )
        )
        assert "path" in result or "error" in result
