"""Ports of KiCad's own source, kept as oracles for the test ladder.

Each module transcribes one piece of KiCad (its s-expression lexer, its string
writer, its number formatter) and cites the KiCad file and line range at the tag
it was read from: 9.0.8, plus 10.0.7 wherever the two differ. A port is checked
against KiCad itself (kicad-cli, pcbnew, or files KiCad saved) by the self-tests
in tests/test_ladder_r1_judges.py, never against this package.

Nothing here imports mcp_server_kicad, and nothing may: a judge that shares code
with the code it judges agrees with it by construction.
"""
