"""Tests for CLI analysis tools (ERC, DRC)."""

import json
from pathlib import Path
from unittest.mock import patch

from conftest import requires_cli

from mcp_server_kicad import _cst, pcb, schematic


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
    def test_the_template_path_is_named_only_when_it_exists(self):
        """Layout-agnostic on purpose.

        This test used to assert the path is always named when kicad-cli
        resolves, and macOS failed it: KiCad's data lives in the .app bundle's
        SharedSupport, not share/kicad, so the file was not where the helper
        looked. The helper now tries both, the same pair _resolve_system_lib
        uses, but a packaging layout matching neither is still possible, and
        naming a path that is not there would be worse than omitting it.
        """
        from mcp_server_kicad._shared import _kicad_root

        note = schematic._sym_lib_table_note([{"type": "lib_symbol_issues"}])
        assert note is not None
        root = _kicad_root()
        found = next(
            (
                c
                for sub in ("share/kicad/template", "SharedSupport/template")
                if root and (c := root / sub / "sym-lib-table").is_file()
            ),
            None,
        )
        if found:
            assert str(found) in note, "a template that exists must be named"
        else:
            assert "ships one at" not in note, "no path may be named when none was found"


_FILLED_POLYGON = (
    '(filled_polygon (layer "F.Cu") (pts (xy 12 12) (xy 38 12) (xy 38 38) (xy 12 38)))'
)


def _fill_zone(board: Path) -> Path:
    """Splice a computed fill into the board's copper zone.

    The conftest builders make zone outlines and nothing else, and the only
    genuinely filled boards in the suite come from real pcbnew under
    requires_e2e. Same trick _bump_version uses, for the same reason: the test
    needs one construct present, not a second board builder.

    Through the CST rather than a regex, because the fixture carries a keepout
    zone as well and its outline comes first in the file: a text splice on the
    first "(polygon" filled the rule area and left the copper zone exactly as
    unfilled as before, so the test passed for the wrong reason.
    """
    tree = _cst.parse(board.read_bytes())
    root = tree.lists[0]
    copper = next(z for z in root.find_all("zone") if z.find("keepout") is None)
    copper.append_child(_cst.parse(_FILLED_POLYGON.encode()).lists[0], b"\n\t\t")
    board.write_bytes(_cst.serialize(tree))
    return board


class TestUnfilledZoneNote:
    """A copper zone stores its outline and its copper separately, and only the
    copper plots. kicad-cli plots what is stored and never refills, so an
    unfilled zone ships nothing at all, quietly.

    Measured on KiCad's own ecc83-pp: the F.Cu gerber is 54,448 bytes with the
    fills and 6,378 with only the (filled_polygon) stripped, both at exit 0 with
    empty stderr. KiCad's GUI warns about stale fills. The CLI does not, and the
    string filled_polygon appeared nowhere in this repo before now.
    """

    def _board(self, tmp_path, **kw):
        from test_pcb_read_tools import _make_keepout_board

        return _make_keepout_board(tmp_path, **kw)

    def test_a_board_with_no_zones_is_quiet(self, scratch_pcb):
        assert pcb._unfilled_zone_note(str(scratch_pcb)) is None

    def test_a_keepout_is_not_a_copper_zone(self, tmp_path):
        """The exclusion most likely to break silently: rule areas never fill."""
        board = self._board(tmp_path)
        assert pcb._unfilled_zone_note(str(board)) is None

    def test_an_unfilled_copper_zone_is_reported(self, tmp_path):
        board = self._board(tmp_path, with_copper_zone=True)
        note = pcb._unfilled_zone_note(str(board))
        assert note is not None
        assert "1 copper zone has" in note
        assert "fill_zones" in note

    def test_a_filled_zone_is_quiet(self, tmp_path):
        """Load-bearing. Without it, "warn if any zone exists" passes every
        other case in this class."""
        board = _fill_zone(self._board(tmp_path, with_copper_zone=True))
        assert pcb._unfilled_zone_note(str(board)) is None

    def test_two_unfilled_zones_pluralise(self, tmp_path):
        board = self._board(tmp_path, with_copper_zone=True)
        pcb.add_copper_zone(
            net_name="Net1",
            layer="B.Cu",
            corners=[{"x": 5, "y": 5}, {"x": 9, "y": 5}, {"x": 9, "y": 9}],
            pcb_path=str(board),
        )
        note = pcb._unfilled_zone_note(str(board))
        assert note is not None and "2 copper zones have" in note

    def test_the_drc_message_names_the_phantom_errors(self, tmp_path):
        """Different harm: the exports lose copper, DRC invents errors."""
        board = self._board(tmp_path, with_copper_zone=True)
        note = pcb._unfilled_zone_note(
            str(board), "DRC reads the stored fill, so it reports connectivity errors."
        )
        assert note is not None and "connectivity errors" in note
        assert "no copper" not in note

    def test_an_unparseable_board_is_silent_not_fatal(self, tmp_path):
        """Advisory: it must never become a failure mode of its own."""
        bad = tmp_path / "not-a-board.kicad_pcb"
        bad.write_bytes(b"(kicad_sch (version 1))")
        assert pcb._unfilled_zone_note(str(bad)) is None

    @requires_cli
    def test_export_gerbers_carries_it(self, tmp_path):
        """One wire-through, proving it reaches the model, rather than six.

        Pinned to a KiCad 9 CLI rather than taking whatever the machine has.
        This test asserted unconditionally until KiCad 10's --check-zones landed,
        at which point it was correct on a KiCad 9 developer machine and wrong on
        both KiCad 10 runners, where refilling means there is deliberately no
        note. The note is the KiCad 9 path now, so the test says which path it is
        testing instead of inheriting one.
        """
        board = self._board(tmp_path, with_copper_zone=True)
        with patch.object(pcb, "_kicad_cli_major", return_value=9):
            result = pcb.export_gerbers(pcb_path=str(board), output_dir=str(tmp_path / "g"))
        assert result.note is not None and "fill_zones" in result.note


class TestRefillOrNote:
    """KiCad 10 refills zones in memory before plotting and does not save the
    board. Measured on both KiCad 10.0.5 runners 2026-08-15 rather than taken
    from the docs: `pcb drc --refill-zones` without --save-board leaves the board
    byte-identical, and so does `pcb export gerbers --check-zones`. The probe's
    control ran --save-board on the same board and watched it gain
    filled_polygon, so it could tell "did not save" from "cannot see a save".

    The flag and the note are mutually exclusive, and the reason is not
    tidiness: with the flag passed the zones ARE filled, so a note reading "they
    contribute no copper to this output" is false rather than redundant. One
    helper returns exactly one of them.
    """

    def _board(self, tmp_path):
        from test_pcb_read_tools import _make_keepout_board

        return _make_keepout_board(tmp_path, with_copper_zone=True)

    def test_kicad_9_gets_the_note_and_no_flag(self, tmp_path):
        board = self._board(tmp_path)
        with patch.object(pcb, "_kicad_cli_major", return_value=9):
            args, note = pcb._refill_or_note("--check-zones", str(board))
        assert args == []
        assert note is not None and "fill_zones" in note

    def test_kicad_10_gets_the_flag_and_no_note(self, tmp_path):
        board = self._board(tmp_path)
        with patch.object(pcb, "_kicad_cli_major", return_value=10):
            args, note = pcb._refill_or_note("--check-zones", str(board))
        assert args == ["--check-zones"]
        assert note is None, "the zones are refilled, so the note would be false"

    def test_an_unreadable_version_keeps_the_old_behaviour(self, tmp_path):
        """None means no opinion. Same contract as pcbnew_major: a probe must
        not become a failure mode of its own."""
        board = self._board(tmp_path)
        with patch.object(pcb, "_kicad_cli_major", return_value=None):
            args, note = pcb._refill_or_note("--check-zones", str(board))
        assert args == []
        assert note is not None

    def test_a_filled_board_gets_neither_on_kicad_9(self, tmp_path):
        """Load-bearing: without it, "always note on 9" passes the cases above."""
        board = _fill_zone(self._board(tmp_path))
        with patch.object(pcb, "_kicad_cli_major", return_value=9):
            args, note = pcb._refill_or_note("--check-zones", str(board))
        assert args == [] and note is None


class TestRefillFlagReachesTheCommandLine:
    """A helper nobody wires up is worth nothing, and the argv is the only place
    the wiring shows. Each case captures the real argv through _run_cli.
    """

    def _capture(self, monkeypatch, major):
        seen = []

        def fake(args, check=True):
            seen.append(args)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(pcb, "_run_cli", fake)
        monkeypatch.setattr(pcb, "_kicad_cli_major", lambda: major)
        monkeypatch.setattr(pcb, "_file_meta", lambda p: {"path": str(p), "size_bytes": 1})
        return seen

    def test_drc_passes_refill_zones_on_kicad_10(self, monkeypatch, tmp_path, scratch_pcb):
        seen = self._capture(monkeypatch, 10)
        report = tmp_path / (Path(str(scratch_pcb)).stem + "-drc.json")
        report.write_text(json.dumps({"violations": [], "unconnected_items": []}))
        pcb.run_drc(str(scratch_pcb), str(tmp_path))
        assert "--refill-zones" in seen[0]
        # Not --check-zones: pcb drc does not accept that spelling at all.
        assert "--check-zones" not in seen[0]

    def test_drc_passes_nothing_on_kicad_9(self, monkeypatch, tmp_path, scratch_pcb):
        seen = self._capture(monkeypatch, 9)
        report = tmp_path / (Path(str(scratch_pcb)).stem + "-drc.json")
        report.write_text(json.dumps({"violations": [], "unconnected_items": []}))
        pcb.run_drc(str(scratch_pcb), str(tmp_path))
        assert not [a for a in seen[0] if a.startswith("--refill")]

    def test_gerbers_multi_layer_passes_check_zones_on_kicad_10(
        self, monkeypatch, tmp_path, scratch_pcb
    ):
        seen = self._capture(monkeypatch, 10)
        pcb.export_gerbers(pcb_path=str(scratch_pcb), output_dir=str(tmp_path), include_drill=True)
        assert "--check-zones" in seen[0]
        # The drill call is a second invocation and takes no such flag.
        assert "drill" in seen[1] and "--check-zones" not in seen[1]

    def test_export_pcb_pdf_passes_check_zones_on_kicad_10(
        self, monkeypatch, tmp_path, scratch_pcb
    ):
        seen = self._capture(monkeypatch, 10)
        pcb.export_pcb(format="pdf", pcb_path=str(scratch_pcb), output_dir=str(tmp_path))
        assert "--check-zones" in seen[0]

    def test_ipc2581_never_gets_the_flag(self, monkeypatch, tmp_path, scratch_pcb):
        """KiCad 10 offers --check-zones on dxf, gerbers, pdf, ps and svg only.
        Passing it to ipc2581 is an unrecognized argument, so this export keeps
        the note on every version."""
        seen = self._capture(monkeypatch, 10)
        result = pcb.export_ipc2581(pcb_path=str(scratch_pcb), output=str(tmp_path / "board.xml"))
        assert "--check-zones" not in seen[0]
        assert result.note is None or "fill_zones" in result.note
