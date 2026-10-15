"""Rung 3: wires are written as KiCad's own wire tool writes them.

KiCad's wire tool, SCH_LINE_WIRE_BUS_TOOL::finishSegments (tools/sch_line_wire_bus_tool.cpp:
1168-1276 at 9.0.8, 1246-1350 at 10.0.7), collects every connection point already on the
sheet that lies on a new wire (1182-1202 at 9.0.8), merges overlapping collinear wires
(SCHEMATIC::CleanUp, schematic.cpp:1116-1304 at 9.0.8), trims a wire drawn across both pins
of a two-pin part (TrimWire, bus-wire-junction.cpp:50-112 at 9.0.8), and puts a junction
wherever three or more directions meet (IsExplicitJunctionNeeded, sch_screen.cpp:488-495 at
9.0.8), splitting every wire through it (AddJunction, bus-wire-junction.cpp:188-199 at 9.0.8;
SCH_LINE::BreakAt, sch_line.cpp:574-583 at 9.0.8). So no junction KiCad writes lies inside
a wire, which test_ladder_r3_judges checks on KiCad's own files.

kicad-cli 9 cuts a wire at a junction inside it and keeps only its first part
(sch_judge's docstring), which is why the netlist anchors of bug 5 run on kicad-cli 9 only:
KiCad 10 joins every wire through a junction on load (connection_graph.cpp:1418-1429 at
10.0.7), so its netlist cannot see that bug, and the structural judge
(sch_judge.junctions_on_interiors) carries it on every major. A wire end or a pin lying on
another wire's interior with no junction there joins nothing in either major (the same
graph adds a line only at its ends), so the anchors of bug 30 run on both.

Pin ends come from routing_fixtures.ORIENTED (measured KiCad-true): R placed at 90 degrees
has pin 1 left and pin 2 right of its origin by 3.81 mm, at 0 degrees pin 1 above and pin 2
below. Coordinates are compared in internal units (sch_judge), as KiCad reads them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import sch_judge as S
import sexpr_gen
from conftest import _kicad_cli_major, requires_cli
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import sch_line
from netlist_oracle import nets
from routing_fixtures import fresh, junction, place, splice, wire

from mcp_server_kicad import project, schematic
from mcp_server_kicad.models import WireSpec

_BUG5 = (
    "bug 5: a junction is written on a wire that is not split there, which kicad-cli 9 cuts;"
    " fixed by step 8"
)
_BUG30 = (
    "bug 30: add_wires looks for junction points only at its own wires' ends, on horizontal"
    " and vertical wires, within 0.01 mm; fixed by step 8"
)

A = (80.01, 101.6)  # R1:2
B = (123.19, 101.6)  # R2:1
C = (101.6, 110.49)  # R3:1
D = (101.6, 92.71)  # R4:2
P = (101.6, 101.6)  # on the bar A-B, between its ends
R1_2, R2_1, R3_1, R4_2 = ("R1", "2"), ("R2", "1"), ("R3", "1"), ("R4", "2")

#: Where layout() places each resistor: (x, y, rotation).
_SPOTS = {
    "R1": (76.2, 101.6, 90),
    "R2": (127.0, 101.6, 90),
    "R3": (101.6, 114.3, 0),
    "R4": (101.6, 88.9, 0),
    "R5": (111.76, 114.3, 0),
}


def layout(tmp_path: Path, name: str, parts=("R1", "R2", "R3")) -> str:
    """A fresh schematic with the named resistors placed by place_component at _SPOTS,
    nothing wired: R1:2 at A, R2:1 at B, R3:1 at C, R4:2 at D."""
    p = fresh(tmp_path, name)
    for ref in parts:
        x, y, rot = _SPOTS[ref]
        place(p, "R", ref, x, y, rot=rot)
    return p


def w(a, b) -> WireSpec:
    return {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}


def joins(path, watch) -> set[frozenset]:
    """kicad-cli's partition of the watched pins: one set of watched pins per net."""
    out = set()
    for _code, _name, _cls, nodes in nets(path):
        hit = frozenset(node for node in nodes if node in watch)
        if hit:
            out.add(hit)
    return out


def one_net(*pins) -> set[frozenset]:
    return {frozenset(pins)}


def cli9() -> bool:
    """Whether the local kicad-cli is KiCad 9's, the reader that cuts a wire at a junction."""
    return _kicad_cli_major() == 9


def read(path) -> bytes:
    return Path(path).read_bytes()


def _mm(point) -> tuple[float, float]:
    return point[0] / 10_000, point[1] / 10_000


def _new_sheet(work: str) -> str:
    path = Path(work) / "t.kicad_sch"
    project.create_schematic(schematic_path=str(path))
    return str(path)


# ---------------------------------------------------------------------------------------------
# A T: KiCad's form is the bar split at the T point with a junction there
# ---------------------------------------------------------------------------------------------


@st.composite
def t_shapes(draw):
    """A bar along an axis and a stem ending strictly inside it, at least 0.1 mm from either
    end, square to it or slanted; internal units on the page. Returns (bar, stem, tee), each
    wire as (start, end) and either way round."""
    lo = draw(st.integers(400_000, 1_400_000))
    length = draw(st.integers(20_000, 400_000))
    fixed = draw(st.integers(400_000, 1_400_000))
    t = draw(st.integers(lo + 1_000, lo + length - 1_000))
    foot = (
        t + draw(st.integers(-200_000, 200_000)),
        fixed + draw(st.sampled_from([-1, 1])) * draw(st.integers(1_000, 300_000)),
    )
    bar, stem, tee = ((lo, fixed), (lo + length, fixed)), (foot, (t, fixed)), (t, fixed)
    if draw(st.booleans()):  # vertical: swap the axes
        bar = tuple((y, x) for x, y in bar)
        stem = tuple((y, x) for x, y in stem)
        tee = (tee[1], tee[0])
    if draw(st.booleans()):
        bar = bar[::-1]
    if draw(st.booleans()):
        stem = stem[::-1]
    return bar, stem, tee


def _wire_mm(segment) -> WireSpec:
    return w(_mm(segment[0]), _mm(segment[1]))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
@settings(max_examples=sexpr_gen.budget(4))
@example(
    shape=(
        ((1_000_000, 1_000_000), (1_400_000, 1_000_000)),
        ((1_200_000, 1_200_000), (1_200_000, 1_000_000)),
        (1_200_000, 1_000_000),
    ),
    calls=1,
)
@given(shape=t_shapes(), calls=st.sampled_from([1, 2]))
def test_no_junction_lands_on_an_unsplit_wire(shape, calls):
    """add_wires drawing a bar and then a stem ending on it, in one call or two: a junction
    at the T point, where three directions meet, and none strictly inside any wire."""
    bar, stem, tee = shape
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        p = _new_sheet(work)
        batches = [[bar, stem]] if calls == 1 else [[bar], [stem]]
        for batch in batches:
            schematic.add_wires([_wire_mm(s) for s in batch], schematic_path=p)
        data = read(p)
    inside = S.junctions_on_interiors(data)
    assert inside == [], f"junctions inside wires: {[(pt, ln.start, ln.end) for pt, ln in inside]}"
    assert tee in S.junctions(data), "no junction at the T point"


_STROKES = (
    b"(stroke (width 0) (type default))",
    b"(stroke (width 0.1524) (type dash))",
    b"(stroke (width 0) (type default) (color 255 0 0 1))",
)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
@settings(max_examples=sexpr_gen.budget(4))
@example(
    shape=(
        ((1_000_000, 1_000_000), (1_400_000, 1_000_000)),
        ((1_200_000, 1_200_000), (1_200_000, 1_000_000)),
        (1_200_000, 1_000_000),
    ),
    stroke=_STROKES[1],
)
@given(shape=t_shapes(), stroke=st.sampled_from(_STROKES))
def test_t_split_matches_breakat(shape, stroke):
    """A stem drawn onto a wire already on the sheet, whatever that wire's stroke: the file
    changes as SCH_LINE::BreakAt changes it and in no other way. The wire keeps its uuid,
    start and stroke and now ends at the T point; one new wire with a fresh uuid and the
    same stroke bytes runs from there to the old end (sch_judge.assert_split_edit, judged by
    KiCad's lexer and kicad_ref.sch_line); the stem and a junction are inserted whole."""
    bar, stem, tee = shape
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        p = _new_sheet(work)
        (a, b) = (_mm(bar[0]), _mm(bar[1]))
        splice(
            p, f'(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) {stroke.decode()} (uuid "x"))'
        )
        before = read(p)
        schematic.add_wires([_wire_mm(stem)], schematic_path=p)
        after = read(p)
    (split,) = S.assert_split_edit(before, after, heads=["wire", "wire", "junction"])
    assert (split.old.start, split.old.end, split.point) == (bar[0], bar[1], tee)


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_a_t_reads_like_kicads_own_t(tmp_path):
    """add_wires drawing the bar A-B and the stem C-P in one call: kicad-cli reads R1:2,
    R2:1 and R3:1 on one net, as it reads the same T in the form KiCad's editor saves (the
    bar split at P, a junction at P)."""
    if not cli9():
        pytest.skip(
            "kicad-cli 10 joins wires through a junction on load, so its netlist cannot see bug 5"
        )
    gui = layout(tmp_path, "gui")
    wire(gui, *A, *P)
    wire(gui, *P, *B)
    wire(gui, *C, *P)
    junction(gui, *P)
    if joins(gui, {R1_2, R2_1, R3_1}) != one_net(R1_2, R2_1, R3_1):
        raise RuntimeError("premise: kicad-cli does not join KiCad's own form of the T")
    p = layout(tmp_path, "tool")
    schematic.add_wires([w(A, B), w(C, P)], schematic_path=p)
    assert joins(p, {R1_2, R2_1, R3_1}) == one_net(R1_2, R2_1, R3_1)


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG30)
def test_a_t_drawn_bar_last_reads_like_kicads_own_t(tmp_path):
    """The stem C-P first, then the bar A-B through its end, in two calls: P is a
    connection point already on the sheet that lies on the new bar, so KiCad's wire tool
    joins it there, and kicad-cli reads R1:2, R2:1 and R3:1 on one net."""
    p = layout(tmp_path, "t3")
    schematic.add_wires([w(C, P)], schematic_path=p)
    schematic.add_wires([w(A, B)], schematic_path=p)
    assert joins(p, {R1_2, R2_1, R3_1}) == one_net(R1_2, R2_1, R3_1)


@st.composite
def _tees_and_two_plans(draw):
    """A bar and one or two square stems ending strictly inside it at distinct points, each
    wire either way round, and two plans for drawing them: each an order of the wires cut
    into consecutive add_wires calls."""
    lo = draw(st.integers(400_000, 1_200_000))
    length = draw(st.integers(20_000, 400_000))
    fixed = draw(st.integers(400_000, 1_400_000))
    ts = draw(
        st.lists(st.integers(lo + 1_000, lo + length - 1_000), min_size=1, max_size=2, unique=True)
    )
    wires = [((lo, fixed), (lo + length, fixed))]
    for t in ts:
        side = draw(st.sampled_from([-1, 1]))
        wires.append(((t, fixed + side * draw(st.integers(1_000, 200_000))), (t, fixed)))
    wires = [seg[::-1] if draw(st.booleans()) else seg for seg in wires]
    if draw(st.booleans()):
        wires = [tuple((y, x) for x, y in seg) for seg in wires]

    def plan():
        order = draw(st.permutations(range(len(wires))))
        cuts = draw(st.lists(st.booleans(), min_size=len(order) - 1, max_size=len(order) - 1))
        calls, current = [], [order[0]]
        for index, cut in zip(order[1:], cuts):
            if cut:
                calls.append(current)
                current = []
            current.append(index)
        return calls + [current]

    return wires, plan(), plan()


def _cover(data: bytes):
    """The wires as point sets (per axis line, their extents merged into maximal runs) and
    the junction points: what a drawing is, whichever pieces it is cut into."""
    runs: dict = {}
    for line in S.lines(data):
        (x1, y1), (x2, y2) = line.start, line.end
        if y1 == y2:
            key, lo, hi = ("h", y1), min(x1, x2), max(x1, x2)
        elif x1 == x2:
            key, lo, hi = ("v", x1), min(y1, y2), max(y1, y2)
        else:
            raise RuntimeError(f"a plan of square wires wrote a slanted one: {line}")
        runs.setdefault(key, []).append((lo, hi))
    merged = {}
    for key, spans in runs.items():
        out: list = []
        for lo, hi in sorted(spans):
            if out and lo <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], hi))
            else:
                out.append((lo, hi))
        merged[key] = tuple(out)
    return merged, sorted(S.junctions(data))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG30)
@settings(max_examples=sexpr_gen.budget(4))
@example(
    case=(
        [
            ((1_000_000, 1_000_000), (1_400_000, 1_000_000)),
            ((1_200_000, 1_200_000), (1_200_000, 1_000_000)),
        ],
        [[0, 1]],
        [[1], [0]],
    )
)
@given(case=_tees_and_two_plans())
def test_add_wires_is_order_independent(case):
    """The same wires drawn in two orders, cut into calls in two ways, end as the same
    drawing: the same wire cover and the same junction points (a drawing is a set of wires,
    so its connections cannot depend on the order they were drawn in)."""
    wires, plan_a, plan_b = case
    shapes = []
    for plan in (plan_a, plan_b):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            p = _new_sheet(work)
            for call in plan:
                schematic.add_wires([_wire_mm(wires[i]) for i in call], schematic_path=p)
            shapes.append(_cover(read(p)))
    assert shapes[0] == shapes[1], f"plan {plan_a} drew {shapes[0]}, plan {plan_b} drew {shapes[1]}"


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG30)
def test_a_wire_drawn_through_a_pin_connects_it(tmp_path):
    """The wire A-B drawn through R6:1, which sits at P: a pin on a new wire is a connection
    point there (finishSegments collects it, and pin plus the wire's two directions are three),
    so kicad-cli reads R1:2, R2:1 and R6:1 on one net."""
    p = layout(tmp_path, "ex1", parts=("R1", "R2"))
    place(p, "R", "R6", 101.6, 105.41)  # R6:1 at P
    schematic.add_wires([w(A, B)], schematic_path=p)
    assert joins(p, {R1_2, R2_1, ("R6", "1")}) == one_net(R1_2, R2_1, ("R6", "1"))


# ---------------------------------------------------------------------------------------------
# The on-wire test: exact, at any angle (IsPointOnSegment)
# ---------------------------------------------------------------------------------------------

_PAGE = st.integers(200_000, 1_800_000)


@st.composite
def _wire_and_point(draw):
    """A wire at any angle in internal units, and a point on its line, a few units off such
    a point (0.005 mm is 50 units), or anywhere."""
    a = (draw(_PAGE), draw(_PAGE))
    b = (draw(_PAGE), draw(_PAGE))
    dx, dy = b[0] - a[0], b[1] - a[1]
    from math import gcd

    g = gcd(dx, dy)
    kind = draw(st.sampled_from(["on_line", "near", "anywhere"]))
    if kind == "anywhere" or g == 0:
        return a, b, (draw(_PAGE), draw(_PAGE))
    k = draw(st.integers(-g, 2 * g))
    p = (a[0] + k * (dx // g), a[1] + k * (dy // g))
    if kind == "near":
        p = (p[0] + draw(st.integers(-100, 100)), p[1] + draw(st.integers(-100, 100)))
    return a, b, p


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 30: the on-wire test sees only horizontal and vertical wires and takes points"
    " within 0.01 mm; fixed by step 8",
)
@settings(max_examples=sexpr_gen.budget())
@example(case=((1_000_000, 1_000_000), (1_300_000, 1_300_000), (1_100_000, 1_100_000)))
@example(case=((1_000_000, 1_000_000), (1_300_000, 1_000_000), (1_100_000, 1_000_050)))
@given(case=_wire_and_point())
def test_point_on_wire_interior_is_exact(case):
    """_point_on_wire_interior holds exactly where a junction splits the wire: on it as
    IsPointOnSegment says, ends excluded (kicad_ref.sch_line.strictly_inside), at any angle.
    The examples: a diagonal wire's midpoint (KiCad splits there) and a point 0.005 mm off
    a horizontal wire (KiCad's split and connection graph are exact, so nothing joins it)."""
    a, b, p = case
    assert schematic._point_on_wire_interior(*_mm(p), *_mm(a), *_mm(b)) == sch_line.strictly_inside(
        a, b, p
    )


@st.composite
def _axis_wire_and_clear_point(draw):
    """A horizontal or vertical wire at least 0.04 mm long, and a point exactly on its line
    or 0.02 mm or more off it, and 0.02 mm or more from either end along it."""
    lo = draw(_PAGE)
    hi = draw(st.integers(lo + 400, lo + 400_000))
    fixed = draw(_PAGE)
    along = draw(
        st.one_of(
            st.integers(lo - 200_000, lo - 200),
            st.integers(lo + 200, hi - 200),
            st.integers(hi + 200, hi + 200_000),
        )
    )
    off = draw(st.one_of(st.just(0), st.integers(200, 200_000), st.integers(-200_000, -200)))
    a, b, p = (lo, fixed), (hi, fixed), (along, fixed + off)
    if draw(st.booleans()):
        a, b, p = (fixed, lo), (fixed, hi), (fixed + off, along)
    if draw(st.booleans()):
        a, b = b, a
    return a, b, p


@settings(max_examples=sexpr_gen.budget())
@given(case=_axis_wire_and_clear_point())
def test_point_on_wire_interior_axis_aligned(case):
    """On horizontal and vertical wires, for points on the wire's line or clear of it and of
    its ends, _point_on_wire_interior already agrees with IsPointOnSegment, ends excluded."""
    a, b, p = case
    assert schematic._point_on_wire_interior(*_mm(p), *_mm(a), *_mm(b)) == sch_line.strictly_inside(
        a, b, p
    )


# ---------------------------------------------------------------------------------------------
# connect_pins: its own points go through the same junction rule
# ---------------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_connect_pins_splits_the_wire_its_corner_lands_on(tmp_path):
    """connect_pins(R2:1, R1:2) draws (101.6, 123.19) to (101.6, 80.01); connect_pins(R3:1,
    R2:1) then turns its L at (101.6, 97.79), on that wire's interior. Three directions meet
    there (the two legs and the first wire's way up), so KiCad's form is a junction with the
    first wire split at the corner: the file changes by that split and whole new items only,
    no junction lies inside a wire, and kicad-cli 9 reads the three pins on one net (it read
    R1:2 cut off, the first wire keeping only its start to the corner)."""
    p = fresh(tmp_path, "cp1b")
    place(p, "R", "R1", 101.6, 76.2)  # R1:2 at (101.6, 80.01)
    place(p, "R", "R2", 101.6, 127.0)  # R2:1 at (101.6, 123.19)
    place(p, "R", "R3", 127.0, 101.6)  # R3:1 at (127, 97.79)
    schematic.connect_pins("R2", "1", "R1", "2", schematic_path=p)
    before = read(p)
    schematic.connect_pins("R3", "1", "R2", "1", schematic_path=p)
    after = read(p)
    (split,) = S.assert_split_edit(before, after, heads=["wire", "wire", "wire", "junction"])
    first = (S.iu_point((101.6, 123.19)), S.iu_point((101.6, 80.01)))
    assert ((split.old.start, split.old.end), split.point) == (first, S.iu_point((101.6, 97.79)))
    assert S.junctions_on_interiors(after) == []
    if cli9():
        assert joins(p, {R1_2, R2_1, ("R3", "1")}) == one_net(R1_2, R2_1, ("R3", "1"))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_connect_pins_joins_a_pin_lying_on_a_wire(tmp_path):
    """R6:1 sits at P on the interior of the wire A-B; connect_pins(R6:1, R4:2) starts there.
    The pin, the new wire and A-B's two directions meet at P, so KiCad's form is a junction
    with A-B split at P: none lies inside a wire, and kicad-cli 9 reads R1:2, R2:1, R4:2 and
    R6:1 on one net (it read R2:1 cut off)."""
    p = layout(tmp_path, "cp2", parts=("R1", "R2", "R4"))
    wire(p, *A, *B)
    place(p, "R", "R6", 101.6, 105.41)  # R6:1 at P
    schematic.connect_pins("R6", "1", "R4", "2", schematic_path=p)
    assert S.junctions_on_interiors(read(p)) == []
    if cli9():
        pins = {R1_2, R2_1, R4_2, ("R6", "1")}
        assert joins(p, pins) == one_net(*pins)


# ---------------------------------------------------------------------------------------------
# What KiCad's wire tool does besides junctions: TrimWire, the overlap merge, exactness
# ---------------------------------------------------------------------------------------------

R9_1, R9_2 = ("R9", "1"), ("R9", "2")
P1, P2 = (97.79, 101.6), (105.41, 101.6)  # R9:1 and R9:2, R9 at P turned 90 degrees


@requires_cli
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 30: add_wires leaves the pins a new wire runs through unjoined, a two-pin"
    " part's included; fixed by step 8",
)
@pytest.mark.parametrize(
    "start, expected",
    [
        pytest.param(A, {frozenset({R1_2, R9_1}), frozenset({R2_1, R9_2})}, id="across_both_pins"),
        pytest.param(
            P1,
            {frozenset({R1_2}), frozenset({R9_1}), frozenset({R2_1, R9_2})},
            id="from_one_pin_across_the_other",
        ),
    ],
)
def test_a_wire_across_a_two_pin_part_is_trimmed_as_kicad_trims_it(tmp_path, start, expected):
    """A wire to B drawn across R9, whose two pins lie on it. finishSegments runs TrimWire
    for every part with at most two connection points: a wire holding both pins, and not
    running exactly from one to the other, loses the stretch between them. From A that
    leaves A-R9:1 and R9:2-B; from R9:1 it leaves R9:2-B (the remainder at R9:1 has no
    length). kicad-cli reads the nets those wires make."""
    p = layout(tmp_path, "trim", parts=("R1", "R2"))
    place(p, "R", "R9", 101.6, 101.6, rot=90)
    schematic.add_wires([w(start, B)], schematic_path=p)
    assert joins(p, {R1_2, R2_1, R9_1, R9_2}) == expected


@requires_cli
def test_a_wire_from_one_pin_to_the_other_is_not_trimmed(tmp_path):
    """A wire running exactly from R9:1 to R9:2 is what was asked for: TrimWire never removes
    such a wire ("Don't remove entire wires", bus-wire-junction.cpp:83-88 at 9.0.8). It is
    inserted whole, and kicad-cli reads both pins on one net."""
    import edit_judge as J

    p = layout(tmp_path, "trim_whole", parts=())
    place(p, "R", "R9", 101.6, 101.6, rot=90)
    before = read(p)
    schematic.add_wires([w(P1, P2)], schematic_path=p)
    J.assert_inserted(before, read(p), heads=["wire"])
    assert joins(p, {R9_1, R9_2}) == one_net(R9_1, R9_2)


BF = (116.84, 101.6)  # a wire end with nothing at it
F = (134.62, 101.6)  # R7:1, R7 at (138.43, 101.6) turned 90 degrees


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_a_wire_overlapping_one_on_the_sheet_joins_it(tmp_path):
    """add_wires(P to F) over the wire A-BF already there, collinear, overlapping it on P-BF
    with no shared end. KiCad's CleanUp merges the overlap, so the new wire adds BF-F and
    meets A-BF at its end: no junction inside a wire, and kicad-cli reads R1:2 and R7:1 on
    one net. (Two such wires left overlapping read as two nets in kicad-cli 9.)"""
    p = layout(tmp_path, "ovl", parts=("R1",))
    place(p, "R", "R7", 138.43, 101.6, rot=90)
    wire(p, *A, *BF)
    schematic.add_wires([w(P, F)], schematic_path=p)
    assert S.junctions_on_interiors(read(p)) == []
    assert joins(p, {R1_2, ("R7", "1")}) == one_net(R1_2, ("R7", "1"))


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG30)
def test_a_wire_laid_over_a_pin_on_a_wire_joins_the_pin(tmp_path):
    """The wire A-B runs through R6:1 at P without joining it; add_wires lays a wire over part
    of A-B, across P. R6:1 is a connection point on the new wire, and the pin with A-B's
    two directions makes three, so KiCad joins it there: kicad-cli reads R1:2, R2:1 and
    R6:1 on one net, and no junction lies inside a wire."""
    pins = {R1_2, R2_1, ("R6", "1")}
    p = layout(tmp_path, "ovlin", parts=("R1", "R2"))
    place(p, "R", "R6", 101.6, 105.41)  # R6:1 at P
    wire(p, *A, *B)
    if joins(p, pins) != {frozenset({R1_2, R2_1}), frozenset({("R6", "1")})}:
        raise RuntimeError("premise: the wire through R6:1 already joins it")
    schematic.add_wires([w((90.17, 101.6), (111.76, 101.6))], schematic_path=p)
    assert joins(p, pins) == one_net(*pins)
    assert S.junctions_on_interiors(read(p)) == []


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
@pytest.mark.parametrize(
    "stroke",
    [
        pytest.param("(stroke (width 0.1524) (type default))", id="width"),
        pytest.param("(stroke (width 0) (type default) (color 255 0 0 1))", id="colour"),
    ],
)
def test_a_wire_laid_on_a_differently_drawn_wire_is_written_as_asked(tmp_path, stroke):
    """A wire laid inside a wire of another width or colour: CleanUp merges only lines whose
    strokes are equivalent (SCH_LINE::IsStrokeEquivalent, sch_line.h:195-209 at 9.0.8), and at
    each end of the new wire the old one's two directions and the new wire's own are two, so
    no junction: the new wire is inserted as asked and nothing else changes."""
    import edit_judge as J

    p = fresh(tmp_path, "styled")
    splice(p, f'(wire (pts (xy {A[0]} {A[1]}) (xy {B[0]} {B[1]})) {stroke} (uuid "x"))')
    before = read(p)
    schematic.add_wires([w((90.17, 101.6), (111.76, 101.6))], schematic_path=p)
    after = read(p)
    J.assert_inserted(before, after, heads=["wire"])
    new = [ln for ln in S.lines(after) if ln.uuid not in {o.uuid for o in S.lines(before)}]
    assert [(ln.start, ln.end) for ln in new] == [
        (S.iu_point((90.17, 101.6)), S.iu_point((111.76, 101.6)))
    ]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG30)
def test_the_same_wire_drawn_twice_changes_nothing_the_second_time(tmp_path):
    """Drawing a wire already on the sheet adds nothing: CleanUp removes a line identical to
    another (schematic.cpp:1272-1278 at 9.0.8), so the file stays byte-identical and the
    result says no change was made."""
    p = fresh(tmp_path, "twice")
    schematic.add_wires([w(A, B)], schematic_path=p)
    before = read(p)
    result = schematic.add_wires([w(A, B)], schematic_path=p)
    assert read(p) == before
    assert "no change" in result.lower(), result


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG30)
@pytest.mark.parametrize("off", [0.005, 0.0099], ids=["5um", "9.9um"])
def test_a_stem_ending_just_off_a_wire_gets_no_junction(tmp_path, off):
    """A stem ending a few micrometres off the bar ends on no wire: KiCad's split and its
    connection graph are exact (IsPointOnSegment), so a junction there would claim a
    connection no reader makes. None is written."""
    p = layout(tmp_path, "near")
    schematic.add_wires([w(A, B), w(C, (P[0], round(P[1] + off, 4)))], schematic_path=p)
    assert S.junctions(read(p)) == []
