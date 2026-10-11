"""Hypothesis strategies for the test ladder.

The *KiCad-written dialect* is what KiCad's own writers produce: bare keywords and
numbers, strings written by OUTPUTFORMATTER::Quotew (tests/kicad_ref/richio.py), and
whitespace made of spaces, TABs and line ends. Its documents never hold a comment
line, a bar, a NUL, FF or VT, a quote glued to a bare token, or an unterminated
string. Every other strategy here produces exactly one of those hostile classes, so a
test can target one cause at a time.

Sizing: ``budget(divisor)`` scales a test's examples with the active profile
(tests/conftest.py), so no test hardcodes ``max_examples``.

Strict xfails over generated inputs pin their failing cases with ``@example``, which
needs a test's inputs to be plain values; ``doc_with_target`` returns its target's
path alongside the document for that reason, instead of a test drawing one with
``st.data()``.
"""

from __future__ import annotations

from hypothesis import settings
from hypothesis import strategies as st
from kicad_ref.richio import quotes


def budget(divisor: int = 1) -> int:
    """Examples for one test: the active profile's max_examples // divisor, at least 1."""
    return max(1, settings().max_examples // divisor)  # settings() inherits the profile


# ---------------------------------------------------------------------------
# atoms and separators of the KiCad-written dialect
# ---------------------------------------------------------------------------

#: Printable ASCII a bare token may hold: no whitespace, parens, quote or bar.
_BARE_ASCII = "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) not in '()"|')
_multibyte = st.characters(min_codepoint=0x80, exclude_categories=("Cs",))
_bare_char = st.one_of(st.sampled_from(_BARE_ASCII), _multibyte)
#: A bare token's first character: never '#', which first on a line starts a comment.
_first_bare_char = st.one_of(st.sampled_from(_BARE_ASCII.replace("#", "")), _multibyte)
_word = st.builds(
    lambda first, rest: (first + rest).encode("utf-8"),
    _first_bare_char,
    st.text(_bare_char, max_size=7),
)
_number = st.from_regex(
    rb"[-+]?(?:[0-9]{1,6}(?:\.[0-9]{1,6})?|\.[0-9]{1,4})(?:[eE][-+]?[0-9]{1,2})?",
    fullmatch=True,
)


def bare_token() -> st.SearchStrategy[bytes]:
    """A bare atom KiCad could write: a word or a number, never starting with '#'."""
    return st.one_of(_number, _word)


#: A character a KiCad string can hold: anything but a lone surrogate or a NUL
#: (Quotew passes the text through a C string).
_kicad_char = st.characters(exclude_categories=("Cs",), exclude_characters="\x00")
kicad_text = st.text(_kicad_char)
_short_text = st.text(_kicad_char, max_size=12)


def quoted_atom(text: st.SearchStrategy[str] | None = None) -> st.SearchStrategy[bytes]:
    """A string as Quotew writes it, for ``text`` (default: any KiCad string)."""
    return (kicad_text if text is None else text).map(quotes)


def atom() -> st.SearchStrategy[bytes]:
    """An atom KiCad could write: a bare token or a short quoted string."""
    return st.one_of(bare_token(), quoted_atom(_short_text))


_WS_UNITS = (b" ", b"\t", b"\n", b"\r\n", b"\r", b"\n\t", b"\n  ", b"\r\n\t\t", b"  ")
#: A whitespace run: spaces, TABs, LF, CRLF, a lone CR, newlines with indentation.
whitespace = st.lists(st.sampled_from(_WS_UNITS), min_size=1, max_size=3).map(b"".join)
_maybe_ws = st.one_of(st.just(b""), whitespace)
_LINE_START = (b"\n", b"\r\n", b"\n\t", b"\n  ", b"\r\n\t\t")
_MID_LINE = (b" ", b"\t", b"  ")
_KEYWORDS = (b"a", b"b", b"at", b"xy", b"pts", b"property", b"symbol", b"uuid")


# ---------------------------------------------------------------------------
# document trees: bytes are atoms, lists are lists, markers render specially
# ---------------------------------------------------------------------------


class _Target:
    """The item doc_with_target points at, with the separator rule before it."""

    __slots__ = ("item", "line_start")

    def __init__(self, item, line_start):
        self.item = item
        self.line_start = line_start


class _Comment:
    """A comment line: rendered on a line of its own wherever it sits."""

    __slots__ = ("text", "indent")

    def __init__(self, text: bytes, indent: bytes):
        self.text = text
        self.indent = indent


def _is_list(item) -> bool:
    if isinstance(item, _Target):
        item = item.item
    return isinstance(item, list)


def _tree(draw, depth: int = 0, max_depth: int = 3) -> list:
    """One list: usually a keyword head and up to four children, else atom-less."""
    if draw(st.integers(0, 7)):
        children: list = [draw(st.sampled_from(_KEYWORDS))]
        for _ in range(draw(st.integers(0, 4))):
            if depth < max_depth and draw(st.integers(0, 2)) == 0:
                children.append(_tree(draw, depth + 1, max_depth))
            else:
                children.append(draw(atom()))
        return children
    return [
        _tree(draw, depth + 1, max_depth)
        for _ in range(draw(st.integers(0, 2 if depth < max_depth else 0)))
    ]


def _render(draw, items: list) -> bytes:
    """Items with separators as KiCad's writers place them: optional after '(', before
    ')' and between two lists (``(name "x")(type "KiCad")`` in a library table), and
    mandatory between any other two items."""
    parts: list[bytes] = []
    previous = None
    for item in items:
        if isinstance(item, _Comment):
            parts.append(b"\n" + item.indent + b"#" + item.text + b"\n" + item.indent)
            previous = item
            continue
        if isinstance(item, _Target):
            target, body = item, item.item
        else:
            target, body = None, item
        mandatory = (
            previous is not None
            and not isinstance(previous, _Comment)
            and not (_is_list(previous) and _is_list(item))
        )
        if target is not None and target.line_start is not None:
            choices = _LINE_START if target.line_start else _MID_LINE
            if not target.line_start and not mandatory:
                choices = (b"",) + choices
            sep = draw(st.sampled_from(choices))
        else:
            sep = draw(whitespace if mandatory else _maybe_ws)
        parts.append(sep)
        parts.append(body if isinstance(body, bytes) else b"(" + _render(draw, body) + b")")
        previous = item
    parts.append(draw(_maybe_ws))
    return b"".join(parts)


def _lists_in(items: list, path: tuple = ()):
    """(path, list) for every list in ``items``, depth first."""
    for i, item in enumerate(items):
        if isinstance(item, list):
            yield path + (i,), item
            yield from _lists_in(item, path + (i,))


@st.composite
def kicad_doc(draw) -> bytes:
    """A document in the KiCad-written dialect: one to three top-level lists, heads
    drawn from a small set of keywords so they repeat, atom-less lists included."""
    return _render(draw, [_tree(draw) for _ in range(draw(st.integers(1, 3)))])


@st.composite
def kicad_item(draw) -> bytes:
    """One item of the dialect, without surrounding whitespace: an atom or a list."""
    if draw(st.booleans()):
        return draw(atom())
    return b"(" + _render(draw, _tree(draw, 1)) + b")"


@st.composite
def doc_with_target(draw, kind: str = "any", line_start: bool | None = None):
    """(document, path): a KiCad-dialect document and the CST child-index path of one item.

    ``kind`` is "quoted" or "bare" (an atom of that kind is placed inside some list),
    "list" (a list is placed), or "any" (an existing item is chosen). ``line_start``
    puts a placed item first on its line (True), after something on the same line
    (False), or either (None). The path counts children from the document root, the
    way the CST's ``children`` index them; KiCad's lexer reads the same items there.
    """
    top = [_tree(draw) for _ in range(draw(st.integers(1, 3)))]
    if kind == "any":
        candidates = [(i,) for i in range(len(top))]
        for path, items in _lists_in(top):
            candidates += [path + (j,) for j in range(len(items))]
        path = draw(st.sampled_from(candidates))
        return _render(draw, top), path
    lists = list(_lists_in(top))
    parent_path, parent = draw(st.sampled_from(lists))
    headed = bool(parent) and isinstance(parent[0], bytes)
    index = draw(st.integers(1 if headed else 0, len(parent)))
    if kind == "quoted":
        placed = draw(quoted_atom())
    elif kind == "bare":
        placed = draw(bare_token())
    elif kind == "list":
        placed = _tree(draw, 2)
    else:
        raise ValueError(f"unknown target kind {kind!r}")
    parent.insert(index, _Target(placed, line_start))
    return _render(draw, top), parent_path + (index,)


# ---------------------------------------------------------------------------
# escape bodies: the bytes between a string's quotes
# ---------------------------------------------------------------------------

_RAW_BODY_BYTE = st.integers(0, 255).filter(lambda b: b not in (0x22, 0x5C, 0x0A))
_raw_chunk = st.one_of(
    st.lists(_RAW_BODY_BYTE, min_size=1, max_size=4).map(bytes),
    st.text(st.characters(exclude_categories=("Cs",)), min_size=1, max_size=3)
    .map(lambda s: s.encode("utf-8"))
    .filter(lambda b: not ({0x22, 0x5C, 0x0A} & set(b))),
)
#: Escapes both readers decode alike: KiCad's (dsnlexer.cpp:661-712) and the CST's.
_AGREED_ESCAPES = (b'\\"', b"\\\\", b"\\n", b"\\r", b"\\t")
#: Bytes that are no escape to KiCad: neither a letter of its table, nor x, nor an
#: octal digit, nor the line end. KiCad reads backslash-then-byte for these.
_UNKNOWN_ESCAPED = bytes(b for b in range(256) if b not in b'abfnrtvx"\\01234567\n')
_unknown_escape = st.one_of(
    st.sampled_from(_UNKNOWN_ESCAPED).map(lambda b: b"\\" + bytes([b])),
    st.characters(min_codepoint=0x80, exclude_categories=("Cs",)).map(
        lambda c: b"\\" + c.encode("utf-8")
    ),
)


def agreed_escape_body() -> st.SearchStrategy[bytes]:
    """A string body both readers decode to the same bytes.

    Raw bytes other than the quote, the backslash and LF (TAB, CR, NUL and bytes above
    0x7F included), the escapes \\" \\\\ \\n \\r \\t, and escapes KiCad does not know
    (\\B, \\8, \\9, a backslash before a multibyte character), which both read as the
    backslash followed by the byte.
    """
    piece = st.one_of(_raw_chunk, st.sampled_from(_AGREED_ESCAPES), _unknown_escape)
    return st.lists(piece, max_size=6).map(b"".join)


_HEX = b"0123456789abcdefABCDEF"
_OCT = b"01234567"
#: A byte that may follow an escape whose length depends on what follows: printable
#: ASCII that is neither a hex nor an octal digit, a quote or a backslash. A byte
#: above 0x7F after \x would reach isxdigit with a negative char, which C leaves
#: undefined, so none is generated there.
_TRAILER = bytes(c for c in range(0x20, 0x7F) if c not in _HEX and c not in b'"\\')


def _kicad_only_escape() -> st.SearchStrategy[bytes]:
    letters = st.sampled_from((b"\\a", b"\\b", b"\\f", b"\\v"))
    hex_short = st.builds(
        lambda digits, tail: b"\\x" + digits + bytes([tail]),
        st.text(st.sampled_from(_HEX.decode()), max_size=1).map(str.encode),
        st.sampled_from(_TRAILER),
    )
    hex_full = st.text(st.sampled_from(_HEX.decode()), min_size=2, max_size=2).map(
        lambda d: b"\\x" + d.encode()
    )
    oct_short = st.builds(
        lambda digits, tail: b"\\" + digits + bytes([tail]),
        st.text(st.sampled_from(_OCT.decode()), min_size=1, max_size=2).map(str.encode),
        st.sampled_from(_TRAILER),
    )
    oct_full = st.text(st.sampled_from(_OCT.decode()), min_size=3, max_size=3).map(
        lambda d: b"\\" + d.encode()
    )
    return st.one_of(letters, hex_short, hex_full, oct_short, oct_full)


@st.composite
def kicad_only_escape_body(draw) -> bytes:
    """A string body holding at least one escape only KiCad decodes (bug 29's classes).

    \\a \\b \\f \\v; \\x with no hex digit (KiCad reads x), one, or two; one to three
    octal digits. An escape whose length depends on the next byte is followed by one
    that ends it (see _TRAILER); the other pieces are agreed ones.
    """
    agreed = st.one_of(_raw_chunk, st.sampled_from(_AGREED_ESCAPES))
    pieces = draw(st.lists(st.one_of(agreed, _kicad_only_escape()), max_size=4))
    pieces.insert(draw(st.integers(0, len(pieces))), draw(_kicad_only_escape()))
    return b"".join(pieces)


# ---------------------------------------------------------------------------
# hostile classes, one cause each
# ---------------------------------------------------------------------------


def _place_atom(draw, top: list, placed) -> None:
    """Put ``placed`` (an atom's raw bytes) at a non-head position of some list."""
    parent_path, parent = draw(st.sampled_from(list(_lists_in(top))))
    headed = bool(parent) and isinstance(parent[0], bytes)
    parent.insert(draw(st.integers(1 if headed else 0, len(parent))), placed)


@st.composite
def comment_line_doc(draw) -> bytes:
    """A KiCad-dialect document plus one comment line: a line whose first non-blank
    byte is '#', anywhere between two items or before the first; its text may hold
    parens and quotes."""
    top: list = [_tree(draw) for _ in range(draw(st.integers(1, 3)))]
    text = draw(st.binary(max_size=12).filter(lambda b: b"\n" not in b))
    comment = _Comment(text, draw(st.sampled_from((b"", b"\t", b"  ", b" \t"))))
    lists = list(_lists_in(top))
    if draw(st.booleans()):
        top.insert(draw(st.integers(0, len(top))), comment)
    else:
        _, parent = draw(st.sampled_from(lists))
        parent.insert(draw(st.integers(0, len(parent))), comment)
    return _render(draw, top)


@st.composite
def glued_quote_doc(draw) -> bytes:
    """A KiCad-dialect document with a bare token glued to a following string, like
    ab"cd". The string holds no separator, so KiCad reads one token where the CST
    reads two."""
    top = [_tree(draw) for _ in range(draw(st.integers(1, 3)))]
    inner = st.text(st.sampled_from(_BARE_ASCII.replace("\\", "")), max_size=6)
    _place_atom(draw, top, draw(bare_token()) + quotes(draw(inner)))
    return _render(draw, top)


@st.composite
def whitespace_class_doc(draw) -> bytes:
    """A KiCad-dialect document with a bare token holding NUL, FF or VT inside it.

    KiCad splits a token at NUL (whitespace to it) and keeps FF and VT in it; the CST
    does the opposite."""
    top = [_tree(draw) for _ in range(draw(st.integers(1, 3)))]
    left, right = draw(_word), draw(_word)
    _place_atom(draw, top, left + draw(st.sampled_from((b"\x00", b"\x0c", b"\x0b"))) + right)
    return _render(draw, top)


_B64 = st.text(
    st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="),
    min_size=1,
    max_size=20,
).map(str.encode)


@st.composite
def bar_doc(draw) -> bytes:
    """A KiCad-dialect document with bars: glued to bare tokens (a|b, |a, a|, ||) or a
    KiCad-shaped embedded-file block, (data |chunk ... chunk|), whose layout follows
    EMBEDDED_FILES::WriteEmbeddedFiles (embedded_files.cpp:186-205 at 9.0.8) as KiCad
    saves it. Read it with the bar known."""
    top = [_tree(draw) for _ in range(draw(st.integers(1, 3)))]
    if draw(st.booleans()):
        chunks = draw(st.lists(_B64, min_size=1, max_size=4))
        indent = draw(st.sampled_from((b"\n\t\t\t\t", b"\n    ", b"\r\n\t")))
        block = [b"data", b"|" + indent.join(chunks) + b"|"]
        parent_path, parent = draw(st.sampled_from(list(_lists_in(top))))
        parent.insert(
            draw(st.integers(1 if parent and isinstance(parent[0], bytes) else 0, len(parent))),
            block,
        )
    else:
        word = st.one_of(_word, st.just(b""))
        _place_atom(draw, top, draw(word) + draw(st.sampled_from((b"|", b"||"))) + draw(word))
    return _render(draw, top)


@st.composite
def unterminated_doc(draw) -> bytes:
    """A document KiCad refuses as an unterminated string: a raw LF inside a quoted
    atom, or a quote still open at the end of input after balanced parens."""
    top = [_tree(draw) for _ in range(draw(st.integers(1, 3)))]
    if draw(st.booleans()):
        left, right = draw(_short_text), draw(_short_text)
        raw = quotes(left)[:-1] + draw(st.sampled_from((b"\n", b"\r\n"))) + quotes(right)[1:]
        _place_atom(draw, top, raw)
        return _render(draw, top)
    body = draw(st.binary(max_size=8).filter(lambda b: not ({0x22, 0x0A} & set(b))))
    return _render(draw, top) + draw(_maybe_ws) + b'"' + body


#: Bytes that end a bare token for both readers, plus the quote that opens a string.
_NOT_BARE = frozenset(b' \t\r\n\x00()|"')


def _deletable(data: bytes, start: int, end: int) -> bool:
    """Whether deleting data[start:end] leaves the tokens beside it apart: no bare token
    may then touch another bare token or a string's opening quote."""
    before = data[start - 1] if start else None
    after = data[end] if end < len(data) else None
    return not (
        before is not None
        and before not in _NOT_BARE
        and after is not None
        and (after not in _NOT_BARE or after == 0x22)
    )


@st.composite
def paren_mutation(draw, docs: st.SearchStrategy[bytes] | None = None) -> bytes:
    """A document from ``docs`` (default kicad_doc()) with one to three paren
    mutations, each deleting a paren token or inserting '(' or ')' at a token boundary
    outside every string. A deletion never glues the tokens around it together, so the
    only thing that changes is the parens. Some results balance again, most do not."""
    from kicad_ref import dsn_lexer

    data = draw(kicad_doc() if docs is None else docs)
    for _ in range(draw(st.integers(1, 3))):
        toks = dsn_lexer.tokens(data)
        parens = [
            t
            for t in toks
            if t.kind in (dsn_lexer.LEFT, dsn_lexer.RIGHT) and _deletable(data, t.start, t.end)
        ]
        if parens and draw(st.booleans()):
            gone = draw(st.sampled_from(parens))
            data = data[: gone.start] + data[gone.end :]
        else:
            cuts = sorted({0, len(data)} | {x for t in toks for x in (t.start, t.end)})
            k = draw(st.sampled_from(cuts))
            data = data[:k] + draw(st.sampled_from((b"(", b")"))) + data[k:]
    return data
