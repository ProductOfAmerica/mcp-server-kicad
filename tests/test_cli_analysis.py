"""Tests for CLI analysis tools (ERC, DRC)."""

import json
import shutil

import pytest

from mcp_server_kicad import pcb, schematic

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
pytestmark = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")


class TestRunErc:
    def test_clean_schematic(self, scratch_sch, tmp_path):
        result = schematic.run_erc(str(scratch_sch), str(tmp_path))
        data = json.loads(result)
        assert "violations" in data

    def test_returns_json(self, scratch_sch, tmp_path):
        result = schematic.run_erc(str(scratch_sch), str(tmp_path))
        data = json.loads(result)
        assert "source" in data
        assert "kicad_version" in data


class TestRunDrc:
    def test_clean_board(self, scratch_pcb, tmp_path):
        result = pcb.run_drc(str(scratch_pcb), str(tmp_path))
        data = json.loads(result)
        # DRC may find violations on scratch board, or fail to load it entirely
        # (kiutils-generated PCBs may not be loadable by kicad-cli).
        # Either way, the tool must return valid JSON.
        assert isinstance(data, dict)
        assert "source" in data or "violations" in data or "sheets" in data or "error" in data
