"""Lossless concrete syntax tree for KiCad s-expressions. Stdlib only.

Decision record: docs/adr-cst-substrate.md. This module deliberately imports
nothing from the rest of the package so it can be extracted later.

Encoding policy: bytes in, bytes out. Nothing is decoded during parse/serialize,
so CRLF vs LF, latin-1 mojibake, invalid UTF-8 and unknown escapes all survive
untouched. Text is decoded (utf-8) only when a caller asks for an atom's value,
and re-encoded only for atoms the caller actually edits.

Invariants: every input byte lands in exactly one node's ``sep``/``raw``/
``close_sep``/``close``, so ``serialize(parse(b)) == b`` holds by construction;
malformed input (unbalanced parens) raises ``SyntaxError`` instead of being
silently repaired the way KiCad's own reader does.

Memory model (hardened for board-scale files, slice-12A ADR entry): whitespace
is not a node. Each node carries its LEADING whitespace in ``sep``; a list
additionally carries the whitespace before its ``)`` in ``close_sep``. Nodes
are per-kind classes so atoms pay for exactly two slots, constants live on the
class, and parse-time interning dedups the highly repetitive separator and
token bytes of large boards.
"""

import re

# Order matters: ws first, bare atom last (it is the catch-all).
# An unterminated quote runs to EOF rather than raising, so it still round-trips.
TOKEN = re.compile(
    rb'(\s+)|(\()|(\))|("(?:[^"\\]|\\.)*"?)|([^\s()"]+)',
    re.DOTALL,
)

WS, OPEN, CLOSE, STR, BARE = 1, 2, 3, 4, 5

# Escape semantics measured against KiCad 9.0.8 itself (see the ADR):
# it unescapes \\ \" \n \r \t, keeps the backslash on an unknown escape
# (\B -> \B), and its writer re-escapes \ " LF CR (TAB is emitted raw).
# A raw LF inside a quoted atom makes KiCad refuse the file outright,
# so the encoder must escape it.
_UNESC = {b"\\": b"\\", b'"': b'"', b"n": b"\n", b"r": b"\r", b"t": b"\t"}
_ESC = ((b"\\", b"\\\\"), (b'"', b'\\"'), (b"\n", b"\\n"), (b"\r", b"\\r"))

_EMPTY = b""


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
    close = _EMPTY
    close_sep = _EMPTY

    def __init__(self, raw=b"", sep=b""):
        self.raw = raw
        self.sep = sep

    @property
    def text(self):
        """Atom value: quotes stripped, escapes decoded the way KiCad decodes them."""
        r = self.raw
        if r[:1] == b'"':
            r = r[1:-1] if r[-1:] == b'"' and len(r) > 1 else r[1:]
            r = re.sub(
                rb"\\(.)",
                lambda m: _UNESC.get(m.group(1), b"\\" + m.group(1)),
                r,
                flags=re.DOTALL,
            )
        return r.decode("utf-8", "surrogateescape")

    def set_text(self, value):
        """Retext the atom, quoting only if it was quoted or now needs to be."""
        b = value.encode("utf-8")
        if self.raw[:1] == b'"' or re.search(rb'[\s()"\\]', b) or not b:
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
        """Splice *node* in after child *ref*, reusing ref's own leading whitespace."""
        i = self.children.index(ref)
        node.sep = sep if sep is not None else (ref.sep or b"\n")
        self.children.insert(i + 1, node)

    def insert_before(self, ref, node, sep=None):
        """Splice *node* in before child *ref*, reusing ref's own leading whitespace."""
        i = self.children.index(ref)
        if sep is None:
            sep = ref.sep or b"\n"
        node.sep = ref.sep
        ref.sep = sep
        self.children.insert(i, node)

    def append_child(self, node, sep=b"\n"):
        """Append *node* as the last child with *sep* as its leading whitespace."""
        node.sep = sep
        self.children.append(node)

    def remove_child(self, ref):
        """Delete child *ref*; its leading whitespace goes with it (one-span removal)."""
        self.children.remove(ref)

    def copy(self):
        return type(self)(self.sep, [c.copy() for c in self.children], self.close_sep)

    def __repr__(self):
        return f"List({self.head!r}, {len(self.atoms)} atoms, {len(self.lists)} lists)"


class Doc(List):
    __slots__ = ()

    kind = "doc"
    raw = _EMPTY
    close = _EMPTY


def parse(data: bytes) -> Doc:
    """bytes -> CST. Raises SyntaxError on unbalanced parens; everything else is data."""
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


def demo():
    """Self-check: byte round-trip, KiCad-faithful escape codec, surgical edits."""
    # The raw LF inside quotes is deliberate: KiCad refuses to LOAD such a file,
    # but the CST still has to carry it through untouched.
    hard = (
        b"(kicad_sch\r\n"
        b'\t(a "say \\"hi\\"" "back\\\\slash" "multi\nline" "nl\\nB" "un\\Bk")\r\n'
        b'\t(b 1.5 -2 ~ \xe2\x84\xa6 "")\r\n)\r\n'
    )
    t = parse(hard)
    assert serialize(t) == hard, "byte round-trip broken"
    sch = t.lists[0]
    a = sch.find("a")
    assert sch.head == "kicad_sch"
    assert a.atoms[1].text == 'say "hi"'
    assert a.atoms[2].text == "back\\slash"
    assert a.atoms[3].text == "multi\nline"
    assert a.atoms[4].text == "nl\nB", a.atoms[4].text  # \n decodes to LF, not 'n'
    assert a.atoms[5].text == "un\\Bk", a.atoms[5].text  # unknown escape keeps its slash
    assert sch.find("b").atoms[4].text.encode() == b"\xe2\x84\xa6"  # U+2126, bare atom

    # The encoder must invert the decoder, and must never emit a raw LF.
    probe = Atom(b"x")
    for v in ['a"b\\c', "line1\nline2", "tab\there", "plain", "", "Ω()"]:
        probe.set_text(v)
        assert probe.text == v, (v, probe.raw, probe.text)
        assert b"\n" not in probe.raw, probe.raw

    # Edit one atom: only that atom's bytes move.
    sch.find("b").atoms[1].set_text("9.75")
    out = serialize(t)
    assert out == hard.replace(b"(b 1.5", b"(b 9.75"), out

    # Splice a copy in after (b ...): every other byte stays put.
    t2 = parse(hard)
    sch2 = t2.lists[0]
    node = sch2.find("a").copy()
    node.atoms[0].set_text("c")
    sch2.insert_after(sch2.find("b"), node)
    o2 = serialize(t2)
    assert o2.count(b'"multi\nline"') == 2 and o2.startswith(hard[:20])
    assert serialize(parse(o2)) == o2

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

    # Malformed input refuses loudly.
    for bad in (b"(a (b)", b"(a))"):
        try:
            parse(bad)
        except SyntaxError:
            pass
        else:
            raise AssertionError(f"{bad!r} should have raised")
    print("cst self-check OK")


if __name__ == "__main__":
    demo()
