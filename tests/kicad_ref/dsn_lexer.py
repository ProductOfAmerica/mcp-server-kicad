"""KiCad's s-expression lexer (DSNLEXER), ported as an oracle.

Read from KiCad's source at 9.0.8 and 10.0.7. The token rules are the same at both
tags: the 10.0.7 diff of common/dsnlexer.cpp only records separators and rewords
messages. Line numbers below are 9.0.8's, with 10.0.7's in brackets where they moved.

Reading (common/richio.cpp, FILE_LINE_READER::ReadLine 249-280 [210-241]): input is
read one line at a time, each line ending just after its LF, which it keeps. NUL bytes
stay in the line.

Tokens (common/dsnlexer.cpp, NextTok 545-833 [541-836]):

- At the start of every line, whitespace is skipped, and a line whose first non-blank
  byte is '#' is a comment and is skipped whole (575-598). '#' anywhere else is data.
- Whitespace is space, LF, CR, TAB and NUL, never FF or VT (isSpace 452-470 [448-466]).
- '(' and ')' are tokens, and so is '|' while the lexer knows the bar (611-633
  [614-636]).
- A double quote opens a string, which must close on the same line (640-733
  [643-736]). Its escapes are \\" \\\\ \\a \\b \\f \\n \\r \\t \\v; \\x takes one or two
  hex digits and reads as a plain 'x' with none (673-690 [676-693]); one to three
  octal digits give one byte, the value cut to 8 bits; any other escaped byte reads as
  a backslash and is then read normally (692-711 [695-714]).
- Anything else runs to the next separator (isSep 480-483 [476-479]: whitespace, the
  parens, and the bar when known), so a quote inside such a run is an ordinary byte
  (805-824 [808-827]). The run is a NUMBER when isNumber accepts it (495-542
  [491-538]), else a SYMBOL (a keyword, to KiCad's parsers).

Values (include/dsnlexer.h, FromUTF8): what KiCad stores for a token. At 9.0.8
(434-437) it is wxString::FromUTF8 of the token as a C string, so the value ends at
the first NUL; at 10.0.7 (439-442) it is FromUTF8 of the whole std::string, so a NUL
stays in it. FromUTF8 decodes through wxMBConvStrictUTF8::ToWChar (wxWidgets 3.2.8,
include/wx/string.h:1786-1787 and 1796-1797, src/common/strconv.cpp:922-1069): a table of
sequence lengths by lead byte (0x80-0xC1 and 0xF5-0xFF start nothing) and a check that
each continuation byte is 10xxxxxx, and no other check, so a three- or four-byte
overlong decodes to its code point. Any failure gives the empty string. Measured on
kicad-cli 9.0.8 (2026-10-10): "A<NUL>B" reads A; bytes 0xFF, C0 AF, a lead F5 and a
cut-off sequence read empty; E0 80 AF and F0 80 80 AF read "/".

Which files know the bar: the lexer's default is on (dsnlexer.cpp:49). The schematic
parser turns it off below version 20240620 (eeschema/sch_io/kicad_sexpr/
sch_io_kicad_sexpr_parser.cpp:2642 [2749]), the symbol-library parser below 20240529
(:187 [188]), the board parser below 20240706 (pcbnew/pcb_io/kicad_sexpr/
pcb_io_kicad_sexpr_parser.cpp:1423 [1693]), and the footprint parser below 20240706
when the footprint declares a version (:4477 [5165]). A board with no version token
reads as 20201115 (:1416), a schematic or symbol library with none as the current
format (sch parser 844-862). The drawing-sheet (common/drawing_sheet/
drawing_sheet_parser.cpp), DRC-rule (pcbnew/drc/drc_rule_parser.cpp) and 9.0.8
library-table (common/lib_table_base.cpp) parsers never change the default. The only
grammar that needs the bar is embedded-file data, whose parser switches it on itself
(common/embedded_files.cpp:332 and 391-418 [420 and 484-511]). KiCad 10 reads library
tables with a PEGTL grammar rather than this lexer (include/libraries/
library_table_grammar.h at 10.0.7), so for those files this port models KiCad 9 only.

Deliberately not modelled:

- the line-length limit, 1,000,000 bytes at 9.0.8 and 16,000,000 at 10.0.7
  (include/richio.h:85 [61]);
- a text-mode end-of-file byte: FILE_LINE_READER opens files "rt" (richio.cpp:201),
  but kicad-cli 9.0.8 on Windows reads a raw 0x1A inside a quoted value as data
  (measured 2026-10-10);
- isxdigit on a byte above 0x7F, which C leaves undefined for a negative char: the
  port reads it as no hex digit, and the generators never put one after \\x;
- the header: a parser reads its version with the bar known and switches afterwards,
  where the port applies one setting to the whole file. They differ only when a bar
  comes before the version token, which no KiCad writer emits.

This module imports nothing from mcp_server_kicad.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import NamedTuple, cast

LEFT = "("
RIGHT = ")"
BAR = "|"
STRING = "STRING"
NUMBER = "NUMBER"
SYMBOL = "SYMBOL"
GROUP = "GROUP"

#: Token kinds that are values (atoms) rather than punctuation.
ATOM_KINDS = frozenset({STRING, NUMBER, SYMBOL})


class Token(NamedTuple):
    """One token as NextTok returns it, with absolute byte offsets into the input.

    ``value`` is what KiCad holds in curText: the decoded bytes between the quotes for
    a STRING, the token's own bytes for every other kind. ``start`` is the offset of
    its first byte (a STRING's opening quote), ``end`` the offset just past its last
    byte (past the closing quote).
    """

    kind: str
    value: bytes
    start: int
    end: int


class Group(NamedTuple):
    """A parenthesised list: its items, the offset of its '(' and the offset past its ')'."""

    items: list
    start: int
    end: int

    kind = GROUP


class LexError(ValueError):
    """Input KiCad's reader refuses at the token level; ``offset`` locates it."""

    def __init__(self, message: str, offset: int):
        super().__init__(message)
        self.offset = offset


class Unterminated(LexError):
    """A string with no closing quote on its own line ("Un-terminated delimited string",
    dsnlexer.cpp:729-732 [732-735]). ``offset`` is that string's opening quote."""


class Unbalanced(LexError):
    """Parentheses that do not pair up.

    ``offset`` is the first ')' with no open '(' or, when some '(' are still open at the
    end, the length of the input; ``unclosed`` counts those open '(' (0 for a stray ')').
    """

    def __init__(self, message: str, offset: int, unclosed: int):
        super().__init__(message, offset)
        self.unclosed = unclosed


_SPACE = re.compile(rb"[ \n\r\t\x00]*")
_BARE = {
    True: re.compile(rb"[^ \n\r\t\x00()|]*"),
    False: re.compile(rb"[^ \n\r\t\x00()]*"),
}
_STRING_STOP = re.compile(rb'["\\]')
_Matcher = Callable[[bytes, int, int], "re.Match[bytes]"]
_ESCAPES = {
    0x22: 0x22,  # \"
    0x5C: 0x5C,  # \\
    0x61: 0x07,  # \a
    0x62: 0x08,  # \b
    0x66: 0x0C,  # \f
    0x6E: 0x0A,  # \n
    0x72: 0x0D,  # \r
    0x74: 0x09,  # \t
    0x76: 0x0B,  # \v
}
_HEX = frozenset(b"0123456789abcdefABCDEF")
_OCT = frozenset(b"01234567")


def is_number(token: bytes) -> bool:
    """isNumber (dsnlexer.cpp:495-542 [491-538]), step for step."""
    i, n, saw = 0, len(token), False
    if i < n and token[i] in b"-+":
        i += 1
    while i < n and 0x30 <= token[i] <= 0x39:
        i, saw = i + 1, True
    if i < n and token[i] == 0x2E:
        i += 1
        while i < n and 0x30 <= token[i] <= 0x39:
            i, saw = i + 1, True
    if saw and i < n and token[i] in b"eE":
        i, saw = i + 1, False  # an exponent needs at least one digit of its own
        if i < n and token[i] in b"-+":
            i += 1
        while i < n and 0x30 <= token[i] <= 0x39:
            i, saw = i + 1, True
    return saw and i == n


def _string(data: bytes, start: int, limit: int) -> tuple[int, bytes]:
    """The quoted string opening at ``start``: (offset past its closing quote, value)."""
    head = start + 1
    out = bytearray()
    while True:
        stop = _STRING_STOP.search(data, head, limit)
        if stop is None:
            raise Unterminated(f"unterminated string at byte {start}", start)
        out += data[head : stop.start()]
        head = stop.start()
        if data[head] == 0x22:
            return head + 1, bytes(out)
        head += 1  # past the backslash
        if head >= limit:
            raise Unterminated(f"unterminated string at byte {start}", start)
        escaped = data[head]
        head += 1
        if escaped in _ESCAPES:
            out.append(_ESCAPES[escaped])
        elif escaped == 0x78:  # \x: one or two hex digits, else a plain 'x'
            digits = head
            while digits < limit and digits - head < 2 and data[digits] in _HEX:
                digits += 1
            out.append(int(data[head:digits], 16) if digits > head else 0x78)
            head = digits
        else:  # one to three octal digits, else the backslash itself
            head -= 1
            digits = head
            while digits < limit and digits - head < 3 and data[digits] in _OCT:
                digits += 1
            out.append(int(data[head:digits], 8) & 0xFF if digits > head else 0x5C)
            head = digits


def iter_tokens(data: bytes, *, knows_bar: bool = True) -> Iterator[Token]:
    """Yield the tokens of ``data`` in order; raises Unterminated where KiCad would.

    Lazy, so reading the first few tokens lexes only the first line.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("the lexer reads bytes, not str")
    data = bytes(data)
    knows_bar = bool(knows_bar)
    # Both patterns match the empty string, so match() never returns None here.
    bare = cast(_Matcher, _BARE[knows_bar].match)
    space = cast(_Matcher, _SPACE.match)
    make = tuple.__new__  # Token(...) without NamedTuple's Python-level __new__
    size = len(data)
    line = 0
    while line < size:
        newline = data.find(b"\n", line)
        limit = size if newline < 0 else newline + 1
        cur = space(data, line, limit).end()
        if cur < limit and data[cur] == 0x23:  # '#' first on the line: a comment
            line = limit
            continue
        while cur < limit:
            byte = data[cur]
            if byte == 0x28:
                yield make(Token, (LEFT, b"(", cur, cur + 1))
                cur += 1
            elif byte == 0x29:
                yield make(Token, (RIGHT, b")", cur, cur + 1))
                cur += 1
            elif byte == 0x7C and knows_bar:
                yield make(Token, (BAR, b"|", cur, cur + 1))
                cur += 1
            elif byte == 0x22:
                end, value = _string(data, cur, limit)
                yield make(Token, (STRING, value, cur, end))
                cur = end
            else:
                end = bare(data, cur, limit).end()
                text = data[cur:end]
                yield make(Token, (NUMBER if is_number(text) else SYMBOL, text, cur, end))
                cur = end
            cur = space(data, cur, limit).end()
        line = limit


def tokens(data: bytes, *, knows_bar: bool = True) -> list[Token]:
    """Every token of ``data``, as a list."""
    return list(iter_tokens(data, knows_bar=knows_bar))


def tree(data: bytes, *, knows_bar: bool = True) -> list:
    """The top-level items of ``data``: Tokens and nested Groups.

    Raises Unterminated as the lexer does, and Unbalanced where KiCad's parsers would
    find a ')' with nothing open or reach the end with a '(' still open.
    """
    root: list = []
    items = root
    open_lists: list[tuple[list, int]] = []
    for tok in iter_tokens(data, knows_bar=knows_bar):
        if tok.kind == LEFT:
            open_lists.append((items, tok.start))
            items = []
        elif tok.kind == RIGHT:
            if not open_lists:
                raise Unbalanced(f"unmatched ')' at byte {tok.start}", tok.start, 0)
            parent, start = open_lists.pop()
            parent.append(Group(items, start, tok.end))
            items = parent
        else:
            items.append(tok)
    if open_lists:
        raise Unbalanced(
            f"{len(open_lists)} unclosed '(' at end of input", len(data), len(open_lists)
        )
    return root


#: The file kinds whose parsers set the bar by version, keyed by their first keyword.
_KIND_OF_HEAD = {
    b"kicad_sch": "schematic",
    b"kicad_symbol_lib": "symbol_lib",
    b"kicad_pcb": "board",
    b"footprint": "footprint",
    b"module": "footprint",
}
#: First version at which each parser turns the bar on (see the module docstring).
_BAR_SINCE = {
    "schematic": 20240620,
    "symbol_lib": 20240529,
    "board": 20240706,
    "footprint": 20240706,
}


def file_kind(data: bytes) -> str:
    """ "schematic", "symbol_lib", "board", "footprint", or "other", from the first keyword."""
    toks = iter_tokens(data, knows_bar=True)  # lazy: lexes only as far as it is read
    try:
        first, second = next(toks), next(toks)
    except (StopIteration, LexError):
        return "other"
    if first.kind != LEFT or second.kind != SYMBOL:
        return "other"
    return _KIND_OF_HEAD.get(second.value, "other")


_STRTOL = re.compile(rb"[-+]?[0-9]+")


def _parse_int(text: bytes) -> int:
    """parseInt: strtol( CurText(), nullptr, 10 ) of a token isNumber accepted."""
    digits = _STRTOL.match(text)
    return int(digits.group()) if digits else 0


def _is_version(window: list[Token]) -> bool:
    return (
        len(window) == 2
        and window[0].kind == SYMBOL
        and window[0].value == b"version"
        and window[1].kind == NUMBER
    )


def file_version(data: bytes) -> int | None:
    """The format version KiCad's parser reads, or None when the file declares none.

    A schematic, symbol library or board (and any other file) declares it only as the
    first item of its top-level list: parseHeader takes '(' 'version' NUMBER ')' straight
    after the file's keyword (sch_io_kicad_sexpr_parser.cpp:844-862,
    pcb_io_kicad_sexpr_parser.cpp:1400-1425). A footprint may declare it as any item of
    its top-level list and the parser keeps the largest (pcb parser 4466-4479).
    """
    try:
        if file_kind(data) != "footprint":
            toks = iter_tokens(data, knows_bar=True)
            head = [tok for _, tok in zip(range(6), toks)]
            if (
                len(head) == 6
                and head[0].kind == LEFT
                and head[2].kind == LEFT
                and _is_version(head[3:5])
                and head[5].kind == RIGHT
            ):
                return _parse_int(head[4].value)
            return None
        found: int | None = None
        depth = 0
        window: list[Token] = []
        for tok in iter_tokens(data, knows_bar=True):
            if tok.kind == LEFT:
                depth += 1
                if depth == 2:
                    window = []
                elif depth == 3:
                    window.append(tok)  # holds a nested list, so it is not (version N)
            elif tok.kind == RIGHT:
                if depth == 2 and _is_version(window):
                    version = _parse_int(window[1].value)
                    found = version if found is None else max(found, version)
                depth -= 1
                if depth == 0:
                    break
            elif depth == 2:
                window.append(tok)
        return found
    except LexError:
        return None


def knows_bar_for(kind: str, version: int | None) -> bool:
    """Whether KiCad's parser for ``kind`` lexes '|' as a separator at ``version``."""
    since = _BAR_SINCE.get(kind)
    if since is None:
        return True  # DSNLEXER::init, dsnlexer.cpp:49
    if version is None:
        # A board without a version reads as 20201115; a schematic or symbol library
        # without one as the current format; a footprint without one never calls
        # SetKnowsBar, so the default stands.
        return kind != "board"
    return version >= since


def knows_bar_of(data: bytes) -> bool:
    """knows_bar_for the kind and version ``data`` declares."""
    return knows_bar_for(file_kind(data), file_version(data))


#: Sequence length by lead byte, tableUtf8Lengths (strconv.cpp:922-954 in wx 3.2.8).
_UTF8_LENGTH = bytes([1] * 0x80 + [0] * 0x42 + [2] * 0x1E + [3] * 0x10 + [4] * 5 + [0] * 0x0B)
_LEAD_MASK = (0x7F, 0x1F, 0x0F, 0x07)


def wx_from_utf8(value: bytes, *, major: int) -> str:
    """The string KiCad ``major`` stores for a token whose bytes are ``value``.

    KiCad 9 cuts the value at its first NUL, KiCad 10 keeps it; then the bytes are
    decoded as wxMBConvStrictUTF8::ToWChar decodes them (see the module docstring),
    and a sequence it rejects makes the whole value empty. Raises ValueError for a
    value that decodes to a surrogate or to a code point above U+10FFFF, which KiCad
    holds differently per platform: Windows keeps UTF-16 units (measured: a lone
    surrogate stops kicad-cli 9.0.8 writing its netlist, rc 2) and leaves an unwritten
    unit for a code point it cannot encode (measured: F4 90 80 80 printed as U+F587).
    """
    if major < 9:
        raise ValueError(f"no FromUTF8 model for KiCad {major}")
    if major == 9:
        value = value.split(b"\x00", 1)[0]
    codes = []
    i, size = 0, len(value)
    while i < size:
        length = _UTF8_LENGTH[value[i]]
        if length == 0 or i + length > size:
            return ""
        code = value[i] & _LEAD_MASK[length - 1]
        for follow in value[i + 1 : i + length]:
            if follow & 0xC0 != 0x80:
                return ""
            code = (code << 6) | (follow & 0x3F)
        codes.append(code)
        i += length
    if any(0xD800 <= code <= 0xDFFF or code > 0x10FFFF for code in codes):
        raise ValueError("KiCad holds surrogates and code points past U+10FFFF per platform")
    return "".join(map(chr, codes))
