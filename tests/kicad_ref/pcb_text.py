"""A footprint text's angle, keep-upright flag and justification, ported as an oracle.

KiCad holds a footprint text (a field such as Reference, or an fp_text) as a PCB_TEXT
whose angle is absolute on the board. PCB_TEXT (pcbnew/pcb_text.cpp; 9.0.8 lines, 10.0.7
in brackets, the bodies identical):

- Rotate (381-390 [460-469]): the position turns about the centre through RotatePoint, and
  the angle becomes Normalize( angle + aAngle ).
- KeepUpright (321-339 [389-407]): for a keep-upright text only, when Normalize( angle )
  is 180 or more, the horizontal and the vertical justification are negated and the angle
  becomes Normalize( Normalize( angle ) + 180 ); below 180 the angle is left as it is.
  GR_TEXT_H_ALIGN_T and GR_TEXT_V_ALIGN_T hold left and top as -1, centre as 0, right and
  bottom as 1, so the negation swaps left and right and top and bottom, keeps centre, and
  leaves the mirror flag alone.
- EDA_TEXT::SetTextAngle stores the angle without normalizing it (common/eda_text.cpp:
  292-297 at 9.0.8, 258-263 at 10.0.7).

Reading, parsePCB_TEXT_effects (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:
3438-3576 at 9.0.8, 3955-4093 at 10.0.7): a footprint text starts keep-upright; a bare
``unlocked`` after the angle inside (at ...) turns that off, and so does (unlocked yes),
which sets keep-upright to the negation of its bool; an (at) without an angle leaves the
angle 0. Then the angle is made relative to the footprint and turned back:
SetTextAngle( angle - A ) and Rotate( {0, 0}, A ), A being the orientation the footprint
holds (Normalize180 of its own angle atom), so KiCad holds Normalize( ( angle - A ) + A )
(3556-3574 [4073-4091]). The double subtraction and addition are kept as written: they
can move the angle by an ulp. parseEDA_TEXT resets the justification to centre and the
mirror to off, then (justify ...) sets left, right, top, bottom and mirror in any order
(505-650 [736-859]).

Writing, format( PCB_TEXT ) (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:1956 at
9.0.8, 2265 at 10.0.7): the angle is always written, FormatAngle( angle ) (1992-1994
[2304-2306]), then (unlocked yes) for a text that is not keep-upright (1996-1997
[2308-2309]); EDA_TEXT::Format writes the justify words horizontal, vertical, mirror, and
no (justify ...) at all for a centred, unmirrored text (common/eda_text.cpp:1096-1110 at
9.0.8, 1078-1092 at 10.0.7).

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from typing import NamedTuple

from kicad_ref.eda_units import format_angle
from kicad_ref.trigo import normalize

LEFT, CENTER, RIGHT = -1, 0, 1
TOP, BOTTOM = -1, 1

_H_WORDS = {LEFT: "left", RIGHT: "right"}
_V_WORDS = {TOP: "top", BOTTOM: "bottom"}


class Style(NamedTuple):
    """What KiCad holds of a text besides its position: angle in degrees, keep-upright,
    horizontal and vertical justification (-1, 0 or 1) and the mirror flag."""

    angle: float
    keep_upright: bool
    h: int
    v: int
    mirror: bool


def loaded_angle(written: float, orientation: float) -> float:
    """The angle KiCad holds for a footprint text whose (at) angle reads ``written``, on a
    footprint holding ``orientation``: Normalize( ( written - A ) + A )."""
    return normalize((written - orientation) + orientation)


def rotate(style: Style, degrees: float) -> Style:
    """PCB_TEXT::Rotate's angle: Normalize( angle + degrees )."""
    return style._replace(angle=normalize(style.angle + degrees))


def keep_upright(style: Style) -> Style:
    """PCB_TEXT::KeepUpright."""
    if not style.keep_upright:
        return style
    angle = normalize(style.angle)
    if angle < 180.0:
        return style
    return style._replace(angle=normalize(angle + 180.0), h=-style.h, v=-style.v)


def justify_words(style: Style) -> list[str]:
    """The words of (justify ...) as EDA_TEXT::Format writes them, [] when it writes none."""
    words = []
    if style.h in _H_WORDS:
        words.append(_H_WORDS[style.h])
    if style.v in _V_WORDS:
        words.append(_V_WORDS[style.v])
    if style.mirror:
        words.append("mirror")
    return words


def angle_token(style: Style) -> str:
    """The (at) angle atom format( PCB_TEXT ) writes: always present."""
    return format_angle(style.angle)
