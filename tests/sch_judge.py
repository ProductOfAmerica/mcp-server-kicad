"""Schematic wires, buses, junctions and groups as KiCad's lexer reads them.

The judge of the wire and junction tests. It reads with the DSNLEXER port
(kicad_ref.dsn_lexer) and the parser's number rule (kicad_ref.parse_units), never with
the CST under test, and imports nothing from mcp_server_kicad. Coordinates are schematic
internal units (100 nm), as KiCad holds them; a location is edit_judge's path of a
top-level item, ((0, "kicad_sch"), (index, head)).

- lines(data): every top-level wire and bus, with its two points, uuid and stroke bytes.
  KiCad's parser reads exactly two points per line (parseLine,
  sch_io_kicad_sexpr_parser.cpp:3920-3937 at 9.0.8, 4270-4287 at 10.0.7), so a line
  holding another count is a premise this judge refuses rather than reads.
- junctions(data): every top-level junction's position.
- junctions_on_interiors(data): each junction strictly inside a wire or bus
  (kicad_ref.sch_line.strictly_inside), with that line. KiCad's own editor never leaves
  one: it splits every line through a junction it places (AddJunction,
  eeschema/bus-wire-junction.cpp:188-199 at 9.0.8). kicad-cli 9 cuts the line there and
  keeps only its first part, because SCHEMATIC::BreakSegment adds the second half to the
  sheet only through a schematic holder, which kicad-cli has none of
  (eeschema/schematic.cpp:1055-1072 and 1090-1113 at 9.0.8).
- groups(data): KiCad 10's top-level (group "name" (uuid ...) (members ...)) lists
  (saveGroup, sch_io_kicad_sexpr.cpp:1633-1660 at 10.0.7), each with its member uuids.
- assert_split_edit(before, after, heads=...): the edit is lines split as SCH_LINE::BreakAt
  splits them (eeschema/sch_line.cpp:574-583 at 9.0.8, 578-587 at 10.0.7) plus whole
  top-level items, and nothing else, judged by edit_judge.diff and checked by uuid.
- assert_merge_edit(before, after, heads=...): its mirror for KiCad's junction delete,
  which joins collinear lines that met at the junction (SCH_LINE::MergeOverlap).
- placed_symbol(data, reference): the location of the placed symbol of that Reference and
  KiCad's reading of its position, transform and fields (kicad_ref.sch_field).
"""

from __future__ import annotations

from typing import NamedTuple

import edit_judge as J
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U
from kicad_ref import parse_units as P
from kicad_ref import sch_field, sch_line

Point = tuple[int, int]
Location = tuple[tuple[int, "str | None"], ...]


class JudgeError(RuntimeError):
    """The judge cannot read the file: a broken premise, never the defect under test."""


class Line(NamedTuple):
    """One wire or bus: ``kind`` is "wire" or "bus"; ``stroke`` the (stroke ...) list's bytes."""

    kind: str
    start: Point
    end: Point
    uuid: bytes | None
    stroke: bytes | None
    location: Location


def _head(group) -> str | None:
    first = group.items[0] if group.items else None
    if isinstance(first, L.Token) and first.kind in L.ATOM_KINDS:
        return first.value.decode("utf-8", "surrogateescape")
    return None


def _top(data: bytes, heads: tuple[str, ...]):
    """(location, group) of each list of the root whose head is in ``heads``."""
    tree = L.tree(data, knows_bar=L.knows_bar_of(data))
    roots = [g for g in tree if isinstance(g, L.Group) and _head(g) == "kicad_sch"]
    if len(roots) != 1:
        raise JudgeError("not one (kicad_sch ...) list at the top of the file")
    for index, item in enumerate(roots[0].items):
        if isinstance(item, L.Group) and _head(item) in heads:
            yield ((0, "kicad_sch"), (index, _head(item))), item


def _child(group, head: str):
    return next((c for c in group.items if isinstance(c, L.Group) and _head(c) == head), None)


def _xy(group, major: int) -> Point:
    """An (xy x y) or (at x y ...) list's position, read as parseInternalUnits reads it."""
    x, y = group.items[1:3]
    return (
        P.parse_sch_internal_units(x.value, major=major),
        P.parse_sch_internal_units(y.value, major=major),
    )


def _uuid(group) -> bytes | None:
    found = _child(group, "uuid")
    return found.items[1].value if found is not None and len(found.items) > 1 else None


def lines(data: bytes, *, major: int = 9) -> list[Line]:
    """Every top-level wire and bus of the schematic ``data``, in file order."""
    out = []
    for location, group in _top(data, ("wire", "bus")):
        pts = _child(group, "pts")
        xys = [c for c in pts.items if isinstance(c, L.Group) and _head(c) == "xy"] if pts else []
        if len(xys) != 2:
            raise JudgeError(f"a {location[-1][1]} with {len(xys)} points at byte {group.start}")
        stroke = _child(group, "stroke")
        out.append(
            Line(
                str(location[-1][1]),
                _xy(xys[0], major),
                _xy(xys[1], major),
                _uuid(group),
                data[stroke.start : stroke.end] if stroke is not None else None,
                location,
            )
        )
    return out


def junctions(data: bytes, *, major: int = 9) -> list[Point]:
    """Every top-level junction's position, in file order."""
    return [_xy(_child(group, "at"), major) for _, group in _top(data, ("junction",))]


def junctions_on_interiors(data: bytes, *, major: int = 9) -> list[tuple[Point, Line]]:
    """(junction, line) for each junction strictly inside a wire or bus of ``data``."""
    every = lines(data, major=major)
    return [
        (point, line)
        for point in junctions(data, major=major)
        for line in every
        if sch_line.strictly_inside(line.start, line.end, point)
    ]


def groups(data: bytes) -> dict[bytes | None, list[bytes]]:
    """{group uuid: member uuids in file order} for each top-level group."""
    out = {}
    for _, group in _top(data, ("group",)):
        members = _child(group, "members")
        out[_uuid(group)] = [m.value for m in members.items[1:]] if members is not None else []
    return out


class Split(NamedTuple):
    """One line split as SCH_LINE::BreakAt splits it: the line before, the same line (by
    uuid) after, and the copy that now carries the rest of it."""

    old: Line
    kept: Line
    copy: Line

    @property
    def point(self) -> Point:
        return self.kept.end


_ROOT = ((0, "kicad_sch"),)


def assert_split_edit(before: bytes, after: bytes, *, heads) -> list[Split]:
    """Assert ``after`` is ``before`` with lines split as BreakAt splits them and whole items
    inserted at the top level, and nothing else; return the splits in file order.

    - every atom that changed is an atom of the second (xy ...) of a wire or bus of
      ``before`` (edit_judge.diff, KiCad's lexer), and every other edit inserts a top-level
      item; the inserted items' labels are ``heads`` as a multiset, the copies included;
    - each line whose end moved kept its kind, uuid, start and stroke bytes, and now ends
      strictly inside its old extent; exactly one line of ``after`` whose uuid no line of
      ``before`` carries has the same kind and stroke bytes and runs from that point to the
      old end (kicad_ref.sch_line.break_at); no uuid is carried by two lines.

    Raises AssertionError on any difference, so a strict xfail can expect it.
    """
    moved: set[Location] = set()
    inserted = []
    for e in J.diff(before, after):
        if e.op == "insert" and e.path == _ROOT:
            inserted.append(e.label)
        elif (
            e.op == "atom"
            and len(e.path) == 4
            and e.path[0] == _ROOT[0]
            and e.path[1][1] in ("wire", "bus")
            and e.path[2][1] == "pts"
            and e.path[3] == (2, "xy")
            and e.index in (1, 2)
        ):
            moved.add(e.path[:2])
        else:
            raise AssertionError(f"neither a line's end moving nor a whole item: {e.describe()}")
    if sorted(map(str, inserted)) != sorted(map(str, heads)):
        raise AssertionError(f"inserted {sorted(map(str, inserted))}, expected {sorted(heads)}")
    old_lines = {line.location: line for line in lines(before)}
    old_uuids = {line.uuid for line in old_lines.values()}
    new_lines = lines(after)
    uuids = [line.uuid for line in new_lines]
    if len(set(uuids)) != len(uuids):
        raise AssertionError("two lines carry one uuid")
    fresh = [line for line in new_lines if line.uuid not in old_uuids]
    splits = []
    for location in sorted(moved):
        old = old_lines[location]
        (kept,) = [line for line in new_lines if line.uuid == old.uuid] or [None]
        if kept is None:
            raise AssertionError(f"the line {old.uuid!r} whose end moved is gone")
        if (kept.kind, kept.start, kept.stroke) != (old.kind, old.start, old.stroke):
            raise AssertionError(f"{old.uuid!r} did not keep its kind, start and stroke")
        if not sch_line.strictly_inside(old.start, old.end, kept.end):
            raise AssertionError(f"{old.uuid!r} now ends at {kept.end}, not inside its old extent")
        want = sch_line.break_at(old.start, old.end, kept.end)[1]
        copies = [
            line
            for line in fresh
            if (line.kind, line.stroke, (line.start, line.end)) == (old.kind, old.stroke, want)
        ]
        if len(copies) != 1:
            raise AssertionError(f"{len(copies)} new lines carry the rest of {old.uuid!r}, {want}")
        splits.append(Split(old, kept, copies[0]))
    return splits


class Merge(NamedTuple):
    """Two collinear lines made one, as SCH_LINE::MergeOverlap makes them: the line that
    stays (before and after) and the line it absorbed, which is gone."""

    old: Line
    new: Line
    absorbed: Line


def _collinear(a: Line, b: Line) -> bool:
    """Whether the four ends of two lines lie on one straight line."""
    base, tip = (a.start, a.end) if a.start != a.end else (b.start, b.end)
    if base == tip:
        return a.start == b.start
    dx, dy = tip[0] - base[0], tip[1] - base[1]
    return all(
        dx * (q[1] - base[1]) == dy * (q[0] - base[0]) for q in (a.start, a.end, b.start, b.end)
    )


def _union(a: Line, b: Line) -> set[Point]:
    """The two extreme points of two collinear lines taken together (x first, then y, the
    order MergeOverlap sorts ends by)."""
    points = [a.start, a.end, b.start, b.end]
    return {min(points), max(points)}


def assert_merge_edit(before: bytes, after: bytes, *, heads) -> list[Merge]:
    """Assert ``after`` is ``before`` with whole top-level items removed and some lines' end
    atoms moved, as KiCad's junction delete leaves it (SCH_EDIT_FRAME::DeleteJunction,
    eeschema/bus-wire-junction.cpp:115-185 at 9.0.8, 117-187 at 10.0.7), and nothing else;
    return the merges.

    - every atom that changed is an atom of an (xy ...) of a wire or bus of ``before``, and
      every other edit removes a top-level item; the removed items' labels are ``heads`` as
      a multiset;
    - each line whose atoms changed kept its kind, uuid and stroke bytes, one of its two
      points, and now spans the union of its old extent and one removed line of the same
      kind collinear with it (SCH_LINE::MergeOverlap, sch_line.cpp:453-571 at 9.0.8).
    """
    moved: set[Location] = set()
    removed = []
    for e in J.diff(before, after):
        if e.op == "delete" and e.path == _ROOT:
            removed.append(e.label)
        elif (
            e.op == "atom"
            and len(e.path) == 4
            and e.path[0] == _ROOT[0]
            and e.path[1][1] in ("wire", "bus")
            and e.path[2][1] == "pts"
            and e.path[3] in ((1, "xy"), (2, "xy"))
            and e.index in (1, 2)
        ):
            moved.add(e.path[:2])
        else:
            raise AssertionError(f"neither a line's end moving nor a removal: {e.describe()}")
    if sorted(map(str, removed)) != sorted(map(str, heads)):
        raise AssertionError(f"removed {sorted(map(str, removed))}, expected {sorted(heads)}")
    old_lines = {line.location: line for line in lines(before)}
    after_by_uuid = {line.uuid: line for line in lines(after)}
    gone = [line for line in old_lines.values() if line.uuid not in after_by_uuid]
    merges = []
    for location in sorted(moved):
        old = old_lines[location]
        new = after_by_uuid.get(old.uuid)
        if new is None or (new.kind, new.stroke) != (old.kind, old.stroke):
            raise AssertionError(f"{old.uuid!r} did not keep its kind and stroke")
        if new.start != old.start and new.end != old.end:
            raise AssertionError(f"{old.uuid!r} moved both of its points")
        found = [
            g
            for g in gone
            if g.kind == old.kind and _collinear(old, g) and {new.start, new.end} == _union(old, g)
        ]
        if len(found) != 1:
            raise AssertionError(
                f"{old.uuid!r} spans {new.start}-{new.end}, no merge of a removed line"
            )
        merges.append(Merge(old, new, found[0]))
    return merges


def iu(value: float) -> int:
    """A millimetre value on the 0.0001 mm grid in internal units, as KiCad reads its text."""
    return U.kiround(value * U.SCH_IU_PER_MM, major=9)


def reference_of(symbol) -> str | None:
    """The value of a placed symbol's Reference property, as the lexer reads it."""
    for item in symbol.items:
        if not (isinstance(item, L.Group) and _head(item) == "property"):
            continue
        parts = item.items[1:]
        if parts and parts[0].kind == L.SYMBOL and parts[0].value == b"private":
            parts = parts[1:]
        if len(parts) > 1 and parts[0].value == b"Reference" and parts[1].kind in L.ATOM_KINDS:
            return parts[1].value.decode("utf-8", "surrogateescape")
    return None


def placed_symbol(data: bytes, reference: str) -> tuple[Location, sch_field.PlacedSymbol]:
    """(location, KiCad's reading) of the one top-level placed symbol of ``data`` whose
    Reference property is ``reference`` (kicad_ref.sch_field.read_placed_symbol). KiCad 9
    and 10 must read it alike, which they do away from parseInternalUnits' clamp and its
    rounding ties; JudgeError otherwise, and for no such symbol or two."""
    found = [
        (location, group)
        for location, group in _top(data, ("symbol",))
        if _child(group, "lib_id") is not None and reference_of(group) == reference
    ]
    if len(found) != 1:
        raise JudgeError(f"{len(found)} placed symbols are {reference!r}, not one")
    location, group = found[0]
    nine, ten = (sch_field.read_placed_symbol(group, major=m) for m in (9, 10))
    if nine != ten:
        raise JudgeError(f"KiCad 9 and 10 read {reference!r} differently: {nine} / {ten}")
    return location, nine


def iu_point(point) -> Point:
    return iu(point[0]), iu(point[1])
