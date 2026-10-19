"""Rung 2: the schematic symbol transform, judged by KiCad's own source.

mcp_server_kicad._connectivity says where KiCad draws every pin: symbol_transform reads a
placed symbol's (at) and (mirror) items in file order, pin_end puts a library pin through
the resulting matrix, and pin_point_mm reports that in millimetres. Their judges import
nothing from mcp_server_kicad:

- kicad_ref.transform: TRANSFORM, SCH_SYMBOL::SetOrientation, the parser's rules for a
  placed symbol, GetPinPhysicalPosition with parseXY( true ) and SCH_PIN::PinDrawOrient,
  checked in test_ladder_r2_judges.py against the 84 pin ends kicad-cli delivered, every
  placed symbol in KiCad's demos and kicad-cli's netlist;
- kicad_ref.parse_units: NeedNUMBER plus parseDouble, and parseInternalUnits' clamp,
  checked there against kicad-cli's load verdicts;
- the lexer port, kicad_ref.dsn_lexer, through which every judge reads the bytes a test
  wrote, so no judge sees them through the CST under test.

The parser (eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp) reads a placed
symbol's (at x y angle) at 3071-3085 and its (mirror x|y) at 3087-3098 at 9.0.8
(3188-3215 at 10.0.7, the same code), and a library pin's (at x y angle) at 1597-1610
(1671-1684 at 10.0.7). (at) takes exactly three NUMBER tokens: x and y through
parseInternalUnits, a symbol's angle through static_cast<int>( parseDouble ) and a pin's
through parseInt, which is strtol; NeedRIGHT then refuses a fourth. (mirror) takes exactly
the bare keyword x or y. The parser handles items in file order and a later item
overrides an earlier one. The 12 states a symbol can be written in (4 rotations, 3 mirror
states) give 8 matrices, and every test here covers all of them on every run.

Coordinates compare as integers. KiROUND changed between KiCad 9 and 10
(kicad_ref.eda_units), so a test accepts the reading of either major; inside the clamp
the two differ only on rare halfway doubles.
"""

from __future__ import annotations

import math
from decimal import Decimal

import geom_gen as G
from hypothesis import example, given
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import parse_units as P
from kicad_ref import transform as T

from mcp_server_kicad import _connectivity as C
from mcp_server_kicad import _cst

MAJORS = (9, 10)
#: The largest schematic coordinate KiCad keeps, in internal units: parseInternalUnits'
#: clamp, INT_MAX * 0.7071 (about 151848.5687 mm), rounded in.
_SYMBOL_LIMIT = G.SCH_LIMIT
#: Library pin offsets stay small enough that a symbol's position plus a transformed
#: offset is still an int, which GetPinPhysicalPosition's VECTOR2I sum needs.
_OFFSET_LIMIT = U.INT_MAX - G.SCH_LIMIT


def _state_items(state: tuple[int, str]) -> list[tuple[str, str]]:
    rotation, mirror = state
    return [("at", str(rotation))] + ([("mirror", mirror)] if mirror else [])


#: The matrix KiCad's parser builds for each of the 12 written states.
_STATE_MATRIX = {state: T.parse_symbol_transform(_state_items(state)) for state in G.STATES}
#: The 8 distinct matrices among them.
_MATRICES = tuple(dict.fromkeys(_STATE_MATRIX.values()))


def _examples(*cases: dict):
    """Each case as an explicit @example, so exhaustive cases run in every profile."""

    def decorate(test):
        for case in reversed(cases):
            test = example(**case)(test)
        return test

    return decorate


# ---------------------------------------------------------------------------
# text, and how KiCad's parser reads it (the judges)
# ---------------------------------------------------------------------------

_OTHER_ITEMS = (
    "(unit 1)",
    "(exclude_from_sim no)",
    # A property carries an (at) of its own, which is not the symbol's.
    '(property "Reference" "R1" (at 101.6 97.79 90) (effects (font (size 1.27 1.27))))',
)


def _symbol_text(items) -> bytes:
    """A placed symbol: (lib_id) first, then ``items`` in order. An item is ("at", x, y,
    angle), ("mirror", axis), or ("raw", text) for any other item, written verbatim."""
    parts = ['(symbol (lib_id "Device:R")']
    for item in items:
        if item[0] == "at":
            parts.append(f"(at {item[1]} {item[2]} {item[3]})")
        elif item[0] == "mirror":
            parts.append(f"(mirror {item[1]})")
        else:
            parts.append(item[1])
    return (" ".join(parts) + ")").encode()


def _pin_text(at: str) -> bytes:
    """A library pin shaped as KiCad writes one, with ``at`` as its (at ...) item(s)."""
    return (
        f"(pin passive line {at} (length 2.54)"
        ' (name "~" (effects (font (size 1.27 1.27))))'
        ' (number "1" (effects (font (size 1.27 1.27)))))'
    ).encode()


def _parse(text: bytes):
    """The node the code under test reads: the CST's first list in ``text``."""
    return _cst.parse(text).lists[0]


def _head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _xy(tokens) -> dict[int, tuple[int, int]]:
    """parseXY of two tokens, as each KiCad major reads them (ParseRefusal if refused)."""
    return {
        major: (
            P.parse_sch_internal_units(tokens[0].value, major=major),
            P.parse_sch_internal_units(tokens[1].value, major=major),
        )
        for major in MAJORS
    }


def _three_numbers(tokens) -> bool:
    """(at) as parseXY, the angle's NeedNUMBER and NeedRIGHT take it: exactly three
    tokens, the first two NUMBERs (the third is the angle reader's to judge)."""
    return (
        len(tokens) == 3
        and all(t.kind == L.NUMBER for t in tokens[:2])
        and tokens[2].kind in L.ATOM_KINDS
    )


def _kicad_symbol(text: bytes):
    """KiCad's reading of the placed symbol ``text``: ({major: (x, y)}, matrix), or None
    where its parser refuses the file. Items apply in file order: each (at) checks its
    tokens, sets the position and resets the matrix; each (mirror) needs exactly one
    token; parse_symbol_transform judges the angles and axes. A symbol with no (at)
    stays at the origin and its (mirror) still applies (measured: kicad-cli 9.0.8 joins
    such a symbol's pins to labels at the mirrored points about the origin, 2026-10-11)."""
    (symbol,) = L.tree(text)
    position = {major: (0, 0) for major in MAJORS}
    children: list[tuple[str, object]] = []
    try:
        for item in symbol.items:
            head = _head(item)
            if head == b"at":
                tokens = item.items[1:]
                if not _three_numbers(tokens):
                    return None
                position = _xy(tokens)
                children.append(("at", tokens[2]))
            elif head == b"mirror":
                tokens = item.items[1:]
                if len(tokens) != 1 or tokens[0].kind not in L.ATOM_KINDS:
                    return None
                children.append(("mirror", tokens[0]))
        return position, T.parse_symbol_transform(children)
    except P.ParseRefusal:
        return None


def _kicad_pin(text: bytes):
    """KiCad's reading of the library pin ``text``: ({major: (x, y) as written, y up},
    orientation), or None where its parser refuses the file. Each (at) needs exactly
    three NUMBER tokens, reads x and y through parseInternalUnits and the angle through
    parseInt (transform.pin_orientation, strtol); a later (at) overrides an earlier one
    (measured: kicad-cli 9.0.8 joins a pin with two (at) items at the second, 2026-10-11)."""
    (pin,) = L.tree(text)
    found = None
    try:
        for item in pin.items:
            if _head(item) == b"at":
                tokens = item.items[1:]
                if not _three_numbers(tokens):
                    return None
                found = _xy(tokens), T.pin_orientation(tokens[2])
    except P.ParseRefusal:
        return None
    if found is None:
        raise RuntimeError(f"the test wrote a pin without (at): {text!r}")
    return found


def _same_symbol(got, want, text: bytes) -> None:
    (pos, matrix), (positions, kicad_matrix) = got, want
    assert matrix == kicad_matrix, (text, matrix, kicad_matrix)
    assert pos in {positions[major] for major in MAJORS}, (text, pos, positions)


def _same_pin_end(pos: tuple[int, int], m: T.Matrix):
    """A comparison of pin_end(pos, m, ...)'s answer with KiCad's reading of the pin."""

    def same(got, want, text: bytes) -> None:
        x, y, outward = got
        offsets, orientation = want
        ends = {T.pin_position(pos, m, offsets[major]) for major in MAJORS}
        assert (x, y) in ends, (text, m, (x, y), ends)
        assert outward == T.pin_outward(m, orientation), (text, m, outward)

    return same


def _assert_reads_as_kicad(text: bytes, want, call, same) -> None:
    """The code under test reads ``text`` as KiCad's parser does: a Refusal where the
    parser refuses the file (``want`` is None), else the parser's reading (``same``
    compares). Any other exception fails: KiCad's parser refuses or reads every file it
    is given, it never crashes, so a reader has to answer with a Refusal."""
    try:
        got = call()
    except C.Refusal:
        assert want is None, f"refused {text!r}, which KiCad's parser reads as {want}"
        return
    except Exception as error:
        verdict = "refuses" if want is None else "reads"
        raise AssertionError(
            f"{type(error).__name__} ({error}) on {text!r}, which KiCad's parser {verdict}"
        ) from error
    assert want is not None, f"read {got} from {text!r}, which KiCad's parser refuses"
    same(got, want, text)


# ---------------------------------------------------------------------------
# generated tokens
# ---------------------------------------------------------------------------

_STYLES = st.sampled_from(("fixed", "scientific", "mantissa"))


def _spell(digits: int, places: int, negative: bool, style: str) -> str:
    """(-)digits * 10**-places as a NUMBER token without a leading '+': fixed point
    (12.34), scientific (1.234e+1) or an integer mantissa with an exponent (1234e-2)."""
    value = Decimal(digits).scaleb(-places)
    if negative:
        value = -value
    if style == "fixed":
        text = format(value, "f")
    elif style == "scientific":
        text = format(value, "e")
    else:
        text = f"{'-' if negative else ''}{digits}e-{places}"
    if text.startswith("+") or not L.is_number(text.encode()):
        raise RuntimeError(f"the generator spelled {text!r}, not a NUMBER without '+'")
    return text


@st.composite
def _decimal_tokens(draw, limit: int) -> str:
    """A coordinate token for a value under limit internal units, not necessarily on
    KiCad's 100 nm grid: up to ten decimal places, in any of _spell's styles."""
    places = draw(st.integers(0, 10))
    digits = draw(st.integers(0, (limit // U.SCH_IU_PER_MM) * 10**places - 1))
    return _spell(digits, places, draw(st.booleans()), draw(_STYLES))


def _coordinates(limit: int) -> st.SearchStrategy[str]:
    """A coordinate token within +-limit internal units, inside the clamp, without '+':
    KiCad's own text for a 100 nm grid value (often on the 1.27 mm grid), or a decimal."""
    return st.one_of(st.builds(G.sch_mm, G.iu_coordinates(limit)), _decimal_tokens(limit))


@st.composite
def _symbol_angles(draw) -> str:
    """An angle token KiCad loads for a placed symbol: static_cast<int> of its value is
    0, 90, 180 or 270 (so 90.5 is 90 and -0.5 is 0), in any of _spell's styles."""
    base = draw(st.sampled_from((0, 90, 180, 270)))
    places = draw(st.integers(0, 8))
    digits = base * 10**places + draw(st.integers(0, 10**places - 1))
    return _spell(digits, places, base == 0 and draw(st.booleans()), draw(_STYLES))


@st.composite
def _pin_angles(draw, value: int) -> str:
    """A spelling of the pin angle ``value`` (0, 90, 180 or 270) on which KiCad's strtol
    and a truncated float agree: a sign ('+', or '-' before 0), leading zeros, a
    fraction, a zero exponent. The spellings where the two disagree are the pin angle test's."""
    sign = draw(st.sampled_from(("", "+", "-") if value == 0 else ("", "+")))
    places = draw(st.integers(0, 6))
    if places:
        fraction = "." + "".join(
            draw(st.lists(st.sampled_from("0123456789"), min_size=places, max_size=places))
        )
    else:
        fraction = draw(st.sampled_from(("", ".")))
    whole = draw(st.sampled_from(("", "0", "00"))) + str(value)
    if value == 0 and places and draw(st.booleans()):
        whole = ""  # ".5": strtol reads no digits, so 0, as the float's truncation does
    return sign + whole + fraction + draw(st.sampled_from(("", "e0", "E+0", "e-00")))


def _all_pin_angles() -> st.SearchStrategy[tuple[str, ...]]:
    """One agreeing spelling of each pin angle, 0, 90, 180 and 270, in that order."""
    return st.tuples(*(_pin_angles(value) for value in (0, 90, 180, 270)))


@st.composite
def _transform_items(draw, max_items: int = 4) -> list:
    """1 to max_items (at) and (mirror) items in any order, all loadable (coordinates in
    the clamp without '+', angles KiCad loads, a bare x or y), with up to two other
    items between them."""
    items: list = []
    for _ in range(draw(st.integers(1, max_items))):
        if draw(st.booleans()):
            x, y = draw(_coordinates(_SYMBOL_LIMIT)), draw(_coordinates(_SYMBOL_LIMIT))
            items.append(("at", x, y, draw(_symbol_angles())))
        else:
            items.append(("mirror", draw(st.sampled_from(("x", "y")))))
    for other in draw(st.lists(st.sampled_from(_OTHER_ITEMS), max_size=2)):
        items.insert(draw(st.integers(0, len(items))), ("raw", other))
    return items


#: Bare tokens KiCad's parseDouble and parseInt refuse (NeedNUMBER wants what isNumber
#: accepts: a sign, digits around a point, an exponent), some of which Python's float()
#: reads.
_NOT_NUMBERS = (
    "inf",
    "-inf",
    "infinity",
    "Infinity",
    "nan",
    "NaN",
    "9_0",
    "1_000.5",
    "0x10",
    "1e",
    ".",
    "abc",
)
#: NUMBER tokens past a double's range: from_chars reports result_out_of_range, and
#: strtol reads their leading digits, no angle a pin takes.
_OUT_OF_RANGE = ("1e400", "-1e400", "1e309", "-2.5e308")
#: NUMBER tokens a coordinate reads and an angle refuses (truncated, none is 0, 90, 180
#: or 270; strtol reads 1e2 as 1).
_OTHER_NUMBERS = ("1e2", "359", "45", "-90", "360", "100")


def _plus(tokens: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """'+' before a token without its sign: a NUMBER to isNumber, which from_chars refuses."""
    return tokens.map(lambda t: "+" + t.lstrip("-"))


def _number_like(valid: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Valid tokens, the same with '+', and the bare tokens above."""
    return st.one_of(
        valid, _plus(valid), st.sampled_from(_NOT_NUMBERS + _OUT_OF_RANGE + _OTHER_NUMBERS)
    )


# ---------------------------------------------------------------------------
# the tables and the matrix arithmetic
# ---------------------------------------------------------------------------


def _sheet_degrees(vector: tuple[int, int]) -> int:
    """The direction of ``vector`` in the sheet's frame, whose Y axis points down: whole
    degrees from +X toward +Y, in [0, 360)."""
    return round(math.degrees(math.atan2(vector[1], vector[0]))) % 360


def test_orientation_tables_are_kicads():
    """The readers' tables are KiCad's: _ROT holds the parser's four (at) literals
    (3076-3079 at 9.0.8), _MIR SetOrientation's two mirror matrices (sch_symbol.cpp:
    1794-1808 at 9.0.8), _TOWARD PinDrawOrient's vector into the body for each angle
    the pin parser maps (sch_pin.cpp:1389-1396 at 9.0.8: 0 right, 90 up, 180 left, 270
    down), and _OUT_DEG names each outward unit vector by its direction in the sheet
    frame, the convention pin_point_mm reports (0 right, 90 down, 180 left, 270 up)."""
    assert C._ROT == T.PARSER_ROTATIONS
    assert C._MIR == T.MIRROR_TEMPS
    assert set(C._TOWARD) == {0, 90, 180, 270}
    for angle, vector in C._TOWARD.items():
        assert vector == T.PIN_END[T.pin_orientation(str(angle))], angle
    assert set(C._OUT_DEG) == {(1, 0), (0, 1), (-1, 0), (0, -1)}
    for vector, degrees in C._OUT_DEG.items():
        assert degrees == _sheet_degrees(vector), vector


def test_compose_is_kicads_incremental_step():
    """_compose is SetOrientation's product (sch_symbol.cpp:1910-1913 at 9.0.8,
    2565-2568 at 10.0.7): the old matrix applied first, the temporary one after it.
    Exhaustive: each of the 8 matrices composed with each of the 8, which include the
    four temporaries SetOrientation multiplies by (rotate counterclockwise, rotate
    clockwise, mirror x, mirror y)."""
    assert len(_MATRICES) == 8
    temps = {*T.ROTATE_TEMPS.values(), *T.MIRROR_TEMPS.values()}
    assert len(temps) == 4 and temps <= set(_MATRICES)
    for m in _MATRICES:
        for temp in _MATRICES:
            assert C._compose(m, temp) == T.compose_incremental(m, temp), (m, temp)


@_examples(*({"m": m, "x": 3, "y": -7} for m in _MATRICES))
@given(
    m=st.sampled_from(_MATRICES),
    x=st.integers(U.INT_MIN, U.INT_MAX),
    y=st.integers(U.INT_MIN, U.INT_MAX),
)
def test_apply_is_transform_coordinate(m, x, y):
    """_apply is TRANSFORM::TransformCoordinate (transform.cpp:44-47 at 9.0.8 and
    10.0.7) for any int point, under each of the 8 matrices (each an explicit example)."""
    assert C._apply(m, x, y) == T.transform_coordinate(m, x, y)


# ---------------------------------------------------------------------------
# a placed symbol's (at) and (mirror)
# ---------------------------------------------------------------------------


@_examples(
    *(
        {"items": [("at", "100", "50", str(r))] + ([("mirror", a)] if a else [])}
        for r, a in G.STATES
    ),
    {"items": [("at", "100.635", "-50.8", "90.5")]},
    {"items": [("at", "0.0001", "151848.5686", "-0.5"), ("mirror", "y")]},
    {"items": [("mirror", "y"), ("at", "1", "2", "270.9")]},  # a mirror before (at) is lost
    {"items": [("at", "1", "2", "90"), ("mirror", "x"), ("at", "3", "4", "180")]},
    {"items": [("mirror", "x"), ("raw", _OTHER_ITEMS[2])]},  # no (at): the origin
)
@given(items=_transform_items())
def test_symbol_transform_follows_kicads_parser(items):
    """For loadable (at) and (mirror) items in any order and number, symbol_transform
    returns the position parseInternalUnits reads from the last (at) and the matrix the
    parser builds (transform.parse_symbol_transform), and ignores an (at) inside another
    item. All 12 states are explicit examples."""
    text = _symbol_text(items)
    want = _kicad_symbol(text)
    if want is None:
        raise RuntimeError(f"the generator wrote a symbol KiCad refuses: {text!r}")
    _assert_reads_as_kicad(text, want, lambda: C.symbol_transform(_parse(text), "R1"), _same_symbol)


@example(x="100", y="50", angle="+90", mirror="")
@example(x="100", y="50", angle="inf", mirror="")
@example(x="+100", y="50", angle="0", mirror="")
@given(
    x=_number_like(_coordinates(_SYMBOL_LIMIT)),
    y=_number_like(_coordinates(_SYMBOL_LIMIT)),
    angle=_number_like(_symbol_angles()),
    mirror=st.sampled_from(("", "x", "y")),
)
def test_symbol_transform_refuses_what_kicads_parser_refuses(x, y, angle, mirror):
    """For number-like bare tokens in a placed symbol's (at), symbol_transform raises
    Refusal exactly where KiCad's parser refuses the file and otherwise reads what it
    reads: parseDouble refuses a token that is not a NUMBER (inf, nan, 9_0), one with a
    leading '+' (std::from_chars), and one out of a double's range (1e400); the angle
    rule then refuses 1e2, 359 or 45. kicad-cli's verdicts on '+100', '+90' and '1e400'
    are test_ladder_r2_judges.py's. Coordinates stay inside the clamp
    (test_ladder_r2_numbers.py). Anything but a Refusal or the reading is a failure."""
    text = _symbol_text([("at", x, y, angle)] + ([("mirror", mirror)] if mirror else []))
    want = _kicad_symbol(text)
    _assert_reads_as_kicad(text, want, lambda: C.symbol_transform(_parse(text), "R1"), _same_symbol)


# ---------------------------------------------------------------------------
# library pins
# ---------------------------------------------------------------------------


@example(pos=(1_000_000, 1_000_000), lib=("0", "3.81"), angles=("0", "90", "180", "270"))
@example(
    pos=(1_006_350, -12_700), lib=("-5.08", "0.0001"), angles=("-.5", "+90", "180.25e0", "270.")
)
@given(
    pos=st.tuples(G.iu_coordinates(_SYMBOL_LIMIT), G.iu_coordinates(_SYMBOL_LIMIT)),
    lib=st.tuples(_coordinates(_OFFSET_LIMIT), _coordinates(_OFFSET_LIMIT)),
    angles=_all_pin_angles(),
)
def test_pin_end_is_kicads_pin_position(pos, lib, angles):
    """pin_end(pos, m, pin) is the connection point GetPinPhysicalPosition computes
    (sch_symbol.cpp:2327-2333 at 9.0.8: the transformed library position, its Y negated
    by parseXY( true ), plus the symbol's position) and the outward direction
    PinDrawOrient implies, for generated positions, library offsets and spellings of
    each pin angle, under all 12 states every example."""
    for angle in angles:
        text = _pin_text(f"(at {lib[0]} {lib[1]} {angle})")
        want = _kicad_pin(text)
        if want is None:
            raise RuntimeError(f"the generator wrote a pin KiCad refuses: {text!r}")
        pin = _parse(text)
        for m in _STATE_MATRIX.values():
            _assert_reads_as_kicad(
                text, want, lambda m=m: C.pin_end(pos, m, pin, "U1"), _same_pin_end(pos, m)
            )


@example(items=[("at", "100.635", "100", "0")], lib=("0", "3.81"), angles=("0", "90", "180", "270"))
@given(
    items=_transform_items(),
    lib=st.tuples(_coordinates(_OFFSET_LIMIT), _coordinates(_OFFSET_LIMIT)),
    angles=_all_pin_angles(),
)
def test_pin_point_mm_is_the_exact_pin_end(items, lib, angles):
    """pin_point_mm(symbol, pin) is the exact pin end in millimetres (the internal units
    over 10,000) and the outward direction in whole degrees in the sheet frame (0
    right, 90 down, 180 left, 270 up), all from the port's reading of the same text."""
    symbol_text = _symbol_text(items)
    want = _kicad_symbol(symbol_text)
    if want is None:
        raise RuntimeError(f"the generator wrote a symbol KiCad refuses: {symbol_text!r}")
    positions, m = want
    symbol = _parse(symbol_text)
    for angle in angles:
        pin_text = _pin_text(f"(at {lib[0]} {lib[1]} {angle})")
        pin_want = _kicad_pin(pin_text)
        if pin_want is None:
            raise RuntimeError(f"the generator wrote a pin KiCad refuses: {pin_text!r}")
        offsets, orientation = pin_want
        degrees = _sheet_degrees(T.pin_outward(m, orientation))
        expected = set()
        for major in MAJORS:
            jx, jy = T.pin_position(positions[major], m, offsets[major])
            expected.add((jx / U.SCH_IU_PER_MM, jy / U.SCH_IU_PER_MM, degrees))
        got = C.pin_point_mm(symbol, _parse(pin_text), "U1")
        assert got in expected, (symbol_text, pin_text, got, expected)


@example(x="+0", y="3.81", angle="270", state=(0, ""))
@example(x="0", y="3.81", angle="inf", state=(0, ""))
@example(x="0", y="nan", angle="90", state=(90, "x"))
@given(
    x=_number_like(_coordinates(_OFFSET_LIMIT)),
    y=_number_like(_coordinates(_OFFSET_LIMIT)),
    angle=st.one_of(
        *(_pin_angles(value) for value in (0, 90, 180, 270)),
        st.sampled_from(_NOT_NUMBERS + _OUT_OF_RANGE + _OTHER_NUMBERS),
    ),
    state=G.symbol_states(),
)
def test_pin_end_refuses_what_kicads_parser_refuses(x, y, angle, state):
    """The same rule for a library pin's (at): its coordinates through parseDouble (a
    leading '+', a non-NUMBER or an out-of-range token is refused; measured: kicad-cli
    9.0.8 refuses (at +0 3.81 270), 2026-10-11) and its angle through parseInt, whose
    NeedNUMBER refuses a word and whose strtol takes a '+'. Angle spellings on which
    strtol and a float disagree are test_pin_end_reads_pin_angles_as_kicads_parser_does's."""
    m, pos = _STATE_MATRIX[state], (1_000_000, 1_000_000)
    text = _pin_text(f"(at {x} {y} {angle})")
    want = _kicad_pin(text)
    _assert_reads_as_kicad(
        text, want, lambda: C.pin_end(pos, m, _parse(text), "U1"), _same_pin_end(pos, m)
    )


# ---------------------------------------------------------------------------
# found while building this rung
# ---------------------------------------------------------------------------


@st.composite
def _pin_angle_numbers(draw) -> str:
    """A NUMBER token for a pin angle, built so that what strtol reads (the leading
    digits) or what a float reads (the whole value) is 0, 90, 180 or 270, and often only
    one of them: the angle spelled with a nonzero exponent (9.0e1, 0.90e2, 900e-1), or
    its digits followed by a fraction and an exponent (90.9e5); sometimes signed."""
    target = draw(st.sampled_from((0, 90, 180, 270)))
    exponent = draw(st.integers(1, 6))
    if draw(st.booleans()):
        shift = exponent if draw(st.booleans()) else -exponent
        text = f"{format(Decimal(target).scaleb(-shift), 'f')}e{shift}"
    else:
        text = f"{target}.{draw(st.integers(0, 999))}e{exponent}"
    return draw(st.sampled_from(("", "", "+", "-"))) + text


@example(token="9e1", state=(0, ""))
@example(token="0.9e2", state=(90, "x"))
@example(token="90.9e5", state=(180, "y"))
@given(token=_pin_angle_numbers(), state=G.symbol_states())
def test_pin_end_reads_pin_angles_as_kicads_parser_does(token, state):
    """A library pin's angle is parseInt( "pin orientation" ): NeedNUMBER, then strtol,
    which stops at the first byte that is not a digit (sch_io_kicad_sexpr_parser.h:
    128-137 at 9.0.8, 150-159 at 10.0.7). Measured with kicad-cli 9.0.8 (2026-10-11):
    9e1, 1.8e2, 2.7e2 and 27e1 are refused, 90.9e5, 0.9e2 and 0.27e3 load, and its SVG
    draws a 0.9e2 pin exactly as a 0 pin, pointing right. pin_end reads the angle as a
    truncated float, so it accepts the first group, refuses 90.9e5 and points 0.9e2 up.
    The pin end itself does not depend on the angle; the outward direction does."""
    m, pos = _STATE_MATRIX[state], (1_000_000, 1_000_000)
    text = _pin_text(f"(at 0 3.81 {token})")
    want = _kicad_pin(text)
    _assert_reads_as_kicad(
        text, want, lambda: C.pin_end(pos, m, _parse(text), "U1"), _same_pin_end(pos, m)
    )


_EXTRAS = ("5", "-1.27", "x", "y", "(z)", '"q"')
_AXES = ("X", "Y", "xy", "z", "0", "mirror", "x_")
_DEFECTS = (
    "at_short",
    "at_extra",
    "at_quoted",
    "mirror_short",
    "mirror_axis",
    "mirror_quoted",
    "mirror_extra",
    "pin_short",
    "pin_extra",
    "pin_quoted",
    "pin_second_at",
)
_VALID_PIN = "(at 0 3.81 270)"


def _quote_one(draw, tokens: list[str]) -> list[str]:
    i = draw(st.integers(0, len(tokens) - 1))
    return tokens[:i] + [f'"{tokens[i]}"'] + tokens[i + 1 :]


@st.composite
def _grammar_defects(draw):
    """(kind, symbol items, pin (at) text): a placed symbol and a library pin, valid but
    for one defect in KiCad's grammar of (at) and (mirror), named by ``kind``: a missing
    token (*_short), an extra token or list (*_extra), a quoted token, a mirror axis
    that is not the bare keyword x or y, or a second (at) in the pin. Every token is
    otherwise valid, so the number readers' rules and the clamp stay out of it."""
    x, y = draw(_coordinates(_SYMBOL_LIMIT)), draw(_coordinates(_SYMBOL_LIMIT))
    angle = str(draw(st.sampled_from((0, 90, 180, 270))))
    items: list = [("at", x, y, angle)]
    mirror = draw(st.sampled_from(("", "x", "y")))
    if mirror:
        items.append(("mirror", mirror))
    pin = [draw(_coordinates(_OFFSET_LIMIT)), draw(_coordinates(_OFFSET_LIMIT))]
    pin.append(str(draw(st.sampled_from((0, 90, 180, 270)))))
    pin_at = f"(at {' '.join(pin)})"
    kind = draw(st.sampled_from(_DEFECTS))
    axis = draw(st.sampled_from(("x", "y")))
    extra = draw(st.sampled_from(_EXTRAS))
    defect = None
    if kind == "at_short":
        defect = "(at " + " ".join([x, y, angle][: draw(st.integers(0, 2))]) + ")"
    elif kind == "at_extra":
        defect = f"(at {x} {y} {angle} {extra})"
    elif kind == "at_quoted":
        defect = "(at " + " ".join(_quote_one(draw, [x, y, angle])) + ")"
    elif kind == "mirror_short":
        defect = "(mirror)"
    elif kind == "mirror_axis":
        defect = f"(mirror {draw(st.sampled_from(_AXES))})"
    elif kind == "mirror_quoted":
        defect = f'(mirror "{axis}")'
    elif kind == "mirror_extra":
        defect = f"(mirror {axis} {extra})"
    elif kind == "pin_short":
        pin_at = "(at " + " ".join(pin[: draw(st.integers(0, 2))]) + ")"
    elif kind == "pin_extra":
        pin_at = f"(at {' '.join(pin)} {extra})"
    elif kind == "pin_quoted":
        pin_at = "(at " + " ".join(_quote_one(draw, pin)) + ")"
    else:
        second = [draw(_coordinates(_OFFSET_LIMIT)), draw(_coordinates(_OFFSET_LIMIT))]
        second.append(str(draw(st.sampled_from((0, 90, 180, 270)))))
        pin_at += f" (at {' '.join(second)})"
    if defect is not None:
        if kind.startswith("at_") and draw(st.booleans()):
            items[0] = ("raw", defect)  # in place of the valid (at)
        else:
            items.insert(draw(st.integers(0, len(items))), ("raw", defect))
    return kind, items, pin_at


_A_SYMBOL = ("at", "100", "100", "0")


@example(defects=("at_short", [("raw", "(at 100 100)")], _VALID_PIN))
@example(defects=("at_short", [("raw", "(at 1)")], _VALID_PIN))
@example(defects=("at_extra", [("raw", "(at 100 100 0 5)")], _VALID_PIN))
@example(defects=("at_quoted", [("raw", '(at 100 100 "90")')], _VALID_PIN))
@example(defects=("at_quoted", [("raw", '(at "100" 100 0)')], _VALID_PIN))
@example(defects=("mirror_short", [_A_SYMBOL, ("raw", "(mirror)")], _VALID_PIN))
@example(defects=("mirror_axis", [_A_SYMBOL, ("raw", "(mirror z)")], _VALID_PIN))
@example(defects=("mirror_axis", [_A_SYMBOL, ("raw", "(mirror X)")], _VALID_PIN))
@example(defects=("mirror_quoted", [_A_SYMBOL, ("raw", '(mirror "x")')], _VALID_PIN))
@example(defects=("mirror_extra", [_A_SYMBOL, ("raw", "(mirror x y)")], _VALID_PIN))
@example(defects=("pin_short", [_A_SYMBOL], "(at 0 3.81)"))
@example(defects=("pin_extra", [_A_SYMBOL], "(at 0 3.81 270 1)"))
@example(defects=("pin_quoted", [_A_SYMBOL], '(at 0 3.81 "270")'))
@example(defects=("pin_quoted", [_A_SYMBOL], '(at "0" 3.81 270)'))
@example(defects=("pin_second_at", [_A_SYMBOL], "(at 0 3.81 270) (at 0 7.62 270)"))
@given(defects=_grammar_defects())
def test_transform_readers_follow_kicads_grammar(defects):
    """A placed symbol's (at) and (mirror) and a library pin's (at), with one defect in
    KiCad's grammar (see the module docstring): the readers refuse exactly what the
    parser refuses and otherwise read what it reads. NeedNUMBER refuses a missing or a
    quoted token, NeedRIGHT an extra one, (mirror) any token but the bare keyword x or
    y (keywords match case-sensitively and a quoted string is no keyword), and a pin's
    second (at) overrides its first. Measured with kicad-cli 9.0.8 (2026-10-11): every
    refused variant among the examples fails to load, and a label at the pin's second
    (at) joins the pin while its first (at) is left alone. pin_end reads the first."""
    kind, items, pin_at = defects
    symbol_text, pin_text = _symbol_text(items), _pin_text(pin_at)
    symbol_want, pin_want = _kicad_symbol(symbol_text), _kicad_pin(pin_text)
    refused = symbol_want if kind.startswith(("at_", "mirror_")) else pin_want
    if kind != "pin_second_at" and refused is not None:
        raise RuntimeError(f"the generator's {kind} is read by KiCad's parser: {items, pin_at}")
    _assert_reads_as_kicad(
        symbol_text,
        symbol_want,
        lambda: C.symbol_transform(_parse(symbol_text), "R1"),
        _same_symbol,
    )
    m, pos = T.IDENTITY, (1_000_000, 1_000_000)
    _assert_reads_as_kicad(
        pin_text, pin_want, lambda: C.pin_end(pos, m, _parse(pin_text), "U1"), _same_pin_end(pos, m)
    )
