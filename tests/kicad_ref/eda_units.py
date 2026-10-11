"""KiCad's coordinate rounding and number formatting, ported as oracles.

Scales (include/base_units.h:70 and 72 at 9.0.8 and 10.0.7): a schematic internal
unit is 100 nm (SCH_IU_PER_MM = 1e4), a board unit is 1 nm (PCB_IU_PER_MM = 1e6).

KiROUND<double, int> (libs/kimath/include/math/util.h) changed between the tags:

- 9.0.8 (99-142): adds 0.5 to the magnitude in double arithmetic and truncates toward
  zero; a result past INT_MAX gives INT_MAX - 1 and one below INT_MIN gives INT_MIN + 1.
- 10.0.7 (101-128): std::llround, which rounds halfway cases away from zero without the
  0.5 addition's own rounding error, then clamps to [INT_MIN, INT_MAX].

They agree everywhere except the clamp values and inputs such as 0.49999999999999994,
where adding 0.5 in double arithmetic rounds up to 1.0. A NaN, and for 10.0.7 a value
outside long long's range, is left unspecified by C++, so both ports refuse one.

EDA_UNIT_UTILS::FormatInternalUnits (common/eda_units.cpp:170-200 at 9.0.8, 194-225 at
10.0.7; 10.0.7 adds a data-type scale that is IU_PER_MM for distances): the value in
millimetres, printed {:.10f} with trailing zeros and a bare point stripped when
0 < |v| <= 0.0001, and {:.10g} otherwise. Python's format() follows the same printf
rules as fmt for both presentations.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

import math

SCH_IU_PER_MM = 10_000
PCB_IU_PER_MM = 1_000_000

INT_MAX = 2**31 - 1
INT_MIN = -(2**31)


def kiround(value: float, *, major: int) -> int:
    """KiROUND(value) to int, as KiCad ``major`` (9 or 10) computes it."""
    if math.isnan(value):
        raise ValueError("KiROUND of NaN is unspecified")
    if major == 9:
        bumped = value - 0.5 if value < 0 else value + 0.5
        if bumped > INT_MAX:
            return INT_MAX - 1
        if bumped < INT_MIN:
            return INT_MIN + 1
        return int(bumped)  # truncates toward zero, as the long long cast does
    if major >= 10:
        if math.isinf(value) or abs(value) >= 2.0**63:
            raise ValueError("std::llround outside long long is unspecified")
        whole = math.trunc(value)
        fraction = value - whole  # exact: removing the integer part of a double
        if fraction >= 0.5:
            whole += 1
        elif fraction <= -0.5:
            whole -= 1
        return max(INT_MIN, min(INT_MAX, whole))
    raise ValueError(f"no KiROUND model for KiCad {major}")


def format_internal_units(value: int, iu_per_mm: int) -> str:
    """FormatInternalUnits: the text KiCad writes for ``value`` internal units."""
    if not INT_MIN <= value <= INT_MAX:
        raise ValueError(f"{value} is not an int32 internal-unit value")
    millimetres = value / iu_per_mm
    if millimetres != 0.0 and abs(millimetres) <= 0.0001:
        text = format(millimetres, ".10f").rstrip("0")
        return text[:-1] if text.endswith(".") else text
    return format(millimetres, ".10g")


def format_angle(degrees: float) -> str:
    """FormatAngle: the text KiCad writes for an angle of ``degrees``.

    EDA_UNIT_UTILS::FormatAngle (common/eda_units.cpp:162-167 at 9.0.8, 186-191 at
    10.0.7) is fmt::format( "{:.10g}", aAngle.AsDegrees() ): ten significant digits,
    trailing zeros and a bare point dropped, an exponent below 1e-4 or from 1e10 on.
    """
    return format(degrees, ".10g")
