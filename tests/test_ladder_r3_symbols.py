"""Rung 3: a placed symbol moved and turned by move_component, judged by KiCad's reading.

Code under test: schematic.move_component, on one placed symbol at a time.

KiCad's rules:

- a field's offset in the symbol's own frame, textPos - S, is what KiCad keeps: Move
  translates the symbol and its fields by one vector, Rotate and both mirrors move each field
  by the symbol's displacement only, and none of them touches a text angle
  (eeschema/sch_symbol.h:701-710 at 9.0.8, 788-797 at 10.0.7; eeschema/sch_symbol.cpp:
  2190-2242 at 9.0.8, 2827-2879 at 10.0.7). A property's (at) is the drawn position
  S + T * offset, read back through T^-1 (kicad_ref.sch_field), so a symbol carried from
  (S, T) to (S', T') has each property at P' = S' + T' * T^-1 * (P - S);
- KiCad turns a single symbol about its own position (eeschema/tools/sch_edit_tool.cpp:814
  and 827 at 9.0.8, 1009 and 1022 at 10.0.7), so turning a symbol in place keeps its anchor
  wherever it is, on the grid or off it;
- move_component's rotation is the symbol's absolute angle, and a mirrored symbol keeps its
  mirror, so after a move KiCad reads the transform the (at) angle and the kept (mirror)
  make (kicad_ref.transform.parse_symbol_transform).

Judges, none of them the code under test: kicad_ref.sch_field and kicad_ref.transform on
the file as the lexer reads it (sch_judge.placed_symbol), for both KiCad majors; and
kicad-cli's SVG, whose plotted field anchors (svg_judge) must move rigidly with the symbol.
The sheets are edit_gen's, in KiCad 9's form, from KiCad's stock Device:R.

Bug 1: move_component rewrites the symbol's (at) and leaves every property where it was.
Bug 12: the 1.27 mm grid snap runs even when the call names the symbol's own position, so
turning an off-grid symbol in place also moves it.

The last section judges what a move does with a symbol KiCad cannot load, by the same
parser (an angle it refuses, or none: bugs 97 and 98 reached by other spellings), and with
a symbol KiCad loads off the page.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import NamedTuple

import edit_gen as E
import edit_judge as J
import geom_gen as G
import pytest
import sch_judge
import sexpr_gen
import svg_judge as SVG
from conftest import HAS_KICAD_CLI, assert_kicad_parseable, requires_cli, run_erc
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import parse_units as P
from kicad_ref import sch_field as SF
from kicad_ref import transform as T
from mcp.server.mcpserver.exceptions import ToolError
from test_ladder_r2_pins import (
    lexed_root,
    placed_symbols,
    read_iu,
    reference_property,
    symbol_placement,
)

from mcp_server_kicad import _connectivity, schematic

#: The 1.27 mm grid move_component snaps a translation to, in internal units.
GRID = 12_700
#: Grid steps that keep an anchor inside edit_gen's A3 sheet (420 x 297 mm), clear of its edge.
_X_STEPS, _Y_STEPS = (8, 320), (8, 225)
_ROTATIONS = (None, 0, 90, 180, 270)
_FIELDS = ("Reference", "Value", "Footprint", "Datasheet")


def _on_grid(steps: tuple[int, int]) -> st.SearchStrategy[int]:
    return st.integers(*steps).map(lambda k: k * GRID)


_ANCHORS = st.tuples(_on_grid(_X_STEPS), _on_grid(_Y_STEPS))
_OFFSET = st.tuples(st.integers(-254_000, 254_000), st.integers(-254_000, 254_000))
_FIELD_SPECS = st.tuples(*[st.tuples(_OFFSET, st.sampled_from((0, 90))) for _ in _FIELDS])


def symbol(
    at, state, specs, *, reference: str = "R1", autoplaced: bool = False, justify=("", "")
) -> E.SynSymbol:
    """A placed Device:R in KiCad's child order at ``at`` in ``state``, its four fields at
    the offsets and text angles of ``specs`` as KiCad reads them; Reference and Value shown
    (justified as ``justify`` says), Footprint and Datasheet hidden."""
    rotation, mirror = state
    values = {
        "Reference": reference,
        "Value": "V" + reference[1:],
        "Footprint": "",
        "Datasheet": "~",
    }
    fields = tuple(
        E.SynField(
            name,
            values[name],
            (0, 0),
            angle,
            name in ("Reference", "Value"),
            justify[k] if k < 2 else "",
        )
        for k, (name, (_, angle)) in enumerate(zip(_FIELDS, specs))
    )
    sym = E.SynSymbol(reference, at, rotation, mirror, fields, autoplaced=autoplaced)
    return E.at_offsets(sym, {name: offset for name, (offset, _) in zip(_FIELDS, specs)})


def turned(transform_state: tuple[int, str], rotation: int | None) -> T.Matrix:
    """The transform KiCad reads after a move to ``rotation`` (None keeps the angle) of a
    symbol in ``transform_state``: the new (at) angle, then the mirror it keeps."""
    angle, mirror = transform_state
    state = [("at", str(angle if rotation is None else rotation))]
    return T.parse_symbol_transform(state + ([("mirror", mirror)] if mirror else []))


def mm(iu: int) -> float:
    """A coordinate as a caller passes it: the float of the text KiCad writes for it."""
    return float(G.sch_mm(iu))


def _autoplaced_bytes(data: bytes, location) -> list[bytes]:
    return [c.raw for c in J.item_at(data, location).items if c.head == "fields_autoplaced"]


def _move(path: Path, at, rotation, reference: str = "R1") -> str:
    return schematic.move_component(
        reference, mm(at[0]), mm(at[1]), rotation=rotation, schematic_path=str(path)
    )


# ---------------------------------------------------------------------------
# the field rule, read back through KiCad's own reading of the file
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget(4))
@given(
    state=G.symbol_states(),
    at=_ANCHORS,
    target=_ANCHORS,
    rotation=st.sampled_from(_ROTATIONS),
    specs=_FIELD_SPECS,
    autoplaced=st.booleans(),
)
@example(
    state=(90, "x"),
    at=(508_000, 508_000),
    target=(762_000, 1_016_000),
    rotation=180,
    specs=(((50_800, -25_400), 0), ((-38_100, 63_500), 90), ((0, 0), 0), ((12_700, 0), 0)),
    autoplaced=True,
)
def test_move_keeps_each_fields_offset_in_the_symbol_frame(
    state, at, target, rotation, specs, autoplaced
):
    """A symbol in any of the 12 states, on the grid, moved to a grid point and turned to
    any angle or none: KiCad reads it at the target with the new angle and its kept mirror,
    and reads every field at the offset in the symbol's frame it had before, so P' = S' +
    T' * T^-1 * (P - S) in exact internal units; every text angle and the
    (fields_autoplaced) flag are as they were."""
    with tempfile.TemporaryDirectory() as work:
        path = E.write_symbol_sheet(
            Path(work) / "move.kicad_sch", [symbol(at, state, specs, autoplaced=autoplaced)]
        )
        before = path.read_bytes()
        _move(path, target, rotation)
        after = path.read_bytes()
    location, old = sch_judge.placed_symbol(before, "R1")
    _, new = sch_judge.placed_symbol(after, "R1")
    assert (new.position, new.transform) == (target, turned(state, rotation)), (
        "KiCad does not read the symbol where the call put it"
    )
    moved = {name: (f.offset, f.angle) for name, f in new.fields.items()}
    kept = {name: (f.offset, f.angle) for name, f in old.fields.items()}
    assert moved == kept, "the symbol moved and its fields did not keep their place on it"
    assert _autoplaced_bytes(after, location) == _autoplaced_bytes(before, location)


@settings(max_examples=sexpr_gen.budget(4))
@given(
    state=G.symbol_states(),
    at=_ANCHORS,
    keep_angle=st.booleans(),
    specs=_FIELD_SPECS,
    autoplaced=st.booleans(),
)
def test_move_to_the_same_place_changes_no_field(state, at, keep_angle, specs, autoplaced):
    """Moving a symbol on the grid to its own position, with its own angle or none, leaves
    the file byte for byte as it was."""
    with tempfile.TemporaryDirectory() as work:
        path = E.write_symbol_sheet(
            Path(work) / "same.kicad_sch", [symbol(at, state, specs, autoplaced=autoplaced)]
        )
        before = path.read_bytes()
        _move(path, at, state[0] if keep_angle else None)
        assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# turning in place keeps the anchor (KiCad turns a symbol about its own position)
# ---------------------------------------------------------------------------


@settings(max_examples=sexpr_gen.budget(4))
@given(
    state=G.symbol_states(),
    cell=_ANCHORS,
    off=st.tuples(st.integers(1, GRID - 1), st.integers(0, GRID - 1)),
    rotation=st.sampled_from((0, 90, 180, 270)),
    specs=_FIELD_SPECS,
)
@example(
    state=(0, ""),
    cell=(990_600, 990_600),
    off=(9_400, 9_400),
    rotation=90,
    specs=(((20_320, -12_700), 0), ((20_320, 12_700), 0), ((-17_780, 0), 90), ((0, 0), 0)),
)
def test_rotate_in_place_keeps_an_off_grid_anchor(state, cell, off, rotation, specs):
    """A symbol off the 1.27 mm grid (the explicit example is at (100, 100), as
    tests/fixtures/kicad_native.kicad_sch places R1) turned at its own position, given as
    the floats of the text KiCad writes for it: KiCad reads the anchor where it was and the
    new angle with the kept mirror."""
    at = (cell[0] + off[0], cell[1] + off[1])
    with tempfile.TemporaryDirectory() as work:
        path = E.write_symbol_sheet(Path(work) / "turn.kicad_sch", [symbol(at, state, specs)])
        _move(path, at, rotation)
        _, new = sch_judge.placed_symbol(path.read_bytes(), "R1")
    assert new.position == at, f"turned in place, the symbol moved from {at} to {new.position}"
    assert new.transform == turned(state, rotation)


@settings(max_examples=sexpr_gen.budget(4))
@given(
    state=G.symbol_states(),
    at=_ANCHORS,
    rotation=st.sampled_from((0, 90, 180, 270)),
    specs=_FIELD_SPECS,
)
def test_rotate_in_place_keeps_an_on_grid_anchor(state, at, rotation, specs):
    """The on-grid sibling: KiCad reads the anchor where it was and the new angle with the
    kept mirror."""
    with tempfile.TemporaryDirectory() as work:
        path = E.write_symbol_sheet(Path(work) / "turn.kicad_sch", [symbol(at, state, specs)])
        _move(path, at, rotation)
        _, new = sch_judge.placed_symbol(path.read_bytes(), "R1")
    assert (new.position, new.transform) == (at, turned(state, rotation))


# ---------------------------------------------------------------------------
# kicad-cli: the fields move where KiCad draws them
# ---------------------------------------------------------------------------


class Move(NamedTuple):
    """A move asked of one placed symbol: where to and at which angle (None keeps it), the
    transform KiCad should then read, and the texts of the symbol's shown fields."""

    target: tuple[int, int]
    rotation: int | None
    transform: T.Matrix
    shown: tuple[str, ...]


def move_all(before: Path, after: Path, moves: dict[str, Move]) -> None:
    """Copy ``before`` to ``after`` and make every move of ``moves`` on it, one call each."""
    after.write_bytes(before.read_bytes())
    for reference, move in moves.items():
        _move(after, move.target, move.rotation, reference)


def rigid_problems(before: Path, after: Path, moves: dict[str, Move]) -> list[str]:
    """Each shown field whose anchor in kicad-cli's SVG of ``after`` is not the rigid image
    of its anchor in the SVG of ``before``: S' + T' * T^-1 * (anchor - S), with (S, T) KiCad's
    reading of the symbol before and (S', T') the move asked of it, within
    svg_judge.TOLERANCE; or whose orientation does not turn with the symbol
    (GetDrawRotation: vertical and horizontal swap with the transform's y1)."""
    texts = [text for move in moves.values() for text in move.shown]
    old = SVG.anchors(before, texts, text_height=E.FIELD_TEXT_HEIGHT)
    new = SVG.anchors(after, texts, text_height=E.FIELD_TEXT_HEIGHT)
    data = before.read_bytes()
    problems = []
    for reference, move in moves.items():
        _, placed = sch_judge.placed_symbol(data, reference)
        frame = (placed.position, placed.transform)
        flips = bool(placed.transform[1]) != bool(move.transform[1])
        for text in move.shown:
            if len(old[text]) != 1 or len(new[text]) != 1:
                raise RuntimeError(f"{text} is not plotted once: {old[text]} / {new[text]}")
            (a,), (b,) = old[text], new[text]
            # The anchor's offset in the symbol's frame, carried to the frame asked for.
            held = SF.set_position(frame, (a.x, a.y))
            offset = (held[0] - placed.position[0], held[1] - placed.position[1])
            x, y = SF.carry_offset((move.target, move.transform), offset)
            off = max(abs(b.x - x), abs(b.y - y))
            if off > SVG.TOLERANCE or b.vertical != (a.vertical != flips):
                problems.append(f"{text}: plotted at {b}, the rigid move puts it at ({x}, {y})")
    return problems


@requires_cli
def test_fields_follow_the_symbol_where_kicad_draws_them(tmp_path):
    """48 symbols, each of the 12 states turned to each of the four angles and moved by
    (2.54, 5.08), their Reference and Value shown at asymmetric offsets, at text angles 0
    and 90, one justified left and one centred: in kicad-cli's SVG every shown field's
    plotted anchor moves rigidly with its symbol and turns with it."""
    symbols, moves = [], {}
    specs = (((50_800, -25_400), 0), ((-38_100, 63_500), 90), ((0, 0), 0), ((0, 0), 0))
    for state in G.STATES:
        for angle in (0, 90, 180, 270):
            k = len(symbols)
            at = (254_000 + 304_800 * (k % 12), 381_000 + 508_000 * (k // 12))
            reference = f"R{k + 1}"
            symbols.append(symbol(at, state, specs, reference=reference, justify=("left", "")))
            moves[reference] = Move(
                (at[0] + 25_400, at[1] + 50_800),
                angle,
                turned(state, angle),
                (reference, "V" + reference[1:]),
            )
    before = E.write_symbol_sheet(tmp_path / "sheet.kicad_sch", symbols)
    after = tmp_path / "moved.kicad_sch"
    move_all(before, after, moves)
    problems = rigid_problems(before, after, moves)
    assert not problems, f"{len(problems)} of 96 fields off:\n" + "\n".join(problems[:20])


_JUSTIFY = st.sampled_from(("", "left", "right"))


@st.composite
def _moves(draw):
    """1 to 6 symbols at their own cells on the grid, in drawn states, each with drawn field
    offsets, text angles and justifications, each moved to a drawn grid point in its cell
    and turned to a drawn angle or none."""
    symbols, moves = [], {}
    for k in range(draw(st.integers(1, 6))):
        cell = (127_000 + 1_016_000 * (k % 3), 127_000 + 1_270_000 * (k // 3))
        at, target = (
            (cell[0] + GRID * draw(st.integers(0, 40)), cell[1] + GRID * draw(st.integers(0, 40)))
            for _ in range(2)
        )
        state = draw(G.symbol_states())
        rotation = draw(st.sampled_from(_ROTATIONS))
        reference = f"R{k + 1}"
        justify = (draw(_JUSTIFY), draw(_JUSTIFY))
        symbols.append(symbol(at, state, draw(_FIELD_SPECS), reference=reference, justify=justify))
        shown = (reference, "V" + reference[1:])
        moves[reference] = Move(target, rotation, turned(state, rotation), shown)
    return symbols, moves


_SPECS = (((50_800, -25_400), 0), ((-38_100, 63_500), 90), ((0, 0), 0), ((0, 0), 0))


@pytest.mark.nightly
@requires_cli
@settings(max_examples=sexpr_gen.budget(25))
@given(_moves())
@example(
    (
        [symbol((381_000, 381_000), (90, "x"), _SPECS)],
        {"R1": Move((635_000, 381_000), 180, turned((90, "x"), 180), ("R1", "V1"))},
    )
)
def test_nightly_fields_follow_the_symbol_where_kicad_draws_them(case):
    """The same over generated symbols and moves."""
    symbols, moves = case
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        before = E.write_symbol_sheet(Path(work) / "sheet.kicad_sch", symbols)
        after = Path(work) / "moved.kicad_sch"
        move_all(before, after, moves)
        problems = rigid_problems(before, after, moves)
    assert not problems, f"{len(problems)} fields off:\n" + "\n".join(problems[:20])


# ---------------------------------------------------------------------------
# a symbol KiCad cannot load, and one it loads off the page
# ---------------------------------------------------------------------------

#: tests/fixtures/kicad_native.kicad_sch, written by KiCad: an A4 sheet (297 x 210 mm) with R1
#: at (100, 100), angle 0.
_NATIVE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"
_R1_AT = b"(at 100 100 0) (unit 1)"

#: R1's (at) angle as a hand edit or another generator leaves it, each spelling one KiCad's
#: parser refuses: the angle atom (empty for none), and the words a result must use for it.
#: NeedNUMBER meets the closing paren where the angle is missing and takes inf and nan for no
#: NUMBER; 1e400 is a NUMBER that from_chars reports out of range; 45 is a number that is no
#: quarter turn (sch_io_kicad_sexpr_parser.cpp:3071-3085 at 9.0.8, 3188-3202 at 10.0.7;
#: include/dsnlexer.h:531-535 at 9.0.8, 536-540 at 10.0.7; kicad_ref.parse_units).
_UNREADABLE = [
    pytest.param(b"", "no angle", id="none"),
    pytest.param(b"45", "angle 45", id="45"),
    pytest.param(b"inf", "angle inf", id="inf"),
    pytest.param(b"1e400", "angle 1e400", id="1e400"),
    pytest.param(b"nan", "angle nan", id="nan"),
]


def _at(angle: bytes, x: str = "100", y: str = "100") -> bytes:
    """An (at x y angle) list as bytes; the angle atom is left out where ``angle`` is empty."""
    return b"(at " + x.encode() + b" " + y.encode() + (b" " + angle if angle else b"") + b")"


def _hand_edited(tmp_path: Path, at: bytes, name: str = "hand.kicad_sch") -> Path:
    """The native fixture with R1's placement written ``at`` (one or more (at) lists)."""
    data = _NATIVE.read_bytes()
    if data.count(_R1_AT) != 1:
        raise RuntimeError(f"the fixture holds {_R1_AT!r} {data.count(_R1_AT)} times, not once")
    path = tmp_path / name
    path.write_bytes(data.replace(_R1_AT, at + b" (unit 1)"))
    return path


def _refused_by_kicad(data: bytes, reference: str) -> bool:
    """Whether KiCad's parser, as kicad_ref ports it, refuses the placement of the placed
    symbol ``reference`` (an angle it cannot read, or none)."""
    (symbol,) = [s for s in placed_symbols(lexed_root(data)) if reference_property(s) == reference]
    try:
        symbol_placement(symbol)
    except P.ParseRefusal:
        return True
    return False


def _premise_cli_refuses(path: Path) -> None:
    """The premise of a test about a sheet KiCad cannot load, where kicad-cli is installed:
    it writes no ERC report for the sheet (conftest.run_erc raises). RuntimeError when it
    loads it, so a broken premise never passes for the defect."""
    if not HAS_KICAD_CLI:
        return
    try:
        run_erc(path)
    except RuntimeError:
        return
    raise RuntimeError(f"the premise is a schematic kicad-cli refuses, and it loads {path.name}")


def _premise_unloadable(path: Path, reference: str = "R1") -> None:
    """The premise of a test about a symbol KiCad cannot load: the parser port refuses the
    placement of ``reference``, and kicad-cli, where installed, refuses the sheet."""
    if not _refused_by_kicad(path.read_bytes(), reference):
        raise RuntimeError(f"the premise is a placement KiCad refuses; it reads {path.name}")
    _premise_cli_refuses(path)


def _outcome(call):
    """(result, None) when ``call()`` returns, (None, exception) when it raises."""
    try:
        return call(), None
    except Exception as exc:  # the type of what escapes is the subject
        return None, exc


def _written_fields(data: bytes) -> dict[str, tuple[tuple[int, int], float]]:
    """{name: ((x, y), text angle)} of R1's properties: each last (at) as KiCad reads its
    numbers, whatever angle the symbol itself is placed at (the lexer port, no transform)."""
    (r1,) = [s for s in placed_symbols(lexed_root(data)) if reference_property(s) == "R1"]
    out = {}
    for prop in r1.items:
        if prop.kind == L.GROUP and prop.items and prop.items[0].value == b"property":
            (*_, at) = [c for c in prop.items if c.kind == L.GROUP and c.items[0].value == b"at"]
            xy = (read_iu(at.items[1]), read_iu(at.items[2]))
            out[SF.property_name(prop)] = (xy, P.parse_double(at.items[3].value))
    return out


@pytest.mark.kicad_placeholder("hand.kicad_sch")
@pytest.mark.parametrize(("angle", "named"), _UNREADABLE)
def test_a_symbol_kicad_cannot_load_is_moved_only_with_a_rotation(tmp_path, angle, named):
    """R1 placed at an angle KiCad's parser refuses, or with none, moved with no rotation:
    a ToolError that names the rotation as the repair, and the file byte for byte as it was.
    Moving it and reporting success would leave a sheet KiCad still cannot open, and a raw
    exception reaches an MCP client as a bare "Error executing tool". The unloadable input is
    this test's subject, so the oracle skips it."""
    path = _hand_edited(tmp_path, _at(angle))
    _premise_unloadable(path)
    before = path.read_bytes()
    result, raised = _outcome(
        lambda: schematic.move_component("R1", 63.5, 63.5, schematic_path=str(path))
    )
    assert isinstance(raised, ToolError), (
        f"returned {result!r}, raised {raised!r}, on a sheet kicad-cli cannot load"
    )
    assert "rotation" in str(raised), f"the refusal names no repair: {raised}"
    assert path.read_bytes() == before


@pytest.mark.parametrize(("angle", "named"), _UNREADABLE)
def test_a_symbol_kicad_cannot_load_is_repaired_by_a_rotation(tmp_path, angle, named):
    """R1 moved to (63.5, 63.5) at rotation 90 from a placement KiCad cannot load: KiCad
    reads R1 there at 90, and every field moved by R1's own displacement and was not turned,
    since an orientation KiCad cannot read gives the old one no meaning; the result says how
    R1 was placed. The oracle then checks that kicad-cli loads the repaired file."""
    path = _hand_edited(tmp_path, _at(angle))
    _premise_unloadable(path)
    written = _written_fields(path.read_bytes())
    result, raised = _outcome(
        lambda: schematic.move_component("R1", 63.5, 63.5, rotation=90, schematic_path=str(path))
    )
    assert raised is None, f"raised {raised!r} on a placement a rotation repairs"
    _, new = sch_judge.placed_symbol(path.read_bytes(), "R1")
    assert (new.position, new.transform) == ((635_000, 635_000), turned((0, ""), 90))
    dx, dy = 635_000 - 1_000_000, 635_000 - 1_000_000
    moved = {name: ((x + dx, y + dy), text) for name, ((x, y), text) in written.items()}
    assert {n: (f.written, f.angle) for n, f in new.fields.items()} == moved, (
        "the fields did not move with the repaired symbol"
    )
    assert named in str(result), f"the result does not say R1 had {named}: {result!r}"


@pytest.mark.kicad_placeholder("hand.kicad_sch")
@pytest.mark.parametrize(("angle", "named"), _UNREADABLE)
def test_an_earlier_at_kicad_cannot_load_is_refused(tmp_path, angle, named):
    """R1 written with two (at) lists, the first one KiCad's parser refuses: it reads the
    first before the last, so no edit of the last one makes the sheet load. A move with a
    rotation is refused with a ToolError and the file byte for byte as it was."""
    path = _hand_edited(tmp_path, _at(angle) + b" " + _at(b"0", "127", "127"))
    _premise_unloadable(path)
    before = path.read_bytes()
    result, raised = _outcome(
        lambda: schematic.move_component("R1", 63.5, 63.5, rotation=90, schematic_path=str(path))
    )
    assert isinstance(raised, ToolError), (
        f"returned {result!r}, raised {raised!r}, on a sheet kicad-cli cannot load"
    )
    assert path.read_bytes() == before


def _wire_with_x(tmp_path: Path, x: bytes) -> Path:
    """The native fixture with the x of its wire's second end written ``x``, a token KiCad's
    parser refuses as a coordinate."""
    data = _NATIVE.read_bytes()
    old = b"(xy 50 100) (xy 80 100)"
    if data.count(old) != 1:
        raise RuntimeError(f"the fixture holds {old!r} {data.count(old)} times, not once")
    try:
        P.parse_sch_internal_units(x, major=9)
    except P.ParseRefusal:
        pass
    else:
        raise RuntimeError(f"the premise is a coordinate KiCad's parser refuses; it reads {x!r}")
    path = tmp_path / "hand.kicad_sch"
    path.write_bytes(data.replace(old, b"(xy 50 100) (xy " + x + b" 100)"))
    _premise_cli_refuses(path)
    return path


def _second_symbol_at_1e400(tmp_path: Path) -> Path:
    """A sheet of two resistors, R1 and R2, with the angle of R2's own (at) written 1e400."""
    r1 = symbol((1_016_000, 1_016_000), (0, ""), _SPECS)
    r2 = symbol((1_905_000, 1_016_000), (0, ""), _SPECS, reference="R2")
    path = E.write_symbol_sheet(tmp_path / "hand.kicad_sch", [r1, r2])
    x, y = G.sch_mm(1_905_000), G.sch_mm(1_016_000)
    written = f"(at {x} {y} 0)".encode()
    head, opener, block = path.read_bytes().rpartition(b"\t(symbol\n\t\t(lib_id")
    if not opener or written not in block:
        raise RuntimeError("R2's own (at) is not where the builder writes it")
    path.write_bytes(head + opener + block.replace(written, _at(b"1e400", x, y), 1))
    _premise_unloadable(path, "R2")
    return path


_ELSEWHERE = [
    pytest.param(lambda d: _wire_with_x(d, b"1e400"), id="wire_1e400"),
    pytest.param(lambda d: _wire_with_x(d, b"abc"), id="wire_text"),
    pytest.param(_second_symbol_at_1e400, id="other_symbol_1e400"),
]


@pytest.mark.kicad_placeholder("hand.kicad_sch")
@pytest.mark.parametrize("build", _ELSEWHERE)
def test_a_move_goes_ahead_where_the_rest_of_the_sheet_cannot_be_read(tmp_path, build):
    """A plain move of R1 on a sheet that holds, elsewhere, something KiCad's parser refuses
    (a coordinate or a placement) goes ahead: KiCad reads R1 at the target and every field at
    the offset it had, and the result says its connections were not checked. The edit does not
    depend on that report, so a failure to read the sheet must not stop the move; an exception
    reaches an MCP client as a bare "Error executing tool". The unloadable sheet is the
    subject, so the oracle skips it."""
    path = build(tmp_path)
    _, old = sch_judge.placed_symbol(path.read_bytes(), "R1")
    result, raised = _outcome(
        lambda: schematic.move_component("R1", 63.5, 63.5, schematic_path=str(path))
    )
    assert raised is None, f"raised {raised!r} instead of moving R1"
    _, new = sch_judge.placed_symbol(path.read_bytes(), "R1")
    assert new.position == (635_000, 635_000), f"returned {result!r}, R1 is at {new.position}"
    assert {n: f.offset for n, f in new.fields.items()} == {
        n: f.offset for n, f in old.fields.items()
    }, "the symbol moved and its fields did not keep their place on it"
    assert result is not None and "Connections were not checked" in result, result


@pytest.mark.parametrize(
    "error",
    [ValueError("a pin number"), OverflowError("a coordinate"), AssertionError("a transform")],
    ids=lambda error: type(error).__name__,
)
def test_a_report_that_cannot_be_computed_does_not_stop_the_move(
    kicad_native_sch, monkeypatch, error
):
    """The contact report only advises, so whatever its comparison of the moved symbol's pins
    raises (here injected, on the native fixture, which kicad-cli loads) does not stop the
    move: KiCad reads R1 at the target and every field at the offset it had, and the result
    says its connections were not checked."""

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(_connectivity, "contact_changes", broken)
    _, old = sch_judge.placed_symbol(kicad_native_sch.read_bytes(), "R1")
    result, raised = _outcome(
        lambda: schematic.move_component("R1", 63.5, 63.5, schematic_path=str(kicad_native_sch))
    )
    assert raised is None, f"raised {raised!r} instead of moving R1"
    _, new = sch_judge.placed_symbol(kicad_native_sch.read_bytes(), "R1")
    assert new.position == (635_000, 635_000), f"returned {result!r}, R1 is at {new.position}"
    assert {n: f.offset for n, f in new.fields.items()} == {
        n: f.offset for n, f in old.fields.items()
    }, "the symbol moved and its fields did not keep their place on it"
    assert result is not None and "Connections were not checked" in result, result


#: Tokens written over a number in place of it. move_component must meet each with a result or a
#: refusal, never with another exception. KiCad's parseDouble refuses four of them: a literal
#: past a double's range (from_chars reports it out of range), inf and nan, which are no NUMBER,
#: and a word. It reads the fifth, 1e308, which is in range: a coordinate or a size takes it
#: clamped to INT_MAX * 0.7071 internal units (parseInternalUnits, sch_io_kicad_sexpr_parser.cpp:
#: 618-638 at 9.0.8, 676-696 at 10.0.7), and kicad-cli loads a sheet that holds it as R1's x
#: (test_ladder_r2_judges.py::test_number_token_refusals_match_kicad_cli). The tool cannot
#: convert it: times 10,000 it is infinity, and _connectivity.iu does not clamp (bug 69).
_HOSTILE_NUMBERS = (b"1e400", b"1e308", b"inf", b"nan", b"abc")


@pytest.mark.parametrize("major", [9, 10])
def test_kicad_refuses_four_hostile_numbers_and_reads_1e308_clamped(major):
    """The comment above, judged by the parser port (kicad_ref.parse_units): of the hostile
    tokens only 1e308 is read, and as a coordinate it is the clamp, INT_MAX * 0.7071 =
    1518485686.79 internal units, rounded. The same on both majors."""
    read = {}
    for token in _HOSTILE_NUMBERS:
        try:
            read[token] = P.parse_sch_internal_units(token, major=major)
        except P.ParseRefusal:
            pass
    assert read == {b"1e308": 1_518_485_687}


@pytest.mark.kicad_placeholder("hand.kicad_sch")
@pytest.mark.parametrize(
    "call", [(63.5, 63.5, None), (100.0, 100.0, 90)], ids=["move", "turn_in_place"]
)
def test_no_hostile_number_makes_a_move_raise_anything_but_a_refusal(tmp_path, call):
    """Each number of the native fixture, as the lexer port finds them, written in turn as
    each hostile token above, then R1 moved or turned in place: the call returns, or raises a
    ToolError and leaves the file byte for byte as it was. An exception of any other type is
    what an MCP client is told only as "Error executing tool", and it is what one
    OverflowError after another (a pin's coordinate, a wire's, an angle) once was. For 1e308
    as a coordinate the ToolError is the tool's own limit, since KiCad loads that sheet. The
    sheets are hostile on purpose, so the oracle skips the one this test leaves."""
    data = _NATIVE.read_bytes()
    spans = [
        (item.start, item.end)
        for _, item in J.walk(data)
        if item.items is None and item.kind == L.NUMBER
    ]
    if len(spans) < 50:
        raise RuntimeError(f"the premise is a sheet of many numbers, and it holds {len(spans)}")
    path = tmp_path / "hand.kicad_sch"
    x, y, rotation = call
    escaped = []
    for start, end in spans:
        for token in _HOSTILE_NUMBERS:
            hostile = data[:start] + token + data[end:]
            path.write_bytes(hostile)
            _, raised = _outcome(
                lambda: schematic.move_component(
                    "R1", x, y, rotation=rotation, schematic_path=str(path)
                )
            )
            refused = isinstance(raised, ToolError) and path.read_bytes() == hostile
            if raised is not None and not refused:
                escaped.append(f"{data[start:end]!r} written {token!r}: {raised!r}")
    assert not escaped, f"{len(escaped)} calls of {len(spans) * len(_HOSTILE_NUMBERS)}: " + (
        "; ".join(escaped[:4])
    )


def test_the_refusal_of_a_coordinate_kicad_clamps_does_not_blame_kicad(tmp_path):
    """R1 placed at x = 1e308 and moved: the tool cannot read the coordinate and refuses it,
    naming the token, with the file byte for byte as it was. KiCad reads it, clamped, and
    loads the sheet (the comment over _HOSTILE_NUMBERS), so the message must not say that
    KiCad cannot read it. The sheet is no placeholder, so the oracle loads it too."""
    path = _hand_edited(tmp_path, _at(b"0", "1e308", "100"))
    before = path.read_bytes()
    result, raised = _outcome(
        lambda: schematic.move_component("R1", 63.5, 63.5, schematic_path=str(path))
    )
    assert isinstance(raised, ToolError), f"returned {result!r}, raised {raised!r}"
    assert "1e308" in str(raised), f"the refusal does not name the token: {raised}"
    assert "KiCad" not in str(raised), f"the refusal blames KiCad for a token it reads: {raised}"
    assert path.read_bytes() == before


#: Places the 297 x 210 mm native sheet does not hold, each on the 0.01 mm grid KiCad writes.
_OFF_THE_PAGE = [
    pytest.param(-10.16, 50.8, id="left"),
    pytest.param(304.8, 50.8, id="right"),
    pytest.param(100.33, -2.54, id="above"),
    pytest.param(100.33, 215.9, id="below"),
]


@pytest.mark.parametrize(("x", "y"), _OFF_THE_PAGE)
def test_a_symbol_off_the_page_is_turned_in_place(tmp_path, x, y):
    """R1 placed off the page, which kicad-cli loads, called at its own position: it turns in
    place, KiCad reading it at the same anchor with the new angle and every field at the
    offset it had. KiCad's editor turns a symbol about its own position wherever it is
    (sch_edit_tool.cpp:814 at 9.0.8, 1009 at 10.0.7), and a call that names that position
    moves nothing, so the page cannot bound it. With no rotation it changes nothing and
    writes nothing."""
    path = _hand_edited(tmp_path, _at(b"0", str(x), str(y)))
    if HAS_KICAD_CLI:
        run_erc(path)  # the premise: kicad-cli loads a symbol placed off the page
    _, old = sch_judge.placed_symbol(path.read_bytes(), "R1")
    result = schematic.move_component("R1", x, y, rotation=90, schematic_path=str(path))
    turned_bytes = path.read_bytes()
    _, new = sch_judge.placed_symbol(turned_bytes, "R1")
    assert (new.position, new.transform) == (old.position, turned((0, ""), 90)), result
    assert {n: f.offset for n, f in new.fields.items()} == {
        n: f.offset for n, f in old.fields.items()
    }, "the symbol turned and its fields did not keep their place on it"
    schematic.move_component("R1", x, y, schematic_path=str(path))
    assert path.read_bytes() == turned_bytes


@pytest.mark.parametrize(
    ("x", "y"), [(-10.16, 50.8), (304.8, 50.8)], ids=["left_of_the_page", "right_of_the_page"]
)
def test_the_remedy_a_pin_read_names_can_be_followed_off_the_page(tmp_path, x, y):
    """A pin read of R1 placed off the page at an angle KiCad cannot load refuses and names
    move_component at the symbol's own position with a rotation as the repair, since KiCad
    cannot open the sheet to repair it there. Following it leaves a file kicad-cli loads, and
    the pin read then answers."""
    path = _hand_edited(tmp_path, _at(b"45", str(x), str(y)))
    _premise_unloadable(path)
    with pytest.raises(ToolError) as refused:
        schematic.get_pin_positions("R1", schematic_path=str(path))
    assert "move_component" in str(refused.value), (
        f"the remedy names no repair that works: {refused.value}"
    )
    schematic.move_component("R1", x, y, rotation=0, schematic_path=str(path))
    if HAS_KICAD_CLI:
        assert_kicad_parseable(path)
    schematic.get_pin_positions("R1", schematic_path=str(path))


def test_a_symbol_off_the_page_is_not_moved_to_another_place_off_it(tmp_path):
    """The page still bounds a symbol that moves: R1 placed off the page is refused a move to
    another place off it, with the file byte for byte as it was, and may move back onto it."""
    path = _hand_edited(tmp_path, _at(b"0", "-10.16", "50.8"))
    before = path.read_bytes()
    with pytest.raises(ToolError, match="outside the sheet boundary"):
        schematic.move_component("R1", -20.32, 50.8, schematic_path=str(path))
    assert path.read_bytes() == before
    schematic.move_component("R1", 63.5, 63.5, schematic_path=str(path))
    _, new = sch_judge.placed_symbol(path.read_bytes(), "R1")
    assert new.position == (635_000, 635_000)
