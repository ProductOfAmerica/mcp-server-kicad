"""Hypothesis strategies for rung 2's geometry, and builders of KiCad-format text.

Strategies (all coordinates are ints: schematic internal units of 100 nm, or board
nanometres, so that the text written for them is exactly what KiCad writes for the
same value and no rounding sits between a generated case and its judge):

- iu_coordinates, nm_coordinates: single coordinates within a limit;
- symbol_states: the 12 (rotation, mirror) states a placed symbol can be written in;
- angles, half_degree_angles: degrees as floats;
- simple_polygons, convex_polygons, split_convex_polygons: integer polygons;
- arcs: three integer points (start, mid, end) on an arc;
- courtyard_shapes: closed courtyard outlines as shape tuples (see below);
- board_outlines: an Edge.Cuts outline with cutouts, its contours and query points.

Shape tuples, in footprint-local nanometres: ("fp_line", start, end),
("fp_rect", start, end), ("fp_poly", [points]), ("fp_circle", center, end) and
("fp_arc", start, mid, end). courtyard_box() is their exact bounding box on the board
for a footprint position and orientation, built from geom_judge and the RotatePoint
port; shapes_of() reads them back from a footprint the lexer port parsed.

Builders write KiCad 9 text from templates embedded here, never through kiutils or the
code under test, with every number spelled by FormatInternalUnits and FormatAngle (the
eda_units port) and every run of points packed as Prettify packs it (the
kicad_io_utils port):

- board_text(items, version="k9"|"k10"): a board around the given top-level items. The
  KiCad 9 header (format 20241229) loaded in pcbnew 9.0.8 and kicad-cli 9.0.8 when the
  rung 2 design probe (M3, 2026-10-10) wrote it; the KiCad 10 one (20260206) is the same
  header without the net table KiCad 10 no longer writes, unmeasured here (the KiCad 10
  CI legs load it), and a KiCad 9 kicad-cli cannot open it.
- footprint_text(...): a footprint as KiCad saves one on a board: the angle as
  FormatAngle( Normalize180 ), omitted at 0; pad and text angles absolute; pads and
  shapes footprint-local; zones in board coordinates.
- pretty_library(directory, name, pads): a .pretty holding one footprint shaped like
  KiCad 9.0.8's stock R_0805_2012Metric.kicad_mod.
- schematic_with_r1(at_iu, state, pins): tests/fixtures/kicad_native.kicad_sch with
  R1's position and orientation and its two library pins replaced. A KiCad 9
  kicad-cli refuses the "k10" variant, which carries generator_version 10.0 and format
  20260306 (sch_io_kicad_sexpr_parser.cpp:2619-2627 and 2692-2696 at 9.0.8).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from itertools import product
from pathlib import Path
from typing import NamedTuple

import geom_judge as GJ
from hypothesis import assume
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref.eda_units import PCB_IU_PER_MM, SCH_IU_PER_MM, format_angle, format_internal_units
from kicad_ref.kicad_io_utils import pack_xy
from kicad_ref.parse_units import PCB_INT_LIMIT, SCH_INT_LIMIT, parse_board_units
from kicad_ref.trigo import normalize, normalize180, rotate_point_double

FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------

#: The largest schematic coordinate KiCad keeps (parseInternalUnits' clamp, rounded in).
SCH_LIMIT = int(SCH_INT_LIMIT)
#: The largest board coordinate KiCad keeps (parseBoardUnits' clamp).
PCB_LIMIT = int(PCB_INT_LIMIT)


def iu_coordinates(limit: int = SCH_LIMIT) -> st.SearchStrategy[int]:
    """A schematic coordinate in internal units (100 nm) within +-limit, often on the
    1.27 mm grid KiCad's symbols use."""
    grid = 12_700
    return st.one_of(
        st.integers(-limit, limit),
        st.integers(-(limit // grid), limit // grid).map(lambda k: k * grid),
    )


def nm_coordinates(limit: int = PCB_LIMIT) -> st.SearchStrategy[int]:
    """A board coordinate in nanometres within +-limit, often a whole micrometre."""
    return st.one_of(
        st.integers(-limit, limit),
        st.integers(-(limit // 1000), limit // 1000).map(lambda k: k * 1000),
    )


#: Every (rotation, mirror) a placed symbol can be written with: 4 x 3, 8 matrices.
STATES: tuple[tuple[int, str], ...] = tuple(product((0, 90, 180, 270), ("", "x", "y")))


def symbol_states() -> st.SearchStrategy[tuple[int, str]]:
    """One of the 12 (rotation, mirror) states."""
    return st.sampled_from(STATES)


_SPECIAL_ANGLES = (
    0.0,
    90.0,
    180.0,
    270.0,
    360.0,
    45.0,
    135.0,
    225.0,
    315.0,
    37.5,
    359.9,
    1e-7,
    0.001,
    12.3456789,
    135.25,
    450.0,
    540.5,
    720.0,
    1000.25,
    3600.0,
)


def angles() -> st.SearchStrategy[float]:
    """Degrees: quarter turns, the multiples of 45, awkward values, both signs, past a
    full turn either way, and any double within +-3600 (no subnormals, which builds of
    KiCad 9 parse differently)."""
    special = st.builds(
        lambda a, neg: -a if neg else a, st.sampled_from(_SPECIAL_ANGLES), st.booleans()
    )
    return st.one_of(
        special,
        st.floats(-3600.0, 3600.0, allow_nan=False, allow_infinity=False, allow_subnormal=False),
    )


def half_degree_angles() -> st.SearchStrategy[float]:
    """Multiples of 0.5 degree within +-720."""
    return st.integers(-1440, 1440).map(lambda k: k / 2)


# ---------------------------------------------------------------------------
# polygons
# ---------------------------------------------------------------------------


def _cross(o, a, b) -> int:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _star_around(points: Sequence[tuple[int, int]], centre: tuple[int, int]) -> bool:
    """Exact check that the points run counterclockwise round ``centre`` (y up), each
    next one strictly less than half a turn on: then the polygon is star-shaped from
    the centre, so simple."""
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        da = (a[0] - centre[0], a[1] - centre[1])
        db = (b[0] - centre[0], b[1] - centre[1])
        if da == (0, 0) or da[0] * db[1] - da[1] * db[0] <= 0:
            return False
    return True


@st.composite
def simple_polygons(draw, min_vertices: int = 3, max_vertices: int = 12, span: int = 1000):
    """A simple integer polygon, star-shaped from an interior point, counterclockwise
    (y up), its vertices distinct; some edges carry a vertex at their midpoint, so runs
    of collinear vertices occur. Coordinates within +-2 * span."""
    n = draw(st.integers(min_vertices, max_vertices))
    cx, cy = draw(st.integers(-span, span)), draw(st.integers(-span, span))
    # One direction per slot of 1/n turn, offset within the slot by less than the
    # amount that keeps every gap below half a turn.
    room = min(0.9, n / 2 - 1.05)
    pts = []
    for i in range(n):
        phase = draw(st.floats(0.0, room))
        radius = draw(st.integers(max(4, span // 50), span))
        a = 2 * math.pi * (i + phase) / n
        pts.append((cx + round(radius * math.cos(a)), cy + round(radius * math.sin(a))))
    assume(_star_around(pts, (cx, cy)))
    out = []
    for i, p in enumerate(pts):
        out.append(p)
        q = pts[(i + 1) % n]
        if (p[0] + q[0]) % 2 == 0 and (p[1] + q[1]) % 2 == 0 and draw(st.booleans()):
            out.append(((p[0] + q[0]) // 2, (p[1] + q[1]) // 2))
    return out


def _convex_hull(points: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Andrew's monotone chain: the strictly convex hull, counterclockwise (y up)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts
    lower: list = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


@st.composite
def convex_polygons(draw, min_vertices: int = 3, max_vertices: int = 10, span: int = 1000):
    """A strictly convex integer polygon (no collinear vertices), counterclockwise (y up),
    within +-2 * span."""
    n = draw(st.integers(min_vertices, max_vertices))
    cx, cy = draw(st.integers(-span, span)), draw(st.integers(-span, span))
    radius = draw(st.integers(max(10, span // 20), span))
    start = draw(st.floats(0.0, 2 * math.pi, exclude_max=True))
    candidates = [
        (
            cx + round(radius * math.cos(start + 2 * math.pi * (k + draw(st.floats(0, 0.8))) / n)),
            cy + round(radius * math.sin(start + 2 * math.pi * (k + draw(st.floats(0, 0.8))) / n)),
        )
        for k in range(n)
    ]
    hull = _convex_hull(candidates)
    assume(len(hull) >= min_vertices)
    return hull


@st.composite
def split_convex_polygons(draw, max_vertices: int = 10, span: int = 1000):
    """(polygon, part_a, part_b): a strictly convex polygon of four or more vertices and
    the two polygons a chord between two non-adjacent vertices cuts it into, all
    counterclockwise (y up); the chord is an edge of both parts."""
    hull = draw(convex_polygons(min_vertices=4, max_vertices=max_vertices, span=span))
    n = len(hull)
    shift = draw(st.integers(0, n - 1))
    hull = hull[shift:] + hull[:shift]
    k = draw(st.integers(2, n - 2))
    return hull, hull[: k + 1], hull[k:] + hull[:1]


@st.composite
def arcs(
    draw,
    min_radius: int = 100_000,
    max_radius: int = 50_000_000,
    span: int = 100_000_000,
    min_sweep: float = 0.2,
):
    """(start, mid, end): integer points (nm) on an arc of radius min_radius to
    max_radius about a centre within +-span, sweeping min_sweep to 2 pi - 0.2 radians
    either way, mid halfway along. The arc is the one through the three integer points."""
    cx, cy = draw(st.integers(-span, span)), draw(st.integers(-span, span))
    radius = draw(st.integers(min_radius, max_radius))
    start = draw(st.floats(0.0, 2 * math.pi, exclude_max=True))
    sweep = draw(st.floats(min_sweep, 2 * math.pi - 0.2)) * draw(st.sampled_from((1, -1)))
    pts = tuple(
        (
            cx + round(radius * math.cos(start + sweep * t)),
            cy + round(radius * math.sin(start + sweep * t)),
        )
        for t in (0.0, 0.5, 1.0)
    )
    assume(len(set(pts)) == 3 and GJ.orient(*pts) != 0)
    return pts


# ---------------------------------------------------------------------------
# footprint shapes
# ---------------------------------------------------------------------------

COURTYARD_KINDS = ("line", "rect", "poly", "circle", "arc")
#: The thinnest courtyard generated, in nm. KiCad shrinks a courtyard by 0.005 mm after
#: polygonizing it (footprint.cpp:3154-3165 at 9.0.8), so one much thinner vanishes and
#: pcbnew reports none; real courtyards are tenths of a millimetre and up.
_MIN_COURTYARD = 100_000


@st.composite
def _closed_outline(draw, kind: str, span: int) -> list[tuple]:
    if kind in ("line", "poly"):
        poly = draw(
            convex_polygons(
                min_vertices=3, max_vertices=8, span=max(span // 2, 20 * _MIN_COURTYARD)
            )
        )
        if kind == "poly":
            return [("fp_poly", poly)]
        edges = [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]
        return [("fp_line", a, b) if draw(st.booleans()) else ("fp_line", b, a) for a, b in edges]
    if kind == "rect":
        x1 = draw(st.integers(-span, span - _MIN_COURTYARD))
        y1 = draw(st.integers(-span, span - _MIN_COURTYARD))
        x2 = draw(st.integers(x1 + _MIN_COURTYARD, span))
        y2 = draw(st.integers(y1 + _MIN_COURTYARD, span))
        return [("fp_rect", (x1, y1), (x2, y2))]
    if kind == "circle":
        cx, cy = draw(st.integers(-span // 2, span // 2)), draw(st.integers(-span // 2, span // 2))
        r = draw(st.integers(max(_MIN_COURTYARD, span // 20), span // 2))
        return [("fp_circle", (cx, cy), (cx + r, cy))]
    if kind == "arc":
        # A sweep of at least one radian keeps the region between the arc and its chord
        # at least 0.12 of the radius thick (1 - cos 0.5).
        start, mid, end = draw(
            arcs(max(_MIN_COURTYARD * 10, span // 20), span // 2, span // 2, 1.0)
        )
        return [("fp_arc", start, mid, end), ("fp_line", end, start)]
    raise ValueError(f"unknown courtyard kind {kind!r}")


@st.composite
def courtyard_shapes(
    draw, kinds: Sequence[str] = COURTYARD_KINDS, max_shapes: int = 1, span: int = 20_000_000
):
    """Courtyard shapes in footprint-local nm: 1 to max_shapes closed outlines, each of
    one kind drawn from ``kinds``: "line" (a convex loop of fp_line, edges in either
    direction), "rect", "poly" (convex), "circle", "arc" (an arc closed by its chord).
    With max_shapes 1 there is exactly one closed outline, which is what pcbnew needs
    to build a courtyard whose outline vertices bound it; more outlines may overlap."""
    shapes: list[tuple] = []
    for _ in range(draw(st.integers(1, max_shapes))):
        shapes += draw(_closed_outline(draw(st.sampled_from(tuple(kinds))), span))
    return shapes


def _shape_points(shape: tuple) -> list:
    kind = shape[0]
    if kind in ("fp_line", "gr_line"):
        return [shape[1], shape[2]]
    if kind in ("fp_rect", "gr_rect"):
        (sx, sy), (ex, ey) = shape[1], shape[2]
        return [(sx, sy), (ex, sy), (ex, ey), (sx, ey)]
    if kind in ("fp_poly", "gr_poly"):
        return list(shape[1])
    raise ValueError(f"{kind} is not a straight shape")


def courtyard_box(shapes: Iterable[tuple], angle_deg: float = 0.0, at: tuple = (0, 0)) -> tuple:
    """(min x, min y, max x, max y) in nm of the shapes on a footprint at ``at`` with
    orientation ``angle_deg``: every defining point rotated by RotatePoint's double
    arithmetic (trigo.rotate_point_double), then the exact extremes of each shape
    (geom_judge), rounded once. A rectangle turns with the footprint, so its four
    corners are rotated; a circle's centre is."""
    extremes: list = []
    for shape in shapes:
        kind = shape[0]
        if kind in ("fp_circle", "gr_circle"):
            centre, end = shape[1], shape[2]
            radius = math.sqrt((end[0] - centre[0]) ** 2 + (end[1] - centre[1]) ** 2)
            cx, cy = rotate_point_double(centre[0], centre[1], angle_deg)
            extremes += [(cx - radius, cy - radius), (cx + radius, cy + radius)]
        elif kind in ("fp_arc", "gr_arc"):
            turned = [rotate_point_double(p[0], p[1], angle_deg) for p in shape[1:4]]
            extremes += GJ.arc_extremes(*turned)
        else:
            extremes += [rotate_point_double(p[0], p[1], angle_deg) for p in _shape_points(shape)]
    x0, y0, x1, y1 = GJ.bbox(extremes)
    return x0 + at[0], y0 + at[1], x1 + at[0], y1 + at[1]


def shapes_of(footprint, layer: str = "F.CrtYd", *, major: int = 10) -> list[tuple]:
    """The shape tuples of a footprint's graphic items on ``layer``, read from the lexer
    port's Group of a (footprint ...) list (as written in a .kicad_mod or a board), in
    nm through parseBoardUnits. Items other than lines, rects, polygons, circles and
    arcs are left out."""

    def head(item):
        return item.items[0].value if item.kind == L.GROUP and item.items else None

    def find(group, name: bytes):
        return next((c for c in group.items if c.kind == L.GROUP and head(c) == name), None)

    def child(group, name: bytes):
        found = find(group, name)
        if found is None:
            raise LookupError(f"no ({name.decode()} ...) in the shape at byte {group.start}")
        return found

    def point(group) -> tuple[int, int]:
        return (
            parse_board_units(group.items[1].value, major=major),
            parse_board_units(group.items[2].value, major=major),
        )

    out = []
    for item in footprint.items:
        name = head(item)
        if name not in (b"fp_line", b"fp_rect", b"fp_poly", b"fp_circle", b"fp_arc"):
            continue
        lay = find(item, b"layer")
        if lay is None or lay.items[1].value.decode() != layer:
            continue
        kind = name.decode()
        if kind == "fp_poly":
            pts = child(item, b"pts")
            out.append((kind, [point(g) for g in pts.items if head(g) == b"xy"]))
        elif kind == "fp_circle":
            out.append((kind, point(child(item, b"center")), point(child(item, b"end"))))
        elif kind == "fp_arc":
            out.append((kind, *(point(child(item, n)) for n in (b"start", b"mid", b"end"))))
        else:
            out.append((kind, point(child(item, b"start")), point(child(item, b"end"))))
    return out


# ---------------------------------------------------------------------------
# KiCad text
# ---------------------------------------------------------------------------

_uid_counter = itertools.count(1)


def _uid() -> str:
    return f"{next(_uid_counter):08x}-0000-4000-8000-00000000a2a2"


def mm(nm: int) -> str:
    """A board coordinate as KiCad writes it (FormatInternalUnits, 1e6 per mm)."""
    return format_internal_units(nm, PCB_IU_PER_MM)


def sch_mm(iu: int) -> str:
    """A schematic coordinate as KiCad writes it (FormatInternalUnits, 1e4 per mm)."""
    return format_internal_units(iu, SCH_IU_PER_MM)


def _pt(p) -> str:
    return f"{mm(p[0])} {mm(p[1])}"


_STROKE = "{t}(stroke\n{t}\t(width 0.05)\n{t}\t(type {kind})\n{t})\n"


def _pts_block(points: Sequence, depth: int) -> str:
    """(pts ...) at ``depth`` TABs, its points packed as Prettify packs them."""
    tabs = "\t" * depth
    tokens = [f"(xy {_pt(p)})".encode() for p in points]
    return f"{tabs}(pts\n" + pack_xy(tokens, depth + 1).decode() + f"\n{tabs})\n"


def shape_text(shape: tuple, *, layer: str, depth: int, stroke_type: str = "solid") -> str:
    """One graphic item for a shape tuple (fp_* or gr_*), at ``depth`` TABs."""
    t = "\t" * depth
    kind = shape[0]
    body = ""
    if kind in ("fp_line", "gr_line"):
        body = f"{t}\t(start {_pt(shape[1])})\n{t}\t(end {_pt(shape[2])})\n"
    elif kind in ("fp_rect", "gr_rect"):
        body = f"{t}\t(start {_pt(shape[1])})\n{t}\t(end {_pt(shape[2])})\n"
    elif kind in ("fp_circle", "gr_circle"):
        body = f"{t}\t(center {_pt(shape[1])})\n{t}\t(end {_pt(shape[2])})\n"
    elif kind in ("fp_arc", "gr_arc"):
        body = "".join(
            f"{t}\t({name} {_pt(p)})\n" for name, p in zip(("start", "mid", "end"), shape[1:4])
        )
    elif kind in ("fp_poly", "gr_poly"):
        body = _pts_block(shape[1], depth + 1)
    else:
        raise ValueError(f"unknown shape {kind!r}")
    fill = f"{t}\t(fill no)\n" if kind[3:] in ("rect", "circle", "poly") else ""
    return (
        f"{t}({kind}\n{body}"
        + _STROKE.format(t=t + "\t", kind=stroke_type)
        + f'{fill}{t}\t(layer "{layer}")\n{t}\t(uuid "{_uid()}")\n{t})\n'
    )


K9_BOARD_VERSION = 20241229
K10_BOARD_VERSION = 20260206

_BOARD_HEAD = """(kicad_pcb
\t(version {version})
\t(generator "pcbnew")
\t(generator_version "{generator}")
\t(general
\t\t(thickness 1.6)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(1 "F.Mask" user)
\t\t(13 "F.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(25 "Edge.Cuts" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t)
"""


def board_text(items: Iterable[str], *, version: str = "k9") -> str:
    """A whole board: the header for ``version`` ("k9" or "k10"), then ``items``, each a
    complete top-level item ending in a newline (footprint_text, shape_text at depth 1)."""
    if version == "k9":
        head = _BOARD_HEAD.format(version=K9_BOARD_VERSION, generator="9.0") + '\t(net 0 "")\n'
    elif version == "k10":
        head = _BOARD_HEAD.format(version=K10_BOARD_VERSION, generator="10.0")
    else:
        raise ValueError(f"unknown board version {version!r}")
    return head + "".join(items) + ")\n"


_TEXT_EFFECTS = "{t}(effects\n{t}\t(font\n{t}\t\t(size 1 1)\n{t}\t)\n{t})\n"


def _angle_atom(degrees: float) -> str:
    """' angle' for an (at) list, or nothing when KiCad omits a zero angle."""
    return "" if degrees == 0 else f" {format_angle(degrees)}"


def footprint_text(
    ref: str,
    at_nm: tuple[int, int],
    angle: float = 0.0,
    pads: Sequence[tuple] = (),
    shapes: Sequence[tuple] = (),
    zones: Sequence[Sequence[tuple[int, int]]] = (),
    *,
    version: str = "k9",
    shape_layer: str = "F.CrtYd",
) -> str:
    """A footprint as KiCad saves one on a board (depth 1), placed at ``at_nm`` with the
    orientation ``angle`` (degrees, any value).

    The written angle is FormatAngle( Normalize180( angle ) ), omitted when it is 0
    (FOOTPRINT::SetOrientation, footprint.cpp:2641-2669, and format(FOOTPRINT),
    pcb_io_kicad_sexpr.cpp:1127-1131 at 9.0.8). ``pads`` holds (number, (x, y), own
    angle) with a footprint-local position; a pad's written angle is absolute,
    Normalize( Normalize( own ) + angle ), omitted at 0 (PAD::Rotate and
    PADSTACK::SetOrientation, pad.cpp:1614-1620 and padstack.h:294-298; writer
    1492-1496). The Reference text's angle is absolute as well and always written
    (1992-1994). ``shapes`` are shape tuples, footprint-local, on ``shape_layer``.
    ``zones`` are keepout outlines owned by the footprint, in board coordinates as KiCad
    stores them (format(ZONE) calls formatPolyPts without the footprint, 2628-2638,
    where footprint shapes pass it, 978), each forbidding
    everything; KiCad 9 writes (net 0) (net_name "") on them and KiCad 10 no net at
    all (format(ZONE), 2868-2876 at 10.0.7).
    """
    stored = normalize180(angle)
    lines = [
        f'\t(footprint "T:{ref}"\n',
        '\t\t(layer "F.Cu")\n',
        f'\t\t(uuid "{_uid()}")\n',
        f"\t\t(at {_pt(at_nm)}{_angle_atom(stored)})\n",
        f'\t\t(property "Reference" "{ref}"\n',
        f"\t\t\t(at 0 -3 {format_angle(normalize(angle))})\n",
        '\t\t\t(layer "F.SilkS")\n',
        f'\t\t\t(uuid "{_uid()}")\n',
        _TEXT_EFFECTS.format(t="\t\t\t"),
        "\t\t)\n",
    ]
    for number, local, own in pads:
        lines += [
            f'\t\t(pad "{number}" smd rect\n',
            f"\t\t\t(at {_pt(local)}{_angle_atom(normalize(normalize(own) + angle))})\n",
            "\t\t\t(size 0.5 0.5)\n",
            '\t\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n',
            f'\t\t\t(uuid "{_uid()}")\n',
            "\t\t)\n",
        ]
    lines += [shape_text(shape, layer=shape_layer, depth=2) for shape in shapes]
    for outline in zones:
        net = '\t\t\t(net 0)\n\t\t\t(net_name "")\n' if version == "k9" else ""
        lines += [
            "\t\t(zone\n",
            net,
            '\t\t\t(layer "F.Cu")\n',
            f'\t\t\t(uuid "{_uid()}")\n',
            "\t\t\t(hatch edge 0.5)\n",
            "\t\t\t(connect_pads\n\t\t\t\t(clearance 0)\n\t\t\t)\n",
            "\t\t\t(min_thickness 0.25)\n",
            "\t\t\t(filled_areas_thickness no)\n",
            "\t\t\t(keepout\n\t\t\t\t(tracks not_allowed)\n\t\t\t\t(vias not_allowed)\n"
            "\t\t\t\t(pads not_allowed)\n\t\t\t\t(copperpour not_allowed)\n"
            "\t\t\t\t(footprints not_allowed)\n\t\t\t)\n",
            "\t\t\t(fill\n\t\t\t\t(thermal_gap 0.5)\n\t\t\t\t(thermal_bridge_width 0.5)\n\t\t\t)\n",
            "\t\t\t(polygon\n",
            _pts_block(outline, 4),
            "\t\t\t)\n",
            "\t\t)\n",
        ]
    lines.append("\t)\n")
    return "".join(lines)


#: A library footprint shaped like KiCad 9.0.8's stock
#: footprints/Resistor_SMD.pretty/R_0805_2012Metric.kicad_mod: its header and fields
#: verbatim, its graphics and 3D model left out, its pads replaced.
_LIBRARY_FOOTPRINT = """(footprint "{name}"
\t(version 20241229)
\t(generator "kicad-footprint-generator")
\t(layer "F.Cu")
\t(property "Reference" "REF**"
\t\t(at 0 -1.65 0)
\t\t(layer "F.SilkS")
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1 1)
\t\t\t\t(thickness 0.15)
\t\t\t)
\t\t)
\t)
\t(property "Value" "{name}"
\t\t(at 0 1.65 0)
\t\t(layer "F.Fab")
\t\t(effects
\t\t\t(font
\t\t\t\t(size 1 1)
\t\t\t\t(thickness 0.15)
\t\t\t)
\t\t)
\t)
\t(attr smd)
{pads}\t(embedded_fonts no)
)
"""


def pretty_library(directory, name: str, pads: Sequence[tuple], *, library: str = "Ladder") -> Path:
    """Write <directory>/<library>.pretty/<name>.kicad_mod and return the .pretty path.

    ``pads`` holds (number, (x, y) in nm, own angle in degrees); a library footprint has
    orientation 0, so a pad's written angle is Normalize( own ), omitted at 0.
    """
    pretty = Path(directory) / f"{library}.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f'\t(pad "{number}" smd rect\n'
        f"\t\t(at {_pt(local)}{_angle_atom(normalize(own))})\n"
        "\t\t(size 0.5 0.5)\n"
        '\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
        "\t)\n"
        for number, local, own in pads
    )
    (pretty / f"{name}.kicad_mod").write_text(
        _LIBRARY_FOOTPRINT.format(name=name, pads=body), encoding="utf-8", newline="\n"
    )
    return pretty


_R1_AT = b"(at 100 100 0) (unit 1)"
_R1_PINS = (b"(at 0 3.81 270)", b"(at 0 -3.81 90)")


def schematic_with_r1(
    at_iu: tuple[int, int],
    state: tuple[int, str],
    pins: Sequence[tuple[int, int, int]] = ((0, 38_100, 270), (0, -38_100, 90)),
    *,
    version: str = "k9",
) -> bytes:
    """tests/fixtures/kicad_native.kicad_sch with R1 at ``at_iu`` (internal units) in
    ``state`` (rotation, mirror axis or ""), written as given, and the two library pins
    of Device:R at ``pins``: (x, y, angle) per pin, as written in the library (y up,
    internal units). The fixture's own bytes are kept otherwise, line endings included.
    """
    data = (FIXTURES / "kicad_native.kicad_sch").read_bytes()
    rotation, mirror = state
    at = f"(at {sch_mm(at_iu[0])} {sch_mm(at_iu[1])} {rotation})"
    at += f" (mirror {mirror})" if mirror else ""
    replacements = [(_R1_AT, (at + " (unit 1)").encode())]
    for old, (x, y, a) in zip(_R1_PINS, pins):
        replacements.append((old, f"(at {sch_mm(x)} {sch_mm(y)} {a})".encode()))
    if version == "k10":
        replacements += [
            (b"(version 20250114)", b"(version 20260306)"),
            (b'(generator_version "9.0")', b'(generator_version "10.0")'),
        ]
    elif version != "k9":
        raise ValueError(f"unknown schematic version {version!r}")
    for old, new in replacements:
        if data.count(old) != 1:
            raise RuntimeError(f"the fixture holds {old!r} {data.count(old)} times, not once")
        data = data.replace(old, new)
    return data


# ---------------------------------------------------------------------------
# board outlines
# ---------------------------------------------------------------------------

OUTLINE_KINDS = ("gr_line", "gr_rect", "gr_circle", "gr_poly", "gr_arc")
#: Query points keep this far (nm) from every boundary: 0.01 mm, twice KiCad's
#: polygonization error (m_MaxError 0.005 mm), so no rounding on either side can move
#: a point across an edge.
QUERY_CLEARANCE = 10_000


class Outline(NamedTuple):
    """A generated board edge: ``items`` (the Edge.Cuts text, in file order),
    ``contours`` (polygons and geom_judge.Circle, in nm, for geom_judge.parity) and
    ``points`` (query points in nm, each at least QUERY_CLEARANCE from every edge)."""

    items: list[str]
    contours: list
    points: list[tuple[int, int]]


def _regular_polygon(draw, centre, radius: int, n: int) -> list[tuple[int, int]]:
    """n vertices round ``centre`` at radius ``radius``, each within a quarter of its
    slot of the regular position, so every angular gap is under half a turn and the
    polygon contains the disc of radius 0.7 * radius (for n >= 6)."""
    out = []
    for k in range(n):
        a = 2 * math.pi * (k + draw(st.floats(-0.25, 0.25))) / n
        out.append(
            (centre[0] + round(radius * math.cos(a)), centre[1] + round(radius * math.sin(a)))
        )
    assume(_star_around(out, centre))
    return out


def _contour_items(draw, kind: str, contour) -> list[str]:
    """Edge.Cuts items drawing ``contour`` (a polygon or a Circle) as ``kind``."""
    if isinstance(contour, GJ.Circle):
        (cx, cy), r = contour.center, int(contour.radius)
        if kind == "gr_circle":
            return [
                shape_text(
                    ("gr_circle", (cx, cy), (cx + r, cy)),
                    layer="Edge.Cuts",
                    depth=1,
                    stroke_type="default",
                )
            ]
        k = draw(st.integers(2, 4))
        theta = draw(st.floats(0.0, 2 * math.pi, exclude_max=True))

        def on_circle(t: float) -> tuple[int, int]:
            return cx + round(r * math.cos(t)), cy + round(r * math.sin(t))

        ends = [on_circle(theta + 2 * math.pi * i / k) for i in range(k)]
        mids = [on_circle(theta + 2 * math.pi * (i + 0.5) / k) for i in range(k)]
        return [
            shape_text(
                ("gr_arc", ends[i], mids[i], ends[(i + 1) % k]),
                layer="Edge.Cuts",
                depth=1,
                stroke_type="default",
            )
            for i in range(k)
        ]
    poly = list(contour)
    if kind == "gr_rect":
        return [
            shape_text(
                ("gr_rect", poly[0], poly[2]), layer="Edge.Cuts", depth=1, stroke_type="default"
            )
        ]
    if kind == "gr_poly":
        return [shape_text(("gr_poly", poly), layer="Edge.Cuts", depth=1, stroke_type="default")]
    items = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        if draw(st.booleans()):
            a, b = b, a
        items.append(
            shape_text(("gr_line", a, b), layer="Edge.Cuts", depth=1, stroke_type="default")
        )
    return items


def _shape_in_box(draw, kind: str, box) -> list[tuple[int, int]] | GJ.Circle:
    """A contour to draw as ``kind``, inside ``box`` (x0, y0, x1, y1): a Circle for
    gr_circle and gr_arc, an axis-aligned rectangle for gr_rect, else a star-shaped
    polygon."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = min(x1 - x0, y1 - y0) // 2
    if kind in ("gr_circle", "gr_arc"):
        return GJ.Circle((cx, cy), draw(st.integers(max(1, half // 4), half)))
    if kind == "gr_rect":
        w = draw(st.integers(max(1, half // 4), half))
        h = draw(st.integers(max(1, half // 4), half))
        return [(cx - w, cy - h), (cx + w, cy - h), (cx + w, cy + h), (cx - w, cy + h)]
    return _regular_polygon(
        draw, (cx, cy), draw(st.integers(max(8, half // 4), half)), draw(st.integers(6, 9))
    )


@st.composite
def board_outlines(
    draw,
    kinds: Sequence[str] = OUTLINE_KINDS,
    max_cutouts: int = 3,
    points: int = 8,
    span: int = 100_000_000,
):
    """An Edge.Cuts board edge: an outer contour of a kind drawn from ``kinds`` and 0 to
    ``max_cutouts`` (at most 3) cutouts strictly inside it and apart from each other,
    each of a kind from ``kinds`` ("gr_line": a loop of gr_line, segment directions
    random; "gr_rect"; "gr_circle"; "gr_poly"; "gr_arc": a circle drawn as 2 to 4 arcs),
    all items shuffled into one file order. Returns an Outline, in nanometres; the outer
    contour lies within about +-span."""
    kinds = tuple(kinds)
    radius = draw(st.integers(span // 10, span // 2))
    centre = (draw(st.integers(-span // 2, span // 2)), draw(st.integers(-span // 2, span // 2)))
    outer_kind = draw(st.sampled_from(kinds))
    # A box well inside the outer contour, whatever its kind (see _regular_polygon for
    # the polygon's inscribed disc).
    half = int({"gr_rect": 0.8, "gr_circle": 0.6, "gr_arc": 0.6}.get(outer_kind, 0.45) * radius)
    if outer_kind == "gr_rect":
        outer = [
            (centre[0] - radius, centre[1] - radius),
            (centre[0] + radius, centre[1] - radius),
            (centre[0] + radius, centre[1] + radius),
            (centre[0] - radius, centre[1] + radius),
        ]
    elif outer_kind in ("gr_circle", "gr_arc"):
        outer = GJ.Circle(centre, radius)
    else:
        outer = _regular_polygon(draw, centre, radius, draw(st.integers(6, 10)))
    # Cutouts go in three cells side by side across that box, so they never meet.
    width = 2 * half // 3
    cells = [
        (
            centre[0] - half + i * width,
            centre[1] - width // 2,
            centre[0] - half + (i + 1) * width,
            centre[1] + width // 2,
        )
        for i in range(3)
    ]
    shaped: list[tuple[str, list[tuple[int, int]] | GJ.Circle]] = [(outer_kind, outer)]
    for cell in draw(
        st.lists(st.sampled_from(range(3)), max_size=min(3, max_cutouts), unique=True)
    ):
        x0, y0, x1, y1 = cells[cell]
        margin = (x1 - x0) // 10
        kind = draw(st.sampled_from(kinds))
        shaped.append(
            (kind, _shape_in_box(draw, kind, (x0 + margin, y0 + margin, x1 - margin, y1 - margin)))
        )
    items: list[str] = []
    for kind, contour in shaped:
        items += _contour_items(draw, kind, contour)
    items = list(draw(st.permutations(items)))
    contours = [contour for _, contour in shaped]
    x0, y0, x1, y1 = GJ.bbox(_extent(outer))
    pad = 1_000_000
    found: list[tuple[int, int]] = []
    for _ in range(points * 4):
        if len(found) >= points:
            break
        p = (
            draw(st.integers(int(x0) - pad, int(x1) + pad)),
            draw(st.integers(int(y0) - pad, int(y1) + pad)),
        )
        if all(GJ.clear_of(p, c, QUERY_CLEARANCE) for c in contours):
            found.append(p)
    return Outline(items, contours, found)


def _extent(contour) -> list:
    if isinstance(contour, GJ.Circle):
        (cx, cy), r = contour.center, contour.radius
        return [(cx - r, cy - r), (cx + r, cy + r)]
    return list(contour)
