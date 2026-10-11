"""Exact plane geometry for the test ladder's judges.

Everything here is rational arithmetic (fractions.Fraction) on the numbers it is given:
ints, Fractions, or floats, each float taken at its exact binary value. Nothing
compares with a tolerance. Where an answer is irrational (a radius, a distance) the
function says so and rounds once, at the end.

Points are (x, y) pairs. orient's sign is the sign of the cross product, positive for
a counterclockwise turn when y points up; KiCad's y points down, which mirrors what
counterclockwise looks like on screen and changes nothing here, because every function
uses the one convention throughout.

Board outlines: parity() is KiCad's nesting rule for closed Edge.Cuts contours
(pcbnew/convert_shape_list_to_polygon.cpp:524-595 at 9.0.8; buildContourHierarchy,
addOutlinesToPolygon and addHolesToPolygon, 392-487 at 10.0.7): a contour with an even
number of contours around its first point is an outline, one with an odd number is a
hole in the contour one level up. For closed contours that neither touch nor cross,
a point is then on the board exactly when an odd number of contours contain it.
KiCad 10's separate handling of contours that cross each other is not modelled.

This module imports nothing from the repository.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from fractions import Fraction
from typing import NamedTuple, Union

#: An exact number: ints stay ints, anything else becomes a Fraction.
Rational = Union[int, Fraction]


def _q(value) -> Rational:
    """An exact rational: an int or a Fraction as it is, anything else as a Fraction.
    Ints stay ints so that integer inputs never pay for Fraction arithmetic; every
    division below goes through Fraction, so nothing turns into a float."""
    return value if isinstance(value, (int, Fraction)) else Fraction(value)


def _pt(p) -> tuple[Rational, Rational]:
    return _q(p[0]), _q(p[1])


def _cross(u, v) -> Fraction:
    return u[0] * v[1] - u[1] * v[0]


def _dot(u, v) -> Fraction:
    return u[0] * v[0] + u[1] * v[1]


def _sub(a, b) -> tuple[Fraction, Fraction]:
    return a[0] - b[0], a[1] - b[1]


def orient(a, b, p) -> int:
    """The sign of det [[ax, ay, 1], [bx, by, 1], [px, py, 1]], expanded by cofactors
    down its last column: +1 when a, b, p turn counterclockwise (y up), -1 clockwise,
    0 when they are collinear."""
    (ax, ay), (bx, by), (px, py) = _pt(a), _pt(b), _pt(p)
    det = (bx * py - by * px) - (ax * py - ay * px) + (ax * by - ay * bx)
    return (det > 0) - (det < 0)


def on_segment(p, a, b) -> bool:
    """Whether p lies on the closed segment ab (on the point itself when a == b)."""
    (px, py), (ax, ay), (bx, by) = _pt(p), _pt(a), _pt(b)
    if orient(a, b, p) != 0:
        return False
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def segment_distance2(p, a, b) -> Fraction:
    """The exact squared distance from p to the closed segment ab."""
    p, a, b = _pt(p), _pt(a), _pt(b)
    ab, ap = _sub(b, a), _sub(p, a)
    length2 = _dot(ab, ab)
    if length2 == 0:
        return _dot(ap, ap)
    t = min(max(Fraction(_dot(ap, ab), 1) / length2, Fraction(0)), Fraction(1))
    d = (ap[0] - t * ab[0], ap[1] - t * ab[1])
    return _dot(d, d)


def segments_intersect(a, b, c, d) -> bool:
    """Whether the closed segments ab and cd share a point."""
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    return (
        (o1 == 0 and on_segment(c, a, b))
        or (o2 == 0 and on_segment(d, a, b))
        or (o3 == 0 and on_segment(a, c, d))
        or (o4 == 0 and on_segment(b, c, d))
    )


def segments_distance2(a, b, c, d) -> Fraction:
    """The exact squared distance between the closed segments ab and cd."""
    if segments_intersect(a, b, c, d):
        return Fraction(0)
    return min(
        segment_distance2(a, c, d),
        segment_distance2(b, c, d),
        segment_distance2(c, a, b),
        segment_distance2(d, a, b),
    )


def _edges(poly: Sequence) -> Iterable[tuple]:
    n = len(poly)
    if n < 3:
        raise ValueError(f"a polygon needs at least 3 vertices, got {n}")
    for i in range(n):
        yield poly[i], poly[(i + 1) % n]


def on_boundary(p, poly: Sequence) -> bool:
    """Whether p lies on an edge of the closed polygon ``poly``."""
    return any(on_segment(p, a, b) for a, b in _edges(poly))


def winding_number(p, poly: Sequence) -> int:
    """How many times the closed polygon ``poly`` winds around p.

    Positive for counterclockwise vertices (y up). Raises ValueError for a point on
    the boundary, where no winding number exists.
    """
    if on_boundary(p, poly):
        raise ValueError(f"{p} is on the boundary")
    py = _q(p[1])
    wn = 0
    for a, b in _edges(poly):
        ay, by = _q(a[1]), _q(b[1])
        if ay <= py:
            if by > py and orient(a, b, p) > 0:
                wn += 1
        elif by <= py and orient(a, b, p) < 0:
            wn -= 1
    return wn


def inside(p, poly: Sequence) -> bool:
    """Whether p is inside the closed polygon ``poly`` (nonzero winding number)."""
    return winding_number(p, poly) != 0


class Circle(NamedTuple):
    """A circle contour: centre (x, y) and a rational radius."""

    center: tuple
    radius: Rational


def contour_contains(p, contour) -> bool:
    """Whether p is inside a closed contour: a polygon (sequence of points) or a Circle.

    Raises ValueError on the boundary, which KiCad gives no rule for.
    """
    if isinstance(contour, Circle):
        d = _sub(_pt(p), _pt(contour.center))
        d2, r2 = _dot(d, d), _q(contour.radius) ** 2
        if d2 == r2:
            raise ValueError(f"{p} is on the circle")
        return d2 < r2
    return inside(p, contour)


def clear_of(p, contour, delta) -> bool:
    """Whether p keeps at least ``delta`` from the contour's boundary (exact)."""
    delta = _q(delta)
    if isinstance(contour, Circle):
        d = _sub(_pt(p), _pt(contour.center))
        d2, r = _dot(d, d), _q(contour.radius)
        return d2 >= (r + delta) ** 2 or (r >= delta and d2 <= (r - delta) ** 2)
    return all(segment_distance2(p, a, b) >= delta * delta for a, b in _edges(contour))


def parity(p, contours: Iterable) -> bool:
    """Whether p is on the board KiCad builds from these contours: inside an odd number
    of them (the nesting rule in the module docstring)."""
    return sum(contour_contains(p, c) for c in contours) % 2 == 1


def circle_through(a, b, c) -> tuple[Fraction, Fraction, Fraction]:
    """(cx, cy, r2): the centre and squared radius of the circle through a, b and c.

    Raises ValueError when the three points are collinear (or two coincide).
    """
    (ax, ay), (bx, by), (cx, cy) = _pt(a), _pt(b), _pt(c)
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if d == 0:
        raise ValueError(f"{a}, {b}, {c} are collinear: no circle passes through them")
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = Fraction(a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = Fraction(a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, (ax - ux) ** 2 + (ay - uy) ** 2


def _same_direction(u, v) -> bool:
    return _cross(u, v) == 0 and _dot(u, v) > 0


def _strictly_between(u, v, w) -> bool:
    """Whether direction v lies strictly inside the counterclockwise turn from u to w."""
    if _same_direction(v, u) or _same_direction(v, w):
        return False
    turn = _cross(u, w)
    if turn > 0:  # less than half a turn
        return _cross(u, v) > 0 and _cross(v, w) > 0
    if turn < 0:  # more than half a turn: between unless in the closed turn from w to u
        return not (_cross(w, v) >= 0 and _cross(v, u) >= 0)
    if _dot(u, w) < 0:  # exactly half a turn
        return _cross(u, v) > 0
    raise ValueError("an arc from a direction to the same direction is no arc")


_AXES = ((1, 0), (0, 1), (-1, 0), (0, -1))


def arc_extremes(start, mid, end) -> list[tuple[float, float]]:
    """The points an arc's bounding box is made of: its two ends, plus each of its
    circle's four axis extremes that the arc from ``start`` through ``mid`` to ``end``
    passes. Membership is decided exactly; an extreme's coordinates are the exact
    centre plus or minus math.sqrt of the exact squared radius, rounded once."""
    cx, cy, r2 = circle_through(start, mid, end)
    centre = (cx, cy)
    vs, vm, ve = (_sub(_pt(q), centre) for q in (start, mid, end))
    low, high = (vs, ve) if _strictly_between(vs, vm, ve) else (ve, vs)
    radius = math.sqrt(r2)
    out = [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))]
    for axis in _AXES:
        if (
            _same_direction(axis, low)
            or _same_direction(axis, high)
            or _strictly_between(low, axis, high)
        ):
            out.append((float(cx) + radius * axis[0], float(cy) + radius * axis[1]))
    return out


def bbox(points: Iterable) -> tuple:
    """(min x, min y, max x, max y) of the points."""
    pts = list(points)
    if not pts:
        raise ValueError("the box of no points")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def arc_bbox(start, mid, end) -> tuple:
    """The bounding box of the arc from ``start`` through ``mid`` to ``end``."""
    return bbox(arc_extremes(start, mid, end))


def circle_distance(p, center, radius) -> float:
    """The distance from p to the circle (the curve, not the disc); the exact squared
    distance to the centre is rounded once through math.sqrt."""
    d = _sub(_pt(p), _pt(center))
    return abs(math.sqrt(_dot(d, d)) - float(radius))
