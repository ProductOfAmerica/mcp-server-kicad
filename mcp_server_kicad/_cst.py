"""Lossless concrete syntax tree for KiCad s-expressions. Stdlib only.

Decision record: docs/adr-cst-substrate.md. This module deliberately imports
nothing from the rest of the package so it can be extracted later.

Encoding policy: bytes in, bytes out. Nothing is decoded during parse/serialize,
so CRLF vs LF, latin-1 mojibake, invalid UTF-8 and unknown escapes all survive
untouched. Text is decoded (utf-8) only when a caller asks for an atom's value,
and re-encoded only for atoms the caller actually edits.

Tokens are KiCad's (DSNLEXER, common/dsnlexer.cpp:545-833 at 9.0.8, the same at
10.0.7). Whitespace is space, TAB, CR, LF and NUL, never FF or VT. A line whose first
non-blank byte is '#' is a comment, and is whitespace here, kept in the separator of the
node that follows it. A quoted string must close on its own line. Any other run of bytes
up to whitespace or a paren is one bare atom, a double quote inside it included. A bar is a
token of its own to KiCad once the file's parser knows it (every current format); a run of
bare bytes and bars with nothing between them is one atom here. Formats that predate the
bar (schematics below 20240620, symbol libraries below 20240529, boards and footprints
below 20240706) read ``a|"b"`` as one bare token where this module reads two atoms; only a
hand-edited file holds it.

Invariants: every input byte lands in exactly one node's ``sep``/``raw``/
``close_sep``/``close``, so ``serialize(parse(b)) == b`` holds by construction;
malformed input (unbalanced parens, a string left open) raises ``SyntaxError`` instead of
being silently repaired the way KiCad's own reader does.

Memory model (hardened for board-scale files, slice-12A ADR entry): whitespace
is not a node. Each node carries its LEADING whitespace in ``sep``; a list
additionally carries the whitespace before its ``)`` in ``close_sep``. Nodes
are per-kind classes so atoms pay for exactly two slots, constants live on the
class, and parse-time interning dedups the highly repetitive separator and
token bytes of large boards.
"""

import math
import re

_BLANK = rb"[ \t\r\x00]"  # KiCad's whitespace but LF
_COMMENT = rb"#[^\n]*"
# The whitespace group: blanks, and after each LF the next line's blanks and, when it is
# one, its comment. A '#' anywhere else is data, a bare atom's byte.
_WHITESPACE = (
    rb"(?:\A"
    + _BLANK
    + rb"*"
    + _COMMENT
    + rb"|(?=[ \t\r\n\x00])"
    + _BLANK
    + rb"*)(?:\n"
    + _BLANK
    + rb"*(?:"
    + _COMMENT
    + rb")?)*"
)
TOKEN = re.compile(
    rb"(" + _WHITESPACE + rb")"  # 1
    rb"|(\()"  # 2
    rb"|(\))"  # 3
    rb'|("[^"\\\n]*(?:\\[^\n][^"\\\n]*)*")'  # 4 a string that closes on its own line
    rb'|(")'  # 5 a quote that opens no such string
    rb'|((?:\||[^ \t\r\n\x00()"|][^ \t\r\n\x00()|]*)+)'  # 6
)

WS, OPEN, CLOSE, STR, LONE_QUOTE, BARE = 1, 2, 3, 4, 5, 6

# What DSNLEXER decodes after a backslash inside a string (dsnlexer.cpp:661-712 at 9.0.8,
# 664-715 at 10.0.7): these, \x with one or two hex digits, and one to three octal
# digits cut to a byte. Any other byte keeps the backslash.
_UNESC = {
    b"\\": b"\\",
    b'"': b'"',
    b"a": b"\x07",
    b"b": b"\x08",
    b"f": b"\x0c",
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"v": b"\x0b",
    b"x": b"x",  # \x with no hex digit after it
}
_ESCAPE = re.compile(rb"\\(?:x([0-9A-Fa-f]{1,2})|([0-7]{1,3})|(.))", re.DOTALL)


def _unescape(m) -> bytes:
    if m.lastindex == 1:
        return bytes([int(m.group(1), 16)])
    if m.lastindex == 2:
        return bytes([int(m.group(2), 8) & 0xFF])
    other: bytes = m.group(3)
    return _UNESC[other] if other in _UNESC else b"\\" + other


# OUTPUTFORMATTER::Quotes escapes these four bytes and no other (richio.cpp:509-545 at 9.0.8).
_ESC = ((b"\\", b"\\\\"), (b'"', b'\\"'), (b"\n", b"\\n"), (b"\r", b"\\r"))
# What cannot stay in a bare atom: whitespace, a paren, a quote, a backslash or a bar.
_NEEDS_QUOTES = re.compile(rb'[\s()"\\|]')

_EMPTY = b""


def _anchor_ws(sep: bytes) -> bytes:
    """The line break and indentation of a separator, less any comment line before them."""
    if b"#" not in sep:
        return sep
    start = sep.rfind(b"\n")
    if start < 0:
        return sep
    if start and sep[start - 1 : start] == b"\r":
        start -= 1
    return sep[start:]


class Node:
    """Common base for CST nodes; concrete kinds are Atom, List, and Doc.

    Container accessors live here and are None-safe, so probing an atom with
    ``find``/``atoms`` behaves like probing an empty list, as it always has.
    """

    __slots__ = ()

    @property
    def atoms(self):
        return [c for c in self.children or () if c.kind == "atom"]

    @property
    def lists(self):
        return [c for c in self.children or () if c.kind == "list"]

    @property
    def head(self):
        a = self.atoms
        return a[0].text if a else None

    def find_all(self, name):
        """Direct child lists named *name*."""
        return [c for c in self.lists if c.head == name]

    def find(self, name):
        got = self.find_all(name)
        return got[0] if got else None


class Atom(Node):
    __slots__ = ("raw", "sep")

    kind = "atom"
    children = None

    def __init__(self, raw=b"", sep=b""):
        self.raw = raw
        self.sep = sep

    @property
    def text(self):
        """Atom value: quotes stripped, escapes decoded the way KiCad's lexer decodes them."""
        r = self.raw
        if r[:1] == b'"':
            r = r[1:-1] if r[-1:] == b'"' and len(r) > 1 else r[1:]
            if b"\\" in r:
                r = _ESCAPE.sub(_unescape, r)
        return r.decode("utf-8", "surrogateescape")

    def set_text(self, value):
        """Retext the atom, quoting only if it was quoted or now needs to be.

        A bare atom stays bare unless the value is empty, starts with '#' or holds
        whitespace, a paren, a quote, a backslash or a bar. A value holding a NUL, or a lone
        surrogate, is refused with ValueError and the atom is left as it was: KiCad 9 ends a
        value at its first NUL (include/dsnlexer.h:434-437 at 9.0.8) and no escape writes one.
        """
        b = value.encode("utf-8")
        if b"\x00" in b:
            raise ValueError("text holding a NUL cannot be written: no escape carries it to KiCad")
        if self.raw[:1] == b'"' or not b or b[:1] == b"#" or _NEEDS_QUOTES.search(b):
            for a, z in _ESC:
                b = b.replace(a, z)
            b = b'"' + b + b'"'
        self.raw = b

    def copy(self):
        return Atom(self.raw, self.sep)

    def __repr__(self):
        return f"Atom({self.text!r})"


class List(Node):
    __slots__ = ("sep", "children", "close_sep")

    kind = "list"
    raw = b"("
    close = b")"

    def __init__(self, sep=b"", children=None, close_sep=b""):
        self.sep = sep
        self.children = children if children is not None else []
        self.close_sep = close_sep

    def insert_after(self, ref, node, sep=None):
        """Splice *node* in after child *ref*, reusing ref's own line break and indentation."""
        i = self.children.index(ref)
        node.sep = sep if sep is not None else (_anchor_ws(ref.sep) or b"\n")
        self.children.insert(i + 1, node)

    def insert_before(self, ref, node, sep=None):
        """Splice *node* in before child *ref*, reusing ref's own line break and indentation."""
        i = self.children.index(ref)
        if sep is None:
            sep = _anchor_ws(ref.sep) or b"\n"
        node.sep = ref.sep
        ref.sep = sep
        self.children.insert(i, node)

    def append_child(self, node, sep=b"\n"):
        """Append *node* as the last child with *sep* as its leading whitespace."""
        node.sep = sep
        self.children.append(node)

    def remove_child(self, ref):
        """Delete child *ref*; its leading whitespace goes with it, a comment line above it
        included (one-span removal)."""
        self.children.remove(ref)

    def copy(self):
        top = type(self)(self.sep, [], self.close_sep)
        stack = [(self, top)]
        while stack:
            source, target = stack.pop()
            for child in source.children:
                if child.children is None:
                    target.children.append(Atom(child.raw, child.sep))
                else:
                    twin = type(child)(child.sep, [], child.close_sep)
                    target.children.append(twin)
                    stack.append((child, twin))
        return top

    def __repr__(self):
        return f"List({self.head!r}, {len(self.atoms)} atoms, {len(self.lists)} lists)"


class Doc(List):
    __slots__ = ()

    kind = "doc"
    raw = _EMPTY
    close = _EMPTY


def parse(data: bytes) -> Doc:
    """bytes -> CST. Raises SyntaxError on unbalanced parens or a string that does not close on
    its own line; everything else is data."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("parse() takes bytes, not str")
    root = Doc()
    stack: list[List] = [root]
    pending = _EMPTY
    interned: dict[bytes, bytes] = {}
    pos = 0
    for m in TOKEN.finditer(data):
        if m.start() != pos:  # would mean a byte fell through every branch
            raise AssertionError(f"gap at {pos}:{m.start()}")
        pos = m.end()
        g = m.lastindex
        if g == WS:
            tok = m.group()
            pending = interned.setdefault(tok, tok)
            continue
        top = stack[-1]
        if g == OPEN:
            n = List(sep=pending)
            top.children.append(n)
            stack.append(n)
        elif g == CLOSE:
            if len(stack) == 1:
                raise SyntaxError(f"unmatched ')' at byte {m.start()}")
            stack.pop().close_sep = pending
        elif g == LONE_QUOTE:
            raise SyntaxError(f"unterminated string at byte {m.start()}")
        else:
            tok = m.group()
            top.children.append(Atom(interned.setdefault(tok, tok), pending))
        pending = _EMPTY
    if pos != len(data):
        raise AssertionError(f"trailing {len(data) - pos} bytes unconsumed")
    if len(stack) != 1:
        raise SyntaxError(f"{len(stack) - 1} unclosed '(' at end of input")
    root.close_sep = pending
    return root


def serialize(node: Node) -> bytes:
    """Bytes for a node, exactly as parsed apart from edited spans.

    Write the result through ``_shared._atomic_write``, never straight to the
    destination: a plain write opens with O_TRUNC, so a failure part way through
    leaves the file truncated, which the invariant in docs/adr-cst-substrate.md
    forbids. Enforced by test_every_write_goes_through_atomic_write.
    """
    out = []
    stack: list[Node | bytes] = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, bytes):
            out.append(n)
            continue
        out.append(n.sep)
        out.append(n.raw)
        if n.children is not None:
            stack.append(n.close)
            stack.append(n.close_sep)
            stack.extend(reversed(n.children))
    return b"".join(out)


#: Internal units per millimetre, the grid KiCad holds a distance on: 100 nm in a schematic and
#: a symbol library, 1 nm on a board and in a footprint (include/base_units.h:70 and 72 at
#: 9.0.8, the same at 10.0.7).
SCH_IU_PER_MM = 10_000
PCB_IU_PER_MM = 1_000_000


def kiround(v: float) -> int:
    """KiCad 9's KiROUND to int, half away from zero
    (libs/kimath/include/math/util.h:99-142 at 9.0.8): ``v < 0 ? v - 0.5 : v + 0.5`` in
    floating point, then truncated.

    KiCad 10 rounds with std::llround instead (_shared.kiround10). Inside int the two agree on
    every double but +-0.49999999999999994, the one just below a half, where KiCad 9's added 0.5
    rounds up: KiCad 9 gives +-1, KiCad 10 gives 0. RotatePoint lands there where a turned
    coordinate is exactly that, as for a point 1 nm off the centre along an axis at 30 or 150
    degrees, so a board position goes through _shared._kiround_for and the KiCad whose format the
    board carries. This form also serves the readers that are given no board version: the
    schematic readers in _connectivity, pcb._numish_text, which negates a footprint's local
    coordinates for the flip, and _snap. Coordinates as KiCad writes them are whole units of its
    grid (100 nm in a schematic, 1 nm on a board), where the two never part; only a hand-written
    coordinate a hair below half a unit would read differently. Neither clamp is modelled:
    coordinates stay far inside int.
    """
    return int(v + 0.5) if v >= 0 else int(v - 0.5)


def _snap(v: float, iu: int) -> float:
    """The millimetres KiCad holds for ``v`` in a file of ``iu`` internal units per
    millimetre: KiROUND( v * iu ) / iu. A ``v`` already on that grid comes back as itself, so
    an int or a float that needs no rounding keeps its type and a message that echoes it
    reads as the request did. Raises ValueError for NaN and OverflowError for infinity."""
    held = kiround(v * iu) / iu
    return v if held == v else held


def _num(v: float, iu: int | None = None) -> str:
    """The text of a number in a file.

    With ``iu``, the file's internal units per millimetre (SCH_IU_PER_MM or PCB_IU_PER_MM), v is
    first snapped to the position KiCad holds (_snap), so the text is the one KiCad writes for
    it. Without it the text reads back as exactly v: KiCad's own, FormatInternalUnits
    (common/eda_units.cpp:170-200 at 9.0.8), when that reads back as v, and the shortest text that
    does when it does not. A whole number is an integer, never "-0" or "5.0"; the rest is
    ``{:.10f}`` stripped of its trailing zeros up to 0.0001 and ``{:.10g}`` above.
    """
    if iu is not None:
        v = _snap(v, iu)
    if v == int(v):
        return str(int(v))
    text = f"{v:.10f}".rstrip("0") if abs(v) <= 0.0001 else f"{v:.10g}"
    return text if float(text) == v else repr(v)


def _angle_text(degrees: float) -> str:
    """An angle as KiCad writes it: FormatAngle, fmt's ``{:.10g}``
    (common/eda_units.cpp:162-167 at 9.0.8, 186-191 at 10.0.7), which Python's ``.10g``
    matches. Adding 0.0 turns -0.0 into 0, so no angle is written "-0". An angle that is not
    finite is refused with ValueError: KiCad's parser takes an angle only as a number."""
    if not math.isfinite(degrees):
        raise ValueError(f"an angle must be finite, not {degrees}")
    return format(degrees + 0.0, ".10g")


def _numish(s: str | float) -> float | int:
    """Numeric text (or a number) as int when whole, else float: mirrors kiutils' repr in prints."""
    v = float(s)
    return int(v) if v == int(v) else v


def _node_text(node) -> str:
    return node.atoms[1].text


def _node_xy(node) -> tuple[float, float]:
    at = node.find("at")
    return float(at.atoms[1].text), float(at.atoms[2].text)


def _fill_at(
    node, x: float, y: float, rotation: float | None = None, iu: int | None = None
) -> None:
    """Set the position of ``node``'s first (at ...), and its angle when ``rotation`` is given
    (an atom is appended after y when the list has none). With ``iu``, the file's internal
    units per millimetre, x and y are written as _num writes them for that file and the angle
    as _angle_text does; without it every value is written exactly."""
    at = node.find("at")
    at.atoms[1].set_text(_num(x, iu))
    at.atoms[2].set_text(_num(y, iu))
    if rotation is not None:
        text = _num(rotation) if iu is None else _angle_text(rotation)
        if len(at.atoms) > 3:
            at.atoms[3].set_text(text)
        else:
            rot = at.atoms[2].copy()
            rot.sep = b" "
            rot.set_text(text)
            at.children.append(rot)


def demo():
    """Self-check: byte round-trip, KiCad-faithful tokens and escape codec, surgical edits."""
    hard = (
        b"(kicad_sch\r\n"
        b'\t(a "say \\"hi\\"" "back\\\\slash" "nl\\nB" "un\\Bk" "h\\x41\\102")\r\n'
        b'\t(b 1.5 -2 ~ \xe2\x84\xa6 "")\r\n'
        b"  # a comment (with a paren)\r\n"
        b'\t(c ab"cd" x\x0cy)\r\n)\r\n'
    )
    t = parse(hard)
    assert serialize(t) == hard, "byte round-trip broken"
    sch = t.lists[0]
    a = sch.find("a")
    assert sch.head == "kicad_sch"
    assert a.atoms[1].text == 'say "hi"'
    assert a.atoms[2].text == "back\\slash"
    assert a.atoms[3].text == "nl\nB", a.atoms[3].text  # \n decodes to LF, not 'n'
    assert a.atoms[4].text == "un\\Bk", a.atoms[4].text  # unknown escape keeps its slash
    assert a.atoms[5].text == "hAB", a.atoms[5].text  # hex and octal escapes
    assert sch.find("b").atoms[4].text.encode() == b"\xe2\x84\xa6"  # U+2126, bare atom
    assert len(sch.lists) == 3  # the comment line is whitespace
    c = sch.find("c")
    assert [x.raw for x in c.atoms[1:]] == [b'ab"cd"', b"x\x0cy"], c.atoms  # one atom each

    # The encoder inverts the decoder, never emits a raw LF, and quotes what a bare atom
    # could not stay.
    probe = Atom(b"x")
    for v in ['a"b\\c', "line1\nline2", "tab\there", "plain", "", "Ω()", "a|b", "#x"]:
        probe.set_text(v)
        assert probe.text == v, (v, probe.raw, probe.text)
        assert b"\n" not in probe.raw, probe.raw
    bare = Atom(b"x")
    bare.set_text("plain")
    assert bare.raw == b"plain"  # nothing to quote, so it stays bare
    try:
        bare.set_text("A\x00B")
    except ValueError:
        assert bare.raw == b"plain"
    else:
        raise AssertionError("a NUL should be refused")

    # Edit one atom: only that atom's bytes move.
    sch.find("b").atoms[1].set_text("9.75")
    out = serialize(t)
    assert out == hard.replace(b"(b 1.5", b"(b 9.75"), out

    # Splice a copy in after (c ...): every other byte stays put, and the comment line
    # above the anchor stays once.
    t2 = parse(hard)
    sch2 = t2.lists[0]
    node = sch2.find("a").copy()
    node.atoms[0].set_text("d")
    sch2.insert_after(sch2.find("c"), node)
    o2 = serialize(t2)
    assert o2.count(b"# a comment") == 1 and o2.startswith(hard[:20])
    assert serialize(parse(o2)) == o2

    # _fill_at appends the rotation atom when (at ...) lacks it.
    t_at = parse(b"(x\n\t(at 1 2)\n)")
    _fill_at(t_at.lists[0], 3, 4, 90)
    assert serialize(t_at) == b"(x\n\t(at 3 4 90)\n)", serialize(t_at)

    # Number text: exact without a scale, the position KiCad holds with one, and an angle as
    # FormatAngle writes it.
    assert _num(4.5e-05) == "0.000045" and _num(0.1 + 0.2) == "0.30000000000000004"
    assert _num(100.123456, SCH_IU_PER_MM) == "100.1235" and _num(50, PCB_IU_PER_MM) == "50"
    assert _snap(50, SCH_IU_PER_MM) == 50 and isinstance(_snap(50, SCH_IU_PER_MM), int)
    assert _angle_text(0.1 + 0.2) == "0.3" and _angle_text(-0.0) == "0"
    _fill_at(t_at.lists[0], 1.00004, 2.5e-07, 0.1 + 0.2, PCB_IU_PER_MM)
    assert serialize(t_at) == b"(x\n\t(at 1.00004 0 0.3)\n)", serialize(t_at)

    # insert_before mirrors insert_after; remove_child inverts both;
    # append_child grows a list with an explicit separator.
    t3 = parse(b"(r\n\t(x 1)\n\t(z 3)\n)")
    r = t3.lists[0]
    y = parse(b"(y 2)").lists[0]
    r.insert_before(r.find("z"), y)
    assert serialize(t3) == b"(r\n\t(x 1)\n\t(y 2)\n\t(z 3)\n)", serialize(t3)
    r.remove_child(r.find("y"))
    assert serialize(t3) == b"(r\n\t(x 1)\n\t(z 3)\n)", serialize(t3)
    w = parse(b"(w 4)").lists[0]
    r.append_child(w, b"\n\t")
    assert serialize(t3) == b"(r\n\t(x 1)\n\t(z 3)\n\t(w 4)\n)", serialize(t3)

    # Malformed input refuses loudly: unbalanced parens, and a string that does not close
    # on its own line.
    for bad in (b"(a (b)", b"(a))", b'(a "multi\nline")', b'(a) "open'):
        try:
            parse(bad)
        except SyntaxError:
            pass
        else:
            raise AssertionError(f"{bad!r} should have raised")
    print("cst self-check OK")


if __name__ == "__main__":
    demo()
