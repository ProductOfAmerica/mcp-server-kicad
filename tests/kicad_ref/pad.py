"""A footprint pad's angle, ported as an oracle.

KiCad holds a pad's orientation absolute on the board, in a PADSTACK whose SetOrientation
normalizes it into [0, 360) (pcbnew/padstack.h:294-298 at 9.0.8, 348-352 at 10.0.7):

- reading, the pad's (at x y angle) sets the position footprint-relative and the
  orientation through that setter; a pad written without an angle keeps the padstack's
  default, 0 (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:5124-5140 at 9.0.8,
  6013-6029 at 10.0.7);
- PAD::Rotate turns the position about the centre and sets the orientation to
  orientation + aAngle through the same setter (pcbnew/pad.cpp:1614-1620 at 9.0.8,
  2183-2189 at 10.0.7);
- writing, the angle atom is left out when the orientation IsZero() (exactly 0) and is
  FormatAngle( orientation ) otherwise (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:
  1492-1496 at 9.0.8, 1696-1700 at 10.0.7);
- PAD::Flip (pcbnew/pad.cpp:1029-1093 at 9.0.8, 1504-1565 at 10.0.7), as FOOTPRINT::Flip
  calls it, top to bottom with the footprint's orientation held at 0: the angle becomes
  SetFPRelativeOrientation( -GetFPRelativeOrientation() ), that is the setter's
  Normalize( -( angle - 0 ) + 0 ); the chamfered corners swap top for bottom
  (mirrorBitFlags of RECT_CHAMFER_TOP_LEFT with BOTTOM_LEFT and TOP_RIGHT with
  BOTTOM_RIGHT); the shape offset and the trapezoid delta are mirrored in y, the
  primitives flipped (kicad_ref.footprint), the layers taken to their opposites;
- the chamfer words are written in the order top_left, top_right, bottom_left,
  bottom_right (pcb_io_kicad_sexpr.cpp:1587-1601 at 9.0.8, 1833-1847 at 10.0.7).

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from kicad_ref.eda_units import format_angle
from kicad_ref.trigo import normalize


def loaded_angle(written: float | None) -> float:
    """The orientation KiCad holds for a pad whose (at) angle reads ``written`` (None when
    the (at) has no angle)."""
    return 0.0 if written is None else normalize(written)


def rotate(angle: float, degrees: float) -> float:
    """PAD::Rotate's orientation: Normalize( angle + degrees )."""
    return normalize(angle + degrees)


def angle_token(angle: float) -> str | None:
    """The (at) angle atom format( PAD ) writes, None when it writes none."""
    return None if angle == 0 else format_angle(angle)


def flip_angle(angle: float) -> float:
    """PAD::Flip's orientation while the footprint holds 0: Normalize( -( angle - 0 ) + 0 )."""
    return normalize(-(angle - 0.0) + 0.0)


#: The corners PAD::Flip's top to bottom mirrorBitFlags exchange.
_CHAMFER_FLIP = {
    "top_left": "bottom_left",
    "bottom_left": "top_left",
    "top_right": "bottom_right",
    "bottom_right": "top_right",
}
#: The order format( PAD ) writes the chamfer words in.
CHAMFER_ORDER = ("top_left", "top_right", "bottom_left", "bottom_right")


def flip_chamfer(corners: frozenset[str]) -> frozenset[str]:
    """PAD::Flip's chamfered corners, top to bottom."""
    return frozenset(_CHAMFER_FLIP[corner] for corner in corners)
