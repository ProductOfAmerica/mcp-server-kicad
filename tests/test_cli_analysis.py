"""Tests for CLI analysis tools (ERC, DRC)."""

import json
from pathlib import Path
from unittest.mock import patch

from conftest import requires_cli

from mcp_server_kicad import pcb, schematic


@requires_cli
class TestRunErc:
    def test_clean_schematic(self, scratch_sch, tmp_path):
        result = schematic.run_erc(str(scratch_sch), str(tmp_path))
        assert hasattr(result, "violations")

    def test_returns_structured(self, scratch_sch, tmp_path):
        result = schematic.run_erc(str(scratch_sch), str(tmp_path))
        assert hasattr(result, "source")
        assert hasattr(result, "kicad_version")


class TestRunDrc:
    @requires_cli
    def test_clean_board(self, scratch_pcb, tmp_path):
        result = pcb.run_drc(str(scratch_pcb), str(tmp_path))
        assert hasattr(result, "violation_count")

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


class TestSymLibTableNote:
    """KiCad's library warning names neither the missing file nor its home.

    Measured 2026-08-12 on a Windows install where fp-lib-table had been copied
    into the user config on KiCad's first run and sym-lib-table had not: every
    ERC of every project warned, and a model reading it could only say "update
    your sym-lib-table" without saying which file or where.
    """

    def test_no_note_when_there_are_no_library_violations(self):
        assert schematic._sym_lib_table_note([]) is None
        assert schematic._sym_lib_table_note([{"type": "pin_not_connected"}]) is None

    def test_note_explains_the_missing_table(self):
        note = schematic._sym_lib_table_note([{"type": "lib_symbol_issues"}])
        assert note is not None
        assert "sym-lib-table" in note
        # The remedy, not just the symptom: placement is fine, ERC is not.
        assert "embedded in the schematic" in note

    @requires_cli
    def test_note_names_the_stock_template_kicad_ships(self):
        note = schematic._sym_lib_table_note([{"type": "lib_symbol_issues"}])
        assert note is not None and "template" in note, (
            "with kicad-cli resolvable the note should point at the file to copy"
        )
