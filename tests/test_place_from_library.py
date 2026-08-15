"""Placing a real footprint, copied out of a .kicad_mod through the CST.

Until now place_footprint emitted _FOOTPRINT_TPL, a shell carrying a layer, a
uuid, an at and the Reference/Value properties and nothing else. No pads. A
board full of those looks right in a list tool and cannot be routed, checked or
manufactured, because there is no copper to connect to.

The transform from a library file to a board footprint was measured, not
reasoned about, against KiCad 9's own multichannel_mixer-unrouted: its
Potentiometer_Alps_RK09K_Single_Vertical is directly comparable with the stock
library file, 17 fp_line and 5 pad and 1 fp_circle on both sides, so the geometry
is identical and every remaining difference is the transform itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import requires_cli
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, pcb
from mcp_server_kicad._shared import _kicad_root

STOCK = next(
    (
        d
        for root in ([_kicad_root()] if _kicad_root() else [])
        for sub in ("share/kicad/footprints", "SharedSupport/footprints")
        if (d := root / sub).is_dir()
    ),
    None,
)
requires_stock = pytest.mark.skipif(STOCK is None, reason="no stock footprint libraries")

LIB = "Resistor_SMD"
FP = "R_0805_2012Metric"


def _uuids(node, out=None):
    """Every uuid in the subtree. find_all is direct children only."""
    out = [] if out is None else out
    for child in node.lists:
        if child.head == "uuid" and len(child.atoms) > 1:
            out.append(child.atoms[1].text)
        else:
            _uuids(child, out)
    return out


def _place(board: Path, **kw):
    # R99, not R1: scratch_pcb already ships a footprint referenced R1, and
    # _placed would find the fixture's rather than ours.
    args = {"reference": "R99", "value": "10k", "x": 50, "y": 50, "pcb_path": str(board)}
    return pcb.place_footprint(**{**args, **kw})


def _footprints(board: Path):
    return _cst.parse(board.read_bytes()).lists[0].find_all("footprint")


def _placed(board: Path, reference: str = "R99"):
    """The footprint we just placed. scratch_pcb is not an empty board: it ships
    footprints of its own, so selecting by reference beats taking the only one."""
    for fp in _footprints(board):
        if any(
            p.atoms[1].text == "Reference" and p.atoms[2].text == reference
            for p in fp.find_all("property")
        ):
            return fp
    raise AssertionError(f"no footprint with Reference {reference!r} on the board")


@requires_stock
class TestCopyLibFootprint:
    def _copy(self, name=FP, fpid=f"{LIB}:{FP}"):
        return pcb._copy_lib_footprint_cst(str(STOCK / f"{LIB}.pretty"), name, fpid)

    def test_the_pads_come_with_it(self):
        """The whole point. The old template had none."""
        node = self._copy()
        assert len(node.find_all("pad")) == 2

    def test_the_library_stamps_are_dropped(self):
        """A board carries one version stamp for the whole document. Copying the
        library's in would put a second, stale one inside a footprint."""
        node = self._copy()
        for head in ("version", "generator", "generator_version"):
            assert node.find_all(head) == [], f"{head} survived the copy"

    def test_the_name_becomes_a_library_id(self):
        assert self._copy().atoms[1].text == f"{LIB}:{FP}"

    def test_it_gains_a_uuid_and_an_at_after_the_layer(self):
        """Order measured from KiCad's own board: layer, uuid, at."""
        heads = [c.head for c in self._copy().lists]
        assert heads[:3] == ["layer", "uuid", "at"]

    def test_the_root_uuid_is_not_the_placeholder(self):
        """Caught in review, and it is the failure mode this whole file exists
        for: the template ships the literal "x", so every footprint placed
        carried the same uuid, and kicad-cli loaded a board with two identical
        ones at rc 0 without a word."""
        node = self._copy()
        assert node.find("uuid").atoms[1].text != "x"
        assert len(node.find("uuid").atoms[1].text) == 36

    def test_two_copies_share_no_uuid(self):
        """Library footprints already carry uuids on properties and pads, so a
        straight copy would put the same one on two different objects, and a
        uuid is what KiCad matches a board object back to its symbol by.
        Potentiometer rather than the resistor because that file HAS them: 29
        per copy, where R_0805 has none and would pass vacuously."""
        pot = "Potentiometer_Alps_RK09K_Single_Vertical"
        a = pcb._copy_lib_footprint_cst(str(STOCK / "Potentiometer_THT.pretty"), pot, "L:N")
        b = pcb._copy_lib_footprint_cst(str(STOCK / "Potentiometer_THT.pretty"), pot, "L:N")
        ua, ub = _uuids(a), _uuids(b)
        assert len(ua) > 5, "this library was supposed to carry uuids; pick another"
        assert len(set(ua)) == len(ua), "duplicate uuid inside one copy"
        assert not set(ua) & set(ub), "two placements share a uuid"

    def test_a_missing_footprint_is_None_not_an_exception(self):
        assert self._copy(name="NoSuchFootprint") is None

    def test_the_whole_stock_corpus_survives_the_transform(self):
        """A sweep, because the transform is shape-sensitive and the stock
        libraries are the shapes users actually place. Parse and transform only,
        no board writes: what this catches is a footprint whose structure the
        copy cannot handle, and that is decided before anything reaches a file.
        """
        files = sorted(STOCK.glob("*.pretty/*.kicad_mod"))
        assert len(files) > 500, f"only {len(files)} stock footprints; is this a real install?"
        failed = []
        for path in files:
            try:
                node = pcb._copy_lib_footprint_cst(str(path.parent), path.stem, "L:N")
                assert node is not None and node.find("uuid") is not None
            except Exception as exc:  # noqa: BLE001 - the sweep reports, it does not raise
                failed.append(f"{path.parent.name}/{path.name}: {exc}")
        assert not failed, f"{len(failed)} of {len(files)} failed:\n" + "\n".join(failed[:10])


@requires_stock
class TestPlaceFootprintFromLibrary:
    def test_the_board_gains_a_footprint_with_pads(self, scratch_pcb):
        _place(scratch_pcb, library=LIB, footprint=FP)
        fp = _placed(scratch_pcb)
        assert len(fp.find_all("pad")) == 2
        assert fp.atoms[1].text == f"{LIB}:{FP}"

    def test_the_marker_path_still_works_and_has_no_pads(self, scratch_pcb):
        """The older behaviour, kept for callers that only want a placeholder."""
        _place(scratch_pcb)
        fp = _placed(scratch_pcb)
        assert fp.find_all("pad") == []

    def test_reference_and_value_are_set(self, scratch_pcb):
        _place(scratch_pcb, reference="R7", value="4k7", library=LIB, footprint=FP)
        fp = _placed(scratch_pcb, "R7")
        props = {p.atoms[1].text: p.atoms[2].text for p in fp.find_all("property")}
        assert props["Reference"] == "R7"
        assert props["Value"] == "4k7"

    def test_rotation_reaches_the_pads(self, scratch_pcb):
        """Measured on KiCad's own board: a pad on an unrotated footprint reads
        (at 0 0) and the same pad at 90 degrees reads (at 0 0 90). The pad keeps
        its local position and takes the footprint's angle."""
        _place(scratch_pcb, rotation=90, library=LIB, footprint=FP)
        fp = _placed(scratch_pcb)
        for pad in fp.find_all("pad"):
            assert pad.find("at").atoms[3].text == "90"

    def test_no_rotation_leaves_the_pads_alone(self, scratch_pcb):
        """Load-bearing. Without it, "always write the angle" passes the case
        above while emitting an explicit 0 that no KiCad file carries."""
        _place(scratch_pcb, rotation=0, library=LIB, footprint=FP)
        fp = _placed(scratch_pcb)
        for pad in fp.find_all("pad"):
            assert len(pad.find("at").atoms) == 3, "an unrotated pad gained an angle"

    def test_two_placements_share_no_uuid(self, scratch_pcb):
        _place(scratch_pcb, reference="R98", library=LIB, footprint=FP)
        _place(scratch_pcb, reference="R99", x=70, library=LIB, footprint=FP)
        a, b = _placed(scratch_pcb, "R98"), _placed(scratch_pcb, "R99")
        assert _uuids(a) and not set(_uuids(a)) & set(_uuids(b))

    def test_placing_is_a_pure_insertion(self, scratch_pcb):
        """The governing invariant: bytes the caller did not ask to change reach
        the disk unchanged."""
        from conftest import _pure_insertion

        before = scratch_pcb.read_bytes()
        _place(scratch_pcb, library=LIB, footprint=FP)
        _pure_insertion(before, scratch_pcb.read_bytes())

    def test_an_unknown_library_names_where_it_looked(self, scratch_pcb):
        with pytest.raises(ToolError) as exc:
            _place(scratch_pcb, library="NoSuchLibrary", footprint=FP)
        assert "NoSuchLibrary.pretty" in str(exc.value)
        assert "Looked for" in str(exc.value)

    def test_an_unknown_footprint_points_at_the_list_tool(self, scratch_pcb):
        with pytest.raises(ToolError) as exc:
            _place(scratch_pcb, library=LIB, footprint="NoSuchFootprint")
        assert "list_lib_footprints" in str(exc.value)

    @pytest.mark.parametrize("kw", [{"library": LIB}, {"footprint": FP}])
    def test_half_a_pair_is_refused(self, scratch_pcb, kw):
        """Naming one without the other is a typo, not a request for a marker."""
        with pytest.raises(ToolError) as exc:
            _place(scratch_pcb, **kw)
        assert "go together" in str(exc.value)

    def test_a_direct_pretty_path_works(self, scratch_pcb):
        """A library outside the search path is reachable by naming it."""
        _place(scratch_pcb, library=str(STOCK / f"{LIB}.pretty"), footprint=FP)
        fp = _placed(scratch_pcb)
        assert len(fp.find_all("pad")) == 2

    @requires_cli
    def test_kicad_reads_the_result(self, scratch_pcb):
        """The autouse oracle covers this too, but state it here: a footprint
        assembled wrongly is a file KiCad refuses, and that is the only check
        that knows KiCad's semantics rather than the grammar."""
        _place(scratch_pcb, rotation=90, library=LIB, footprint=FP)
        from mcp_server_kicad._shared import _run_cli

        out = scratch_pcb.parent / "drc.json"
        result = _run_cli(
            ["pcb", "drc", "--format", "json", "--output", str(out), str(scratch_pcb)],
            check=False,
        )
        assert result.returncode == 0, (result.stdout + result.stderr)[:400]
