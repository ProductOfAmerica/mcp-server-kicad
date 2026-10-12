"""Rung 2: footprint shapes and pad angles.

The code under test reads a footprint's courtyard and writes its pads' angles:

- _shared._linearize_arc: 17 points along a three-point arc;
- _shared._courtyard_bbox_cst: a footprint's courtyard box in its own frame;
- pcb.get_footprint_bounds: that box on the board, at the footprint's orientation;
- pcb.place_footprint: the pad angles of a library footprint placed at a rotation.

Judges, none of them the code under test:

- geom_judge, exact rational geometry: the circle through an arc's three points, the
  arc's direction (the turn of the triangle they make, which runs the way the arc does
  for points on a circle), and the axis extremes the arc passes, decided by exact cross
  products.
- geom_gen.courtyard_box: the exact board box of a footprint's courtyard, each defining
  point turned by RotatePoint's double arithmetic (trigo.rotate_point_double) through
  the orientation KiCad reads from the file, Normalize180( parseDouble( angle ) )
  (FOOTPRINT::SetOrientation, see kicad_ref/board_item.py), and the shapes read back
  from the file by the lexer port (geom_gen.shapes_of), never by the CST.
- pcbnew: its own bounding box of each courtyard shape on the board, less half the
  stroke width, which test_ladder_r2_judges.py checks against courtyard_box.
- KiCad's source for pad angles. The parser reads a library pad's angle through
  PAD::SetOrientation (pcb_io_kicad_sexpr_parser.cpp:5124-5134 at 9.0.8, 6013-6023 at
  10.0.7), and PADSTACK::SetOrientation normalizes it into [0, 360) (padstack.h:294-298
  at 9.0.8, 348-352 at 10.0.7). FOOTPRINT::SetOrientation turns every pad by the new
  angle minus the old (footprint.cpp:2641-2669 at 9.0.8, 3099-3137 at 10.0.7), which for
  a library footprint, whose orientation is 0, is the rotation itself; PAD::Rotate adds
  it to the pad's angle through the same normalizing setter (pad.cpp:1614-1620 at 9.0.8,
  2183-2189 at 10.0.7). The writer leaves out a pad angle that IsZero() and spells any
  other through FormatAngle (pcb_io_kicad_sexpr.cpp:1492-1496 at 9.0.8, 1696-1700 at
  10.0.7; eda_units.format_angle). The bug 2 scout (2026-10-10) reproduced this rule
  token for token on 131 boards pcbnew 9.0.8 saved, and found it on 316 of 316 rotated
  pads in KiCad 9.0.8's demo boards.
"""

from __future__ import annotations

import math
import tempfile
import uuid
from fractions import Fraction
from pathlib import Path

import geom_gen as G
import geom_judge as GJ
import kicad_corpus as K
import pcbnew_judge as PJ
import pytest
import sexpr_gen
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import parse_units as P
from kicad_ref import trigo

from mcp_server_kicad import _cst, pcb
from mcp_server_kicad._shared import _courtyard_bbox_cst, _linearize_arc

MM = 1_000_000  # nanometres per millimetre


def _xfail(reason: str):
    """The one xfail the ladder allows: strict, and only for the bug's own assertion."""
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


_BUG27 = (
    "bug 27: get_footprint_bounds turns the corners of the courtyard's local box, which"
    " over-reports a rotated courtyard that is not an aligned rectangle; fixed by step 12c"
)
_BUG68 = (
    "bug 68: _courtyard_bbox_cst boxes 16 chords of each courtyard arc, so an extreme of"
    " the arc between two samples falls outside the reported box; fixed by step 12c"
)
_BUG_85 = (
    "bug 85: _courtyard_bbox_cst reads only the (xy ...) points of a courtyard"
    " fp_poly and drops the (arc ...) segments KiCad reads into the same outline;"
    " fixed by step 12c"
)


# ---------------------------------------------------------------------------
# reading files with the lexer port
# ---------------------------------------------------------------------------


def _head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _children(group, name: bytes) -> list:
    return [c for c in group.items if _head(c) == name]


def _child(group, name: bytes):
    found = _children(group, name)
    if not found:
        raise LookupError(f"no ({name.decode()} ...) in the list at byte {group.start}")
    return found[0]


def _top(data: bytes):
    """The first top-level list of ``data``, as the lexer port reads it."""
    return L.tree(data, knows_bar=L.knows_bar_of(data))[0]


def _footprint(board: Path, ref: str):
    """The (footprint ...) Group whose Reference property is ``ref``."""
    for fp in _children(_top(board.read_bytes()), b"footprint"):
        for prop in _children(fp, b"property"):
            if prop.items[1].value == b"Reference" and prop.items[2].value == ref.encode():
                return fp
    raise LookupError(f"no footprint {ref} on {board.name}")


def _placement(fp) -> tuple[tuple[int, int], float]:
    """A footprint's position (nm, parseBoardUnits) and the orientation KiCad stores for
    its (at): Normalize180( parseDouble( angle ) ), or 0 without an angle atom
    (kicad_ref/board_item.py's docstring cites the parser and FOOTPRINT::SetOrientation).
    Both majors read these values alike: they are written on the nanometre."""
    at = _child(fp, b"at")
    xy = (
        P.parse_board_units(at.items[1].value, major=10),
        P.parse_board_units(at.items[2].value, major=10),
    )
    angle = trigo.normalize180(P.parse_double(at.items[3].value)) if len(at.items) > 3 else 0.0
    return xy, angle


def _exact_board_box_mm(board: Path, ref: str) -> tuple[float, float, float, float]:
    """courtyard_box of footprint ``ref`` as KiCad reads the board, in millimetres."""
    fp = _footprint(board, ref)
    at, orientation = _placement(fp)
    return tuple(v / MM for v in G.courtyard_box(G.shapes_of(fp, major=10), orientation, at))


# ---------------------------------------------------------------------------
# shapes and boards
# ---------------------------------------------------------------------------


def _polar(radius_mm: float, degrees: float) -> tuple[int, int]:
    return (
        round(radius_mm * MM * math.cos(math.radians(degrees))),
        round(radius_mm * MM * math.sin(math.radians(degrees))),
    )


#: A circle of radius 5.25 mm about (1, 0.5) mm.
_CIRCLE = [("fp_circle", (MM, MM // 2), (MM + 5_250_000, MM // 2))]
#: A square of half-diagonal 3 mm standing on a corner.
_DIAMOND = [("fp_poly", [(0, -3 * MM), (3 * MM, 0), (0, 3 * MM), (-3 * MM, 0)])]
#: A half circle of radius 5 mm closed by its diameter; its extreme lies on the middle
#: one of the 17 samples, so the chords meet it at rotation 0.
_HALF_CIRCLE = [
    ("fp_arc", (-5 * MM, 0), (0, -5 * MM), (5 * MM, 0)),
    ("fp_line", (5 * MM, 0), (-5 * MM, 0)),
]
#: A 90-degree arc of radius 50 mm from 47.8125 to 137.8125 degrees: its extreme at 90
#: degrees falls halfway between two of the 16 equal steps, 50 * (1 - cos 2.8125) mm,
#: about 0.06 mm, beyond the nearest sample.
_HALF_STEP = (_polar(50, 47.8125), _polar(50, 92.8125), _polar(50, 137.8125))
_HALF_STEP_ARC = [
    ("fp_arc", *_HALF_STEP),
    ("fp_line", _HALF_STEP[2], _HALF_STEP[0]),
]

_STRAIGHT_AND_CIRCLES = ("line", "rect", "poly", "circle")


@st.composite
def _any_way_drawn(draw, shapes_strategy):
    """Courtyard shapes drawn every way KiCad stores them: a circle is its centre and any
    point on it (G.courtyard_shapes puts that point due east of the centre), and a
    rectangle is either diagonal, from either end (G.courtyard_shapes gives the
    low-x, low-y corner first)."""
    out = []
    for shape in draw(shapes_strategy):
        if shape[0] == "fp_circle":
            (cx, cy), (ex, ey) = shape[1], shape[2]
            radius = math.hypot(ex - cx, ey - cy)
            t = draw(st.floats(0.0, 2 * math.pi, exclude_max=True))
            end = (cx + round(radius * math.cos(t)), cy + round(radius * math.sin(t)))
            out.append(("fp_circle", (cx, cy), end))
        elif shape[0] == "fp_rect":
            (x1, y1), (x2, y2) = shape[1], shape[2]
            corners = draw(
                st.sampled_from(
                    (
                        ((x1, y1), (x2, y2)),
                        ((x2, y2), (x1, y1)),
                        ((x1, y2), (x2, y1)),
                        ((x2, y1), (x1, y2)),
                    )
                )
            )
            out.append(("fp_rect", *corners))
        else:
            out.append(shape)
    return out


def _courtyards(kinds=_STRAIGHT_AND_CIRCLES, max_shapes: int = 1):
    return _any_way_drawn(G.courtyard_shapes(kinds=kinds, max_shapes=max_shapes))


@st.composite
def _quadrant_arcs(draw, span: int = 10 * MM):
    """(start, mid, end) in nm of an arc of radius 1 mm or more that stays inside one
    quadrant of its circle, either way round. It passes none of its circle's four axis
    extremes, so its box is the box of its two ends, which a polyline through its ends
    reaches: the domain where 16 chords are right, for passing siblings of the bug 68
    tests."""
    cx, cy = draw(st.integers(-span, span)), draw(st.integers(-span, span))
    radius = draw(st.integers(MM, span))
    quadrant = draw(st.integers(0, 3)) * math.pi / 2
    low = draw(st.floats(0.05, math.pi / 2 - 0.35))
    high = draw(st.floats(low + 0.3, math.pi / 2 - 0.05))
    a, b = quadrant + low, quadrant + high
    if draw(st.booleans()):
        a, b = b, a
    points = tuple(
        (cx + round(radius * math.cos(t)), cy + round(radius * math.sin(t)))
        for t in (a, (a + b) / 2, b)
    )
    assume(len(set(points)) == 3 and GJ.orient(*points) != 0)
    assume(len(GJ.arc_extremes(*points)) == 2)
    return points


def _with_a_quadrant_arc(shapes_strategy):
    """``shapes_strategy``'s shapes, sometimes with one more outline: a quadrant arc
    closed by its chord."""
    return st.tuples(shapes_strategy, st.lists(_quadrant_arcs(), max_size=1)).map(
        lambda pair: (
            pair[0]
            + [shape for s, m, e in pair[1] for shape in (("fp_arc", s, m, e), ("fp_line", e, s))]
        )
    )


#: Angle atoms that KiCad reads as a quarter turn (None: no angle atom). KiCad itself
#: writes Normalize180 of the angle and leaves out 0; the others are spellings it reads
#: the same way, as place_footprint wrote them until step 7 (270, an explicit 0: bug 53).
_QUARTER_TOKENS = (None, "0", "90", "180", "-180", "270", "-90", "360", "-270", "450")


def _write_board(path: Path, footprints, *, version: str = "k9") -> Path:
    path.write_text(G.board_text(footprints, version=version), encoding="utf-8", newline="\n")
    return path


def _with_angle_token(text: str, at_nm: tuple[int, int], token: str | None) -> str:
    """A footprint_text written at angle 0 (no angle atom), its own (at) given ``token``."""
    plain = f"\n\t\t(at {G.mm(at_nm[0])} {G.mm(at_nm[1])})\n"
    if text.count(plain) != 1:
        raise RuntimeError(f"the footprint text holds {plain!r} {text.count(plain)} times")
    if token is None:
        return text
    return text.replace(plain, f"\n\t\t(at {G.mm(at_nm[0])} {G.mm(at_nm[1])} {token})\n")


def _tool_box(board: Path, ref: str) -> tuple[float, float, float, float, float, float]:
    courtyard = pcb.get_footprint_bounds(ref, pcb_path=str(board)).courtyard
    assert courtyard is not None, f"get_footprint_bounds found no courtyard on {ref}"
    return tuple(courtyard[k] for k in ("min_x", "min_y", "max_x", "max_y", "width", "height"))


#: get_footprint_bounds rounds each number to 4 decimals of a millimetre, so a right
#: answer lies within half of that of the exact box; 1e-9 mm covers the float error of
#: turning the points and of the conversions.
_BOUNDS_TOLERANCE_MM = 0.5e-4 + 1e-9


def _bounds_problem(board: Path, ref: str) -> str | None:
    """Why get_footprint_bounds of ``ref`` is not the exact board box, or None."""
    tool = _tool_box(board, ref)
    x0, y0, x1, y1 = _exact_board_box_mm(board, ref)
    exact = (x0, y0, x1, y1, x1 - x0, y1 - y0)
    worst = max(abs(t - e) for t, e in zip(tool, exact))
    if worst <= _BOUNDS_TOLERANCE_MM:
        return None
    fp = _footprint(board, ref)
    at = _child(fp, b"at")
    written = b" ".join(t.value for t in at.items[1:]).decode()
    return (
        f"{ref} at ({written}): get_footprint_bounds {tool}, exact {exact}, off by {worst:.6f} mm"
    )


# ---------------------------------------------------------------------------
# _linearize_arc
# ---------------------------------------------------------------------------

#: How far a point of the linearized arc may sit from the exact arc: a tenth of a
#: nanometre, KiCad's resolution being one. Double arithmetic puts the centre of a
#: 0.1 mm arc 140 mm from the origin 1.09e-9 mm off (rung 2 package D probe over
#: G.arcs' own domain, 2026-10-11), so 1e-9 mm is finer than the arithmetic holds there.
_ARC_TOLERANCE_MM = 1e-7


@st.composite
def _collinear_triples(draw):
    """Three distinct collinear points in nm, the middle one strictly between the others,
    all within +-150 mm like the points G.arcs draws."""
    x0, y0 = draw(st.integers(-100 * MM, 100 * MM)), draw(st.integers(-100 * MM, 100 * MM))
    dx, dy = draw(st.integers(-1000, 1000)), draw(st.integers(-1000, 1000))
    assume((dx, dy) != (0, 0))
    k, m = draw(st.integers(1, 25_000)), draw(st.integers(1, 25_000))
    return (x0, y0), (x0 + k * dx, y0 + k * dy), (x0 + (k + m) * dx, y0 + (k + m) * dy)


def _mm_args(points) -> tuple[float, float, float, float, float, float]:
    """Three points in nm as _linearize_arc's six arguments, in millimetres."""
    (sx, sy), (mx, my), (ex, ey) = points
    return sx / MM, sy / MM, mx / MM, my / MM, ex / MM, ey / MM


@settings(max_examples=sexpr_gen.budget())
@given(G.arcs(), _collinear_triples())
@example(arc=_HALF_STEP, line=((0, 0), (MM, MM), (3 * MM, 3 * MM)))
@example(arc=((-5 * MM, 0), (0, 5 * MM), (5 * MM, 0)), line=((0, 0), (0, 7), (0, 11)))
def test_linearize_arc_lies_on_its_circle(arc, line):
    """For an arc through three integer points (read in millimetres, as the tools read
    them), _linearize_arc returns 17 points: the first and last on the arc's ends, all
    on the exact circle through the three points (geom_judge.circle_through), each step
    turning the way the arc runs from its start through its middle (the sign of the
    exact turn of the three points), and the steps adding up to the arc's exact sweep.
    For three collinear points it returns the three points as given."""
    start, mid, end = arc
    points = _linearize_arc(*_mm_args(arc))
    assert len(points) == 17, len(points)
    cx, cy, r2 = GJ.circle_through(start, mid, end)
    centre = (float(cx) / MM, float(cy) / MM)
    radius = math.sqrt(r2) / MM
    for got, want in ((points[0], start), (points[-1], end)):
        off = math.dist(got, (want[0] / MM, want[1] / MM))
        assert off <= _ARC_TOLERANCE_MM, (arc, got, want, off)
    worst = max(abs(math.dist(p, centre) - radius) for p in points)
    assert worst <= _ARC_TOLERANCE_MM, (arc, worst)
    turn = GJ.orient(start, mid, end)
    angles = [math.atan2(y - centre[1], x - centre[0]) for x, y in points]
    steps = [(b - a + math.pi) % (2 * math.pi) - math.pi for a, b in zip(angles, angles[1:])]
    assert all(step * turn > 0 for step in steps), (arc, turn, steps)
    vs = (Fraction(start[0]) - cx, Fraction(start[1]) - cy)
    ve = (Fraction(end[0]) - cx, Fraction(end[1]) - cy)
    cross = float(vs[0] * ve[1] - vs[1] * ve[0])
    dot = float(vs[0] * ve[0] + vs[1] * ve[1])
    sweep = math.atan2(turn * cross, dot) % (2 * math.pi)
    assert abs(sum(steps) - turn * sweep) <= 1e-6, (arc, sum(steps), turn * sweep)
    if GJ.orient(*line) != 0:
        raise RuntimeError(f"_collinear_triples drew {line}, which is not collinear")
    flat = _mm_args(line)
    assert _linearize_arc(*flat) == [(flat[0], flat[1]), (flat[2], flat[3]), (flat[4], flat[5])]


# ---------------------------------------------------------------------------
# _courtyard_bbox_cst, in the footprint's own frame
# ---------------------------------------------------------------------------


def _local_box(shapes) -> tuple[dict | None, list]:
    """(_courtyard_bbox_cst of a footprint holding ``shapes`` in its own frame, the
    shapes the lexer port reads back from the same text)."""
    text = G.footprint_text("U1", (0, 0), 0.0, shapes=shapes)
    box = _courtyard_bbox_cst(_cst.parse(text.encode()).lists[0])
    return box, G.shapes_of(_top(text.encode()), major=10)


@settings(max_examples=sexpr_gen.budget())
@given(_courtyards(max_shapes=3))
def test_courtyard_box_of_straight_shapes_and_circles_is_exact(shapes):
    """For lines, rectangles, polygons and circles (one to three closed outlines, which
    may overlap), _courtyard_bbox_cst is the exact box of the courtyard in the
    footprint's own frame, within 1e-9 mm, on the layer they are drawn on."""
    box, read = _local_box(shapes)
    assert box is not None and box["layer"] == "F.CrtYd", box
    exact = tuple(v / MM for v in G.courtyard_box(read))
    got = (box["min_x"], box["min_y"], box["max_x"], box["max_y"], box["width"], box["height"])
    want = (*exact, exact[2] - exact[0], exact[3] - exact[1])
    assert max(abs(g - w) for g, w in zip(got, want)) <= 1e-9, (shapes, got, want)


@_xfail(_BUG68)
@settings(max_examples=sexpr_gen.budget())
@given(G.arcs())
@example(arc=_HALF_STEP)
def test_courtyard_box_of_an_arc_reaches_its_extremes(arc):
    """For a courtyard of one arc, _courtyard_bbox_cst holds every point the arc's box is
    made of (its ends and each axis extreme its sweep passes, geom_judge.arc_extremes)
    and stays inside that box, within 1e-7 mm. The half-step arc's extreme lies
    0.06 mm past the chords."""
    box, read = _local_box([("fp_arc", *arc)])
    assert box is not None, arc
    ((kind, *points),) = read
    if kind != "fp_arc":
        raise RuntimeError(f"the lexer port read back {read}, not the arc written")
    extremes = [(x / MM, y / MM) for x, y in GJ.arc_extremes(*points)]
    x0, y0, x1, y1 = GJ.bbox(extremes)
    tol = _ARC_TOLERANCE_MM
    assert box["min_x"] >= x0 - tol and box["max_x"] <= x1 + tol, (arc, box, (x0, x1))
    assert box["min_y"] >= y0 - tol and box["max_y"] <= y1 + tol, (arc, box, (y0, y1))
    missed = [
        (x, y)
        for x, y in extremes
        if not (box["min_x"] - tol <= x <= box["max_x"] + tol)
        or not (box["min_y"] - tol <= y <= box["max_y"] + tol)
    ]
    assert not missed, f"{arc}: the box {box} misses the arc's extremes {missed}"


@settings(max_examples=sexpr_gen.budget())
@given(_quadrant_arcs())
@example(arc=(_polar(5, -80), _polar(5, -45), _polar(5, -10)))
def test_courtyard_box_of_an_arc_inside_a_quadrant_is_exact(arc):
    """For a courtyard of one arc that stays inside a quadrant of its circle,
    _courtyard_bbox_cst is the arc's exact box (geom_judge.arc_bbox, here the box of its
    ends) within 1e-7 mm: the sibling of the bug 68 test above, over the arcs whose box
    16 chords do reach."""
    box, read = _local_box([("fp_arc", *arc)])
    assert box is not None, arc
    ((kind, *points),) = read
    if kind != "fp_arc" or len(GJ.arc_extremes(*points)) != 2:
        raise RuntimeError(f"the lexer port read back {read}, not a quadrant arc")
    want = tuple(v / MM for v in GJ.arc_bbox(*points))
    got = (box["min_x"], box["min_y"], box["max_x"], box["max_y"])
    assert max(abs(g - w) for g, w in zip(got, want)) <= _ARC_TOLERANCE_MM, (arc, got, want)


def _poly_with_arcs_text(items, *, depth: int = 2) -> str:
    """An fp_poly on F.CrtYd whose (pts ...) holds ``items`` in order, each ("xy", point)
    or ("arc", start, mid, end) in nm, spelled as formatPolyPts spells them
    (pcb_io_kicad_sexpr.cpp:466-498 at 9.0.8, 489-521 at 10.0.7) and laid out one item to
    a line, as in the zones of KiCad 9.0.8's stock RaspberryPi_Pico_Common_SMD."""
    t = "\t" * depth
    body = []
    for item in items:
        if item[0] == "xy":
            body.append(f"{t}\t\t(xy {G.mm(item[1][0])} {G.mm(item[1][1])})\n")
            continue
        ends = zip(("start", "mid", "end"), item[1:])
        body.append(
            f"{t}\t\t(arc\n"
            + "".join(f"{t}\t\t\t({name} {G.mm(p[0])} {G.mm(p[1])})\n" for name, p in ends)
            + f"{t}\t\t)\n"
        )
    return (
        f"{t}(fp_poly\n{t}\t(pts\n{''.join(body)}{t}\t)\n"
        f"{t}\t(stroke\n{t}\t\t(width 0.05)\n{t}\t\t(type solid)\n{t}\t)\n"
        f'{t}\t(fill no)\n{t}\t(layer "F.CrtYd")\n{t}\t(uuid "{uuid.uuid4()}")\n{t})\n'
    )


def _outline_items(fp) -> list[tuple]:
    """The ("xy", point) and ("arc", start, mid, end) items of a footprint's fp_poly
    outlines, in file order, read by the lexer port as parseOutlinePoints reads them
    (pcb_io_kicad_sexpr_parser.cpp:316-398 at 9.0.8, 389-471 at 10.0.7), in nm."""

    def point(group) -> tuple[int, int]:
        return (
            P.parse_board_units(group.items[1].value, major=10),
            P.parse_board_units(group.items[2].value, major=10),
        )

    out: list[tuple] = []
    for poly in _children(fp, b"fp_poly"):
        for item in _child(poly, b"pts").items:
            if _head(item) == b"xy":
                out.append(("xy", point(item)))
            elif _head(item) == b"arc":
                out.append(
                    ("arc", *(point(_child(item, name)) for name in (b"start", b"mid", b"end")))
                )
    return out


@st.composite
def _outlines_with_arcs(draw):
    """The items of one polygon outline: one or two arcs and up to three straight points,
    in any order (KiCad joins each item to the next by a straight edge, so any order is an
    outline, and its box is the box of its points and of its arcs)."""
    arcs = draw(
        st.lists(G.arcs(min_radius=MM, max_radius=20 * MM, span=20 * MM), min_size=1, max_size=2)
    )
    corners = st.tuples(st.integers(-30 * MM, 30 * MM), st.integers(-30 * MM, 30 * MM))
    points = draw(st.lists(corners, max_size=3))
    return draw(st.permutations([("arc", *a) for a in arcs] + [("xy", p) for p in points]))


@_xfail(_BUG_85)
@settings(max_examples=sexpr_gen.budget())
@given(_outlines_with_arcs())
@example(
    items=[
        ("xy", (-5 * MM, 5 * MM)),
        ("xy", (5 * MM, 5 * MM)),
        ("arc", (5 * MM, 0), (0, -5 * MM), (-5 * MM, 0)),
    ]
)
def test_courtyard_box_of_a_polygon_with_arcs_is_exact(items):
    """A courtyard fp_poly whose outline holds arc segments: KiCad's parser appends each
    (arc (start) (mid) (end)) of its (pts ...) to the outline as an arc
    (parseOutlinePoints, pcb_io_kicad_sexpr_parser.cpp:335-388 at 9.0.8, 408-461 at
    10.0.7; read for fp_poly at 3075-3103 and 3594-3622), and its writer emits one for any
    polygon whose outline holds an arc (formatPolyPts, called for footprint shapes at
    pcb_io_kicad_sexpr.cpp:978 at 9.0.8, 1047 at 10.0.7). So _courtyard_bbox_cst must be
    the box of the polygon's points and its arcs (geom_judge.arc_extremes), within
    1e-7 mm. pcbnew 9.0.8 loads 120 such generated outlines, keeps every arc as an arc,
    and boxes each shape within 0.56 um of that, where get_footprint_bounds misses a
    50 mm arc by 16.4 mm and reports the explicit example's half circle as a line (rung 2
    package D probe, 2026-10-11). No stock courtyard holds one, though KiCad 9.0.8's stock
    footprints do write arc segments in zone outlines and pad primitives."""
    text = G.footprint_text("U1", (0, 0), 0.0)
    close = text.rindex("\t)\n")
    text = text[:close] + _poly_with_arcs_text(items) + text[close:]
    read = _outline_items(_top(text.encode()))
    if read != list(items):
        raise RuntimeError(f"the lexer port read back {read}, not the outline written")
    box = _courtyard_bbox_cst(_cst.parse(text.encode()).lists[0])
    assert box is not None, f"no courtyard box for the outline {items}"
    points = [item[1] for item in read if item[0] == "xy"]
    points += [p for item in read if item[0] == "arc" for p in GJ.arc_extremes(*item[1:])]
    want = tuple(v / MM for v in GJ.bbox(points))
    got = (box["min_x"], box["min_y"], box["max_x"], box["max_y"])
    worst = max(abs(g - w) for g, w in zip(got, want))
    assert worst <= _ARC_TOLERANCE_MM, f"{items}: box {got}, exact {want}"


# ---------------------------------------------------------------------------
# get_footprint_bounds, on the board
# ---------------------------------------------------------------------------


def _one_footprint_bounds_problem(shapes, at, text_angle, token, version) -> str | None:
    """Write one footprint holding ``shapes`` at ``at`` (nm) to a fresh board and return
    _bounds_problem for it. ``text_angle`` is the angle footprint_text writes (KiCad's
    own spelling); ``token``, when not ``...``, replaces it with a raw angle atom."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        text = G.footprint_text("U1", at, text_angle, shapes=shapes, version=version)
        if token is not ...:
            text = _with_angle_token(text, at, token)
        board = _write_board(Path(work) / "bounds.kicad_pcb", [text], version=version)
        return _bounds_problem(board, "U1")


_AT = st.tuples(G.nm_coordinates(500 * MM), G.nm_coordinates(500 * MM))
_VERSIONS = st.sampled_from(("k9", "k10"))


@settings(max_examples=sexpr_gen.budget(4))
@given(
    _with_a_quadrant_arc(_courtyards(max_shapes=2)),
    _AT,
    st.sampled_from(_QUARTER_TOKENS),
    _VERSIONS,
)
@example(shapes=_CIRCLE, at=(50 * MM, 40 * MM), token="90", version="k9")
@example(shapes=_DIAMOND, at=(0, 0), token="270", version="k10")
@example(shapes=_HALF_CIRCLE, at=(-20 * MM, 30 * MM), token="-90", version="k9")
def test_footprint_bounds_at_quarter_turns_are_exact(shapes, at, token, version):
    """Lines, rectangles, polygons and circles, and sometimes an arc inside one quadrant
    of its circle, on a footprint at a quarter turn written any way KiCad reads as one
    (no angle, 0, 90, 180, -180, 270, -90, 360, -270, 450), on a KiCad 9 or a KiCad 10
    board: get_footprint_bounds is the exact board box of the turned courtyard, within
    half its 1e-4 mm resolution. A quarter turn maps the box of the courtyard onto the
    box of the turned courtyard, so turning corners is right here, and so are 16 chords
    of such an arc: the sibling of the bug 27 and bug 68 tests below."""
    problem = _one_footprint_bounds_problem(shapes, at, 0.0, token, version)
    assert problem is None, problem


@_xfail(_BUG27)
@settings(max_examples=sexpr_gen.budget(4))
@given(_courtyards(max_shapes=2), _AT, G.angles(), _VERSIONS)
@example(shapes=_CIRCLE, at=(50 * MM, 50 * MM), angle=45.0, version="k9")
@example(shapes=_DIAMOND, at=(50 * MM, 50 * MM), angle=45.0, version="k9")
def test_footprint_bounds_of_rotated_shapes_are_exact(shapes, at, angle, version):
    """The same at any angle, the footprint's angle written as KiCad writes it
    (FormatAngle of Normalize180, left out at 0), no arcs (they are bug 68's): a circle
    of radius 5.25 mm at 45 degrees is reported 2.17 mm too large on each side, the
    diamond 2.12 mm (rung 2 design probe M3, 2026-10-10)."""
    problem = _one_footprint_bounds_problem(shapes, at, angle, ..., version)
    assert problem is None, problem


@_xfail(_BUG68)
@settings(max_examples=sexpr_gen.budget(4))
@given(
    G.courtyard_shapes(kinds=("arc",), max_shapes=2),
    _AT,
    st.sampled_from(_QUARTER_TOKENS),
    _VERSIONS,
)
@example(shapes=_HALF_STEP_ARC, at=(0, 0), token=None, version="k9")
def test_footprint_bounds_of_arcs_are_exact(shapes, at, token, version):
    """Courtyards of arcs closed by their chords, at quarter turns (so bug 27 stays out):
    get_footprint_bounds is the exact board box. The half-step arc at 0 is reported
    0.06 mm short at its extreme."""
    problem = _one_footprint_bounds_problem(shapes, at, 0.0, token, version)
    assert problem is None, problem


# ---------------------------------------------------------------------------
# get_footprint_bounds against pcbnew
# ---------------------------------------------------------------------------

_SHAPE_BOXES = r"""
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
        result[fp.GetReference()] = shapes
"""
#: How far pcbnew's own box of a courtyard shape may lie from the exact one, in nm: 3 for
#: straight shapes and circles (RotatePoint rounds each point to the nanometre, and a
#: circle's radius is rounded when it is boxed), 125 when an arc is present (CalcArcCenter
#: moves an arc's centre to a multiple of 10 or 100 nm within its own error estimate,
#: which moves the radius too). These are the bounds test_ladder_r2_judges.py states and
#: checks against pcbnew for courtyard_box (test_courtyard_judge_matches_pcbnew), with
#: their sources.
_PCBNEW_SHAPE_NM = 3
_PCBNEW_ARC_NM = 125
#: Half of get_footprint_bounds' 1e-4 mm resolution, plus a nanometre for float error.
_TOOL_NM = 50 + 1


def _pcbnew_problem(board: Path, ref: str, shapes, boxes) -> str | None:
    """Why get_footprint_bounds of ``ref`` is not pcbnew's box of the same courtyard: the
    union of pcbnew's per-shape boxes less half each stroke, within the tool's resolution
    plus pcbnew's own distance from the exact box."""
    if not boxes:
        raise RuntimeError(f"pcbnew found no courtyard shapes on {ref}")
    kicad = (
        min(b[0] + b[4] // 2 for b in boxes),
        min(b[1] + b[4] // 2 for b in boxes),
        max(b[2] - b[4] // 2 for b in boxes),
        max(b[3] - b[4] // 2 for b in boxes),
    )
    has_arc = any(shape[0] == "fp_arc" for shape in shapes)
    tolerance = _TOOL_NM + (_PCBNEW_ARC_NM if has_arc else _PCBNEW_SHAPE_NM)
    tool = _tool_box(board, ref)[:4]
    worst = max(abs(t * MM - k) for t, k in zip(tool, kicad))
    if worst <= tolerance:
        return None
    return (
        f"{ref}: get_footprint_bounds {tool} mm, pcbnew {tuple(k / MM for k in kicad)} mm,"
        f" off by {worst / MM:.6f} mm (tolerance {tolerance} nm)"
    )


def _battery_holder() -> Path | None:
    for share in K.share_dirs():
        path = share / "footprints" / "Battery.pretty" / "BatteryHolder_Keystone_500.kicad_mod"
        if path.is_file():
            return path
    return None


def _hand_picked_courtyards() -> dict[str, tuple[list, float]]:
    cases = {
        "C0": (_CIRCLE, 0.0),
        "H0": (_HALF_CIRCLE, 0.0),
        "C45": (_CIRCLE, 45.0),
        "D45": (_DIAMOND, 45.0),
        "H30": (_HALF_CIRCLE, 30.0),
        "A0": (_HALF_STEP_ARC, 0.0),
    }
    battery = _battery_holder()
    if battery is not None:
        cases["B0"] = (G.shapes_of(_top(battery.read_bytes()), major=10), 0.0)
    return cases


@pytest.fixture(scope="module")
def pcbnew_courtyards(tmp_path_factory):
    """One board holding every hand-picked courtyard, and pcbnew's per-shape boxes of
    each: one pcbnew run per worker for all the parameters below."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    cases = _hand_picked_courtyards()
    board = _write_board(
        tmp_path_factory.mktemp("courtyards") / "courtyards.kicad_pcb",
        [
            G.footprint_text(ref, (50 * MM + 60 * MM * i, 40 * MM), angle, shapes=shapes)
            for i, (ref, (shapes, angle)) in enumerate(cases.items())
        ],
    )
    return board, cases, PJ.run(_SHAPE_BOXES, {"boards": [str(board)]})


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("C0", id="circle-at-0"),
        pytest.param("H0", id="half-circle-at-0"),
        pytest.param("C45", id="circle-at-45", marks=_xfail(_BUG27)),
        pytest.param("D45", id="diamond-at-45", marks=_xfail(_BUG27)),
        # Once the turn is right, this half circle's extremes at 30 degrees still fall
        # between its chords (bug 68); both are step 12c's.
        pytest.param("H30", id="half-circle-at-30", marks=_xfail(_BUG27)),
        pytest.param("A0", id="half-step-arc-at-0", marks=_xfail(_BUG68)),
        pytest.param("B0", id="stock-battery-holder-at-0", marks=_xfail(_BUG68)),
    ],
)
def test_footprint_bounds_match_pcbnew(request, ref):
    """get_footprint_bounds is pcbnew's box of the same courtyard (its per-shape boxes,
    less half the stroke), within half the tool's 1e-4 mm resolution plus pcbnew's own
    distance from the exact box: a circle and a half circle closed by a line at 0; a
    circle, a diamond at 45 and the half circle at 30 (bug 27); the half-step arc and the
    stock BatteryHolder_Keystone_500, whose arcs reach past their chords, at 0 (bug 68;
    placed by place_footprint the holder is 0.0954 mm short where pcbnew is within
    0.0075 mm, rung 2 design probe M5, 2026-10-10)."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    if ref == "B0" and _battery_holder() is None:
        pytest.skip("no stock footprint libraries: Battery.pretty is not installed")
    board, cases, found = request.getfixturevalue("pcbnew_courtyards")
    if ref == "B0" and not any(shape[0] == "fp_arc" for shape in cases[ref][0]):
        raise RuntimeError("the installed BatteryHolder_Keystone_500 has no courtyard arc")
    problem = _pcbnew_problem(board, ref, cases[ref][0], found[ref])
    assert problem is None, f"pcbnew {PJ.major()}: {problem}"


def _check_bounds_against_pcbnew(placed, work: Path) -> None:
    """``placed`` is a list of (shapes, footprint text angle, raw token or ...): one board
    with a footprint per entry, one pcbnew run, every footprint judged."""
    items, cases = [], {}
    for i, (shapes, angle, token) in enumerate(placed):
        ref, at = f"U{i + 1}", (60 * MM * (i + 1), 60 * MM)
        text = G.footprint_text(ref, at, angle, shapes=shapes)
        items.append(text if token is ... else _with_angle_token(text, at, token))
        cases[ref] = shapes
    board = _write_board(work / "bounds.kicad_pcb", items)
    found = PJ.run(_SHAPE_BOXES, {"boards": [str(board)]})
    wrong = [
        p for ref, shapes in cases.items() if (p := _pcbnew_problem(board, ref, shapes, found[ref]))
    ]
    assert not wrong, f"pcbnew {PJ.major()}:\n" + "\n".join(wrong)


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(_with_a_quadrant_arc(_courtyards()), st.sampled_from(_QUARTER_TOKENS)),
        min_size=1,
        max_size=8,
    )
)
def test_nightly_footprint_bounds_match_pcbnew_at_quarter_turns(courtyards):
    """Generated lines, rectangles, polygons and circles, and sometimes an arc inside one
    quadrant of its circle, at quarter turns: the tool's box is pcbnew's."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        _check_bounds_against_pcbnew([(s, 0.0, token) for s, token in courtyards], Path(work))


@pytest.mark.nightly
@_xfail(_BUG27)
@settings(max_examples=sexpr_gen.budget(25))
@given(
    st.lists(
        st.tuples(_courtyards(), G.angles()),
        min_size=1,
        max_size=8,
    )
)
@example(courtyards=[(_CIRCLE, 45.0)])
def test_nightly_rotated_footprint_bounds_match_pcbnew(courtyards):
    """Generated lines, rectangles, polygons and circles at any angle, written as KiCad
    writes it: the tool's box is pcbnew's."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        _check_bounds_against_pcbnew([(s, angle, ...) for s, angle in courtyards], Path(work))


# ---------------------------------------------------------------------------
# pad angles of a placed footprint
# ---------------------------------------------------------------------------


def _pad_angle_tokens(fp) -> list[tuple[str, str | None]]:
    """(number, angle atom or None) of each pad of a footprint Group, in file order."""
    out = []
    for pad in _children(fp, b"pad"):
        at = _child(pad, b"at")
        out.append(
            (pad.items[1].value.decode(), at.items[3].value.decode() if len(at.items) > 3 else None)
        )
    return out


def _kicads_pad_angle(library_token: str | None, rotation: float) -> str | None:
    """The angle atom KiCad writes for a library pad whose angle atom is
    ``library_token`` once its footprint is placed at ``rotation``: the parser's
    Normalize of the library angle, plus the rotation, normalized again; left out at 0,
    else FormatAngle (module docstring)."""
    own = trigo.normalize(P.parse_double(library_token)) if library_token is not None else 0.0
    angle = trigo.normalize(own + rotation)
    return None if angle == 0 else U.format_angle(angle)


def _pad_angle_problems(library: Path, board: Path, rotation: float) -> list[str]:
    """Each pad of the one footprint on ``board`` against the pad of the same place in
    the library file it was placed from. A board without exactly that footprint, or
    with other pads, raises RuntimeError: that is not the angle defect judged here, and
    must not pass for it."""
    lib = _pad_angle_tokens(_top(library.read_bytes()))
    footprints = _children(_top(board.read_bytes()), b"footprint")
    if len(footprints) != 1:
        raise RuntimeError(f"{board.name} holds {len(footprints)} footprints, not one")
    placed = _pad_angle_tokens(footprints[0])
    if [n for n, _ in placed] != [n for n, _ in lib]:
        raise RuntimeError(f"place_footprint wrote the pads {placed}, not the library's {lib}")
    problems = []
    for (number, own), (_, written) in zip(lib, placed):
        want = _kicads_pad_angle(own, rotation)
        if written != want:
            problems.append(
                f"pad {number!r}: library angle {own}, footprint at {rotation}:"
                f" written {written}, KiCad writes {want}"
            )
    return problems


def _place_from_generated_library(pads, rotation: float, version: str) -> list[str]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        pretty = G.pretty_library(work, "Pads", pads)
        board = _write_board(Path(work) / "pads.kicad_pcb", [], version=version)
        pcb.place_footprint(
            "U1",
            "X",
            100,
            80,
            rotation=rotation,
            library=str(pretty),
            footprint="Pads",
            pcb_path=str(board),
        )
        return _pad_angle_problems(pretty / "Pads.kicad_mod", board, rotation)


def _pads(own_angles):
    """Library pads with distinct numbers, at distinct places 1 mm apart, at ``own_angles``."""
    return own_angles.map(
        lambda angles: [(str(k + 1), (MM * k, -MM * (k % 2)), a) for k, a in enumerate(angles)]
    )


@settings(max_examples=sexpr_gen.budget(4))
@given(
    _pads(st.lists(G.half_degree_angles(), min_size=1, max_size=4)),
    G.half_degree_angles(),
    _VERSIONS,
)
@example(pads=[("1", (0, 0), 270.0)], rotation=90.0, version="k9")
def test_pad_angles_compose_with_the_footprint_rotation(pads, rotation, version):
    """A library footprint whose pads have their own angles (multiples of half a degree,
    so the arithmetic is exact and no float noise in the text, bug 61, can enter), placed
    by place_footprint at a rotation (a multiple of half a degree within +-720): every
    pad's angle atom is what KiCad writes, Normalize( Normalize( own ) + rotation ),
    left out at 0. A pad at 270 on a footprint placed at 90 has no angle atom. Until
    step 7 every pad was written at the footprint's rotation (bug 2)."""
    problems = _place_from_generated_library(pads, rotation, version)
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@given(_pads(st.lists(st.just(0.0), min_size=1, max_size=4)), G.half_degree_angles(), _VERSIONS)
@example(pads=[("1", (0, 0), 0.0)], rotation=-90.0, version="k9")
@example(pads=[("1", (0, 0), 0.0), ("2", (MM, 0), 0.0)], rotation=720.0, version="k10")
def test_pad_angles_of_unrotated_pads_take_the_footprint_rotation(pads, rotation, version):
    """Library pads with no angle of their own, placed at a rotation (a multiple of half a
    degree within +-720): every pad's angle atom is FormatAngle( Normalize( rotation ) ),
    left out when that is 0 (the sibling of the bug 2 test above)."""
    problems = _place_from_generated_library(pads, rotation, version)
    assert not problems, "\n".join(problems)


_AO_DFN = "AO_DFN-8-1EP_5.55x5.2mm_P1.27mm_EP4.12x4.6mm"


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_pad_angles_compose_on_a_stock_footprint(tmp_path, version):
    """KiCad's stock AO_DFN-8-1EP_5.55x5.2mm_P1.27mm_EP4.12x4.6mm, whose 25 pads stand at
    270 degrees in KiCad 9.0.8's library, placed at 90: every pad's angle atom is the one
    KiCad writes, which for 270 + 90 is none at all (pcbnew 9.0.8 placing it at 90 writes
    no pad angle, measured in the rungs 1 and 2 inventory, 2026-10-10). The expected
    atoms come from the installed library's own tokens, so another release's copy of the
    footprint is judged by the same rule."""
    libraries = [
        share / "footprints" / "Package_DFN_QFN.pretty"
        for share in K.share_dirs()
        if (share / "footprints" / "Package_DFN_QFN.pretty" / f"{_AO_DFN}.kicad_mod").is_file()
    ]
    if not libraries:
        pytest.skip(f"no stock footprint libraries: {_AO_DFN} is not installed")
    library = libraries[0]
    own = [
        token
        for _, token in _pad_angle_tokens(_top((library / f"{_AO_DFN}.kicad_mod").read_bytes()))
    ]
    if not any(token is not None and P.parse_double(token) != 0 for token in own):
        raise RuntimeError(f"no pad of the installed {_AO_DFN} has an angle of its own")
    board = _write_board(tmp_path / "ao_dfn.kicad_pcb", [], version=version)
    pcb.place_footprint(
        "U1",
        "AO",
        100,
        80,
        rotation=90,
        library=str(library),
        footprint=_AO_DFN,
        pcb_path=str(board),
    )
    problems = _pad_angle_problems(library / f"{_AO_DFN}.kicad_mod", board, 90.0)
    assert not problems, f"{len(problems)} pads:\n" + "\n".join(problems)
