"""Rung 2: points in polygons, distances, board outlines and keepouts.

Code under test: _shared._point_in_polygon (float ray casting, behind every keepout and
board-edge answer); the routing model's exact integer geometry in _connectivity
(_in_poly, _within, _side, _near_poly, _seg_poly); _shared._chain_edge_polygon and
pcb._edge_polygon_cst (the board edge); and the read tools built on them,
check_placement and validate_board, with pcb._keepout_violations_cst, _zone_pts and
_zone_forbids_footprints.

Judges, none of them the code under test:

- geom_judge's exact rational geometry: a winding number (inside), squared distances,
  orientation by a cofactor expansion, and parity over closed contours;
- math: a convex polygon cut along a chord is partitioned by its two parts, so a point
  inside it or on the chord lies in exactly one part, which settles points on a shared
  boundary without any convention; orientation is antisymmetric, cyclic and unchanged by
  translation; whether a point is inside is unchanged by translating polygon and point,
  rotating the vertex list or reversing it;
- KiCad's board outline (pcbnew/convert_shape_list_to_polygon.cpp:524-594 at 9.0.8;
  buildContourHierarchy, addOutlinesToPolygon and addHolesToPolygon, 392-487 at
  10.0.7): every closed Edge.Cuts contour counts, whatever its kind and its place in the
  file, and a point is on the board when an odd number of contours hold it
  (geom_judge.parity); shape ends chain when they lie within 0.01 mm of each other
  (close_enough with DEFAULT_CHAINING_EPSILON_MM, pcbnew/board.h:85 at 9.0.8, 92 at
  10.0.7); curves are polygonized within the board's maximum error, ARC_HIGH_DEF =
  0.005 mm by default (include/base_units.h:117 and 127 at 9.0.8, 119 and 129 at
  10.0.7). Where pcbnew is installed, its GetBoardPolygonOutlines judges directly;
- KiCad's file rule for a zone a footprint owns: its points are board coordinates, since
  format( ZONE ) writes them through formatPolyPts without the parent footprint
  (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:2628-2637 at 9.0.8, 3061-3074 at
  10.0.7), where a footprint's own shapes pass it (978 at 9.0.8, 1047 at 10.0.7).

Query points keep geom_gen.QUERY_CLEARANCE (0.01 mm) from every edge: KiCad gives no rule
for a point on an outline, the tools round zone and edge points to 1 um, and KiCad
polygonizes curves within 0.005 mm, so a point closer than that could fall either way
for a reason that is no defect. The boundary itself is judged by the partition law.
"""

from __future__ import annotations

import json
import math
import tempfile
import uuid
from pathlib import Path

import edit_gen as E
import geom_gen as G
import geom_judge as GJ
import pcbnew_judge as PJ
import pytest
import sexpr_gen
from conftest import HAS_KICAD_CLI, _cli_can_load_board, _cli_report
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import kicad_io_utils as IO

from mcp_server_kicad import _connectivity as C
from mcp_server_kicad import pcb
from mcp_server_kicad._shared import _chain_edge_polygon, _point_in_polygon
from mcp_server_kicad.models import BoardValidationResult

MM = 1_000_000  # nanometres per millimetre

# ---------------------------------------------------------------------------
# point in polygon
# ---------------------------------------------------------------------------


def _probes(poly) -> list[tuple[int, int]]:
    """Lattice points to ask about: every vertex and every edge's midpoint (rounded down)
    with its eight neighbours, where a ray caster's handling of vertices and horizontal
    edges shows, and a coarse grid over the polygon's box and a little beyond it."""
    out = set()
    n = len(poly)
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        for cx, cy in ((ax, ay), ((ax + bx) // 2, (ay + by) // 2)):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    out.add((cx + dx, cy + dy))
    x0, y0, x1, y1 = GJ.bbox(poly)
    for i in range(6):
        for j in range(6):
            out.add((x0 - 3 + (x1 - x0 + 6) * i // 5, y0 - 3 + (y1 - y0 + 6) * j // 5))
    return sorted(out)


def _inside_or_none(p, poly) -> bool | None:
    """geom_judge.inside, or None for a point on the boundary, where no answer is right."""
    try:
        return GJ.inside(p, poly)
    except ValueError:
        return None


@settings(max_examples=sexpr_gen.budget())
@given(
    poly=G.simple_polygons(),
    shift=st.integers(0, 10**6),
    reverse=st.booleans(),
    offset=st.tuples(st.integers(-(10**9), 10**9), st.integers(-(10**9), 10**9)),
)
def test_point_in_polygon_is_exact(poly, shift, reverse, offset):
    """At lattice points off the boundary of a simple polygon, the float ray caster
    (_shared._point_in_polygon) and the integer one (_connectivity._in_poly) both say
    what the exact winding number says, and say it again after polygon and point are
    translated together, the vertex list is rotated and, half the time, reversed. The
    vertices lie within 2000 of the origin, so where a lattice point's horizontal ray
    crosses an edge is a rational with a denominator of at most 4000: the point itself
    (then it is on the boundary and skipped) or at least 1/4000 away from it, room
    enough for the float caster to be exact, which it must be."""
    k = shift % len(poly)
    moved = poly[k:] + poly[:k]
    if reverse:
        moved = moved[::-1]
    moved = [(x + offset[0], y + offset[1]) for x, y in moved]
    checked = 0
    for p in _probes(poly):
        want = _inside_or_none(p, poly)
        if want is None:
            continue
        q = (p[0] + offset[0], p[1] + offset[1])
        assert _point_in_polygon(*p, poly) is want, ("_point_in_polygon", p, poly)
        assert C._in_poly(*p, poly) is want, ("_in_poly", p, poly)
        assert _point_in_polygon(*q, moved) is want, ("_point_in_polygon", q, moved)
        assert C._in_poly(*q, moved) is want, ("_in_poly", q, moved)
        checked += 1
    assert checked, "every probe fell on the boundary"


def _chord_points(u, v, limit: int = 60) -> list[tuple[int, int]]:
    """Lattice points strictly between u and v, at most ``limit`` of them, evenly spread."""
    dx, dy = v[0] - u[0], v[1] - u[1]
    g = math.gcd(dx, dy)
    step = max(1, -(-(g - 1) // limit))
    return [(u[0] + dx // g * t, u[1] + dy // g * t) for t in range(1, g, step)]


@st.composite
def _split_lattice_polygons(draw):
    """split_convex_polygons scaled by a whole factor, so that the chord passes through
    many lattice points (its gcd is at least the factor)."""
    span = draw(st.sampled_from((12, 40, 400)))
    whole, part_a, part_b = draw(G.split_convex_polygons(span=span))
    m = draw(st.integers(1, 30))
    return tuple([(m * x, m * y) for x, y in poly] for poly in (whole, part_a, part_b))


@settings(max_examples=sexpr_gen.budget())
@given(_split_lattice_polygons())
def test_point_in_polygon_partitions_a_split_polygon(split):
    """A strictly convex polygon cut along a chord between two vertices: each lattice
    point inside it, those on the chord included, lies in exactly one of the two parts,
    for both implementations. A point on the chord is on the boundary of both parts,
    which is where a point-in-polygon rule has to commit, and the partition law is the
    one judgement there that needs no convention."""
    whole, part_a, part_b = split
    u, v = part_a[0], part_a[-1]  # the chord: an edge of both parts
    chord = _chord_points(u, v)
    probes = set(_probes(whole))
    for cx, cy in chord:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                probes.add((cx + dx, cy + dy))
    checked = set()
    for p in sorted(probes):
        if _inside_or_none(p, whole) is not True:
            continue  # outside, or on the polygon's own boundary
        for name, inside in (("_point_in_polygon", _point_in_polygon), ("_in_poly", C._in_poly)):
            hits = [inside(*p, part) for part in (part_a, part_b)]
            assert sum(hits) == 1, (name, p, hits, part_a, part_b)
        checked.add(p)
    assert set(chord) <= checked, "a lattice point of the chord was not inside the polygon"


# ---------------------------------------------------------------------------
# distances and orientation
# ---------------------------------------------------------------------------

_SMALL = st.integers(-40, 40)
_SMALL_POINT = st.tuples(_SMALL, _SMALL)


@settings(max_examples=sexpr_gen.budget())
@example(p=(0, 5), a=(0, 0), b=(10, 0), scale=1, slack=0)  # projects onto a
@example(p=(10, 5), a=(0, 0), b=(10, 0), scale=1, slack=0)  # projects onto b
@example(p=(5, 3), a=(0, 0), b=(10, 0), scale=1, slack=0)  # interior, distance exactly 3
@example(p=(3, 4), a=(0, 0), b=None, scale=1, slack=0)  # a point segment
@given(
    p=_SMALL_POINT,
    a=_SMALL_POINT,
    b=st.one_of(_SMALL_POINT, st.none()),
    scale=st.sampled_from((1, 1, 1, 7, 10**6)),
    slack=st.integers(-2, 2),
)
def test_within_is_the_exact_segment_distance(p, a, b, scale, slack):
    """_within(p, a, b, r2) is True exactly when the squared distance from p to the
    closed segment ab, computed in rationals, is at most r2: for r2 at and around that
    distance (so both sides of every comparison are reached), for point segments
    (b None means b = a), and at a scale where products leave 64 bits."""
    b = a if b is None else b
    p, a, b = ((scale * q[0], scale * q[1]) for q in (p, a, b))
    d2 = GJ.segment_distance2(p, a, b)
    for r2 in {0, max(0, math.floor(d2) + slack), max(0, math.ceil(d2) + slack)}:
        assert C._within(*p, *a, *b, r2) is (d2 <= r2), (p, a, b, r2, d2)


_BIG = st.integers(-(2**40), 2**40)
_ANY = st.one_of(_SMALL, _BIG)


@settings(max_examples=sexpr_gen.budget())
@given(
    a=st.tuples(_ANY, _ANY),
    b=st.tuples(_ANY, _ANY),
    p=st.tuples(_ANY, _ANY),
    along=st.one_of(st.none(), st.integers(-3, 3)),
    shift=st.tuples(_BIG, _BIG),
)
def test_side_is_the_exact_orientation(a, b, p, along, shift):
    """_side(a, b, p) is the sign geom_judge.orient computes by a different expansion of
    the same determinant; swapping a and b negates it, rotating (a, b, p) keeps it, and
    translating all three keeps it. ``along`` puts p on the line through a and b (an
    integer multiple of b - a from a), where the answer must be exactly 0."""
    if along is not None:
        p = (a[0] + along * (b[0] - a[0]), a[1] + along * (b[1] - a[1]))
    s = C._side(*a, *b, *p)
    assert s == GJ.orient(a, b, p), (a, b, p)
    assert C._side(*b, *a, *p) == -s
    assert C._side(*b, *p, *a) == s and C._side(*p, *a, *b) == s
    moved = [(x + shift[0], y + shift[1]) for x, y in (a, b, p)]
    assert C._side(*moved[0], *moved[1], *moved[2]) == s


_NEAR = st.integers(-100, 100)
_NEAR_POINT = st.tuples(_NEAR, _NEAR)


@settings(max_examples=sexpr_gen.budget())
@given(
    poly=G.simple_polygons(max_vertices=8, span=40),
    points=st.lists(_NEAR_POINT, min_size=1, max_size=10),
    segments=st.lists(st.tuples(_NEAR_POINT, _NEAR_POINT), max_size=5),
    r2=st.one_of(st.just(0), st.integers(0, 30), st.integers(0, 3000)),
)
def test_near_poly_and_seg_poly_are_exact(poly, points, segments, r2):
    """_near_poly holds exactly when the point is inside the polygon or within sqrt(r2)
    of an edge; _seg_poly exactly when an end of the segment does, or the segment comes
    within sqrt(r2) of an edge (crossing one is distance 0). Both judged in rationals.
    A point within sqrt(r2) of an edge needs no inside test, so the winding number is
    only asked about points off the boundary."""
    edges = list(zip(poly, poly[1:] + poly[:1]))

    def near(q) -> bool:
        return any(GJ.segment_distance2(q, c, d) <= r2 for c, d in edges) or GJ.inside(q, poly)

    for q in points + [(x + 1, y) for x, y in poly]:
        assert C._near_poly(q[0], q[1], poly, r2) is near(q), (q, poly, r2)
    for a, b in segments:
        want = near(a) or near(b) or any(GJ.segments_distance2(a, b, c, d) <= r2 for c, d in edges)
        assert C._seg_poly(a[0], a[1], b[0], b[1], poly, r2) is want, (a, b, poly, r2)


# ---------------------------------------------------------------------------
# chaining one loop
# ---------------------------------------------------------------------------


@st.composite
def _shuffled_loop(draw):
    """The segments of one simple loop in millimetres at the 1 um resolution the board
    edge reader rounds to, shuffled, each segment's direction drawn at random."""
    poly = draw(G.simple_polygons())
    n = len(poly)
    points = [(x / 1000, y / 1000) for x, y in poly]
    segments = [(points[i], points[(i + 1) % n]) for i in range(n)]
    order = draw(st.permutations(range(n)))
    flips = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    return [segments[i][::-1] if flip else segments[i] for i, flip in zip(order, flips)]


@settings(max_examples=sexpr_gen.budget())
@given(_shuffled_loop())
def test_chain_edge_polygon_returns_the_one_loop(segments):
    """Given the segments of a single simple loop in any order and direction,
    _chain_edge_polygon returns a cycle through each vertex once whose edges are exactly
    the segments given."""
    out = _chain_edge_polygon(segments)
    assert out is not None, segments
    n = len(segments)
    assert len(out) == n and len(set(out)) == n, out
    got = {frozenset((out[i], out[(i + 1) % n])) for i in range(n)}
    assert got == {frozenset(s) for s in segments}


# ---------------------------------------------------------------------------
# board outlines through the read tools
# ---------------------------------------------------------------------------


def _edge(shape) -> str:
    return G.shape_text(shape, layer="Edge.Cuts", depth=1, stroke_type="default")


def _square(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _loop(points) -> list[str]:
    return [
        _edge(("gr_line", points[i], points[(i + 1) % len(points)])) for i in range(len(points))
    ]


def _at(x_mm: float, y_mm: float) -> tuple[int, int]:
    return round(x_mm * MM), round(y_mm * MM)


_OUTER = _square(0, 0, 100 * MM, 100 * MM)
_HOLE = _square(40 * MM, 40 * MM, 60 * MM, 60 * MM)
_TRIANGLE = [(10 * MM, 70 * MM), (30 * MM, 70 * MM), (10 * MM, 90 * MM)]
#: A 20 mm cutout written before the 100 mm outline around it, bug 58's board: pcbnew
#: 9.0.8 builds one outline with one hole, (10, 10) on the board and (50, 50) in the hole
#: (measured 2026-10-11).
_CUTOUT_FIRST = G.Outline(
    _loop(_HOLE) + _loop(_OUTER),
    [_OUTER, _HOLE],
    [_at(10, 10), _at(50, 50), _at(99, 99), _at(150, 150), _at(45, 55)],
)
#: A gr_rect outline with a gr_circle and a gr_poly cutout: pcbnew 9.0.8 builds one
#: outline with two holes (measured 2026-10-11). (26, 86) lies in the triangle's box,
#: outside the triangle.
_PRIMITIVES = G.Outline(
    [
        _edge(("gr_rect", (0, 0), (100 * MM, 100 * MM))),
        _edge(("gr_circle", (50 * MM, 50 * MM), (60 * MM, 50 * MM))),
        _edge(("gr_poly", _TRIANGLE)),
    ],
    [_OUTER, GJ.Circle((50 * MM, 50 * MM), 10 * MM), _TRIANGLE],
    [_at(5, 5), _at(50, 50), _at(14, 74), _at(26, 86), _at(80, 20), _at(150, 50), _at(59.98, 50)],
)


def _require_clearance(outline: G.Outline) -> None:
    """A premise, not the property: every query point keeps 0.01 mm from every edge."""
    for p in outline.points:
        for contour in outline.contours:
            if not GJ.clear_of(p, contour, G.QUERY_CLEARANCE):
                raise RuntimeError(f"{p} is within 0.01 mm of an edge of {contour}")


def _beside_edges(contour, distance: int) -> list[tuple[int, int]]:
    """Points ``distance`` nm either side of each edge's midpoint (eight places round a
    circle): close to the boundary, and many of them inside the outline's bounding box
    yet off the board, where a box test and the outline disagree."""
    out = []
    if isinstance(contour, GJ.Circle):
        (cx, cy), r = contour.center, contour.radius
        for j in range(8):
            t = math.pi * j / 4 + 0.3
            for radius in (r - distance, r + distance):
                if radius > 0:
                    out.append((round(cx + radius * math.cos(t)), round(cy + radius * math.sin(t))))
        return out
    n = len(contour)
    for i in range(n):
        (ax, ay), (bx, by) = contour[i], contour[(i + 1) % n]
        length = math.hypot(bx - ax, by - ay)
        for side in (1, -1):
            out.append(
                (
                    round((ax + bx) / 2 + side * distance * (by - ay) / length),
                    round((ay + by) / 2 - side * distance * (bx - ax) / length),
                )
            )
    return out


@st.composite
def _outlines_probed_at_edges(draw, kinds, max_cutouts: int):
    """geom_gen.board_outlines, with query points added beside some edges (12 um to
    1 mm away), each still at least geom_gen.QUERY_CLEARANCE from every edge."""
    outline = draw(G.board_outlines(kinds=kinds, max_cutouts=max_cutouts))
    distance = draw(st.sampled_from((G.QUERY_CLEARANCE + 2_000, 100_000, 1_000_000)))
    near = [p for c in outline.contours for p in _beside_edges(c, distance)]
    chosen = draw(
        st.lists(st.sampled_from(near), min_size=min(4, len(near)), max_size=12, unique=True)
    )
    extra = [
        p for p in chosen if all(GJ.clear_of(p, c, G.QUERY_CLEARANCE) for c in outline.contours)
    ]
    return G.Outline(outline.items, outline.contours, outline.points + extra)


def _write_board(directory, name: str, items, version: str = "k9") -> str:
    path = Path(directory) / name
    path.write_text(G.board_text(items, version=version), encoding="utf-8", newline="\n")
    return str(path)


def _probe_footprints(points, version: str) -> list[str]:
    """One footprint P<k> at each query point, for validate_board (check_placement only
    needs one to exist)."""
    return [G.footprint_text(f"P{k}", p, version=version) for k, p in enumerate(points, 1)]


def _position(p) -> dict:
    """The position a read tool reports for a footprint written at p (nm): the float of
    the coordinate text KiCad writes for it."""
    return {"x": float(G.mm(p[0])), "y": float(G.mm(p[1]))}


def _assert_board_edge_reads(path: str, points, on_board) -> None:
    """check_placement at each point and validate_board over the footprints at the
    points say what ``on_board`` says, with the board edge checked."""
    for p, inside in zip(points, on_board):
        where = f"({G.mm(p[0])}, {G.mm(p[1])}), {'on' if inside else 'off'} the board"
        got = pcb.check_placement("P1", p[0] / MM, p[1] / MM, pcb_path=path)
        assert got.board_edge_checked, f"check_placement at {where}: no board outline found"
        assert got.outside_board_edge is (not inside), (
            f"check_placement at {where}: outside_board_edge {got.outside_board_edge}"
        )
        assert got.status == ("ok" if inside else "violations_found"), got
    violations = [
        {
            "reference": f"P{k}",
            "position": _position(p),
            "layer": "F.Cu",
            "issues": ["outside_board_edge"],
        }
        for k, (p, inside) in enumerate(zip(points, on_board), 1)
        if not inside
    ]
    want = BoardValidationResult(
        total_footprints=len(points),
        violations=violations,
        board_edge_checked=True,
        status=f"{len(violations)} violations found" if violations else "ok",
    )
    assert pcb.validate_board(pcb_path=path) == want


@settings(max_examples=sexpr_gen.budget(4))
@given(
    outline=_outlines_probed_at_edges(kinds=("gr_line",), max_cutouts=0),
    version=st.sampled_from(("k9", "k10")),
)
def test_board_edge_of_one_line_loop_is_read_exactly(outline, version):
    """A board edge drawn as one loop of gr_line, segment directions and file order
    random, under a KiCad 9 or a KiCad 10 header: check_placement finds the outline and
    says a point is outside it exactly when geom_judge says so, and validate_board flags
    exactly the footprints placed outside; points anywhere round the outline and beside
    its edges. The domain where the board-edge reader is right today (one loop, ends
    that meet exactly), so mutants of both tools' edge logic have a killer while the
    wider tests below stay red."""
    assume(outline.points)
    _require_clearance(outline)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        path = _write_board(
            work,
            "edge.kicad_pcb",
            outline.items + _probe_footprints(outline.points, version),
            version,
        )
        on_board = [GJ.parity(p, outline.contours) for p in outline.points]
        _assert_board_edge_reads(path, outline.points, on_board)


_BUG_58 = (
    "bug 58: the board-edge check takes the first Edge.Cuts loop in file order and ignores"
    " gr_rect, gr_circle and gr_poly; fixed by step 12c"
)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_58)
@settings(max_examples=sexpr_gen.budget(4))
@example(outline=_CUTOUT_FIRST, version="k9")
@given(
    outline=_outlines_probed_at_edges(
        kinds=("gr_line", "gr_rect", "gr_circle", "gr_poly"), max_cutouts=3
    ),
    version=st.sampled_from(("k9", "k10")),
)
def test_board_edge_reads_every_loop_and_primitive(outline, version):
    """An outer contour and up to three cutouts, each a loop of gr_line, a gr_rect, a
    gr_circle or a gr_poly, all items in shuffled file order, K9 or K10 header: the
    outline is found, and a point is outside it exactly when an even number of contours
    hold it (KiCad's nesting rule, geom_judge.parity); validate_board agrees for
    footprints at the points, which include points beside the edges. No gr_arc here:
    arcs are bug 68's (rung 2 plan D6). Explicit example: the cutout-first board, where
    the tool reads the hole as the board."""
    assume(outline.points)
    _require_clearance(outline)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        path = _write_board(
            work,
            "edge.kicad_pcb",
            outline.items + _probe_footprints(outline.points, version),
            version,
        )
        on_board = [GJ.parity(p, outline.contours) for p in outline.points]
        _assert_board_edge_reads(path, outline.points, on_board)


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


def _assert_matches_pcbnew(outlines: dict[str, G.Outline], work: Path) -> None:
    """Write each outline as a KiCad 9 board with footprints at its points; pcbnew's
    GetBoardPolygonOutlines().Contains at each point must be what check_placement and
    validate_board say. pcbnew failing to build an outline is a broken premise."""
    jobs, paths = [], {}
    for name, outline in outlines.items():
        _require_clearance(outline)
        paths[name] = _write_board(
            work, f"{name}.kicad_pcb", outline.items + _probe_footprints(outline.points, "k9")
        )
        jobs.append([name, paths[name], [list(p) for p in outline.points]])
    found = PJ.run(_OUTLINE_SCRIPT, {"boards": jobs})
    for name, outline in outlines.items():
        if not found[name]["ok"]:
            raise RuntimeError(f"pcbnew {PJ.major()} could not build the {name} outline")
        _assert_board_edge_reads(paths[name], outline.points, found[name]["contains"])


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_58)
def test_board_edge_matches_pcbnew(tmp_path):
    """On the cutout-first board and the primitives board, check_placement and
    validate_board say at each listed point what pcbnew's board outline says."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    _assert_matches_pcbnew({"cutout_first": _CUTOUT_FIRST, "primitives": _PRIMITIVES}, tmp_path)


@pytest.mark.nightly
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG_58)
@settings(max_examples=sexpr_gen.budget(25))
@example(outlines=[_CUTOUT_FIRST])
@given(
    outlines=st.lists(
        _outlines_probed_at_edges(
            kinds=("gr_line", "gr_rect", "gr_circle", "gr_poly"), max_cutouts=3
        ),
        min_size=1,
        max_size=4,
    )
)
def test_nightly_board_edge_matches_pcbnew(outlines):
    """Generated outlines (no arcs, as above, points beside the edges included) against
    pcbnew, one run per example."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    assume(all(o.points for o in outlines))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        _assert_matches_pcbnew({f"o{i}": o for i, o in enumerate(outlines)}, Path(work))


_GAP = 5_000  # nm: half of KiCad's 0.01 mm chaining epsilon
#: A 100 mm square whose corners miss by 5 um: pcbnew 9.0.8 builds one outline from it,
#: with (150, 50) outside and the other points inside (measured 2026-10-11;
#: test_ladder_r2_judges.test_outline_judge_matches_pcbnew checks it on every run).
_CORNER_GAPS = G.Outline(
    [
        _edge(("gr_line", (0, 0), (100 * MM, 0))),
        _edge(("gr_line", (100 * MM + _GAP, 0), (100 * MM, 100 * MM))),
        _edge(("gr_line", (100 * MM, 100 * MM + _GAP), (0, 100 * MM))),
        _edge(("gr_line", (0, 100 * MM), (0, _GAP))),
    ],
    [_OUTER],
    [_at(50, 50), _at(150, 50), _at(1, 1), _at(99.98, 99.98)],
)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "bug 71: _edge_polygon_cst chains Edge.Cuts ends only within 1 um where KiCad"
        " chains within 0.01 mm; fixed by step 12c"
    ),
)
def test_board_edge_closes_gaps_kicad_closes(tmp_path):
    """Corners that miss by 5 um are joined by KiCad, which chains shape ends within
    DEFAULT_CHAINING_EPSILON_MM = 0.01 mm (pcbnew/board.h:85 at 9.0.8, 92 at 10.0.7;
    close_enough, pcbnew/convert_shape_list_to_polygon.cpp:79-82 at 9.0.8, 85-88 at
    10.0.7): the outline is found, (150, 50) is outside it, the others inside."""
    _require_clearance(_CORNER_GAPS)
    path = _write_board(
        tmp_path,
        "gaps.kicad_pcb",
        _CORNER_GAPS.items + _probe_footprints(_CORNER_GAPS.points, "k9"),
    )
    on_board = [GJ.parity(p, _CORNER_GAPS.contours) for p in _CORNER_GAPS.points]
    assert on_board == [True, False, True, True]  # the judge on the constructed square
    _assert_board_edge_reads(path, _CORNER_GAPS.points, on_board)


# ---------------------------------------------------------------------------
# curved board edges
# ---------------------------------------------------------------------------

#: The tool linearizes every arc into this many chords (_shared._linearize_arc's default).
_CHORDS = 16


def _arc_circle(centre, radius: int, k: int, theta0: float, direction: int) -> list[tuple]:
    """A circle drawn as k gr_arc items: (start, mid, end) in nm, the ends on the
    whole micrometre (so the reader's rounding to 1 um keeps neighbouring ends equal and
    chaining is not what is tested), the mids on the nanometre."""

    def on(t: float, grid: int) -> tuple[int, int]:
        return (
            grid * round((centre[0] + radius * math.cos(t)) / grid),
            grid * round((centre[1] + radius * math.sin(t)) / grid),
        )

    ends = [on(theta0 + direction * 2 * math.pi * i / k, 1000) for i in range(k)]
    mids = [on(theta0 + direction * 2 * math.pi * (i + 0.5) / k, 1) for i in range(k)]
    return [(ends[i], mids[i], ends[(i + 1) % k]) for i in range(k)]


def _on_board_by_an_arc(p, arc) -> bool:
    """Exactly: p lies in the circular segment between the arc and its chord, inside the
    circle through the arc's three points and on the mid point's side of the chord.
    That segment is part of the board the arcs enclose."""
    start, mid, end = arc
    cx, cy, r2 = GJ.circle_through(start, mid, end)
    inside_circle = (p[0] - cx) ** 2 + (p[1] - cy) ** 2 < r2
    return inside_circle and GJ.orient(start, end, p) == GJ.orient(start, end, mid) != 0


def _between_arc_and_chords(arc, chords) -> list[tuple[int, int]]:
    """For each chord index c of the tool's 16, the point at the middle of that chord's
    stretch of the arc, halfway between the chord and the arc: inside the arc by half
    the sagitta r (1 - cos(sweep / 32)), outside the 16-chord polygon by as much."""
    start, mid, end = arc
    fx, fy, r2 = (float(v) for v in GJ.circle_through(start, mid, end))
    radius = math.sqrt(r2)
    a_start = math.atan2(start[1] - fy, start[0] - fx)
    turn_mid = (math.atan2(mid[1] - fy, mid[0] - fx) - a_start) % (2 * math.pi)
    turn_end = (math.atan2(end[1] - fy, end[0] - fx) - a_start) % (2 * math.pi)
    sweep = turn_end if turn_mid < turn_end else turn_end - 2 * math.pi
    depth = radius * (1 - math.cos(sweep / (2 * _CHORDS))) / 2
    out = []
    for c in chords:
        t = a_start + sweep * (c + 0.5) / _CHORDS
        out.append(
            (round(fx + (radius - depth) * math.cos(t)), round(fy + (radius - depth) * math.sin(t)))
        )
    return out


@st.composite
def _curved_outlines(draw):
    """(arcs, points): a circle of radius 20 to 60 mm drawn as 2 to 4 arcs either way
    round, and query points between some arcs and their 16 chords."""
    centre = (1000 * draw(st.integers(-50_000, 50_000)), 1000 * draw(st.integers(-50_000, 50_000)))
    radius = 1000 * draw(st.integers(20_000, 60_000))
    arcs = _arc_circle(
        centre,
        radius,
        draw(st.integers(2, 4)),
        draw(st.floats(0.0, 2 * math.pi, exclude_max=True)),
        draw(st.sampled_from((1, -1))),
    )
    points = []
    for arc in draw(st.lists(st.sampled_from(arcs), min_size=1, max_size=3)):
        chords = draw(st.lists(st.integers(0, _CHORDS - 1), min_size=1, max_size=2, unique=True))
        points += [(arc, p) for p in _between_arc_and_chords(arc, chords)]
    return arcs, points


#: Four 50 mm quarter arcs round (100, 100), as in the judges' pcbnew check of a curved
#: outline, with points 0.03 mm inside the circle and as far outside the 16 chords.
_QUARTER_ARCS = _arc_circle((100 * MM, 100 * MM), 50 * MM, 4, 0.0, 1)
_QUARTER_ARC_POINTS = [
    (arc, p) for arc in _QUARTER_ARCS for p in _between_arc_and_chords(arc, (0, 7, 15))
]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "bug 68: _edge_polygon_cst takes each Edge.Cuts arc as 16 chords whatever its size,"
        " where KiCad polygonizes within 0.005 mm; fixed by step 12c"
    ),
)
@settings(max_examples=sexpr_gen.budget(4))
@example(curved=(_QUARTER_ARCS, _QUARTER_ARC_POINTS))
@given(curved=_curved_outlines())
def test_curved_board_edge_is_read_within_kicads_error(curved):
    """A board edge that is a circle drawn as arcs (the four 50 mm quarters explicitly,
    then generated circles of radius 20 mm or more in 2 to 4 arcs): a point at least
    0.01 mm inside an arc is on the board to KiCad, which polygonizes curves within
    0.005 mm, even where it lies outside a coarser polygon of the arc (here, halfway
    between the arc and the tool's 16 chords). Judged exactly by the circle through each
    arc's three points; the points are chosen at least 0.01 mm from the arc."""
    arcs, points = curved
    for arc, p in points:
        cx, cy, r2 = (float(v) for v in GJ.circle_through(*arc))
        clearance = math.sqrt(r2) - math.hypot(p[0] - cx, p[1] - cy)
        if not (_on_board_by_an_arc(p, arc) and clearance >= G.QUERY_CLEARANCE):
            raise RuntimeError(f"{p} is not 0.01 mm inside the arc {arc}")
    query = [p for _, p in points]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        items = [_edge(("gr_arc", *arc)) for arc in arcs] + _probe_footprints(query, "k9")
        path = _write_board(work, "curved.kicad_pcb", items)
        _assert_board_edge_reads(path, query, [True] * len(query))


# ---------------------------------------------------------------------------
# keepouts
# ---------------------------------------------------------------------------

#: The restriction keys of a rule area, in the order KiCad writes them.
_RESTRICTIONS = ("tracks", "vias", "pads", "copperpour", "footprints")
_KEEPOUT_LAYERS = (("F.Cu",), ("B.Cu",), ("F.Cu", "B.Cu"))


def _keepout_text(outline, layers, footprints: str, *, version: str) -> str:
    """A board-level rule area as KiCad writes one (format( ZONE ),
    pcb_io_kicad_sexpr.cpp:2443-2637 at 9.0.8, 2868-3074 at 10.0.7): one (layer) or,
    for a copper zone on several layers, every layer listed (formatLayers enumerates
    them, 1345-1425 at 9.0.8); KiCad 9 writes (net 0) (net_name "") and
    (filled_areas_thickness no), KiCad 10 neither; the outline's points packed as
    Prettify packs them. Only the footprints restriction varies; the rest forbid. A layer
    set name such as "F&B.Cu" goes in (layers ...), the only list KiCad reads it from."""
    rules = {key: "not_allowed" for key in _RESTRICTIONS} | {"footprints": footprints}
    if len(layers) == 1 and not any(mark in layers[0] for mark in "*&"):
        layer_line = f'\t\t(layer "{layers[0]}")\n'
    else:
        layer_line = "\t\t(layers " + " ".join(f'"{name}"' for name in layers) + ")\n"
    tokens = [f"(xy {G.mm(x)} {G.mm(y)})".encode() for x, y in outline]
    return (
        "\t(zone\n"
        + ('\t\t(net 0)\n\t\t(net_name "")\n' if version == "k9" else "")
        + layer_line
        + f'\t\t(uuid "{uuid.uuid4()}")\n'
        + "\t\t(hatch edge 0.5)\n"
        + "\t\t(connect_pads\n\t\t\t(clearance 0)\n\t\t)\n"
        + "\t\t(min_thickness 0.25)\n"
        + ("\t\t(filled_areas_thickness no)\n" if version == "k9" else "")
        + "\t\t(keepout\n"
        + "".join(f"\t\t\t({key} {rules[key]})\n" for key in _RESTRICTIONS)
        + "\t\t)\n"
        + "\t\t(fill\n\t\t\t(thermal_gap 0.5)\n\t\t\t(thermal_bridge_width 0.5)\n\t\t)\n"
        + "\t\t(polygon\n\t\t\t(pts\n"
        + IO.pack_xy(tokens, 4).decode()
        + "\n\t\t\t)\n\t\t)\n\t)\n"
    )


@st.composite
def _keepout_boards(draw):
    """(zones, points): 1 to 3 rule areas (a simple polygon of up to about 80 mm, its
    layers, and whether it forbids footprints), and query points in the zones' boxes that
    keep 0.01 mm from every zone's edge."""
    zones = draw(
        st.lists(
            st.tuples(
                G.simple_polygons(span=20 * MM),
                st.sampled_from(_KEEPOUT_LAYERS),
                st.sampled_from(("not_allowed", "allowed")),
            ),
            min_size=1,
            max_size=3,
        )
    )
    points = []
    for _ in range(draw(st.integers(1, 8))):
        x0, y0, x1, y1 = GJ.bbox(draw(st.sampled_from(zones))[0])
        p = (draw(st.integers(x0 - MM, x1 + MM)), draw(st.integers(y0 - MM, y1 + MM)))
        if all(GJ.clear_of(p, poly, G.QUERY_CLEARANCE) for poly, _, _ in zones):
            points.append(p)
    assume(points)
    return zones, points


@settings(max_examples=sexpr_gen.budget(4))
@given(board=_keepout_boards(), version=st.sampled_from(("k9", "k10")))
def test_board_keepouts_are_read_as_exact_polygons(board, version):
    """Board-level rule areas on F.Cu, on B.Cu or on both, forbidding footprints or not,
    and a footprint on F.Cu: check_placement lists, in file order, exactly the zones that
    forbid footprints, include F.Cu and hold the point (geom_judge, exact), with the
    layers and restrictions as written; validate_board flags "keepout_zone" exactly for
    the footprints placed in such a zone. No board edge, so none is checked."""
    zones, points = board
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        items = [_keepout_text(poly, layers, rule, version=version) for poly, layers, rule in zones]
        path = _write_board(
            work, "keepouts.kicad_pcb", items + _probe_footprints(points, version), version
        )
        flagged = []
        for k, p in enumerate(points, 1):
            want = [
                {
                    "source": "board",
                    "layers": list(layers),
                    "restrictions": {key: "not_allowed" for key in _RESTRICTIONS}
                    | {"footprints": rule},
                }
                for poly, layers, rule in zones
                if rule == "not_allowed" and "F.Cu" in layers and GJ.inside(p, poly)
            ]
            got = pcb.check_placement("P1", p[0] / MM, p[1] / MM, pcb_path=path)
            assert got.keepout_violations == want, (p, zones)
            assert got.status == ("violations_found" if want else "ok")
            assert (got.board_edge_checked, got.outside_board_edge) == (False, False)
            if want:
                flagged.append(
                    {
                        "reference": f"P{k}",
                        "position": _position(p),
                        "layer": "F.Cu",
                        "issues": ["keepout_zone"],
                    }
                )
        assert pcb.validate_board(pcb_path=path) == BoardValidationResult(
            total_footprints=len(points),
            violations=flagged,
            board_edge_checked=False,
            status=f"{len(flagged)} violations found" if flagged else "ok",
        )


_ZONE_LAYERS_SCRIPT = r"""
import pcbnew
board = pcbnew.LoadBoard(payload["board"])
result = [
    [bool(z.GetIsRuleArea()), bool(z.GetDoNotAllowFootprints()),
     bool(z.IsOnLayer(pcbnew.F_Cu)), bool(z.IsOnLayer(pcbnew.B_Cu))]
    for z in board.Zones()
]
"""


@pytest.mark.parametrize("layer_set", ["F&B.Cu", "*.Cu"], ids=["front_and_back", "all_copper"])
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_keepouts_on_kicads_layer_sets_are_read(tmp_path, layer_set, version):
    """A rule area forbidding footprints, its layers written as one of KiCad's layer set
    names: KiCad's parser reads a zone's (layers ...) through the same table as an item's,
    so "F&B.Cu" is F.Cu and B.Cu and "*.Cu" every copper layer (m_layerMasks,
    pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:115-117 at 9.0.8, 120-122 at
    10.0.7; parseZONE's T_layers, 6772-6774 at 9.0.8 and 8048-8050 at 10.0.7, through
    parseBoardItemLayersAsMask, 2140-2153 at 9.0.8). KiCad 9.0.8 ships a board written
    that way
    (demos/multichannel/multichannel_mixer-unrouted.kicad_pcb, five zones on "F&B.Cu",
    generator_version 8.99). A footprint on F.Cu inside the area is in it:
    check_placement lists the area at (25, 25) and not at (60, 60), and validate_board
    flags the footprint placed inside. Where pcbnew is installed and loads the board, it
    first confirms the premise: one rule area, forbidding footprints, on F.Cu and B.Cu."""
    square = _square(10 * MM, 10 * MM, 40 * MM, 40 * MM)
    items = [_keepout_text(square, (layer_set,), "not_allowed", version=version)]
    items += _probe_footprints([_at(25, 25), _at(60, 60)], version)
    path = _write_board(tmp_path, "layer_sets.kicad_pcb", items, version)
    if PJ.skip_reason() is None and (version == "k9" or PJ.major() >= 10):
        found = PJ.run(_ZONE_LAYERS_SCRIPT, {"board": path})
        if found != [[True, True, True, True]]:
            raise RuntimeError(f"pcbnew {PJ.major()} reads the {layer_set} zone as {found}")
    inside = pcb.check_placement("P1", 25, 25, pcb_path=path)
    assert [v["source"] for v in inside.keepout_violations] == ["board"], (
        f"(25, 25) is inside a rule area on {layer_set} that forbids footprints: {inside}"
    )
    assert inside.keepout_violations[0]["restrictions"]["footprints"] == "not_allowed"
    assert pcb.check_placement("P1", 60, 60, pcb_path=path).keepout_violations == []
    assert pcb.validate_board(pcb_path=path).violations == [
        {
            "reference": "P1",
            "position": _position(_at(25, 25)),
            "layer": "F.Cu",
            "issues": ["keepout_zone"],
        }
    ]


_FOOTPRINT_ZONES_SCRIPT = r"""
import pcbnew
board = pcbnew.LoadBoard(payload["board"])
result = []
for fp in board.GetFootprints():
    for zone in fp.Zones():
        box = zone.Outline().BBox()
        result.append([fp.GetReference(), bool(zone.GetIsRuleArea()),
                       [box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()]])
"""


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_a_footprints_keepout_is_read_in_board_coordinates(tmp_path, version):
    """Footprint K1 at (80, 80) turned 90 degrees owns a keepout whose points, as KiCad
    writes them, are the board square 75..85. check_placement for R9 lists it at
    (80, 80) and not at (20, 20). Where pcbnew is installed and loads the board, it
    first confirms the premise: the rule area it builds spans 75..85 on the board
    (measured on 9.0.8)."""
    square = _square(75 * MM, 75 * MM, 85 * MM, 85 * MM)
    items = [
        G.footprint_text("K1", (80 * MM, 80 * MM), 90.0, zones=[square], version=version),
        G.footprint_text("R9", (20 * MM, 20 * MM), version=version),
    ]
    path = _write_board(tmp_path, "fpzone.kicad_pcb", items, version)
    if PJ.skip_reason() is None and (version == "k9" or PJ.major() >= 10):
        found = PJ.run(_FOOTPRINT_ZONES_SCRIPT, {"board": path})
        if found != [["K1", True, [75 * MM, 75 * MM, 85 * MM, 85 * MM]]]:
            raise RuntimeError(f"pcbnew {PJ.major()} reads the footprint's zone as {found}")
    hit = pcb.check_placement("R9", 80, 80, pcb_path=path)
    assert hit.keepout_violations == [
        {
            "source": "footprint:K1",
            "layers": ["F.Cu"],
            "restrictions": {key: "not_allowed" for key in _RESTRICTIONS},
        }
    ], "(80, 80) is inside K1's keepout, which spans 75..85 on the board"
    assert pcb.check_placement("R9", 20, 20, pcb_path=path).keepout_violations == []


# ---------------------------------------------------------------------------
# a footprint's own keepout, and keepouts on KiCad's layer sets, as KiCad reads them
# ---------------------------------------------------------------------------

_WARNING = "WARNING: position is inside a keep-out zone"


def _sided_footprint(ref: str, at, side: str, version: str) -> str:
    """A footprint at ``at`` on ``side`` with a 1 mm courtyard on that side's courtyard
    layer, which is what kicad-cli's DRC tests a footprint against a rule area by
    (collidesWithArea, pcbnew/pcbexpr_functions.cpp:498-541 at 9.0.8)."""
    crtyd = "B.CrtYd" if side == "B.Cu" else "F.CrtYd"
    box = ("fp_rect", (-500_000, -500_000), (500_000, 500_000))
    text = G.footprint_text(ref, at, shapes=[box], version=version, shape_layer=crtyd)
    if side == "B.Cu":
        front = '\t\t(layer "F.Cu")\n'
        if text.count(front) != 1:
            raise RuntimeError("geom_gen's footprint no longer writes one front layer line")
        text = text.replace(front, '\t\t(layer "B.Cu")\n', 1)
    return text


def _layered_board(directory, name: str, items, version: str) -> str:
    """A board with every technical layer in its table (edit_gen.layered_board_text), so
    the back courtyard the DRC premise reads is one of the board's layers."""
    path = Path(directory) / name
    path.write_text(E.layered_board_text(items, version=version), encoding="utf-8", newline="\n")
    return str(path)


def _lexer_head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _drc_keepout_offenders(path: str) -> set[str] | None:
    """The references of the footprints kicad-cli's DRC reports in a rule area that
    forbids them ("items_not_allowed"), matched by uuid, so the report's language does not
    matter; None where kicad-cli is missing or older than the board. A board it cannot
    load is a broken premise (RuntimeError)."""
    data = Path(path).read_bytes()
    if not HAS_KICAD_CLI or not _cli_can_load_board(data):
        return None
    _, report = _cli_report(
        ["pcb", "drc", "--format", "json", "--severity-all"], Path(path), "drc.json"
    )
    if report is None:
        raise RuntimeError(f"kicad-cli wrote no DRC report for {path}")
    board = L.tree(data, knows_bar=L.knows_bar_of(data))[0]
    by_uuid = {}
    for fp in (c for c in board.items if _lexer_head(c) == b"footprint"):
        uid = next(c for c in fp.items if _lexer_head(c) == b"uuid").items[1].value.decode()
        by_uuid[uid] = next(
            c.items[2].value.decode()
            for c in fp.items
            if _lexer_head(c) == b"property" and c.items[1].value == b"Reference"
        )
    found = set()
    for violation in json.loads(report).get("violations", []):
        if violation.get("type") == "items_not_allowed":
            found |= {by_uuid[i["uuid"]] for i in violation["items"] if i.get("uuid") in by_uuid}
    return found


def _flagged(path: str) -> list[str]:
    """The references validate_board flags with "keepout_zone", in its order."""
    return [
        v["reference"]
        for v in pcb.validate_board(pcb_path=path).violations
        if "keepout_zone" in v["issues"]
    ]


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_a_footprints_own_keepout_does_not_count_against_it(tmp_path, version):
    """N8. K1 at the origin, unturned (so its keepout's points read the same as board or
    footprint-local coordinates, and bug 15 plays no part), owns a keepout forbidding
    footprints over -10..10 on F.Cu, its own origin and courtyard inside it; R1 sits
    outside. KiCad never applies an area to the footprint that owns it (intersectsArea
    skips an area whose parent is the item, pcbnew/pcbexpr_functions.cpp:697 at 9.0.8, 825
    at 10.0.7): validate_board flags nothing; check_placement of K1 at its own origin is
    ok and of R1 at (5, 5) lists K1's keepout; move_footprint warns when R1 moves to
    (5, 5), and validate_board then flags R1 alone; moving K1 by 1 mm warns of nothing.
    Premise, where kicad-cli can load the board: its DRC on the same board with R1 inside
    reports R1 and not K1 (RuntimeError otherwise)."""
    square = _square(-10 * MM, -10 * MM, 10 * MM, 10 * MM)
    box = ("fp_rect", (-MM, -MM), (MM, MM))
    k1 = G.footprint_text("K1", (0, 0), shapes=[box], zones=[square], version=version)
    inside = _sided_footprint("R1", _at(5, 5), "F.Cu", version)
    premise = _layered_board(tmp_path, "premise.kicad_pcb", [k1, inside], version)
    offenders = _drc_keepout_offenders(premise)
    if offenders is not None and offenders != {"R1"}:
        raise RuntimeError(f"kicad-cli's DRC reports {offenders} inside K1's keepout, not R1")
    outside = _sided_footprint("R1", _at(40, 40), "F.Cu", version)
    path = _layered_board(tmp_path, "own.kicad_pcb", [k1, outside], version)
    assert _flagged(path) == [], "K1 is inside its own keepout only"
    assert pcb.check_placement("K1", 0, 0, pcb_path=path).keepout_violations == []
    assert pcb.check_placement("R1", 5, 5, pcb_path=path).keepout_violations == [
        {
            "source": "footprint:K1",
            "layers": ["F.Cu"],
            "restrictions": {key: "not_allowed" for key in _RESTRICTIONS},
        }
    ]
    assert _WARNING in pcb.move_footprint("R1", 5, 5, pcb_path=path)
    assert _flagged(path) == ["R1"]
    assert _WARNING not in pcb.move_footprint("K1", 1, 1, pcb_path=path)


def test_a_placed_tag_connect_is_not_flagged_by_its_own_keepout(tmp_path):
    """N8's stock case: KiCad 9.0.8's Tag-Connect_TC2030-IDC-FP_2x03_P1.27mm_Vertical owns
    a keepout forbidding footprints round its own origin. Placed by place_footprint at
    (50, 50), validate_board does not flag it and check_placement of it there is ok, as
    KiCad's DRC never tests a footprint against its own area. The tool flagged it (the
    zone copied at the library's origin, read through the footprint's transform, lands on
    the footprint; measured in the step 7 design, 2026-10-11)."""
    library = E.stock_footprint("Tag-Connect_TC2030-IDC-FP_2x03_P1.27mm_Vertical")
    path = _layered_board(tmp_path, "tag.kicad_pcb", [], "k9")
    try:
        pcb.place_footprint(
            "J1",
            "TC2030",
            50,
            50,
            library=str(library.parent),
            footprint=library.stem,
            pcb_path=path,
        )
    finally:
        pcb._BOARD_CACHE.pop(str(Path(path).resolve()), None)
    assert _flagged(path) == []
    assert pcb.check_placement("J1", 50, 50, pcb_path=path).keepout_violations == []


#: Every zone pcbnew reads on a board, at board level or a footprint's: whether it is a
#: rule area, forbids footprints, and is on F.Cu and on B.Cu.
_ALL_ZONE_LAYERS_SCRIPT = r"""
import pcbnew
board = pcbnew.LoadBoard(payload["board"])
zones = list(board.Zones())
for fp in board.GetFootprints():
    zones += list(fp.Zones())
result = [
    [bool(z.GetIsRuleArea()), bool(z.GetDoNotAllowFootprints()),
     bool(z.IsOnLayer(pcbnew.F_Cu)), bool(z.IsOnLayer(pcbnew.B_Cu))]
    for z in zones
]
"""


@pytest.mark.parametrize("layer_set", ["F&B.Cu", "*.Cu"], ids=["front_and_back", "all_copper"])
@pytest.mark.parametrize("owner", ["board", "footprint"])
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_keepout_wildcard_layers_are_read_as_kicad_reads_them(tmp_path, layer_set, owner, version):
    """N9. A rule area forbidding footprints over 5..25 mm, its layers written as one of
    KiCad's layer set names, either at board level or owned by K1 (at the origin,
    unturned, its own origin outside the area: bugs 15 and 88 play no part). KiCad reads
    the set through the parser's wildcard table ("F&B.Cu" is F.Cu and B.Cu, "*.Cu" every
    copper layer, pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:115-123 at
    9.0.8, 120-128 at 10.0.7) and tests a footprint against an area holding its own side,
    F.Cu or B.Cu (intersectsArea's common layers, pcbnew/pcbexpr_functions.cpp:712-715 at
    9.0.8). So check_placement lists the area for F1 (front) at (10, 10) and B1 (back) at
    (20, 20) and nothing for F2 at (40, 40); move_footprint warns when F2 moves to
    (15, 15); validate_board then flags F1, B1 and F2. Premises, where they can load the
    board: pcbnew reads every zone as a rule area forbidding footprints on F.Cu and B.Cu,
    and kicad-cli's DRC reports F1 and B1 (RuntimeError otherwise)."""
    square = _square(5 * MM, 5 * MM, 25 * MM, 25 * MM)
    if owner == "board":
        items = [_keepout_text(square, (layer_set,), "not_allowed", version=version)]
        source = "board"
    else:
        zone = E.SynZone((layer_set,), (tuple(square),))
        k1 = E.footprint_with_pad_at(0.0)._replace(zones=(zone,))
        items = [E.board_footprint_text(k1, "K1", (0, 0), 0.0, version=version)]
        source = "footprint:K1"
    items += [
        _sided_footprint("F1", _at(10, 10), "F.Cu", version),
        _sided_footprint("B1", _at(20, 20), "B.Cu", version),
        _sided_footprint("F2", _at(40, 40), "F.Cu", version),
    ]
    path = _layered_board(tmp_path, "layer_sets.kicad_pcb", items, version)
    if PJ.skip_reason() is None and (version == "k9" or PJ.major() >= 10):
        found = PJ.run(_ALL_ZONE_LAYERS_SCRIPT, {"board": path})
        if found != [[True, True, True, True]]:
            raise RuntimeError(f"pcbnew {PJ.major()} reads the {layer_set} zone as {found}")
    offenders = _drc_keepout_offenders(path)
    if offenders is not None and offenders != {"F1", "B1"}:
        raise RuntimeError(f"kicad-cli's DRC reports {offenders} inside the {layer_set} area")
    for ref, x, y in (("F1", 10, 10), ("B1", 20, 20)):
        hit = pcb.check_placement(ref, x, y, pcb_path=path).keepout_violations
        assert [v["source"] for v in hit] == [source], f"{ref} at ({x}, {y}): {hit}"
        assert hit[0]["restrictions"]["footprints"] == "not_allowed"
    assert pcb.check_placement("F2", 40, 40, pcb_path=path).keepout_violations == []
    assert _WARNING in pcb.move_footprint("F2", 15, 15, pcb_path=path)
    assert _flagged(path) == ["F1", "B1", "F2"]
