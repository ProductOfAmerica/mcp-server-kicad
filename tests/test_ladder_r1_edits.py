"""Rung 1: an edit through the CST changes only its own tokens.

The judge is never the CST. KiCad's lexer, ported in tests/kicad_ref/dsn_lexer.py, reads
the bytes before and after each edit, and the same operation applied to its reading of
the before bytes is the model the after bytes must read as: an item inserted at the
index the call names, an item deleted, or one atom in place of the retexted one. The
byte diff is judged on its own by tests/edit_judge.py (single_span and token boundaries):
one contiguous span that starts and ends on a token boundary. Documents come from the
KiCad-written dialect in tests/sexpr_gen.py, where the CST's child indices and the port's
items correspond one to one, so the CST and the model are addressed with the same paths.

Copies and refused edits are judged by arithmetic: a copy serializes to the same bytes
and shares nothing with its original, and an edit that raises changes nothing.
"""

from __future__ import annotations

import re
import sys
from typing import Any

import edit_judge as J
import pytest
import sexpr_gen as G
from hypothesis import assume, example, given
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import eda_units as U

from mcp_server_kicad import _cst

# ---------------------------------------------------------------------------
# the model: KiCad's reading of bytes, as offset-free nested data
# ---------------------------------------------------------------------------

#: What stands in for the retexted atom while the rest of a reading is compared.
_HOLE = ("HOLE",)


def _shape(item, data: bytes):
    """One port item without offsets: a token as (kind, value, bytes), a list as
    ["(", [children]] (a Python list, so the model can be edited in place)."""
    if item.kind == L.GROUP:
        return ["(", [_shape(child, data) for child in item.items]]
    return (item.kind, item.value, data[item.start : item.end])


def _premise(data: bytes) -> list:
    """The port's items for bytes the test generated; failing to lex breaks the premise."""
    try:
        return L.tree(data, knows_bar=True)
    except L.LexError as exc:
        raise J.JudgeError(f"the generated input does not lex: {exc}") from exc


def _result(data: bytes) -> list:
    """The port's items for the edited bytes; failing to lex is the edit's fault."""
    try:
        return L.tree(data, knows_bar=True)
    except L.LexError as exc:
        raise AssertionError(f"KiCad's lexer cannot read the edited bytes: {exc}") from exc


def _model(items: list, data: bytes) -> list:
    return [_shape(item, data) for item in items]


def _model_children(model: list, path: tuple) -> list:
    """The children of the list at ``path`` (child indices) in a model."""
    for index in path:
        model = model[index][1]
    return model


def _port_at(items: list, path: tuple) -> Any:
    """The port item at ``path`` (child indices from the document's top level)."""
    item = None
    for index in path:
        item = items[index]
        items = item.items if item.kind == L.GROUP else []
    return item


def _take(model: list, path: tuple):
    """Put _HOLE where ``path`` points in ``model`` and return what was there, or None
    when the model has no item at that path."""
    items = model
    for depth, index in enumerate(path):
        if not isinstance(items, list) or not 0 <= index < len(items):
            return None
        if depth == len(path) - 1:
            found, items[index] = items[index], _HOLE
            return found
        node = items[index]
        if not (isinstance(node, list) and node[0] == "("):
            return None
        items = node[1]
    return None


def _assert_one_span(small: bytes, big: bytes) -> None:
    """``big`` is ``small`` with one contiguous span added, and some placement of that
    span starts and ends on token boundaries of both sides (edit_judge's byte checks,
    which read KiCad's lexer, not the CST)."""
    span = J.single_span(small, big)
    assert span is not None, "the bytes changed in more than one place"
    lo, hi = span
    grown = len(big) - len(small)
    small_cuts = J.boundaries(small, knows_bar=True)
    big_cuts = J.boundaries(big, knows_bar=True)
    assert any(
        k in small_cuts and k in big_cuts and k + grown in big_cuts for k in range(lo, hi + 1)
    ), f"the changed span ({lo}..{hi}, {grown} bytes) does not sit on token boundaries"


# ---------------------------------------------------------------------------
# reaching into the CST by path
# ---------------------------------------------------------------------------


def _cst_at(tree, path: tuple) -> Any:
    node = tree
    for index in path:
        node = node.children[index]
    return node


def _cst_nodes(tree) -> list:
    """(path, node) for every node below the document root."""
    out = []
    pending: list[tuple[tuple[int, ...], Any]] = [((), tree)]
    while pending:
        path, node = pending.pop()
        for index, child in enumerate(node.children):
            out.append((path + (index,), child))
            if child.children is not None:
                pending.append((path + (index,), child))
    return out


def _fresh(data: bytes):
    """(node, model, bytes) of the single item a generated item parses to."""
    doc = _cst.parse(data)
    items = _premise(data)
    if len(doc.children) != 1 or len(items) != 1:
        raise J.JudgeError(f"a generated item must be exactly one item: {data!r}")
    return doc.children[0], _shape(items[0], data), data[items[0].start : items[0].end]


def _port_children(items: list, path: tuple) -> tuple[list, int]:
    """(children, offset just past the opening paren) of the list at ``path``; the top
    level counts as a list opened at offset 0."""
    if not path:
        return items, 0
    group = _port_at(items, path)
    return group.items, group.start + 1


def _assert_put(before: bytes, after: bytes, at: int, head: bytes, run, tail: bytes) -> None:
    """``after`` is ``before`` with ``head + run + tail`` put in at offset ``at``; a
    ``run`` of None stands for some non-empty run of whitespace."""
    rest = len(before) - at
    assert (
        len(after) >= len(before)
        and after[:at] == before[:at]
        and after[len(after) - rest :] == before[at:]
    ), f"bytes outside the insertion point at {at} moved"
    put = after[at : len(after) - rest]
    if run is not None:
        assert put == head + run + tail, f"put in {put!r}, expected {head + run + tail!r}"
        return
    middle = put[len(head) : len(put) - len(tail)]
    assert put.startswith(head) and put.endswith(tail) and len(put) > len(head + tail), (
        f"put in {put!r}, expected {head!r} and {tail!r} around a separator"
    )
    assert not middle.strip(b" \t\r\n"), f"the separator {middle!r} is not whitespace"


_OPS = ("insert_after", "insert_before", "append_child", "remove_child")


@given(doc=G.kicad_doc(), data=st.data())
def test_structural_edit_changes_only_its_tokens(doc, data):
    """insert_after, insert_before, append_child and remove_child, with a copy of an
    item of the document or a fresh one, and no separator or an explicit whitespace run
    (what every production call passes): KiCad's lexer reads the result as the model
    edit of its reading of the input, and the bytes changed in one span on token
    boundaries.

    The span is pinned down further by the documented whitespace contract ("sep=reuse",
    docs/adr-cst-substrate.md, Bytes-only I/O; the docstrings of append_child, with its
    b"\\n" default, and remove_child). An inserted span is the node plus one whitespace
    run: the separator given, else the anchor's leading whitespace, else (an anchor with
    none) some whitespace. insert_after and append_child put the run before the node, so
    it becomes the node's leading whitespace; insert_before puts the node where the
    anchor started and the run between them. A removed child takes its leading
    whitespace with it."""
    before_items = _premise(doc)
    tree = _cst.parse(doc)
    nodes = _cst_nodes(tree)
    lists = [((), tree)] + [(path, node) for path, node in nodes if node.kind == "list"]
    op = data.draw(st.sampled_from(_OPS), label="op")
    if op == "append_child":
        parent_path, parent = data.draw(st.sampled_from(lists), label="parent")
    else:
        parent_path, parent = data.draw(
            st.sampled_from([(p, n) for p, n in lists if n.children]), label="parent"
        )
        ref_index = data.draw(st.integers(0, len(parent.children) - 1), label="ref index")
    if op != "remove_child":
        if data.draw(st.booleans(), label="copy an item of the document"):
            source_path, source = data.draw(st.sampled_from(nodes), label="copied item")
            node = source.copy()
            item = _port_at(before_items, source_path)
            node_model, node_bytes = _shape(item, doc), doc[item.start : item.end]
        else:
            node, node_model, node_bytes = _fresh(data.draw(G.kicad_item(), label="fresh item"))
        sep = data.draw(st.one_of(st.none(), G.whitespace), label="sep")

    expected = _model(before_items, doc)
    siblings = _model_children(expected, parent_path)
    if op == "insert_after":
        parent.insert_after(parent.children[ref_index], node, sep)
        siblings.insert(ref_index + 1, node_model)
    elif op == "insert_before":
        parent.insert_before(parent.children[ref_index], node, sep)
        siblings.insert(ref_index, node_model)
    elif op == "append_child":
        if sep is None:
            parent.append_child(node)
        else:
            parent.append_child(node, sep)
        siblings.append(node_model)
    else:
        parent.remove_child(parent.children[ref_index])
        del siblings[ref_index]
    after = _cst.serialize(tree)

    assert _model(_result(after), after) == expected, (
        f"{op} on the list at {parent_path}: KiCad's lexer reads more than that edit in {after!r}"
    )
    if op == "remove_child":
        _assert_one_span(after, doc)
    else:
        _assert_one_span(doc, after)

    children, opened = _port_children(before_items, parent_path)
    if op == "append_child":
        at = children[-1].end if children else opened
        _assert_put(doc, after, at, b"", b"\n" if sep is None else sep, node_bytes)
        return
    ref = children[ref_index]
    lead_start = children[ref_index - 1].end if ref_index else opened
    if op == "remove_child":
        assert after == doc[:lead_start] + doc[ref.end :], (
            "remove_child did not take exactly the child and its leading whitespace"
        )
        return
    run = sep if sep is not None else (doc[lead_start : ref.start] or None)
    if op == "insert_after":
        _assert_put(doc, after, ref.end, b"", run, node_bytes)
    else:
        _assert_put(doc, after, ref.start, node_bytes, run, b"")


# ---------------------------------------------------------------------------
# set_text
# ---------------------------------------------------------------------------

#: Characters KiCad's writer escapes (LF, CR, backslash, quote: Quotes,
#: common/richio.cpp:509-545 at 9.0.8) or its lexer reads specially (whitespace, NUL,
#: the parens, the bar, '#' first on a line: common/dsnlexer.cpp). Plain generated text
#: meets them too rarely (in 200 derandomized draws next to a document, no LF and a
#: quote 8 times, 96 of the values empty; measured 2026-10-10), so half the values are
#: built around one.
_SPECIAL = '\n\r"\\\t\x00 |#()'
_ANY_CHAR = st.characters(exclude_categories=("Cs",))
#: Any text a str can hold except a lone surrogate, which UTF-8 cannot encode.
_TEXT = st.one_of(
    st.text(_ANY_CHAR),
    st.builds(
        lambda head, special, tail: head + special + tail,
        st.text(_ANY_CHAR, max_size=6),
        st.sampled_from(_SPECIAL),
        st.text(st.one_of(st.sampled_from(_SPECIAL), _ANY_CHAR), max_size=6),
    ),
)


def _check_set_text(doc: bytes, path: tuple, value: str, *, quoted: bool) -> None:
    """set_text(value) on the atom at ``path`` changes that atom's bytes and nothing
    else, and KiCad's lexer reads exactly one atom there whose decoded bytes are the
    UTF-8 of ``value``. A value holding a NUL is the one value refused, and a refusal
    changes nothing (no escape carries a NUL to KiCad; the codec rung judges why)."""
    before_items = _premise(doc)
    target = _port_at(before_items, path)
    if target is None or target.kind not in L.ATOM_KINDS or (target.kind == L.STRING) != quoted:
        raise J.JudgeError(f"the generated target at {path} is not the atom kind asked for")
    tree = _cst.parse(doc)
    try:
        _cst_at(tree, path).set_text(value)
    except ValueError:
        assert "\x00" in value, f"set_text refused {value!r}, which holds no NUL"
        assert _cst.serialize(tree) == doc, "a refused set_text changed the document"
        return
    after = _cst.serialize(tree)

    end = len(after) - (len(doc) - target.end)
    assert (
        end >= target.start
        and after[: target.start] == doc[: target.start]
        and after[end:] == doc[target.end :]
    ), "bytes outside the retexted atom changed"

    expected = _model(before_items, doc)
    _take(expected, path)
    got = _model(_result(after), after)
    found = _take(got, path)
    assert found is not None and got == expected, (
        f"set_text({value!r}) on the atom at {path} wrote {after[target.start : end]!r},"
        f" which KiCad's lexer does not read as one atom in its place"
    )
    assert isinstance(found, tuple) and found[0] in L.ATOM_KINDS, found
    if quoted:
        assert found[0] == L.STRING, f"a quoted atom came back {found[0]}"
    assert found[1] == value.encode("utf-8"), (
        f"set_text({value!r}): KiCad's lexer reads {found[1]!r}"
    )
    assert found[2] == after[target.start : end]


@given(case=G.doc_with_target("quoted"), value=_TEXT)
def test_quoted_set_text_changes_only_its_token(case, value):
    """Retexting a quoted atom with any text: one STRING token whose bytes, as KiCad's
    lexer decodes them, are the text's UTF-8, in the atom's place, and every other byte
    where it was."""
    doc, path = case
    _check_set_text(doc, path, value, quoted=True)


@example(case=(b"(a\n\tb)", (0, 1)), value="#x")
@example(case=(b"(a b)", (0, 1)), value="a|b")
@example(case=(b"(a b)", (0, 1)), value="a\x00b")
@given(
    case=st.one_of(
        G.doc_with_target("bare", line_start=True), G.doc_with_target("bare", line_start=False)
    ),
    value=_TEXT,
)
def test_bare_set_text_changes_only_its_token(case, value):
    """The same for a bare atom, first on its line or after another token. KiCad's lexer
    splits a bare run at '|' (isSep, common/dsnlexer.cpp:480-483 at 9.0.8, 476-479 at
    10.0.7, with the bar known as it is in every current format) and at NUL (isSpace),
    and skips a line whose first non-blank byte is '#' (575-599 at both tags), which can
    take the rest of the list with it."""
    doc, path = case
    _check_set_text(doc, path, value, quoted=False)


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------

_SURROGATE_TEXT = st.builds(
    lambda head, surrogate, tail: head + surrogate + tail,
    st.text(max_size=4),
    st.characters(categories=("Cs",)),
    st.text(max_size=4),
)


@given(doc=G.kicad_doc(), data=st.data())
def test_a_refused_edit_changes_no_byte(doc, data):
    """Inserting relative to a node that is not a child of the list, removing such a
    node, and retexting with a lone surrogate all raise, and the document's bytes are
    what they were. A byte-identical copy of a child is not that child."""
    tree = _cst.parse(doc)
    nodes = [node for _, node in _cst_nodes(tree)]
    atoms = [node for node in nodes if node.kind == "atom"]
    ops = ["insert_after", "insert_before", "remove_child"] + (["set_text"] if atoms else [])
    op = data.draw(st.sampled_from(ops), label="op")
    if op == "set_text":
        atom = data.draw(st.sampled_from(atoms), label="atom")
        raw = atom.raw
        with pytest.raises(UnicodeEncodeError):
            atom.set_text(data.draw(_SURROGATE_TEXT, label="value"))
        assert atom.raw == raw
    else:
        parent = data.draw(
            st.sampled_from([tree] + [node for node in nodes if node.kind == "list"]),
            label="parent",
        )
        own = {id(child) for child in parent.children}
        fresh, _, _ = _fresh(data.draw(G.kicad_item(), label="fresh item"))
        strangers = [node for node in nodes if id(node) not in own]
        strangers += [child.copy() for child in parent.children] + [parent, tree, fresh]
        ref = data.draw(st.sampled_from(strangers), label="stranger")
        if op == "remove_child":
            with pytest.raises(ValueError):
                parent.remove_child(ref)
        else:
            node = data.draw(st.sampled_from(nodes + [fresh]), label="node")
            sep = data.draw(st.one_of(st.none(), G.whitespace), label="sep")
            with pytest.raises(ValueError):
                getattr(parent, op)(ref, node, sep)
    assert _cst.serialize(tree) == doc


# ---------------------------------------------------------------------------
# where an inserted node goes
# ---------------------------------------------------------------------------


def _port_lists(items: list) -> list:
    """(path, children, offset just past the opening paren) for the document's top level
    and for every list in it."""
    out: list[tuple[tuple[int, ...], list, int]] = [((), items, 0)]
    pending: list[tuple[tuple[int, ...], list]] = [((), items)]
    while pending:
        path, children = pending.pop()
        for index, item in enumerate(children):
            if item.kind == L.GROUP:
                out.append((path + (index,), item.items, item.start + 1))
                pending.append((path + (index,), item.items))
    return out


def _leading(data: bytes, children: list, index: int, opened: int) -> bytes:
    """The whitespace before child ``index``: from the end of the item before it (or the
    opening paren) to its first byte."""
    start = children[index - 1].end if index else opened
    return data[start : children[index].start]


@given(doc=G.kicad_doc(), data=st.data())
def test_inserted_node_takes_its_anchors_whitespace(doc, data):
    """With no separator given, a node inserted next to an anchor whose leading
    whitespace is not empty is preceded by that same whitespace, and an anchor the node
    went in front of keeps it too: "Inserted spans copy the EOL/indent of their anchor
    sibling" (docs/adr-cst-substrate.md, Bytes-only I/O)."""
    before_items = _premise(doc)
    anchors = [
        (path, index, ws)
        for path, children, opened in _port_lists(before_items)
        for index in range(len(children))
        if (ws := _leading(doc, children, index, opened))
    ]
    assume(anchors)
    parent_path, index, ws = data.draw(st.sampled_from(anchors), label="anchor")
    op = data.draw(st.sampled_from(("insert_after", "insert_before")), label="op")
    tree = _cst.parse(doc)
    if data.draw(st.booleans(), label="copy an item of the document"):
        source_path, source = data.draw(st.sampled_from(_cst_nodes(tree)), label="copied item")
        node, node_model = source.copy(), _shape(_port_at(before_items, source_path), doc)
    else:
        node, node_model, _ = _fresh(data.draw(G.kicad_item(), label="fresh item"))
    parent = _cst_at(tree, parent_path)
    getattr(parent, op)(parent.children[index], node)
    after = _cst.serialize(tree)

    children, opened = _port_children(_result(after), parent_path)
    new = index + 1 if op == "insert_after" else index
    assert _shape(children[new], after) == node_model, "the node is not where the call put it"
    assert _leading(after, children, new, opened) == ws, (
        f"{op}: the node is preceded by {_leading(after, children, new, opened)!r},"
        f" its anchor by {ws!r}"
    )
    if op == "insert_before":
        assert _leading(after, children, index + 1, opened) == ws, "the anchor lost its whitespace"


#: A comment line as KiCad's lexer skips it: its first non-blank byte is '#'.
_COMMENT_LINE = re.compile(rb"(?m)^[ \t\r\x00]*#")


def _comment_lines(tree) -> int:
    """How many comment lines the separators of a tree hold."""
    total = 0
    pending = [tree]
    while pending:
        node = pending.pop()
        total += len(_COMMENT_LINE.findall(node.sep))
        if node.children is not None:
            total += len(_COMMENT_LINE.findall(node.close_sep))
            pending.extend(node.children)
    return total


@given(doc=G.comment_line_doc(), crlf=st.booleans(), data=st.data())
def test_an_insertion_beside_a_commented_node_leaves_one_copy_of_the_comment(doc, crlf, data):
    """A comment line is whitespace to KiCad's lexer, and lies in the separator of the node
    after it. With no separator given, a node inserted next to such an anchor takes the
    anchor's own line break and indentation (from its last line end, a CR before that
    included), never the comment line: the document still holds exactly the comment lines it
    held, the anchor keeps (insert_after) or hands over (insert_before) its whole separator,
    and KiCad's lexer reads the result as the document with the one new item inserted.
    LF and CRLF line ends."""
    if crlf:
        doc = doc.replace(b"\n", b"\r\n")
    _premise(doc)
    tree = _cst.parse(doc)
    commented = [(path, node) for path, node in _cst_nodes(tree) if _COMMENT_LINE.search(node.sep)]
    assume(commented)
    path, anchor = data.draw(st.sampled_from(commented), label="commented node")
    parent = _cst_at(tree, path[:-1])
    ops = ["insert_after"]
    if path[-1] != 0 or len(path) == 1:  # a new first item would change the list's name
        ops.append("insert_before")
    op = data.draw(st.sampled_from(ops), label="op")
    item = data.draw(G.kicad_item(), label="fresh item")
    node, _, _ = _fresh(item)
    comments, old_sep = _comment_lines(tree), anchor.sep
    own_line = re.search(rb"\r?\n[^\n]*\Z", old_sep)
    assert own_line is not None
    getattr(parent, op)(anchor, node)
    after = _cst.serialize(tree)

    assert _comment_lines(_cst.parse(after)) == comments, f"{op} copied or dropped a comment"
    if op == "insert_after":
        assert (anchor.sep, node.sep) == (old_sep, own_line.group())
    else:
        assert (node.sep, anchor.sep) == (old_sep, own_line.group())
    label = next(J.walk(item, knows_bar=True))[1].label
    J.assert_inserted(doc, after, heads=[label], knows_bar=True)


def test_removing_a_node_takes_the_comment_line_above_it():
    """remove_child takes the child with all of its leading whitespace, and a comment line
    above the child is part of that whitespace: the result is the document less one span
    from the end of the item before the child to the end of the child, which KiCad's lexer
    reads as that one item removed."""
    before = b"(r\n\t(a)\n\t# a note (with a paren\n\t(b 1)\n\t(d)\n)\n"
    tree = _cst.parse(before)
    root = tree.lists[0]
    root.remove_child(root.find("b"))
    after = _cst.serialize(tree)
    assert after == b"(r\n\t(a)\n\t(d)\n)\n"
    J.assert_removed(before, after, heads=["b"], knows_bar=True)


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------


def _assert_twin(original, twin) -> None:
    """``twin`` has the original's kinds, bytes and shape, and shares no node and no
    children list with it."""
    pending = [(original, twin)]
    while pending:
        a, b = pending.pop()
        assert b is not a, "the copy shares a node with its original"
        assert type(b) is type(a) and b.kind == a.kind, (type(a), type(b))
        assert (b.sep, b.raw) == (a.sep, a.raw)
        if a.children is None:
            assert b.children is None
            continue
        assert b.children is not a.children, "the copy shares a children list"
        assert len(b.children) == len(a.children) and b.close_sep == a.close_sep
        pending.extend(zip(a.children, b.children))


def _edit_everything(root) -> None:
    """Retext every atom and grow every list under ``root``, separators included."""
    every = [root]
    if root.children is not None:
        every += [node for _, node in _cst_nodes(root)]
    for node in every:
        node.sep += b" "
        if node.children is None:
            node.set_text("x" + node.text)
        else:
            node.close_sep += b" "
            node.append_child(_cst.Atom(b"edited"), b" ")


@given(doc=G.kicad_doc())
def test_copy_is_an_independent_byte_identical_tree(doc):
    """For every node, the document root included: the copy serializes to the node's
    bytes with the node's kinds all the way down, and editing everything in the copy
    leaves the node's bytes, and the document's, as they were."""
    tree = _cst.parse(doc)
    for node in [tree] + [node for _, node in _cst_nodes(tree)]:
        original = _cst.serialize(node)
        twin = node.copy()
        _assert_twin(node, twin)
        assert _cst.serialize(twin) == original
        _edit_everything(twin)
        assert _cst.serialize(twin) != original, "the edits to the copy did not take"
        assert _cst.serialize(node) == original, "editing the copy changed the original"
    assert _cst.serialize(tree) == doc


def test_copy_accepts_every_tree_parse_accepts():
    """parse and serialize walk the tree with an explicit stack, so they take any depth;
    copy must take every tree they do. Nested 100 levels past the interpreter's
    recursion limit (copy raised RecursionError at depth 1100 under the default limit of
    1000 on CPython 3.10 to 3.14, measured 2026-10-10)."""
    depth = sys.getrecursionlimit() + 100
    data = b"(" * depth + b")" * depth
    tree = _cst.parse(data)
    if _cst.serialize(tree) != data:
        raise RuntimeError("parse and serialize no longer take this tree; the premise broke")
    try:
        twin = tree.copy()
    except RecursionError:
        twin = None
    assert twin is not None, f"copy() raised RecursionError on a tree {depth} lists deep"
    assert _cst.serialize(twin) == data


# ---------------------------------------------------------------------------
# _fill_at and the accessors
# ---------------------------------------------------------------------------

#: A coordinate as KiCad writes one: FormatInternalUnits of an int32 at the schematic or
#: the board scale (tests/kicad_ref/eda_units.py).
_COORD = st.builds(
    lambda value, scale: U.format_internal_units(value, scale).encode(),
    st.integers(U.INT_MIN, U.INT_MAX),
    st.sampled_from((U.SCH_IU_PER_MM, U.PCB_IU_PER_MM)),
)
#: An angle as KiCad writes one: FormatAngle prints the degrees with {:.10g}
#: (common/eda_units.cpp:162-167 at 9.0.8, 186-191 at 10.0.7).
_ANGLE = st.integers(-3600, 3600).map(lambda tenths: format(tenths / 10, ".10g").encode())
#: Any token KiCad's lexer reads as a NUMBER (isNumber, common/dsnlexer.cpp:495-542 at
#: 9.0.8, 491-538 at 10.0.7): a sign, digits around an optional point, an exponent.
_NUMBER_TOKEN = st.from_regex(
    rb"[-+]?(?:[0-9]{1,6}\.?[0-9]{0,6}|\.[0-9]{1,6})(?:[eE][-+]?[0-9]{1,2})?", fullmatch=True
).filter(L.is_number)
_NUMBER = st.one_of(_COORD, _NUMBER_TOKEN)
_HOLDERS = (b"symbol", b"property", b"label", b"text", b"pad", b"footprint", b"junction")


def _at_node(numbers):
    """(at x y) or (at x y angle) spaced as KiCad's writers space it ("(at %s %s %s)",
    eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp:1303 at 9.0.8, 1464 at 10.0.7)."""
    return st.builds(
        lambda x, y, angle: b"(at " + b" ".join([x, y] + ([angle] if angle else [])) + b")",
        numbers,
        numbers,
        st.one_of(st.none(), _ANGLE),
    )


def _is_at_list(item: bytes) -> bool:
    toks = L.tokens(item, knows_bar=True)
    return len(toks) > 1 and toks[0].kind == L.LEFT and toks[1].value == b"at"


@st.composite
def _holder_doc(draw, numbers=_COORD):
    """(document, holder path, index of the holder's first (at ...) child): a list headed
    like one KiCad places, whose (at ...) comes after children that are not (at ...)
    and may be followed by more of them, among sibling items of a top-level list."""
    head = draw(st.sampled_from(_HOLDERS))
    pre = draw(st.lists(G.kicad_item().filter(lambda item: not _is_at_list(item)), max_size=3))
    post = draw(st.lists(st.one_of(G.kicad_item(), _at_node(numbers)), max_size=3))
    children = [head, *pre, draw(_at_node(numbers)), *post]
    holder = (
        b"("
        + head
        + b"".join(draw(G.whitespace) + child for child in children[1:])
        + draw(st.one_of(st.just(b""), G.whitespace))
        + b")"
    )
    before = draw(st.lists(G.kicad_item(), max_size=2))
    after = draw(st.lists(G.kicad_item(), max_size=2))
    doc = (
        b"(root"
        + b"".join(draw(G.whitespace) + item for item in [*before, holder, *after])
        + b"\n)\n"
    )
    return doc, (0, 1 + len(before)), 1 + len(pre)


def _gaps(data: bytes, group) -> list[bytes]:
    """The whitespace runs of a list: after its '(', between its items, before its ')'."""
    edges = [group.start + 1]
    for item in group.items:
        edges += [item.start, item.end]
    edges.append(group.end - 1)
    return [data[edges[k] : edges[k + 1]] for k in range(0, len(edges), 2)]


_FINITE = st.floats(allow_nan=False, allow_infinity=False)


@given(case=_holder_doc(), x=_FINITE, y=_FINITE, angle=st.one_of(st.none(), _FINITE))
def test_fill_at_rewrites_only_its_at_node(case, x, y, angle):
    """_fill_at(node, x, y, angle) changes the node's first (at ...) and nothing else.
    KiCad reads (at x y [angle]) as two or three NUMBER tokens (parseDouble's NeedNUMBER,
    include/dsnlexer.h:531-535 at 9.0.8, 536-540 at 10.0.7; the optional angle,
    pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp:4533-4549 at 9.0.8,
    5227-5243 at 10.0.7), so: x and y read back as the floats given; with an angle the
    node holds exactly one, replaced or appended after a single space as KiCad writes it;
    without one the atom count stays; the whitespace already there stays; and doing it
    twice is doing it once. Without an angle the call takes the three-argument form most
    callers use, so the default is what is tested."""
    doc, holder_path, at_index = case
    args = (x, y) if angle is None else (x, y, angle)
    before_items = _premise(doc)
    holder = _port_at(before_items, holder_path)
    at_before = holder.items[at_index]
    if not (
        at_before.kind == L.GROUP
        and at_before.items[0].value == b"at"
        and len(at_before.items) in (3, 4)
        and all(item.kind == L.NUMBER for item in at_before.items[1:])
    ):
        raise J.JudgeError(f"the generated (at ...) is not one KiCad writes: {at_before}")
    tree = _cst.parse(doc)
    _cst._fill_at(_cst_at(tree, holder_path), *args)
    after = _cst.serialize(tree)

    location = ((0, "root"), (holder_path[1], holder.items[0].value.decode()), (at_index, "at"))
    if after != doc:  # unchanged only when the values were already written
        J.assert_confined(doc, after, location, knows_bar=True)
    at_after = _port_at(_result(after), holder_path + (at_index,))
    toks = at_after.items
    assert all(tok.kind != L.GROUP for tok in toks), "a list appeared inside (at ...)"
    assert len(toks) == (4 if angle is not None else len(at_before.items)), toks
    assert toks[0].value == b"at"
    for tok, want in zip(toks[1:], args):
        assert tok.kind == L.NUMBER and float(tok.value) == want, (tok, want)
    if angle is None and len(toks) == 4:
        old = at_before.items[3]
        assert after[toks[3].start : toks[3].end] == doc[old.start : old.end], "the angle moved"
    gaps = _gaps(doc, at_before)
    if len(toks) > len(at_before.items):
        gaps = gaps[:-1] + [b" "] + gaps[-1:]
    assert _gaps(after, at_after) == gaps, "whitespace inside (at ...) changed"

    _cst._fill_at(_cst_at(tree, holder_path), *args)
    assert _cst.serialize(tree) == after, "a second identical _fill_at changed the bytes"


@given(case=_holder_doc(numbers=_NUMBER), doc=G.kicad_doc())
def test_node_accessors_read_kicads_tokens(case, doc):
    """_node_xy is the floats of the x and y tokens of the node's first (at ...), and
    _node_text the decoded second atom of a list, both as KiCad's lexer reads them."""
    holder_doc, holder_path, at_index = case
    at = _port_at(_premise(holder_doc), holder_path + (at_index,))
    node = _cst_at(_cst.parse(holder_doc), holder_path)
    assert _cst._node_xy(node) == (float(at.items[1].value), float(at.items[2].value))

    items = _premise(doc)
    tree = _cst.parse(doc)
    for path, node in _cst_nodes(tree):
        if node.kind != "list":
            continue
        atoms = [item for item in _port_at(items, path).items if item.kind in L.ATOM_KINDS]
        if len(atoms) >= 2:
            assert _cst._node_text(node) == atoms[1].value.decode("utf-8"), path
