"""Tests for the byte-preserving CST substrate and its first consumer, add_label.

Decision record: docs/adr-cst-substrate.md. The property under test is the
substrate invariant: bytes the caller did not edit reach the disk unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import reparse, requires_cli
from mcp.server.fastmcp.exceptions import ToolError

from mcp_server_kicad import _cst, schematic
from mcp_server_kicad._shared import _resolve_system_lib, _run_cli

FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"


def _pure_insertion(before: bytes, after: bytes) -> bool:
    """True if *after* is *before* with one contiguous run of bytes inserted."""
    if len(after) < len(before):
        return False
    p = 0
    while p < len(before) and before[p] == after[p]:
        p += 1
    s = 0
    while s < len(before) - p and before[-1 - s] == after[-1 - s]:
        s += 1
    return p + s >= len(before)


class TestRoundTrip:
    def test_native_fixture(self):
        data = FIXTURE.read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data

    def test_kiutils_written_file(self, scratch_sch):
        data = Path(scratch_sch).read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data

    @pytest.mark.skipif(
        _resolve_system_lib("Device") is None, reason="KiCad system libraries not installed"
    )
    def test_stock_device_library(self):
        lib = _resolve_system_lib("Device")
        assert lib is not None
        data = Path(lib).read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data

    def test_self_check(self):
        _cst.demo()  # escape codec, edits, splices, malformed refusal


class TestMalformed:
    def test_unmatched_close_raises(self):
        with pytest.raises(SyntaxError):
            _cst.parse(b"(kicad_sch))")

    def test_unclosed_open_raises(self):
        with pytest.raises(SyntaxError):
            _cst.parse(b"(kicad_sch (paper")


class TestAddLabelPreservation:
    def test_pure_insertion_and_unmodeled_tokens_survive(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        # Today's kiutils save path drops both of these tokens; the CST path
        # is the reason they survive. This assertion is the slice's point.
        assert b"(embedded_fonts" in before and b"generator_version" in before
        schematic.add_label("CST_NET", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert b"(embedded_fonts" in after and b"generator_version" in after
        sch = reparse(kicad_native_sch)
        assert "CST_NET" in [lbl.text for lbl in sch.labels]

    def test_hostile_text_roundtrips(self, kicad_native_sch):
        hostile = 'A(B "q" \\end'
        schematic.add_label(hostile, 60, 90, schematic_path=str(kicad_native_sch))
        sch = reparse(kicad_native_sch)
        # kiutils 1.4.8 does not decode \\ and \" the way KiCad does, so assert
        # via the CST's own KiCad-faithful decoder plus kiutils label count.
        tree = _cst.parse(kicad_native_sch.read_bytes())
        texts = [n.atoms[1].text for n in tree.lists[0].find_all("label")]
        assert hostile in texts
        assert len(sch.labels) == 2  # original TEST_NET + the hostile one

    def test_anchor_fallback_no_labels(self, empty_sch):
        schematic.add_label("FIRST", 50, 50, schematic_path=str(empty_sch))
        sch = reparse(empty_sch)
        assert "FIRST" in [lbl.text for lbl in sch.labels]

    def test_rotation_and_position_survive_kiutils(self, kicad_native_sch):
        schematic.add_label("ROT", 96.19, 100.5, rotation=90, schematic_path=str(kicad_native_sch))
        sch = reparse(kicad_native_sch)
        lbl = next(x for x in sch.labels if x.text == "ROT")
        assert lbl.position.X == pytest.approx(96.19)
        assert lbl.position.Y == pytest.approx(100.5)
        assert lbl.position.angle == 90


def _kicad_cli_major() -> int:
    result = _run_cli(["version"], check=False)
    try:
        return int(result.stdout.strip().split(".")[0])
    except (ValueError, IndexError):
        return 0


class TestGuardRelaxAddLabelOnly:
    @pytest.mark.no_kicad_validation
    def test_future_version_file_add_label_works_other_tools_refuse(self, kicad_native_sch):
        # Simulate a KiCad-10-saved schematic: bump only the version claim.
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        with pytest.raises(ToolError, match="newer than the KiCad 9 formats"):
            schematic.get_schematic_summary(schematic_path=str(kicad_native_sch))
        before = kicad_native_sch.read_bytes()
        schematic.add_label("RELAXED", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        tree = _cst.parse(after)
        assert "RELAXED" in [n.atoms[1].text for n in tree.lists[0].find_all("label")]


@requires_cli
class TestKicad10E2E:
    def test_add_label_on_real_kicad10_schematic(self, kicad_native_sch):
        if _kicad_cli_major() < 10:
            pytest.skip("needs kicad-cli 10+ to mint a current-format schematic")
        _run_cli(["sch", "upgrade", "--force", str(kicad_native_sch)])
        before = kicad_native_sch.read_bytes()
        assert b"(version 2026" in before[:80], before[:80]
        schematic.add_label("K10_NET", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        tree = _cst.parse(after)
        assert "K10_NET" in [n.atoms[1].text for n in tree.lists[0].find_all("label")]
        # The autouse _validate_kicad_output fixture then runs this runner's
        # kicad-cli ERC over the edited KiCad-10 file: the real acceptance gate.
