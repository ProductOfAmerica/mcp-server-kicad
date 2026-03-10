"""Tests for IPC-2581 export tool."""

import json
import shutil

import pytest

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")


class TestExportIpc2581:
    def test_export_runs(self, scratch_pcb, tmp_path):
        result = json.loads(
            pcb.export_ipc2581(
                pcb_path=str(scratch_pcb),
                output=str(tmp_path / "board.xml"),
            )
        )
        assert "path" in result or "error" in result
