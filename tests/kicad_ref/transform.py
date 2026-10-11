"""KiCad's schematic symbol transform, ported as an oracle.

A TRANSFORM (libs/kimath/include/transform.h:45-108, libs/kimath/src/transform.cpp;
both files identical at 9.0.8 and 10.0.7) is four ints (x1, y1, x2, y2), constructed in
that order (transform.h:63-68):

- TransformCoordinate (transform.cpp:44-47): (x1 * x + y1 * y, x2 * x + y2 * y).
- InverseTransform (59-78): det = x1 * y2 - x2 * y1, then y2 / det, -y1 / det, -x2 / det,
  x1 / det in C++ integer division (det is +1 or -1 for every symbol transform).

The orientation codes are SYMBOL_ORIENTATION_T (eeschema/symbol.h:34-45 at both tags):
SYM_NORMAL 0, SYM_ROTATE_CLOCKWISE 1, SYM_ROTATE_COUNTERCLOCKWISE 2, SYM_ORIENT_0 to
SYM_ORIENT_270 3 to 6, SYM_MIRROR_X 0x100, SYM_MIRROR_Y 0x200.

SCH_SYMBOL::SetOrientation (eeschema/sch_symbol.cpp:1766-1916 at 9.0.8, 2421-2571 at
10.0.7, the same switch): SYM_ORIENT_0 and SYM_NORMAL reset to the identity; the two
rotations and the two mirrors are incremental, multiplying the current matrix by a
temporary one (1910-1913: the new matrix applies the old one first and the temporary
one after it); SYM_ORIENT_90/180/270 reset and then rotate counterclockwise once or
twice, or clockwise once; a rotation code plus mirror bits sets the rotation and then
mirrors X before Y. Any other code fails an assertion and leaves the matrix alone in a
release build; the port raises instead.

SCH_SYMBOL::GetOrientation (1919-1963 at 9.0.8, 2574-2616 at 10.0.7; 10.0.7 only adds
a SetParentGroup( nullptr ) on its copy) walks twelve codes on a copy of the symbol,
SetOrientation on that copy each time, so the incremental SYM_MIRROR_Y in the list
acts on whatever the previous code left; the first code whose matrix equals the
symbol's wins.

The schematic parser (eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp,
3071-3098 at 9.0.8, 3188-3215 at 10.0.7): (at x y angle) sets the position and then
REPLACES the matrix with one of four literals chosen by static_cast<int>( parseDouble )
(so 90.5 is read as 90 and -0.5 as 0; anything else is refused), and (mirror x|y)
calls SetOrientation( SYM_MIRROR_X or SYM_MIRROR_Y ), incrementally, in file order. A
mirror written before (at) is therefore lost. The axis must be the bare keyword x or
y: the lexer gives a quoted string no keyword id.

The writer (saveSymbol, eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp:652-689 at
9.0.8, 700-735 at 10.0.7) writes GetOrientation's rotation as the (at) angle and its
mirror bits as (mirror x) or (mirror y).

Library pins (the same parser, 1597-1610 at 9.0.8, 1671-1684 at 10.0.7): the position
is parseXY( true ), which negates Y (sch_io_kicad_sexpr_parser.h:148-157 at 9.0.8,
170-179 at 10.0.7), and the orientation is parseInt( "pin orientation" ), NeedNUMBER
then strtol (128-137 at 9.0.8, 150-159 at 10.0.7), mapped 0 right, 90 up, 180 left,
270 down. strtol stops at the first byte that is not a digit, so it reads 0.9e2 as 0
(a float parse would read 90) and .5 as 0.

A pin's connection point is SCH_SYMBOL::GetPinPhysicalPosition (sch_symbol.cpp:
2327-2333 at 9.0.8, 2983-2989 at 10.0.7; SCH_PIN::GetPosition, sch_pin.cpp:222-228 and
254-260, says the same): the transformed library position plus the symbol position.
SCH_PIN::PinDrawOrient (sch_pin.cpp:1384-1416 at 9.0.8, 1232-1264 at 10.0.7) transforms
a unit vector pointing from the connection point into the body and classifies it.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from collections.abc import Iterable

from kicad_ref.dsn_lexer import STRING, Token, is_number
from kicad_ref.eda_units import INT_MAX, INT_MIN
from kicad_ref.parse_units import ParseRefusal, parse_double

#: (x1, y1, x2, y2): x' = x1 * x + y1 * y, y' = x2 * x + y2 * y.
Matrix = tuple[int, int, int, int]
IDENTITY: Matrix = (1, 0, 0, 1)

SYM_NORMAL = 0
SYM_ROTATE_CLOCKWISE = 1
SYM_ROTATE_COUNTERCLOCKWISE = 2
SYM_ORIENT_0 = 3
SYM_ORIENT_90 = 4
SYM_ORIENT_180 = 5
SYM_ORIENT_270 = 6
SYM_MIRROR_X = 0x100
SYM_MIRROR_Y = 0x200

#: The parser's literals for (at x y angle), keyed by the truncated angle (3076-3079 at
#: 9.0.8, 3193-3196 at 10.0.7).
PARSER_ROTATIONS: dict[int, Matrix] = {
    0: (1, 0, 0, 1),
    90: (0, 1, -1, 0),
    180: (-1, 0, 0, -1),
    270: (0, -1, 1, 0),
}
#: SetOrientation's temporary matrices for the incremental rotations (sch_symbol.cpp:
#: 1778-1792 at 9.0.8).
ROTATE_TEMPS: dict[str, Matrix] = {"ccw": (0, 1, -1, 0), "cw": (0, -1, 1, 0)}
#: SetOrientation's temporary matrices for the incremental mirrors (1794-1808 at 9.0.8).
MIRROR_TEMPS: dict[str, Matrix] = {"x": (1, 0, 0, -1), "y": (-1, 0, 0, 1)}

#: The order GetOrientation tries the codes in (sch_symbol.cpp:1928-1942 at 9.0.8, from
#: 2583 at 10.0.7).
_GET_ORIENTATION_ORDER = (
    SYM_ORIENT_0,
    SYM_ORIENT_90,
    SYM_ORIENT_180,
    SYM_ORIENT_270,
    SYM_MIRROR_X + SYM_ORIENT_0,
    SYM_MIRROR_X + SYM_ORIENT_90,
    SYM_MIRROR_X + SYM_ORIENT_270,
    SYM_MIRROR_Y,
    SYM_MIRROR_Y + SYM_ORIENT_0,
    SYM_MIRROR_Y + SYM_ORIENT_90,
    SYM_MIRROR_Y + SYM_ORIENT_180,
    SYM_MIRROR_Y + SYM_ORIENT_270,
)

#: PinDrawOrient's unit vector from the connection point into the body (sch_pin.cpp:
#: 1389-1396 at 9.0.8; the default case is PIN_RIGHT).
PIN_END: dict[str, tuple[int, int]] = {
    "right": (1, 0),
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
}
_PIN_ORIENTATIONS = {0: "right", 90: "up", 180: "left", 270: "down"}


def transform_coordinate(m: Matrix, x: int, y: int) -> tuple[int, int]:
    """TRANSFORM::TransformCoordinate."""
    x1, y1, x2, y2 = m
    return (x1 * x) + (y1 * y), (x2 * x) + (y2 * y)


def _cdiv(a: int, b: int) -> int:
    """C++ integer division: the quotient truncated toward zero."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def inverse_transform(m: Matrix) -> Matrix:
    """TRANSFORM::InverseTransform."""
    x1, y1, x2, y2 = m
    det = x1 * y2 - x2 * y1
    if det == 0:
        raise ValueError(f"{m} has no inverse")
    return (_cdiv(y2, det), _cdiv(-y1, det), _cdiv(-x2, det), _cdiv(x1, det))


def compose_incremental(m: Matrix, temp: Matrix) -> Matrix:
    """SetOrientation's product (1910-1913 at 9.0.8): ``m`` applied first, then ``temp``."""
    mx1, my1, mx2, my2 = m
    tx1, ty1, tx2, ty2 = temp
    return (
        mx1 * tx1 + mx2 * ty1,
        my1 * tx1 + my2 * ty1,
        mx1 * tx2 + mx2 * ty2,
        my1 * tx2 + my2 * ty2,
    )


_ROTATION_CODES = (SYM_ORIENT_0, SYM_ORIENT_90, SYM_ORIENT_180, SYM_ORIENT_270)


def set_orientation(m: Matrix, code: int) -> Matrix:
    """SCH_SYMBOL::SetOrientation( code ) on a symbol whose matrix is ``m``."""
    if code in (SYM_ORIENT_0, SYM_NORMAL):
        return IDENTITY
    if code == SYM_ROTATE_COUNTERCLOCKWISE:
        return compose_incremental(m, ROTATE_TEMPS["ccw"])
    if code == SYM_ROTATE_CLOCKWISE:
        return compose_incremental(m, ROTATE_TEMPS["cw"])
    if code == SYM_MIRROR_Y:
        return compose_incremental(m, MIRROR_TEMPS["y"])
    if code == SYM_MIRROR_X:
        return compose_incremental(m, MIRROR_TEMPS["x"])
    if code == SYM_ORIENT_90:
        return set_orientation(set_orientation(m, SYM_ORIENT_0), SYM_ROTATE_COUNTERCLOCKWISE)
    if code == SYM_ORIENT_180:
        m = set_orientation(m, SYM_ORIENT_0)
        m = set_orientation(m, SYM_ROTATE_COUNTERCLOCKWISE)
        return set_orientation(m, SYM_ROTATE_COUNTERCLOCKWISE)
    if code == SYM_ORIENT_270:
        return set_orientation(set_orientation(m, SYM_ORIENT_0), SYM_ROTATE_CLOCKWISE)
    rotation, mirrors = code & ~(SYM_MIRROR_X | SYM_MIRROR_Y), code & (SYM_MIRROR_X | SYM_MIRROR_Y)
    if rotation in _ROTATION_CODES and mirrors:
        m = set_orientation(m, rotation)
        if mirrors & SYM_MIRROR_X:
            m = set_orientation(m, SYM_MIRROR_X)
        if mirrors & SYM_MIRROR_Y:
            m = set_orientation(m, SYM_MIRROR_Y)
        return m
    raise ValueError(f"invalid schematic symbol orientation {code:#x}")


def get_orientation(m: Matrix) -> int:
    """SCH_SYMBOL::GetOrientation of a symbol whose matrix is ``m``."""
    temp = m  # SCH_SYMBOL temp( *this ): the copy starts with the symbol's own matrix
    for code in _GET_ORIENTATION_ORDER:
        temp = set_orientation(temp, code)
        if temp == m:
            return code
    raise ValueError(f"{m} is no symbol orientation (KiCad asserts and returns SYM_NORMAL)")


def _token_bytes(token) -> bytes:
    if isinstance(token, Token):
        return token.value
    return token.encode("utf-8") if isinstance(token, str) else bytes(token)


def parse_symbol_transform(children: Iterable[tuple[str, object]]) -> Matrix:
    """The matrix KiCad's parser builds from a placed symbol's (at) and (mirror) items.

    ``children`` holds ("at", angle_token) and ("mirror", axis_token) in file order.
    A token is a dsn_lexer Token, bytes or str (bytes and str are read as bare tokens);
    an angle token of None means the (at) list has no angle, which NeedNUMBER refuses.
    Raises ParseRefusal where KiCad's parser refuses the file.
    """
    m = IDENTITY  # a new SCH_SYMBOL's transform is TRANSFORM(), the identity
    for head, token in children:
        if head == "at":
            if token is None:
                raise ParseRefusal("need a number for 'symbol orientation'")
            if isinstance(token, Token) and token.kind == STRING:
                raise ParseRefusal("need a number for 'symbol orientation', got a string")
            angle = int(parse_double(_token_bytes(token)))  # static_cast<int>: toward zero
            if angle not in PARSER_ROTATIONS:
                raise ParseRefusal(f"Expecting 0, 90, 180, or 270, got {token!r}")
            m = PARSER_ROTATIONS[angle]
        elif head == "mirror":
            if token is None or (isinstance(token, Token) and token.kind == STRING):
                raise ParseRefusal("Expecting x or y")
            axis = _token_bytes(token)
            if axis == b"x":
                m = set_orientation(m, SYM_MIRROR_X)
            elif axis == b"y":
                m = set_orientation(m, SYM_MIRROR_Y)
            else:
                raise ParseRefusal(f"Expecting x or y, got {token!r}")
        else:
            raise ValueError(f"{head!r} is neither 'at' nor 'mirror'")
    return m


def written_orientation(m: Matrix) -> tuple[int, str]:
    """(angle, mirror axis) as saveSymbol writes ``m``: axis "", "x" or "y"."""
    code = get_orientation(m)
    rotation = code & ~(SYM_MIRROR_X | SYM_MIRROR_Y)
    angle = {SYM_ORIENT_90: 90, SYM_ORIENT_180: 180, SYM_ORIENT_270: 270}.get(rotation, 0)
    axis = ("x" if code & SYM_MIRROR_X else "") + ("y" if code & SYM_MIRROR_Y else "")
    return angle, axis


def _strtol(raw: bytes) -> int:
    """strtol( text, nullptr, 10 ) of a token NeedNUMBER accepted: sign, then digits."""
    i, sign = 0, 1
    if raw[:1] in (b"+", b"-"):
        sign = -1 if raw[:1] == b"-" else 1
        i = 1
    j = i
    while j < len(raw) and 0x30 <= raw[j] <= 0x39:
        j += 1
    return sign * int(raw[i:j]) if j > i else 0


def pin_orientation(token) -> str:
    """parseInt( "pin orientation" ) mapped as the parser maps it.

    "right", "up", "left" or "down"; ParseRefusal for a token that is not a NUMBER or
    whose strtol value is not 0, 90, 180 or 270. A value outside int is refused too:
    strtol then saturates to long, which is 32 bits on Windows and 64 on Linux, and the
    cast to int differs between the two.
    """
    if isinstance(token, Token) and token.kind == STRING:
        raise ParseRefusal("need a number for 'pin orientation', got a string")
    raw = _token_bytes(token)
    if not is_number(raw):
        raise ParseRefusal(f"need a number for 'pin orientation', got {raw!r}")
    value = _strtol(raw)
    if not INT_MIN <= value <= INT_MAX:
        raise ParseRefusal(f"pin orientation {raw!r} is outside int")
    if value not in _PIN_ORIENTATIONS:
        raise ParseRefusal(f"Expecting 0, 90, 180, or 270, got {raw!r}")
    return _PIN_ORIENTATIONS[value]


def pin_draw_orient(m: Matrix, orientation: str) -> str:
    """SCH_PIN::PinDrawOrient: where the pin points, from its end into the body."""
    end = transform_coordinate(m, *PIN_END[orientation])
    if end[0] == 0:
        return "down" if end[1] > 0 else "up"
    return "left" if end[0] < 0 else "right"


def pin_outward(m: Matrix, orientation: str) -> tuple[int, int]:
    """The unit vector a wire leaves the pin's connection point along: away from the body."""
    into_body = PIN_END[pin_draw_orient(m, orientation)]
    return -into_body[0], -into_body[1]


def pin_position(symbol_xy: tuple[int, int], m: Matrix, lib_xy: tuple[int, int]) -> tuple[int, int]:
    """GetPinPhysicalPosition for a library pin written (at lib_x lib_y ...).

    ``lib_xy`` is the pin's position as written in the library, in internal units;
    parseXY( true ) negates its Y before the transform applies.
    """
    dx, dy = transform_coordinate(m, lib_xy[0], -lib_xy[1])
    return symbol_xy[0] + dx, symbol_xy[1] + dy
