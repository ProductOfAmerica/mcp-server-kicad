"""Rung 2's judges, each checked against something other than itself.

The ports rung 2 adds to tests/kicad_ref answer to KiCad: the symbol transform to the
pin ends kicad-cli delivered (routing_fixtures.ORIENTED), to every placed symbol in the
schematics KiCad saved, and to kicad-cli's netlist; RotatePoint, the angle normalizers
and the footprint-relative positions to pcbnew; the number parsers to kicad-cli's load
verdicts, the pin angle reader to its load verdicts and drawings, and the load clamps to
its netlist and position export; FormatAngle and the point packing to the boards KiCad
saved. geom_judge answers to constructed cases, to a second algorithm that shares none
of its arithmetic, and to pcbnew's board outlines and shape boxes. geom_gen's builders
answer to KiCad loading what they write and to the lexer port reading it back.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import tempfile
import uuid
from fractions import Fraction
from pathlib import Path

import geom_gen as G
import geom_judge as GJ
import kicad_corpus as K
import pcbnew_judge as PJ
import pytest
import routing_fixtures as RF
import sexpr_gen
from conftest import (
    _kicad_cli_major,
    _nightly_skip_reason,
    netlist_fields,
    netlist_nodes,
    requires_cli,
)
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import board_item as B
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import kicad_io_utils as IO
from kicad_ref import parse_units as P
from kicad_ref import transform as T
from kicad_ref import trigo
from routing_fixtures import ORIENTED

from mcp_server_kicad._shared import _run_cli

FIXTURES = Path(__file__).parent / "fixtures"
MM = 1_000_000  # nanometres per millimetre


# ---------------------------------------------------------------------------
# reading files with the lexer port
# ---------------------------------------------------------------------------


def _head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _children(group, name: bytes) -> list:
    return [c for c in group.items if _head(c) == name]


def _find(group, name: bytes):
    """The first child list headed ``name``, or None."""
    found = _children(group, name)
    return found[0] if found else None


def _child(group, name: bytes):
    """The first child list headed ``name``; LookupError when there is none."""
    found = _find(group, name)
    if found is None:
        raise LookupError(f"no ({name.decode()} ...) in the list at byte {group.start}")
    return found


def _walk(items):
    """Every Group in ``items``, depth first."""
    stack = list(items)
    while stack:
        item = stack.pop()
        if item.kind == L.GROUP:
            yield item
            stack.extend(item.items)


def _root(data: bytes):
    return L.tree(data, knows_bar=L.knows_bar_of(data))[0]


def _cli_major() -> int:
    major = _kicad_cli_major()
    if major < 9:
        raise RuntimeError(f"cannot read the major version of kicad-cli (got {major})")
    return major


def _pcbnew_or_skip() -> int:
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    return PJ.major()


# ---------------------------------------------------------------------------
# library pins and placed symbols, read with the lexer port
# ---------------------------------------------------------------------------


def _library_symbols(container) -> dict[str, object]:
    """{name: (symbol ...) group} of the symbols directly in ``container``."""
    return {s.items[1].value.decode(): s for s in _children(container, b"symbol")}


def _lib_pins(symbol, unit: int = 1, style: int = 1) -> list[tuple[str, int, int, str]]:
    """(number, x, y, orientation) of the pins unit ``unit`` draws in body style
    ``style``: those of every sub-symbol NAME_U_S with U in (0, unit) and S in (0,
    style), positions in internal units as written (y up)."""
    out = []
    for sub in _children(symbol, b"symbol"):
        u, s = (int(part) for part in sub.items[1].value.decode().rsplit("_", 2)[1:])
        if u not in (0, unit) or s not in (0, style):
            continue
        for pin in _children(sub, b"pin"):
            at = _child(pin, b"at")
            number = _child(pin, b"number").items[1].value.decode()
            out.append(
                (
                    number,
                    P.parse_sch_internal_units(at.items[1].value, major=10),
                    P.parse_sch_internal_units(at.items[2].value, major=10),
                    T.pin_orientation(at.items[3]),
                )
            )
    return out


def _symbol_children(symbol) -> list[tuple[str, object]]:
    """The ("at", angle token) and ("mirror", axis token) items of a placed symbol, in
    file order, as parse_symbol_transform takes them."""
    out: list[tuple[str, object]] = []
    for item in symbol.items:
        if _head(item) == b"at":
            out.append(("at", item.items[3] if len(item.items) > 3 else None))
        elif _head(item) == b"mirror":
            out.append(("mirror", item.items[1] if len(item.items) > 1 else None))
    return out


def _placed_symbols(root) -> list:
    """The placed symbols of a schematic: top-level (symbol ...) lists with a lib_id."""
    return [s for s in _children(root, b"symbol") if _find(s, b"lib_id") is not None]


def _property(symbol, key: bytes) -> str:
    for prop in _children(symbol, b"property"):
        if len(prop.items) > 2 and prop.items[1].value == key:
            return prop.items[2].value.decode()
    raise LookupError(key)


_ROUTING_LIB = _library_symbols(_root((FIXTURES / "routing.kicad_sym").read_bytes()))


def _state_matrix(rotation: int, mirror: str) -> T.Matrix:
    return T.parse_symbol_transform(
        [("at", str(rotation))] + ([("mirror", mirror)] if mirror else [])
    )


# ---------------------------------------------------------------------------
# the symbol transform
# ---------------------------------------------------------------------------


def test_transform_port_matches_the_oriented_table():
    """For R, D and Q_NPN_BCE in all 12 states, pin_position and pin_outward reproduce
    every pin end and outward direction kicad-cli 9.0.8 delivered in the routing
    pressure test (routing_fixtures.ORIENTED, 84 pin ends), the pins read from
    tests/fixtures/routing.kicad_sym by the lexer port."""
    checked = 0
    for name, table in ORIENTED.items():
        pins = sorted(_lib_pins(_ROUTING_LIB[name]), key=lambda pin: int(pin[0]))
        assert set(table) == set(G.STATES)
        for (rotation, mirror), ends in table.items():
            m = _state_matrix(rotation, mirror)
            assert len(pins) == len(ends), (name, pins)
            for (number, lx, ly, orientation), (dx, dy, direction) in zip(pins, ends):
                want = (U.kiround(dx * 1e4, major=10), U.kiround(dy * 1e4, major=10))
                assert T.pin_position((0, 0), m, (lx, ly)) == want, (name, rotation, mirror, number)
                assert T.pin_outward(m, orientation) == direction, (name, rotation, mirror, number)
                checked += 1
    assert checked == 84


def test_transform_port_obeys_the_group_laws():
    """The dihedral group of the square, as KiCad builds it: 12 states give 8 matrices;
    a quarter turn four times and a mirror twice are the identity; mirroring x then y is
    a half turn; the inverse is two-sided; a mirror written before (at) is lost; KiCad
    rotates before it mirrors, which differs from the other order exactly at 90 and 270;
    and the writer's canonical form parses back to the matrix it came from."""
    states = {state: _state_matrix(*state) for state in G.STATES}
    matrices = set(states.values())
    assert len(matrices) == 8
    for m in matrices:
        assert sorted(abs(v) for v in m) == [0, 0, 1, 1]  # a signed permutation
        assert m[0] * m[3] - m[1] * m[2] in (1, -1)
    r90 = T.PARSER_ROTATIONS[90]
    assert T.set_orientation(T.IDENTITY, T.SYM_ORIENT_90) == r90
    for temp in T.ROTATE_TEMPS.values():
        m = T.IDENTITY
        for _ in range(4):
            m = T.compose_incremental(m, temp)
        assert m == T.IDENTITY
    for code in (T.SYM_ROTATE_COUNTERCLOCKWISE, T.SYM_ROTATE_CLOCKWISE):
        m = T.IDENTITY
        for _ in range(4):
            m = T.set_orientation(m, code)
        assert m == T.IDENTITY
    for m in matrices:
        for code in (T.SYM_MIRROR_X, T.SYM_MIRROR_Y):
            assert T.set_orientation(T.set_orientation(m, code), code) == m
        inverse = T.inverse_transform(m)
        assert T.compose_incremental(m, inverse) == T.IDENTITY
        assert T.compose_incremental(inverse, m) == T.IDENTITY
        for point in ((1, 0), (0, 1), (3, -7), (-12_700, 38_100)):
            assert T.transform_coordinate(inverse, *T.transform_coordinate(m, *point)) == point
    mirrored = T.set_orientation(T.IDENTITY, T.SYM_MIRROR_X)
    assert T.set_orientation(mirrored, T.SYM_MIRROR_Y) == T.PARSER_ROTATIONS[180]
    for axis in ("x", "y"):
        assert T.parse_symbol_transform([("mirror", axis), ("at", "90")]) == r90

    def apply(m, p):  # TransformCoordinate (transform.cpp:44-47 at 9.0.8), by hand
        return m[0] * p[0] + m[1] * p[1], m[2] * p[0] + m[3] * p[1]

    # KiCad rotates before it mirrors (the parser replaces the matrix at (at), then
    # (mirror) multiplies the mirror on after it), and the two orders differ exactly
    # at 90 and 270.
    for rotation in (0, 90, 180, 270):
        rotate = T.PARSER_ROTATIONS[rotation]
        for axis in ("x", "y"):
            kicad = _state_matrix(rotation, axis)
            mirror = T.MIRROR_TEMPS[axis]
            points = ((1, 0), (0, 1), (3, -7))
            for p in points:
                assert T.transform_coordinate(kicad, *p) == apply(mirror, apply(rotate, p))
            differs = any(
                apply(mirror, apply(rotate, p)) != apply(rotate, apply(mirror, p)) for p in points
            )
            assert differs is (rotation in (90, 270)), (rotation, axis)
    # GetOrientation's list (sch_symbol.cpp:1928-1942 at 9.0.8), traced by hand: the four
    # rotations, then x at 0, 90 and 270, then y at 0; each other state's matrix equals
    # an earlier entry. The corpus test below checks that KiCad writes only these.
    canonical = {(0, ""), (90, ""), (180, ""), (270, ""), (0, "x"), (90, "x"), (270, "x"), (0, "y")}
    written = {state: T.written_orientation(m) for state, m in states.items()}
    assert set(written.values()) == canonical
    for state, form in written.items():
        assert _state_matrix(*form) == states[state]
        assert T.written_orientation(_state_matrix(*form)) == form
    for m in matrices:
        assert T.set_orientation(T.IDENTITY, T.get_orientation(m)) == m
    with pytest.raises(ValueError):
        T.set_orientation(T.IDENTITY, T.SYM_MIRROR_X | T.SYM_MIRROR_Y)  # no such case
    with pytest.raises(ValueError):
        T.get_orientation((1, 1, 0, 1))


def _orientation_mismatches(top: list) -> tuple[int, list[str]]:
    """(placed symbols checked, mismatches) of one schematic, given the lexer port's
    tree of it: each placed symbol's (at) angle and (mirror) as written against
    written_orientation of what they parse to."""
    root = top[0]
    placed = _placed_symbols(root)
    wrong = []
    for symbol in placed:
        kids = _symbol_children(symbol)
        angle, axis = T.written_orientation(T.parse_symbol_transform(kids))
        at = _child(symbol, b"at")
        mirrors = [item.items[1].value.decode() for item in _children(symbol, b"mirror")]
        as_written = (at.items[3].value.decode() if len(at.items) > 3 else None, "".join(mirrors))
        if as_written != (U.format_angle(angle), axis):
            wrong.append(f"at byte {symbol.start}: written {as_written}, port {(angle, axis)}")
    return len(placed), wrong


_SCHEMATICS = K.shipped(suffixes=(".kicad_sch",), names=(), cap=K.PER_PUSH_CAP)
_BOARDS = K.shipped(suffixes=(".kicad_pcb",), names=(), cap=K.PER_PUSH_CAP)


def _per_file(files):
    return [pytest.param(path, id=name) for name, path in files] or [
        pytest.param(None, id="no-install", marks=pytest.mark.skip(reason=K.NO_INSTALL))
    ]


def _nightly_files(suffix: str):
    """Every installed file with ``suffix``, computed only under the nightly profile
    (walking the whole install at every collection is not worth it)."""
    if _nightly_skip_reason(os.environ.get("HYPOTHESIS_PROFILE")) is not None:
        return [pytest.param(None, id="nightly-only")]
    return _per_file(K.shipped(subdirs=None, suffixes=(suffix,), names=()))


@pytest.mark.parametrize("path", _per_file(_SCHEMATICS))
def test_kicad_writes_only_the_orientations_the_port_writes(path):
    """Every placed symbol in a demo or template schematic KiCad saved (files up to
    kicad_corpus.PER_PUSH_CAP) carries the (at) angle and (mirror) that saveSymbol
    writes for the matrix the parser builds from them. The lexer must find every
    placed symbol: as many as the file holds "(lib_id " tokens."""
    data = path.read_bytes()
    checked, wrong = _orientation_mismatches(K.lexed(path))
    assert checked == data.count(b"(lib_id "), "the lexer missed placed symbols"
    assert not wrong, "\n".join(wrong[:20])


@pytest.mark.nightly
@pytest.mark.parametrize("path", _nightly_files(".kicad_sch"))
def test_nightly_kicad_writes_only_the_orientations_the_port_writes(path):
    """The same over every schematic the install holds."""
    data = path.read_bytes()
    checked, wrong = _orientation_mismatches(L.tree(data, knows_bar=L.knows_bar_of(data)))
    assert checked == data.count(b"(lib_id "), "the lexer missed placed symbols"
    assert not wrong, "\n".join(wrong[:20])


def _label(name: str, x: int, y: int) -> bytes:
    return (
        f'\t(label "{name}"\n\t\t(at {G.sch_mm(x)} {G.sch_mm(y)} 0)\n'
        "\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n"
        f'\t\t\t(justify left bottom)\n\t\t)\n\t\t(uuid "{uuid.uuid4()}")\n\t)\n'
    ).encode()


def _labels_at_port_pin_ends(path: Path) -> dict[str, tuple[str, str]]:
    """Read every placed symbol back with the lexer port, put a label named
    N_<ref>_<pin> at each pin end the port computes, and return {label: (ref, pin)}.
    The labels go in as text before the root list's closing parenthesis."""
    data = path.read_bytes()
    root = _root(data)
    libraries = _library_symbols(_child(root, b"lib_symbols"))
    expected: dict[str, tuple[str, str]] = {}
    labels = b""
    for symbol in _placed_symbols(root):
        ref = _property(symbol, b"Reference")
        at = _child(symbol, b"at")
        origin = (
            P.parse_sch_internal_units(at.items[1].value, major=10),
            P.parse_sch_internal_units(at.items[2].value, major=10),
        )
        m = T.parse_symbol_transform(_symbol_children(symbol))
        unit = _find(symbol, b"unit")
        # The lib_symbols entry a symbol draws: its lib_name when it has one, else its
        # lib_id (SCH_SYMBOL::GetSchSymbolLibraryName, sch_symbol.cpp:242-248 at 9.0.8,
        # 246-252 at 10.0.7).
        named = _find(symbol, b"lib_name") or _child(symbol, b"lib_id")
        lib = libraries[named.items[1].value.decode()]
        for number, lx, ly, _ in _lib_pins(lib, int(unit.items[1].value) if unit else 1):
            px, py = T.pin_position(origin, m, (lx, ly))
            name = f"N_{ref}_{number}"
            expected[name] = (ref, number)
            labels += _label(name, px, py)
    close = root.end - 1
    path.write_bytes(data[:close] + labels + data[close:])
    return expected


def _assert_each_pin_joins_its_label(path: Path, expected: dict[str, tuple[str, str]]) -> None:
    nets = netlist_nodes(path)
    wrong = []
    for name, node in sorted(expected.items()):
        got = set(nets.get(name, {}))
        if got != {node}:
            wrong.append(f"{name}: kicad-cli puts {sorted(got)} on it, the port {node}")
    assert not wrong, f"kicad-cli {_cli_major()}:\n" + "\n".join(wrong)


@requires_cli
def test_port_pin_ends_join_in_kicad_cli(tmp_path):
    """R, D and Q_NPN_BCE placed in all 12 states; a label at every pin end the port
    computes from the file's own tokens puts that pin, and only it, on the label's net
    in kicad-cli's netlist."""
    path = Path(RF.fresh(tmp_path, "pins"))
    for row, name in enumerate(("R", "D", "Q_NPN_BCE")):
        for col, (rotation, mirror) in enumerate(G.STATES):
            ref = f"{'RDQ'[row]}{col + 1}"
            RF.place(str(path), name, ref, 20.32 * (col + 1), 38.1 * (row + 1), rotation, mirror)
    expected = _labels_at_port_pin_ends(path)
    assert len(expected) == 12 * (2 + 2 + 3)
    _assert_each_pin_joins_its_label(path, expected)


#: Symbols of routing.kicad_sym whose pins carry no implicit net: every one but the
#: power symbols, whose pins join a global net named by their value.
_JOIN_SYMBOLS = tuple(name for name, sym in _ROUTING_LIB.items() if not _children(sym, b"power"))


def _offset_placements(path: Path, moves: dict[str, tuple[int, int]]) -> None:
    """Move placed symbols to exact positions (internal units) by rewriting their (at)
    x and y through the lexer's offsets."""
    data = path.read_bytes()
    edits = []
    for symbol in _placed_symbols(_root(data)):
        ref = _property(symbol, b"Reference")
        if ref in moves:
            at = _child(symbol, b"at")
            x, y = at.items[1], at.items[2]
            edits.append((x.start, y.end, f"{G.sch_mm(moves[ref][0])} {G.sch_mm(moves[ref][1])}"))
    for start, end, text in sorted(edits, reverse=True):
        data = data[:start] + text.encode() + data[end:]
    path.write_bytes(data)


@pytest.mark.nightly
@requires_cli
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(
            st.sampled_from(_JOIN_SYMBOLS),
            G.symbol_states(),
            st.integers(0, 12_699),
            st.integers(0, 12_699),
        ),
        min_size=1,
        max_size=8,
    )
)
def test_nightly_port_pin_ends_join_in_kicad_cli(placements):
    """routing.kicad_sym's symbols in generated states, moved off the 1.27 mm grid by up
    to one grid step: labels at the port's pin ends join exactly their pins."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(RF.fresh(Path(work), "pins"))
        moves = {}
        for i, (name, (rotation, mirror), dx, dy) in enumerate(placements):
            ref = f"X{i + 1}"
            gx, gy = 254_000 * (1 + i % 4), 381_000 * (1 + i // 4)
            RF.place(str(path), name, ref, gx / 1e4, gy / 1e4, rotation, mirror)
            moves[ref] = (gx + dx, gy + dy)
        _offset_placements(path, moves)
        _assert_each_pin_joins_its_label(path, _labels_at_port_pin_ends(path))


# ---------------------------------------------------------------------------
# RotatePoint, angles and footprint-relative positions, against pcbnew
# ---------------------------------------------------------------------------

_PAD_POSITIONS = r"""
import pcbnew
result = []
for path in payload["boards"]:
    board = pcbnew.LoadBoard(path)
    for fp in board.GetFootprints():
        result.append([
            fp.GetReference(),
            [[pad.GetNumber(), pad.GetPosition().x, pad.GetPosition().y] for pad in fp.Pads()],
        ])
"""


def _footprints_in(path: Path) -> dict[str, dict]:
    """{reference: {"at": (x, y), "orientation": deg, "pads": {number: (x, y)}}} as KiCad
    reads the board's own tokens: positions through parseBoardUnits, the footprint's
    orientation as Normalize180( parseDouble( angle ) )."""
    major = 10  # both majors read these values alike; the comparison supplies its own
    out = {}
    for fp in _children(_root(path.read_bytes()), b"footprint"):
        at = _child(fp, b"at")
        orientation = (
            trigo.normalize180(P.parse_double(at.items[3].value)) if len(at.items) > 3 else 0.0
        )
        ref = next(
            p.items[2].value.decode()
            for p in _children(fp, b"property")
            if p.items[1].value == b"Reference"
        )
        pads = {}
        for pad in _children(fp, b"pad"):
            pat = _child(pad, b"at")
            pads[pad.items[1].value.decode()] = (
                P.parse_board_units(pat.items[1].value, major=major),
                P.parse_board_units(pat.items[2].value, major=major),
            )
        out[ref] = {
            "at": (
                P.parse_board_units(at.items[1].value, major=major),
                P.parse_board_units(at.items[2].value, major=major),
            ),
            "orientation": orientation,
            "pads": pads,
        }
    return out


def _check_pad_positions(boards: list[Path]) -> int:
    major = PJ.major()
    found = PJ.run(_PAD_POSITIONS, {"boards": [str(b) for b in boards]})
    written: dict[str, dict] = {}
    for board in boards:
        written.update(_footprints_in(board))
    checked = 0
    wrong = []
    for ref, pads in found:
        fp = written[ref]
        for number, x, y in pads:
            want = B.fp_relative_to_board(
                fp["at"], fp["orientation"], fp["pads"][number], major=major
            )
            checked += 1
            if (x, y) != want:
                wrong.append(
                    f"{ref} pad {number} at {fp['orientation']}: pcbnew {(x, y)}, port {want}"
                )
    assert not wrong, f"pcbnew {major}:\n" + "\n".join(wrong)
    return checked


_AWKWARD_ANGLES = (0, 90, -90, 180, 45, 30, 37.5, 359.9, 135, 12.3456789, -135.25, 0.001)
_PAD_OFFSETS = (
    (-5 * MM, MM),
    (1_234_567, -7_654_321),
    (1, 3 * MM),
    (12_500_000, 0),
    (-333_333, -666_667),
)


def test_rotate_point_port_matches_pcbnew_pad_positions(tmp_path):
    """Twelve footprints at awkward angles, five pads each at awkward offsets: every
    pad.GetPosition() pcbnew computes equals fp_relative_to_board of the board's own
    tokens (the footprint angle through Normalize180) at pcbnew's major, to the
    nanometre."""
    _pcbnew_or_skip()
    items = [
        G.footprint_text(
            f"U{i + 1}",
            (100 * MM + 10 * MM * i, 50_123_457),
            angle,
            [(str(k + 1), offset, 0.0) for k, offset in enumerate(_PAD_OFFSETS)],
        )
        for i, angle in enumerate(_AWKWARD_ANGLES)
    ]
    board = tmp_path / "pads.kicad_pcb"
    board.write_text(G.board_text(items), encoding="utf-8", newline="\n")
    assert _check_pad_positions([board]) == 60


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(
            st.tuples(G.nm_coordinates(1_000_000_000), G.nm_coordinates(1_000_000_000)),
            G.angles(),
            st.lists(
                st.tuples(G.nm_coordinates(50 * MM), G.nm_coordinates(50 * MM)),
                min_size=1,
                max_size=5,
            ),
        ),
        min_size=1,
        max_size=6,
    )
)
def test_nightly_rotate_point_port_matches_pcbnew(footprints):
    """Generated footprint positions, angles and pad offsets: pcbnew's pad positions
    equal the port's."""
    _pcbnew_or_skip()
    items = [
        G.footprint_text(f"U{i + 1}", at, angle, [(str(k + 1), p, 0.0) for k, p in enumerate(pads)])
        for i, (at, angle, pads) in enumerate(footprints)
    ]
    with tempfile.TemporaryDirectory() as work:
        board = Path(work) / "pads.kicad_pcb"
        board.write_text(G.board_text(items), encoding="utf-8", newline="\n")
        _check_pad_positions([board])


_ANGLE_SCRIPT = r"""
import pcbnew
result = []
for a in payload["angles"]:
    n = pcbnew.EDA_ANGLE(a, pcbnew.DEGREES_T)
    n.Normalize()
    n180 = pcbnew.EDA_ANGLE(a, pcbnew.DEGREES_T)
    n180.Normalize180()
    e = pcbnew.EDA_ANGLE(a, pcbnew.DEGREES_T)
    result.append([n.AsDegrees(), n180.AsDegrees(), e.Sin(), e.Cos()])
"""


def test_angle_normalizers_match_pcbnew():
    """EDA_ANGLE's Normalize, Normalize180, Sin and Cos in pcbnew equal the port's, bit
    for bit (the sign of zero included), on angles that reach every branch."""
    _pcbnew_or_skip()
    angles = [
        -0.0,
        -90.0,
        270.0,
        360.0,
        540.5,
        -180.0,
        180.0,
        -720.25,
        1e-9,
        359.99999999,
        45.0,
        135.0,
        225.0,
        315.0,
        -45.0,
        37.5,
        -1e-20,
        3600.5,
    ]
    found = PJ.run(_ANGLE_SCRIPT, {"angles": angles})
    port = [
        [trigo.normalize(a), trigo.normalize180(a), trigo.sin_deg(a), trigo.cos_deg(a)]
        for a in angles
    ]
    assert [[repr(v) for v in row] for row in found] == [[repr(v) for v in row] for row in port]


# ---------------------------------------------------------------------------
# number tokens, against kicad-cli
# ---------------------------------------------------------------------------


@requires_cli
def test_schematic_clamp_port_matches_kicad_cli(tmp_path):
    """R1 written at (at 200000 100 0), past parseInternalUnits' clamp (INT_MAX * 0.7071
    internal units, about 151848.5687 mm): a label at each pin end the port computes from
    the file's own tokens joins exactly that pin in kicad-cli's netlist. Pin 1 sits at
    x offset 0, so its label lies on the clamp, where KiCad clamps a label written past
    it too: it catches a port that clamps low. Pin 2 is moved to x offset -3.81 mm, so
    its label lies inside the range and is read where it is written: a port that clamps
    anywhere else, higher, lower or not at all, puts it off the pin."""
    path = tmp_path / "clamp.kicad_sch"
    path.write_bytes(
        G.schematic_with_r1(
            (2_000_000_000, 1_000_000), (0, ""), ((0, 38_100, 270), (-38_100, -38_100, 90))
        )
    )
    expected = _labels_at_port_pin_ends(path)
    if expected != {"N_R1_1": ("R1", "1"), "N_R1_2": ("R1", "2")}:
        raise RuntimeError(f"the sheet holds other pins than R1's two: {expected}")
    _assert_each_pin_joins_its_label(path, expected)


@requires_cli
def test_board_clamp_port_matches_kicad_cli(tmp_path):
    """Footprints written at x = 3000, -3000, 2147.483637 and 2147.483638 mm: kicad-cli's
    position export prints, for each, the nanometres parse_board_units reads (KiCad
    clamps to INT_MAX - 10 nm), and the y it was given, negated as the export does."""
    major = _cli_major()
    xs = ("3000", "-3000", "2147.483637", "2147.483638")
    items = []
    for i, x in enumerate(xs):
        y = (i + 1) * 10 * MM
        text = G.footprint_text(f"U{i + 1}", (0, y), 0.0)
        line = f"\t\t(at 0 {G.mm(y)})\n"
        assert text.count(line) == 1
        items.append(text.replace(line, f"\t\t(at {x} {G.mm(y)})\n"))
    board = tmp_path / "far.kicad_pcb"
    board.write_text(G.board_text(items), encoding="utf-8", newline="\n")
    out = tmp_path / "far-pos.csv"
    _run_cli(
        ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both"]
        + ["--output", str(out), str(board)],
        check=False,
    )
    if not out.is_file():
        raise RuntimeError("kicad-cli wrote no position file for a board it loads")
    rows = {row["Ref"]: row for row in csv.DictReader(io.StringIO(out.read_text()))}
    for i, x in enumerate(xs):
        row = rows[f"U{i + 1}"]
        printed = U.kiround(float(row["PosX"]) * MM, major=major)
        assert printed == P.parse_board_units(x, major=major), (x, row)
        assert U.kiround(-float(row["PosY"]) * MM, major=major) == (i + 1) * 10 * MM, row
    assert P.parse_board_units("2147.483638", major=major) == 2_147_483_637


def _port_loads(x: str, y: str, angle: str) -> bool:
    try:
        P.parse_sch_internal_units(x, major=10)
        P.parse_sch_internal_units(y, major=10)
        T.parse_symbol_transform([("at", angle)])
    except P.ParseRefusal:
        return False
    return True


#: R1's (at x y angle) tokens, with what the port says KiCad does with each.
_TOKEN_VARIANTS = [
    ("+100", "100", "0"),  # from_chars takes no leading '+'
    ("100", "100", "+90"),
    ("100", "100", "9_0"),  # no NUMBER: isNumber stops at the underscore
    ("100", "100", "90.5"),  # read as 90
    ("100", "100", "-0.5"),  # read as 0
    ("200000", "100", "0"),  # past the clamp: clamped, loads
    ("1e308", "100", "0"),  # in a double's range, infinite once scaled: clamped, loads
    ("100", "100", "-90"),  # static_cast<int> gives -90: not 0, 90, 180 or 270
    ("100", "100", "360"),
    ("100", "100", "0.9e2"),  # read as 90
    ("1e400", "100", "0"),  # out of a double's range
    ("1e-400", "100", "0"),  # a nonzero literal that underflows to zero: out of range too
    ("100", "100", "1e-400"),  # the same underflow, as an angle
    ("1e-300", "100", "0"),  # far below one unit, yet a normal double: loads at 0
    ("1e-310", "100", "0"),  # a subnormal double: loads at 0
]


@requires_cli
@pytest.mark.no_kicad_validation  # the refused files are the subject; kicad-cli judges each
@pytest.mark.parametrize("x,y,angle", _TOKEN_VARIANTS, ids=["-".join(v) for v in _TOKEN_VARIANTS])
def test_number_token_refusals_match_kicad_cli(tmp_path, x, y, angle):
    """kicad-cli loads a schematic whose R1 is written (at x y angle) exactly when every
    token passes parse_double and the parser's angle rule, as the port reads them."""
    data = (FIXTURES / "kicad_native.kicad_sch").read_bytes()
    anchor = b"(at 100 100 0) (unit 1)"
    assert data.count(anchor) == 1
    path = tmp_path / "tokens.kicad_sch"
    path.write_bytes(data.replace(anchor, f"(at {x} {y} {angle}) (unit 1)".encode()))
    loaded = netlist_fields(path) is not None
    assert loaded is _port_loads(x, y, angle), f"kicad-cli {_cli_major()} loaded={loaded}"


#: Angle tokens for pin 1 of Device:R, read by parseInt (NeedNUMBER, then strtol); a float
#: reading would load 9e1 (90), refuse 90.9e5 and point 0.9e2 up (90), and parseDouble
#: would refuse +90.
_PIN_ANGLE_VARIANTS = (
    "9e1",  # strtol stops at the 'e': 9, refused
    "90.9e5",  # strtol stops at the point: 90, up
    "+90",  # strtol takes a sign: 90, up
    "0.9e2",  # strtol stops at the point: 0, right
)


def _pin1_sheet(angle: str) -> bytes:
    """tests/fixtures/kicad_native.kicad_sch with pin 1 of Device:R written (at 0 3.81
    angle) in place of (at 0 3.81 270)."""
    data = (FIXTURES / "kicad_native.kicad_sch").read_bytes()
    anchor = b"(at 0 3.81 270)"
    if data.count(anchor) != 1:
        raise RuntimeError(f"the fixture holds {anchor!r} {data.count(anchor)} times")
    return data.replace(anchor, f"(at 0 3.81 {angle})".encode())


def _port_loads_pin(angle: str) -> bool:
    try:
        T.pin_orientation(angle)
    except P.ParseRefusal:
        return False
    return True


@requires_cli
@pytest.mark.no_kicad_validation  # the refused files are the subject; kicad-cli judges each
@pytest.mark.parametrize("angle", _PIN_ANGLE_VARIANTS)
def test_pin_angle_refusals_match_kicad_cli(tmp_path, angle):
    """kicad-cli loads a schematic whose Device:R pin 1 is written (at 0 3.81 angle)
    exactly when pin_orientation reads the angle, as strtol reads it, as 0, 90, 180 or
    270."""
    path = tmp_path / "pin.kicad_sch"
    path.write_bytes(_pin1_sheet(angle))
    loaded = netlist_fields(path) is not None
    assert loaded is _port_loads_pin(angle), f"kicad-cli {_cli_major()} loaded={loaded}"


#: The <title> of kicad-cli's SVG carries the export's date and time.
_SVG_TITLE = re.compile(rb"<title>.*?</title>", re.S)


def _drawing(work: Path, name: str, angle: str) -> bytes:
    """kicad-cli's SVG of the sheet whose pin 1 is written at ``angle``, title blanked."""
    sheet = work / name / "pin.kicad_sch"
    sheet.parent.mkdir()
    sheet.write_bytes(_pin1_sheet(angle))
    out = work / name / "svg"
    result = _run_cli(["sch", "export", "svg", "--output", str(out), str(sheet)], check=False)
    found = sorted(out.glob("*.svg"))
    if len(found) != 1:
        raise RuntimeError(
            f"kicad-cli drew {len(found)} SVG files for a pin at {angle!r}"
            f" (rc={result.returncode}): {result.stderr.strip()[:400]}"
        )
    return _SVG_TITLE.sub(b"<title/>", found[0].read_bytes())


@requires_cli
def test_pin_angle_orientations_match_kicad_cli_drawings():
    """Each spelling in _PIN_ANGLE_VARIANTS that the port loads draws, in kicad-cli's SVG
    of the sheet, exactly as the one of 0, 90, 180 and 270 that pin_orientation reads it
    as, and unlike the other three: 0.9e2 draws as 0 does, pointing right. A load verdict
    cannot see that; test_pin_end_reads_pin_angles_as_kicads_parser_does rests on it."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        work = Path(scratch)
        canonical = {a: _drawing(work, f"at{a}", str(a)) for a in (0, 90, 180, 270)}
        if len(set(canonical.values())) != 4:
            raise RuntimeError("kicad-cli draws two of the four pin angles alike")
        if _drawing(work, "at0-again", "0") != canonical[0]:
            raise RuntimeError("kicad-cli's SVG of one sheet varies between exports")
        drawn = {
            token: _drawing(work, f"variant{i}", token)
            for i, token in enumerate(_PIN_ANGLE_VARIANTS)
            if _port_loads_pin(token)
        }
    if not drawn:
        raise RuntimeError("the port loads none of the variants, so none was drawn")
    wrong = []
    for token, svg in drawn.items():
        alike = sorted(a for a, other in canonical.items() if other == svg)
        want = [a for a in canonical if T.pin_orientation(str(a)) == T.pin_orientation(token)]
        if alike != want:
            wrong.append(f"{token}: kicad-cli draws it as {alike}, the port reads {want}")
    assert not wrong, f"kicad-cli {_cli_major()}:\n" + "\n".join(wrong)


# ---------------------------------------------------------------------------
# angle text and point packing, against the boards KiCad saved
# ---------------------------------------------------------------------------


def _angle_problems(top: list) -> tuple[int, list[str]]:
    """(footprints found, problems) for one board, given the lexer port's tree of it:
    every (at x y angle) atom must be FormatAngle of what KiCad reads from it; a
    footprint's angle must lie in (-180, 180] and a pad's in [0, 360), neither written
    as zero. Text angles keep whatever range their writer gave them
    (multichannel_mixer-unrouted writes -90)."""
    root = top[0]
    problems = []
    for group in _walk([root]):
        if _head(group) != b"at" or len(group.items) < 4:
            continue
        token = group.items[3]
        if token.kind != L.NUMBER or not all(t.kind == L.NUMBER for t in group.items[1:3]):
            continue
        value = P.parse_double(token.value)
        if U.format_angle(value) != token.value.decode():
            problems.append(f"byte {token.start}: {token.value!r} is not FormatAngle's text")
    footprints = _children(root, b"footprint")
    for fp in footprints:
        at = _child(fp, b"at")
        if len(at.items) > 3:
            value = P.parse_double(at.items[3].value)
            if not (-180 < value <= 180 and value != 0):
                problems.append(f"footprint at byte {fp.start}: angle {value}")
        for pad in _children(fp, b"pad"):
            pat = _child(pad, b"at")
            if len(pat.items) > 3:
                value = P.parse_double(pat.items[3].value)
                if not (0 <= value < 360 and value != 0):
                    problems.append(f"pad at byte {pad.start}: angle {value}")
    return len(footprints), problems


@pytest.mark.parametrize("path", _per_file(_BOARDS))
def test_format_angle_and_angle_ranges_match_kicad_files(path):
    """Every angle atom in a demo or template board KiCad saved (files up to
    kicad_corpus.PER_PUSH_CAP) is FormatAngle's text for the angle KiCad reads, and
    footprint and pad angles lie in the ranges KiCad normalizes them to. The lexer must
    find every footprint: as many as the file holds "(footprint " tokens. A file the
    lexer port finds unbalanced is bug 54's (rung 1's corpus test asserts that policy)."""
    data = path.read_bytes()
    try:
        found, problems = _angle_problems(K.lexed(path))
    except L.Unbalanced:
        pytest.skip("the lexer port finds this file unbalanced: bug 54, rung 1's policy")
    assert found == data.count(b"(footprint "), "the lexer missed footprints"
    assert not problems, "\n".join(problems[:20])


@pytest.mark.nightly
@pytest.mark.parametrize("path", _nightly_files(".kicad_pcb"))
def test_nightly_format_angle_and_angle_ranges_match_kicad_files(path):
    """The same over every board the install holds."""
    data = path.read_bytes()
    try:
        found, problems = _angle_problems(L.tree(data, knows_bar=L.knows_bar_of(data)))
    except L.Unbalanced:
        pytest.skip("the lexer port finds this file unbalanced: bug 54, rung 1's policy")
    assert found == data.count(b"(footprint "), "the lexer missed footprints"
    assert not problems, "\n".join(problems[:20])


def _fill_problems(data: bytes, top: list) -> tuple[int, list[str]]:
    """(fills found, problems) for one board, given its bytes and the lexer port's tree
    of it: each filled_polygon's run of (xy lists, from its first line's indentation to
    its last ')', must be what Prettify writes for those lists at that depth. The
    Windows installer ships the demos with CRLF line ends (measured on 9.0.8,
    2026-10-11) where KiCad's writer ends lines with LF, so a CR before an LF is dropped
    before the comparison: the rule is about where lines break."""
    fills = 0
    problems = []
    for group in _walk(top):
        if _head(group) != b"filled_polygon":
            continue
        fills += 1
        points = [g for g in _child(group, b"pts").items if _head(g) == b"xy"]
        line_start = data.rindex(b"\n", 0, points[0].start) + 1
        indent = data[line_start : points[0].start]
        if set(indent) - {0x09}:
            problems.append(f"fill at byte {group.start}: indented with {indent!r}, not TABs")
            continue
        written = data[line_start : points[-1].end].replace(b"\r\n", b"\n")
        tokens = [data[g.start : g.end] for g in points]
        if IO.pack_xy(tokens, len(indent)) != written:
            problems.append(f"fill at byte {group.start}: packed unlike Prettify")
    return fills, problems


@pytest.mark.parametrize("path", _per_file(_BOARDS))
def test_xy_packing_port_reproduces_kicads_fills(path):
    """Every zone fill in a demo or template board KiCad saved (files up to
    kicad_corpus.PER_PUSH_CAP) is packed as pack_xy packs its own points. The lexer
    must find every fill: as many as the file holds "(filled_polygon" tokens."""
    data = path.read_bytes()
    try:
        found, problems = _fill_problems(data, K.lexed(path))
    except L.Unbalanced:
        pytest.skip("the lexer port finds this file unbalanced: bug 54, rung 1's policy")
    assert found == data.count(b"(filled_polygon"), "the lexer missed fills"
    assert not problems, "\n".join(problems[:20])


@pytest.mark.nightly
@pytest.mark.parametrize("path", _nightly_files(".kicad_pcb"))
def test_nightly_xy_packing_port_reproduces_kicads_fills(path):
    """The same over every board the install holds."""
    data = path.read_bytes()
    try:
        found, problems = _fill_problems(data, L.tree(data, knows_bar=L.knows_bar_of(data)))
    except L.Unbalanced:
        pytest.skip("the lexer port finds this file unbalanced: bug 54, rung 1's policy")
    assert found == data.count(b"(filled_polygon"), "the lexer missed fills"
    assert not problems, "\n".join(problems[:20])


def test_the_corpus_holds_what_the_judges_check():
    """Where the install ships demos, the per-push files above hold placed symbols,
    footprints and zone fills, so the per-file tests are not passing on empty files.
    Templates alone (the macOS cask) hold no fills, so this needs the demos."""
    if not K.share_dirs():
        pytest.skip(K.NO_INSTALL)
    if not any(name.startswith("demos/") for name, _ in _SCHEMATICS + _BOARDS):
        pytest.skip("the install ships no demos (the macOS cask ships templates only)")
    counts = {
        "placed symbols": sum(p.read_bytes().count(b"(lib_id ") for _, p in _SCHEMATICS),
        "footprints": sum(p.read_bytes().count(b"(footprint ") for _, p in _BOARDS),
        "zone fills": sum(p.read_bytes().count(b"(filled_polygon") for _, p in _BOARDS),
    }
    if not all(counts.values()):
        raise RuntimeError(f"the shipped demos hold none of something the judges check: {counts}")


# ---------------------------------------------------------------------------
# courtyards and board outlines, against pcbnew
# ---------------------------------------------------------------------------

_COURTYARDS_SCRIPT = r"""
import pcbnew
result = {}
for path in payload["boards"]:
    board = pcbnew.LoadBoard(path)
    for fp in board.GetFootprints():
        shapes = []
        for item in fp.GraphicalItems():
            if item.GetLayer() == pcbnew.F_CrtYd and item.GetClass() == "PCB_SHAPE":
                box = item.GetBoundingBox()
                shapes.append([box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom(),
                               item.GetWidth()])
        courtyard = fp.GetCourtyard(pcbnew.F_CrtYd)
        xs, ys = [], []
        for oi in range(courtyard.OutlineCount()):
            outline = courtyard.Outline(oi)
            for k in range(outline.PointCount()):
                xs.append(outline.CPoint(k).x)
                ys.append(outline.CPoint(k).y)
        result[fp.GetReference()] = {
            "shapes": shapes,
            "vertices": [min(xs), min(ys), max(xs), max(ys)] if xs else None,
        }
"""
#: How far pcbnew's box of a straight shape or a circle may sit from the exact one, in nm:
#: KiCad rounds each rotated point to the nanometre (RotatePoint's KiROUND) and a radius
#: when it boxes it (about 2.2 nm at worst; 1.5 measured over 400 generated courtyards,
#: 2026-10-11).
_SHAPE_BOX_TOLERANCE = 3
#: The same for a footprint with an arc. EDA_SHAPE::SetArcGeometry takes the centre from
#: CalcArcCenter (common/eda_shape.cpp:886-891 at 9.0.8, 1173-1178 at 10.0.7), which
#: moves it to a multiple of 100 nm or 10 nm whenever that lies within its own error
#: estimate (libs/kimath/src/trigo.cpp:488-516 at 9.0.8, 501-529 at 10.0.7): up to 50 nm
#: on each axis. computeArcBBox boxes centre +- radius (eda_shape.cpp:1548-1595 at
#: 9.0.8, 1894-1941 at 10.0.7), and the radius, measured from that centre, moves by up
#: to 71 nm with it; with the nanometre roundings that bounds the difference at 125 nm
#: (20.3 measured over 400 generated courtyards, 2026-10-11).
_ARC_BOX_TOLERANCE = 125


def _check_courtyards(
    cases: list[tuple[str, list, float, tuple[int, int]]], boards: list[Path]
) -> None:
    """For each (ref, shapes, angle, at): pcbnew's own boxes of the footprint's courtyard
    shapes on the board, less half their stroke width, bound exactly courtyard_box; and
    pcbnew's courtyard polygon (polygonized within 0.005 mm, then shrunk by 0.005 mm:
    footprint.cpp:3154-3165 at 9.0.8, 3699-3710 at 10.0.7) lies inside that box."""
    found = PJ.run(_COURTYARDS_SCRIPT, {"boards": [str(b) for b in boards]})
    wrong = []
    for ref, shapes, angle, at in cases:
        exact = G.courtyard_box(shapes, angle, at)
        boxes = found[ref]["shapes"]
        if not boxes:
            raise RuntimeError(f"pcbnew found no courtyard shapes on {ref}")
        union = (
            min(b[0] + b[4] // 2 for b in boxes),
            min(b[1] + b[4] // 2 for b in boxes),
            max(b[2] - b[4] // 2 for b in boxes),
            max(b[3] - b[4] // 2 for b in boxes),
        )
        has_arc = any(shape[0] == "fp_arc" for shape in shapes)
        tolerance = _ARC_BOX_TOLERANCE if has_arc else _SHAPE_BOX_TOLERANCE
        if max(abs(u - e) for u, e in zip(union, exact)) > tolerance:
            wrong.append(f"{ref} at {angle}: pcbnew's shape boxes {union}, judge {exact}")
        vertices = found[ref]["vertices"]
        if vertices is None:
            raise RuntimeError(f"pcbnew built no courtyard for {ref}, whose outline is closed")
        inset = (
            vertices[0] - exact[0],
            vertices[1] - exact[1],
            exact[2] - vertices[2],
            exact[3] - vertices[3],
        )
        if min(inset) < -1:
            wrong.append(f"{ref} at {angle}: courtyard vertices {vertices} outside the box {exact}")
    assert not wrong, f"pcbnew {PJ.major()}:\n" + "\n".join(wrong)


def _polar(radius_mm: float, degrees: float) -> tuple[int, int]:
    return (
        round(radius_mm * MM * math.cos(math.radians(degrees))),
        round(radius_mm * MM * math.sin(math.radians(degrees))),
    )


_CIRCLE = [("fp_circle", (MM, MM // 2), (MM + 5_250_000, MM // 2))]
_DIAMOND = [("fp_poly", [(0, -3 * MM), (3 * MM, 0), (0, 3 * MM), (-3 * MM, 0)])]
_HALF_CIRCLE = [
    ("fp_arc", (-5 * MM, 0), (0, -5 * MM), (5 * MM, 0)),
    ("fp_line", (5 * MM, 0), (-5 * MM, 0)),
]
#: A 90-degree arc of radius 50 mm from 47.8125 to 137.8125 degrees, closed by its chord:
#: its extreme at 90 degrees falls halfway between two of 16 equal steps.
_HALF_STEP_ARC = [
    ("fp_arc", _polar(50, 47.8125), _polar(50, 92.8125), _polar(50, 137.8125)),
    ("fp_line", _polar(50, 137.8125), _polar(50, 47.8125)),
]


def test_courtyard_judge_matches_pcbnew(tmp_path):
    """courtyard_box (geom_judge's exact extremes of RotatePoint's rotated shapes) is
    pcbnew's own box of each courtyard shape, within a few nanometres, and contains
    pcbnew's courtyard polygon: a circle at 0 and 45 degrees, a diamond at 45, a half
    circle closed by a line at 0 and 30, the half-step arc at 0, and the stock
    BatteryHolder_Keystone_500 at 0 where the stock libraries are installed.

    The polygon lies up to 0.0125 mm inside the box at the half-step arc's ends
    (measured 2026-10-11), not 0.01 mm: shrinking a polygon by 0.005 mm moves a 45-degree
    corner 0.005 / sin(22.5 degrees) = 0.013 mm along its bisector. So pcbnew's shape
    boxes, not its courtyard polygon, are what judges the box's size."""
    _pcbnew_or_skip()
    cases = [
        ("C0", _CIRCLE, 0.0),
        ("C45", _CIRCLE, 45.0),
        ("D45", _DIAMOND, 45.0),
        ("H0", _HALF_CIRCLE, 0.0),
        ("H30", _HALF_CIRCLE, 30.0),
        ("A0", _HALF_STEP_ARC, 0.0),
    ]
    battery = [
        share / "footprints" / "Battery.pretty" / "BatteryHolder_Keystone_500.kicad_mod"
        for share in K.share_dirs()
    ]
    battery = [path for path in battery if path.is_file()]
    if battery:
        footprint = L.tree(battery[0].read_bytes(), knows_bar=True)[0]
        cases.append(("B0", G.shapes_of(footprint, major=10), 0.0))
    placed = [
        (ref, shapes, angle, (50 * MM + 60 * MM * i, 50 * MM))
        for i, (ref, shapes, angle) in enumerate(cases)
    ]
    board = tmp_path / "courtyards.kicad_pcb"
    board.write_text(
        G.board_text(
            G.footprint_text(ref, at, angle, shapes=shapes) for ref, shapes, angle, at in placed
        ),
        encoding="utf-8",
        newline="\n",
    )
    _check_courtyards(placed, [board])


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(st.lists(st.tuples(G.courtyard_shapes(), G.angles()), min_size=1, max_size=6))
def test_nightly_courtyard_judge_matches_pcbnew(courtyards):
    """Generated closed courtyards at generated angles, judged as above."""
    _pcbnew_or_skip()
    placed = [
        (f"U{i + 1}", shapes, angle, (60 * MM * (i + 1), 60 * MM))
        for i, (shapes, angle) in enumerate(courtyards)
    ]
    with tempfile.TemporaryDirectory() as work:
        board = Path(work) / "courtyards.kicad_pcb"
        board.write_text(
            G.board_text(
                G.footprint_text(ref, at, angle, shapes=shapes) for ref, shapes, angle, at in placed
            ),
            encoding="utf-8",
            newline="\n",
        )
        _check_courtyards(placed, [board])


_OUTLINE_SCRIPT = r"""
import pcbnew
result = {}
for name, path, points in payload["boards"]:
    board = pcbnew.LoadBoard(path)
    outline = pcbnew.SHAPE_POLY_SET()
    try:
        ok = board.GetBoardPolygonOutlines(outline)  # 9.x
    except TypeError:
        ok = board.GetBoardPolygonOutlines(outline, False)  # 10.x: aInferOutlineIfNecessary
    result[name] = {
        "ok": bool(ok),
        "contains": [bool(outline.Contains(pcbnew.VECTOR2I(x, y))) for x, y in points],
    }
"""


def _check_outlines(
    cases: dict[str, tuple[list[str], list, list[tuple[int, int]]]], work: Path
) -> None:
    """Each case is (Edge.Cuts items, contours, points): pcbnew's
    GetBoardPolygonOutlines().Contains says what geom_judge.parity says, at points at
    least 0.01 mm from every edge (twice KiCad's 0.005 mm polygonization error)."""
    jobs = []
    for name, (items, contours, points) in cases.items():
        for p in points:
            if not all(GJ.clear_of(p, c, G.QUERY_CLEARANCE) for c in contours):
                raise RuntimeError(f"{name}: {p} is within 0.01 mm of an edge")
        board = work / f"{name}.kicad_pcb"
        board.write_text(G.board_text(items), encoding="utf-8", newline="\n")
        jobs.append([name, str(board), [list(p) for p in points]])
    found = PJ.run(_OUTLINE_SCRIPT, {"boards": jobs})
    wrong = []
    for name, (_, contours, points) in cases.items():
        if not found[name]["ok"]:
            raise RuntimeError(f"pcbnew could not build the {name} outline")
        judge = [GJ.parity(p, contours) for p in points]
        if found[name]["contains"] != judge:
            wrong.append(f"{name}: pcbnew {found[name]['contains']}, parity {judge} at {points}")
    assert not wrong, f"pcbnew {PJ.major()}:\n" + "\n".join(wrong)


def _edge(shape) -> str:
    return G.shape_text(shape, layer="Edge.Cuts", depth=1, stroke_type="default")


def _square(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _loop(points) -> list[str]:
    return [
        _edge(("gr_line", points[i], points[(i + 1) % len(points)])) for i in range(len(points))
    ]


def test_outline_judge_matches_pcbnew(tmp_path):
    """parity over the drawn contours equals pcbnew's board outline at listed points: a
    cutout listed before its outline; a gr_rect outline with a gr_circle and a gr_poly
    cutout; a square whose corners miss by 5 um (KiCad chains ends within 0.01 mm,
    DEFAULT_CHAINING_EPSILON_MM, pcbnew/board.h:85 at 9.0.8, 92 at 10.0.7); a circle
    drawn as four 50 mm quarter arcs."""
    _pcbnew_or_skip()
    outer = _square(0, 0, 100 * MM, 100 * MM)
    hole = _square(40 * MM, 40 * MM, 60 * MM, 60 * MM)
    triangle = [(10 * MM, 70 * MM), (30 * MM, 70 * MM), (10 * MM, 90 * MM)]
    gap = 5_000
    centre, r = (100 * MM, 100 * MM), 50 * MM
    ends = [
        (centre[0] + r, centre[1]),
        (centre[0], centre[1] + r),
        (centre[0] - r, centre[1]),
        (centre[0], centre[1] - r),
    ]
    mids = [
        (
            centre[0] + round(r * math.cos(math.radians(45 + 90 * i))),
            centre[1] + round(r * math.sin(math.radians(45 + 90 * i))),
        )
        for i in range(4)
    ]

    def at(x_mm: float, y_mm: float) -> tuple[int, int]:
        return round(x_mm * MM), round(y_mm * MM)

    cases = {
        "cutout_first": (
            _loop(hole) + _loop(outer),
            [outer, hole],
            [at(10, 10), at(50, 50), at(99, 99), at(150, 150), at(45, 55)],
        ),
        "primitives": (
            [
                _edge(("gr_rect", (0, 0), (100 * MM, 100 * MM))),
                _edge(("gr_circle", (50 * MM, 50 * MM), (60 * MM, 50 * MM))),
                _edge(("gr_poly", triangle)),
            ],
            [outer, GJ.Circle((50 * MM, 50 * MM), 10 * MM), triangle],
            # (26, 86) lies in the triangle's box but outside the triangle: on the board.
            [at(5, 5), at(50, 50), at(14, 74), at(26, 86), at(80, 20), at(150, 50), at(59.98, 50)],
        ),
        "corner_gaps": (
            [
                _edge(("gr_line", (0, 0), (100 * MM, 0))),
                _edge(("gr_line", (100 * MM + gap, 0), (100 * MM, 100 * MM))),
                _edge(("gr_line", (100 * MM, 100 * MM + gap), (0, 100 * MM))),
                _edge(("gr_line", (0, 100 * MM), (0, gap))),
            ],
            [outer],
            [at(50, 50), at(150, 50), at(1, 1), at(99.98, 99.98)],
        ),
        "quarter_arcs": (
            [_edge(("gr_arc", ends[i], mids[i], ends[(i + 1) % 4])) for i in range(4)],
            [GJ.Circle(centre, r)],
            [at(100, 100), at(149.98, 100), at(100, 50.02), at(151, 100), at(135.34, 135.34)],
        ),
    }
    _check_outlines(cases, tmp_path)


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(st.lists(G.board_outlines(), min_size=1, max_size=4))
def test_nightly_outline_judge_matches_pcbnew(outlines):
    """Generated outlines with cutouts, every primitive kind, shuffled file order."""
    _pcbnew_or_skip()
    with tempfile.TemporaryDirectory() as work:
        cases = {f"o{i}": (o.items, o.contours, o.points) for i, o in enumerate(outlines)}
        _check_outlines(cases, Path(work))


# ---------------------------------------------------------------------------
# geom_judge, against known answers and a second algorithm
# ---------------------------------------------------------------------------


def test_geom_judge_on_constructed_cases():
    """Answers known by construction: winding numbers and boundaries of squares, a
    concave L, a pentagram (winding 2 at its centre) and a polygon with collinear runs;
    orientation signs; segment distances; the extremes of quarter, half and
    three-quarter arcs in both directions; parity through nested contours."""
    square = [(0, 0), (4, 0), (4, 4), (0, 4)]
    assert GJ.winding_number((1, 1), square) == 1
    assert GJ.winding_number((1, 1), square[::-1]) == -1
    assert GJ.inside((1, 1), square) and not GJ.inside((5, 1), square)
    assert GJ.on_boundary((2, 4), square) and GJ.on_boundary((4, 4), square)
    assert not GJ.on_boundary((2, 3), square)
    with pytest.raises(ValueError):
        GJ.winding_number((0, 2), square)
    ell = [(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]
    assert GJ.inside((1, 5), ell) and GJ.inside((5, 1), ell) and not GJ.inside((4, 4), ell)
    star = [(0, 10), (6, -8), (-10, 3), (10, 3), (-6, -8)]  # a pentagram
    assert GJ.winding_number((0, 0), star) in (2, -2) and GJ.inside((0, 0), star)
    runs = [(0, 0), (2, 0), (4, 0), (4, 2), (4, 4), (0, 4)]  # collinear vertices
    assert GJ.inside((1, 1), runs) and GJ.on_boundary((3, 0), runs) and GJ.on_boundary((2, 0), runs)
    assert GJ.orient((0, 0), (1, 0), (0, 1)) == 1
    assert GJ.orient((0, 0), (0, 1), (1, 0)) == -1
    assert GJ.orient((0, 0), (2, 2), (5, 5)) == 0
    assert GJ.orient((0.5, 0), (1.5, 0), (1, 1e-300)) == 1  # exact on floats
    assert GJ.on_segment((1, 1), (0, 0), (2, 2)) and not GJ.on_segment((3, 3), (0, 0), (2, 2))
    assert GJ.segment_distance2((0, 1), (0, 0), (2, 0)) == 1
    assert GJ.segment_distance2((3, 0), (0, 0), (2, 0)) == 1
    assert GJ.segment_distance2((0, 1), (0, 0), (1, 1)) == Fraction(1, 2)
    assert GJ.segment_distance2((5, 5), (1, 1), (1, 1)) == 32
    assert GJ.segments_distance2((0, 0), (2, 2), (0, 2), (2, 0)) == 0  # crossing
    assert GJ.segments_distance2((0, 0), (2, 0), (0, 1), (2, 1)) == 1  # parallel
    assert GJ.segments_distance2((0, 0), (2, 0), (1, 0), (1, 3)) == 0  # a T
    assert GJ.segments_distance2((0, 0), (1, 0), (3, 0), (4, 0)) == 4  # collinear, apart
    assert GJ.circle_through((5, 0), (0, 5), (-5, 0)) == (0, 0, 25)
    with pytest.raises(ValueError):
        GJ.circle_through((0, 0), (1, 1), (2, 2))
    # Arcs on the circle of radius 5 through the lattice points (5, 0), (4, 3), (3, 4) ...
    assert GJ.arc_bbox((5, 0), (4, 3), (0, 5)) == (0, 0, 5, 5)  # quarter, counterclockwise
    assert GJ.arc_bbox((0, 5), (4, 3), (5, 0)) == (0, 0, 5, 5)  # the same, clockwise
    assert GJ.arc_bbox((5, 0), (0, 5), (-5, 0)) == (-5, 0, 5, 5)  # half
    assert GJ.arc_bbox((5, 0), (0, -5), (-5, 0)) == (-5, -5, 5, 0)  # the other half
    assert GJ.arc_bbox((5, 0), (-4, 3), (0, -5)) == (-5, -5, 5, 5)  # three quarters
    assert GJ.arc_bbox((5, 0), (-4, -3), (0, 5)) == (-5, -5, 5, 5)  # three quarters, cw
    assert GJ.arc_bbox((24, 7), (20, 15), (15, 20)) == (15, 7, 24, 20)  # no extreme inside
    outer, hole, island = _square(0, 0, 10, 10), _square(2, 2, 8, 8), _square(4, 4, 6, 6)
    contours = [island, outer, hole]  # any file order
    assert GJ.parity((5, 5), contours) and GJ.parity((1, 1), contours)
    assert not GJ.parity((3, 3), contours) and not GJ.parity((11, 11), contours)
    circle = GJ.Circle((0, 0), 5)
    assert GJ.contour_contains((3, 3), circle) and not GJ.contour_contains((4, 4), circle)
    with pytest.raises(ValueError):
        GJ.contour_contains((3, 4), circle)
    assert GJ.clear_of((5, 1), outer, 1) and not GJ.clear_of((5, 1), outer, 2)
    assert GJ.clear_of((0, 0), circle, 5) and not GJ.clear_of((0, 1), circle, 5)
    assert GJ.clear_of((0, 7), circle, 2) and not GJ.clear_of((0, 6), circle, 2)
    assert GJ.circle_distance((0, 7), (0, 0), 5) == 2.0


def _sign_q2(a: int, b: int) -> int:
    """The sign of a + b * sqrt(2), exactly, for integers a and b."""
    if a >= 0 and b >= 0:
        return 1 if a or b else 0
    if a <= 0 and b <= 0:
        return -1
    bigger = (a * a > 2 * b * b) - (a * a < 2 * b * b)  # opposite signs: compare squares
    return bigger if a > 0 else -bigger


def _inside_by_ray(p, poly) -> bool:
    """Even-odd count of the edges crossed by the ray from p along (1, sqrt(2)), decided
    in exact integer arithmetic on numbers a + b * sqrt(2). For integer points the
    slope never meets a vertex, so every crossing is clean. With e = b - a and
    d = (1, sqrt(2)): the ray meets the edge's line at t = ((a - p) x e) / (d x e) and
    the edge at u = ((p - a) x d) / (e x d), where e x d = ex sqrt(2) - ey; a crossing
    needs t > 0 and 0 < u < 1."""
    px, py = p
    crossings = 0
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % len(poly)]
        ex, ey = bx - ax, by - ay
        den = (-ey, ex)  # e x d
        sd = _sign_q2(*den)
        if sd == 0:
            continue
        t_num = (ax - px) * ey - (ay - py) * ex  # (a - p) x e, over d x e = -(e x d)
        if (t_num > 0) - (t_num < 0) != -sd:
            continue
        u_num = (-(py - ay), px - ax)  # (p - a) x d
        if _sign_q2(*u_num) == sd and _sign_q2(u_num[0] - den[0], u_num[1] - den[1]) == -sd:
            crossings += 1
    return crossings % 2 == 1


@given(
    G.simple_polygons(),
    st.lists(st.tuples(st.integers(-2500, 2500), st.integers(-2500, 2500)), max_size=8),
)
def test_geom_judge_inside_agrees_with_an_independent_count(poly, extra):
    """On generated simple polygons, inside() (a winding number from orient on a
    horizontal ray) equals an exact crossing count along a ray of irrational slope, at
    the polygon's vertices nudged by one and at other lattice points off the boundary."""
    probes = [(x + dx, y + dy) for x, y in poly for dx, dy in ((1, 0), (0, 1), (-1, -1))] + extra
    for p in probes:
        if GJ.on_boundary(p, poly):
            continue
        assert GJ.inside(p, poly) == _inside_by_ray(p, poly), (p, poly)


# ---------------------------------------------------------------------------
# the ports, against KiCad's source and math
# ---------------------------------------------------------------------------


@given(
    st.integers(-1_000_000_000, 1_000_000_000),
    st.integers(-1_000_000_000, 1_000_000_000),
    G.angles(),
)
@example(1_000_000, 0, 45.0)
@example(-7, 3, 359.9)
def test_rotate_point_port_laws(x, y, angle):
    """Quarter turns are exact and four of them are the identity; a rotation by t then
    -t comes back within one nanometre on each axis and keeps the length within one
    (each rotation rounds once, so the error is at most 0.5 + 0.71 nm per axis); the
    normalizers are idempotent, stay in range, and agree with mod 360 on whole degrees."""
    major = 10 if x % 2 else 9
    for quarter, want in (
        (90, (y, -x)),
        (180, (-x, -y)),
        (270, (-y, x)),
        (-90, (-y, x)),
        (450, (y, -x)),
    ):
        assert trigo.rotate_point(x, y, quarter, major=major) == want
    p = (x, y)
    for _ in range(4):
        p = trigo.rotate_point(*p, 90.0, major=major)
    assert p == (x, y)
    rx, ry = trigo.rotate_point(x, y, angle, major=major)
    bx, by = trigo.rotate_point(rx, ry, -angle, major=major)
    assert abs(bx - x) <= 1 and abs(by - y) <= 1, ((x, y), angle, (bx, by))
    assert abs(math.hypot(rx, ry) - math.hypot(x, y)) <= 1.0
    # KiROUND of the very doubles rotate_point_double computes; 9.0.8 adds 0.5 in double
    # arithmetic, which can round up by an ulp at a half, hence the margin.
    dx, dy = trigo.rotate_point_double(x, y, angle)
    assert abs(rx - dx) <= 0.5 + 1e-9 and abs(ry - dy) <= 0.5 + 1e-9
    n, n180 = trigo.normalize(angle), trigo.normalize180(angle)
    assert 0 <= n < 360 and -180 < n180 <= 180
    assert trigo.normalize(n) == n and trigo.normalize180(n180) == n180
    whole = round(angle)
    assert (trigo.normalize(whole) - whole) % 360 == 0 and trigo.normalize(whole) == whole % 360
    assert (trigo.normalize180(whole) - whole) % 360 == 0


def test_trigo_port_follows_eda_angle():
    """Cases traced by hand through eda_angle.h: Normalize keeps -0.0 (it is not below
    -0.0) and sends a tiny negative angle to 0 by way of 360; the exact branches of Sin
    and Cos; the refusals that guard the loops."""
    assert math.copysign(1.0, trigo.normalize(-0.0)) == -1.0
    assert trigo.normalize(-1e-20) == 0.0
    assert trigo.normalize(720.0) == 0.0 and trigo.normalize(-90.0) == 270.0
    assert trigo.normalize180(180.0) == 180.0 and trigo.normalize180(-180.0) == 180.0
    assert trigo.normalize180(359.9) == 359.9 - 360.0
    for degrees, s, c in ((0, 0.0, 1.0), (90, 1.0, 0.0), (180, 0.0, -1.0), (270, -1.0, 0.0)):
        assert (trigo.sin_deg(degrees), trigo.cos_deg(degrees)) == (s, c)
    root = trigo.M_SQRT1_2
    assert (trigo.sin_deg(45), trigo.cos_deg(45), trigo.sin_deg(-45)) == (root, root, -root)
    assert (trigo.sin_deg(135), trigo.cos_deg(135)) == (root, -root)
    # Off the special angles Sin uses the angle's own value, not the normalized copy.
    assert trigo.sin_deg(540.5) == math.sin(540.5 * (math.pi / 180.0))
    assert trigo.rotate_point(1_000_000, 0, 45.0, major=9) == (707_107, -707_107)
    assert trigo.rotate_point_about(110, 100, 100, 100, 90, major=9) == (100, 90)
    for bad in (float("inf"), float("nan"), 1e9, -1e9):
        with pytest.raises(ValueError):
            trigo.normalize(bad)
    with pytest.raises(ValueError):
        trigo.rotate_point(-(2**31), 0, 90, major=9)  # its negation overflows int


@given(
    st.tuples(G.nm_coordinates(1_000_000_000), G.nm_coordinates(1_000_000_000)),
    G.angles(),
    st.tuples(G.nm_coordinates(100 * MM), G.nm_coordinates(100 * MM)),
)
def test_board_item_port_inverts_itself(fp, angle, local):
    """GetFPRelativePosition undoes SetFPRelativePosition to within the nanometre each
    rotation rounds, exactly on quarter turns."""
    stored = trigo.normalize180(angle)
    board = B.fp_relative_to_board(fp, stored, local, major=10)
    back = B.board_to_fp_relative(fp, stored, board, major=10)
    tolerance = 0 if trigo.normalize(stored) in (0.0, 90.0, 180.0, 270.0) else 1
    assert abs(back[0] - local[0]) <= tolerance and abs(back[1] - local[1]) <= tolerance


def test_parse_units_ports_follow_the_source():
    """Cases traced by hand through parseBoardUnits, parseInternalUnits, parseDouble,
    the symbol (at) rule and parseInt. kicad-cli's own verdicts on the pin angles, the
    underflow and both clamps are the kicad-cli tests above."""
    for major in (9, 10):
        assert P.parse_board_units("2147.483637", major=major) == 2_147_483_637
        assert P.parse_board_units("2147.483638", major=major) == 2_147_483_637  # clamped
        assert P.parse_board_units("3000", major=major) == 2_147_483_637
        assert P.parse_board_units("-3000", major=major) == -2_147_483_637
        assert P.parse_board_units("-2147.483637", major=major) == -2_147_483_637
        assert P.parse_board_units("0.0000005", major=major) == 1  # half away from zero
        assert P.parse_board_units("-0.0000005", major=major) == -1
        assert P.parse_sch_internal_units("151848.5686", major=major) == 1_518_485_686
        assert P.parse_sch_internal_units("151848.5687", major=major) == 1_518_485_687
        assert P.parse_sch_internal_units("200000", major=major) == 1_518_485_687  # clamped
        assert P.parse_sch_internal_units("-200000", major=major) == -1_518_485_687
        assert P.parse_sch_internal_units("100.635", major=major) == 1_006_350
    assert P.PCB_INT_LIMIT == 2_147_483_637.0
    assert P.SCH_INT_LIMIT == 2_147_483_647 * 0.7071
    assert P.parse_double("90.5") == 90.5 and P.parse_double("1.e5") == 100_000.0
    assert P.parse_double(".5") == 0.5 and math.copysign(1.0, P.parse_double("-0")) == -1.0
    assert P.parse_double("0e-400") == 0.0  # a zero literal is no underflow
    for refused in ("+1", "+90", "inf", "nan", "1e400", "-1e400", "1e-400", "abc", "1e", ".", ""):
        with pytest.raises(P.ParseRefusal):
            P.parse_double(refused)
    for angle, rotation in (("90.5", 90), ("-0.5", 0), ("270.9", 270), ("0.9e2", 90), ("180", 180)):
        assert T.parse_symbol_transform([("at", angle)]) == T.PARSER_ROTATIONS[rotation]
    for angle in ("-90", "360", "+90", "45", "1e400", None):
        with pytest.raises(P.ParseRefusal):
            T.parse_symbol_transform([("at", angle)])
    for axis in ("X", "xy", "z", None):
        with pytest.raises(P.ParseRefusal):
            T.parse_symbol_transform([("at", "0"), ("mirror", axis)])
    quoted = L.tokens(b'(mirror "x")')[2]
    with pytest.raises(P.ParseRefusal):
        T.parse_symbol_transform([("at", "0"), ("mirror", quoted)])
    for token, orientation in (
        ("0", "right"),
        ("90", "up"),
        ("180", "left"),
        ("270", "down"),
        ("0.9e2", "right"),  # strtol stops at the point
        ("+90", "up"),  # strtol takes a sign
        ("-0", "right"),
        (".5", "right"),  # no digits before the point: 0
        ("270.9", "down"),
    ):
        assert T.pin_orientation(token) == orientation, token
    for token in ("-90", "45", "abc", "4294967386"):
        with pytest.raises(P.ParseRefusal):
            T.pin_orientation(token)


@given(
    st.from_regex(
        rb"[-+]?(?:[0-9]{1,8}(?:\.[0-9]{0,8})?|\.[0-9]{1,8})(?:[eE][-+]?[0-9]{1,2})?",
        fullmatch=True,
    )
)
def test_parse_double_reads_numbers_as_a_correctly_rounded_parse(token):
    """A NUMBER token inside the range, without a leading '+', reads as the correctly
    rounded value of its decimal text, which Python's float() also computes (from_chars
    and fast_float both round correctly); with a leading '+' it is refused."""
    assert L.is_number(token)
    if token.startswith(b"+"):
        with pytest.raises(P.ParseRefusal):
            P.parse_double(token)
    else:
        assert P.parse_double(token) == float(token)


def test_format_angle_follows_eda_units():
    """eda_units.cpp:162-167 at 9.0.8: {:.10g} of the degrees."""
    assert U.format_angle(90.0) == "90"
    assert U.format_angle(0.30000000000000004) == "0.3"
    assert U.format_angle(1e-05) == "1e-05"
    assert U.format_angle(-90.0) == "-90"
    assert U.format_angle(37.5) == "37.5"
    assert U.format_angle(359.9 - 360.0) == "-0.1"
    assert U.format_angle(12.3456789) == "12.3456789"
    assert U.format_angle(0.0001) == "0.0001"


def test_pack_xy_follows_prettify():
    """Runs of (xy that reach column 99 from either side, traced by hand through
    Prettify's list-of-points branch (kicad_io_utils.cpp:219-224 at 9.0.8): a point
    follows on the line while the line is shorter than 99 bytes, TABs counting one."""
    token = b"(xy 100.123456 -200.654321)"  # 27 bytes
    assert len(token) == 27
    # depth 4: 4 + 27 = 31, + 28 = 59, + 28 = 87, + 28 = 115 > 99: the fifth starts a line
    four = b"\t\t\t\t" + b" ".join([token] * 4)
    assert IO.pack_xy([token] * 6, 4) == four + b"\n\t\t\t\t" + token + b" " + token
    # A line of exactly 98 bytes takes one more point and so crosses 99 from below; one
    # of exactly 99 does not, and neither does one past it.
    short = b"(xy 1 2)"  # 8 bytes
    t98 = b"(xy " + b"9" * 91 + b")"  # 96 bytes: with two TABs the line is 98
    assert IO.pack_xy([t98, short], 2) == b"\t\t" + t98 + b" " + short
    t99 = b"(xy " + b"9" * 92 + b")"  # 97 bytes: with two TABs the line is 99
    assert IO.pack_xy([t99, short], 2) == b"\t\t" + t99 + b"\n\t\t" + short
    # Several short points up to and over the limit: 2 + 8 = 10, then 9 a point.
    run = IO.pack_xy([short] * 12, 2)
    first, second = run.split(b"\n")
    assert first == b"\t\t" + b" ".join([short] * 11) and len(first) == 2 + 11 * 8 + 10
    assert second == b"\t\t" + short
    assert IO.pack_xy([short], 0) == short
    with pytest.raises(ValueError):
        IO.pack_xy([], 1)


# ---------------------------------------------------------------------------
# geom_gen, against what it claims and what KiCad loads
# ---------------------------------------------------------------------------


def _simple(poly) -> bool:
    """No two edges meet except neighbours at their shared vertex, and neighbours do not
    fold back over each other."""
    n = len(poly)
    edges = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            (a, b), (c, d) = edges[i], edges[j]
            if j == i + 1:  # b == c
                if GJ.on_segment(a, c, d) or GJ.on_segment(d, a, b):
                    return False
            elif i == 0 and j == n - 1:  # d == a
                if GJ.on_segment(b, c, d) or GJ.on_segment(c, a, b):
                    return False
            elif GJ.segments_intersect(a, b, c, d):
                return False
    return True


@given(G.simple_polygons(), G.convex_polygons(), G.split_convex_polygons())
def test_geom_gen_polygons_are_what_they_claim(simple, convex, split):
    """simple_polygons are simple, with distinct vertices; convex_polygons turn the same
    way at every vertex; split_convex_polygons cut along a chord into two convex parts
    that share exactly that chord and between them use every vertex."""
    assert len(set(simple)) == len(simple) and _simple(simple)
    turns = {
        GJ.orient(convex[i], convex[(i + 1) % len(convex)], convex[(i + 2) % len(convex)])
        for i in range(len(convex))
    }
    assert turns == {1}
    whole, part_a, part_b = split
    for part in (part_a, part_b):
        assert len(part) >= 3
        assert {
            GJ.orient(part[i], part[(i + 1) % len(part)], part[(i + 2) % len(part)])
            for i in range(len(part))
        } == {1}
    assert set(part_a) | set(part_b) == set(whole)
    assert set(part_a) & set(part_b) == {part_a[0], part_a[-1]}


def test_geom_gen_builders_write_files_kicad_loads(tmp_path):
    """A board with footprints (pads at angles, courtyards of every kind, a keepout in
    board coordinates) and an outline with cutouts, a footprint library, and the R1
    schematic in a mirrored, rotated, off-grid state: the autouse oracle has kicad-cli
    load each one, and the lexer port reads back the values they were built from."""
    angles = (-90.0, 0.0, 37.5, 270.0)
    shapes = [
        ("fp_line", (-MM, -MM), (MM, -MM)),
        ("fp_rect", (-2 * MM, -2 * MM), (2 * MM, 2 * MM)),
        ("fp_poly", [(0, 0), (MM, 0), (0, MM)]),
        ("fp_circle", (0, 0), (MM, 0)),
        ("fp_arc", (-MM, 0), (0, -MM), (MM, 0)),
    ]
    items = [
        G.footprint_text(
            f"U{i + 1}",
            (30 * MM * (i + 1), 40 * MM),
            angle,
            [("1", (-MM, 0), 0.0), ("2", (MM, 0), 45.0)],
            shapes,
            [_square(30 * MM * (i + 1) - 3 * MM, 37 * MM, 30 * MM * (i + 1) + 3 * MM, 43 * MM)]
            if i == 0
            else (),
        )
        for i, angle in enumerate(angles)
    ]
    items += [
        _edge(("gr_rect", (0, 0), (160 * MM, 80 * MM))),
        _edge(("gr_circle", (20 * MM, 70 * MM), (22 * MM, 70 * MM))),
    ]
    board = tmp_path / "builders.kicad_pcb"
    board.write_text(G.board_text(items), encoding="utf-8", newline="\n")
    written = _footprints_in(board)
    for i, angle in enumerate(angles):
        fp = written[f"U{i + 1}"]
        assert fp["at"] == (30 * MM * (i + 1), 40 * MM)
        assert fp["orientation"] == trigo.normalize180(angle)
        assert fp["pads"] == {"1": (-MM, 0), "2": (MM, 0)}
    root = _root(board.read_bytes())
    # KiCad writes a footprint angle through Normalize180 and leaves out a zero one.
    fp_angles = [
        at.items[3].value.decode() if len(at.items) > 3 else None
        for at in (_child(fp, b"at") for fp in _children(root, b"footprint"))
    ]
    assert fp_angles == ["-90", None, "37.5", "-90"]
    pad_angles = [
        _child(pad, b"at").items[3].value.decode() if len(_child(pad, b"at").items) > 3 else "0"
        for fp in _children(root, b"footprint")
        for pad in _children(fp, b"pad")
    ]
    assert pad_angles == ["270", "315", "0", "45", "37.5", "82.5", "270", "315"]
    fp0 = _children(root, b"footprint")[0]
    assert sorted(G.shapes_of(fp0), key=str) == sorted(shapes, key=str)
    G.pretty_library(tmp_path, "Two", [("1", (-MM, 0), 0.0), ("2", (MM, 500_000), 270.0)])
    mod = (tmp_path / "Ladder.pretty" / "Two.kicad_mod").read_bytes()
    lib_pads = [_child(p, b"at").items for p in _children(_root(mod), b"pad")]
    assert [[t.value for t in at[1:]] for at in lib_pads] == [[b"-1", b"0"], [b"1", b"0.5", b"270"]]
    sch = G.schematic_with_r1(
        (1_006_350, 1_000_123), (90, "y"), ((0, 38_100, 270), (0, -38_100, 90))
    )
    (tmp_path / "r1.kicad_sch").write_bytes(sch)
    r1 = next(s for s in _placed_symbols(_root(sch)) if _property(s, b"Reference") == "R1")
    assert [t.value for t in _child(r1, b"at").items[1:]] == [b"100.635", b"100.0123", b"90"]
    assert T.parse_symbol_transform(_symbol_children(r1)) == _state_matrix(90, "y")
