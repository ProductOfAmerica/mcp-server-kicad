"""Tests for IPC-2581 export tool."""

from pathlib import Path

import pytest
from conftest import HAS_KICAD_CLI

from mcp_server_kicad import pcb

pytestmark = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")


class TestExportIpc2581:
    def test_export_runs(self, scratch_pcb, tmp_path):
        result = pcb.export_ipc2581(
            pcb_path=str(scratch_pcb),
            output=str(tmp_path / "board.xml"),
        )
        assert result.path

    def test_with_precision(self, scratch_pcb, tmp_path):
        result = pcb.export_ipc2581(
            pcb_path=str(scratch_pcb),
            output=str(tmp_path / "board_p6.xml"),
            precision=6,
        )
        assert result.path

    def test_with_compress(self, scratch_pcb, tmp_path):
        result = pcb.export_ipc2581(
            pcb_path=str(scratch_pcb),
            output=str(tmp_path / "board_c.xml"),
            compress=True,
        )
        assert result.path


class TestDefaultLocation:
    """Where the file goes when no output is named: the other exports' rule, which is the
    configured output directory, else the directory of the design."""

    def test_lands_beside_the_board_when_no_output_dir_is_configured(
        self, scratch_pcb, tmp_path, monkeypatch
    ):
        """Bug 11: with no output directory configured the default was a bare file name,
        so kicad-cli wrote it into the server process's working directory."""
        server_cwd = tmp_path / "server-cwd"
        server_cwd.mkdir()
        monkeypatch.setattr(pcb, "OUTPUT_DIR", "")
        monkeypatch.chdir(server_cwd)

        result = pcb.export_ipc2581(pcb_path=str(scratch_pcb))

        assert Path(result.path).parent == scratch_pcb.parent.resolve()
        assert Path(result.path).is_file()
        assert list(server_cwd.iterdir()) == []

    def test_lands_where_a_sibling_export_with_the_same_arguments_lands(
        self, scratch_pcb, tmp_path, monkeypatch
    ):
        """export_positions is the rule's other half: no output_dir given and none
        configured puts its file beside the board."""
        server_cwd = tmp_path / "server-cwd"
        server_cwd.mkdir()
        monkeypatch.setattr(pcb, "OUTPUT_DIR", "")
        monkeypatch.chdir(server_cwd)
        sibling = pcb.export_positions(pcb_path=str(scratch_pcb), output_dir="")
        result = pcb.export_ipc2581(pcb_path=str(scratch_pcb))
        assert Path(result.path).parent == Path(sibling.path).parent

    def test_lands_in_the_configured_output_dir(self, scratch_pcb, tmp_path, monkeypatch):
        configured = tmp_path / "configured"
        configured.mkdir()
        monkeypatch.setattr(pcb, "OUTPUT_DIR", str(configured))
        result = pcb.export_ipc2581(pcb_path=str(scratch_pcb))
        assert Path(result.path).parent == configured.resolve()

    def test_a_named_output_wins(self, scratch_pcb, tmp_path, monkeypatch):
        monkeypatch.setattr(pcb, "OUTPUT_DIR", str(tmp_path / "configured"))
        named = tmp_path / "named.xml"
        result = pcb.export_ipc2581(pcb_path=str(scratch_pcb), output=str(named))
        assert Path(result.path) == named.resolve()
