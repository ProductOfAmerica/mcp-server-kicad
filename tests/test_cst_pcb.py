"""Tests for the CST board substrate: guard-free reads, cache, first writers.

Contract net for output shapes stays in test_pcb_read_tools.py and
test_pcb_write_tools.py; these tests pin the substrate properties:
byte preservation, KiCad 10 readability, cache behavior. What a writer changed
is judged by tests/edit_judge.py on KiCad's own lexer, never on the CST.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import edit_gen as E
import edit_judge as J
import footprint_judge as FJ
import geom_gen as G
import geom_judge as GJ
import pytest
from conftest import requires_cli
from kicad_ref import dsn_lexer as L
from kicad_ref import footprint as F
from kicad_ref import pad as PAD
from kicad_ref import parse_units as P
from kicad_ref import trigo
from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Net, Position
from kiutils.items.gritems import GrArc, GrLine
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, pcb
from mcp_server_kicad._shared import _gen_uuid
from mcp_server_kicad.models import BoardValidationResult, PlacementCheckResult, PointSpec

# KiCad-9-native-format board (quoted generator, uuid tokens), the dialect
# kicad-cli 9 writes; shape shared with test_pcb_write_tools.py, plus one
# footprint and via so every read tool has a subject.
_K9_BOARD = """(kicad_pcb (version 20241108) (generator "pcbnew")

  (general
    (thickness 1.6)
  )

  (paper "A4")

  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (44 "Edge.Cuts" user)
    (48 "B.Fab" user)
    (49 "F.Fab" user)
  )

  (setup
    (pad_to_mask_clearance 0)
  )

  (net 0 "")
  (net 1 "Net1")
  (net 2 "Net2")

  (footprint "Resistor_SMD:R_0603" (layer "F.Cu")
    (at 100 100 0)
    (uuid "fp-0001")
    (property "Reference" "R1")
    (property "Value" "10K")
    (pad "1" smd rect (at -0.75 0) (size 0.7 0.8) (layers "F.Cu" "F.Paste" "F.Mask")
      (net 1 "Net1"))
    (pad "2" smd rect (at 0.75 0) (size 0.7 0.8) (layers "F.Cu" "F.Paste" "F.Mask")
      (net 2 "Net2"))
  )

  (gr_line (start 0 0) (end 100 0) (layer "Edge.Cuts") (width 0.05) (uuid "gl-0001"))

  (segment (start 100 100) (end 110 100) (width 0.25)
    (layer "F.Cu") (net 1) (uuid "aaa-1111-2222"))

  (via (at 115 100) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "via-0001"))

)"""


# KiCad-10-native-format board, shape measured via the slice-13 probe on the
# K10 runner: no net table, no net numbers anywhere, items reference nets by
# name only, B.Cu ordinal is 2.
_K10_BOARD = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(36 "B.SilkS" user "B.Silkscreen")
\t\t(37 "F.SilkS" user "F.Silkscreen")
\t\t(44 "Edge.Cuts" user)
\t\t(48 "B.Fab" user)
\t\t(49 "F.Fab" user)
\t)
\t(footprint "Resistor_SMD:R_0603"
\t\t(layer "F.Cu")
\t\t(uuid "fp-0001")
\t\t(at 100 100)
\t\t(property "Reference" "R1"
\t\t\t(at 0 0 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(property "Value" "10K"
\t\t\t(at 0 2 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(pad "1" smd rect
\t\t\t(at -0.75 0)
\t\t\t(size 0.7 0.8)
\t\t\t(layers "F.Cu" "F.Paste" "F.Mask")
\t\t\t(net "/SIG")
\t\t)
\t\t(pad "2" smd rect
\t\t\t(at 0.75 0)
\t\t\t(size 0.7 0.8)
\t\t\t(layers "F.Cu" "F.Paste" "F.Mask")
\t\t\t(net "/GND")
\t\t)
\t)
\t(segment
\t\t(start 100 100)
\t\t(end 110 100)
\t\t(width 0.25)
\t\t(layer "F.Cu")
\t\t(net "/SIG")
\t\t(uuid "seg-0001")
\t)
)"""


def _write_board(tmp_path, content: str) -> str:
    p = tmp_path / "board.kicad_pcb"
    p.write_text(content)
    return str(p)


#: edit_judge's location of a board's root list: the path of every edit to a
#: top-level item (a footprint, a segment, a zone).
ROOT = ((0, "kicad_pcb"),)

MM = 1_000_000

_BUG2 = (
    "bug 2: place_footprint and move_footprint do not turn pad and text angles as KiCad's"
    " FOOTPRINT::Rotate does (a pad takes the footprint's angle instead of adding it to its"
    " own, a text keeps its library angle); fixed by step 7"
)
_BUG53 = (
    "bug 53: footprint angles are not spelled as KiCad writes them (the footprint's angle"
    " 270 for -90 and an explicit 0, a library angle copied as the library spells it);"
    " fixed by step 7"
)
_BUG_NONFINITE = (
    "bug 89: place_footprint and move_footprint let a non-finite rotation reach the"
    " caller as a raw OverflowError or ValueError instead of refusing it with a ToolError;"
    " fixed by step 7"
)


def _the(data: bytes, *labels: str, where=lambda location, item: True) -> J.Path:
    """The location of the one list in ``data`` whose labels, outermost first, are
    ``labels`` and that ``where(location, item)`` accepts. Raises LookupError, never
    AssertionError."""
    found = J.find_paths(
        data,
        lambda loc, item: (
            item.items is not None
            and [label for _, label in loc] == list(labels)
            and where(loc, item)
        ),
    )
    if len(found) != 1:
        raise LookupError(f"{len(found)} items match where one was expected: {found[:4]}")
    return found[0]


def _uuid_of(item) -> bytes | None:
    """The value of a list item's (uuid ...) child, as the lexer reads it."""
    return next((c.items[1].value for c in item.items if c.head == "uuid"), None)


class TestBoardRoundTrip:
    def test_scratch_pcb(self, scratch_pcb):
        data = Path(scratch_pcb).read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data

    def test_k9_native(self, tmp_path):
        p = _write_board(tmp_path, _K9_BOARD)
        data = Path(p).read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data


class TestBoardReadsCst:
    """The 8 converted read tools on the kiutils fixture dialect."""

    def test_scratch_reads(self, scratch_pcb):
        p = str(scratch_pcb)
        fps = pcb.list_pcb_footprints(p)
        assert [(f.reference, f.value, f.lib_id) for f in fps] == [
            ("R1", "10K", "Resistor_SMD:R_0603")
        ]
        assert (fps[0].x, fps[0].y, fps[0].layer) == (100.0, 100.0, "F.Cu")
        traces = pcb.list_pcb_traces(p)
        assert [(t.type, t.net) for t in traces] == [("segment", 1)]
        nets = pcb.list_pcb_nets(p)
        assert [(n.number, n.name) for n in nets] == [(1, "Net1"), (2, "Net2")]
        layers = pcb.list_pcb_layers(p)
        assert (layers[0].ordinal, layers[0].name, layers[0].type) == (0, "F.Cu", "signal")
        graphics = pcb.list_pcb_graphic_items(p)
        assert [(g.type, g.layer) for g in graphics] == [("line", "Edge.Cuts")]
        info = pcb.get_board_info(p)
        assert info == "Footprints: 1\nTraces: 1\nVias: 0\nNets: 3\nZones: 0\nThickness: 1.6mm"
        pads = pcb.get_footprint_pads("R1", p)
        assert "Pad 1: smd rect @ board (99.25, 100) local (-0.75, 0) size=(0.7, 0.8)" in pads
        assert "net=Net1" in pads and "net=Net2" in pads

    def test_unknown_footprint(self, scratch_pcb):
        with pytest.raises(ToolError, match="not found"):
            pcb.get_footprint_pads("R999", str(scratch_pcb))

    def test_kicad10_header_reads_work(self, tmp_path):
        """Converted reads no longer refuse post-KiCad-9 board versions."""
        p = _write_board(tmp_path, _K9_BOARD.replace("(version 20241108)", "(version 20260206)"))
        fps = pcb.list_pcb_footprints(p)
        assert [f.reference for f in fps] == ["R1"]
        traces = pcb.list_pcb_traces(p)
        assert [(t.type, t.net) for t in traces] == [("segment", 1), ("via", 1)]
        via = traces[1]
        assert (via.x, via.y, via.layers) == (115.0, 100.0, ["F.Cu", "B.Cu"])
        assert [n.name for n in pcb.list_pcb_nets(p)] == ["Net1", "Net2"]
        assert len(pcb.list_pcb_layers(p)) == 7
        assert [g.type for g in pcb.list_pcb_graphic_items(p)] == ["line"]
        info = pcb.get_board_info(p)
        assert "Footprints: 1" in info and "Vias: 1" in info
        pads = pcb.get_footprint_pads("R1", p)
        assert "net=Net1" in pads


class TestBoardCache:
    def test_hit_then_mtime_invalidates(self, scratch_pcb):
        p = str(scratch_pcb)
        t1, _, key = pcb._open_pcb_cst(p)
        t2, _, _ = pcb._open_pcb_cst(p)
        assert t2 is t1
        st = os.stat(key)
        os.utime(key, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        t3, _, _ = pcb._open_pcb_cst(p)
        assert t3 is not t1

    def test_external_rewrite_invalidates(self, scratch_pcb):
        """A non-CST rewrite (kiutils here, standing in for any external
        editor) moves mtime, so the next read re-parses."""
        p = str(scratch_pcb)
        pcb._open_pcb_cst(p)
        board = Board.from_file(p)
        for t in board.traceItems:
            if isinstance(t, Segment):
                t.width = 0.5
        board.to_file()
        traces = pcb.list_pcb_traces(p)
        assert traces[0].width == 0.5


class TestAddTraceViaCst:
    def test_scratch_pure_insertion_and_readback(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        pcb.add_trace(10, 20, 30, 20, net=1, pcb_path=p)
        mid = Path(p).read_bytes()
        (segment,) = J.assert_inserted(before, mid, heads=["segment"])
        assert segment.path == ROOT
        pcb.add_via(20, 20, net=1, pcb_path=p)
        after = Path(p).read_bytes()
        (via,) = J.assert_inserted(mid, after, heads=["via"])
        assert via.path == ROOT
        board = Board.from_file(p)
        assert sum(1 for t in board.traceItems if isinstance(t, Segment)) == 2
        assert sum(1 for t in board.traceItems if isinstance(t, Via)) == 1
        assert ("via", 1) in [(t.type, t.net) for t in pcb.list_pcb_traces(p)]

    def test_k9_native_numeric_net(self, tmp_path):
        p = _write_board(tmp_path, _K9_BOARD)
        before = Path(p).read_bytes()
        pcb.add_trace(50, 50, 60, 50, net=1, pcb_path=p)
        after = Path(p).read_bytes()
        (segment,) = J.assert_inserted(before, after, heads=["segment"])
        assert segment.path == ROOT
        i = after.index(b"(start 50 50)")
        span = after[i - 100 : i + 250]
        assert b"(net 1)" in span
        assert b'(net "Net1")' not in span

    def test_k10_named_net(self, tmp_path):
        p = _write_board(tmp_path, _K9_BOARD.replace("(version 20241108)", "(version 20260206)"))
        before = Path(p).read_bytes()
        pcb.add_trace(50, 50, 60, 50, net=1, pcb_path=p)
        after = Path(p).read_bytes()
        (segment,) = J.assert_inserted(before, after, heads=["segment"])
        assert segment.path == ROOT
        i = after.index(b"(start 50 50)")
        span = after[i - 100 : i + 250]
        assert b'(net "Net1")' in span
        pcb.add_via(55, 50, net=2, pcb_path=p)
        assert b'(net "Net2")' in Path(p).read_bytes()

    def test_k10_unknown_net_refuses_untouched(self, tmp_path):
        p = _write_board(tmp_path, _K9_BOARD.replace("(version 20241108)", "(version 20260206)"))
        pcb._open_pcb_cst(p)
        before = Path(p).read_bytes()
        with pytest.raises(ToolError, match="Net 9 not found"):
            pcb.add_trace(0, 0, 1, 1, net=9, pcb_path=p)
        assert Path(p).read_bytes() == before
        assert str(Path(p).resolve()) not in pcb._BOARD_CACHE

    def test_k10_tableless_derived_nets_and_emission(self, tmp_path):
        """The measured K10 shape: no net table, name-only references.

        Numbers are synthesized from document order, and add_trace resolves
        them back to names for emission.
        """
        p = _write_board(tmp_path, _K10_BOARD)
        assert _cst.serialize(_cst.parse(Path(p).read_bytes())) == Path(p).read_bytes()
        nets = pcb.list_pcb_nets(p)
        assert [(n.number, n.name) for n in nets] == [(1, "/SIG"), (2, "/GND")]
        traces = pcb.list_pcb_traces(p)
        assert [(t.type, t.net) for t in traces] == [("segment", 1)]
        pads = pcb.get_footprint_pads("R1", p)
        assert "net=/SIG" in pads and "net=/GND" in pads
        assert "Nets: 2" in pcb.get_board_info(p)

        before = Path(p).read_bytes()
        pcb.add_trace(50, 50, 60, 50, net=2, pcb_path=p)
        after = Path(p).read_bytes()
        (segment,) = J.assert_inserted(before, after, heads=["segment"])
        assert segment.path == ROOT
        i = after.index(b"(start 50 50)")
        assert b'(net "/GND")' in after[i - 100 : i + 250]
        assert [(t.type, t.net) for t in pcb.list_pcb_traces(p)] == [
            ("segment", 1),
            ("segment", 2),
        ]

    @requires_cli
    def test_drc_accepts_spliced_board(self, scratch_pcb):
        """KiCad 9 accepts (uuid ...) items inside a tstamp-era board: the
        live oracle for the always-uuid template choice."""
        p = str(scratch_pcb)
        pcb.add_trace(10, 20, 30, 20, net=1, pcb_path=p)
        pcb.add_via(20, 20, net=1, pcb_path=p)
        result = pcb.run_drc(pcb_path=p)
        assert result.violation_count >= 0


def _bump_version(path) -> str:
    """Text-swap a kiutils-written board's version token to the KiCad 10 format."""
    p = Path(path)
    text = p.read_text()
    assert "(version 20211014)" in text, "kiutils create-new version moved, fix the swap"
    p.write_text(text.replace("(version 20211014)", "(version 20260206)"))
    return str(p)


def _make_keepout_board_bumped(tmp_path):
    """The shared kiutils keepout board with its version text-swapped to KiCad 10."""
    from test_pcb_write_tools import _make_keepout_pcb

    return _bump_version(_make_keepout_pcb(tmp_path))


class TestFootprintWritersCst:
    def test_place_scratch(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.place_footprint("R2", "4.7K", 50, 60, rotation=90, layer="B.Cu", pcb_path=p)
        # The suffix is not decoration: naming no library places a marker with no
        # pads, and the result has to say which of the two the caller got.
        assert result == "Placed R2 (4.7K) at (50, 60) on B.Cu as a pad-less marker"
        (placed,) = J.assert_inserted(before, Path(p).read_bytes(), heads=["footprint"])
        assert placed.path == ROOT
        board = Board.from_file(p)
        assert "R2" in [fp.properties.get("Reference") for fp in board.footprints]
        r2 = next(f for f in pcb.list_pcb_footprints(p) if f.reference == "R2")
        assert (r2.x, r2.y, r2.rotation, r2.layer, r2.value) == (50.0, 60.0, 90.0, "B.Cu", "4.7K")

    def test_place_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.place_footprint("R2", "1K", 50, 60, pcb_path=p)
        (placed,) = J.assert_inserted(before, Path(p).read_bytes(), heads=["footprint"])
        assert placed.path == ROOT
        assert any(f.reference == "R2" for f in pcb.list_pcb_footprints(p))

    def test_move_k9(self, scratch_pcb):
        p = str(scratch_pcb)
        assert pcb.move_footprint("R1", 42, 24, pcb_path=p) == "Moved R1 to (42, 24)"
        r1 = next(f for f in pcb.list_pcb_footprints(p) if f.reference == "R1")
        assert (r1.x, r1.y) == (42.0, 24.0)
        assert Board.from_file(p).footprints[0].position.X == 42

    def test_move_k10_same_side_layer_confined(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.move_footprint("R1", 50, 50, layer="F.Cu", pcb_path=p)
        after = Path(p).read_bytes()
        # KiCad stores a footprint's pads and texts relative to the footprint, so a
        # translation on the same side is the footprint's own x and y and nothing more.
        at = _the(before, "kicad_pcb", "footprint", "at")
        assert J.atom_changes(before, after) == [
            J.AtomChange(at, 1, b"100", b"50"),
            J.AtomChange(at, 2, b"100", b"50"),
        ]
        r1 = next(f for f in pcb.list_pcb_footprints(p) if f.reference == "R1")
        assert (r1.x, r1.y, r1.layer) == (50.0, 50.0, "F.Cu")

    def test_moving_to_the_other_side_flips_it(self, tmp_path):
        """The transform is measured, not derived.

        Probed on the macOS runner against a stock Resistor_SMD footprint
        through pcbnew's own Flip(pos, FLIP_DIRECTION_LEFT_RIGHT). Before the
        flip existed this tool rewrote the footprint's layer atom and nothing
        else, so KiCad read a bottom-side part with top-side copper and
        kicad-cli loaded it without complaint.
        """
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.move_footprint("R1", 50, 50, layer="B.Cu", pcb_path=p)
        fp = _cst.parse(Path(p).read_bytes()).lists[0].find_all("footprint")[0]
        assert fp.find("layer").atoms[1].text == "B.Cu"
        # Nothing may be left on the front: that was the whole defect.
        raw = _cst.serialize(fp).decode()
        assert '"F.' not in raw, raw[:400]
        # Measured: the footprint's own angle becomes (180 - angle) % 360.
        assert [a.text for a in fp.find("at").atoms[1:]] == ["50", "50", "180"]

    def test_flipping_twice_returns_the_footprint_to_where_it_started(self, tmp_path):
        """The strongest check available without pcbnew: the transform is its
        own inverse, so a double flip is the identity on layers and geometry."""
        p = _write_board(tmp_path, _K10_BOARD)
        fp0 = _cst.parse(Path(p).read_bytes()).lists[0].find_all("footprint")[0]
        before = _cst.serialize(fp0)
        pcb.move_footprint("R1", 50, 50, layer="B.Cu", pcb_path=p)
        pcb.move_footprint("R1", 50, 50, layer="F.Cu", pcb_path=p)
        fp1 = _cst.parse(Path(p).read_bytes()).lists[0].find_all("footprint")[0]
        assert fp1.find("layer").atoms[1].text == "F.Cu"
        # Angle returns: 180 - (180 - 0) == 0.
        assert [a.text for a in fp1.find("at").atoms[1:]][:2] == ["50", "50"]
        assert b'"B.' not in _cst.serialize(fp1)
        assert b"justify" not in _cst.serialize(fp1) or b"justify" in before

    def test_local_geometry_mirrors_in_y(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        fp0 = _cst.parse(Path(p).read_bytes()).lists[0].find_all("footprint")[0]
        ys_before = [
            t.find("at").atoms[2].text
            for t in fp0.find_all("fp_text") + fp0.find_all("property")
            if t.find("at") is not None
        ]
        pcb.move_footprint("R1", 50, 50, layer="B.Cu", pcb_path=p)
        fp1 = _cst.parse(Path(p).read_bytes()).lists[0].find_all("footprint")[0]
        ys_after = [
            t.find("at").atoms[2].text
            for t in fp1.find_all("fp_text") + fp1.find_all("property")
            if t.find("at") is not None
        ]
        assert [float(v) for v in ys_after] == [-float(v) for v in ys_before]

    def test_text_gains_a_mirrored_justification(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.move_footprint("R1", 50, 50, layer="B.Cu", pcb_path=p)
        fp = _cst.parse(Path(p).read_bytes()).lists[0].find_all("footprint")[0]
        texts = [
            t
            for t in fp.find_all("fp_text") + fp.find_all("property")
            if t.find("effects") is not None
        ]
        assert texts, "fixture carries no text with effects; the check is vacuous"
        for t in texts:
            just = t.find("effects").find("justify")
            assert just is not None and "mirror" in [a.text for a in just.atoms[1:]]

    def test_an_unmeasured_construct_is_refused_with_the_file_intact(self, tmp_path):
        """A 3D model block's flip has not been measured, so mirroring
        everything around it and leaving it alone is refused instead."""
        p = _write_board(tmp_path, _K10_BOARD)
        tree = _cst.parse(Path(p).read_bytes())
        fp = tree.lists[0].find_all("footprint")[0]
        fp.append_child(_cst.parse(b'(model "x.step")').lists[0], b"\n\t\t")
        Path(p).write_bytes(_cst.serialize(tree))
        before = Path(p).read_bytes()
        with pytest.raises(ToolError) as exc:
            pcb.move_footprint("R1", 50, 50, layer="B.Cu", pcb_path=p)
        assert "model" in str(exc.value) and "Nothing was changed" in str(exc.value)
        assert Path(p).read_bytes() == before

    def test_move_k10_rotation_appends_atom(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.move_footprint("R1", 50, 50, rotation=90, pcb_path=p)
        after = Path(p).read_bytes()
        assert b"(at 50 50 90)" in after

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG2)
    def test_move_k10_rotation_turns_the_pads(self, tmp_path):
        """KiCad turns every pad with its footprint (FOOTPRINT::SetOrientation calls
        PAD::Rotate, which adds the change to the pad's absolute angle): R1's pads, written
        at -0.75 and 0.75 with no angle (0), read after a turn to 90 at 90, which KiCad
        writes (at -0.75 0 90) and (at 0.75 0 90) (kicad_ref.pad; pcbnew writes the same
        shape for R_0805). The positions stay footprint-relative. The tool leaves the pads
        at (at -0.75 0)."""
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.move_footprint("R1", 50, 50, rotation=90, pcb_path=p)
        group = FJ.footprint_group(Path(p).read_bytes(), "R1")
        pads = [
            [t.value.decode() for t in FJ.child(g, b"at").items[1:]] for g in F.pad_groups(group)
        ]
        turned = PAD.angle_token(PAD.rotate(PAD.loaded_angle(None), 90.0))
        assert pads == [["-0.75", "0", turned], ["0.75", "0", turned]]

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_NONFINITE)
    @pytest.mark.parametrize("version", ["k9", "k10"])
    @pytest.mark.parametrize(
        "rotation", [float("inf"), float("-inf"), float("nan")], ids=["inf", "-inf", "nan"]
    )
    @pytest.mark.parametrize("tool", ["place_footprint", "move_footprint"])
    def test_non_finite_rotation_is_refused(self, tmp_path, tool, rotation, version):
        """KiCad cannot load a footprint whose angle is inf, -inf or nan: the parser takes
        the angle only as a NUMBER token, and kicad-cli refuses the board
        (test_ladder_r3_judges.py's premise test). So the tools must refuse such a
        rotation with a ToolError and leave the board byte-identical. Today a raw
        OverflowError or ValueError reaches the caller, the file left intact."""
        board = tmp_path / "board.kicad_pcb"
        footprint = G.footprint_text("R1", (50 * MM, 40 * MM), version=version)
        board.write_text(G.board_text([footprint], version=version), newline="\n")
        before = board.read_bytes()
        try:
            if tool == "place_footprint":
                pcb.place_footprint("R2", "1k", 60, 40, rotation=rotation, pcb_path=str(board))
            else:
                pcb.move_footprint("R1", 60, 40, rotation=rotation, pcb_path=str(board))
        except ToolError:
            pass
        except Exception as exc:
            raise AssertionError(
                f"{tool} let {type(exc).__name__}: {exc} through for rotation {rotation}"
            ) from exc
        else:
            raise AssertionError(f"{tool} accepted the rotation {rotation}")
        assert board.read_bytes() == before

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG53)
    @pytest.mark.parametrize("version", ["k9", "k10"])
    def test_rotating_to_the_current_angle_changes_nothing(self, tmp_path, version):
        """A footprint at -90 whose keep-upright Reference stands at 270 (as
        SetOrientation, the Python API's path, leaves it), moved to its own position at
        rotation -90, 270 and -450: each names the angle the footprint already holds
        (Normalize180 gives -90 for all three), so nothing changes and the board stays
        byte-identical (the identity; FOOTPRINT::Rotate returns at once for a change of 0,
        pcbnew/footprint.cpp:2456-2459 at 9.0.8, 2898-2901 at 10.0.7). A KeepUpright pass
        nobody asked for would turn that Reference. Today 270 and -450 are written as
        given."""
        library = E.footprint_with_pad_at(45.0)
        text = E.board_footprint_text(library, "R1", (100 * MM, 80 * MM), -90.0, semantics="set")
        start = tmp_path / "start.kicad_pcb"
        start.write_text(G.board_text([text], version=version), newline="\n")
        held = FJ.read(start.read_bytes(), "R1")
        reference = next(t for t in held.texts if t.key == ("property", "Reference"))
        if held.orientation != -90 or not reference.style.keep_upright:
            raise RuntimeError(f"the start footprint reads {held.orientation}, {reference}")
        if not 180 <= reference.style.angle < 360:
            raise RuntimeError(f"the start Reference reads at {reference.style.angle}")
        problems = []
        for rotation in (-90.0, 270.0, -450.0):
            board = tmp_path / f"turned_{rotation}.kicad_pcb"
            shutil.copyfile(start, board)
            pcb.move_footprint("R1", 100, 80, rotation=rotation, pcb_path=str(board))
            if board.read_bytes() != start.read_bytes():
                at = FJ.child(FJ.footprint_group(board.read_bytes(), "R1"), b"at")
                problems.append(f"rotation {rotation}: (at) now {[t.value for t in at.items]}")
        assert not problems, problems

    def test_move_missing_refuses_untouched(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        with pytest.raises(ToolError, match="not found"):
            pcb.move_footprint("R99", 10, 10, pcb_path=p)
        assert Path(p).read_bytes() == before

    def test_move_warnings_survive_k10_header(self, tmp_path):
        """The kiutils warning path silently skipped K10 boards; the CST
        twins warn on any version."""
        p = _make_keepout_board_bumped(tmp_path)
        result = pcb.move_footprint("R1", 25, 25, pcb_path=p)
        assert "WARNING: position is inside a keep-out zone" in result
        result = pcb.move_footprint("R1", 100, 100, pcb_path=p)
        assert "WARNING" not in result

    def test_remove_k9(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        assert pcb.remove_footprint("R1", pcb_path=p) == "Removed R1"
        (removed,) = J.assert_removed(before, Path(p).read_bytes(), heads=["footprint"])
        assert removed.path == ROOT
        assert pcb.list_pcb_footprints(p) == []

    def test_remove_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.remove_footprint("R1", pcb_path=p)
        (removed,) = J.assert_removed(before, Path(p).read_bytes(), heads=["footprint"])
        assert removed.path == ROOT
        assert pcb.list_pcb_footprints(p) == []


class TestGraphicWritersCst:
    def test_text_scratch(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_pcb_text("BOARD V1", 100, 110, layer="F.SilkS", pcb_path=p)
        assert result == "Text 'BOARD V1' at (100, 110) on F.SilkS"
        (text,) = J.assert_inserted(before, Path(p).read_bytes(), heads=["gr_text"])
        assert text.path == ROOT
        texts = [g for g in pcb.list_pcb_graphic_items(p) if g.type == "text"]
        assert [(t.text, t.x, t.y, t.layer) for t in texts] == [
            ("BOARD V1", 100.0, 110.0, "F.SilkS")
        ]

    def test_line_scratch(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_pcb_line(80, 80, 120, 80, layer="Edge.Cuts", pcb_path=p)
        assert result == "Line: (80, 80) -> (120, 80) on Edge.Cuts"
        (line,) = J.assert_inserted(before, Path(p).read_bytes(), heads=["gr_line"])
        assert line.path == ROOT
        lines = [g for g in pcb.list_pcb_graphic_items(p) if g.type == "line"]
        assert (lines[-1].start_x, lines[-1].end_x) == (80.0, 120.0)

    def test_text_and_line_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.add_pcb_text("note", 10, 10, pcb_path=p)
        pcb.add_pcb_line(0, 0, 50, 0, pcb_path=p)
        added = J.assert_inserted(before, Path(p).read_bytes(), heads=["gr_text", "gr_line"])
        assert [item.path for item in added] == [ROOT, ROOT]
        kinds = [g.type for g in pcb.list_pcb_graphic_items(p)]
        assert "text" in kinds and "line" in kinds


class TestTraceFiltersCst:
    def test_width_by_net_name_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        result = pcb.set_trace_width(width=0.5, net_name="/SIG", pcb_path=p)
        assert result.traces_modified == 1
        after = Path(p).read_bytes()
        # The one /SIG segment's width atom, and nothing else.
        width = _the(before, "kicad_pcb", "segment", "width")
        assert J.atom_changes(before, after) == [J.AtomChange(width, 1, b"0.25", b"0.5")]
        assert b"(width 0.5)" in after

    def test_remove_by_net_k10(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        result = pcb.remove_traces(net_name="/SIG", pcb_path=p)
        assert result.traces_removed == 1
        after = Path(p).read_bytes()
        (removed,) = J.assert_removed(before, after, heads=["segment"])
        assert removed.path == ROOT
        assert pcb.list_pcb_traces(p) == []

    def test_unknown_net_message_parity(self, scratch_pcb):
        with pytest.raises(ToolError, match=r"Net 'Nope' not found\. Available nets:"):
            pcb.set_trace_width(width=0.5, net_name="Nope", pcb_path=str(scratch_pcb))

    def test_no_filters_message_parity(self, scratch_pcb):
        with pytest.raises(ToolError, match="at least one filter is required"):
            pcb.remove_traces(pcb_path=str(scratch_pcb))


class TestZoneWritersCst:
    _CORNERS: list[PointSpec] = [
        {"x": 10, "y": 10},
        {"x": 40, "y": 10},
        {"x": 40, "y": 40},
        {"x": 10, "y": 40},
    ]

    def test_copper_k9_bytes(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_copper_zone("Net1", "F.Cu", self._CORNERS, priority=1, pcb_path=p)
        assert (result.net, result.layer, result.corners) == ("Net1", "F.Cu", 4)
        after = Path(p).read_bytes()
        (zone,) = J.assert_inserted(before, after, heads=["zone"])
        assert zone.path == ROOT
        i = after.index(b"(zone")
        span = after[i : i + 700]
        assert b"(net 1)" in span
        assert b'(net_name "Net1")' in span
        assert b"(priority 1)" in span
        zones = pcb.list_pcb_zones(p)
        assert [(z.net_name, z.layers, z.priority, z.is_keepout) for z in zones] == [
            ("Net1", ["F.Cu"], 1, False)
        ]
        polygon = zones[0].polygon
        assert polygon is not None and len(polygon) == 4

    def test_copper_solid_connect(self, scratch_pcb):
        p = str(scratch_pcb)
        pcb.add_copper_zone("Net1", "F.Cu", self._CORNERS, thermal_relief=False, pcb_path=p)
        after = Path(p).read_bytes()
        assert b"(connect_pads yes" in after
        zone = next(z for z in Board.from_file(p).zones)
        assert zone.connectPads == "yes"

    def test_copper_k10_named(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        pcb.add_copper_zone("/GND", "F.Cu", self._CORNERS, pcb_path=p)
        after = Path(p).read_bytes()
        (zone,) = J.assert_inserted(before, after, heads=["zone"])
        assert zone.path == ROOT
        i = after.index(b"(zone")
        span = after[i : i + 700]
        assert b'(net "/GND")' in span
        assert b"net_name" not in span
        assert b"filled_areas_thickness" not in span
        zones = pcb.list_pcb_zones(p)
        assert [(z.net_name, z.is_keepout) for z in zones] == [("/GND", False)]

    def test_copper_k10_unknown_net_refuses_untouched(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        with pytest.raises(ToolError, match="Net 'Nope' not found"):
            pcb.add_copper_zone("Nope", "F.Cu", self._CORNERS, pcb_path=p)
        assert Path(p).read_bytes() == before

    def test_keepout_k9_bytes(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_keepout_zone(self._CORNERS[:3], no_tracks=False, no_vias=True, pcb_path=p)
        assert result.corners == 3
        assert result.restrictions["tracks"] == "allowed"
        assert result.restrictions["vias"] == "not_allowed"
        after = Path(p).read_bytes()
        (zone,) = J.assert_inserted(before, after, heads=["zone"])
        assert zone.path == ROOT
        i = after.index(b"(keepout")
        span = after[i : i + 250]
        assert b"(tracks allowed)" in span and b"(vias not_allowed)" in span
        kz = next(z for z in pcb.list_pcb_zones(p) if z.is_keepout)
        assert kz.keepout is not None
        assert kz.keepout["tracks"] == "allowed"

    def test_keepout_k10_drops_net_tokens(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.add_keepout_zone(self._CORNERS, pcb_path=p)
        after = Path(p).read_bytes()
        i = after.index(b"(zone")
        span = after[i : i + 700]
        assert b"(net " not in span
        assert b"net_name" not in span
        kz = next(z for z in pcb.list_pcb_zones(p) if z.is_keepout)
        assert kz.keepout is not None
        assert kz.keepout["footprints"] == "not_allowed"


@requires_cli
def test_drc_accepts_all_new_constructs(scratch_pcb):
    """KiCad 9 parses a board carrying every slice-14 construct: the
    property-based footprint template, stroke-form gr_line, both zone
    shapes with the measured connect_pads token, thermal vias."""
    p = str(scratch_pcb)
    pcb.place_footprint("R2", "4.7K", 150, 100, pcb_path=p)
    pcb.move_footprint("R1", 100, 100, rotation=90, pcb_path=p)
    pcb.add_pcb_text("SLICE14", 100, 115, pcb_path=p)
    pcb.add_pcb_line(90, 90, 110, 90, pcb_path=p)
    pcb.add_copper_zone(
        "Net1", "F.Cu", [{"x": 95, "y": 95}, {"x": 105, "y": 95}, {"x": 105, "y": 105}], pcb_path=p
    )
    pcb.add_copper_zone(
        "Net2",
        "B.Cu",
        [{"x": 95, "y": 95}, {"x": 105, "y": 95}, {"x": 105, "y": 105}],
        thermal_relief=False,
        pcb_path=p,
    )
    pcb.add_keepout_zone([{"x": 10, "y": 10}, {"x": 20, "y": 10}, {"x": 20, "y": 20}], pcb_path=p)
    pcb.add_thermal_vias("R1", pad_number="2", rows=2, cols=2, pcb_path=p)
    pcb.set_trace_width(width=0.5, net_name="Net1", pcb_path=p)
    pcb.remove_traces(net_name="Net2", pcb_path=p)
    pcb.remove_dangling_tracks(pcb_path=p)
    result = pcb.run_drc(pcb_path=p)
    assert result.violation_count >= 0


class TestDanglingCst:
    def test_k10_removes_dangling(self, tmp_path):
        """The fixture's lone segment touches no pad world position, so it
        is dangling; removal is a clean span deletion on a K10 board."""
        p = _write_board(tmp_path, _K10_BOARD)
        before = Path(p).read_bytes()
        result = pcb.remove_dangling_tracks(pcb_path=p)
        assert result.tracks_removed == 1
        assert result.iterations == 1
        after = Path(p).read_bytes()
        (removed,) = J.assert_removed(before, after, heads=["segment"])
        assert removed.path == ROOT
        assert b"(segment" not in after

    def test_noop_is_byte_identical(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        pcb.remove_dangling_tracks(pcb_path=p)
        before = Path(p).read_bytes()
        result = pcb.remove_dangling_tracks(pcb_path=p)
        assert result.tracks_removed == 0
        assert Path(p).read_bytes() == before


class TestThermalViasCst:
    def test_k9_grid_pure_insertion(self, scratch_pcb):
        p = str(scratch_pcb)
        before = Path(p).read_bytes()
        result = pcb.add_thermal_vias("R1", pad_number="1", rows=2, cols=2, pcb_path=p)
        assert result.vias_added == 4
        assert result.net == "Net1"
        vias = J.assert_inserted(before, Path(p).read_bytes(), heads=["via"] * 4)
        assert [via.path for via in vias] == [ROOT] * 4
        board = Board.from_file(p)
        assert sum(1 for t in board.traceItems if isinstance(t, Via)) == 4

    def test_k10_pad_net_resolution(self, tmp_path):
        p = _write_board(tmp_path, _K10_BOARD)
        result = pcb.add_thermal_vias("R1", pad_number="1", rows=1, cols=1, pcb_path=p)
        assert result.vias_added == 1
        assert result.net == "/SIG"
        after = Path(p).read_bytes()
        i = after.index(b"(via")
        assert b'(net "/SIG")' in after[i : i + 250]
        vias = [t for t in pcb.list_pcb_traces(p) if t.type == "via"]
        assert len(vias) == 1


def _root_of(path):
    """The CST root of a board file written by a kiutils fixture."""
    return _cst.parse(Path(path).read_bytes()).lists[0]


def _make_board_with_edges(tmp_path, lines, arcs=()):
    """Board with Edge.Cuts lines (sx,sy,ex,ey) and arcs (sx,sy,mx,my,ex,ey)."""
    board = Board.create_new()
    board.nets = [Net(number=0, name="")]
    for sx, sy, ex, ey in lines:
        gl = GrLine()
        gl.start = Position(X=sx, Y=sy)
        gl.end = Position(X=ex, Y=ey)
        gl.layer = "Edge.Cuts"
        gl.width = 0.05
        gl.tstamp = _gen_uuid()
        board.graphicItems.append(gl)
    for sx, sy, mx, my, ex, ey in arcs:
        arc = GrArc()
        arc.start = Position(X=sx, Y=sy)
        arc.mid = Position(X=mx, Y=my)
        arc.end = Position(X=ex, Y=ey)
        arc.layer = "Edge.Cuts"
        arc.width = 0.05
        arc.tstamp = _gen_uuid()
        board.graphicItems.append(arc)
    board.filePath = str(tmp_path / "edge_test.kicad_pcb")
    board.to_file()
    return _root_of(board.filePath)


class TestEdgePolygonCst:
    """Ported from the retired kiutils _board_edge_polygon tests."""

    _RECT = [(0, 0, 50, 0), (50, 0, 50, 50), (50, 50, 0, 50), (0, 50, 0, 0)]

    def test_closed_rectangle(self, tmp_path):
        poly = pcb._edge_polygon_cst(_make_board_with_edges(tmp_path, self._RECT))
        assert poly is not None
        assert len(poly) == 4

    def test_no_edges(self, tmp_path):
        assert pcb._edge_polygon_cst(_make_board_with_edges(tmp_path, [])) is None

    def test_with_arcs(self, tmp_path):
        """Three lines closed by an arc: the arc is linearized, not dropped."""
        root = _make_board_with_edges(
            tmp_path,
            [(0, 0, 50, 0), (50, 0, 50, 50), (0, 50, 0, 0)],
            arcs=[(50, 50, 25, 60, 0, 50)],
        )
        poly = pcb._edge_polygon_cst(root)
        assert poly is not None
        assert len(poly) >= 4

    def test_t_junction_no_crash(self, tmp_path):
        pcb._edge_polygon_cst(_make_board_with_edges(tmp_path, [*self._RECT, (25, 0, 25, -20)]))

    def test_multiple_outlines_no_crash(self, tmp_path):
        """Two separate rectangles on Edge.Cuts: one polygon comes back, no crash."""
        second = [(100, 100, 120, 100), (120, 100, 120, 120), (120, 120, 100, 120)]
        root = _make_board_with_edges(tmp_path, [*self._RECT, *second, (100, 120, 100, 100)])
        poly = pcb._edge_polygon_cst(root)
        assert poly is not None
        assert len(poly) == 4


class TestKeepoutViolationsCst:
    """Ported from the retired kiutils _check_footprint_keepout_violations test."""

    def test_layer_mismatch_no_violation(self, tmp_path):
        """Keepout zone on F.Cu only: a B.Cu query is not a violation."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        zone = Zone()
        zone.net = 0
        zone.netName = ""
        zone.layers = ["F.Cu"]
        zone.tstamp = _gen_uuid()
        zone.hatch = Hatch(style="edge", pitch=0.5)
        zone.keepoutSettings = KeepoutSettings(
            tracks="not_allowed",
            vias="not_allowed",
            pads="not_allowed",
            copperpour="not_allowed",
            footprints="not_allowed",
        )
        poly = ZonePolygon()
        poly.coordinates = [
            Position(X=0, Y=0),
            Position(X=100, Y=0),
            Position(X=100, Y=100),
            Position(X=0, Y=100),
        ]
        zone.polygons = [poly]
        board.zones.append(zone)
        board.filePath = str(tmp_path / "layer_test.kicad_pcb")
        board.to_file()

        root = _root_of(board.filePath)
        assert pcb._keepout_violations_cst(root, 50, 50, "B.Cu") == []
        assert pcb._keepout_violations_cst(root, 50, 50, "F.Cu") == [
            {
                "source": "board",
                "layers": ["F.Cu"],
                "restrictions": dict.fromkeys(
                    ("tracks", "vias", "pads", "copperpour", "footprints"), "not_allowed"
                ),
            }
        ]


def _k9_k10_pair(tmp_path, make):
    """The same kiutils fixture built twice, the second bumped to a K10 version."""
    k9, k10 = tmp_path / "k9", tmp_path / "k10"
    k9.mkdir()
    k10.mkdir()
    return str(make(k9)), _bump_version(make(k10))


MM = 1_000_000  # nanometres per millimetre


def _lexer_head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _lexer_kids(group, name: bytes) -> list:
    return [c for c in group.items if _lexer_head(c) == name]


def _lexer_child(group, name: bytes):
    found = _lexer_kids(group, name)
    if not found:
        raise LookupError(f"no ({name.decode()} ...) in the list at byte {group.start}")
    return found[0]


def _nm_xy(group) -> tuple[int, int]:
    """(x, y) of a list such as (at x y ...), (start x y) or (xy x y), in nm as KiCad
    reads them (parseBoardUnits; the two majors read these fixtures' values alike)."""
    return (
        P.parse_board_units(group.items[1].value, major=10),
        P.parse_board_units(group.items[2].value, major=10),
    )


def _closed_loops(segments) -> list[list[tuple[int, int]]]:
    """The closed loops the segments form, chained where their ends are equal (the
    fixtures here draw exact corners); LookupError when an end has no partner or more
    than one, which no closed outline has."""
    ends: dict[tuple[int, int], list[int]] = {}
    for i, (a, b) in enumerate(segments):
        ends.setdefault(a, []).append(i)
        ends.setdefault(b, []).append(i)
    if any(len(uses) != 2 for uses in ends.values()):
        raise LookupError(f"Edge.Cuts segments that do not close into loops: {segments}")
    loops, used = [], set()
    for first, (start, nxt) in enumerate(segments):
        if first in used:
            continue
        used.add(first)
        loop = [start]
        while nxt != start:
            loop.append(nxt)
            i = next(k for k in ends[nxt] if k not in used)
            used.add(i)
            a, b = segments[i]
            nxt = b if a == nxt else a
        loops.append(loop)
    return loops


def _geometry_judge(path) -> dict:
    """What KiCad reads from a board for the geometry trio, through its lexer (the port
    in tests/kicad_ref), never the CST: each footprint's reference, position (nm),
    orientation (Normalize180 of the angle, as FOOTPRINT::SetOrientation stores it) and
    layer; each board-level rule area's layers, restrictions and outline (nm); and the
    closed loops of the Edge.Cuts gr_line items. Raises LookupError for an Edge.Cuts
    shape of another kind, which this judge does not read."""
    data = Path(path).read_bytes()
    root = L.tree(data, knows_bar=L.knows_bar_of(data))[0]
    footprints = {}
    for fp in _lexer_kids(root, b"footprint"):
        at = _lexer_child(fp, b"at")
        reference = next(
            p.items[2].value.decode()
            for p in _lexer_kids(fp, b"property")
            if p.items[1].value == b"Reference"
        )
        footprints[reference] = {
            "at": _nm_xy(at),
            "orientation": (
                trigo.normalize180(P.parse_double(at.items[3].value)) if len(at.items) > 3 else 0.0
            ),
            "layer": _lexer_child(fp, b"layer").items[1].value.decode(),
            "group": fp,
        }
    keepouts = []
    for zone in _lexer_kids(root, b"zone"):
        for rules in _lexer_kids(zone, b"keepout"):
            named = (_lexer_kids(zone, b"layers") or _lexer_kids(zone, b"layer"))[0]
            keepouts.append(
                {
                    "layers": [t.value.decode() for t in named.items[1:]],
                    "restrictions": {
                        r.items[0].value.decode(): r.items[1].value.decode()
                        for r in rules.items[1:]
                    },
                    "outline": [
                        _nm_xy(g)
                        for g in _lexer_kids(
                            _lexer_child(_lexer_child(zone, b"polygon"), b"pts"), b"xy"
                        )
                    ],
                }
            )
    segments = []
    for item in root.items:
        layer = _lexer_kids(item, b"layer") if item.kind == L.GROUP else []
        if not layer or layer[0].items[1].value != b"Edge.Cuts":
            continue
        if _lexer_head(item) != b"gr_line":
            raise LookupError(f"this judge reads gr_line edges only, not {_lexer_head(item)!r}")
        segments.append((_nm_xy(_lexer_child(item, b"start")), _nm_xy(_lexer_child(item, b"end"))))
    return {"footprints": footprints, "keepouts": keepouts, "contours": _closed_loops(segments)}


def _forbidding(judge: dict, layer: str, p: tuple[int, int]) -> list[dict]:
    """The rule areas that forbid a footprint on ``layer`` at p, as check_placement lists
    them: geom_judge.inside on the exact outline (none of the points asked about here
    is on one)."""
    return [
        {"source": "board", "layers": zone["layers"], "restrictions": zone["restrictions"]}
        for zone in judge["keepouts"]
        if zone["restrictions"].get("footprints") == "not_allowed"
        and layer in zone["layers"]
        and GJ.inside(p, zone["outline"])
    ]


def _expected_check(judge: dict, reference: str, x_mm: float, y_mm: float) -> PlacementCheckResult:
    """check_placement's answer from the judge: the keepouts holding the point, and
    whether it is off the board (an even number of Edge.Cuts loops around it)."""
    p = (round(x_mm * MM), round(y_mm * MM))
    hits = _forbidding(judge, judge["footprints"][reference]["layer"], p)
    contours = judge["contours"]
    outside = bool(contours) and not GJ.parity(p, contours)
    return PlacementCheckResult(
        status="violations_found" if hits or outside else "ok",
        board_edge_checked=bool(contours),
        keepout_violations=hits,
        outside_board_edge=outside,
    )


def _expected_validation(judge: dict) -> BoardValidationResult:
    """validate_board's answer from the judge, footprint by footprint in file order."""
    contours = judge["contours"]
    violations = []
    for reference, fp in judge["footprints"].items():
        issues = []
        if _forbidding(judge, fp["layer"], fp["at"]):
            issues.append("keepout_zone")
        if contours and not GJ.parity(fp["at"], contours):
            issues.append("outside_board_edge")
        if issues:
            violations.append(
                {
                    "reference": reference,
                    "position": {"x": fp["at"][0] / MM, "y": fp["at"][1] / MM},
                    "layer": fp["layer"],
                    "issues": issues,
                }
            )
    return BoardValidationResult(
        total_footprints=len(judge["footprints"]),
        violations=violations,
        board_edge_checked=bool(contours),
        status=f"{len(violations)} violations found" if violations else "ok",
    )


class TestGeometryTrioCst:
    """The geometry trio on KiCad 9 and KiCad 10 headers, which kiutils refused outright.

    Each answer is judged on its own file, read back through KiCad's lexer: its rule
    area and Edge.Cuts loop by geom_judge, its courtyard by geom_gen.courtyard_box (exact
    extremes of the shapes as RotatePoint turns them). Pinning the K10 answer to the K9
    answer of the same code showed only that the code agrees with itself. The hand
    values stay, the readable statement of what the judge computes.
    """

    def test_check_placement(self, tmp_path):
        from test_pcb_write_tools import _make_keepout_pcb

        k9, k10 = _k9_k10_pair(tmp_path, _make_keepout_pcb)
        for path in (k9, k10):
            judge = _geometry_judge(path)
            for x, y in ((100, 100), (25, 25), (500, 500)):
                assert pcb.check_placement("R1", x, y, pcb_path=path) == _expected_check(
                    judge, "R1", x, y
                ), (path, x, y)
            hit = pcb.check_placement("R1", 25, 25, pcb_path=path)
            assert hit.status == "violations_found"
            assert [v["source"] for v in hit.keepout_violations] == ["board"]
            assert hit.keepout_violations[0]["layers"] == ["F.Cu"]
            assert hit.keepout_violations[0]["restrictions"]["footprints"] == "not_allowed"
            assert pcb.check_placement("R1", 500, 500, pcb_path=path).outside_board_edge is True

    def test_check_placement_takes_no_rotation(self, tmp_path):
        """It accepted one and never read it, so the schema promised nothing.

        Both checks are on the origin point, and rotating about the origin
        cannot move the origin, so no angle could ever have changed the answer.
        """
        import inspect

        from test_pcb_write_tools import _make_keepout_pcb

        assert "rotation" not in inspect.signature(pcb.check_placement).parameters
        tool = pcb.mcp._tool_manager._tools["check_placement"]
        assert "rotation" not in tool.parameters.get("properties", {})

        k9, _ = _k9_k10_pair(tmp_path, _make_keepout_pcb)
        with pytest.raises(TypeError):
            pcb.check_placement("R1", 25, 25, rotation=90, pcb_path=k9)  # type: ignore[call-arg]

    def test_move_footprint_reports_a_failed_placement_check(self, tmp_path, monkeypatch):
        """The checks are advisory, but a fault in them must not be silent.

        This branch was `except Exception: pass`, so any defect in the geometry
        helpers stayed invisible for as long as nobody went looking.
        """
        from test_pcb_write_tools import _make_keepout_pcb

        k9, _ = _k9_k10_pair(tmp_path, _make_keepout_pcb)
        monkeypatch.setattr(
            pcb,
            "_keepout_violations_cst",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        before = Path(k9).read_bytes()
        result = pcb.move_footprint("R1", 40, 40, pcb_path=k9)
        assert "Moved R1" in result, "an advisory check must never block the move"
        assert "placement checks could not run" in result
        assert "RuntimeError: boom" in result
        assert Path(k9).read_bytes() != before, "the move itself must still land"

    def test_get_footprint_bounds(self, tmp_path):
        from test_pcb_read_tools import _make_board_with_courtyard_fp

        k9, k10 = _k9_k10_pair(tmp_path, _make_board_with_courtyard_fp)
        for path in (k9, k10):
            result = pcb.get_footprint_bounds("U1", pcb_path=path)
            fp = _geometry_judge(path)["footprints"]["U1"]
            assert result.position == {"x": fp["at"][0] / MM, "y": fp["at"][1] / MM}
            assert result.rotation == fp["orientation"]
            assert result.layer == fp["layer"]
            shapes = G.shapes_of(fp["group"], "F.CrtYd", major=10)
            x0, y0, x1, y1 = G.courtyard_box(shapes, fp["orientation"], fp["at"])
            want = {
                "min_x": x0 / MM,
                "min_y": y0 / MM,
                "max_x": x1 / MM,
                "max_y": y1 / MM,
                "width": (x1 - x0) / MM,
                "height": (y1 - y0) / MM,
            }
            assert result.courtyard is not None and result.courtyard.keys() == want.keys()
            for key, value in want.items():
                # The tool reports four decimals (0.1 um), so the exact box to within that.
                assert abs(result.courtyard[key] - value) <= 1e-4, (path, key, result, want)
            assert result.position == {"x": 100, "y": 100}
            assert result.rotation == 0
            assert result.layer == "F.Cu"
            # Local courtyard -5..5 around a footprint at (100, 100).
            assert result.courtyard == {
                "min_x": 95,
                "min_y": 95,
                "max_x": 105,
                "max_y": 105,
                "width": 10,
                "height": 10,
            }

    def test_validate_board(self, tmp_path):
        from test_pcb_write_tools import _make_keepout_pcb

        k9, k10 = _k9_k10_pair(tmp_path, _make_keepout_pcb)
        for path in (k9, k10):
            clean = pcb.validate_board(pcb_path=path)
            assert clean == _expected_validation(_geometry_judge(path)), path
            assert (clean.total_footprints, clean.board_edge_checked, clean.status) == (
                1,
                True,
                "ok",
            )

        for path in (k9, k10):
            pcb.move_footprint("R1", 25, 25, pcb_path=path)
            bad = pcb.validate_board(pcb_path=path)
            assert bad == _expected_validation(_geometry_judge(path)), path
            assert bad.status == "1 violations found"
            assert bad.violations == [
                {
                    "reference": "R1",
                    "position": {"x": 25, "y": 25},
                    "layer": "F.Cu",
                    "issues": ["keepout_zone"],
                }
            ]


class TestResolveLayer:
    """The board's own stackup is the only statement of what a layer may be.

    An enum cannot express it: the set is per-board and users rename layers.
    Unvalidated, a typo reached the disk verbatim, and measured 2026-08-12
    kicad-cli then refused the whole board with "Failed to load board".
    """

    def _root(self, text: str = _K9_BOARD):
        return _cst.parse(text.encode()).lists[0]

    def test_known_layer_passes_through(self):
        assert pcb._resolve_layer_cst(self._root(), "F.Cu") == "F.Cu"

    def test_unknown_layer_lists_what_the_board_defines(self):
        with pytest.raises(ToolError, match="not defined on this board") as exc:
            pcb._resolve_layer_cst(self._root(), "banana")
        assert "F.Cu" in str(exc.value), "the error must name the legal values"

    def test_display_alias_is_not_a_layer_name(self):
        """(36 "B.SilkS" user "B.Silkscreen") -- only atoms[1] is referenceable."""
        root = self._root()
        assert pcb._resolve_layer_cst(root, "B.SilkS") == "B.SilkS"
        with pytest.raises(ToolError):
            pcb._resolve_layer_cst(root, "B.Silkscreen")

    def test_copper_only_rejects_a_technical_layer(self):
        root = self._root()
        with pytest.raises(ToolError, match="not a copper layer"):
            pcb._resolve_layer_cst(root, "F.SilkS", copper_only=True)
        assert pcb._resolve_layer_cst(root, "B.Cu", copper_only=True) == "B.Cu"

    def test_copper_test_is_the_suffix_not_the_type_atom(self):
        """KiCad writes power/mixed/jumper for inner copper.

        A `type == "signal"` test would reject a legal power plane, so the
        suffix is what decides.
        """
        text = _K9_BOARD.replace('(31 "B.Cu" signal)', '(31 "B.Cu" signal)\n    (1 "In1.Cu" power)')
        assert pcb._resolve_layer_cst(self._root(text), "In1.Cu", copper_only=True) == "In1.Cu"

    def test_k10_board_table_reads_the_same(self):
        root = self._root(_K10_BOARD)
        assert pcb._resolve_layer_cst(root, "B.Cu") == "B.Cu"
        with pytest.raises(ToolError):
            pcb._resolve_layer_cst(root, "banana")


class TestZoneFillSplice:
    """pcbnew computes the fill; this server writes it.

    The old fill_zones handed the board to pcbnew and let it SaveBoard, which
    rewrote the whole file. Measured on KiCad 9: that took KiCad's own
    multichannel_mixer-unrouted from version 20241030 to 20241229, dropped 114
    footprint property UUIDs and renamed user layers, all while reporting
    success. pcbnew is handed the board read-only now and returns polygons.
    """

    def test_millimetres_match_kicads_own_formatting(self):
        # Measured across 4000 coordinates of ecc83-pp: 1 to 6 decimal places,
        # never a padded six, so trailing zeros are stripped.
        assert pcb._mm(166963652) == "166.963652"
        assert pcb._mm(1000000) == "1"
        assert pcb._mm(0) == "0"
        assert pcb._mm(-2500000) == "-2.5"
        assert pcb._mm(1500) == "0.0015"

    def test_the_node_round_trips_through_the_cst(self):
        raw = pcb._filled_polygon_bytes("B.Cu", [(1, 2), (3, 4), (5, 6)])
        doc = _cst.parse(raw)
        assert _cst.serialize(doc) == raw
        node = doc.lists[0]
        assert node.head == "filled_polygon"
        assert node.find("layer").atoms[1].text == "B.Cu"
        assert len(node.find("pts").find_all("xy")) == 3

    def test_points_are_packed_not_one_per_line(self):
        """KiCad packs (xy ...) runs onto shared lines; one per line would be a
        different file for the same geometry. Where KiCad breaks those lines (its
        Prettify rule, under which a line can run past column 99) is judged by
        test_ladder_r2_fills.py, a strict xfail until bug 62 is fixed."""
        pts = [(i * 1_234_567, i * 987_654) for i in range(40)]
        text = pcb._filled_polygon_bytes("F.Cu", pts).decode()
        body = [ln for ln in text.splitlines() if "(xy " in ln]
        assert any(ln.count("(xy ") > 1 for ln in body), "points were not packed"

    def _board_with_zone(self, board):
        pcb.add_copper_zone(
            net_name="Net1",
            layer="F.Cu",
            corners=[{"x": 10, "y": 10}, {"x": 40, "y": 10}, {"x": 40, "y": 40}],
            pcb_path=str(board),
        )
        _, root, _ = pcb._open_pcb_cst(str(board))
        uuid_ = next(
            z.find("uuid").atoms[1].text for z in root.find_all("zone") if z.find("keepout") is None
        )
        return board, uuid_

    def test_the_splice_only_touches_the_zone(self, scratch_pcb):
        board, uuid_ = self._board_with_zone(scratch_pcb)
        before = board.read_bytes()
        tree, root, _ = pcb._open_pcb_cst(str(board))
        n = pcb._splice_fills(
            root,
            [{"uuid": uuid_, "layer": "F.Cu", "outlines": [[[0, 0], [10**7, 0], [10**7, 10**7]]]}],
        )
        after = _cst.serialize(tree)
        assert n == 1
        assert b"filled_polygon" in after
        # The fill is one filled_polygon inside its own zone, and nothing else.
        (fill,) = J.assert_inserted(before, after, heads=["filled_polygon"])
        assert fill.path == _the(
            before, "kicad_pcb", "zone", where=lambda loc, zone: _uuid_of(zone) == uuid_.encode()
        )

    def test_refilling_replaces_rather_than_doubles(self, scratch_pcb):
        """A zone filled before carries last time's polygons. Appending would
        double its copper, which no oracle in this suite would catch."""
        board, uuid_ = self._board_with_zone(scratch_pcb)
        dump = [
            {"uuid": uuid_, "layer": "F.Cu", "outlines": [[[0, 0], [10**7, 0], [10**7, 10**7]]]}
        ]
        tree, root, _ = pcb._open_pcb_cst(str(board))
        pcb._splice_fills(root, dump)
        first = _cst.serialize(tree)
        pcb._splice_fills(root, dump)
        assert _cst.serialize(tree) == first
        assert first.count(b"filled_polygon") == 1

    def test_a_degenerate_outline_is_dropped(self, scratch_pcb):
        """Two points are a line, not a pour. KiCad would refuse the board."""
        board, uuid_ = self._board_with_zone(scratch_pcb)
        tree, root, _ = pcb._open_pcb_cst(str(board))
        pcb._splice_fills(root, [{"uuid": uuid_, "layer": "F.Cu", "outlines": [[[0, 0], [1, 1]]]}])
        assert b"filled_polygon" not in _cst.serialize(tree)

    def test_an_unknown_uuid_touches_nothing(self, scratch_pcb):
        board, _ = self._board_with_zone(scratch_pcb)
        before = board.read_bytes()
        tree, root, _ = pcb._open_pcb_cst(str(board))
        n = pcb._splice_fills(
            root,
            [{"uuid": "no-such-zone", "layer": "F.Cu", "outlines": [[[0, 0], [1, 0], [1, 1]]]}],
        )
        assert n == 0
        assert _cst.serialize(tree) == before
