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
from conftest import requires_cli
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import sch_field as SF
from kicad_ref import transform as T

from mcp_server_kicad import schematic

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
