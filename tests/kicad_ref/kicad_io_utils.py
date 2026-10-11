"""How KiCad lays out a run of (xy ...) points when it saves a file, ported as an oracle.

KICAD_FORMAT::Prettify (common/io/kicad/kicad_io_utils.cpp:89-303 at 9.0.8, 97-339 at
10.0.7) rewrites the writer's flat output into indented lines. A '(' normally starts
a new line indented by one TAB per enclosing list, its column then being that depth
plus one. Lists of points are the exception (xySpecialCaseColumnLimit = 99, line 98 at
9.0.8 and 106 at 10.0.7; the branch at 219-224 and 245-250): when the previous list was
an (xy and this one is too, it follows on the same line after one space while the
current column is below 99; otherwise it starts a new line like any other list. Inside
an (xy list every run of whitespace becomes one space. The column counts bytes since
the last newline, a TAB counting one (indentSize 1).

So for a run of (xy tokens as KiCad writes them, at nesting depth d: the first opens a
line of d TABs; each later one is appended after a space while the line so far is
shorter than 99 bytes, and opens a new line otherwise. KiCad ends lines with LF.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from collections.abc import Sequence

#: xySpecialCaseColumnLimit (kicad_io_utils.cpp:98 at 9.0.8, 106 at 10.0.7).
XY_COLUMN_LIMIT = 99


def pack_xy(tokens: Sequence[bytes], depth: int) -> bytes:
    """The bytes Prettify writes for ``tokens``, each a whole (xy ...) list, at ``depth``.

    From the first token's indentation to the last token's ')', lines joined by LF.
    """
    if not tokens:
        raise ValueError("a run of points has at least one (xy")
    if depth < 0:
        raise ValueError(f"depth {depth} is negative")
    lines: list[bytes] = []
    line = b"\t" * depth + bytes(tokens[0])
    for token in tokens[1:]:
        if len(line) < XY_COLUMN_LIMIT:
            line += b" " + bytes(token)
        else:
            lines.append(line)
            line = b"\t" * depth + bytes(token)
    lines.append(line)
    return b"\n".join(lines)
