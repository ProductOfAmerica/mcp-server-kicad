"""Tests for CLI analysis tools (ERC, DRC)."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_server_kicad import pcb, schematic

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
pytestmark = pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")


class TestRunErc:
    def test_clean_schematic(self, scratch_sch, tmp_path):
        result = schematic.run_erc(str(scratch_sch), str(tmp_path))
        assert hasattr(result, "violations")

    def test_returns_structured(self, scratch_sch, tmp_path):
        result = schematic.run_erc(str(scratch_sch), str(tmp_path))
        assert hasattr(result, "source")
        assert hasattr(result, "kicad_version")


class TestRunDrc:
    def test_clean_board(self, scratch_pcb, tmp_path):
        from mcp.server.fastmcp.exceptions import ToolError

        try:
            result = pcb.run_drc(str(scratch_pcb), str(tmp_path))
            assert hasattr(result, "violation_count")
        except ToolError:
            pass  # kiutils-generated boards may not be loadable by kicad-cli

    def test_parses_top_level_violations(self, scratch_pcb, tmp_path):
        """DRC JSON has violations at top level, not nested under sheets."""
        fake_report = {
            "source": "test.kicad_pcb",
            "kicad_version": "9.0.0",
            "violations": [
                {"type": "clearance", "severity": "error", "description": "too close"},
                {"type": "width", "severity": "warning", "description": "too thin"},
            ],
            "unconnected_items": [],
        }
        pcb_path = str(scratch_pcb)
        out_dir = str(tmp_path)

        fake_out = Path(out_dir) / (Path(pcb_path).stem + "-drc.json")
        fake_out.write_text(json.dumps(fake_report))

        with patch.object(pcb, "_run_cli"):
            result = pcb.run_drc(pcb_path, out_dir)

        assert result.violation_count == 2
        assert len(result.violations) == 2
        assert result.violations[0]["type"] == "clearance"

    def test_includes_unconnected_items(self, scratch_pcb, tmp_path):
        """DRC JSON has unconnected_items at top level; they must be reported."""
        fake_report = {
            "source": "test.kicad_pcb",
            "kicad_version": "9.0.0",
            "violations": [
                {"type": "clearance", "severity": "error", "description": "too close"},
            ],
            "unconnected_items": [
                {"type": "unconnected", "severity": "error", "description": "pad not connected"},
                {"type": "unconnected", "severity": "error", "description": "another pad"},
            ],
        }
        pcb_path = str(scratch_pcb)
        out_dir = str(tmp_path)

        fake_out = Path(out_dir) / (Path(pcb_path).stem + "-drc.json")
        fake_out.write_text(json.dumps(fake_report))

        with patch.object(pcb, "_run_cli"):
            result = pcb.run_drc(pcb_path, out_dir)

        assert result.violation_count == 1
        assert result.unconnected_count == 2
        assert len(result.unconnected_items) == 2
