"""Rung 1: the number text the CST writes and reads.

_cst._num writes the text of every coordinate and distance a tool emits (through _fill_at
and the writers), exactly without a scale and as the position KiCad holds with one;
_cst._angle_text writes every angle; _cst._numish turns number text, or a number, into what
a tool prints.

Judges, none of them the code under test: arithmetic (text read back with float() is the
number written); KiCad's lexer port, because KiCad needs a NUMBER token wherever it reads
a coordinate (parseDouble's NeedNUMBER, include/dsnlexer.h:531-535 at 9.0.8, 536-540 at
10.0.7); and the port of FormatInternalUnits in tests/kicad_ref/eda_units.py, the text
KiCad itself writes for a distance (common/eda_units.cpp:170-200 at 9.0.8, 194-225 at
10.0.7). That function formats an int count of internal units, so it never writes "-0",
and it strips trailing zeros and a bare point ({:.10f} below 0.0001 mm, {:.10g} above),
so it never writes a number ending in ".0".
"""

from __future__ import annotations

import math

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import parse_units as P

from mcp_server_kicad import _cst

_FINITE = st.floats(allow_nan=False, allow_infinity=False)
_SCALES = st.sampled_from((U.SCH_IU_PER_MM, U.PCB_IU_PER_MM))

# ---------------------------------------------------------------------------
# _num
# ---------------------------------------------------------------------------


@example(-0.0)
@example(-4.5e-05)
@example(0.1 + 0.2)
@given(_FINITE)
def test_num_round_trips(x):
    """The text read back is the number written, and KiCad's lexer reads it as a NUMBER
    token, which is what its parsers need for a coordinate."""
    text = _cst._num(x)
    assert float(text) == x, (x, text)
    assert L.is_number(text.encode("ascii")), (x, text)


@example(-0.0)
@example(-1.0)
@example(1e16)
@given(_FINITE)
def test_num_never_writes_point_zero_or_minus_zero(x):
    """KiCad's formatter writes neither a trailing ".0" nor "-0" (see the module
    docstring), so neither does the CST."""
    text = _cst._num(x)
    assert not text.endswith(".0"), (x, text)
    assert text != "-0", (x, text)


@example(value=-45, scale=U.PCB_IU_PER_MM)
@given(value=st.integers(U.INT_MIN, U.INT_MAX), scale=_SCALES)
def test_num_writes_kicads_text_at_the_files_resolution(value, scale):
    """A coordinate KiCad holds as ``value`` internal units is, in millimetres,
    value / scale; written by _num it must be the text KiCad writes for it. -45 nm on a
    board is -0.000045 to KiCad (_num used to write -4.5e-05)."""
    assert _cst._num(value / scale) == U.format_internal_units(value, scale)


# ---------------------------------------------------------------------------
# a number written for a file: _num with a scale, _snap, _angle_text
# ---------------------------------------------------------------------------

#: Millimetres KiCad's int coordinates reach at either scale without KiROUND clamping.
_MM = st.floats(-2147.0, 2147.0, allow_nan=False, allow_infinity=False)


def _reading(scale: int):
    return P.parse_sch_internal_units if scale == U.SCH_IU_PER_MM else P.parse_board_units


@example(v=100.123456, scale=U.SCH_IU_PER_MM)
@example(v=0.1 + 0.2, scale=U.PCB_IU_PER_MM)
@example(v=-4.5e-05, scale=U.PCB_IU_PER_MM)
@example(v=1e-05, scale=U.PCB_IU_PER_MM)
@example(v=0.5 / U.SCH_IU_PER_MM, scale=U.SCH_IU_PER_MM)
@example(v=-0.5 / U.SCH_IU_PER_MM, scale=U.SCH_IU_PER_MM)
@example(v=-1e-09, scale=U.PCB_IU_PER_MM)
@given(v=_MM, scale=_SCALES)
def test_num_with_a_scale_writes_kicads_text_for_the_value_it_holds(v, scale):
    """In a file of ``scale`` units per millimetre KiCad holds KiROUND( v * scale ) units,
    half away from zero, and writes them with FormatInternalUnits: that is _num(v, scale),
    and both majors read the text back as the count it was written for."""
    held = U.kiround(v * scale, major=9)
    text = _cst._num(v, scale)
    assert text == U.format_internal_units(held, scale)
    assert _reading(scale)(text, major=9) == _reading(scale)(text, major=10) == held, text


@example(n=0, scale=U.SCH_IU_PER_MM)
@example(n=U.INT_MAX, scale=U.PCB_IU_PER_MM)
@given(n=st.integers(U.INT_MIN, U.INT_MAX), scale=_SCALES)
def test_snap_leaves_a_value_on_the_grid_as_it_is(n, scale):
    """A count of units over the scale is already what KiCad holds, so _snap hands back that
    very float; an int on the grid stays an int, so a message that echoes it reads as the
    request did."""
    v = n / scale
    assert _cst._snap(v, scale) is v
    whole = n // scale
    assert type(_cst._snap(whole, scale)) is int and _cst._snap(whole, scale) == whole


@example(v=0.1 + 0.2, scale=U.SCH_IU_PER_MM)
@example(v=100.123456, scale=U.SCH_IU_PER_MM)
@given(v=_MM, scale=_SCALES)
def test_snap_lands_on_the_nearest_unit_and_stays_there(v, scale):
    """The snapped value is within half a unit of the request (and a hair for the product
    v * scale), and is a count of units over the scale, so snapping it again changes nothing."""
    snapped = _cst._snap(v, scale)
    assert abs(snapped - v) <= 0.5 / scale + 1e-9, (v, snapped)
    assert snapped == U.kiround(v * scale, major=9) / scale
    assert _cst._snap(snapped, scale) == snapped


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_number_that_is_no_number_is_refused_not_written(bad):
    """NaN and infinity raise instead of becoming text KiCad's parser refuses."""
    for scale in (None, U.SCH_IU_PER_MM, U.PCB_IU_PER_MM):
        with pytest.raises((ValueError, OverflowError)):
            _cst._num(bad, scale)
    with pytest.raises(ValueError):
        _cst._angle_text(bad)


@example(-0.0)
@example(0.1 + 0.2)
@example(1e-05)
@example(359.9 - 360.0)
@example(12.3456789012)
@example(1e300)
@given(st.floats(-1e300, 1e300, allow_nan=False, allow_subnormal=False))
def test_angle_text_is_formatangles_text_and_never_minus_zero(angle):
    """FormatAngle prints ten significant digits (the port in eda_units); an angle with more
    comes out rounded, so the text is a fixed point of KiCad's read then write, and the one
    value FormatAngle would write "-0" for is written 0."""
    text = _cst._angle_text(angle)
    assert U.format_angle(P.parse_double(text)) == text
    want = U.format_angle(angle)
    assert text == ("0" if want == "-0" else want)


# ---------------------------------------------------------------------------
# _numish
# ---------------------------------------------------------------------------

#: Number text: as KiCad writes a coordinate; any token KiCad's lexer reads as a NUMBER
#: (isNumber, common/dsnlexer.cpp:495-542 at 9.0.8, 491-538 at 10.0.7: a sign, digits
#: around an optional point, an exponent); and Python's own spelling of a float.
_NUMBER_TEXT = st.one_of(
    st.builds(U.format_internal_units, st.integers(U.INT_MIN, U.INT_MAX), _SCALES),
    st.from_regex(
        r"[-+]?(?:[0-9]{1,6}\.?[0-9]{0,6}|\.[0-9]{1,6})(?:[eE][-+]?[0-9]{1,2})?", fullmatch=True
    ).filter(lambda text: L.is_number(text.encode("ascii"))),
    _FINITE.map(repr),
).filter(lambda text: math.isfinite(float(text)))
#: Numbers handed over directly, as the read tools sometimes do.
_NUMBERS = st.one_of(_FINITE, st.integers(-(2**63), 2**63))


@example("-0")
@example("1e5")
@example("-0.000045")
@example(-2.5)
@given(st.one_of(_NUMBER_TEXT, _NUMBERS))
def test_numish_reads_numbers_as_float_parse_does(value):
    """_numish(t) is the number float(t) reads, for text and for numbers alike."""
    got = _cst._numish(value)
    assert isinstance(got, (int, float)) and not isinstance(got, bool), type(got)
    assert got == float(value), (value, got)


@example("100.0")
@example("-0.0")
@example("1e16")
@example(3.0)
@given(st.one_of(_NUMBER_TEXT, _NUMBERS))
def test_numish_prints_whole_numbers_without_a_point(value):
    """What a tool prints for a number follows KiCad's formatter: a whole number has no
    trailing ".0" and zero is never "-0"; the printed text reads back as the number."""
    printed = str(_cst._numish(value))
    assert not printed.endswith(".0"), (value, printed)
    assert printed != "-0", (value, printed)
    assert float(printed) == float(value), (value, printed)
