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


class TestAddFamilyPreservation:
    """Slice 3: global_label, hierarchical_label, text, junctions via the same splice."""

    def test_global_label(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_global_label(
            "GNET", 60, 90, rotation=90, shape="bidirectional", schematic_path=str(kicad_native_sch)
        )
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        gl = next(g for g in reparse(kicad_native_sch).globalLabels if g.text == "GNET")
        assert gl.shape == "bidirectional"
        assert gl.position.angle == 90

    def test_hierarchical_label(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_hierarchical_label(
            "HNET", "output", 25.4, 30, schematic_path=str(kicad_native_sch)
        )
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        hl = next(h for h in reparse(kicad_native_sch).hierarchicalLabels if h.text == "HNET")
        assert hl.shape == "output"
        assert hl.position.X == pytest.approx(25.4)
        with pytest.raises(ToolError, match="invalid shape"):
            schematic.add_hierarchical_label(
                "Z", "sideways", 10, 10, schematic_path=str(kicad_native_sch)
            )

    def test_text(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_text("note here", 100.123456, 50, schematic_path=str(kicad_native_sch))
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        t = next(t for t in reparse(kicad_native_sch).texts if t.text == "note here")
        # add_text never rounded coordinates; the quirk is preserved.
        assert t.position.X == pytest.approx(100.123456)

    def test_junctions_two_points_one_write(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_junctions(
            [{"x": 50.8, "y": 50.8}, {"x": 96.19, "y": 100.5}],
            schematic_path=str(kicad_native_sch),
        )
        assert _pure_insertion(before, kicad_native_sch.read_bytes())
        sch = reparse(kicad_native_sch)
        assert len(sch.junctions) == 2
        # File order is list order: the last point supplied is junctions[-1].
        assert sch.junctions[-1].position.X == pytest.approx(96.19)

    def test_junctions_all_validated_before_any_write(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        with pytest.raises(ToolError, match="outside"):
            schematic.add_junctions(
                [{"x": 50, "y": 50}, {"x": 9999, "y": 50}],
                schematic_path=str(kicad_native_sch),
            )
        assert kicad_native_sch.read_bytes() == before


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

    @pytest.mark.no_kicad_validation
    def test_future_version_file_whole_add_family_works(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        schematic.add_global_label("G", 60, 90, schematic_path=p)
        schematic.add_hierarchical_label("H", "input", 60, 92, schematic_path=p)
        schematic.add_text("T", 60, 94, schematic_path=p)
        schematic.add_junctions([{"x": 60, "y": 96}], schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert len(after) > len(before)
        tree = _cst.parse(after)
        root = tree.lists[0]
        for token in ("global_label", "hierarchical_label", "text", "junction"):
            assert root.find(token) is not None, token


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

    def _mint(self, path):
        if _kicad_cli_major() < 10:
            pytest.skip("needs kicad-cli 10+ to mint a current-format schematic")
        _run_cli(["sch", "upgrade", "--force", str(path)])
        before = path.read_bytes()
        assert b"(version 2026" in before[:80], before[:80]
        return before

    def test_global_label_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_global_label("K10_G", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert _cst.parse(after).lists[0].find("global_label") is not None

    def test_hierarchical_label_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_hierarchical_label(
            "K10_H", "input", 60, 90, schematic_path=str(kicad_native_sch)
        )
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert _cst.parse(after).lists[0].find("hierarchical_label") is not None

    def test_text_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_text("K10 note", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert _cst.parse(after).lists[0].find("text") is not None

    def test_junctions_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_junctions([{"x": 60, "y": 90}], schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        assert _pure_insertion(before, after)
        assert _cst.parse(after).lists[0].find("junction") is not None
