"""Judging one footprint against another: as KiCad reads it, and token for token.

Every footprint is read from the file's bytes with the lexer port and
kicad_ref.footprint, never with the CST under test. The comparisons follow the test
ladder's rules for edits:

- D1, KiCad's reading: two footprints are compared as KiCad reads their tokens (angles
  through Normalize180, Normalize and the text loader's arithmetic), so a spelling KiCad
  reads the same way is no difference; spelling is judged on its own (``tokens``).
- D2, the comparison form (kicad_ref.footprint.canonical): a keep-upright text as
  KeepUpright leaves it, any other text at its angle normalized.
- D3, positions: footprint-relative positions are exact on quarter turns; elsewhere KiCad
  re-derives the ones it writes from board positions it rounded through RotatePoint, which
  can move each coordinate by up to two nanometres over a load, a turn and a save
  (BOARD_ITEM::GetFPRelativePosition, pcbnew/board_item.cpp:334-345 at 9.0.8, 399-410 at
  10.0.7), so a comparison with pcbnew's output is given ``tolerance_nm=2`` there.
  Graphics compare by geometry: a rectangle is the closed outline of its four corners,
  so it equals the polygon KiCad makes of it when a turn is not a multiple of 90 degrees
  (EDA_SHAPE::rotate, common/eda_shape.cpp:636-694 at 9.0.8, 930-988 at 10.0.7), and a
  polygon is the same outline from any corner, either way round; an arc is its two ends,
  in either order (EDA_SHAPE::flip swaps them, 697-737 at 9.0.8, 991-1033 at 10.0.7), and
  the circle its mid point lies on: two arcs are one when their ends agree and each one's
  mid point lies within ARC_TOLERANCE_NM of the other's circle, on the same side of the
  chord. KiCad writes a library arc's mid point as it read it until the arc changes, and
  then the angular middle of the arc it holds (EDA_SHAPE::GetArcMid, 811-822 at 9.0.8,
  1108-1118 at 10.0.7), a different point of the same arc.

A mandatory field a file leaves out (kicad_ref.footprint's ``default``) has no tokens a
tool editing that file could change, so ``differences( written_fields_only=True )`` leaves
out every field the first footprint holds as a default: KiCad's own flip takes such a
field to the back, where KiCad reading the edited file still finds it on the front.

Pads are paired by number and texts by key (a field's name, an fp_text's text) and layer;
within a number or a key, by the nearest footprint-relative position, ties broken by the
nearest angle and then, for pads, by the same layers, because pcbnew's writer sorts pads
and drawings (FOOTPRINT::cmp_drawings and
cmp_pads, pcbnew/footprint.cpp:3774-3894 at 9.0.8, 4346-4497 at 10.0.7) where a library
file keeps its own order, and some stock footprints stack pads of one number at one place.
Graphics are paired by kind and layer, then by the nearest geometry (_geometry_gap).
"""

from __future__ import annotations

import math
from collections import defaultdict
from fractions import Fraction

from kicad_ref import board_layers as BL
from kicad_ref import dsn_lexer as L
from kicad_ref import footprint as F
from kicad_ref import pad as PAD
from kicad_ref import pcb_text as T
from kicad_ref.trigo import normalize

#: Within this many nanometres of each other, two footprint-relative positions are the
#: same after a turn off the quarter turns (module docstring, D3).
ROTATION_TOLERANCE_NM = 2
#: Within this many nanometres of an arc's circle, a point lies on that arc. KiCad holds
#: an arc by its ends and the centre it computes from the three points it reads
#: (EDA_SHAPE::SetArcGeometry, common/eda_shape.cpp:886-913 at 9.0.8, 1173-1200 at
#: 10.0.7), and CalcArcCenter moves that centre onto a multiple of 100 or 10 nm whenever
#: one lies within the error it estimates for it (libs/kimath/src/trigo.cpp:369-518 at
#: 9.0.8, 369-531 at 10.0.7): up to 50 nm on each axis, about 71 nm in all, before its own
#: KiROUND. The angular middle KiCad writes after a flip lies on the circle around that
#: centre, so it can sit off the circle through the points it read by twice that, about
#: 143 nm. Measured: at most 72.7 nm over the 13,421 arcs of the 1,603 stock footprints
#: holding one (713 of them past 2 nm), placed at 0 and flipped by pcbnew 9.0.8
#: (2026-10-11). A flip that misses an arc moves it by far more than this.
ARC_TOLERANCE_NM = 150
#: Within this many degrees, a mandatory field one file leaves out has the angle the
#: other file writes for it. KiCad reads the one left out at Normalize( 0 + A ), A being
#: the footprint's angle atom, whose FormatAngle keeps ten significant digits: within
#: 5e-8 degrees of the angle KiCad held for any angle below 1000 (a footprint placed at
#: 180.17578125 is written -179.8242188, so such a field reads 0.1757812 after
#: KeepUpright where KiCad writing it gives 0.17578125). A defect moves an angle by a
#: whole step, not by a digit's rounding.
DEFAULT_FIELD_ANGLE_TOLERANCE = 1e-7


def root(data: bytes):
    """The top-level list of a board or a .kicad_mod, read by the lexer port."""
    return L.tree(data, knows_bar=L.knows_bar_of(data))[0]


def reference_of(group) -> str | None:
    """A footprint's Reference, from its (property "Reference" ...) or a legacy
    (fp_text reference ...)."""
    for item in group.items:
        name = F.head(item)
        if name == b"property" and len(item.items) > 2 and item.items[1].value == b"Reference":
            return item.items[2].value.decode()
        if name == b"fp_text" and len(item.items) > 2 and item.items[1].value == b"reference":
            return item.items[2].value.decode()
    return None


def child(group, name: bytes):
    """The first child list of ``group`` headed ``name``; RuntimeError when there is none
    (a premise of the caller's reading, never the defect judged)."""
    found = F.child(group, name)
    if found is None:
        raise RuntimeError(f"no ({name.decode()} ...) in the list at byte {group.start}")
    return found


def footprint_group(data: bytes, reference: str):
    """The one (footprint ...) of a board whose Reference is ``reference``; RuntimeError
    when there is not exactly one (a premise, never the defect judged)."""
    found = [fp for fp in F.children(root(data), b"footprint") if reference_of(fp) == reference]
    if len(found) != 1:
        raise RuntimeError(f"{len(found)} footprints {reference!r} on the board, not one")
    return found[0]


def copper_count(data: bytes) -> int:
    """The number of copper rows in a board's (layers ...) table."""
    table = F.child(root(data), b"layers")
    if table is None:
        raise RuntimeError("the board has no (layers ...) table")
    names = [row.items[1].value.decode() for row in table.items[1:] if row.kind == L.GROUP]
    return sum(1 for name in names if name.endswith(".Cu") and BL.is_copper(name))


def layer_table(data: bytes) -> BL.LayerTable:
    """A board's layer table as KiCad reads it (kicad_ref.board_layers)."""
    return BL.read_layer_table(root(data))


def read(data: bytes, reference: str, *, major: int = 10) -> F.Footprint:
    """Footprint ``reference`` of the board ``data`` as KiCad ``major`` reads it."""
    return F.read_footprint(
        footprint_group(data, reference), major=major, copper_count=copper_count(data)
    )


def read_library(data: bytes, *, major: int = 10) -> F.Footprint:
    """The footprint of a .kicad_mod as KiCad reads it (orientation 0, at the origin)."""
    return F.read_footprint(root(data), major=major)


def _distance(a, b) -> float:
    if a is None or b is None:
        return 0.0 if a is b else math.inf
    return math.dist(a, b)


def _angle_gap(a: float, b: float) -> float:
    gap = abs(normalize(a) - normalize(b))
    return min(gap, 360.0 - gap)


def pair(
    got, want, key, local, what: str, tiebreak=None, *, distance=None
) -> tuple[list[tuple], list[str]]:
    """(pairs, problems): the items of ``got`` and ``want`` paired by ``key`` and, within a
    key, by the nearest ``local`` position (or the smallest ``distance( item, other )``
    when one is given), ties broken by the smallest ``tiebreak( item, other )`` (several
    of KiCad's stock footprints stack pads of one number at one place at different
    angles); a key holding a different number of items on each side is a problem."""
    apart = distance or (lambda a, b: _distance(local(a), local(b)))
    by_got, by_want = defaultdict(list), defaultdict(list)
    for item in got:
        by_got[key(item)].append(item)
    for item in want:
        by_want[key(item)].append(item)
    pairs, problems = [], []
    for k in sorted(set(by_got) | set(by_want), key=repr):
        mine, theirs = by_got[k], list(by_want[k])
        if len(mine) != len(theirs):
            problems.append(f"{what} {k!r}: {len(mine)} written where KiCad has {len(theirs)}")
            continue
        for item in sorted(mine, key=lambda i: repr(local(i))):
            best = min(
                theirs,
                key=lambda other: (
                    apart(item, other),
                    tiebreak(item, other) if tiebreak else 0,
                ),
            )
            theirs.remove(best)
            pairs.append((item, best))
    return pairs, problems


def _same_style(a: F.Text, b: F.Text) -> bool:
    """Two texts' styles are KiCad's same reading: equal, or, where either text is a
    mandatory field its file leaves out, equal but for the angle, which may differ by
    the footprint angle atom's rounding (DEFAULT_FIELD_ANGLE_TOLERANCE).

    That rounding can also cross the comparison form's fold at 180, so for a centred
    keep-upright text, which KeepUpright turns half a turn and changes in nothing else,
    the angle is compared on either side of the fold. Placed at -1.2306671787116972e-08,
    a footprint is written -1.230667179e-08, so KiCad reads a field the file leaves out
    at 359.99999998769334, folded to 179.9999999876934; KiCad itself holds that field at
    179.9999999876934 after KeepUpright, writes it "180" and reads it back folded to 0."""
    if a.style == b.style:
        return True
    if not (a.default or b.default) or a.style[1:] != b.style[1:]:
        return False
    gap = _angle_gap(a.style.angle, b.style.angle)
    if a.style.keep_upright and a.style.h == T.CENTER and a.style.v == T.CENTER:
        gap = min(gap, _angle_gap(a.style.angle + 180.0, b.style.angle))
    return gap <= DEFAULT_FIELD_ANGLE_TOLERANCE


def _pad_tiebreak(a: F.Pad, b: F.Pad) -> tuple[float, bool]:
    """Between pads of one number at one place, the nearest angle, then the same layers
    (some stock footprints stack a front and a back pad of one number at one place)."""
    return _angle_gap(a.angle, b.angle), a.layers != b.layers


def _text_tiebreak(a: F.Text, b: F.Text) -> float:
    return _angle_gap(a.style.angle, b.style.angle) + (0 if a.style == b.style else 1)


def _near(a, b, tolerance_nm: int) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a[0] - b[0]) <= tolerance_nm and abs(a[1] - b[1]) <= tolerance_nm


def _cycle(points) -> tuple:
    """A closed outline's points from its least point, in the direction whose next point
    is the lesser: one outline whatever corner or direction it is written from."""
    best = None
    for seq in (list(points), list(reversed(points))):
        k = seq.index(min(seq))
        turned = tuple(seq[k:] + seq[:k])
        if best is None or turned < best:
            best = turned
    return best


def geometry(shape: F.Shape) -> tuple[str, tuple]:
    """(form, points) of a graphic or a pad primitive by geometry (module docstring):
    "loop" for a rectangle or a polygon, its corners as _cycle orders them; "arc" for an
    arc, its two ends in order then its mid point; any other shape its points as read."""
    kind = shape.kind[3:]
    if kind in ("rect", "bbox"):
        (sx, sy), (ex, ey) = shape.points
        return "loop", _cycle([(sx, sy), (ex, sy), (ex, ey), (sx, ey)])
    if kind == "poly":
        return "loop", _cycle(shape.points)
    if kind == "arc":
        start, mid, end = shape.points
        return "arc", (*sorted((start, end)), mid)
    return kind, tuple(shape.points)


def _alignments(form: str, points: tuple, first, tolerance_nm: int) -> list[tuple]:
    """The orders of ``points``, a geometry() form's, that describe the same shape and
    that a shape starting at ``first`` is compared with: a loop from each corner within
    ``tolerance_nm`` of ``first`` (from the nearest corner when none is), either way
    round, since _cycle's start moves when a nanometre's rounding reorders two corners;
    an arc with its ends either way round, which a nanometre can reorder as well; any
    other shape as it is."""
    if form == "arc":
        return [points, (points[1], points[0], points[2])]
    if form != "loop" or not points:
        return [points]
    starts = [k for k, point in enumerate(points) if _near(first, point, tolerance_nm)]
    if not starts:
        starts = [min(range(len(points)), key=lambda k: math.dist(first, points[k]))]
    orders = []
    for k in starts:
        forward = points[k:] + points[:k]
        orders += [forward, forward[:1] + forward[:0:-1]]
    return orders


def _geometry_gap(a: tuple, b: tuple, tolerance_nm: int) -> float:
    """How far apart two geometry() forms are: the summed distance of their points, in
    the order of the second that brings them nearest (_alignments); infinite between
    forms of different kinds or sizes."""
    (form, points), (other_form, other) = a, b
    if form != other_form or len(points) != len(other):
        return math.inf
    if not points:
        return 0.0
    return min(
        sum(math.dist(p, q) for p, q in zip(points, order))
        for order in _alignments(form, other, points[0], tolerance_nm)
    )


def _cross(o, a, b) -> int:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def off_arc(arc, point) -> float:
    """How far ``point`` lies from the arc (start, mid, end): its distance from the circle
    through the three points (computed exactly, then taken to a float), or from the chord
    when they are collinear; infinite when it lies across the chord from the mid point.
    An arc whose start is its end is a whole circle, whose centre KiCad puts halfway to
    the mid point (CalcArcCenter, libs/kimath/src/trigo.cpp:404-411 at 9.0.8)."""
    a, m, b = arc
    if a == b:
        cx, cy = Fraction(a[0] + m[0], 2), Fraction(a[1] + m[1], 2)
        radius = math.dist(a, m) / 2
        return abs(math.sqrt((point[0] - cx) ** 2 + (point[1] - cy) ** 2) - radius)
    if _cross(a, b, m) * _cross(a, b, point) < 0:
        return math.inf
    ax, ay = a
    mx, my = m
    bx, by = b
    d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if d == 0:
        chord = math.dist(a, b)
        return abs(_cross(a, b, point)) / chord if chord else math.dist(a, point)
    sa, sm, sb = ax * ax + ay * ay, mx * mx + my * my, bx * bx + by * by
    cx = Fraction(sa * (my - by) + sm * (by - ay) + sb * (ay - my), d)
    cy = Fraction(sa * (bx - mx) + sm * (ax - bx) + sb * (mx - ax), d)
    radius = math.sqrt((ax - cx) ** 2 + (ay - cy) ** 2)
    return abs(math.sqrt((point[0] - cx) ** 2 + (point[1] - cy) ** 2) - radius)


def _same_shape(mine: tuple, theirs: tuple, tolerance_nm: int) -> bool:
    """Whether two geometry() forms are one shape, positions within ``tolerance_nm`` in
    one of the second's orders (_alignments): every point of a loop or of any other
    shape, an arc's two ends and then its mid point by the circle (module docstring)."""
    (form, points), (their_form, their_points) = mine, theirs
    if form != their_form or len(points) != len(their_points):
        return False
    if not points:
        return True
    compared = 2 if form == "arc" else len(points)
    if not any(
        all(_near(p, q, tolerance_nm) for p, q in zip(points[:compared], order[:compared]))
        for order in _alignments(form, their_points, points[0], tolerance_nm)
    ):
        return False
    if form != "arc":
        return True
    within = max(tolerance_nm, ARC_TOLERANCE_NM)
    arc = (points[0], points[2], points[1])
    their_arc = (their_points[0], their_points[2], their_points[1])
    return off_arc(arc, their_points[2]) <= within and off_arc(their_arc, points[2]) <= within


def _shape_problems(got, want, tolerance_nm: int, what: str) -> list[str]:
    drawn, problems = pair(
        [(geometry(s), s) for s in got],
        [(geometry(s), s) for s in want],
        lambda g: (g[0][0], g[1].layer),
        lambda g: g[0][1],
        what,
        distance=lambda a, b: _geometry_gap(a[0], b[0], tolerance_nm),
    )
    for (mine, shape), (theirs, _) in drawn:
        if not _same_shape(mine, theirs, tolerance_nm):
            problems.append(f"{what} {shape.kind} on {shape.layer}: {mine[1]} against {theirs[1]}")
    return problems


def _pad_detail_problems(mine: F.Pad, theirs: F.Pad, tolerance_nm: int) -> list[str]:
    problems = []
    for name in ("offset", "delta", "chamfer", "kind"):
        if getattr(mine, name) != getattr(theirs, name):
            problems.append(
                f"pad {mine.number!r} at {mine.local}: {name} {getattr(mine, name)!r}"
                f" where KiCad has {getattr(theirs, name)!r}"
            )
    problems += _shape_problems(
        mine.primitives, theirs.primitives, tolerance_nm, f"pad {mine.number!r} primitive"
    )
    return problems


def differences(
    got: F.Footprint,
    want: F.Footprint,
    *,
    tolerance_nm: int = 0,
    position: bool = True,
    shapes: bool = False,
    details: bool = False,
    written_fields_only: bool = False,
    angles: bool = True,
    mirrors: bool = True,
    copper_count: int | None = None,
) -> list[str]:
    """Where ``got`` differs from ``want`` in the comparison form: the footprint's
    orientation and layer (and position, unless ``position`` is False), every pad's
    absolute angle and footprint-relative position, every text's style (angle,
    keep-upright, justification, mirror), layer and position, with ``shapes`` every
    graphic's geometry and layer, and with ``details`` every pad's shape offset,
    trapezoid delta, chamfered corners, kind and primitives. Every pad's layers too, with
    ``details`` or with ``copper_count``, the copper layer count of the boards compared:
    then of the copper layers only the board's count, since KiCad's writer spells a pad
    on every copper layer of its board *.Cu (formatLayers, pcbnew/pcb_io/kicad_sexpr/
    pcb_io_kicad_sexpr.cpp:1345-1429 at 9.0.8, 1550-1632 at 10.0.7), which reads back
    as all 32. Without ``angles`` the pads' and texts' angles and the texts'
    justification are left out (the footprint's own angle is kept), and without
    ``mirrors`` the texts' mirror flags. With ``written_fields_only`` the mandatory
    fields ``got`` holds as defaults are left out of both (module docstring). Positions
    within ``tolerance_nm``. Empty when they agree."""
    got, want = F.canonical(got), F.canonical(want)
    if written_fields_only:
        unwritten = {t.key for t in got.texts if t.default}
        got = got._replace(texts=tuple(t for t in got.texts if t.key not in unwritten))
        want = want._replace(texts=tuple(t for t in want.texts if t.key not in unwritten))
    if not angles:
        got, want = _without_angles(got), _without_angles(want)
    if not mirrors:
        got, want = _without_mirrors(got), _without_mirrors(want)
    problems = []
    if position and got.position != want.position:
        problems.append(f"footprint position {got.position} where KiCad has {want.position}")
    if got.orientation != want.orientation:
        problems.append(f"orientation {got.orientation!r} where KiCad has {want.orientation!r}")
    if got.layer != want.layer:
        problems.append(f"layer {got.layer} where KiCad has {want.layer}")
    pads, found = pair(
        got.pads, want.pads, lambda p: p.number, lambda p: p.local, "pad", _pad_tiebreak
    )
    problems += found
    for mine, theirs in pads:
        if mine.angle != theirs.angle:
            problems.append(
                f"pad {mine.number!r} at {mine.local}: angle {mine.angle!r}"
                f" where KiCad has {theirs.angle!r}"
            )
        if not _near(mine.local, theirs.local, tolerance_nm):
            problems.append(f"pad {mine.number!r}: at {mine.local} where KiCad has {theirs.local}")
        mine_layers, their_layers = (
            _on_board(mine.layers, copper_count),
            _on_board(theirs.layers, copper_count),
        )
        if (details or copper_count is not None) and mine_layers != their_layers:
            problems.append(
                f"pad {mine.number!r} at {mine.local}: layers {sorted(mine_layers)}"
                f" where KiCad has {sorted(their_layers)}"
            )
        if details:
            problems += _pad_detail_problems(mine, theirs, tolerance_nm)
    texts, found = pair(
        got.texts,
        want.texts,
        lambda t: (t.key, t.layer),
        lambda t: t.local,
        "text",
        _text_tiebreak,
    )
    problems += found
    for mine, theirs in texts:
        if not _same_style(mine, theirs):
            problems.append(f"text {mine.key!r}: {mine.style} where KiCad has {theirs.style}")
        if mine.layer != theirs.layer:
            problems.append(f"text {mine.key!r}: layer {mine.layer} where KiCad has {theirs.layer}")
        if not _near(mine.local, theirs.local, tolerance_nm):
            problems.append(f"text {mine.key!r}: at {mine.local} where KiCad has {theirs.local}")
    if shapes:
        problems += _shape_problems(got.shapes, want.shapes, tolerance_nm, "graphic")
    return problems


def _without_angles(fp: F.Footprint) -> F.Footprint:
    """``fp`` with every pad's and text's angle at 0 and every text centred, keeping each
    text's mirror flag: what differences( angles=False ) compares."""
    return fp._replace(
        pads=tuple(p._replace(angle=0.0) for p in fp.pads),
        texts=tuple(
            t._replace(style=t.style._replace(angle=0.0, h=T.CENTER, v=T.CENTER)) for t in fp.texts
        ),
    )


def _on_board(layers: frozenset[str], copper_count: int | None) -> frozenset[str]:
    """A pad's layers less the copper layers a board of ``copper_count`` lacks."""
    if copper_count is None:
        return layers
    board = BL.all_cu(copper_count)
    return frozenset(name for name in layers if not BL.is_copper(name) or name in board)


def _without_mirrors(fp: F.Footprint) -> F.Footprint:
    """``fp`` with every text unmirrored: what differences( mirrors=False ) compares."""
    return fp._replace(
        texts=tuple(t._replace(style=t.style._replace(mirror=False)) for t in fp.texts)
    )


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------


def _angle_atom(at) -> str | None:
    if at is None or len(at.items) < 4 or at.items[3].kind != L.NUMBER:
        return None
    return at.items[3].value.decode()


def footprint_angle_token(group) -> str | None:
    """The footprint's own (at) angle atom as written, None when it has none."""
    return _angle_atom(F.child(group, b"at"))


def tokens(group, *, major: int = 10) -> dict:
    """What a footprint's orientation edit writes, as written: the footprint's angle atom,
    and per pad and per text (in read_footprint's order, alongside its reading) the (at)
    angle atom, and for a text the (unlocked ...) word or bare atom and the justify words.
    Returns {"footprint": atom, "pads": [(Pad, atom)], "texts": [(Text, (atom, unlocked,
    justify))]}."""
    fp = F.read_footprint(group, major=major)
    pads = [(pad, _angle_atom(F.child(g, b"at"))) for pad, g in zip(fp.pads, F.pad_groups(group))]
    texts = []
    for text, g in zip(fp.texts, F.text_groups(group)):
        at = F.child(g, b"at")
        unlocked = []
        if at is not None:
            unlocked = [w.value.decode() for w in at.items[3:] if w.kind == L.SYMBOL]
        for node in F.children(g, b"unlocked"):
            unlocked.append(node.items[1].value.decode())
        effects = F.child(g, b"effects")
        justify = []
        for node in F.children(effects, b"justify") if effects is not None else []:
            justify += [w.value.decode() for w in node.items[1:]]
        texts.append((text, (_angle_atom(at), tuple(unlocked), tuple(justify))))
    return {"footprint": footprint_angle_token(group), "pads": pads, "texts": texts}


def expected_tokens(fp: F.Footprint) -> dict:
    """The tokens KiCad's writer spells for the footprint ``fp`` holds, in the shape
    ``tokens`` returns: the footprint's angle atom (fp_angle_token), each pad's
    (kicad_ref.pad.angle_token), and each text's angle atom (always written), ("yes",)
    for a text that is not keep-upright and the justify words (kicad_ref.pcb_text)."""
    return {
        "footprint": F.fp_angle_token(fp.orientation),
        "pads": [(p, PAD.angle_token(p.angle)) for p in fp.pads],
        "texts": [
            (
                t,
                (
                    T.angle_token(t.style),
                    () if t.style.keep_upright else ("yes",),
                    tuple(T.justify_words(t.style)),
                ),
            )
            for t in fp.texts
        ],
    }


#: The fields a footprint holds whether or not its file writes them (kicad_ref.footprint
#: MANDATORY_FIELDS): a file that leaves one out is read with its default, so in a
#: comparison of written tokens one side may lack it; ``differences`` compares its reading.
MANDATORY_KEYS = frozenset(("property", name) for name, _ in F.MANDATORY_FIELDS)


def token_problems(
    got: dict, want: dict, *, tolerance_nm: int = 0, footprint: bool = True
) -> list[str]:
    """Where two ``tokens`` results differ: the footprint's angle atom (unless
    ``footprint`` is False), each pad's angle atom, each text's angle atom, unlocked word
    and justify words, all compared as strings; positions as KiCad reads them, within
    ``tolerance_nm``. A mandatory field ``got`` does not write is left to ``differences``.
    Empty when they agree."""
    written = {t.key for t, _ in got["texts"]}
    want = {
        **want,
        "texts": [
            entry
            for entry in want["texts"]
            if entry[0].key in written or entry[0].key not in MANDATORY_KEYS
        ],
    }
    problems = []
    if footprint and got["footprint"] != want["footprint"]:
        problems.append(
            f"footprint angle {got['footprint']!r} where KiCad writes {want['footprint']!r}"
        )
    pads, found = pair(
        got["pads"],
        want["pads"],
        lambda p: p[0].number,
        lambda p: p[0].local,
        "pad",
        lambda a, b: _pad_tiebreak(a[0], b[0]),
    )
    problems += found
    for (mine, mine_atom), (theirs, their_atom) in pads:
        if mine_atom != their_atom:
            problems.append(
                f"pad {mine.number!r} at {mine.local}: angle {mine_atom!r}"
                f" where KiCad writes {their_atom!r}"
            )
        if not _near(mine.local, theirs.local, tolerance_nm):
            problems.append(f"pad {mine.number!r}: at {mine.local} where KiCad has {theirs.local}")
    texts, found = pair(
        got["texts"],
        want["texts"],
        lambda t: (t[0].key, t[0].layer),
        lambda t: t[0].local,
        "text",
        lambda a, b: _text_tiebreak(a[0], b[0]),
    )
    problems += found
    for (mine, mine_tokens), (theirs, their_tokens) in texts:
        if mine_tokens != their_tokens:
            problems.append(
                f"text {mine.key!r}: (angle, unlocked, justify) {mine_tokens!r}"
                f" where KiCad writes {their_tokens!r}"
            )
        if not _near(mine.local, theirs.local, tolerance_nm):
            problems.append(f"text {mine.key!r}: at {mine.local} where KiCad has {theirs.local}")
    return problems


def token_differences(
    got_group, want_group, *, tolerance_nm: int = 0, footprint: bool = True
) -> list[str]:
    """token_problems of two footprints as written."""
    return token_problems(
        tokens(got_group), tokens(want_group), tolerance_nm=tolerance_nm, footprint=footprint
    )
