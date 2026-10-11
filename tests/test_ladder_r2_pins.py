"""Rung 2: where the schematic tools say a pin is, judged by KiCad's own transform.

The code under test is schematic._get_pin_pos_cst (the pin read behind connect_pins,
no_connect_pin and remove_no_connect), the read tool get_pin_positions, and the
arithmetic they share in _connectivity (symbol_transform, pin_end, pin_point_mm).

Judges, none of which is that code:

- the transform port (tests/kicad_ref/transform.py, which test_ladder_r2_judges.py
  checks against the pin ends kicad-cli delivered, every placed symbol in KiCad's demos
  and kicad-cli's netlist), applied to the file's own tokens as the lexer port reads
  them;
- KiCad's reading of a printed number (tests/kicad_ref/parse_units.py): a reported
  coordinate is right when KiCad reads its text as the exact pin end, whatever its
  spelling, so these tests do not constrain how a fix formats numbers;
- kicad-cli's netlist, in which a label joins a pin only at the pin's exact end;
- the demo schematics KiCad ships.

Where KiCad draws a pin: GetPinPhysicalPosition (eeschema/sch_symbol.cpp:2327-2333 at
9.0.8, 2983-2989 at 10.0.7) adds the transformed library position to the symbol's, the
library position being parseXY( true ), Y negated (transform.pin_position does both). A
placed symbol draws the lib_symbols entry its lib_name names, else its lib_id
(SCH_SYMBOL::GetSchSymbolLibraryName, sch_symbol.cpp:242-248 at 9.0.8, 246-252 at
10.0.7), every one of those names read with {slash} as '/'
(eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp:332-358 and 3013-3048 at
9.0.8, 339-365 and 3130-3165 at 10.0.7). Of that entry it draws the pins whose unit is
its own or 0 and whose body style is its own or 0 (LIB_SYMBOL::GetPins,
eeschema/lib_symbol.cpp:880-909 at 9.0.8; GetGraphicalPins, 1140-1173 at 10.0.7), a pin
taking both from its sub-symbol's name, <item name>_<unit>_<body style> (parser 433-481
and 1570-1571 at 9.0.8, 485-533 and 1644-1645 at 10.0.7). A placed symbol's unit is its
(unit N), else 1, and its body style its (convert N) at 9.0.8, (convert N) or
(body_style N) at 10.0.7, else 1 (parser 3100-3110 at 9.0.8, 3217-3226 at 10.0.7).
GetPins keeps the pins in its own sorted order, not the file's, so pins are compared
as multisets of (number, x, y).

tests/test_read_tools.py and tests/test_cst.py judge the pin reads they check with the
helpers below.
"""

from __future__ import annotations

import math
import re
import shutil
import tempfile
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import geom_gen as G
import kicad_corpus as K
import pytest
import routing_fixtures as RF
import sexpr_gen
from conftest import netlist_nodes, requires_cli
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import parse_units as P
from kicad_ref import transform as T

from mcp_server_kicad import _cst, schematic

#: The KiCad majors whose readings of a coordinate a pure test requires to agree.
MAJORS = (9, 10)

BUG_56 = (
    "bug 56: get_pin_positions prints pin ends rounded to 2 decimals, so an end off the"
    " 0.01 mm grid is reported where the pin is not; fixed by step 12b"
)


# ---------------------------------------------------------------------------
# reading a schematic with the lexer port
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


def _text(token) -> str:
    return token.value.decode("utf-8", "surrogateescape")


def lexed_root(data: bytes):
    """The top-level list of a schematic, as the lexer port reads it."""
    return L.tree(data, knows_bar=L.knows_bar_of(data))[0]


def read_iu(token) -> int:
    """KiCad's reading of a schematic coordinate (parseInternalUnits), a lexer Token or
    text, in internal units of 100 nm. Both majors read every value these tests produce
    alike (KiROUND differs only at its clamp and at a few halfway doubles); a value they
    would read differently is outside the tests' domain and raises RuntimeError."""
    raw = token.value if isinstance(token, L.Token) else token
    readings = {P.parse_sch_internal_units(raw, major=major) for major in MAJORS}
    if len(readings) != 1:
        raise RuntimeError(f"KiCad 9 and 10 read {raw!r} differently: {sorted(readings)}")
    return readings.pop()


def report_iu(text: str) -> int:
    """KiCad's reading of a coordinate a tool printed; AssertionError for text KiCad would
    not read as a number, since the report is then wrong whatever it meant."""
    try:
        return read_iu(text)
    except P.ParseRefusal as exc:
        raise AssertionError(f"the report prints {text!r}, which KiCad reads as no number") from exc


def report_double(text: str) -> float:
    """KiCad's reading of an angle a tool printed (parseDouble); AssertionError as above."""
    try:
        return P.parse_double(text)
    except P.ParseRefusal as exc:
        raise AssertionError(f"the report prints {text!r}, which KiCad reads as no number") from exc


def placed_symbols(root) -> list:
    """The placed symbols of a schematic: its top-level (symbol ...) lists with a lib_id."""
    return [s for s in _children(root, b"symbol") if _find(s, b"lib_id") is not None]


def reference_property(symbol) -> str | None:
    for prop in _children(symbol, b"property"):
        if len(prop.items) > 2 and prop.items[1].value == b"Reference":
            return _text(prop.items[2])
    return None


def instance_entries(symbol) -> list[tuple[str | None, int]]:
    """(reference, unit) of every path in the symbol's (instances ...), as the parser
    reads them (sch_io_kicad_sexpr_parser.cpp:3188-3257 at 9.0.8, 3319-3497 at 10.0.7);
    a path without a unit is unit 1, SCH_SYMBOL_INSTANCE's default
    (eeschema/sch_sheet_path.h:49 at 9.0.8, 114 at 10.0.7)."""
    out = []
    instances = _find(symbol, b"instances")
    for project in _children(instances, b"project") if instances is not None else ():
        for path in _children(project, b"path"):
            ref, unit = _find(path, b"reference"), _find(path, b"unit")
            out.append(
                (_text(ref.items[1]) if ref else None, int(unit.items[1].value) if unit else 1)
            )
    return out


def _name(token) -> str:
    """A library symbol name as KiCad's parser keeps it: {slash} read as '/'."""
    return _text(token).replace("{slash}", "/")


def drawn_units(symbol) -> tuple[int, int]:
    """(unit, body style) a placed symbol draws: its (unit N) and its (convert N) or
    (body_style N), the last of each in file order, else 1."""
    unit, style = 1, 1
    for item in symbol.items:
        head = _head(item)
        if head == b"unit":
            unit = int(item.items[1].value)
        elif head in (b"convert", b"body_style"):
            style = int(item.items[1].value)
    return unit, style


def symbol_placement(symbol) -> tuple[tuple[int, int], T.Matrix]:
    """(position, matrix) KiCad's parser builds from a placed symbol's (at) and (mirror)
    lists in file order: each (at) sets the position and replaces the matrix."""
    position = None
    children: list[tuple[str, object]] = []
    for item in symbol.items:
        head = _head(item)
        if head == b"at":
            position = (read_iu(item.items[1]), read_iu(item.items[2]))
            children.append(("at", item.items[3] if len(item.items) > 3 else None))
        elif head == b"mirror":
            children.append(("mirror", item.items[1] if len(item.items) > 1 else None))
    if position is None:
        raise LookupError(f"the placed symbol at byte {symbol.start} has no (at ...)")
    return position, T.parse_symbol_transform(children)


def mirror_after_at(symbol) -> str:
    """The mirror axis written after a placed symbol's last (at), "" for none; KiCad's
    parser drops a mirror written before it. RuntimeError for two, which the read tools'
    one-axis headers cannot state."""
    axes: list[str] = []
    for item in symbol.items:
        head = _head(item)
        if head == b"at":
            axes = []
        elif head == b"mirror":
            axes.append(_text(item.items[1]))
    if len(axes) > 1:
        raise RuntimeError(f"the symbol at byte {symbol.start} is mirrored {axes} after (at)")
    return axes[0] if axes else ""


def library_symbol(root, symbol):
    """The lib_symbols entry a placed symbol draws: the one its lib_name names when that
    is not empty, else its lib_id. LookupError when there is none; RuntimeError for two
    of one name, where this judge does not model which one KiCad keeps."""
    named = _find(symbol, b"lib_name")
    if named is None or not named.items[1].value:
        named = _child(symbol, b"lib_id")
    key = _name(named.items[1])
    libraries = _find(root, b"lib_symbols")
    entries = [
        entry
        for entry in (_children(libraries, b"symbol") if libraries is not None else ())
        if _name(entry.items[1]) == key
    ]
    if not entries:
        raise LookupError(f"lib_symbols holds no {key!r}")
    if len(entries) > 1:
        raise RuntimeError(f"lib_symbols holds {key!r} {len(entries)} times")
    return entries[0]


def _drawn_pins(library, unit: int, style: int) -> list:
    """The (pin ...) lists of a library symbol that a placed symbol of ``unit`` and
    ``style`` draws."""
    item_name = _name(library.items[1]).partition(":")[2] or _name(library.items[1])
    pins = []
    for sub in _children(library, b"symbol"):
        name = _name(sub.items[1])
        fields = name[len(item_name) + 1 :].split("_") if name.startswith(item_name) else []
        if len(fields) != 2 or not all(f.lstrip("-").isdigit() for f in fields):
            raise RuntimeError(f"KiCad's parser refuses the sub-symbol name {name!r}")
        sub_unit, sub_style = int(fields[0]), int(fields[1])
        if unit and sub_unit and sub_unit != unit:
            continue
        if style and sub_style and sub_style != style:
            continue
        pins += _children(sub, b"pin")
    return pins


class PinEnd(NamedTuple):
    """A pin as KiCad draws it: number and name as written, the connection point in
    internal units (y down), and the unit vector a wire leaves it along."""

    number: str
    name: str
    x: int
    y: int
    outward: tuple[int, int]


def pin_end(position: tuple[int, int], matrix: T.Matrix, pin) -> PinEnd:
    """The end of the library pin ``pin`` (a lexer Group) on a symbol at ``position``
    whose transform is ``matrix``."""
    at = _child(pin, b"at")
    if len(at.items) < 4:
        raise RuntimeError(f"a library pin with no angle at byte {at.start}: KiCad refuses it")
    x, y = T.pin_position(position, matrix, (read_iu(at.items[1]), read_iu(at.items[2])))
    return PinEnd(
        _text(_child(pin, b"number").items[1]),
        _text(_child(pin, b"name").items[1]),
        x,
        y,
        T.pin_outward(matrix, T.pin_orientation(at.items[3])),
    )


def symbol_pin_ends(root, symbol) -> list[PinEnd]:
    """Every pin end a placed symbol draws, from the schematic's own tokens."""
    position, matrix = symbol_placement(symbol)
    unit, style = drawn_units(symbol)
    pins = _drawn_pins(library_symbol(root, symbol), unit, style)
    return [pin_end(position, matrix, pin) for pin in pins]


def port_pin_ends(data: bytes, reference: str) -> list[list[PinEnd]]:
    """The pin ends of each placed symbol whose Reference property is ``reference``, in
    file order: what KiCad draws for the symbols the read tools look up by that name."""
    root = lexed_root(data)
    return [
        symbol_pin_ends(root, symbol)
        for symbol in placed_symbols(root)
        if reference_property(symbol) == reference
    ]


def pin_map(ends: list[PinEnd]) -> dict[str, tuple[int, int]]:
    """{number: (x, y)} of one symbol's pin ends, whose numbers must be distinct."""
    found = {end.number: (end.x, end.y) for end in ends}
    if len(found) != len(ends):
        raise RuntimeError(f"pin numbers repeat: {[end.number for end in ends]}")
    return found


def outward_degrees(vector: tuple[int, int]) -> int:
    """A direction as the pin reads report it: degrees from +x toward +y with the sheet's
    y pointing down, so 0 right, 90 down, 180 left, 270 up."""
    return round(math.degrees(math.atan2(vector[1], vector[0]))) % 360


def pin_pos_of(end: PinEnd) -> tuple[float, float, int]:
    """What a pin read (_get_pin_pos_cst, pin_point_mm) should return for ``end``: x and y
    as floats of the millimetres KiCad writes for them (FormatInternalUnits), and the
    outward direction in degrees."""
    return float(G.sch_mm(end.x)), float(G.sch_mm(end.y)), outward_degrees(end.outward)


# ---------------------------------------------------------------------------
# reading get_pin_positions' report
# ---------------------------------------------------------------------------

#: get_pin_positions' line for a placed symbol, and for each of its pins.
_HEADER = re.compile(
    r"(?P<ref>\S+) \((?P<lib>.*)\) @ \((?P<x>[^,()\s]+), (?P<y>[^,()\s]+)\)"
    r" rot=(?P<rot>\S+) mirror=(?P<mirror>\S+)(?: unit=(?P<unit>\S+))?"
)
_PIN = re.compile(r"  Pin (?P<number>.*?) \((?P<name>.*)\): \((?P<x>[^,()\s]+), (?P<y>[^,()\s]+)\)")


class Reported(NamedTuple):
    """One placed symbol in a get_pin_positions report: its header's fields and its pins
    as (number, x text, y text)."""

    header: dict
    pins: list


def reported_pins(text: str) -> list[Reported]:
    """The report's symbols in the order printed. A line of neither documented form is
    the report's defect, so it fails as an assertion."""
    blocks: list[Reported] = []
    for line in text.splitlines():
        pin = _PIN.fullmatch(line)
        if pin is not None and blocks:
            blocks[-1].pins.append((pin["number"], pin["x"], pin["y"]))
            continue
        header = _HEADER.fullmatch(line)
        if header is None:
            raise AssertionError(f"get_pin_positions printed a line of no known form: {line!r}")
        blocks.append(Reported(header.groupdict(), []))
    return blocks


def reported_ends(block: Reported) -> Counter:
    """(number, x, y) of a reported symbol's pins, coordinates as KiCad reads the text."""
    return Counter((number, report_iu(x), report_iu(y)) for number, x, y in block.pins)


def port_ends(ends: list[PinEnd]) -> Counter:
    return Counter((end.number, end.x, end.y) for end in ends)


def report_map(text: str) -> dict[str, tuple[int, int]]:
    """{number: (x, y)} from a report of one placed symbol, read as KiCad reads it."""
    blocks = reported_pins(text)
    assert len(blocks) == 1, f"one placed symbol, {len(blocks)} reported:\n{text}"
    found = {number: (report_iu(x), report_iu(y)) for number, x, y in blocks[0].pins}
    assert len(found) == len(blocks[0].pins), f"a pin is reported twice:\n{text}"
    return found


def check_report(data: bytes, text: str, reference: str = "R1") -> None:
    """get_pin_positions' report for the one placed ``reference`` in ``data``: its header
    states the symbol's position, (at) angle and the mirror after it, and its pins are the
    symbol's pin ends as KiCad reads the printed numbers."""
    root = lexed_root(data)
    (symbol,) = [s for s in placed_symbols(root) if reference_property(s) == reference]
    blocks = reported_pins(text)
    assert len(blocks) == 1, f"one placed {reference}, {len(blocks)} reported:\n{text}"
    header = blocks[0].header
    at = _child(symbol, b"at")
    first = text.splitlines()[0]
    assert (report_iu(header["x"]), report_iu(header["y"])) == (
        read_iu(at.items[1]),
        read_iu(at.items[2]),
    ), f"the header puts {reference} where it is not: {first!r}"
    assert report_double(header["rot"]) == P.parse_double(at.items[3].value), (
        f"the header's rot= is not the (at) angle {_text(at.items[3])}: {first!r}"
    )
    axis = mirror_after_at(symbol)
    if axis:
        assert header["mirror"] == axis, f"the symbol is mirrored {axis}: {first!r}"
    else:
        assert header["mirror"] not in ("x", "y"), f"the symbol is not mirrored: {first!r}"
    got, want = reported_ends(blocks[0]), port_ends(symbol_pin_ends(root, symbol))
    assert got == want, (
        f"reported (number, x, y) {sorted(got.elements())}, KiCad's pin ends"
        f" {sorted(want.elements())}, in internal units"
    )


# ---------------------------------------------------------------------------
# generated placements of R1 in tests/fixtures/kicad_native.kicad_sch
# ---------------------------------------------------------------------------

#: Positions and library offsets stay within half of parseInternalUnits' clamp, so a pin
#: end, their sum, stays inside the clamp and inside int.
_HALF = G.SCH_LIMIT // 2
#: The fixture's Device:R pins, (x, y, angle) as written in the library.
_R_PINS = ((0, 38_100, 270), (0, -38_100, 90))


def _on_grid(step: int) -> st.SearchStrategy[int]:
    """A coordinate (internal units) within +-_HALF on a grid of ``step``."""
    return st.integers(-(_HALF // step), _HALF // step).map(lambda k: k * step)


def _lib_pins(coordinate: st.SearchStrategy[int]) -> st.SearchStrategy[tuple]:
    """The two library pins of R1: (x, y, angle) each, an angle KiCad writes."""
    pin = st.tuples(coordinate, coordinate, st.sampled_from((0, 90, 180, 270)))
    return st.tuples(pin, pin)


_VERSIONS = st.sampled_from(("k9", "k10"))


@settings(max_examples=sexpr_gen.budget(4))
@given(
    at=st.tuples(G.iu_coordinates(_HALF), G.iu_coordinates(_HALF)),
    pins=_lib_pins(G.iu_coordinates(_HALF)),
    version=_VERSIONS,
)
def test_get_pin_pos_cst_is_kicads_pin_position(at, pins, version):
    """For R1 at a generated position with generated library pins, in each of the 12
    states and under a KiCad 9 or 10 header, _get_pin_pos_cst gives each pin's exact end,
    as a float of the millimetres KiCad writes for it, and the direction a wire leaves
    it, the port's reading of the file's own tokens."""
    for state in G.STATES:
        data = G.schematic_with_r1(at, state, pins, version=version)
        root = _cst.parse(data).lists[0]
        (ends,) = port_pin_ends(data, "R1")
        for end in ends:
            got = schematic._get_pin_pos_cst(root, "R1", end.number)
            assert got == pin_pos_of(end), (state, end)


def _check_every_state(at, pins, version) -> None:
    """get_pin_positions' report of R1 in each of the 12 states, judged by check_report."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "r1.kicad_sch"
        for state in G.STATES:
            data = G.schematic_with_r1(at, state, pins, version=version)
            path.write_bytes(data)
            check_report(data, schematic.get_pin_positions("R1", str(path)))


@settings(max_examples=sexpr_gen.budget(12))
@given(
    at=st.tuples(_on_grid(100), _on_grid(100)),
    pins=_lib_pins(_on_grid(100)),
    version=_VERSIONS,
)
def test_get_pin_positions_reports_pin_ends_on_the_hundredth_grid(at, pins, version):
    """With R1 and its library pins on multiples of 0.01 mm, so every pin end is one too,
    get_pin_positions reports each pin at its exact end in all 12 states and under both
    headers, and its header states R1's position, angle and mirror."""
    _check_every_state(at, pins, version)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=BUG_56)
@settings(max_examples=sexpr_gen.budget(12))
@given(
    at=st.tuples(G.iu_coordinates(_HALF), G.iu_coordinates(_HALF)),
    pins=_lib_pins(G.iu_coordinates(_HALF)),
    version=_VERSIONS,
)
@example(at=(1_006_350, 1_000_000), pins=_R_PINS, version="k9")
def test_get_pin_positions_reports_exact_pin_ends(at, pins, version):
    """The same on the internal unit's own grid of 100 nm: R1 at (100.635, 100), on the
    25 mil grid, has its pins at x = 100.635, which a report to 0.01 mm cannot state."""
    _check_every_state(at, pins, version)


# ---------------------------------------------------------------------------
# kicad-cli: a label at the reported end joins the pin
# ---------------------------------------------------------------------------


def _label(name: str, x: str, y: str) -> bytes:
    """A local label at (x, y), the coordinates written as given."""
    return (
        f'\t(label "{name}"\n\t\t(at {x} {y} 0)\n\t\t(effects\n\t\t\t(font\n'
        "\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t\t(justify left bottom)\n\t\t)\n"
        f'\t\t(uuid "{uuid.uuid4()}")\n\t)\n'
    ).encode()


def with_labels(data: bytes, labels) -> bytes:
    """``data`` with a label (name, x text, y text) for each of ``labels``, inserted as
    text before the root list's closing parenthesis."""
    close = lexed_root(data).end - 1
    return data[:close] + b"".join(_label(*label) for label in labels) + data[close:]


def _unjoined(path: Path, expected: dict[str, tuple[str, str]]) -> list[str]:
    """Each label of ``expected`` whose net in kicad-cli's netlist is not exactly its
    (reference, pin)."""
    nets = netlist_nodes(path)
    wrong = []
    for name, node in sorted(expected.items()):
        got = set(nets.get(name, {}))
        if got != {node}:
            wrong.append(f"{name}: kicad-cli puts {sorted(got)} on it, not {node}")
    return wrong


def _require_port_ends_join(path: Path, data: bytes, references) -> None:
    """The premise of the label tests: with a label at every pin end the port computes,
    kicad-cli joins each pin to its own label. RuntimeError otherwise."""
    labels, expected = [], {}
    for ref in references:
        for ends in port_pin_ends(data, ref):
            for end in ends:
                name = f"N_{ref}_{end.number}"
                labels.append((name, G.sch_mm(end.x), G.sch_mm(end.y)))
                expected[name] = (ref, end.number)
    path.write_bytes(with_labels(data, labels))
    wrong = _unjoined(path, expected)
    if wrong:
        raise RuntimeError("labels at the port's pin ends do not join:\n" + "\n".join(wrong))


def _labels_at_reported_ends(path: Path, data: bytes, references) -> list[str]:
    """Put a label at every pin end get_pin_positions reports for ``references``, each
    written with the report's own text, and return the labels kicad-cli does not join to
    their pins."""
    labels, expected = [], {}
    for ref in references:
        for block in reported_pins(schematic.get_pin_positions(ref, str(path))):
            for number, x, y in block.pins:
                for coordinate in (x, y):
                    report_iu(coordinate)  # KiCad must read the text as a number
                name = f"N_{ref}_{number}"
                labels.append((name, x, y))
                expected[name] = (ref, number)
    at_report = path.with_name("reported.kicad_sch")
    at_report.write_bytes(with_labels(data, labels))
    return _unjoined(at_report, expected)


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=BUG_56)
def test_a_label_at_the_reported_pin_end_joins_in_kicad_cli(tmp_path):
    """R1 at (100.635, 100): a label at each pin end get_pin_positions reports puts that
    pin on the label's net in kicad-cli's netlist. The premise, checked first, is that a
    label at each pin end the port computes does (measured on kicad-cli 9.0.8 by the
    inventory: the exact end joins, the reported (100.64, 96.19) does not)."""
    data = G.schematic_with_r1((1_006_350, 1_000_000), (0, ""))
    base = tmp_path / "r1.kicad_sch"
    base.write_bytes(data)
    _require_port_ends_join(tmp_path / "exact.kicad_sch", data, ["R1"])
    wrong = _labels_at_reported_ends(base, data, ["R1"])
    assert not wrong, "labels at the reported pin ends miss their pins:\n" + "\n".join(wrong)


#: routing.kicad_sym's symbols whose pins carry no implicit net: all but the power symbols.
_ROUTING_LIB = {
    _text(s.items[1]): s for s in _children(lexed_root(Path(RF.LIB).read_bytes()), b"symbol")
}
_JOIN_SYMBOLS = tuple(name for name, s in _ROUTING_LIB.items() if not _children(s, b"power"))
#: The first placement's grid point, (100 mm, 100 mm) in internal units; later ones step
#: 25.4 mm right, four to a row, rows 38.1 mm apart.
_BASE = (1_000_000, 1_000_000)
#: An offset off the 1.27 mm grid, on the 1 um grid.
_UM_OFFSET = st.integers(0, 1_269).map(lambda k: 10 * k)


def _move(path: Path, moves: dict[str, tuple[int, int]]) -> None:
    """Rewrite each named symbol's (at) x and y, through the lexer's offsets."""
    data = path.read_bytes()
    edits = []
    for symbol in placed_symbols(lexed_root(data)):
        ref = reference_property(symbol)
        if ref in moves:
            at = _child(symbol, b"at")
            text = f"{G.sch_mm(moves[ref][0])} {G.sch_mm(moves[ref][1])}"
            edits.append((at.items[1].start, at.items[2].end, text))
    for start, end, text in sorted(edits, reverse=True):
        data = data[:start] + text.encode() + data[end:]
    path.write_bytes(data)


@pytest.mark.nightly
@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=BUG_56)
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(st.sampled_from(_JOIN_SYMBOLS), G.symbol_states(), _UM_OFFSET, _UM_OFFSET),
        min_size=1,
        max_size=8,
    )
)
@example([("R", (0, ""), 6_350, 0)])
def test_nightly_labels_at_reported_pin_ends_join_in_kicad_cli(placements):
    """routing.kicad_sym's symbols placed in generated states at positions on the 1 um
    grid (the explicit example is R at (100.635, 100)): a label at every pin end
    get_pin_positions reports joins that pin in kicad-cli. When one does not, the premise
    that labels at the port's pin ends do is checked before the test fails."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(RF.fresh(Path(work), "pins"))
        moves = {}
        for i, (name, (rotation, mirror), dx, dy) in enumerate(placements):
            ref = f"X{i + 1}"
            gx, gy = _BASE[0] + 254_000 * (i % 4), _BASE[1] + 381_000 * (i // 4)
            RF.place(str(path), name, ref, gx / 1e4, gy / 1e4, rotation, mirror)
            moves[ref] = (gx + dx, gy + dy)
        _move(path, moves)
        data = path.read_bytes()
        wrong = _labels_at_reported_ends(path, data, list(moves))
        if wrong:
            _require_port_ends_join(path.with_name("exact.kicad_sch"), data, list(moves))
        assert not wrong, "labels at the reported pin ends miss their pins:\n" + "\n".join(wrong)


# ---------------------------------------------------------------------------
# the demo schematics KiCad ships
# ---------------------------------------------------------------------------

#: Two demo sheets with parts off the 0.01 mm grid (measured on KiCad 9.0.8, 2026-10-11:
#: Debugger's TP7 at x = 95.201; 30 parts of hv_converter, D2 at x = 127.762).
_DEMO_SHEETS = ("Debugger.kicad_sch", "hv_converter.kicad_sch")


@lru_cache(maxsize=1)
def _demo_sheets() -> dict[str, Path]:
    """The installed demo schematics named in _DEMO_SHEETS, by file name."""
    found: dict[str, Path] = {}
    for _, path in K.shipped(subdirs=("demos",), suffixes=(".kicad_sch",), names=()):
        if path.name in _DEMO_SHEETS:
            found.setdefault(path.name, path)
    return found


def off_grid_parts(root) -> tuple[list[tuple[str, list[PinEnd]]], Counter]:
    """(reference, pin ends of every placed symbol of it) for each part of a schematic
    with a pin end off the 0.01 mm grid, the port finding them, and a count of the parts
    left out and why. Left out: a part whose instance entries give another reference or
    unit than its Reference property and (unit N) (which pins the read tools then draw is
    bug 32's question, not this one's), a part whose definition is missing or derived,
    and a part with every pin end on the grid."""
    by_reference: dict[str | None, list] = {}
    for symbol in placed_symbols(root):
        by_reference.setdefault(reference_property(symbol), []).append(symbol)
    found, left_out = [], Counter()
    for reference, symbols in by_reference.items():
        if reference is None:
            left_out["no Reference property"] += 1
            continue
        if any(
            entry != (reference, drawn_units(symbol)[0])
            for symbol in symbols
            for entry in instance_entries(symbol)
        ):
            left_out["instance entries disagree"] += 1
            continue
        try:
            libraries = [library_symbol(root, symbol) for symbol in symbols]
        except LookupError:
            left_out["no definition"] += 1
            continue
        if any(_find(library, b"extends") is not None for library in libraries):
            left_out["derived definition"] += 1
            continue
        ends = [end for symbol in symbols for end in symbol_pin_ends(root, symbol)]
        if all(end.x % 100 == 0 and end.y % 100 == 0 for end in ends):
            left_out["every pin end on the grid"] += 1
            continue
        found.append((reference, ends))
    return found, left_out


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=BUG_56)
@pytest.mark.parametrize("sheet", _DEMO_SHEETS, ids=[s.split(".")[0] for s in _DEMO_SHEETS])
def test_get_pin_positions_on_kicads_demo_sheets(sheet):
    """On a copy of a demo sheet KiCad saved, every part the port finds with a pin end
    off the 0.01 mm grid is reported with every pin at its exact end, read as KiCad reads
    the printed numbers (all of one reference's placed symbols together, as the tool
    lists them). The sheet must hold at least one such part."""
    if not K.share_dirs():
        pytest.skip(K.NO_INSTALL)
    path = _demo_sheets().get(sheet)
    if path is None:
        pytest.skip(f"the install ships no demo {sheet} (the macOS cask ships templates only)")
    parts, left_out = off_grid_parts(K.lexed(path)[0])
    if not parts:
        raise RuntimeError(f"{sheet} holds no part off the 0.01 mm grid ({dict(left_out)})")
    wrong = []
    with tempfile.TemporaryDirectory() as work:
        copy = Path(work) / sheet
        shutil.copyfile(path, copy)
        for reference, ends in parts:
            blocks = reported_pins(schematic.get_pin_positions(reference, str(copy)))
            got = sum((reported_ends(block) for block in blocks), Counter())
            if got != port_ends(ends):
                wrong.append(
                    f"{reference}: reported {sorted(got.elements())},"
                    f" KiCad's {sorted(port_ends(ends).elements())}"
                )
    assert not wrong, f"{len(wrong)} of {len(parts)} parts misreported:\n" + "\n".join(wrong[:10])
