"""Tests for the byte-preserving CST substrate and its first consumer, add_label.

Decision record: docs/adr-cst-substrate.md. The property under test is the
substrate invariant: bytes the caller did not edit reach the disk unchanged.
What an edit changed is judged by tests/edit_judge.py, which reads both files
with the port of KiCad's lexer, never with the CST under test.
"""

from __future__ import annotations

from pathlib import Path

import edit_gen as E
import edit_judge as J
import geom_gen as G
import pytest
import routing_fixtures as RF
import sch_judge as S
from conftest import (
    HAS_KICAD_CLI,
    _kicad_cli_major,
    assert_kicad_parseable,
    build_test_footprint,
    make_dual_unit_sch,
    reparse,
    requires_cli,
    run_erc,
)
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import parse_units as P
from kicad_ref import sch_field as SF
from kicad_ref import transform as T
from kicad_ref.richio import quotes
from kiutils.items.schitems import Connection
from mcp.server.mcpserver.exceptions import ToolError
from test_ladder_r2_pins import (
    lexed_root,
    pin_map,
    pin_pos_of,
    placed_symbols,
    port_pin_ends,
    read_iu,
    reference_property,
    report_double,
    report_iu,
    report_map,
    symbol_placement,
)
from test_ladder_r3_symbols import Move, move_all, rigid_problems, turned

from mcp_server_kicad import _cst, footprint, project, schematic, symbol
from mcp_server_kicad._shared import _node_uuid, _resolve_system_lib, _run_cli

FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"

#: edit_judge's location of a schematic's root list: the path of every edit to a
#: top-level item (a label, a wire, a placed symbol).
ROOT = ((0, "kicad_sch"),)


def _labels(location) -> list:
    """A location's labels, outermost first: ["kicad_sch", "symbol", "property"]."""
    return [label for _, label in location]


def _the(data: bytes, *labels: str, where=lambda location, item: True) -> J.Path:
    """The location of the one list in ``data`` whose labels, outermost first, are
    ``labels`` and that ``where(location, item)`` accepts.

    Raises LookupError, not AssertionError: a locator that matches nothing is a broken
    premise, never the defect a strict xfail expects.
    """
    found = J.find_paths(
        data,
        lambda loc, item: (
            item.items is not None and _labels(loc) == list(labels) and where(loc, item)
        ),
    )
    if len(found) != 1:
        raise LookupError(f"{len(found)} items match where one was expected: {found[:4]}")
    return found[0]


def _uuid_of(item) -> bytes | None:
    """The value of a list item's (uuid ...) child, as the lexer reads it."""
    return next((c.items[1].value for c in item.items if c.head == "uuid"), None)


def _tee(path, point, *arms) -> None:
    """Splice raw wires from point + arm to point, one per arm (mm): wire ends meeting at a
    point, which is where KiCad's editor takes a junction when three directions meet."""
    x, y = point
    for dx, dy in arms:
        RF.wire(str(path), round(x + dx, 4), round(y + dy, 4), x, y)


# ---------------------------------------------------------------------------
# move_component (step 9): what a move changes, by KiCad's rule
# ---------------------------------------------------------------------------

#: The fixture's R1 as written: at (100, 100), angle 0.
_R1_AT = b"(at 100 100 0) (unit 1)"


def _r1_variant(tmp_path: Path, name: str, new: bytes) -> Path:
    """tests/fixtures/kicad_native.kicad_sch with R1's (at) written ``new``, as ``name``."""
    data = FIXTURE.read_bytes()
    if data.count(_R1_AT) != 1:
        raise RuntimeError(f"the fixture holds {_R1_AT!r} {data.count(_R1_AT)} times, not once")
    path = tmp_path / name
    path.write_bytes(data.replace(_R1_AT, new))
    return path


def _mm(iu: int) -> float:
    """A coordinate as a caller passes it: the float of the text KiCad writes for it."""
    return float(G.sch_mm(iu))


def _outcome(call):
    """(result, None) when ``call()`` returns, (None, exception) when it raises."""
    try:
        return call(), None
    except Exception as exc:  # the type of what escapes is the subject
        return None, exc


def _require_unloadable(path: Path) -> None:
    """The premise of a test about a file KiCad cannot load: kicad-cli writes no ERC report
    for it (conftest.run_erc raises). RuntimeError when it loads, so a broken premise never
    passes for the defect; nothing is checked where kicad-cli is not installed."""
    if not HAS_KICAD_CLI:
        return
    try:
        run_erc(path)
    except RuntimeError:
        return
    raise RuntimeError(f"the premise is a schematic kicad-cli refuses, and it loads {path.name}")


def _last_at(item) -> tuple[int, J.Item]:
    """(index, list) of the last (at) among a placed symbol's children, the one KiCad keeps."""
    found = [(index, child) for index, child in enumerate(item.items) if child.head == "at"]
    if not found:
        raise RuntimeError("the placed symbol has no (at)")
    return found[-1]


def _with_last_at(data: bytes, reference: str, target, rotation) -> bytes:
    """``data`` with the last (at) of the placed ``reference`` holding ``target`` and, when
    ``rotation`` is given, that angle, written as KiCad writes them: the frames KiCad reads
    once that (at) changes and nothing else that sets a frame does."""
    location, _ = S.placed_symbol(data, reference)
    _, at = _last_at(J.item_at(data, location))
    edits = [(at.items[1], G.sch_mm(target[0])), (at.items[2], G.sch_mm(target[1]))]
    if rotation is not None:
        edits.append((at.items[3], str(rotation)))
    for atom, text in sorted(edits, key=lambda edit: -edit[0].start):
        data = data[: atom.start] + text.encode() + data[atom.end :]
    return data


def expected_move_changes(before: bytes, reference: str, target, rotation) -> list:
    """The atoms moving ``reference`` to ``target`` (internal units) at ``rotation`` (None
    keeps the angle) changes by KiCad's rule, in document order, as (path of the list holding
    the atom, its index, the value KiCad must read there).

    The last (at)'s x and y change where the target differs from what KiCad reads, and its
    angle where ``rotation`` differs from the angle KiCad reads (static_cast<int> of
    parseDouble). Each property's last (at) changes its x and y where the field, carried to
    the frame KiCad reads it in once the move is made, lands elsewhere: P' = S'_p + T'_p *
    offset, the offset in the symbol's frame KiCad keeps (kicad_ref.sch_field). Nothing else
    changes: no angle of a property, no other list."""
    location, old = S.placed_symbol(before, reference)
    _, framed = S.placed_symbol(_with_last_at(before, reference, target, rotation), reference)
    item = J.item_at(before, location)
    at_index, _ = _last_at(item)
    out = []
    for index, child in enumerate(item.items):
        if index == at_index:
            path = location + ((index, "at"),)
            out += [
                (path, k, target[k - 1])
                for k in (1, 2)
                if read_iu(child.items[k].value) != target[k - 1]
            ]
            if rotation is not None and int(P.parse_double(child.items[3].value)) != rotation:
                out.append((path, 3, rotation))
        elif child.head == "property":
            field = old.fields[SF.property_name(child)]
            want = SF.carry_offset(framed.fields[field.name].frame, field.offset)
            at, _ = _last_at(child)
            path = location + ((index, "property"), (at, "at"))
            out += [(path, k, want[k - 1]) for k in (1, 2) if field.written[k - 1] != want[k - 1]]
    return out


#: Moves of the native fixture's R1 from (100, 100): (id, target in internal units, angle).
_MOVES = [
    ("translate", (635_000, 635_000), None),
    ("translate_and_turn", (635_000, 762_000), 90),
]

#: rule probe sheet B's three child orders: where the symbol sits, its state, the second
#: (at) the "second_at" order adds, the angle the move asks for, and the mirror after the
#: last (at), which KiCad keeps.
_ORDER_CASES = {
    "properties_first": ((508_000, 508_000), (90, ""), None, 0, ""),
    "mirror_last": ((1_016_000, 508_000), (0, "y"), None, 90, "y"),
    "second_at": ((1_524_000, 508_000), (0, ""), (1_651_000, 635_000, 180), 270, ""),
}
_ORDER_PARAMS = list(_ORDER_CASES)


def _order_symbol(order: str) -> E.SynSymbol:
    """R1 with its children in ``order`` (edit_gen.SYMBOL_ORDERS), its shown Reference and
    Value at asymmetric offsets in its frame and at text angles 0 and 90."""
    at, (rotation, mirror), second, _, _ = _ORDER_CASES[order]
    fields = (
        E.SynField("Reference", "R1", (0, 0), 0),
        E.SynField("Value", "V1", (0, 0), 90),
        E.SynField("Footprint", "", (0, 0), 0, False),
        E.SynField("Datasheet", "~", (0, 0), 0, False),
    )
    sym = E.SynSymbol("R1", at, rotation, mirror, fields, order, second)
    offsets = {"Reference": (50_800, -25_400), "Value": (-38_100, 63_500)}
    return E.at_offsets(sym, offsets | {"Footprint": (0, 0), "Datasheet": (0, 0)})


def _order_move(order: str, old: SF.PlacedSymbol):
    """(target, angle asked, transform KiCad should read after) for _order_symbol(order) as
    KiCad reads it: 12.7 mm right and 25.4 mm down, the angle the case asks for, the mirror
    after the last (at) kept."""
    _, _, _, rotation, kept = _ORDER_CASES[order]
    target = (old.position[0] + 127_000, old.position[1] + 254_000)
    return target, rotation, turned((rotation, kept), rotation)


def _written_fields(data: bytes, reference: str) -> dict:
    """{name: ((x, y), angle)} of the placed ``reference``'s properties: each last (at) as
    KiCad reads its numbers, whatever the symbol's own angle (the lexer port, no transform)."""
    (symbol,) = [s for s in placed_symbols(lexed_root(data)) if reference_property(s) == reference]
    out = {}
    for prop in symbol.items:
        if prop.kind == L.GROUP and prop.items and prop.items[0].value == b"property":
            (*_, at) = [c for c in prop.items if c.kind == L.GROUP and c.items[0].value == b"at"]
            xy = (read_iu(at.items[1]), read_iu(at.items[2]))
            out[SF.property_name(prop)] = (xy, P.parse_double(at.items[3].value))
    return out


def check_move_changes(before: bytes, after: bytes, reference: str, target, rotation) -> None:
    """The move changed exactly the atoms expected_move_changes names (edit_judge's exact
    atom list: nothing else, separators included), each to text KiCad reads as the value."""
    want = expected_move_changes(before, reference, target, rotation)
    got = J.atom_changes(before, after)
    assert [(c.path, c.index) for c in got] == [(p, k) for p, k, _ in want], (
        "the move changed other atoms than KiCad's rule moves:\n"
        f"changed {[(c.path[-1], c.index, c.new) for c in got]}\n"
        f"rule    {[(p[-1], k, v) for p, k, v in want]}"
    )
    for change, (_, k, value) in zip(got, want):
        text = change.new.decode("utf-8", "surrogateescape")
        read = report_double(text) if k == 3 else report_iu(text)
        assert read == value, f"{change.path[-1]} atom {k} reads {read}, the rule puts {value}"


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
        (label,) = J.assert_inserted(before, after, heads=["label"])
        assert label.path == ROOT
        assert b"(embedded_fonts" in after and b"generator_version" in after
        sch = reparse(kicad_native_sch)
        assert "CST_NET" in [lbl.text for lbl in sch.labels]

    def test_hostile_text_roundtrips(self, kicad_native_sch):
        hostile = 'A(B "q" \\end'
        schematic.add_label(hostile, 60, 90, schematic_path=str(kicad_native_sch))
        sch = reparse(kicad_native_sch)
        # kiutils 1.4.8 does not decode \\ and \" the way KiCad does, and the CST's
        # decoder is the code under test, so KiCad's lexer (the port) reads the
        # label back; the written atom is what KiCad's own Quotew writes for it.
        data = kicad_native_sch.read_bytes()
        atoms = [
            item.items[1]
            for loc, item in J.walk(data)
            if item.items is not None and _labels(loc) == ["kicad_sch", "label"]
        ]
        assert hostile.encode() in [atom.value for atom in atoms]
        assert quotes(hostile) in [atom.raw for atom in atoms]
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
        (label,) = J.assert_inserted(before, kicad_native_sch.read_bytes(), heads=["global_label"])
        assert label.path == ROOT
        gl = next(g for g in reparse(kicad_native_sch).globalLabels if g.text == "GNET")
        assert gl.shape == "bidirectional"
        assert gl.position.angle == 90

    def test_hierarchical_label(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        schematic.add_hierarchical_label(
            "HNET", "output", 25.4, 30, schematic_path=str(kicad_native_sch)
        )
        after = kicad_native_sch.read_bytes()
        (label,) = J.assert_inserted(before, after, heads=["hierarchical_label"])
        assert label.path == ROOT
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
        after = kicad_native_sch.read_bytes()
        (text,) = J.assert_inserted(before, after, heads=["text"])
        assert text.path == ROOT
        item = J.item_at(after, text.location)
        assert item.items[1].value == b"note here"
        # KiCad reads a schematic coordinate to its 100 nm unit (parseInternalUnits), so
        # the text lands where KiCad rounds the requested point to. How the number is
        # spelled is bug 61's question (test_ladder_r2_numbers.py).
        (at,) = [c for c in item.items if c.head == "at"]
        for major in (9, 10):
            for atom, requested in zip(at.items[1:3], (100.123456, 50)):
                assert P.parse_sch_internal_units(atom.value, major=major) == U.kiround(
                    requested * U.SCH_IU_PER_MM, major=major
                ), (major, atom.raw)

    def test_junctions_two_points_one_write(self, kicad_native_sch):
        # Three wire ends meet at each point, the joinable kind of point KiCad's editor
        # takes a junction at, and no wire runs through either, so nothing is split.
        _tee(kicad_native_sch, (50.8, 50.8), (-5.08, 0), (5.08, 0), (0, -5.08))
        _tee(kicad_native_sch, (96.19, 100.5), (-5.08, 0), (0, -5.08), (0, 5.08))
        before = kicad_native_sch.read_bytes()
        schematic.add_junctions(
            [{"x": 50.8, "y": 50.8}, {"x": 96.19, "y": 100.5}],
            schematic_path=str(kicad_native_sch),
        )
        after = kicad_native_sch.read_bytes()
        junctions = J.assert_inserted(before, after, heads=["junction", "junction"])
        assert [j.path for j in junctions] == [ROOT, ROOT]
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


class TestRemoveFamilyPreservation:
    """Slice 4: removal/modify via CST one-span deletions and substitutions."""

    def test_remove_label_local_and_global(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_global_label("MIX", 60, 90, schematic_path=p)
        schematic.add_label("MIX", 60, 92, schematic_path=p)
        result = schematic.remove_label("MIX", schematic_path=p)
        assert "1 label(s)" in result and "1 global label(s)" in result
        sch = reparse(kicad_native_sch)
        assert not any(lbl.text == "MIX" for lbl in sch.labels)
        assert not any(gl.text == "MIX" for gl in sch.globalLabels)

    def test_remove_label_position_filter_no_over_deletion(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_label("NEAR", 49.61, 50, schematic_path=p)
        schematic.add_label("NEAR", 49.77, 50, schematic_path=p)
        result = schematic.remove_label("NEAR", x=49.77, y=50, schematic_path=p)
        assert "Removed 1" in result
        remaining = [lbl for lbl in reparse(kicad_native_sch).labels if lbl.text == "NEAR"]
        assert len(remaining) == 1
        assert abs(remaining[0].position.X - 49.61) < 0.01

    def test_remove_junction_single_span(self, kicad_native_sch):
        p = str(kicad_native_sch)
        # Spliced as a node, not through add_junctions, which refuses a point where nothing
        # joins; nothing ends there, so the delete merges nothing and is one removal.
        RF.junction(p, 50.8, 50.8)
        before = kicad_native_sch.read_bytes()
        assert "Removed junction" in schematic.remove_junction(50.8, 50.8, schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert len(after) < len(before)
        (junction,) = J.assert_removed(before, after, heads=["junction"])
        assert junction.path == ROOT
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_junction(50.8, 50.8, schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_remove_text(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_text("gone", 60, 90, schematic_path=p)
        before = kicad_native_sch.read_bytes()
        assert "Removed 1 text(s)" in schematic.remove_text("gone", schematic_path=p)
        (text,) = J.assert_removed(before, kicad_native_sch.read_bytes(), heads=["text"])
        assert text.path == ROOT
        assert not any(t.text == "gone" for t in reparse(kicad_native_sch).texts)

    def test_remove_hierarchical_label_by_uuid(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_hierarchical_label("DUP", "input", 60, 90, schematic_path=p)
        schematic.add_hierarchical_label("DUP", "output", 60, 92, schematic_path=p)
        second_uuid = reparse(kicad_native_sch).hierarchicalLabels[1].uuid
        assert second_uuid is not None
        result = schematic.remove_hierarchical_label("DUP", schematic_path=p, uuid=second_uuid)
        assert "Removed hierarchical label 'DUP'" in result
        remaining = reparse(kicad_native_sch).hierarchicalLabels
        assert len(remaining) == 1
        assert remaining[0].shape == "input"

    def test_modify_hierarchical_label_substitution(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_hierarchical_label("VIN", "input", 25.4, 30, schematic_path=p)
        before = kicad_native_sch.read_bytes()
        result = schematic.modify_hierarchical_label(
            "VIN", schematic_path=p, new_text="VIN_PROT", new_shape="output"
        )
        assert "VIN_PROT" in result and "output" in result
        hl = reparse(kicad_native_sch).hierarchicalLabels[0]
        assert hl.text == "VIN_PROT"
        assert hl.shape == "output"
        # Two atom substitutions land inside the label node: everything before
        # and after that node is untouched.
        after = kicad_native_sch.read_bytes()
        assert after != before
        assert b"generator_version" in after and b"(embedded_fonts" in after


def _wires_of(sch):
    return [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"]


class TestWiresPreservation:
    """Slice 5: add_wires/remove_wire via CST splices. Fixture wire: (50,100)-(80,100)."""

    def test_add_wires_pure_insertion(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        n = len(_wires_of(reparse(kicad_native_sch)))
        schematic.add_wires(
            [{"x1": 96.19, "y1": 20.5, "x2": 103.81, "y2": 20.5}],
            schematic_path=str(kicad_native_sch),
        )
        after = kicad_native_sch.read_bytes()
        # Far from the fixture wire, so no junction belongs with it.
        (wire,) = J.assert_inserted(before, after, heads=["wire"])
        assert wire.path == ROOT
        assert b"generator_version" in after and b"(embedded_fonts" in after
        wires = _wires_of(reparse(kicad_native_sch))
        assert len(wires) == n + 1
        new = wires[-1]
        assert (new.points[0].X, new.points[0].Y) == (96.19, 20.5)
        assert (new.points[1].X, new.points[1].Y) == (103.81, 20.5)

    def test_auto_junction_on_t_connection(self, kicad_native_sch):
        schematic.add_wires(
            [{"x1": 65, "y1": 80, "x2": 65, "y2": 100}],
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(kicad_native_sch)
        assert (65, 100) in [(j.position.X, j.position.Y) for j in sch.junctions]

    def test_auto_junction_on_t_connection_splits_the_bar(self, kicad_native_sch):
        """KiCad's form of the T: the fixture wire (50,100)-(80,100) keeps its uuid and start
        and ends at (65,100), a new wire with the same stroke runs (65,100)-(80,100), and the
        stem and a junction at (65,100) are inserted whole; nothing else changes and no
        junction lies inside a wire (sch_judge.assert_split_edit, KiCad's lexer)."""
        before = kicad_native_sch.read_bytes()
        schematic.add_wires(
            [{"x1": 65, "y1": 80, "x2": 65, "y2": 100}],
            schematic_path=str(kicad_native_sch),
        )
        after = kicad_native_sch.read_bytes()
        (split,) = S.assert_split_edit(before, after, heads=["wire", "wire", "junction"])
        tee = S.iu_point((65, 100))
        assert (split.old.start, split.old.end) == (S.iu_point((50, 100)), S.iu_point((80, 100)))
        assert split.point == tee
        assert tee in S.junctions(after)
        assert S.junctions_on_interiors(after) == []

    def test_no_junction_at_wire_endpoint(self, kicad_native_sch):
        schematic.add_wires(
            [{"x1": 80, "y1": 80, "x2": 80, "y2": 100}],
            schematic_path=str(kicad_native_sch),
        )
        sch = reparse(kicad_native_sch)
        assert len(_wires_of(sch)) == 2
        assert sch.junctions == []

    def test_remove_wire_reversed_and_span(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.add_wires([{"x1": 20, "y1": 20, "x2": 30, "y2": 20}], schematic_path=p)
        before = kicad_native_sch.read_bytes()
        n = len(_wires_of(reparse(kicad_native_sch)))
        assert "Removed 1 wire(s)." == schematic.remove_wire(30, 20, 20, 20, schematic_path=p)
        after = kicad_native_sch.read_bytes()
        (wire,) = J.assert_removed(before, after, heads=["wire"])
        assert wire.path == ROOT
        assert len(_wires_of(reparse(kicad_native_sch))) == n - 1
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_wire(30, 20, 20, 20, schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_add_wires_all_validated_before_any_write(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        with pytest.raises(ToolError, match="outside the sheet boundary"):
            schematic.add_wires(
                [
                    {"x1": 20, "y1": 20, "x2": 30, "y2": 20},
                    {"x1": 9999, "y1": 20, "x2": 30, "y2": 20},
                ],
                schematic_path=str(kicad_native_sch),
            )
        assert kicad_native_sch.read_bytes() == before


class TestHybridRoutingPreservation:
    """Slices 6+7: routing trio on the substrate; slice 7 moved the pin reads
    to a CST walk, so the trio is guard-free and single-parse.
    Fixture R1 at (100,100): pin 1 at (100, 96.19), pin 2 at (100, 103.81)."""

    def test_no_connect_pure_insertion(self, kicad_native_sch):
        before = kicad_native_sch.read_bytes()
        result = schematic.no_connect_pin("R1", "1", schematic_path=str(kicad_native_sch))
        assert result == "No-connect on R1:1 at (100.0, 96.19)"
        after = kicad_native_sch.read_bytes()
        (flag,) = J.assert_inserted(before, after, heads=["no_connect"])
        assert flag.path == ROOT
        assert b"generator_version" in after and b"(embedded_fonts" in after
        nc = reparse(kicad_native_sch).noConnects[0]
        assert (nc.position.X, nc.position.Y) == (100.0, 96.19)

    def test_no_connect_idempotent_no_write(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.no_connect_pin("R1", "1", schematic_path=p)
        mid = kicad_native_sch.read_bytes()
        assert "already present" in schematic.no_connect_pin("R1", "1", schematic_path=p)
        assert kicad_native_sch.read_bytes() == mid

    def test_remove_no_connect_span(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.no_connect_pin("R1", "1", schematic_path=p)
        before = kicad_native_sch.read_bytes()
        assert "Removed 1 no-connect" in schematic.remove_no_connect("R1", "1", schematic_path=p)
        after = kicad_native_sch.read_bytes()
        (flag,) = J.assert_removed(before, after, heads=["no_connect"])
        assert flag.path == ROOT
        assert reparse(kicad_native_sch).noConnects == []
        with pytest.raises(ToolError, match="No no-connect"):
            schematic.remove_no_connect("R1", "1", schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_connect_pins_straight_and_label(self, kicad_native_sch):
        p = str(kicad_native_sch)
        result = schematic.connect_pins("R1", "1", "R1", "2", schematic_path=p)
        assert result == "Connected R1:1 -> R1:2 via 1 wire segment"
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after
        assert _cst.serialize(_cst.parse(after)) == after
        sch = reparse(kicad_native_sch)
        new = _wires_of(sch)[-1]
        assert (new.points[0].X, new.points[0].Y) == (100.0, 96.19)
        assert (new.points[1].X, new.points[1].Y) == (100.0, 103.81)
        assert any(lbl.text == "Net-(R1-1)" for lbl in sch.labels)

    @pytest.mark.no_kicad_validation
    def test_future_version_file_routing_tools_work(self, kicad_native_sch):
        # Slice 7: pin reads went CST, so the routing trio is guard-free.
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        before = kicad_native_sch.read_bytes()
        schematic.no_connect_pin("R1", "1", schematic_path=p)
        (flag,) = J.assert_inserted(before, kicad_native_sch.read_bytes(), heads=["no_connect"])
        assert flag.path == ROOT
        assert "Removed 1 no-connect" in schematic.remove_no_connect("R1", "1", schematic_path=p)
        schematic.connect_pins("R1", "1", "R1", "2", schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert len(root.find_all("wire")) == 2
        assert "Net-(R1-1)" in [n.atoms[1].text for n in root.find_all("label")]

    def test_pin_pos_cst_is_kicads_pin_end_on_both_file_shapes(self, scratch_sch, kicad_native_sch):
        # The CST walk gives each pin's exact end and outward direction as the transform
        # port computes them from the file's own tokens (test_ladder_r2_pins), on both
        # file shapes, through rotation and mirror.
        from mcp_server_kicad.schematic import _get_pin_pos_cst

        def check(path, label) -> None:
            data = path.read_bytes()
            root = _cst.parse(data).lists[0]
            (ends,) = port_pin_ends(data, "R1")
            assert sorted(pin_map(ends)) == ["1", "2"]
            for end in ends:
                assert _get_pin_pos_cst(root, "R1", end.number) == pin_pos_of(end), (label, end)

        for path in (scratch_sch, kicad_native_sch):
            check(path, path.name)
        # Rotation via a real tool, then mirror via a hand-spliced token; the port reads
        # the file each time, so both must land where KiCad reads them.
        schematic.move_component("R1", 90, 80, rotation=90, schematic_path=str(scratch_sch))
        for mutate_mirror in (False, True):
            if mutate_mirror:
                data = scratch_sch.read_bytes()
                assert b"(mirror" not in data
                scratch_sch.write_bytes(data.replace(b"(unit 1)", b"(mirror x) (unit 1)", 1))
            (r1,) = [
                s
                for s in placed_symbols(lexed_root(scratch_sch.read_bytes()))
                if reference_property(s) == "R1"
            ]
            state = [("at", "90")] + ([("mirror", "x")] if mutate_mirror else [])
            assert symbol_placement(r1)[1] == T.parse_symbol_transform(state)
            check(scratch_sch, state)
        root = _cst.parse(scratch_sch.read_bytes()).lists[0]
        with pytest.raises(ValueError, match="Component X99 not found"):
            _get_pin_pos_cst(root, "X99", "1")
        with pytest.raises(ValueError, match="Pin 'NOPE' not found on R1"):
            _get_pin_pos_cst(root, "R1", "NOPE")

    def test_pin_pos_cst_on_a_multi_unit_part_is_kicads_pin_end(self, tmp_path):
        # A part placed as two symbols sharing one reference, whose units put their pins
        # at identical body-local coordinates (make_dual_unit_sch): unit 1, placed first,
        # draws pins 1 and 2, unit 2 pins 3 and 4, and both draw the unit-0 pins 5 and 6,
        # which the first placed unit answers for. Each answer is the port's end of that
        # pin on that placed symbol, from the file's own tokens.
        from mcp_server_kicad.schematic import _get_pin_pos_cst

        path = Path(make_dual_unit_sch(tmp_path))
        data = path.read_bytes()
        root = _cst.parse(data).lists[0]
        unit1, unit2 = port_pin_ends(data, "U1")
        assert sorted(pin_map(unit1)) == ["1", "2", "5", "6"]
        assert sorted(pin_map(unit2)) == ["3", "4", "5", "6"]
        drawn_by = {"1": unit1, "2": unit1, "3": unit2, "4": unit2, "5": unit1, "6": unit1}
        for pin, ends in drawn_by.items():
            (end,) = [e for e in ends if e.number == pin]
            assert _get_pin_pos_cst(root, "U1", pin) == pin_pos_of(end), pin
        assert _get_pin_pos_cst(root, "U1", "4")[:2] == (155.08, 100.0)
        assert _get_pin_pos_cst(root, "U1", "6")[:2] == (100.0, 92.38)


def _power_in_sch(tmp_path):
    """Schematic with a placed power_in VCC symbol #PWR01 at (100, 100)."""
    from conftest import make_power_sch

    return Path(make_power_sch(tmp_path))


class TestWirePinsToNetPreservation:
    """Slice 8: wire_pins_to_net on the substrate, plus the verbatim
    system-lib copy its PWR_FLAG used to exercise (now via place_component)."""

    def test_stub_and_label_preservation(self, kicad_native_sch):
        p = str(kicad_native_sch)
        result = schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="NETX",
            direction="up",
            schematic_path=p,
        )
        assert result.splitlines()[0] == "Wired 1 pins to 'NETX'."
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after
        assert _cst.serialize(_cst.parse(after)) == after
        sch = reparse(kicad_native_sch)
        new = _wires_of(sch)[-1]
        assert (new.points[0].X, new.points[0].Y) == (100.0, 96.19)
        assert (new.points[1].X, new.points[1].Y) == (100.0, 93.65)
        lbl = next(lbl for lbl in sch.labels if lbl.text == "NETX")
        assert (lbl.position.X, lbl.position.Y, lbl.position.angle) == (100.0, 93.65, 90)

    def test_pwr_flag_lib_copy_verbatim(self, tmp_path):
        lib_path = _resolve_system_lib("power")
        if lib_path is None:
            pytest.skip("no KiCad system symbol library on this host")
        path = _power_in_sch(tmp_path)
        schematic.place_component(
            lib_id="power:PWR_FLAG",
            reference="#FLG01",
            value="PWR_FLAG",
            x=110,
            y=100,
            schematic_path=str(path),
        )

        def symbol_bytes(data: bytes, *labels: str, name: bytes) -> bytes:
            # The node's bytes from its '(' to its ')' as KiCad's lexer delimits them,
            # so a paren inside a string cannot cut it short and a CR stays a CR.
            node = _the(data, *labels, where=lambda loc, item: item.items[1].value == name)
            return J.item_at(data, node).raw

        lib = Path(lib_path).read_bytes()
        want = symbol_bytes(lib, "kicad_symbol_lib", "symbol", name=b"PWR_FLAG")
        got = symbol_bytes(
            path.read_bytes(), "kicad_sch", "lib_symbols", "symbol", name=b"power:PWR_FLAG"
        )
        # Verbatim except the name atom, which gains the lib prefix (the way
        # KiCad imports symbols; a bare name segfaults kicad-cli 9.0 on load).
        assert got.replace(b'"power:PWR_FLAG"', b'"PWR_FLAG"', 1) == want

    @pytest.mark.no_kicad_validation
    def test_future_version_file_wire_pins_to_net_works(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        assert "Wired 1 pins" in schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="K10NET", schematic_path=p
        )
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert "K10NET" in [n.atoms[1].text for n in root.find_all("label")]


class TestPlaceComponentPreservation:
    """Slice 10: place_component and the root-instance helpers on the substrate."""

    def test_place_from_custom_lib_pure_insertions(self, kicad_native_sch, scratch_sym_lib):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        result = schematic.place_component(
            lib_id="Test:TestPart",
            reference="U1",
            value="TP",
            x=150,
            y=150,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=p,
        )
        assert "Placed U1 (TP) at (149.86, 149.86)" == result
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after
        assert _cst.serialize(_cst.parse(after)) == after
        assert before[: len(before) // 3] == after[: len(before) // 3]
        # Two whole items and nothing else: the definition into lib_symbols, the
        # placed symbol among the sheet's items (no .kicad_pro, so no root index).
        edits = J.assert_inserted(before, after, heads=["symbol", "symbol"], contiguous=False)
        assert {e.path for e in edits} == {ROOT, _the(before, "kicad_sch", "lib_symbols")}
        sch = reparse(kicad_native_sch)
        u1 = next(
            s
            for s in sch.schematicSymbols
            if any(pr.key == "Reference" and pr.value == "U1" for pr in s.properties)
        )
        assert u1.libId == "Test:TestPart"
        assert u1.position.X == 149.86 and u1.position.Y == 149.86
        assert set(u1.pins) == {"1", "2"}
        assert u1.instances and u1.instances[0].paths[0].reference == "U1"
        assert any(pr.key == "Value" and pr.value == "TP" for pr in u1.properties)

    def test_place_reuses_existing_lib_symbol(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.place_component(
            lib_id="Device:R", reference="R2", value="4.7K", x=60, y=60, schematic_path=p
        )
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        entries = root.find("lib_symbols").find_all("symbol")
        assert len(entries) == 1  # reused, not duplicated
        from mcp_server_kicad._shared import _sym_property_cst

        r2 = next(s for s in root.find_all("symbol") if _sym_property_cst(s, "Reference") == "R2")
        assert r2.find("lib_name").atoms[1].text == "Device:R"
        assert r2.find("lib_id").atoms[1].text == "Device:R"

    def test_place_missing_symbol_error_bytes_untouched(self, kicad_native_sch, scratch_sym_lib):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        with pytest.raises(ToolError, match="not found in Test library"):
            schematic.place_component(
                lib_id="Test:Nonexistent",
                reference="U9",
                value="X",
                x=60,
                y=60,
                symbol_lib_path=str(scratch_sym_lib),
                schematic_path=p,
            )
        assert kicad_native_sch.read_bytes() == before

    def test_place_rotation_and_mirror_pin_ends_are_kicads(self, kicad_native_sch, scratch_sym_lib):
        from mcp_server_kicad.schematic import _get_pin_pos_cst

        p = str(kicad_native_sch)
        schematic.place_component(
            lib_id="Test:TestPart",
            reference="U1",
            value="TP",
            x=63.5,
            y=63.5,
            rotation=90,
            mirror="x",
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=p,
        )
        sch = reparse(kicad_native_sch)
        u1 = next(
            s
            for s in sch.schematicSymbols
            if any(pr.key == "Reference" and pr.value == "U1" for pr in s.properties)
        )
        assert u1.position.angle == 90
        assert u1.mirror == "x"
        # Each pin read is the port's end on the file's own tokens. By hand: rotating 90
        # and then mirroring x is the matrix (0, 1, 1, 0), which swaps x and y, so IN,
        # written at (-5.08, 0), library y up, ends 5.08 above (63.5, 63.5) and OUT 5.08
        # below it.
        data = kicad_native_sch.read_bytes()
        root = _cst.parse(data).lists[0]
        (ends,) = port_pin_ends(data, "U1")
        assert sorted(end.name for end in ends) == ["IN", "OUT"]
        for end in ends:
            assert _get_pin_pos_cst(root, "U1", end.name) == pin_pos_of(end), end.name
        assert _get_pin_pos_cst(root, "U1", "IN")[:2] == (63.5, 58.42)
        assert _get_pin_pos_cst(root, "U1", "OUT")[:2] == (63.5, 68.58)

    def test_root_symbol_instances_upsert_and_remove(self, kicad_native_sch):
        # A .kicad_pro sibling makes this file its own root: the helpers write
        # a symbol_instances section here via the CST now.
        pro = kicad_native_sch.with_suffix(".kicad_pro")
        pro.write_text("{}")
        p = str(kicad_native_sch)
        schematic.place_component(
            lib_id="Device:R", reference="R2", value="4.7K", x=60, y=60, schematic_path=p
        )
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        si = root.find("symbol_instances")
        assert si is not None
        entry = si.find("path")
        assert entry.find("reference").atoms[1].text == "R2"
        assert entry.find("value").atoms[1].text == "4.7K"
        schematic.set_component_property("R2", "Value", "9.1K", schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        entry = root.find("symbol_instances").find("path")
        assert entry.find("value").atoms[1].text == "9.1K"
        schematic.remove_component("R2", schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        # Last entry removed drops the whole section, like the kiutils writer.
        assert root.find("symbol_instances") is None


class TestSymbolFamilyPreservation:
    """Slice 9: in-place symbol-family tools on the substrate."""

    def test_add_lib_symbol_pure_insertion(self, kicad_native_sch, scratch_power_lib):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        result = schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert result == "Added 'VCC' to lib_symbols."
        after = kicad_native_sch.read_bytes()
        (entry,) = J.assert_inserted(before, after, heads=["symbol"])
        assert entry.path == _the(before, "kicad_sch", "lib_symbols")
        assert any(ls.entryName == "VCC" for ls in reparse(kicad_native_sch).libSymbols)
        with pytest.raises(ToolError, match="already in lib_symbols"):
            schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert kicad_native_sch.read_bytes() == after
        with pytest.raises(ToolError, match="not found in"):
            schematic.add_lib_symbol(str(scratch_power_lib), "NOPE", schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_move_component_substitution(self, kicad_native_sch):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        assert "Moved R1" in schematic.move_component("R1", 63.5, 63.5, schematic_path=p)
        after = kicad_native_sch.read_bytes()
        # A move edits the moved symbol and nothing else on the sheet, and inside it only
        # positions: every atom it changes sits in an (at ...) list. Which of R1's (at ...)
        # lists move is test_move_component_carries_its_fields's question, not this one's.
        J.assert_confined(before, after, _the(before, "kicad_sch", "symbol"))
        changes = J.atom_changes(before, after)
        assert changes, "the move changed no atom"
        assert [c for c in changes if c.path[-1][1] != "at"] == [], "a move changed a non-position"
        sym = reparse(kicad_native_sch).schematicSymbols[0]
        assert (sym.position.X, sym.position.Y, sym.position.angle) == (63.5, 63.5, 0)
        schematic.move_component("R1", 63.5, 63.5, rotation=90, schematic_path=p)
        assert reparse(kicad_native_sch).schematicSymbols[0].position.angle == 90

    def test_move_component_carries_its_fields(self, kicad_native_sch):
        """A symbol's fields travel with it.

        KiCad writes a field's (at ...) from SCH_FIELD::GetPosition (sch_io_kicad_sexpr.cpp
        910-918 at 9.0.8, 1019-1027 at 10.0.7), which is the field's drawn position on the
        sheet, its offset from the symbol plus the symbol's own position (sch_field.cpp
        1615-1628 at 9.0.8, 1398-1411 at 10.0.7). SCH_SYMBOL::Move translates the symbol
        and every field by the same vector (sch_symbol.h:701-710 at 9.0.8, 788-797 at
        10.0.7). So a pure translation adds the symbol's displacement to each field's x
        and y, keeps each field's angle, and changes nothing outside the symbol.
        Positions are compared in schematic internal units, as KiCad reads them, not as
        text.
        """
        before = kicad_native_sch.read_bytes()
        schematic.move_component("R1", 63.5, 63.5, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        r1 = _the(before, "kicad_sch", "symbol")
        J.assert_confined(before, after, r1)

        def iu(mm: float) -> int:
            # KiROUND's two majors differ only at its clamp and at one tie (eda_units).
            return U.kiround(mm * U.SCH_IU_PER_MM, major=9)

        def placement(item):
            # An item's own (at x y angle): x and y in internal units, the angle as written.
            (at,) = [c for c in item.items if c.head == "at"]
            x, y = (iu(float(atom.value)) for atom in at.items[1:3])
            return x, y, tuple(atom.raw for atom in at.items[3:])

        def fields(symbol):
            return {p.items[1].value: placement(p) for p in symbol.items if p.head == "property"}

        old, new = J.item_at(before, r1), J.item_at(after, r1)
        (x0, y0, _), (x1, y1, _) = placement(old), placement(new)
        if (x1, y1) != (iu(63.5), iu(63.5)):
            raise RuntimeError(f"R1 landed at {(x1, y1)} IU, not where the call put it")
        dx, dy = x1 - x0, y1 - y0
        expected = {name: (x + dx, y + dy, angle) for name, (x, y, angle) in fields(old).items()}
        assert fields(new) == expected, "the symbol moved and its fields stayed where they were"

    @pytest.mark.parametrize("move", _MOVES, ids=[m[0] for m in _MOVES])
    @pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
    def test_move_changes_only_position_atoms(self, tmp_path, newline, move):
        """Moving the native fixture's R1 (translated, and translated and turned) changes
        exactly the atoms KiCad's rule moves (expected_move_changes): its last (at)'s x, y
        and angle where they change, and each property's x and y where the field, kept at
        its offset in the symbol's frame, lands elsewhere; no text angle, no
        (fields_autoplaced), no separator. On LF and on CRLF line endings alike."""
        _, target, rotation = move
        data = FIXTURE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
        path = tmp_path / "kicad_native.kicad_sch"
        path.write_bytes(data)
        schematic.move_component(
            "R1", _mm(target[0]), _mm(target[1]), rotation=rotation, schematic_path=str(path)
        )
        check_move_changes(data, path.read_bytes(), "R1", target, rotation)

    @pytest.mark.parametrize("order", _ORDER_PARAMS)
    def test_move_reads_properties_in_kicads_file_order(self, tmp_path, order):
        """A symbol whose children KiCad loads in an order it never writes (every property
        before the (at); the (mirror) after the properties; a second (at) after them),
        moved by (12.7, 25.4) and turned: KiCad reads it at the target with the new angle and
        the mirror after its last (at), and reads every field at the offset in the symbol's
        frame it had before (kicad_ref.sch_field, which reads each property in the frame in
        force where it sits). The properties-first order passes today: its properties are
        read before any (at), so their (at)s must not change at all."""
        path = E.write_symbol_sheet(tmp_path / "order.kicad_sch", [_order_symbol(order)])
        before = path.read_bytes()
        _, old = S.placed_symbol(before, "R1")
        target, rotation, transform = _order_move(order, old)
        message = schematic.move_component(
            "R1", _mm(target[0]), _mm(target[1]), rotation=rotation, schematic_path=str(path)
        )
        _, new = S.placed_symbol(path.read_bytes(), "R1")
        assert (new.position, new.transform) == (target, transform), (
            f"returned {message!r}, and KiCad reads R1 at {new.position}, {new.transform}"
        )
        assert {n: f.offset for n, f in new.fields.items()} == {
            n: f.offset for n, f in old.fields.items()
        }, "the symbol moved and its fields did not keep their place on it"

    @requires_cli
    @pytest.mark.parametrize("order", _ORDER_PARAMS)
    def test_move_in_kicads_file_order_where_kicad_draws_it(self, tmp_path, order):
        """The kicad-cli sibling: in its SVG, each shown field of that symbol moves rigidly
        with it and turns with it (test_ladder_r3_symbols.rigid_problems)."""
        before = E.write_symbol_sheet(tmp_path / "order.kicad_sch", [_order_symbol(order)])
        _, old = S.placed_symbol(before.read_bytes(), "R1")
        target, rotation, transform = _order_move(order, old)
        moves = {"R1": Move(target, rotation, transform, ("R1", "V1"))}
        after = tmp_path / "moved.kicad_sch"
        move_all(before, after, moves)
        problems = rigid_problems(before, after, moves)
        assert not problems, "\n".join(problems)

    def test_move_rewrites_the_at_kicad_keeps(self, tmp_path):
        """R1 written with two (at) lists, (100, 100) and then (127, 127), which kicad-cli
        9.0.8 loads (measured 2026-10-14): KiCad's parser keeps the last, since each (at)
        sets the position and the transform anew (sch_io_kicad_sexpr_parser.cpp:3071-3085
        at 9.0.8, 3188-3202 at 10.0.7). After a move to (63.5, 63.5) KiCad reads R1 there."""
        path = _r1_variant(tmp_path, "two_at.kicad_sch", b"(at 100 100 0) (at 127 127 0) (unit 1)")
        _, old = S.placed_symbol(path.read_bytes(), "R1")
        if old.position != (1_270_000, 1_270_000):
            raise RuntimeError(f"the premise is KiCad reading the last (at); read {old.position}")
        message = schematic.move_component("R1", 63.5, 63.5, schematic_path=str(path))
        _, new = S.placed_symbol(path.read_bytes(), "R1")
        assert new.position == (635_000, 635_000), (
            f"returned {message!r}, and KiCad reads R1 at {new.position}"
        )

    def test_move_refuses_a_symbol_without_at(self, tmp_path):
        """R1 written with no (at), which kicad-cli 9.0.8 loads (measured 2026-10-14):
        move_component refuses with a ToolError, the file byte for byte as it was, rather
        than letting an exception from inside escape."""
        path = _r1_variant(tmp_path, "no_at.kicad_sch", b"(unit 1)")
        before = path.read_bytes()
        result, raised = _outcome(
            lambda: schematic.move_component("R1", 63.5, 63.5, schematic_path=str(path))
        )
        assert isinstance(raised, ToolError), f"returned {result!r}, raised {raised!r}"
        assert path.read_bytes() == before

    @pytest.mark.kicad_placeholder("angle45.kicad_sch")
    def test_unloadable_angle_needs_a_rotation(self, tmp_path):
        """R1 at angle 45, which KiCad cannot load (its parser takes 0, 90, 180 and 270
        only, sch_io_kicad_sexpr_parser.cpp:3074-3081 at 9.0.8, 3191-3198 at 10.0.7), the
        premise checked with kicad-cli: a move with no rotation refuses with a ToolError that
        names the rotation as the repair, and leaves the file byte for byte as it was. The
        unloadable input is this test's subject, so the oracle skips it."""
        path = _r1_variant(tmp_path, "angle45.kicad_sch", b"(at 100 100 45) (unit 1)")
        _require_unloadable(path)
        before = path.read_bytes()
        result, raised = _outcome(
            lambda: schematic.move_component("R1", 63.5, 63.5, schematic_path=str(path))
        )
        assert isinstance(raised, ToolError), (
            f"returned {result!r}, raised {raised!r}, on a file kicad-cli cannot load"
        )
        assert "rotation" in str(raised), f"the refusal names no repair: {raised}"
        assert path.read_bytes() == before

    def test_unloadable_angle_is_repaired_by_a_rotation(self, tmp_path):
        """R1 at angle 45, moved to (63.5, 63.5) at rotation 90: KiCad reads R1 there at 90,
        and every field moved by R1's own displacement and was not turned, since an angle
        KiCad cannot load gives the old orientation no meaning; the result says R1 was at
        45. The oracle then checks that kicad-cli loads the repaired file."""
        path = _r1_variant(tmp_path, "angle45.kicad_sch", b"(at 100 100 45) (unit 1)")
        _require_unloadable(path)
        written = _written_fields(path.read_bytes(), "R1")
        message = schematic.move_component("R1", 63.5, 63.5, rotation=90, schematic_path=str(path))
        _, new = S.placed_symbol(path.read_bytes(), "R1")
        assert (new.position, new.transform) == ((635_000, 635_000), turned((0, ""), 90))
        dx, dy = 635_000 - 1_000_000, 635_000 - 1_000_000
        moved = {name: (x + dx, y + dy, angle) for name, ((x, y), angle) in written.items()}
        assert {n: (*f.written, f.angle) for n, f in new.fields.items()} == moved, (
            "the fields did not move with the repaired symbol"
        )
        assert "45" in message, f"the result does not say R1 was at angle 45: {message!r}"

    def test_unloadable_angle_remedy_names_a_repair_that_works(self, tmp_path):
        """A pin read of R1 at angle 45 refuses [unloadable]. KiCad cannot open the sheet,
        so a remedy pointing at KiCad cannot be followed; the one that works is
        move_component with a legal rotation, which the refusal must name. Following it
        leaves a file kicad-cli loads and the pin read then answers."""
        path = _r1_variant(tmp_path, "angle45.kicad_sch", b"(at 100 100 45) (unit 1)")
        _require_unloadable(path)
        with pytest.raises(ToolError) as refused:
            schematic.get_pin_positions("R1", schematic_path=str(path))
        assert "move_component" in str(refused.value), (
            f"the remedy names no repair that works: {refused.value}"
        )
        schematic.move_component("R1", 100, 100, rotation=0, schematic_path=str(path))
        if HAS_KICAD_CLI:
            assert_kicad_parseable(path)
        schematic.get_pin_positions("R1", schematic_path=str(path))

    def test_set_component_property_existing_and_new(self, kicad_native_sch):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        assert "Set R1.Value = 22K" in schematic.set_component_property(
            "R1", "Value", "22K", schematic_path=p
        )
        after = kicad_native_sch.read_bytes()
        # Setting a field's value rewrites that one atom.
        value = _the(
            before,
            "kicad_sch",
            "symbol",
            "property",
            where=lambda loc, p: p.items[1].value == b"Value",
        )
        assert J.atom_changes(before, after) == [J.AtomChange(value, 2, b'"10K"', b'"22K"')]
        sym = reparse(kicad_native_sch).schematicSymbols[0]
        assert any(pr.key == "Value" and pr.value == "22K" for pr in sym.properties)
        assert "(new property)" in schematic.set_component_property(
            "R1", "MPN", "RC0805", schematic_path=p
        )
        sym = reparse(kicad_native_sch).schematicSymbols[0]
        mpn = next(pr for pr in sym.properties if pr.key == "MPN")
        assert mpn.value == "RC0805"
        schematic.set_component_property("R1", "Reference", "R9", schematic_path=p)
        sym = reparse(kicad_native_sch).schematicSymbols[0]
        assert sym.instances[0].paths[0].reference == "R9"

    def test_remove_component_span(self, kicad_native_sch):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        assert schematic.remove_component("R1", schematic_path=p) == "Removed R1"
        after = kicad_native_sch.read_bytes()
        (r1,) = J.assert_removed(before, after, heads=["symbol"])
        assert r1.path == ROOT
        assert reparse(kicad_native_sch).schematicSymbols == []
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_component("R1", schematic_path=p)
        assert kicad_native_sch.read_bytes() == after

    def test_set_page_size_swap(self, kicad_native_sch):
        p = str(kicad_native_sch)
        before = kicad_native_sch.read_bytes()
        assert "A3" in schematic.set_page_size("A3", schematic_path=p)
        mid = kicad_native_sch.read_bytes()
        paper_node = _the(before, "kicad_sch", "paper")
        assert J.atom_changes(before, mid) == [J.AtomChange(paper_node, 1, b'"A4"', b'"A3"')]
        assert reparse(kicad_native_sch).paper.paperSize == "A3"
        result = schematic.set_page_size(
            "User", width=200, height=150, portrait=True, schematic_path=p
        )
        assert "portrait" in result
        J.assert_confined(mid, kicad_native_sch.read_bytes(), paper_node)
        paper = reparse(kicad_native_sch).paper
        assert (paper.paperSize, paper.width, paper.height) == ("User", 200, 150)
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after


class TestProjectToolsPreservation:
    """Slice 11: project.py sheet/annotate/composite tools on the substrate."""

    @staticmethod
    def _hierarchy(tmp_path, kicad_native_sch):
        parent = kicad_native_sch
        child = tmp_path / "child.kicad_sch"
        project.create_schematic(str(child))
        return parent, child

    def test_create_schematic_native_shape(self, tmp_path):
        out = tmp_path / "fresh.kicad_sch"
        project.create_schematic(str(out))
        data = out.read_bytes()
        assert b"generator_version" in data and b"(embedded_fonts no)" in data
        assert _cst.serialize(_cst.parse(data)) == data
        with pytest.raises(ToolError, match="already exists"):
            project.create_schematic(str(out))

    def test_add_hierarchical_sheet_preserves_both_files(self, tmp_path, kicad_native_sch):
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        p_before = parent.read_bytes()
        c_before = child.read_bytes()
        project.add_hierarchical_sheet(
            str(parent), "sub", str(child), [{"name": "A", "direction": "input"}]
        )
        p_after = parent.read_bytes()
        assert b"generator_version" in p_after and b"(embedded_fonts" in p_after
        assert p_after[: len(p_before) // 3] == p_before[: len(p_before) // 3]
        # Whole items added to each file and nothing else: in the parent the sheet
        # plus, for its one pin, a wire stub and the label naming it; in the child the
        # matching hierarchical label plus its own stub and label (the tool's design,
        # one stub and label per pin on each side).
        for before, after, heads in (
            (p_before, p_after, ["sheet", "wire", "label"]),
            (c_before, child.read_bytes(), ["hierarchical_label", "wire", "label"]),
        ):
            edits = J.assert_inserted(before, after, heads=heads, contiguous=False)
            assert {e.path for e in edits} == {ROOT}
        psch = reparse(parent)
        assert len(psch.sheets) == 1
        sheet = psch.sheets[0]
        assert sheet.sheetName.value == "sub"
        assert sheet.fileName.value == "child.kicad_sch"
        assert [pn.name for pn in sheet.pins] == ["A"]
        csch = reparse(child)
        assert [hl.text for hl in csch.hierarchicalLabels] == ["A"]
        assert any(lbl.text == "A" for lbl in csch.labels)

    def test_sheet_pin_modify_move_reorder(self, tmp_path, kicad_native_sch):
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        child2 = tmp_path / "child2.kicad_sch"
        project.create_schematic(str(child2))
        project.add_hierarchical_sheet(str(parent), "s1", str(child), [])
        project.add_hierarchical_sheet(str(parent), "s2", str(child2), [], x=76.2, y=25.4)
        uuids = [s.uuid for s in reparse(parent).sheets if s.uuid is not None]
        assert len(uuids) == 2

        def s1(data: bytes) -> J.Path:
            uuid = uuids[0].encode()
            return _the(data, "kicad_sch", "sheet", where=lambda loc, s: _uuid_of(s) == uuid)

        def child_of_s1(data: bytes, head: str, where=lambda item: True) -> J.Path:
            sheet = s1(data)
            return _the(
                data,
                "kicad_sch",
                "sheet",
                head,
                where=lambda loc, c: loc[:-1] == sheet and where(c),
            )

        before = parent.read_bytes()
        project.add_sheet_pin(uuids[0], "P1", "input", schematic_path=str(parent))
        (pin,) = J.assert_inserted(before, parent.read_bytes(), heads=["pin"])
        assert pin.path == s1(before)
        before = parent.read_bytes()
        project.remove_sheet_pin(uuids[0], "P1", schematic_path=str(parent))
        (pin,) = J.assert_removed(before, parent.read_bytes(), heads=["pin"])
        assert pin.path == s1(before)
        with pytest.raises(ToolError, match="not found on sheet"):
            project.remove_sheet_pin(uuids[0], "P1", schematic_path=str(parent))

        # Renaming a sheet rewrites its Sheetname value and nothing else.
        before = parent.read_bytes()
        project.modify_hierarchical_sheet(
            uuids[0], schematic_path=str(parent), sheet_name="renamed"
        )
        name = child_of_s1(before, "property", lambda item: item.items[1].value == b"Sheetname")
        assert J.atom_changes(before, parent.read_bytes()) == [
            J.AtomChange(name, 2, b'"s1"', b'"renamed"')
        ]
        # A wider sheet with no pins is its width atom alone. KiCad's Resize keeps pins
        # on their edges and re-places autoplaced fields at offsets from the sheet's
        # origin and height, never its width (sch_sheet.cpp:665-706 and 1023-1037 at
        # 9.0.8, 767-811 and 1160-1174 at 10.0.7).
        before = parent.read_bytes()
        project.modify_hierarchical_sheet(uuids[0], schematic_path=str(parent), width=30)
        size = child_of_s1(before, "size")
        assert J.atom_changes(before, parent.read_bytes()) == [
            J.AtomChange(size, 1, b"25.4", b"30")
        ]
        sheet = next(s for s in reparse(parent).sheets if s.uuid == uuids[0])
        assert sheet.sheetName.value == "renamed"
        assert sheet.width == 30

        project.move_hierarchical_sheet(uuids[0], 101.6, 50.8, schematic_path=str(parent))
        sheet = next(s for s in reparse(parent).sheets if s.uuid == uuids[0])
        assert (sheet.position.X, sheet.position.Y) == (101.6, 50.8)

        project.reorder_sheet_pages([uuids[1], uuids[0]], schematic_path=str(parent))
        assert [s.uuid for s in reparse(parent).sheets] == [uuids[1], uuids[0]]
        with pytest.raises(ToolError, match="Sheet UUIDs not found"):
            project.reorder_sheet_pages(["nope"], schematic_path=str(parent))

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="bug 35: modify_hierarchical_sheet leaves an autoplaced sheet's file name inside"
        " the sheet when it grows taller; fixed by step 12a",
    )
    def test_a_taller_sheet_keeps_its_file_name_below_it(self, tmp_path, kicad_native_sch):
        """KiCad resizes a sheet through SCH_SHEET::Resize (its point editor's call,
        sch_point_editor.cpp:900-901 at 9.0.8, 873-874 at 10.0.7), which re-places
        autoplaced fields: on a sheet with no pins the file name goes to the sheet's origin
        plus its height plus a positive margin, below the box (sch_sheet.cpp:665-706 and
        1023-1037 at 9.0.8, 767-811 and 1160-1174 at 10.0.7)."""
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        project.add_hierarchical_sheet(str(parent), "s1", str(child), [])
        data = parent.read_bytes()
        location = _the(data, "kicad_sch", "sheet")
        uuid = _uuid_of(J.item_at(data, location))
        if uuid is None:
            raise RuntimeError("add_hierarchical_sheet wrote a sheet with no uuid")
        project.modify_hierarchical_sheet(uuid.decode(), schematic_path=str(parent), height=30)
        sheet = J.item_at(parent.read_bytes(), location)

        def part(head: str, name: bytes | None = None):
            return next(
                c
                for c in sheet.items
                if c.head == head and (name is None or c.items[1].value == name)
            )

        def iu(atom) -> int:
            return U.kiround(float(atom.value) * U.SCH_IU_PER_MM, major=9)

        autoplaced = [a.value for a in part("fields_autoplaced").items[1:]] in ([], [b"yes"])
        if not autoplaced or any(c.head == "pin" for c in sheet.items):
            raise RuntimeError("the premise is a sheet with autoplaced fields and no pins")
        top, height = iu(part("at").items[2]), iu(part("size").items[2])
        (name_at,) = [c for c in part("property", b"Sheetfile").items if c.head == "at"]
        assert iu(name_at.items[2]) > top + height, "the file name is left inside the sheet"

    def test_annotate_cst(self, kicad_native_sch, scratch_sym_lib):
        p = str(kicad_native_sch)
        schematic.place_component(
            "Test:TestPart",
            "U1",
            "TP",
            40,
            40,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=p,
        )
        schematic.set_component_property("U1", "Reference", "U?", schematic_path=p)
        result = project.annotate_schematic(schematic_path=p)
        assert result == "Annotated 1 components: U2" or result == "Annotated 1 components: U1"
        after = kicad_native_sch.read_bytes()
        assert b"generator_version" in after and b"(embedded_fonts" in after
        sch = reparse(kicad_native_sch)
        refs = [
            next((pr.value for pr in s.properties if pr.key == "Reference"), "")
            for s in sch.schematicSymbols
        ]
        assert not any("?" in r for r in refs)
        assert project.annotate_schematic(schematic_path=p) == "No unannotated components found"
        assert kicad_native_sch.read_bytes() == after

    def test_duplicate_sheet_uuid_scope(
        self, tmp_path, kicad_native_sch, stock_symbol_dir, monkeypatch
    ):
        # Device:R from the stand-in stock folder, so this does not need KiCad.
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(stock_symbol_dir))
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        schematic.place_component(
            "Device:R",
            "R7",
            "1K",
            60,
            60,
            schematic_path=str(child),
        )
        project.add_hierarchical_sheet(
            str(parent), "s1", str(child), [{"name": "A", "direction": "input"}]
        )
        src_uuid = reparse(parent).sheets[0].uuid
        assert src_uuid is not None
        project.duplicate_sheet(src_uuid, "copy", schematic_path=str(parent))
        psch = reparse(parent)
        assert len(psch.sheets) == 2
        new_file = tmp_path / psch.sheets[1].fileName.value
        assert new_file.exists()
        src_root = _cst.parse(child.read_bytes()).lists[0]
        dst_root = _cst.parse(new_file.read_bytes()).lists[0]
        assert _node_uuid(src_root) != _node_uuid(dst_root)
        src_sym = src_root.find("symbol")
        dst_sym = dst_root.find("symbol")
        assert _node_uuid(src_sym) != _node_uuid(dst_sym)
        # kiutils parity: nested pin uuids in the copy are NOT regenerated
        src_pin_uuids = [pn.find("uuid").atoms[1].text for pn in src_sym.find_all("pin")]
        dst_pin_uuids = [pn.find("uuid").atoms[1].text for pn in dst_sym.find_all("pin")]
        # Without a definition R7 had no pins, and on a host without KiCad this
        # compared two empty lists and proved nothing.
        assert src_pin_uuids, "R7 has no pins to compare"
        assert src_pin_uuids == dst_pin_uuids

    def test_flatten_counts(self, tmp_path, kicad_native_sch, stock_symbol_dir, monkeypatch):
        # Device:R from the stand-in stock folder, so this does not need KiCad.
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(stock_symbol_dir))
        parent, child = self._hierarchy(tmp_path, kicad_native_sch)
        schematic.place_component("Device:R", "R7", "1K", 60, 60, schematic_path=str(child))
        project.add_hierarchical_sheet(
            str(parent), "s1", str(child), [{"name": "A", "direction": "input"}]
        )
        result = project.flatten_hierarchy(schematic_path=str(parent))
        assert "2 components" in result
        flat = tmp_path / (parent.stem + "_flat.kicad_sch")
        froot = _cst.parse(flat.read_bytes()).lists[0]
        assert froot.find("sheet") is None
        assert froot.find("hierarchical_label") is None
        assert len(froot.find_all("symbol")) == 2


class TestMemoryGate:
    """ADR-1 board gate: the hardened repr against real KiCad demo boards.

    Baseline before slice 12A: 37.4x on the 6MB Video board. Hardened repr
    measured 2026-08-09: 11.35x retained on Video, 8.85x on the 71MB
    vme-wren. ~11x is the floor of a one-object-per-token design in
    CPython (~32B/object + 33B/bytes overhead), so the gate was revised
    to 12x with the user's sign-off (ADR): 68MB for the benchmark board
    is acceptable for a server process. The flat-token-array design
    stays on record as the next step if real memory pain appears.
    """

    @staticmethod
    def _demo_boards():
        from mcp_server_kicad._shared import _kicad_root

        roots = []
        root = _kicad_root()
        if root is not None:
            roots += [root / "share" / "kicad" / "demos", root / "SharedSupport" / "demos"]
        boards = []
        for r in roots:
            if r.is_dir():
                boards += [
                    b for b in r.rglob("*.kicad_pcb") if 2_000_000 <= b.stat().st_size <= 20_000_000
                ]
        return sorted(boards, key=lambda b: b.stat().st_size)

    def test_largest_demo_board_under_gate(self):
        import gc
        import tracemalloc

        boards = self._demo_boards()
        if not boards:
            pytest.skip("no 2-20MB KiCad demo board on this host")
        board = boards[-1]
        data = board.read_bytes()
        tracemalloc.start()
        tree = _cst.parse(data)
        gc.collect()
        retained, _peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert _cst.serialize(tree) == data
        multiple = retained / len(data)
        assert multiple <= 12, f"{board.name}: {multiple:.2f}x retained (gate <=12x)"

    def test_demo_board_roundtrips(self):
        boards = self._demo_boards()
        if not boards:
            pytest.skip("no >=2MB KiCad demo board on this host")
        data = boards[0].read_bytes()
        assert _cst.serialize(_cst.parse(data)) == data


class TestReadToolsRelaxed:
    """Slice 12B: the 13 schematic read tools are CST-native and guard-free."""

    @pytest.mark.no_kicad_validation
    def test_future_version_file_reads_work(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        summary = schematic.get_schematic_summary(schematic_path=p)
        assert summary.components == 1 and summary.wires == 1 and summary.labels == 1
        comps = schematic.list_schematic_components(schematic_path=p)
        assert [c.reference for c in comps] == ["R1"]
        assert comps[0].lib_id == "Device:R"
        labels = schematic.list_schematic_labels(schematic_path=p)
        assert [(lbl.text, lbl.x, lbl.y) for lbl in labels] == [("TEST_NET", 60.0, 100.0)]
        wires = schematic.list_schematic_wires(schematic_path=p)
        assert (wires[0].x1, wires[0].y1, wires[0].x2, wires[0].y2) == (50.0, 100.0, 80.0, 100.0)
        pins = schematic.get_pin_positions("R1", schematic_path=p)
        # Every pin at its exact end, the port's reading of the file's own tokens.
        (ends,) = port_pin_ends(kicad_native_sch.read_bytes(), "R1")
        assert pin_map(ends) == {"1": (1_000_000, 961_900), "2": (1_000_000, 1_038_100)}
        assert report_map(pins) == pin_map(ends)

    def test_net_connections_cst(self, kicad_native_sch):
        p = str(kicad_native_sch)
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="NETX", schematic_path=p
        )
        result = schematic.get_net_connections("NETX", schematic_path=p)
        assert result.label_count == 1
        assert any(c["reference"] == "R1" and c["pin"] == "1" for c in result.connections)


class TestGuardRelax:
    @pytest.mark.no_kicad_validation
    def test_future_version_file_add_label_works_other_tools_refuse(self, kicad_native_sch):
        # Simulate a KiCad-10-saved schematic: bump only the version claim.
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=str(kicad_native_sch)).components >= 0
        before = kicad_native_sch.read_bytes()
        schematic.add_label("RELAXED", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        (label,) = J.assert_inserted(before, after, heads=["label"])
        assert label.path == ROOT
        tree = _cst.parse(after)
        assert "RELAXED" in [n.atoms[1].text for n in tree.lists[0].find_all("label")]

    @pytest.mark.no_kicad_validation
    def test_future_version_file_whole_add_family_works(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        _tee(kicad_native_sch, (30.48, 96), (-5.08, 0), (5.08, 0), (0, -5.08))
        before = kicad_native_sch.read_bytes()
        schematic.add_global_label("G", 60, 90, schematic_path=p)
        schematic.add_hierarchical_label("H", "input", 60, 92, schematic_path=p)
        schematic.add_text("T", 60, 94, schematic_path=p)
        schematic.add_junctions([{"x": 30.48, "y": 96}], schematic_path=p)
        after = kicad_native_sch.read_bytes()
        assert len(after) > len(before)
        tree = _cst.parse(after)
        root = tree.lists[0]
        for token in ("global_label", "hierarchical_label", "text", "junction"):
            assert root.find(token) is not None, token

    @pytest.mark.no_kicad_validation
    def test_future_version_file_remove_and_modify_work(self, kicad_native_sch):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        _tee(kicad_native_sch, (30.48, 96), (-5.08, 0), (5.08, 0), (0, -5.08))
        schematic.add_label("R1X", 60, 90, schematic_path=p)
        schematic.add_hierarchical_label("H1X", "input", 60, 92, schematic_path=p)
        schematic.add_junctions([{"x": 30.48, "y": 96}], schematic_path=p)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        assert "1 label(s)" in schematic.remove_label("R1X", schematic_path=p)
        assert "output" in schematic.modify_hierarchical_label(
            "H1X", schematic_path=p, new_shape="output"
        )
        assert "Removed junction" in schematic.remove_junction(30.48, 96, schematic_path=p)
        assert "Added 1 wires" in schematic.add_wires(
            [{"x1": 20, "y1": 20, "x2": 30, "y2": 20}], schematic_path=p
        )
        assert "Removed 1 wire(s)." in schematic.remove_wire(20, 20, 30, 20, schematic_path=p)

    @pytest.mark.no_kicad_validation
    def test_future_version_file_symbol_tools_work(self, kicad_native_sch, scratch_power_lib):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        assert "Moved R1" in schematic.move_component("R1", 63.5, 63.5, schematic_path=p)
        assert "22K" in schematic.set_component_property("R1", "Value", "22K", schematic_path=p)
        assert "A3" in schematic.set_page_size("A3", schematic_path=p)
        assert "VCC" in schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert schematic.remove_component("R1", schematic_path=p) == "Removed R1"
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert root.find("symbol") is None  # placed R1 gone; lib entries stay nested
        assert "VCC" in [s.atoms[1].text for s in root.find("lib_symbols").find_all("symbol")]

    @pytest.mark.no_kicad_validation
    def test_future_version_file_project_tools_work(self, tmp_path, kicad_native_sch):
        child = tmp_path / "child.kicad_sch"
        project.create_schematic(str(child))
        for f in (kicad_native_sch, child):
            f.write_bytes(f.read_bytes().replace(b"(version 20250114)", b"(version 20260306)"))
        p = str(kicad_native_sch)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        project.add_hierarchical_sheet(p, "sub", str(child), [{"name": "A", "direction": "input"}])
        s_uuid = _cst.parse(kicad_native_sch.read_bytes()).lists[0].find("sheet")
        uuid = _node_uuid(s_uuid)
        project.add_sheet_pin(uuid, "B", "output", schematic_path=p)
        assert "renamed" in project.modify_hierarchical_sheet(
            uuid, schematic_path=p, sheet_name="renamed"
        )
        assert "Removed hierarchical sheet" in project.remove_hierarchical_sheet(p, uuid=uuid)

    @pytest.mark.no_kicad_validation
    def test_future_version_file_place_component_works(self, kicad_native_sch, scratch_sym_lib):
        bumped = kicad_native_sch.read_bytes().replace(b"(version 20250114)", b"(version 20260306)")
        kicad_native_sch.write_bytes(bumped)
        p = str(kicad_native_sch)
        # Slice 12B: reads are CST-native too, so the summary now succeeds.
        assert schematic.get_schematic_summary(schematic_path=p).components >= 0
        result = schematic.place_component(
            lib_id="Test:TestPart",
            reference="U1",
            value="TP",
            x=63.5,
            y=63.5,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=p,
        )
        assert "Placed U1" in result
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert any(
            n.find("lib_id") is not None and n.find("lib_id").atoms[1].text == "Test:TestPart"
            for n in root.find_all("symbol")
        )


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
        (label,) = J.assert_inserted(before, after, heads=["label"])
        assert label.path == ROOT
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
        (label,) = J.assert_inserted(before, after, heads=["global_label"])
        assert label.path == ROOT
        assert _cst.parse(after).lists[0].find("global_label") is not None

    def test_hierarchical_label_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_hierarchical_label(
            "K10_H", "input", 60, 90, schematic_path=str(kicad_native_sch)
        )
        after = kicad_native_sch.read_bytes()
        (label,) = J.assert_inserted(before, after, heads=["hierarchical_label"])
        assert label.path == ROOT
        assert _cst.parse(after).lists[0].find("hierarchical_label") is not None

    def test_text_on_real_kicad10(self, kicad_native_sch):
        before = self._mint(kicad_native_sch)
        schematic.add_text("K10 note", 60, 90, schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        (text,) = J.assert_inserted(before, after, heads=["text"])
        assert text.path == ROOT
        assert _cst.parse(after).lists[0].find("text") is not None

    def test_junctions_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        # Three wire ends meet at (60, 90): a point KiCad's editor takes a junction at.
        _tee(kicad_native_sch, (60, 90), (-5.08, 0), (5.08, 0), (0, -5.08))
        before = kicad_native_sch.read_bytes()
        schematic.add_junctions([{"x": 60, "y": 90}], schematic_path=str(kicad_native_sch))
        after = kicad_native_sch.read_bytes()
        (junction,) = J.assert_inserted(before, after, heads=["junction"])
        assert junction.path == ROOT
        assert _cst.parse(after).lists[0].find("junction") is not None

    def test_remove_label_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        schematic.add_label("K10_RM", 60, 90, schematic_path=p)
        mid = kicad_native_sch.read_bytes()
        assert "1 label(s)" in schematic.remove_label("K10_RM", schematic_path=p)
        after = kicad_native_sch.read_bytes()
        (label,) = J.assert_removed(mid, after, heads=["label"])
        assert label.path == ROOT
        assert "K10_RM" not in [
            n.atoms[1].text for n in _cst.parse(after).lists[0].find_all("label")
        ]

    def test_modify_hierarchical_label_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        schematic.add_hierarchical_label("K10_M", "input", 60, 90, schematic_path=p)
        schematic.modify_hierarchical_label(
            "K10_M", schematic_path=p, new_text="K10_M2", new_shape="output"
        )
        node = _cst.parse(kicad_native_sch.read_bytes()).lists[0].find("hierarchical_label")
        assert node.atoms[1].text == "K10_M2"
        assert node.find("shape").atoms[1].text == "output"

    def test_wires_on_real_kicad10(self, kicad_native_sch):
        """A T onto the fixture wire (50,100)-(80,100) of a file kicad-cli 10 minted: a
        junction at (65,100) and none inside a wire. After the stem is removed, none lies
        inside a wire still and the bar's pieces run from (50,100) to (80,100) end to end,
        one connected run. remove_wire leaves the split bar and its junction; KiCad's delete
        would also delete the junction, no longer explicit, and merge the halves
        (sch_edit_tool.cpp:1703-1711 at 9.0.8)."""
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        schematic.add_wires([{"x1": 65, "y1": 80, "x2": 65, "y2": 100}], schematic_path=p)
        data = kicad_native_sch.read_bytes()
        assert S.iu_point((65, 100)) in S.junctions(data, major=10)
        assert S.junctions_on_interiors(data, major=10) == []
        assert "Removed 1 wire(s)." in schematic.remove_wire(65, 80, 65, 100, schematic_path=p)
        data = kicad_native_sch.read_bytes()
        assert S.junctions_on_interiors(data, major=10) == []
        pieces = sorted(
            tuple(sorted((line.start, line.end)))
            for line in S.lines(data, major=10)
            if line.start[1] == line.end[1] == S.iu(100)
        )
        ends = [S.iu(50)] + [x for (a, b) in pieces for x in (a[0], b[0])] + [S.iu(80)]
        assert all(ends[2 * k] == ends[2 * k + 1] for k in range(len(pieces) + 1)), pieces

    def test_routing_tools_on_real_kicad10(self, kicad_native_sch):
        # sch upgrade rewrites lib_symbols into the KiCad 10 shape, so this is
        # the live portability measurement for the CST pin walk and no_connect.
        before = self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        assert "at (100.0, 96.19)" in schematic.no_connect_pin("R1", "1", schematic_path=p)
        mid = kicad_native_sch.read_bytes()
        (flag,) = J.assert_inserted(before, mid, heads=["no_connect"])
        assert flag.path == ROOT
        assert _cst.parse(mid).lists[0].find("no_connect") is not None
        assert "Removed 1 no-connect" in schematic.remove_no_connect("R1", "1", schematic_path=p)
        (flag,) = J.assert_removed(mid, kicad_native_sch.read_bytes(), heads=["no_connect"])
        assert flag.path == ROOT
        schematic.connect_pins("R1", "1", "R1", "2", schematic_path=p)
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert len(root.find_all("wire")) == 2
        assert "Net-(R1-1)" in [n.atoms[1].text for n in root.find_all("label")]

    def test_wire_pins_to_net_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        result = schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="K10NET", schematic_path=p
        )
        assert result.splitlines()[0] == "Wired 1 pins to 'K10NET'."
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert len(root.find_all("wire")) == 2
        assert "K10NET" in [n.atoms[1].text for n in root.find_all("label")]

    def test_symbol_tools_on_real_kicad10(self, kicad_native_sch, scratch_power_lib):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        assert "Moved R1" in schematic.move_component("R1", 63.5, 63.5, schematic_path=p)
        assert "22K" in schematic.set_component_property("R1", "Value", "22K", schematic_path=p)
        assert "A3" in schematic.set_page_size("A3", schematic_path=p)
        assert "VCC" in schematic.add_lib_symbol(str(scratch_power_lib), "VCC", schematic_path=p)
        assert schematic.remove_component("R1", schematic_path=p) == "Removed R1"
        root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert root.find("symbol") is None
        assert root.find("paper").atoms[1].text == "A3"

    def test_move_component_carries_fields_on_real_kicad10(self, kicad_native_sch):
        """On a schematic kicad-cli 10 minted: a translation of R1 to (63.5, 63.5), then a
        turn to 90 in place there, each change exactly the atoms KiCad's rule moves
        (expected_move_changes, read from the minted file's own tokens), and the autouse
        oracle loads the result with kicad-cli 10."""
        self._mint(kicad_native_sch)
        for rotation in (None, 90):
            before = kicad_native_sch.read_bytes()
            schematic.move_component(
                "R1", 63.5, 63.5, rotation=rotation, schematic_path=str(kicad_native_sch)
            )
            after = kicad_native_sch.read_bytes()
            check_move_changes(before, after, "R1", (635_000, 635_000), rotation)

    def test_place_component_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        # System Device lib on a KiCad 10 runner is K10-format: the copied
        # entry and the minted schematic stay format-consistent.
        result = schematic.place_component(
            lib_id="Device:C", reference="C1", value="100nF", x=63.5, y=63.5, schematic_path=p
        )
        assert "Placed C1" in result
        sch_root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        assert any(
            n.find("lib_id") is not None and n.find("lib_id").atoms[1].text == "Device:C"
            for n in sch_root.find_all("symbol")
        )

    def test_add_power_symbol_on_real_kicad10(self, kicad_native_sch):
        # A power symbol on a real KiCad 10 file, copied from the runner's K10
        # power library. add_power_symbol places no PWR_FLAG alongside it.
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        result = schematic.add_power_symbol("power:VCC", "#PWR01", 60, 90, schematic_path=p)
        assert "#PWR01" in result and "#FLG" not in result
        sch_root = _cst.parse(kicad_native_sch.read_bytes()).lists[0]
        lib_names = [s.atoms[1].text for s in sch_root.find("lib_symbols").find_all("symbol")]
        assert "power:VCC" in lib_names  # system copy, prefixed
        assert not any(name.endswith("PWR_FLAG") for name in lib_names)

    def test_hierarchy_on_real_kicad10(self, tmp_path, kicad_native_sch):
        self._mint(kicad_native_sch)
        child = tmp_path / "k10child.kicad_sch"
        project.create_schematic(str(child))
        _run_cli(["sch", "upgrade", "--force", str(child)])
        p = str(kicad_native_sch)
        project.add_hierarchical_sheet(p, "sub", str(child), [{"name": "A", "direction": "input"}])
        sheet = _cst.parse(kicad_native_sch.read_bytes()).lists[0].find("sheet")
        uuid = _node_uuid(sheet)
        project.add_sheet_pin(uuid, "B", "output", schematic_path=p)
        assert "renamed" in project.modify_hierarchical_sheet(
            uuid, schematic_path=p, sheet_name="renamed"
        )
        schematic.place_component("Device:C", "C1", "100nF", 63.5, 63.5, schematic_path=p)
        schematic.set_component_property("C1", "Reference", "C?", schematic_path=p)
        assert "Annotated 1 components" in project.annotate_schematic(schematic_path=p)

    def test_project_reads_on_real_kicad10(self, tmp_path, kicad_native_sch):
        # Slice 16: the six project.py reads walk the CST, so they see a real
        # KiCad 10 hierarchy instead of refusing it. Pin B is deliberately
        # left without a child label so the issue engine has something to find.
        self._mint(kicad_native_sch)
        child = tmp_path / "k10child.kicad_sch"
        project.create_schematic(str(child))
        _run_cli(["sch", "upgrade", "--force", str(child)])
        p = str(kicad_native_sch)
        project.add_hierarchical_sheet(p, "sub", str(child), [{"name": "A", "direction": "input"}])
        uuid = _node_uuid(_cst.parse(kicad_native_sch.read_bytes()).lists[0].find("sheet"))
        project.add_sheet_pin(uuid, "B", "output", schematic_path=p)

        hierarchy = project.list_hierarchy(schematic_path=p)
        assert hierarchy.sheet_count == 1
        assert hierarchy.sheets[0]["file_name"] == "k10child.kicad_sch"
        assert hierarchy.sheets[0]["pin_count"] == 2
        assert hierarchy.sheets[0]["hierarchical_label_count"] == 1

        info = project.get_sheet_info(uuid, schematic_path=p)
        assert {pin["name"]: pin["matched"] for pin in info.pins} == {"A": True, "B": False}

        trace = project.trace_hierarchical_net("A", schematic_path=p)
        assert "k10child.kicad_sch" in trace.sheets_touched
        assert {"sheet_pin", "hierarchical_label"} <= {c["type"] for c in trace.connections}

        nets = project.list_cross_sheet_nets(schematic_path=p)
        assert {n["name"]: n["label_matched"] for n in nets.hierarchical_nets} == {
            "A": True,
            "B": False,
        }
        # No .kicad_pro beside the fixture, so nothing writes a symbol_instances
        # section: the empty-section path is what a KiCad 8+ file gives anyway.
        assert project.get_symbol_instances(schematic_path=p).instances == []

        schematic.set_component_property("R1", "Reference", "R?", schematic_path=p)
        issues = project.validate_hierarchy(schematic_path=p)
        assert issues.status == "issues_found"
        assert {"type": "orphaned_pin", "sheet_name": "sub", "pin": "B"} in issues.issues
        assert any(i["type"] == "unannotated_ref" and i["reference"] == "R?" for i in issues.issues)
        assert "Annotated 1 components" in project.annotate_schematic(schematic_path=p)
        after = project.validate_hierarchy(schematic_path=p)
        assert not any(i["type"] == "unannotated_ref" for i in after.issues)

    def test_duplicate_and_flatten_on_real_kicad10(self, tmp_path, kicad_native_sch):
        self._mint(kicad_native_sch)
        child = tmp_path / "k10child.kicad_sch"
        project.create_schematic(str(child))
        _run_cli(["sch", "upgrade", "--force", str(child)])
        p = str(kicad_native_sch)
        project.add_hierarchical_sheet(p, "s1", str(child), [{"name": "A", "direction": "input"}])
        src_uuid = _cst.parse(kicad_native_sch.read_bytes()).lists[0].find("sheet")
        project.duplicate_sheet(_node_uuid(src_uuid), "copy", schematic_path=p)
        assert len(_cst.parse(kicad_native_sch.read_bytes()).lists[0].find_all("sheet")) == 2
        result = project.flatten_hierarchy(schematic_path=p)
        assert "components" in result

    def test_read_tools_on_real_kicad10(self, kicad_native_sch):
        self._mint(kicad_native_sch)
        p = str(kicad_native_sch)
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}], label_text="K10NET", schematic_path=p
        )
        summary = schematic.get_schematic_summary(schematic_path=p)
        assert summary.components == 1 and summary.wires == 2
        assert [c.reference for c in schematic.list_schematic_components(schematic_path=p)] == [
            "R1"
        ]
        assert "Pin 1" in schematic.get_pin_positions("R1", schematic_path=p)
        result = schematic.get_net_connections("K10NET", schematic_path=p)
        assert any(c["reference"] == "R1" for c in result.connections)

    def test_symbol_lib_on_real_kicad10(self, kicad_native_sch, scratch_sym_lib):
        # Slice 17: the symbol library tools are CST-native, so a real KiCad 10
        # .kicad_sym is listed, inspected, written to, and placed from. Minting
        # goes through KiCad itself so the file is the runner's own dialect.
        self._mint(kicad_native_sch)  # skips the whole test below kicad-cli 10
        lib = str(scratch_sym_lib)
        _run_cli(["sym", "upgrade", "--force", lib])
        before = scratch_sym_lib.read_bytes()
        version = int(_cst.parse(before).lists[0].find("version").atoms[1].text)
        assert version > 20241209, version

        assert symbol.list_lib_symbols(lib) == "TestPart (2 pins)"
        info = symbol.get_symbol_info("TestPart", lib)
        assert "Pin 1: IN (passive) @ (-5.08, 0) rot=0" in info

        symbol.add_symbol(
            name="K10Part",
            pins=[{"number": "1", "name": "A", "type": "input", "x": -5.08, "y": 0}],
            pin_names_offset=1.016,
            symbol_lib_path=lib,
        )
        after = scratch_sym_lib.read_bytes()
        (added,) = J.assert_inserted(before, after, heads=["symbol"])
        assert added.path == ((0, "kicad_symbol_lib"),)
        # The KiCad 10 stamp survives: no downgrade, and KiCad still loads it.
        assert int(_cst.parse(after).lists[0].find("version").atoms[1].text) == version
        _run_cli(["sym", "upgrade", "--force", lib])

        p = str(kicad_native_sch)
        assert "Placed U1" in schematic.place_component(
            lib_id="K10Part",
            reference="U1",
            value="x",
            x=63.5,
            y=63.5,
            symbol_lib_path=lib,
            schematic_path=p,
        )
        lib_names = [
            s.atoms[1].text
            for s in _cst.parse(kicad_native_sch.read_bytes())
            .lists[0]
            .find("lib_symbols")
            .find_all("symbol")
        ]
        assert "K10Part" in lib_names

    def test_footprint_lib_on_real_kicad10(self, tmp_path):
        # get_footprint_info against a .kicad_mod KiCad 10 wrote itself.
        if _kicad_cli_major() < 10:
            pytest.skip("needs kicad-cli 10+ to mint a current-format footprint")
        pretty = tmp_path / "K10.pretty"
        pretty.mkdir()
        fp = build_test_footprint()
        fp.filePath = str(pretty / "R_0603.kicad_mod")
        fp.to_file()
        _run_cli(["fp", "upgrade", str(pretty)])
        mod = pretty / "R_0603.kicad_mod"
        version = int(_cst.parse(mod.read_bytes()).lists[0].find("version").atoms[1].text)
        assert version > 20241229, version

        result = footprint.get_footprint_info(str(mod))
        assert result.startswith("Footprint: R_0603")
        assert "Pad 1:" in result and "Pad 2:" in result
        assert footprint.list_lib_footprints(str(pretty)) == "R_0603"
