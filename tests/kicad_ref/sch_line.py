"""A schematic line's point test and split, ported as oracles for the wire and junction tests.

IsPointOnSegment (libs/kimath/src/trigo.cpp:89-104, identical at 9.0.8 and 10.0.7): with
S the segment's start, E its end and P the point, P is on the closed segment when the
cross product of E - S and P - S is zero and their dot product is at least |P - S|^2,
all in 64-bit integers, so the test is exact and holds at any angle; a segment whose
ends coincide holds only that point. SCH_SCREEN::GetBusesAndWires with aIgnoreEndpoints
(eeschema/sch_screen.cpp:1234-1254 at 9.0.8, 1338-1358 at 10.0.7), the list of lines a
junction splits, skips a line that ends at the point and keeps those IsPointOnSegment
holds for: a point strictly inside the line.

SCH_LINE::BreakAt (eeschema/sch_line.cpp:574-583 at 9.0.8, 578-587 at 10.0.7) duplicates
the line, starts the copy at the point and ends the original there. SCH_ITEM::Duplicate
(eeschema/sch_item.cpp:115-131 at 9.0.8, 149-177 at 10.0.7) gives the copy a fresh KIID
and copies everything else, its stroke included, but no group: at 10.0.7 the SCH_LINE copy
constructor sets the copy's group to none (sch_line.cpp:83-103), so Duplicate's step that
adds the copy to the original's group finds none. So the original keeps its start, its
uuid and its stroke, and the copy runs from the point to the original's old end.

Points are (x, y) tuples of schematic internal units (100 nm), as KiCad holds them.
This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

Point = tuple[int, int]


def _int(point) -> Point:
    x, y = point
    if not (isinstance(x, int) and isinstance(y, int)):
        raise TypeError(f"internal units are integers, got {point!r}")
    return x, y


def on_segment(start, end, point) -> bool:
    """IsPointOnSegment( start, end, point ): the closed segment, ends included."""
    (sx, sy), (ex, ey), (px, py) = _int(start), _int(end), _int(point)
    seg_x, seg_y = ex - sx, ey - sy
    vec_x, vec_y = px - sx, py - sy
    if seg_x * vec_y - seg_y * vec_x:
        return False
    return seg_x * vec_x + seg_y * vec_y >= vec_x * vec_x + vec_y * vec_y


def strictly_inside(start, end, point) -> bool:
    """Whether a junction at ``point`` splits the line: on it, and neither of its ends."""
    point = _int(point)
    return point != _int(start) and point != _int(end) and on_segment(start, end, point)


def break_at(start, end, point) -> tuple[tuple[Point, Point], tuple[Point, Point]]:
    """SCH_LINE::BreakAt's two lines: (the original, the copy), each as (start, end).

    Raises ValueError for a point the split would not happen at (not strictly inside),
    since KiCad only breaks a line GetBusesAndWires returned.
    """
    if not strictly_inside(start, end, point):
        raise ValueError(f"{point!r} is not strictly inside {start!r} to {end!r}")
    start, end, point = _int(start), _int(end), _int(point)
    return (start, point), (point, end)
