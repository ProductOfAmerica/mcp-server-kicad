"""Rung 1, tokens: the CST reads bytes into the tokens KiCad's lexer reads.

Judges: math (a parse that succeeds serializes back to its input), and the DSNLEXER
port in tests/kicad_ref/dsn_lexer.py, which answers to kicad-cli in
tests/test_ladder_r1_judges.py. A handful of files go to kicad-cli itself, through the
netlist's field export (conftest.netlist_fields), so KiCad judges the hand-edited
shapes directly.

The CST and KiCad's lexer agree on everything KiCad's writers produce, and on input KiCad
never writes (decision D3 of the rung-1 plan):

- A quoted string that KiCad refuses as unterminated (a raw line end inside the quotes,
  or a quote still open at the end of the input) is refused by the CST too: the
  unterminated-string error at common/dsnlexer.cpp:730 at 9.0.8, 733 at 10.0.7.
- Token boundaries. A line whose first non-blank byte is '#' is a comment to KiCad
  (dsnlexer.cpp:575-598 at 9.0.8); a quote glued to a bare token is part of that token,
  because a bare token runs to the next separator (805-824); NUL is a separator and FF
  and VT are not (isSpace, 452-470).

Bars are not a difference: the CST keeps a run of bytes holding '|' as one atom where
KiCad's lexer, once it knows the bar, splits it into tokens; edit_judge.lexer_view
merges those tokens back (decision D1), and test_glued_bar_runs_are_one_atom pins the
mapping exactly.
"""

from __future__ import annotations

import re
from pathlib import Path

import edit_judge as J
import pytest
import sexpr_gen as G
from conftest import netlist_fields, requires_cli
from hypothesis import event, example, given
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref.richio import quotes

from mcp_server_kicad import _cst

FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_or_fail(data: bytes, what: str):
    """The CST of ``data``, or an AssertionError when the CST refuses it."""
    try:
        return _cst.parse(data)
    except SyntaxError as exc:
        raise AssertionError(f"the CST refuses {what}: {exc}") from exc


def _reading_difference(tree, data: bytes, knows_bar: bool) -> str | None:
    """None when the CST reads ``data`` item for item as KiCad's lexer does; else where
    the two readings part, kept short so a failure never prints whole documents."""
    cst = J.cst_view(tree)
    kicad = J.lexer_view(data, knows_bar=knows_bar)
    if cst == kicad:
        return None
    at = next(
        (i for i, (a, b) in enumerate(zip(cst, kicad)) if a != b),
        min(len(cst), len(kicad)),
    )
    return (
        f"the readings part at event {at} of {len(cst)} (CST) and {len(kicad)} (KiCad):"
        f" CST {cst[at : at + 3]!r}, KiCad's lexer {kicad[at : at + 3]!r}"
    )


def _cst_atoms(node):
    """Every atom under ``node``, in document order."""
    for child in node.children:
        if child.kind == "atom":
            yield child
        else:
            yield from _cst_atoms(child)


def _cst_lists(node):
    """Every list under ``node``, parents before their contents."""
    for child in node.children:
        if child.kind == "list":
            yield child
            yield from _cst_lists(child)


def _lexer_groups(items):
    """Every Group in a dsn_lexer tree, parents before their contents."""
    for item in items:
        if item.kind == L.GROUP:
            yield item
            yield from _lexer_groups(item.items)


# ---------------------------------------------------------------------------
# (a) round trip
# ---------------------------------------------------------------------------

#: Bytes that matter to one reader or the other, so short random runs of them reach
#: every branch of both tokenizers far more often than uniform bytes do.
_NOISE = (
    b"(",
    b")",
    b'"',
    b"\\",
    b" ",
    b"\t",
    b"\n",
    b"\r",
    b"\x00",
    b"\x0b",
    b"\x0c",
    b"|",
    b"#",
    b"a",
    b"1",
    b".",
    b"\xff",
    b"\xce\xa9",  # U+03A9 in UTF-8
)

_any_bytes = st.one_of(
    st.binary(max_size=256),
    st.lists(st.sampled_from(_NOISE), max_size=64).map(b"".join),
    G.kicad_doc(),
    G.comment_line_doc(),
    G.glued_quote_doc(),
    G.whitespace_class_doc(),
    G.bar_doc(),
    G.unterminated_doc(),
    G.paren_mutation(),
)


@given(_any_bytes)
@example(b"")
@example(b'(a "open quote')
@example(b"\xff\xfe(\x00 \x0c|#)\r")
def test_round_trip_any_bytes(data):
    """Whatever the bytes, parse either refuses them with a SyntaxError or gives a tree
    that serializes back to exactly those bytes."""
    try:
        tree = _cst.parse(data)
    except SyntaxError:
        event("refused")
        return
    event("parsed")
    out = _cst.serialize(tree)
    assert type(out) is bytes
    assert out == data


@given(G.kicad_doc())
def test_round_trip_kicad_dialect(doc):
    """Every document KiCad's writers could produce round-trips byte for byte."""
    assert _cst.serialize(_cst.parse(doc)) == doc


def test_parse_takes_bytes_only():
    """CLAUDE.md: CST I/O is bytes-only. A bytearray is read like the same bytes and
    gives bytes back; the tree keeps its own copy, so changing the caller's buffer
    afterwards changes nothing; text is refused rather than encoded behind the
    caller's back, and the refusal says bytes are wanted, so a caller can tell it from
    a TypeError raised by something broken inside parse."""
    data = FIXTURE.read_bytes() + b'(odd "\xff\\B" \x00 |x| #y)\r\n'
    buffer = bytearray(data)
    tree = _cst.parse(buffer)  # type: ignore[arg-type]  # annotated bytes, reads bytearray
    out = _cst.serialize(tree)
    assert type(out) is bytes
    assert out == data
    buffer[:] = bytes(len(buffer))
    assert _cst.serialize(tree) == data
    with pytest.raises(TypeError, match="bytes"):
        _cst.parse(data.decode("latin-1"))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# structure and accessors
# ---------------------------------------------------------------------------


def _lexer_head(items) -> str | None:
    """A list's name in KiCad's grammar: its first item, when that item is an atom."""
    if items and items[0].kind in L.ATOM_KINDS:
        return items[0].value.decode("utf-8", "surrogateescape")
    return None


def _check_accessors(node, items) -> None:
    """``node``'s accessors against the lexer's items of the same list, recursively."""
    kids = node.children
    assert len(kids) == len(items), (len(kids), len(items))
    slot = {id(child): i for i, child in enumerate(kids)}
    atom_slots = [i for i, item in enumerate(items) if item.kind in L.ATOM_KINDS]
    list_slots = [i for i, item in enumerate(items) if item.kind == L.GROUP]
    assert [slot[id(a)] for a in node.atoms] == atom_slots, "atoms"
    assert [slot[id(x)] for x in node.lists] == list_slots, "lists"
    if atom_slots and atom_slots[0] == 0:
        assert node.head == _lexer_head(items), "head"
    elif not atom_slots:
        assert node.head is None, "head of a list without atoms"
    # A list that opens with a list and holds atoms later has no name in KiCad's
    # grammar; kicad_doc() writes no such list, so head is not judged there.
    named: dict[str, list[int]] = {}
    for i in list_slots:
        head = _lexer_head(items[i].items)
        if head is not None:
            named.setdefault(head, []).append(i)
    probes = set(named) | {"no such head"}
    probes |= {items[i].value.decode("utf-8", "surrogateescape") for i in atom_slots}
    for name in sorted(probes):
        want = named.get(name, [])
        assert [slot[id(x)] for x in node.find_all(name)] == want, f"find_all({name!r})"
        found = node.find(name)
        got = None if found is None else slot[id(found)]
        assert got == (want[0] if want else None), f"find({name!r})"
    for i in list_slots:
        _check_accessors(kids[i], items[i].items)


#: Text made mostly of the bytes Quotew escapes or that end a bare token. kicad_doc()
#: draws its strings from all of Unicode, so a run of 200 documents holds an escape in
#: about one of them (measured 2026-10-10); these strings put one in every example.
_ESCAPABLE_TEXT = st.text(st.sampled_from('ab "\\\n\r\t()|#'), min_size=1, max_size=8)


@st.composite
def _dialect_doc_with_strings(draw) -> bytes:
    """kicad_doc() with one to three more strings, each Quotew's output for escapable
    text, placed straight after a list's head as KiCad writes (property "Value" ...)."""
    doc = draw(G.kicad_doc())
    toks = L.tokens(doc, knows_bar=True)
    heads = [t for t, prev in zip(toks[1:], toks) if prev.kind == L.LEFT and t.kind in L.ATOM_KINDS]
    if not heads:
        return doc
    chosen = draw(st.lists(st.sampled_from(heads), min_size=1, max_size=3, unique=True))
    for head in sorted(chosen, key=lambda t: t.end, reverse=True):
        doc = doc[: head.end] + b" " + quotes(draw(_ESCAPABLE_TEXT)) + doc[head.end :]
    return doc


@given(_dialect_doc_with_strings())
def test_structure_and_accessors_match_kicads_lexer(doc):
    """The CST holds the items KiCad's lexer reads, in the same lists and order, and its
    accessors answer from them: ``atoms`` and ``lists`` split a list's children in
    order, ``head`` is the first child's text when that child is an atom and None for a
    list with no atom, ``find_all(name)`` is every child list named ``name`` in order,
    and ``find`` the first of them or None."""
    tree = _cst.parse(doc)
    difference = _reading_difference(tree, doc, True)
    assert difference is None, difference
    _check_accessors(tree, L.tree(doc, knows_bar=True))


_BARE_KINDS = frozenset({L.SYMBOL, L.NUMBER, L.BAR})


def _touching_runs(toks) -> list[list]:
    """Maximal runs of SYMBOL, NUMBER and BAR tokens with no byte between them."""
    runs: list[list] = []
    previous = None
    for tok in toks:
        if tok.kind in _BARE_KINDS:
            if previous is not None and previous.kind in _BARE_KINDS and previous.end == tok.start:
                runs[-1].append(tok)
            else:
                runs.append([tok])
        previous = tok
    return runs


@given(G.bar_doc())
def test_glued_bar_runs_are_one_atom(doc):
    """Decision D1, judged by KiCad's lexer with the bar known: each bare CST atom is
    exactly the bytes of one run of lexer tokens that touch (a|b, |a, a|, ||, and the
    bars around embedded-file data), so nothing a bar separates is lost; and in a
    KiCad-shaped (data |...|) block the base64 tokens KiCad reads between the bars
    (embedded_files.cpp:391-418 at 9.0.8) come back exactly from the CST's atoms once
    the two bars are taken off."""
    toks = L.tokens(doc, knows_bar=True)
    if not any(tok.kind == L.BAR for tok in toks):
        raise RuntimeError(f"premise: bar_doc() wrote no bar in {doc!r}")
    tree = _cst.parse(doc)
    assert _cst.serialize(tree) == doc
    difference = _reading_difference(tree, doc, True)
    assert difference is None, difference
    bare = [atom.raw for atom in _cst_atoms(tree) if atom.raw[:1] != b'"']
    assert bare == [b"".join(doc[t.start : t.end] for t in run) for run in _touching_runs(toks)]
    cst_lists = list(_cst_lists(tree))
    groups = list(_lexer_groups(L.tree(doc, knows_bar=True)))
    assert len(cst_lists) == len(groups)
    for listed, group in zip(cst_lists, groups):
        items = group.items
        if not (
            len(items) >= 4
            and items[0].kind == L.SYMBOL
            and items[0].value == b"data"
            and items[1].kind == L.BAR
            and items[-1].kind == L.BAR
        ):
            continue
        event("data block")
        payload = [doc[t.start : t.end] for t in items[2:-1]]
        raws = [atom.raw for atom in listed.atoms[1:]]
        assert raws and raws[0][:1] == b"|" and raws[-1][-1:] == b"|", raws
        raws[0] = raws[0][1:]
        raws[-1] = raws[-1][:-1]
        assert raws == payload


# ---------------------------------------------------------------------------
# token boundaries on input KiCad's writers never produce
# ---------------------------------------------------------------------------


@given(G.comment_line_doc())
@example(b'(kicad_sch\n\t#(note\n\t(paper "A4")\n)\n')
@example(b'(kicad_sch\n#note\n\t(paper "A4")\n)\n')
@example(b'(symbol\n\t(property "Value" "10K"\n\t\t# (at 1 2)\n\t)\n)\n')
def test_comment_lines_read_as_kicad_reads_them(doc):
    """A line whose first non-blank byte is '#' is skipped by KiCad's lexer
    (dsnlexer.cpp:575-598 at 9.0.8), so the CST must parse the document and read the
    same items."""
    tree = _parse_or_fail(doc, "a document KiCad's lexer reads")
    difference = _reading_difference(tree, doc, True)
    assert difference is None, difference


@given(G.glued_quote_doc())
@example(b'(p ab"cd")')
def test_quote_glued_to_a_token_is_part_of_it(doc):
    """A bare token runs to the next separator, and a double quote is none
    (dsnlexer.cpp:805-824 at 9.0.8), so ab"cd" is one token to KiCad."""
    tree = _parse_or_fail(doc, "a document KiCad's lexer reads")
    difference = _reading_difference(tree, doc, True)
    assert difference is None, difference


@given(G.whitespace_class_doc())
@example(b"(p a\x0cb)")
@example(b"(p a\x0bb)")
@example(b"(p a\x00b)")
def test_whitespace_classes_match_kicads(doc):
    """KiCad's whitespace is space, LF, CR, TAB and NUL, never FF or VT (isSpace,
    dsnlexer.cpp:452-470 at 9.0.8): a NUL splits a token, FF and VT stay inside one."""
    tree = _parse_or_fail(doc, "a document KiCad's lexer reads")
    difference = _reading_difference(tree, doc, True)
    assert difference is None, difference


def _eol(data: bytes) -> bytes:
    """The file's own line end: kicad_native.kicad_sch is CRLF in a Windows checkout."""
    return b"\r\n" if b"\r\n" in data[:200] else b"\n"


def _property(name: bytes, raw_value: bytes) -> bytes:
    return (
        b"(property "
        + name
        + b" "
        + raw_value
        + b" (at 100 100 0) (effects (font (size 1.27 1.27)) (hide yes)))"
    )


def _fixture_with(*, line_before_paper: bytes = b"", r1_lines: tuple[bytes, ...] = ()) -> bytes:
    """kicad_native.kicad_sch with ``line_before_paper`` as a line of its own before
    (paper ...), and ``r1_lines`` as lines of the placed R1 before its Description."""
    data = FIXTURE.read_bytes()
    eol = _eol(data)
    if r1_lines:
        anchor = data.rindex(b'(property "Description"')  # R1's own, after lib_symbols
        data = data[:anchor] + b"".join(line + eol + b"\t\t" for line in r1_lines) + data[anchor:]
    if line_before_paper:
        anchor = data.index(b'\t(paper "A4")')
        data = data[:anchor] + b"\t" + line_before_paper + eol + data[anchor:]
    return data


def _cst_r1_fields(data: bytes) -> dict[str, str]:
    """The fields of the placed R1 that kicad-cli's netlist prints, as the CST reads
    them: every property but Reference and Value (netlist_exporter_xml.cpp,
    addSymbolFields, prints each non-mandatory field and then Footprint, Datasheet and
    Description, at 9.0.8 and 10.0.7)."""
    tree = _parse_or_fail(data, "a schematic kicad-cli loads")
    for symbol in tree.lists[0].find_all("symbol"):
        fields = {p.atoms[1].text: p.atoms[2].text for p in symbol.find_all("property")}
        if fields.get("Reference") == "R1":
            return {k: v for k, v in fields.items() if k not in ("Reference", "Value")}
    raise AssertionError("the CST finds no placed symbol R1")


#: Hand edits KiCad's writer never makes, each loaded by kicad-cli 9.0.8 (measured
#: 2026-10-10: M4 of the rung-1 plan, and the commented-out property with this test):
#: keyword arguments for _fixture_with, plus the field names KiCad must print and must
#: not print, which establish the premise.
_HAND_EDITS = {
    "comment_line_with_a_paren": {"edit": {"line_before_paper": b"#(note"}},
    "commented_out_property": {
        "edit": {"r1_lines": (b"# " + _property(b'"HIDDEN"', b'"x"'),)},
        "absent": ("HIDDEN",),
    },
    "quote_glued_to_a_token": {
        "edit": {"r1_lines": (_property(b'"P"', b'ab"cd"'),)},
        "present": ("P",),
    },
    "form_feed_inside_a_token": {
        "edit": {"r1_lines": (_property(b'"P"', b"a\x0cb"),)},
        "present": ("P",),
    },
}


@requires_cli
@pytest.mark.parametrize(
    "case",
    sorted(_HAND_EDITS),
)
def test_cst_reads_hand_edited_tokens_as_kicad_cli(tmp_path, case):
    """kicad-cli loads each hand-edited schematic; the CST must parse it too and read
    R1's fields exactly as kicad-cli prints them, no field more and none less."""
    spec = _HAND_EDITS[case]
    data = _fixture_with(**spec["edit"])
    path = tmp_path / f"{case}.kicad_sch"
    path.write_bytes(data)
    got = netlist_fields(path)
    if got is None:
        raise RuntimeError(f"premise: kicad-cli must load the {case} schematic")
    kicad = {name: value for (ref, name), value in got.items() if ref == "R1"}
    if any(name not in kicad for name in spec.get("present", ())) or any(
        name in kicad for name in spec.get("absent", ())
    ):
        raise RuntimeError(f"premise: kicad-cli read the {case} edit as never measured: {kicad!r}")
    cst = _cst_r1_fields(data)
    assert cst == kicad, f"R1's fields: the CST reads {cst!r}, kicad-cli {kicad!r}"


# ---------------------------------------------------------------------------
# (c) malformed input
# ---------------------------------------------------------------------------

_STRAY_CLOSE = re.compile(r"unmatched '\)' at byte (\d+)")
_UNCLOSED = re.compile(r"(\d+) unclosed '\('")


@given(G.paren_mutation())
@example(b"(a))")
@example(b")")
@example(b"((a) (b")
@example(b'(a "(") (b')
def test_unbalanced_parens_are_refused_where_kicads_lexer_finds_them(doc):
    """parse refuses exactly the documents whose parens KiCad's lexer finds unbalanced,
    and says where: the offset of the first ')' with nothing open, or how many '(' are
    still open at the end, each equal to the port's."""
    try:
        L.tree(doc, knows_bar=True)
    except L.Unbalanced as exc:
        port = exc
    else:
        port = None
    try:
        tree = _cst.parse(doc)
    except SyntaxError as exc:
        assert port is not None, f"the CST refuses parens KiCad's lexer balances: {exc}"
        message = str(exc)
        if port.unclosed:
            event("unclosed '('")
            found = _UNCLOSED.search(message)
            assert found and int(found.group(1)) == port.unclosed, (message, port.unclosed)
        else:
            event("stray ')'")
            found = _STRAY_CLOSE.search(message)
            assert found and int(found.group(1)) == port.offset, (message, port.offset)
        return
    assert port is None, f"the CST accepts parens KiCad's lexer finds unbalanced: {port}"
    event("balanced")
    assert _cst.serialize(tree) == doc


@given(G.unterminated_doc())
@example(b'(p "A\nB")')
@example(b'(a) "b')
def test_unterminated_strings_are_refused(doc):
    """KiCad's lexer refuses a string that does not close on its own line
    (dsnlexer.cpp:640-733 at 9.0.8), so the CST must refuse the document too."""
    try:
        L.tokens(doc, knows_bar=True)
    except L.Unterminated:
        pass
    else:
        raise RuntimeError(f"premise: KiCad's lexer reads {doc!r} with no unterminated string")
    try:
        _cst.parse(doc)
    except SyntaxError:
        return
    raise AssertionError(
        "the CST accepts a document KiCad's lexer refuses as an unterminated string"
    )


@requires_cli
@pytest.mark.no_kicad_validation  # the schematic KiCad refuses is this test's subject
def test_cst_refuses_what_kicad_cli_refuses_as_unterminated(tmp_path):
    """A property value with a raw line end inside its quotes: kicad-cli refuses the
    schematic (M4), so the CST must refuse it too."""
    eol = _eol(FIXTURE.read_bytes())
    data = _fixture_with(r1_lines=(_property(b'"P"', b'"A' + eol + b'B"'),))
    path = tmp_path / "raw_line_end_in_quotes.kicad_sch"
    path.write_bytes(data)
    if netlist_fields(path) is not None:
        raise RuntimeError("premise: kicad-cli loaded a value with a raw line end in quotes")
    try:
        _cst.parse(data)
    except SyntaxError:
        return
    raise AssertionError("the CST accepts a schematic kicad-cli refuses as unterminated")
