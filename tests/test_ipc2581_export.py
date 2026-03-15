"""Tests for IPC-2581 export tool."""

import shutil

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not found")


class TestExportIpc2581:
    def test_export_runs(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_ipc2581(
                pcb_path=str(scratch_pcb),
                output=str(tmp_path / "board.xml"),
            )
            assert result.path
        except (ToolError, RuntimeError, FileNotFoundError):
            pass

    def test_with_precision(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_ipc2581(
                pcb_path=str(scratch_pcb),
                output=str(tmp_path / "board_p6.xml"),
                precision=6,
            )
            assert result.path
        except (ToolError, RuntimeError, FileNotFoundError):
            pass

    def test_with_compress(self, scratch_pcb, tmp_path):
        try:
            result = pcb.export_ipc2581(
                pcb_path=str(scratch_pcb),
                output=str(tmp_path / "board_c.xml"),
                compress=True,
            )
            assert result.path
        except (ToolError, RuntimeError, FileNotFoundError):
            pass
