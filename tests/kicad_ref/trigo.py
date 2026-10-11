"""KiCad's angle arithmetic and point rotation, ported as oracles.

EDA_ANGLE (libs/kimath/include/geometry/eda_angle.h; the file is identical at 9.0.8
and 10.0.7) holds degrees in a double:

- Normalize (229-238): ``while v < -0.0: v += 360`` then ``while v >= 360: v -= 360``.
  -0.0 is not below -0.0, so it stays -0.0; a tiny negative value becomes 360 minus
  itself, which can round to 360.0 and then to 0.0.
- Normalize180 (268-277): ``while v <= -180: v += 360`` then ``while v > 180: v -= 360``.
- Sin (178-195) and Cos (197-214) test a normalized copy for the multiples of 45
  degrees and return 0, 1, -1 or M_SQRT1_2 exactly; any other angle goes through
  ``sin( AsRadians() )``, the angle's own value (not the normalized copy) times
  DEGREES_TO_RADIANS = M_PI / 180.0 (122).
- operator== (354-358) compares the doubles exactly.

RotatePoint (libs/kimath/src/trigo.cpp; these functions are identical at 9.0.8 and
10.0.7, though the file differs elsewhere):

- int (229-264): normalize a copy of the angle; 0, 90, 180 and 270 are exact swaps
  ((x, y), (y, -x), (-x, -y), (-y, x)); any other angle gives
  KiROUND( y * sin + x * cos ) and KiROUND( y * cos - x * sin ). The VECTOR2I overload
  (libs/kimath/include/trigo.h:69-72, identical at both tags) forwards to it.
- about a centre (267-278): rotate (x - cx, y - cy), then add the centre back.
- double (295-330): the same arithmetic without KiROUND.

Rounding is eda_units.kiround for the KiCad major asked for. The int arithmetic is
C++ int, so the port refuses coordinates whose negation or offset would overflow it
(undefined behaviour in C++). It also assumes the multiplications and the addition are
rounded separately, as on KiCad's x86-64 builds; a build that fuses them could differ
by one nanometre where a product lands within a few ulps of a half (inference, not
measured).

Two refusals guard the loops: a non-finite angle, and any angle of 1e9 degrees or
more in magnitude. No KiCad file or tool argument comes near that bound, and from
2**62 on KiCad's own loops never end, because 360 is less than half a double's spacing
there.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

import math

from kicad_ref.eda_units import INT_MAX, INT_MIN, kiround

#: M_SQRT1_2 from <cmath>, as the double the literal 0.70710678118654752440 rounds to.
M_SQRT1_2 = 0.70710678118654752440
#: EDA_ANGLE::DEGREES_TO_RADIANS (eda_angle.h:122).
DEGREES_TO_RADIANS = math.pi / 180.0
#: Angles at or beyond this magnitude are refused (see the module docstring).
ANGLE_LIMIT = 1e9


def _checked(degrees: float) -> float:
    value = float(degrees)
    if not math.isfinite(value) or abs(value) >= ANGLE_LIMIT:
        raise ValueError(f"refusing angle {degrees!r}: outside what the port models")
    return value


def normalize(degrees: float) -> float:
    """EDA_ANGLE::Normalize: into [0, 360), -0.0 kept."""
    value = _checked(degrees)
    while value < -0.0:
        value += 360.0
    while value >= 360.0:
        value -= 360.0
    return value


def normalize180(degrees: float) -> float:
    """EDA_ANGLE::Normalize180: into (-180, 180]."""
    value = _checked(degrees)
    while value <= -180.0:
        value += 360.0
    while value > 180.0:
        value -= 360.0
    return value


def sin_deg(degrees: float) -> float:
    """EDA_ANGLE( degrees, DEGREES_T ).Sin()."""
    test = normalize(degrees)
    if test == 0.0 or test == 180.0:
        return 0.0
    if test == 45.0 or test == 135.0:
        return M_SQRT1_2
    if test == 225.0 or test == 315.0:
        return -M_SQRT1_2
    if test == 90.0:
        return 1.0
    if test == 270.0:
        return -1.0
    return math.sin(float(degrees) * DEGREES_TO_RADIANS)


def cos_deg(degrees: float) -> float:
    """EDA_ANGLE( degrees, DEGREES_T ).Cos()."""
    test = normalize(degrees)
    if test == 0.0:
        return 1.0
    if test == 180.0:
        return -1.0
    if test == 90.0 or test == 270.0:
        return 0.0
    if test == 45.0 or test == 315.0:
        return M_SQRT1_2
    if test == 135.0 or test == 225.0:
        return -M_SQRT1_2
    return math.cos(float(degrees) * DEGREES_TO_RADIANS)


def _int(value: int, what: str) -> int:
    """An int coordinate whose negation C++ int arithmetic can hold."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} must be an int, got {value!r}")
    if not INT_MIN < value <= INT_MAX:
        raise ValueError(f"{what} {value} is outside what C++ int arithmetic holds here")
    return value


def rotate_point(x: int, y: int, degrees: float, *, major: int) -> tuple[int, int]:
    """RotatePoint( int* pX, int* pY, const EDA_ANGLE& ) as KiCad ``major`` rounds it."""
    x, y = _int(x, "x"), _int(y, "y")
    angle = normalize(degrees)
    if angle == 0.0:
        return x, y
    if angle == 90.0:
        return y, -x
    if angle == 180.0:
        return -x, -y
    if angle == 270.0:
        return -y, x
    sinus, cosinus = sin_deg(angle), cos_deg(angle)
    return (
        kiround((y * sinus) + (x * cosinus), major=major),
        kiround((y * cosinus) - (x * sinus), major=major),
    )


def rotate_point_about(
    x: int, y: int, cx: int, cy: int, degrees: float, *, major: int
) -> tuple[int, int]:
    """RotatePoint( int* pX, int* pY, int cx, int cy, const EDA_ANGLE& )."""
    ox = _int(_int(x, "x") - _int(cx, "cx"), "x - cx")
    oy = _int(_int(y, "y") - _int(cy, "cy"), "y - cy")
    rx, ry = rotate_point(ox, oy, degrees, major=major)
    return rx + cx, ry + cy


def rotate_point_double(x: float, y: float, degrees: float) -> tuple[float, float]:
    """RotatePoint( double* pX, double* pY, const EDA_ANGLE& )."""
    x, y = float(x), float(y)
    angle = normalize(degrees)
    if angle == 0.0:
        return x, y
    if angle == 90.0:
        return y, -x
    if angle == 180.0:
        return -x, -y
    if angle == 270.0:
        return -y, x
    sinus, cosinus = sin_deg(angle), cos_deg(angle)
    return (y * sinus) + (x * cosinus), (y * cosinus) - (x * sinus)
