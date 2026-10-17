"""KiCad's library table writer and its nickname rule, ported as oracles.

The row (LIB_TABLE_ROW::Format, common/lib_table_base.cpp:125-146 at 9.0.8): the URI
has every backslash turned into '/', then the row is printed as

    (lib (name %s)(type %s)(uri %s)(options %s)(descr %s)%s)

with Quotew on the five fields (tests/kicad_ref/richio.py) and the last %s holding
"(disabled)" and "(hidden)" for a row that is. The table (SYMBOL_LIB_TABLE::Format,
eeschema/symbol_lib_table.cpp:300-309 at 9.0.8): the keyword at nest 0, "(version 7)"
at nest 1 (LIB_TABLE::Save forces 7, common/lib_table_base.cpp:545), each row at nest
1, the closing paren at nest 0. A nest level is two spaces (common/richio.cpp:465).
Lines end in LF here.

KiCad 10.0.7 writes version 7 in a layout of its own (LIBRARY_TABLE::Format,
common/libraries/library_table.cpp:197-202 and 354), so this port is the KiCad 9 layout.

The nickname rule (LIB_ID::FindIllegalLibraryNameChar and isLegalLibraryNameChar,
common/lib_id.cpp:243-274 at 9.0.8, 237-268 at 10.0.7): a character is illegal below
U+0020, as a backslash and as a colon. The symbol table dialog refuses an empty
nickname and an illegal character (eeschema/dialogs/panel_sym_lib_table.cpp:425-494
at 9.0.8) and two rows with one nickname (516-532), and the table reader keeps the
first of two rows with one nickname and reports the later one (SYMBOL_LIB_TABLE::Parse,
eeschema/symbol_lib_table.cpp:271-296).

Checked against pcbnew and the tables KiCad ships by tests/test_ladder_r3_judges.py.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

from kicad_ref.richio import quotes

#: The format version a table is written at.
VERSION = 7


def illegal_nickname_char(name: str) -> str | None:
    """FindIllegalLibraryNameChar: the first character of ``name`` that is illegal in a
    library nickname, or None."""
    for char in name:
        if ord(char) < 0x20 or char in "\\:":
            return char
    return None


def legal_nickname(name: str) -> bool:
    """Whether the table dialog accepts ``name`` as a nickname: not empty, no illegal
    character. The dialog trims first; this judges the text as given."""
    return bool(name) and illegal_nickname_char(name) is None


def format_row(
    nickname: str,
    uri: str,
    type_: str = "KiCad",
    options: str = "",
    descr: str = "",
    *,
    disabled: bool = False,
    hidden: bool = False,
) -> bytes:
    """LIB_TABLE_ROW::Format of a row, indented one level and ended in LF."""
    extra = (b"(disabled)" if disabled else b"") + (b"(hidden)" if hidden else b"")
    return b"  (lib (name %s)(type %s)(uri %s)(options %s)(descr %s)%s)\n" % (
        quotes(nickname),
        quotes(type_),
        quotes(uri.replace("\\", "/")),
        quotes(options),
        quotes(descr),
        extra,
    )


def format_rows(keyword: bytes, rows: list[bytes]) -> bytes:
    """A table of formatted rows: its keyword, the version and the closing paren; the
    fp_lib_table has the same frame (FP_LIB_TABLE::Format, common/fp_lib_table.cpp:266-275)."""
    return b"(%s\n  (version %d)\n" % (keyword, VERSION) + b"".join(rows) + b")\n"


def format_table(rows: list[tuple[str, str]]) -> bytes:
    """SYMBOL_LIB_TABLE::Format of rows given as (nickname, uri), ended in LF."""
    return format_rows(b"sym_lib_table", [format_row(nickname, uri) for nickname, uri in rows])


def duplicate_nicknames(nicknames: list[str]) -> list[str]:
    """The nicknames the reader reports as duplicates: each one an earlier row already
    holds (the first row is kept), in row order."""
    seen: set[str] = set()
    out = []
    for nickname in nicknames:
        if nickname in seen:
            out.append(nickname)
        seen.add(nickname)
    return out
