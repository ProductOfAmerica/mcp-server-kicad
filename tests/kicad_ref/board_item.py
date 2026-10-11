"""Where a footprint's child items sit on the board, ported as an oracle.

BOARD_ITEM (pcbnew/board_item.cpp; identical bodies at 9.0.8 and 10.0.7):

- SetFPRelativePosition (348-359 at 9.0.8, 413-424 at 10.0.7): RotatePoint the local
  position by the parent footprint's orientation, then add the footprint's position.
- GetFPRelativePosition (334-345 at 9.0.8, 399-410 at 10.0.7): subtract the footprint's
  position, then RotatePoint by the negated orientation.

The orientation is the one the footprint holds, not the text of its angle atom: the
board parser reads (at x y angle) with parseBoardUnits for x and y and
SetOrientation( EDA_ANGLE( parseDouble(), DEGREES_T ) ) for the angle
(pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:4533-4549 at 9.0.8, 5227-5243
at 10.0.7), and FOOTPRINT::SetOrientation stores it through Normalize180
(pcbnew/footprint.cpp:2641-2669 at 9.0.8, 3099-3137 at 10.0.7). So the orientation to
pass for an angle token t is trigo.normalize180(parse_units.parse_double(t)); a
footprint whose (at) has no angle has orientation 0. A pad's (at x y) is read the same
way and placed with SetFPRelativePosition (parser 5124-5140 at 9.0.8, 6013-6029 at
10.0.7), after the footprint's own (at), which KiCad writes first.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from kicad_ref.trigo import rotate_point


def fp_relative_to_board(
    fp_xy: tuple[int, int], orientation_deg: float, local_xy: tuple[int, int], *, major: int
) -> tuple[int, int]:
    """BOARD_ITEM::SetFPRelativePosition: the board position of a footprint-local point."""
    rx, ry = rotate_point(local_xy[0], local_xy[1], orientation_deg, major=major)
    return rx + fp_xy[0], ry + fp_xy[1]


def board_to_fp_relative(
    fp_xy: tuple[int, int], orientation_deg: float, board_xy: tuple[int, int], *, major: int
) -> tuple[int, int]:
    """BOARD_ITEM::GetFPRelativePosition: the footprint-local point of a board position."""
    return rotate_point(
        board_xy[0] - fp_xy[0], board_xy[1] - fp_xy[1], -float(orientation_deg), major=major
    )
