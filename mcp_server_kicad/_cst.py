"""Lossless concrete syntax tree for KiCad s-expressions. Stdlib only.

Decision record: docs/adr-cst-substrate.md. This module deliberately imports
nothing from the rest of the package so it can be extracted later.

Encoding policy: bytes in, bytes out. Nothing is decoded during parse/serialize,
so CRLF vs LF, latin-1 mojibake, invalid UTF-8 and unknown escapes all survive
untouched. Text is decoded (utf-8) only when a caller asks for an atom's value,
and re-encoded only for atoms the caller actually edits.

Invariants: every input byte lands in exactly one leaf's ``raw``, so
``serialize(parse(b)) == b`` holds by construction; malformed input (unbalanced
parens) raises ``SyntaxError`` instead of being silently repaired the way
KiCad's own reader does.
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


class Node:
    __slots__ = ("kind", "raw", "children", "close")

    def __init__(self, kind, raw=b"", children=None, close=b""):
        self.kind = kind  # 'doc' | 'list' | 'atom' | 'ws'
        self.raw = raw  # leaf bytes, or b'(' for a list
        self.children = children if children is not None else []
        self.close = close  # b')' for a list

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

    def __repr__(self):
        if self.kind == "atom":
            return f"Atom({self.text!r})"
        if self.kind == "ws":
            return f"Ws({self.raw!r})"
        return f"List({self.head!r}, {len(self.atoms)} atoms, {len(self.lists)} lists)"

    @property
    def atoms(self):
        return [c for c in self.children if c.kind == "atom"]

    @property
    def lists(self):
        return [c for c in self.children if c.kind == "list"]

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

    def insert_after(self, ref, node, sep=None):
        """Splice *node* in after child *ref*, reusing ref's own leading whitespace."""
        i = self.children.index(ref)
        if sep is None:
            j = i - 1
            sep = self.children[j].raw if j >= 0 and self.children[j].kind == "ws" else b"\n"
        self.children[i + 1 : i + 1] = [Node("ws", sep), node]

    def insert_before(self, ref, node, sep=None):
        """Splice *node* in before child *ref*, reusing ref's own leading whitespace."""
        i = self.children.index(ref)
        if sep is None:
            j = i - 1
            sep = self.children[j].raw if j >= 0 and self.children[j].kind == "ws" else b"\n"
        self.children[i:i] = [node, Node("ws", sep)]

    def set_text(self, value):
        """Retext an atom, quoting only if it was quoted or now needs to be."""
        b = value.encode("utf-8")
        if self.raw[:1] == b'"' or re.search(rb'[\s()"\\]', b) or not b:
            for a, z in _ESC:
                b = b.replace(a, z)
            b = b'"' + b + b'"'
        self.raw = b

    def copy(self):
        return Node(self.kind, self.raw, [c.copy() for c in self.children], self.close)


def parse(data: bytes) -> Node:
    """bytes -> CST. Raises SyntaxError on unbalanced parens; everything else is data."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("parse() takes bytes, not str")
    root = Node("doc")
    stack = [root]
    pos = 0
    for m in TOKEN.finditer(data):
        if m.start() != pos:  # would mean a byte fell through every branch
            raise AssertionError(f"gap at {pos}:{m.start()}")
        pos = m.end()
        g = m.lastindex
        top = stack[-1]
        if g == OPEN:
            n = Node("list", b"(")
            top.children.append(n)
            stack.append(n)
        elif g == CLOSE:
            if len(stack) == 1:
                raise SyntaxError(f"unmatched ')' at byte {m.start()}")
            stack.pop().close = b")"
        else:
            top.children.append(Node("ws" if g == WS else "atom", m.group()))
    if pos != len(data):
        raise AssertionError(f"trailing {len(data) - pos} bytes unconsumed")
    if len(stack) != 1:
        raise SyntaxError(f"{len(stack) - 1} unclosed '(' at end of input")
    return root


def serialize(node: Node) -> bytes:
    out = []
    stack: list[Node | bytes] = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, bytes):
            out.append(n)
            continue
        out.append(n.raw)
        if n.kind == "list" or n.kind == "doc":
            stack.append(n.close)
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
    probe = Node("atom", b"x")
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

    # insert_before mirrors insert_after.
    t3 = parse(b"(r\n\t(x 1)\n\t(z 3)\n)")
    r = t3.lists[0]
    y = parse(b"(y 2)").lists[0]
    r.insert_before(r.find("z"), y)
    assert serialize(t3) == b"(r\n\t(x 1)\n\t(y 2)\n\t(z 3)\n)", serialize(t3)

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
