"""Ports of KiCad's own source, kept as oracles for the test ladder.

Each module transcribes one piece of KiCad (its s-expression lexer, its string
writer, its number formatter) and cites the KiCad file and line range at the tag
it was read from: 9.0.8, plus 10.0.7 wherever the two differ. A port is checked
against KiCad itself (kicad-cli, pcbnew, or files KiCad saved) by the self-tests
in tests/test_ladder_r1_judges.py, never against this package.

Nothing here imports mcp_server_kicad, and nothing may: a judge that shares code
with the code it judges agrees with it by construction.

Each rung checks the ports it adds in its own judges file,
tests/test_ladder_r<N>_judges.py: rung 2 adds trigo (angles and RotatePoint),
transform (the schematic symbol transform), parse_units (number tokens and the
load clamps), board_item (footprint-relative positions), kicad_io_utils (point
packing) and eda_units.format_angle, all checked in test_ladder_r2_judges.py.
"""
