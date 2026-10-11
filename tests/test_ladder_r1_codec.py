"""Rung 1, the escape codec: Atom.text reads what KiCad writes, and KiCad reads what
set_text writes.

No judge here is the CST:

- what KiCad writes is tests/kicad_ref/richio.py, a port of OUTPUTFORMATTER::Quotes and
  Quotew (common/richio.cpp:509-556 at 9.0.8, 470-517 at 10.0.7);
- what KiCad reads is tests/kicad_ref/dsn_lexer.py, a port of DSNLEXER, applied to the
  whole document an atom sits in (so the comment-line rule and the bar apply), followed
  by its FromUTF8 model for the string KiCad stores;
- kicad-cli itself, through the component fields of its kicadxml netlist
  (conftest.netlist_fields);
- KiCad's board writer, for the pcb.py helpers that quote by hand.

Atom.text is judged at the token-byte level. The CST keeps a string's bytes by design;
KiCad then stores FromUTF8 of them, which ends a KiCad 9 value at its first NUL and reads
invalid UTF-8 as empty. So a comparison with kicad-cli passes the CST's bytes through the
same model, dsn_lexer.wx_from_utf8.

"KiCad reads back what set_text wrote" must hold for KiCad 9 and 10 alike: set_text
cannot know which major opens the file, and a KiCad 9 format file opens in both. A value
no encoding makes both of them read has to be refused, with the atom left as it was.

Values sent through kicad-cli avoid what KiCad rewrites before it prints a field, which
is none of the codec's doing: "{" (UnescapeString, common/string_utils.cpp:305-386 at
9.0.8, and the text variables "${" and, at 10.0.7, "@{"); a value that is exactly "~",
which KiCad 10 reads as empty in a file older than 20250318
(eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp:2329-2332 at 10.0.7; the
fixture is 20250114); and "<<<ESC_", KiCad 10's display markers
(eeschema/sch_field.cpp:254-256 at 10.0.7). A bare value never lexes as a number: the
schematic parser refuses a number as a property value (IsSymbol,
sch_io_kicad_sexpr_parser.cpp:2225-2231 at 9.0.8), which is a rule of the grammar.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import edit_judge as J
import pytest
import sexpr_gen as G
from conftest import _kicad_cli_major, netlist_fields, requires_cli
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref.richio import quotes

from mcp_server_kicad import _cst, pcb

FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"

#: The majors whose reading a written value must survive (see the module docstring).
MAJORS = (9, 10)

_BUG29 = "bug 29: Atom.text lacks KiCad's \\a \\b \\f \\v, hex and octal escapes; fixed by step 11"
_BUG59 = (
    "bug 59: set_text leaves '|' and NUL unquoted in a bare atom, and a '#' first on its"
    " line; fixed by step 11"
)
_BUG_59_NUL = (
    "bug 59: set_text writes a NUL inside quotes, where KiCad 9 ends the value; fixed by step 11"
)
_BUG_67 = (
    "bug 67: pcb._pad_net_bytes, _ensure_net_rows and _set_fp_path quote by hand, not"
    " as Quotew does (LF and CR raw, the path not escaped at all); fixed by step 11"
)


def _xfail(reason: str):
    """The one xfail the ladder allows: strict, and only for the bug's own assertion."""
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


# ---------------------------------------------------------------------------
# strategies and helpers
# ---------------------------------------------------------------------------

#: Any text but a lone surrogate, which has no UTF-8 form.
_ANY_TEXT = st.text(st.characters(exclude_categories=("Cs",)))
#: Text holding at least one NUL.
_TEXT_WITH_NUL = st.builds(lambda head, tail: head + "\x00" + tail, G.kicad_text, _ANY_TEXT)
#: Text holding at least one lone surrogate.
_UNENCODABLE = st.builds(
    lambda head, lone, tail: head + lone + tail,
    _ANY_TEXT,
    st.characters(categories=("Cs",)),
    _ANY_TEXT,
)


def _targets(kind: str):
    """(document, path) with a ``kind`` atom first on its line, after something on its
    line, or either."""
    return st.sampled_from((True, False, None)).flatmap(
        lambda line_start: G.doc_with_target(kind=kind, line_start=line_start)
    )


def _atom_at(tree, path: tuple[int, ...]) -> _cst.Atom:
    """The CST atom at a child-index path (the premise every target strategy promises)."""
    node = tree
    for index in path:
        node = node.children[index]
    if not isinstance(node, _cst.Atom):
        raise RuntimeError(f"the target at {path} is not an atom: {node!r}")
    return node


def _lexed(data: bytes, *, premise: bool = False) -> dict:
    """KiCad's lexer's items of ``data``, bar known, keyed by child-index path: the
    address the CST's children use, and doc_with_target's paths."""
    try:
        return {tuple(i for i, _ in loc): item for loc, item in J.walk(data, knows_bar=True)}
    except L.LexError as exc:
        if premise:
            raise RuntimeError(f"the input does not lex, so nothing can be judged: {exc}") from exc
        raise AssertionError(f"KiCad's lexer cannot read what set_text wrote: {exc}") from exc


def _string_value(raw: bytes) -> bytes:
    """The bytes KiCad's lexer reads for one quoted atom on its own."""
    toks = L.tokens(b"(x " + raw + b")")
    if [t.kind for t in toks] != [L.LEFT, L.SYMBOL, L.STRING, L.RIGHT]:
        raise RuntimeError(f"{raw!r} is not one string to KiCad's lexer: {toks}")
    return toks[2].value


@st.composite
def _string_in_context(draw, bodies):
    """(document, path): a dialect document whose string at ``path`` has a body drawn
    from ``bodies``, spliced in where KiCad's lexer finds that string."""
    doc, path = draw(_targets("quoted"))
    item = _lexed(doc, premise=True)[path]
    return doc[: item.start] + b'"' + draw(bodies) + b'"' + doc[item.end :], path


def _assert_text_is_kicads(body: bytes) -> None:
    """Atom.text of a string with this body holds the bytes KiCad's lexer reads."""
    raw = b'"' + body + b'"'
    atom = _cst.parse(b"(x " + raw + b")").lists[0].atoms[1]
    assert atom.raw == raw, f"the CST reads {atom.raw!r} as the string, not {raw!r}"
    text = atom.text.encode("utf-8", "surrogateescape")
    kicad = _string_value(raw)
    assert text == kicad, f"{raw!r}: the CST reads {text!r}, KiCad's lexer {kicad!r}"


def _assert_kicad_reads_back(target, value: str, kinds) -> None:
    """set_text(value) on the atom at the target's path, judged by KiCad's reading.

    Either set_text refuses and leaves the atom and the document as they were, or KiCad's
    lexer reads the whole document with the same structure, reads the atom as one token
    of ``kinds`` holding exactly the bytes set_text wrote, and every major in MAJORS
    stores ``value`` for that token.
    """
    doc, path = target
    before = _lexed(doc, premise=True)
    tree = _cst.parse(doc)
    atom = _atom_at(tree, path)
    raw, sep = atom.raw, atom.sep
    try:
        atom.set_text(value)
    except ValueError:
        assert (atom.raw, atom.sep) == (raw, sep), "a refused set_text changed the atom"
        assert _cst.serialize(tree) == doc, "a refused set_text changed the document"
        return
    after = _cst.serialize(tree)
    items = _lexed(after)
    assert items.keys() == before.keys(), (
        f"KiCad's lexer reads another structure once set_text wrote {atom.raw!r}: {after!r}"
    )
    token = items[path]
    assert token.kind in kinds and token.raw == atom.raw, (
        f"KiCad reads the {token.kind} {token.raw!r} where set_text wrote {atom.raw!r}"
    )
    for major in MAJORS:
        read = L.wx_from_utf8(token.value, major=major)
        assert read == value, f"KiCad {major} reads {read!r} where set_text was given {value!r}"


#: Every keyword KiCad's lexer knows is made of these bytes (eeschema/schematic.keywords,
#: common/pcb.keywords, common/drawing_sheet/drawing_sheet.keywords and
#: common/lib_table.keywords, at 9.0.8 and 10.0.7), and the lexer looks a keyword up only
#: for a bare token (dsnlexer.cpp:804-824 at 9.0.8); a quoted one is a string (719).
_KEYWORD_BYTES = frozenset(b"abcdefghijklmnopqrstuvwxyz0123456789_")


def _assert_retext_keeps_reading(target) -> None:
    """set_text(text) on the atom at the target's path leaves KiCad's reading alone.

    The token keeps the bytes KiCad reads, and its kind wherever KiCad's grammar can tell
    kinds apart: a string stays a string, a number a number, and a bare token that could
    be a keyword stays bare. Any other bare token may become a string, which KiCad takes
    wherever it takes a symbol (DSNLEXER::IsSymbol, dsnlexer.cpp:327-333 at 9.0.8 and
    10.0.7). A refusal that changes nothing keeps the reading too.
    """
    doc, path = target
    before = _lexed(doc, premise=True)
    tree = _cst.parse(doc)
    atom = _atom_at(tree, path)
    raw = atom.raw
    try:
        atom.set_text(atom.text)
    except ValueError:
        assert atom.raw == raw and _cst.serialize(tree) == doc, "a refused set_text wrote"
        return
    after = _cst.serialize(tree)
    items = _lexed(after)
    assert items.keys() == before.keys(), f"KiCad's lexer reads another structure: {after!r}"
    old, new = before[path], items[path]
    assert new.raw == atom.raw, f"KiCad reads {new.raw!r} where set_text wrote {atom.raw!r}"
    assert new.value == old.value, (
        f"KiCad read {old.value!r} from {old.raw!r}, and reads {new.value!r} from {new.raw!r}"
    )
    if old.kind == L.SYMBOL and not set(old.value) <= _KEYWORD_BYTES:
        assert new.kind in (L.SYMBOL, L.STRING), f"{old.raw!r} became the {new.kind} {new.raw!r}"
    else:
        assert new.kind == old.kind, f"{old.raw!r} was a {old.kind}, {new.raw!r} is a {new.kind}"


# ---------------------------------------------------------------------------
# what KiCad's writer wrote, Atom.text reads
# ---------------------------------------------------------------------------


@given(G.kicad_text)
@example('A"B\\C\nD\rE\tF')
@example("")
@example("Ω→✓\U0001f600")
def test_text_inverts_kicads_writer(text):
    """Atom.text undoes Quotew: a string KiCad wrote reads back as the text it wrote.

    A KiCad string holds neither a lone surrogate nor a NUL (Quotew hands its UTF-8 to
    Quotes as a C string), which is the alphabet of G.kicad_text.
    """
    raw = quotes(text)
    atom = _cst.parse(b"(x " + raw + b")").lists[0].atoms[1]
    assert atom.raw == raw
    assert atom.text == text


@given(
    st.one_of(G.quoted_atom(), G.agreed_escape_body().map(lambda body: b'"' + body + b'"')),
    G.kicad_text,
)
@example(b'"q"', "plain")
@example(b'"q"', "A\rB")
@example(b'"q"', 'A"B\\C\nD\tE')
@example(b'"A\\tB"', "")
def test_quoted_set_text_writes_kicads_quoting(start, value):
    """On a quoted atom, set_text writes what KiCad's writer writes, Quotew(value), and
    nothing but the atom's own bytes."""
    atom = _cst.Atom(start, b" ")
    atom.set_text(value)
    assert atom.raw == quotes(value)
    assert atom.sep == b" "


@given(G.agreed_escape_body())
@example(b"A\\tB")
@example(b"A\\rB")
@example(b"A\\nB")
@example(b'A\\"B')
@example(b"A\\\\B")
@example(b"A\\BC")
@example(b"A\\8B")
@example(b"A\tB\rC\x00D\xffE")
def test_agreed_escapes_decode_as_kicads_lexer(body):
    """For the escapes both readers know (\\" \\\\ \\n \\r \\t), the ones KiCad reads as
    a backslash and then the byte (\\B, \\8, \\9, a backslash before a multibyte
    character), and raw TAB, CR, NUL and high bytes, Atom.text holds the bytes KiCad's
    lexer reads."""
    _assert_text_is_kicads(body)


@_xfail(_BUG29)
@given(G.kicad_only_escape_body())
@example(b"A\\x42C")
@example(b"A\\102C")
@example(b"A\\xgC")
@example(b"x\\ay")
@example(b"\\x4")
def test_kicad_only_escapes_decode_as_kicads_lexer(body):
    """The same for the escapes only KiCad's lexer decodes: \\a \\b \\f \\v, \\x with zero
    to two hex digits, and one to three octal digits (dsnlexer.cpp:661-712 at 9.0.8,
    664-715 at 10.0.7)."""
    _assert_text_is_kicads(body)


# ---------------------------------------------------------------------------
# what set_text wrote, KiCad reads
# ---------------------------------------------------------------------------


@given(_targets("quoted"), G.kicad_text)
@example((b'(a\n\t"q" x)', (0, 1)), "#x")
@example((b'(a "q")', (0, 1)), 'A"B\\C\nD\rE\tF(G)')
def test_kicad_reads_back_quoted_set_text(target, value):
    """set_text on a quoted atom anywhere in a document, mid-line or first on its line:
    KiCad's lexer reads one string, the bytes set_text wrote, and KiCad 9 and 10 store
    the value for it."""
    _assert_kicad_reads_back(target, value, {L.STRING})


@_xfail(_BUG_59_NUL)
@given(_targets("quoted"), _TEXT_WITH_NUL)
@example((b'(a "q")', (0, 1)), "A\x00B")
def test_quoted_set_text_refuses_nul(target, value):
    """The same for a value holding a NUL. KiCad 9 ends a value at its first NUL
    (dsnlexer.h:434-437 at 9.0.8; measured on kicad-cli 9.0.8, 2026-10-10: "A<NUL>B"
    reads A), and no escape writes one (Quotes escapes only LF, CR, the backslash and
    the quote), so set_text has to refuse it."""
    _assert_kicad_reads_back(target, value, {L.STRING})


@_xfail(_BUG59)
@given(_targets("bare"), _ANY_TEXT)
@example((b"(a z b)", (0, 1)), "a|b")
@example((b"(a z b)", (0, 1)), "a\x00b")
@example((b"(a\n\tz b)", (0, 1)), "#x")
def test_kicad_reads_back_bare_set_text(target, value):
    """The same for a bare atom, with the bar known as every current KiCad file has it:
    a bar splits a bare token (dsnlexer.cpp:611-633 at 9.0.8), a NUL is whitespace
    (452-470), and a line whose first non-blank byte is '#' is a comment (575-598). A
    value holding a NUL can only be refused, as for a quoted atom."""
    _assert_kicad_reads_back(target, value, L.ATOM_KINDS)


def _starts_its_line(doc: bytes, path: tuple[int, ...]) -> bool:
    """Whether the item at ``path`` is the first thing on its line but blanks, the bytes
    DSNLEXER skips before its comment test (isSpace, dsnlexer.cpp:452-470 at 9.0.8)."""
    start = _lexed(doc, premise=True)[path].start
    return not doc[doc.rfind(b"\n", 0, start) + 1 : start].strip(b" \t\r\x00")


@given(_targets("bare"), _ANY_TEXT.filter(lambda value: "|" not in value and "\x00" not in value))
@example((b"(a z b)", (0, 1)), "")
@example((b"(a z b)", (0, 1)), "two words")
@example((b"(a z b)", (0, 1)), "(paren)")
@example((b"(a z b)", (0, 1)), '"x')
@example((b"(a z b)", (0, 1)), "#x")
def test_kicad_reads_back_bare_set_text_outside_bug_59(target, value):
    """The same property over every bare value bug 59 does not cover: no bar, no NUL, and
    no '#' when the atom starts its line. The split follows the bug's own scope, never
    where the code passes, so the rest of the bare codec stays judged while bug 59's
    xfail stands."""
    doc, path = target
    assume(not (value.startswith("#") and _starts_its_line(doc, path)))
    _assert_kicad_reads_back(target, value, L.ATOM_KINDS)


@given(st.one_of(_targets("bare"), _targets("quoted"), _string_in_context(G.agreed_escape_body())))
@example((b'(a "A\\tB")', (0, 1)))
@example((b'(a "A\\rB")', (0, 1)))
@example((b"(a x\\y)", (0, 1)))
@example((b"(a yes 1.5)", (0, 1)))
@example((b"(a yes 1.5)", (0, 2)))
def test_retext_keeps_kicads_reading(target):
    """set_text(text) changes nothing KiCad reads, for the atoms KiCad writes and for
    strings holding the escapes both readers know."""
    _assert_retext_keeps_reading(target)


@_xfail(_BUG29)
@given(_string_in_context(G.kicad_only_escape_body()))
@example((b'(a "A\\x42C")', (0, 1)))
@example((b'(a "A\\102C")', (0, 1)))
@example((b'(a "A\\xgC")', (0, 1)))
@example((b'(a "x\\ay")', (0, 1)))
@example((b'(a "\\x4")', (0, 1)))
def test_retext_keeps_kicads_reading_for_kicad_only_escapes(target):
    """The same for strings holding the escapes only KiCad's lexer decodes."""
    _assert_retext_keeps_reading(target)


@given(st.one_of(_targets("quoted"), _targets("bare")), _UNENCODABLE)
@example((b'(a "q")', (0, 1)), "\ud800")
@example((b"(a q)", (0, 1)), "x\udcffy")
def test_set_text_refuses_unencodable_text_untouched(target, value):
    """A lone surrogate has no UTF-8 form, so no KiCad string holds one (Quotew writes
    UTF-8): set_text refuses it, and a refusal writes nothing."""
    doc, path = target
    tree = _cst.parse(doc)
    atom = _atom_at(tree, path)
    raw, sep = atom.raw, atom.sep
    with pytest.raises(ValueError):
        atom.set_text(value)
    assert (atom.raw, atom.sep) == (raw, sep)
    assert _cst.serialize(tree) == doc


# ---------------------------------------------------------------------------
# the same, against kicad-cli
# ---------------------------------------------------------------------------


def _schematic_with_fields(fields) -> bytes:
    """kicad_native.kicad_sch with properties added to its placed R1, each (name, raw
    value, whether the value starts its line), in the file's own line ending."""
    data = FIXTURE.read_bytes()
    eol = b"\r\n" if b"\r\n" in data[:200] else b"\n"
    anchor = data.rindex(b'(property "Description"')  # R1's own, after lib_symbols
    added = b""
    for name, value, line_start in fields:
        sep = eol + b"\t\t\t" if line_start else b" "
        added += (
            b"(property "
            + quotes(name)
            + sep
            + value
            + b" (at 100 100 0) (effects (font (size 1.27 1.27)) (hide yes)))"
            + eol
            + b"\t\t"
        )
    return data[:anchor] + added + data[anchor:]


def _field_atom(tree, name: str):
    """The value atom of property ``name`` on the fixture's placed R1."""
    (r1,) = tree.lists[0].find_all("symbol")
    for prop in r1.find_all("property"):
        if prop.atoms[1].text == name:
            return prop.atoms[2]
    raise LookupError(f"R1 has no property {name!r}")


def _cli_major() -> int:
    major = _kicad_cli_major()
    if major < 9:
        raise RuntimeError(f"cannot read the major version of kicad-cli (got {major})")
    return major


def _prints_verbatim(text: str) -> bool:
    """Whether kicad-cli prints a field holding ``text`` unchanged (module docstring)."""
    return "{" not in text and text != "~" and "<<<ESC_" not in text


#: One property per escape class: (field name, the value as written, quotes included).
_ESCAPE_CLASSES = [
    ("E_QUOTE", rb'"A\"B"'),
    ("E_BACKSLASH", rb'"A\\B"'),
    ("E_N", rb'"A\nB"'),
    ("E_R", rb'"A\rB"'),
    ("E_T", rb'"A\tB"'),
    ("E_UNKNOWN", rb'"A\BC"'),
    ("E_8", rb'"A\8B"'),
    ("E_9", rb'"A\9B"'),
    ("E_BEFORE_MULTIBYTE", '"A\\ΩB"'.encode()),
    ("RAW_TAB", b'"A\tB"'),
    ("RAW_CR", b'"A\rB"'),
    ("RAW_FF", b'"A\x0cB"'),
    ("RAW_UTF8", '"Ω→✓"'.encode()),
    ("OVERLONG_TWO_BYTE", b'"A\xc0\xafB"'),
    ("OVERLONG_THREE_BYTE", b'"A\xe0\x80\xafB"'),
    ("LEAD_F5", b'"A\xf5\x80\x80\x80B"'),
    ("CUT_OFF_SEQUENCE", b'"A\xe2\x82B"'),
    ("E_A", rb'"A\aB"'),
    ("E_B", rb'"A\bB"'),
    ("E_F", rb'"A\fB"'),
    ("E_V", rb'"A\vB"'),
    ("E_X2", rb'"A\x42C"'),
    ("E_X1", rb'"A\x4"'),
    ("E_X0", rb'"A\xgC"'),
    ("E_X_AT_END", rb'"A\x"'),
    ("E_OCT3", rb'"A\102C"'),
    ("E_OCT1", rb'"A\7B"'),
    ("E_OCT_WRAPS", rb'"A\777B"'),
    ("E_HEX_INVALID_UTF8", rb'"A\xffB"'),
    ("E_HEX_UTF8", rb'"\xf0\x9f\x98\x80"'),
]
#: Values holding a NUL get a schematic of their own: the one place KiCad 9 and 10 store
#: different strings (dsnlexer.h:434-437 at 9.0.8 ends the value at the NUL, 439-442 at
#: 10.0.7 keeps it).
_NUL_CLASSES = [
    ("RAW_NUL", b'"A\x00B"'),
    ("E_OCT_NUL", rb'"A\0B"'),
    ("E_OCT_WRAPS_TO_NUL", rb'"A\400B"'),
]
_CLASS_GROUPS = {"escapes": _ESCAPE_CLASSES, "nul": _NUL_CLASSES}

#: The bytes after a backslash that only KiCad's lexer decodes: \a \b \f \v, \x, and the
#: octal digits (dsnlexer.cpp:661-712 at 9.0.8, 664-715 at 10.0.7). Atom.text decodes the
#: rest of the table as KiCad does, so a class is bug 29's exactly when its raw holds one.
_KICAD_ONLY_ESCAPE = frozenset(b"abfvx01234567")


def _holds_kicad_only_escape(raw: bytes) -> bool:
    body = raw[1:-1]
    i = 0
    while i < len(body):
        if body[i] != 0x5C:
            i += 1
        elif i + 1 < len(body) and body[i + 1] in _KICAD_ONLY_ESCAPE:
            return True
        else:
            i += 2
    return False


def _class_params():
    for group, classes in _CLASS_GROUPS.items():
        for name, raw in classes:
            marks = [_xfail(_BUG29)] if _holds_kicad_only_escape(raw) else []
            yield pytest.param(group, name, raw, marks=marks, id=name)


@pytest.fixture(scope="module")
def kicad_reading(tmp_path_factory):
    """kicad-cli's reading of each class group's schematic, one kicad-cli run per group."""
    cache: dict[str, tuple[dict, bytes]] = {}

    def read(group: str) -> tuple[dict, bytes]:
        if group not in cache:
            data = _schematic_with_fields([(n, raw, False) for n, raw in _CLASS_GROUPS[group]])
            path = tmp_path_factory.mktemp(group) / "classes.kicad_sch"
            path.write_bytes(data)
            got = netlist_fields(path)
            if got is None:
                raise RuntimeError(f"kicad-cli could not load the {group} schematic")
            cache[group] = (got, data)
        return cache[group]

    return read


@requires_cli
@pytest.mark.parametrize(("group", "name", "raw"), list(_class_params()))
def test_cst_reads_each_escape_class_as_kicad_cli(kicad_reading, group, name, raw):
    """One field per escape class, read by kicad-cli and by the CST: the string KiCad
    stores for the CST's bytes is the one kicad-cli prints."""
    got, data = kicad_reading(group)
    atom = _field_atom(_cst.parse(data), name)
    assert atom.raw == raw, f"the CST reads {atom.raw!r} as the value, not {raw!r}"
    major = _cli_major()
    cst = L.wx_from_utf8(atom.text.encode("utf-8", "surrogateescape"), major=major)
    kicad = got.get(("R1", name), "")
    assert cst == kicad, f"{raw!r}: the CST's text {atom.text!r} gives {cst!r}, kicad-cli {kicad!r}"


#: (field, value, whether the value starts its line): each byte Quotes escapes, TAB,
#: which it does not, parens, multibyte text, the empty string, a bar, and a '#' first on
#: its line.
_QUOTED_READ_BACK = [
    ("Q_QUOTE", 'A"B', False),
    ("Q_BACKSLASH", "A\\B", False),
    ("Q_LF", "A\nB", False),
    ("Q_CR", "A\rB", False),
    ("Q_CRLF", "A\r\nB", False),
    ("Q_TAB", "A\tB", False),
    ("Q_PARENS", "A(B)C", False),
    ("Q_MULTIBYTE", "Ω→✓\U0001f600", False),
    ("Q_EMPTY", "", False),
    ("Q_BAR", "a|b", False),
    ("Q_HASH", "#x", True),
]


@requires_cli
def test_kicad_cli_reads_back_quoted_set_text(tmp_path):
    """Values written by set_text into quoted property values read back exactly."""
    fields = [(name, b'"placeholder"', line_start) for name, _, line_start in _QUOTED_READ_BACK]
    tree = _cst.parse(_schematic_with_fields(fields))
    for name, value, _ in _QUOTED_READ_BACK:
        _field_atom(tree, name).set_text(value)
    path = tmp_path / "quoted.kicad_sch"
    path.write_bytes(_cst.serialize(tree))
    got = netlist_fields(path)
    assert got is not None, "kicad-cli cannot load what set_text wrote into quoted values"
    wrong = [
        f"{name}: set_text was given {value!r}, kicad-cli reads {got.get(('R1', name), '')!r}"
        for name, value, _ in _QUOTED_READ_BACK
        if got.get(("R1", name), "") != value
    ]
    assert not wrong, "\n".join(wrong)


@requires_cli
@pytest.mark.parametrize(
    ("value", "line_start"),
    [
        pytest.param("a|b", False, marks=_xfail(_BUG59), id="bar"),
        pytest.param("a\x00b", False, marks=_xfail(_BUG59), id="nul"),
        pytest.param("#x", True, marks=_xfail(_BUG59), id="hash-first-on-its-line"),
        pytest.param("plain", False, id="plain"),
    ],
)
def test_kicad_cli_reads_back_bare_set_text(tmp_path, value, line_start):
    """set_text into a bare property value: kicad-cli loads the file and reads the value,
    or set_text refuses. Measured on kicad-cli 9.0.8 (2026-10-10): the three bug-59
    values make it answer "Failed to load schematic".

    Each xfailed parameter leaves that unloadable file in tmp_path, which the autouse
    kicad-cli oracle has to leave alone because the call phase xfailed: this is that
    rule's end-to-end check, since a teardown error here would mean the oracle judged an
    xfailed call.
    """
    tree = _cst.parse(_schematic_with_fields([("P", b"placeholder", line_start)]))
    atom = _field_atom(tree, "P")
    raw = atom.raw
    try:
        atom.set_text(value)
    except ValueError:
        assert atom.raw == raw, "a refused set_text changed the atom"
        return
    path = tmp_path / "bare.kicad_sch"
    path.write_bytes(_cst.serialize(tree))
    got = netlist_fields(path)
    assert got is not None, f"kicad-cli cannot load the file once set_text wrote {atom.raw!r}"
    assert got.get(("R1", "P"), "") == value


@requires_cli
@_xfail(_BUG_59_NUL)
def test_kicad_cli_reads_back_quoted_nul(tmp_path):
    """set_text("A<NUL>B") on a quoted property value: kicad-cli reads A<NUL>B, or
    set_text refuses. Measured on kicad-cli 9.0.8 (2026-10-10): it reads A."""
    if _cli_major() >= 10:
        pytest.skip(
            "KiCad 10 keeps a NUL in the value (dsnlexer.h:439-442 at 10.0.7), so only a KiCad 9"
            " kicad-cli shows the truncation; test_quoted_set_text_refuses_nul judges both"
            " majors' models on every leg"
        )
    tree = _cst.parse(_schematic_with_fields([("P", b'"placeholder"', False)]))
    atom = _field_atom(tree, "P")
    raw = atom.raw
    try:
        atom.set_text("A\x00B")
    except ValueError:
        assert atom.raw == raw, "a refused set_text changed the atom"
        return
    path = tmp_path / "nul.kicad_sch"
    path.write_bytes(_cst.serialize(tree))
    got = netlist_fields(path)
    assert got is not None, f"kicad-cli cannot load the file once set_text wrote {atom.raw!r}"
    assert got.get(("R1", "P"), "") == "A\x00B"


# ---------------------------------------------------------------------------
# board writers that quote by hand
# ---------------------------------------------------------------------------

#: A KiCad 9 board header (SEXPR_BOARD_FILE_VERSION 20241229, pcbnew/pcb_io/kicad_sexpr/
#: pcb_io_kicad_sexpr.h:175 at 9.0.8) with its net table, and a KiCad 10 one (20260206,
#: :204 at 10.0.7), which has none.
_K9_BOARD = (
    b'(kicad_pcb\n\t(version 20241229)\n\t(generator "pcbnew")\n\t(net 0 "")\n\t(net 1 "GND")\n)\n'
)
_K10_BOARD = b'(kicad_pcb\n\t(version 20260206)\n\t(generator "pcbnew")\n)\n'
_FOOTPRINT = b'(footprint "R_0603"\n\t(layer "F.Cu")\n\t(at 100 100)\n)'


def _own_bytes(node) -> bytes | None:
    return None if node is None else _cst.serialize(node)[len(node.sep) :]


def _pad_net_k9(name: str):
    root = _cst.parse(_K9_BOARD).lists[0]
    return pcb._pad_net_bytes(root, 7, name), b"(net 7 " + quotes(name) + b")"


def _pad_net_k10(name: str):
    root = _cst.parse(_K10_BOARD).lists[0]
    return pcb._pad_net_bytes(root, 7, name), b"(net " + quotes(name) + b")"


def _net_row(name: str):
    root = _cst.parse(_K9_BOARD).lists[0]
    pcb._ensure_net_rows(root, [name])
    row = root.find_all("net")[-1]
    return _own_bytes(row), b"(net " + row.atoms[1].raw + b" " + quotes(name) + b")"


def _fp_path(path: str):
    fp = _cst.parse(_FOOTPRINT).lists[0]
    pcb._set_fp_path(fp, path)
    return _own_bytes(fp.find("path")), b"(path " + quotes(path) + b")"


_BOARD_WRITERS = {
    "pad_net_k9": _pad_net_k9,
    "pad_net_k10": _pad_net_k10,
    "net_row": _net_row,
    "fp_path": _fp_path,
}


@_xfail(_BUG_67)
@pytest.mark.parametrize("writer", sorted(_BOARD_WRITERS))
@given(G.kicad_text.filter(lambda text: text not in ("", "GND")))
@example("a\nb")
@example('/a"b')
def test_board_writers_quote_as_kicad_does(writer, text):
    """Each hand-quoting board writer emits the bytes KiCad's own writer emits: "(net %d
    %s)" with Quotew for a net row and a pad's net (pcb_io_kicad_sexpr.cpp:678-680 and
    1613-1614 at 9.0.8), "(net %s)" in a KiCad 10 board (1858 at 10.0.7), and "(path %s)"
    for a footprint's path (1175 at 9.0.8, 1278 at 10.0.7). A writer that cannot parse
    what it built has written something else, which is a mismatch too."""
    try:
        got, want = _BOARD_WRITERS[writer](text)
    except SyntaxError as exc:
        raise AssertionError(f"{writer} cannot parse what it built for {text!r}: {exc}") from exc
    assert got == want


# ---------------------------------------------------------------------------
# nightly: the kicad-cli checks over generated batches
# ---------------------------------------------------------------------------

#: One kicad-cli run per example.
_NIGHTLY = settings(max_examples=G.budget(25))
_BATCH = 40


def _batch(values):
    """Half a batch to a whole one: left to itself, st.lists averages a few elements, and
    the cost of an example is the kicad-cli run, not the values in it."""
    return st.lists(values, min_size=_BATCH // 2, max_size=_BATCH)


def _kicad_reads(data: bytes) -> dict | None:
    """netlist_fields of ``data`` written to a fresh temporary directory (a @given test
    never takes tmp_path)."""
    with tempfile.TemporaryDirectory() as work:
        path = Path(work) / "batch.kicad_sch"
        path.write_bytes(data)
        return netlist_fields(path)


def _body_prints_verbatim(body: bytes) -> bool:
    """Whether kicad-cli's field for a string with this body is a fair witness: it holds
    no NUL (the one place the majors store different strings, left to the hand-picked NUL
    classes), nothing FromUTF8 holds per platform, and nothing KiCad rewrites."""
    value = _string_value(b'"' + body + b'"')
    if b"\x00" in value:
        return False
    try:
        return _prints_verbatim(L.wx_from_utf8(value, major=9))  # no NUL: both majors agree
    except ValueError:
        return False


def _escapes_against_kicad_cli(bodies: list[bytes]) -> None:
    """One schematic of strings with these bodies: the lexer port must read what kicad-cli
    reads (the judge, checked first, and raising RuntimeError so it is never taken for the
    CST's failure), and then the CST must."""
    fields = [(f"F{i}", b'"' + body + b'"', i % 2 == 1) for i, body in enumerate(bodies)]
    data = _schematic_with_fields(fields)
    got = _kicad_reads(data)
    if got is None:
        raise RuntimeError("kicad-cli could not load a schematic of well-formed strings")
    major = _cli_major()
    kicad = {name: got.get(("R1", name), "") for name, _, _ in fields}
    for name, raw, _ in fields:
        port = L.wx_from_utf8(_string_value(raw), major=major)
        if port != kicad[name]:
            raise RuntimeError(
                f"the lexer port disagrees with kicad-cli {major} on {raw!r}:"
                f" port {port!r}, kicad-cli {kicad[name]!r}"
            )
    tree = _cst.parse(data)
    wrong = []
    for name, raw, _ in fields:
        try:
            cst = L.wx_from_utf8(
                _field_atom(tree, name).text.encode("utf-8", "surrogateescape"), major=major
            )
        except ValueError:
            cst = "(a string KiCad holds per platform)"
        if cst != kicad[name]:
            wrong.append(f"{raw!r}: the CST gives {cst!r}, kicad-cli reads {kicad[name]!r}")
    assert not wrong, f"kicad-cli {major}:\n" + "\n".join(wrong)


def _set_text_against_kicad_cli(values: list[str], placeholder: bytes) -> None:
    """One schematic whose property values set_text wrote, starting from ``placeholder``,
    every other one first on its line: kicad-cli loads it and reads every value back, or
    set_text refused that value and left its atom alone."""
    fields = [(f"F{i}", placeholder, i % 2 == 1) for i in range(len(values))]
    tree = _cst.parse(_schematic_with_fields(fields))
    written = []
    for (name, _, _), value in zip(fields, values):
        atom = _field_atom(tree, name)
        raw = atom.raw
        try:
            atom.set_text(value)
        except ValueError:
            assert atom.raw == raw, "a refused set_text changed the atom"
            continue
        written.append((name, value, atom.raw))
    got = _kicad_reads(_cst.serialize(tree))
    assert got is not None, "kicad-cli cannot load what set_text wrote: " + ", ".join(
        repr(raw) for _, _, raw in written
    )
    wrong = [
        f"{raw!r}: set_text was given {value!r}, kicad-cli reads {got.get(('R1', name), '')!r}"
        for name, value, raw in written
        if got.get(("R1", name), "") != value
    ]
    assert not wrong, "\n".join(wrong)


@pytest.mark.nightly
@requires_cli
@_NIGHTLY
@given(_batch(G.agreed_escape_body().filter(_body_prints_verbatim)))
def test_nightly_escapes_match_kicad_cli(bodies):
    """Batches of strings with the escapes both readers know: the lexer port reads what
    kicad-cli reads, and so does the CST."""
    _escapes_against_kicad_cli(bodies)


@pytest.mark.nightly
@requires_cli
@_xfail(_BUG29)
@_NIGHTLY
@given(_batch(G.kicad_only_escape_body().filter(_body_prints_verbatim)))
@example([b"A\\x42C", b"A\\102C", b"A\\xgC", b"x\\ay", b"\\x4"])
def test_nightly_kicad_only_escapes_match_kicad_cli(bodies):
    """The same for the escapes only KiCad's lexer decodes."""
    _escapes_against_kicad_cli(bodies)


@pytest.mark.nightly
@requires_cli
@_NIGHTLY
@given(_batch(G.kicad_text.filter(_prints_verbatim)))
def test_nightly_kicad_cli_reads_back_set_text(values):
    """Batches of values without a NUL, written by set_text into quoted property values,
    read back exactly."""
    _set_text_against_kicad_cli(values, b'"placeholder"')


#: A bare value kicad-cli can witness: printed unchanged, and not a number, which the
#: schematic parser refuses as a property value (module docstring).
_BARE_VALUE = _ANY_TEXT.filter(
    lambda value: _prints_verbatim(value) and not L.is_number(value.encode("utf-8"))
)


@pytest.mark.nightly
@requires_cli
@_xfail(_BUG59)
@_NIGHTLY
@given(_batch(_BARE_VALUE))
@example(["plain", "a|b"])
@example(["plain", "#x"])
@example(["a\x00b"])
def test_nightly_kicad_cli_reads_back_bare_set_text(values):
    """The same for values written into bare property values."""
    _set_text_against_kicad_cli(values, b"placeholder")
