"""A placed symbol's fields as KiCad reads, holds and writes them, ported as an oracle.

Read from KiCad's source at 9.0.8, with 10.0.7's lines in brackets; the code cited is the
same at both tags.

Holding (eeschema/sch_field.cpp): a field of a placed symbol stores its text position in
the symbol's own, untransformed frame. SCH_FIELD::GetPosition (1615-1628 [1398-1411]) is
the drawn position S + T * (textPos - S), S being the symbol's position and T its transform
(kicad_ref.transform), and SCH_FIELD::SetPosition (1595-1612 [1378-1395]) stores
textPos = S + T^-1 * (P - S) with the S and T in force when it is called.

Writing (eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp): a property's
(at x y angle) is GetPosition() and the field's own text angle (910-918 [1019-1027]),
written after the symbol's (at) and (mirror) (672 and 685 [719 and 732]).

Reading (eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp): the placed symbol's
children in file order, on a symbol the default constructor built at (0, 0) with the
identity (eeschema/sch_symbol.cpp:70-74 and 174-182 [74-78 and 178-186]):

- (at x y angle) (3071-3085 [3188-3202]) calls SCH_SYMBOL::SetPosition, which is Move by
  the difference, so the symbol's position and every field read so far move by it
  (eeschema/sch_symbol.h:701-710 and 786 [788-797 and 873]); then the transform is
  replaced by the rotation (transform.parse_symbol_transform's rule);
- (mirror x|y) (3087-3098 [3204-3215]) composes a mirror onto the transform and moves no
  field;
- (property ...) (3265-3268 [3504-3507]) goes through parseSchField, where every (at) calls
  SCH_FIELD::SetPosition with the symbol's position and transform at that point and sets
  the text angle (2304-2308 [2404-2408]), so a property's last (at) is the one that counts.

So textPos - S, the field's offset in the symbol's frame, is what KiCad keeps: a later
(at) moves the symbol and its fields by one vector, and once loaded, Move translates both
alike while Rotate and the two mirrors move each field by the symbol's own displacement
only (sch_symbol.cpp:2190-2242 [2827-2879]). None of them touches a text angle.

SCH_FIELD::GetDrawRotation (sch_field.cpp:682-701 [446-465]): the text angle, horizontal
and vertical swapped when the transform's y1 is not 0, where EDA_ANGLE::IsHorizontal means
0 or 180 exactly (libs/kimath/include/geometry/eda_angle.h:142-146 at both tags).

Not modelled, and refused rather than guessed: a property with no (at), whose text position
is then SCH_FIELD's default; two properties of one name, which KiCad 9 tells apart by field
id and KiCad 10 renames (parser 3285-3298 at 9.0.8, 3526-3555 at 10.0.7); and in
draw_rotation, a text angle other than a quarter turn.

test_ladder_r3_judges.py checks it against kicad-cli's plotted field anchors
(test_field_port_draws_where_kicad_cli_draws). This module imports nothing from
mcp_server_kicad.
"""

from __future__ import annotations

from typing import NamedTuple

from kicad_ref import dsn_lexer as L
from kicad_ref import transform as T
from kicad_ref.parse_units import ParseRefusal, parse_double, parse_sch_internal_units

Point = tuple[int, int]
#: The symbol's position and transform while KiCad reads one of its children.
Frame = tuple[Point, T.Matrix]


class Field(NamedTuple):
    """One property of a placed symbol as KiCad's parser leaves it.

    ``written`` is the (at) x and y KiCad read (internal units) and ``angle`` that (at)'s
    text angle; ``frame`` is the symbol's position and transform when the property was
    read; ``offset`` is textPos - S, the field in the symbol's own frame, which every later
    (at), Move, Rotate and mirror keeps.
    """

    name: str
    written: Point
    angle: float
    frame: Frame
    offset: Point


class PlacedSymbol(NamedTuple):
    """A placed symbol after KiCad read all its children: its final position and transform,
    and its properties by name, in file order."""

    position: Point
    transform: T.Matrix
    fields: dict[str, Field]

    def drawn(self, name: str) -> Point:
        """SCH_FIELD::GetPosition of the field ``name``: where KiCad draws it, and what its
        writer prints as the property's (at)."""
        return carry_offset((self.position, self.transform), self.fields[name].offset)


def get_position(frame: Frame, text_pos: Point) -> Point:
    """SCH_FIELD::GetPosition: S + T * (textPos - S)."""
    (sx, sy), matrix = frame
    dx, dy = T.transform_coordinate(matrix, text_pos[0] - sx, text_pos[1] - sy)
    return sx + dx, sy + dy


def set_position(frame: Frame, position: Point) -> Point:
    """SCH_FIELD::SetPosition: the textPos it stores, S + T^-1 * (P - S)."""
    (sx, sy), matrix = frame
    dx, dy = T.transform_coordinate(T.inverse_transform(matrix), position[0] - sx, position[1] - sy)
    return sx + dx, sy + dy


def carry_offset(frame: Frame, offset: Point) -> Point:
    """The (at) that KiCad, reading it in ``frame``, takes as the field at ``offset`` in the
    symbol's frame: GetPosition of textPos = S + offset."""
    (sx, sy), _ = frame
    return get_position(frame, (sx + offset[0], sy + offset[1]))


def _head(item) -> bytes | None:
    if item.kind == L.GROUP and item.items and item.items[0].kind in L.ATOM_KINDS:
        return item.items[0].value
    return None


def _coordinate(token, *, major: int) -> int:
    """parseInternalUnits of a token: NeedNUMBER refuses anything but a NUMBER."""
    if token.kind != L.NUMBER:
        raise ParseRefusal(f"need a number, got {token.value!r}")
    return parse_sch_internal_units(token.value, major=major)


def _xy(at, *, major: int) -> Point:
    if len(at.items) < 3:
        raise ParseRefusal(f"an (at ...) with no x and y at byte {at.start}")
    return _coordinate(at.items[1], major=major), _coordinate(at.items[2], major=major)


def property_name(prop) -> str:
    """A property's name as parseSchField reads it: a leading bare ``private`` is skipped
    (parser 2201-2209 at 9.0.8, 2290-2296 at 10.0.7)."""
    parts = prop.items[1:]
    if parts and parts[0].kind == L.SYMBOL and parts[0].value == b"private":
        parts = parts[1:]
    if not parts or parts[0].kind not in L.ATOM_KINDS:
        raise ParseRefusal(f"a property with no name at byte {prop.start}")
    return parts[0].value.decode("utf-8", "surrogateescape")


def read_placed_symbol(symbol, *, major: int) -> PlacedSymbol:
    """What KiCad ``major`` holds after reading the placed symbol ``symbol`` (a lexer Group
    of a (symbol ...) list), child by child in file order.

    Raises ParseRefusal where KiCad's parser refuses the symbol (a coordinate that is not a
    number, an orientation it cannot load, a property (at) with no text angle), LookupError
    for a property with no (at) and ValueError for a name used twice, neither of which this
    port models.
    """
    position: Point = (0, 0)
    state: list[tuple[str, object]] = []  # the last (at) and the mirrors after it
    matrix: T.Matrix = T.IDENTITY
    fields: dict[str, Field] = {}
    for item in symbol.items[1:]:
        head = _head(item)
        if head == b"at":
            # Move: the symbol and every field read so far shift by one vector, so each
            # field's offset is unchanged.
            position = _xy(item, major=major)
            state = [("at", item.items[3] if len(item.items) > 3 else None)]
            matrix = T.parse_symbol_transform(state)
        elif head == b"mirror":
            state.append(("mirror", item.items[1] if len(item.items) > 1 else None))
            matrix = T.parse_symbol_transform(state)
        elif head == b"property":
            name = property_name(item)
            ats = [c for c in item.items if _head(c) == b"at"]
            if not ats:
                raise LookupError(f"property {name!r} at byte {item.start} has no (at ...)")
            if name in fields:
                raise ValueError(f"property {name!r} appears twice in the symbol")
            at = ats[-1]
            written = _xy(at, major=major)
            if len(at.items) < 4 or at.items[3].kind != L.NUMBER:
                raise ParseRefusal(f"property {name!r} has no text angle")
            frame = (position, matrix)
            text_pos = set_position(frame, written)
            offset = (text_pos[0] - position[0], text_pos[1] - position[1])
            fields[name] = Field(name, written, parse_double(at.items[3].value), frame, offset)
    return PlacedSymbol(position, matrix, fields)


def draw_rotation(matrix: T.Matrix, angle: float) -> int:
    """SCH_FIELD::GetDrawRotation for a text angle of a quarter turn: 0 for horizontal text,
    90 for vertical. ValueError for any other angle, which this port does not model."""
    if angle in (0.0, 180.0):
        horizontal = True
    elif angle in (90.0, 270.0):
        horizontal = False
    else:
        raise ValueError(f"text angle {angle} is not a quarter turn")
    if matrix[1]:
        horizontal = not horizontal
    return 0 if horizontal else 90
