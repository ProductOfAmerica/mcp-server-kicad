"""Judges for write-path tests: what an edit changed, read through KiCad's own lexer.

Every function here lexes bytes with the DSNLEXER port in tests/kicad_ref and imports
nothing else from this repository: the CST under test never judges itself. CST objects
are read duck-typed (``kind``, ``children``, ``raw``, ``text``), never imported.

Vocabulary:

- An *item* is one element of a list as KiCad's lexer reads it: an atom (a STRING,
  NUMBER, SYMBOL or BAR token) or a parenthesised list.
- A list's *head* is its first item's decoded text when that item is an atom, else
  None. An item's *label* is its head (a list) or its decoded text (an atom).
- A *path* locates a list: a tuple of ``(index, head)`` steps from the document's
  top-level items down. An item's *location* is its parent's path plus
  ``(index, label)`` for the item itself.

What the judges assert:

- ``assert_inserted(before, after, heads=...)``: ``after`` is ``before`` with whole
  items inserted and every other byte where it was. A no-op fails; so does an insertion
  that splits a token, lands inside a string, or is not whole items; with
  ``contiguous`` (the default) the inserted bytes form one span on token boundaries.
  ``heads`` compares the inserted items' labels as a multiset.
- ``assert_removed``: the same with the roles swapped.
- ``atom_changes(before, after)``: the exact list of atoms that changed, and a failure
  if anything else changed, separators included.
- ``assert_confined(before, after, within)``: every change lies inside the item at the
  path ``within``, and every byte outside that item is unchanged.

Bars (decision D1 of the rung-1 plan): KiCad's lexer splits a run such as ``abc|def``
into SYMBOL, BAR, SYMBOL tokens where the CST keeps one atom. ``lexer_view`` merges
tokens that touch with no separator between them back into one atom, so the two views
compare item for item; every other difference stays visible.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from itertools import combinations, islice, product
from typing import NamedTuple

from kicad_ref import dsn_lexer as L

Step = tuple[int, "str | None"]
Path = tuple[Step, ...]


class JudgeError(RuntimeError):
    """The judge cannot judge: the ``before`` bytes, its premise, do not lex."""


class Item:
    """One lexed item: a token (``items`` None) or a list (``items`` a list of Items)."""

    __slots__ = ("kind", "value", "start", "end", "raw", "items", "head", "shape", "sig")

    def __init__(self, kind, value, start, end, raw, items, head, shape, sig):
        self.kind = kind  # a dsn_lexer token kind, or dsn_lexer.GROUP
        self.value = value  # decoded bytes for a token, None for a list
        self.start = start
        self.end = end
        self.raw = raw  # the item's bytes, as in the input
        self.items = items
        self.head = head  # a list's head, None for a token
        self.shape = shape  # what may be paired as "the same item, changed"
        self.sig = sig  # equal sigs mean byte-identical items

    @property
    def label(self) -> str | None:
        if self.items is not None:
            return self.head
        return self.value.decode("utf-8", "surrogateescape")

    def __repr__(self) -> str:
        return f"Item({self.kind}, {self.raw[:40]!r})"


class _Interner:
    """Maps nested signature keys to small ints shared by both sides of one diff."""

    def __init__(self):
        self.ids: dict = {}

    def __call__(self, key) -> int:
        found = self.ids.get(key)
        if found is None:
            found = self.ids[key] = len(self.ids)
        return found


def _build(data: bytes, knows_bar: bool, intern: _Interner) -> tuple[list[Item], list]:
    """(top-level items, tokens) of ``data``; raises dsn_lexer.LexError."""
    toks = L.tokens(data, knows_bar=knows_bar)
    root: list[Item] = []
    items = root
    open_lists: list[tuple[list[Item], int]] = []
    for tok in toks:
        if tok.kind == L.LEFT:
            open_lists.append((items, tok.start))
            items = []
        elif tok.kind == L.RIGHT:
            if not open_lists:
                raise L.Unbalanced(f"unmatched ')' at byte {tok.start}", tok.start, 0)
            parent, start = open_lists.pop()
            first = items[0] if items else None
            head = None
            head_raw = None
            if first is not None and first.items is None and first.kind in L.ATOM_KINDS:
                head = first.value.decode("utf-8", "surrogateescape")
                head_raw = first.raw
            sig = intern((L.GROUP, tuple(i.sig for i in items)))
            parent.append(
                Item(
                    L.GROUP,
                    None,
                    start,
                    tok.end,
                    data[start : tok.end],
                    items,
                    head,
                    (L.GROUP, head_raw),
                    sig,
                )
            )
            items = parent
        else:
            raw = data[tok.start : tok.end]
            items.append(
                Item(
                    tok.kind,
                    tok.value,
                    tok.start,
                    tok.end,
                    raw,
                    None,
                    None,
                    ("ATOM",),
                    intern((tok.kind, raw)),
                )
            )
    if open_lists:
        raise L.Unbalanced(
            f"{len(open_lists)} unclosed '(' at end of input", len(data), len(open_lists)
        )
    return root, toks


def _knows_bar(data: bytes, knows_bar: bool | None) -> bool:
    return L.knows_bar_of(data) if knows_bar is None else knows_bar


def _lex_premise(data: bytes, knows_bar: bool, intern: _Interner, what: str):
    try:
        return _build(data, knows_bar, intern)
    except L.LexError as exc:
        raise JudgeError(f"{what} does not lex, so there is nothing to judge: {exc}") from exc


def _lex_result(data: bytes, knows_bar: bool, intern: _Interner, what: str):
    try:
        return _build(data, knows_bar, intern)
    except L.LexError as exc:
        raise AssertionError(f"{what} does not lex as KiCad reads it: {exc}") from exc


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


class Edit(NamedTuple):
    """One structural change.

    ``op`` is "insert", "delete" or "atom" (an atom whose bytes changed). ``path`` and
    ``index`` locate the item in ``after`` for an insert and in ``before`` otherwise.
    ``old``/``new`` are the item's bytes on each side (None where it does not exist),
    and ``old_at``/``new_at`` their offsets. ``label`` is the inserted, deleted or
    changed (old) item's label.
    """

    op: str
    path: Path
    index: int
    old: bytes | None
    new: bytes | None
    old_at: int | None
    new_at: int | None
    label: str | None

    @property
    def location(self) -> Path:
        return self.path + ((self.index, self.label),)

    def describe(self) -> str:
        where = " > ".join(f"{head}[{i}]" for i, head in self.location)
        body = self.new if self.op == "insert" else self.old
        shown = body if body is None or len(body) <= 60 else body[:57] + b"..."
        if self.op == "atom":
            return f"atom at {where}: {self.old!r} -> {self.new!r}"
        return f"{self.op} at {where}: {shown!r}"


class AtomChange(NamedTuple):
    """An atom whose bytes changed: the list holding it, its index there, its bytes."""

    path: Path
    index: int
    old: bytes
    new: bytes


#: Cost units for the alignment: pairing two items of the same shape costs _W plus
#: how different they are (below _W), so it always beats deleting one and inserting
#: the other (2 * _W), and among pairings the most similar wins.
_W = 1 << 40
#: Above this many cells the middle of a list is aligned greedily instead.
_DP_LIMIT = 250_000


def _dissimilarity(a: Item, b: Item) -> int:
    if a.items is None:
        return 0 if a.kind == b.kind else 1
    left = Counter(i.sig for i in a.items)
    right = Counter(i.sig for i in b.items)
    return min(_W - 1, sum(((left - right) + (right - left)).values()))


def _align(a: list[Item], b: list[Item]) -> list[tuple[str, int, int]]:
    """Pair the items of two lists: ("eq"|"mod", i, j), ("del", i, -1), ("ins", -1, j)."""
    n, m = len(a), len(b)
    lo = 0
    while lo < n and lo < m and a[lo].sig == b[lo].sig:
        lo += 1
    hi_a, hi_b = n, m
    while hi_a > lo and hi_b > lo and a[hi_a - 1].sig == b[hi_b - 1].sig:
        hi_a -= 1
        hi_b -= 1
    ops = [("eq", k, k) for k in range(lo)]
    ops += _align_middle(a, b, lo, hi_a, lo, hi_b)
    ops += [("eq", hi_a + k, hi_b + k) for k in range(n - hi_a)]
    return ops


def _align_middle(a, b, a0, a1, b0, b1) -> list[tuple[str, int, int]]:
    rows, cols = a1 - a0, b1 - b0
    if rows == 0:
        return [("ins", -1, b0 + j) for j in range(cols)]
    if cols == 0:
        return [("del", a0 + i, -1) for i in range(rows)]
    if rows * cols > _DP_LIMIT:
        return _align_greedy(a, b, a0, a1, b0, b1)
    # cost[i][j]: cheapest alignment of a[a0 + i:a1] with b[b0 + j:b1]
    cost = [[0] * (cols + 1) for _ in range(rows + 1)]
    for j in range(cols - 1, -1, -1):
        cost[rows][j] = cost[rows][j + 1] + _W
    for i in range(rows - 1, -1, -1):
        cost[i][cols] = cost[i + 1][cols] + _W
        x = a[a0 + i]
        row, below = cost[i], cost[i + 1]
        for j in range(cols - 1, -1, -1):
            y = b[b0 + j]
            best = below[j] + _W  # delete x
            right = row[j + 1] + _W  # insert y
            if right < best:
                best = right
            if x.sig == y.sig:
                pair = below[j + 1]
            elif x.shape == y.shape:
                pair = below[j + 1] + _W + _dissimilarity(x, y)
            else:
                pair = None
            if pair is not None and pair <= best:
                best = pair
            row[j] = best
    ops: list[tuple[str, int, int]] = []
    i = j = 0
    while i < rows or j < cols:
        if i < rows and j < cols:
            x, y = a[a0 + i], b[b0 + j]
            if x.sig == y.sig and cost[i][j] == cost[i + 1][j + 1]:
                ops.append(("eq", a0 + i, b0 + j))
                i, j = i + 1, j + 1
                continue
            if (
                x.shape == y.shape
                and x.sig != y.sig
                and cost[i][j] == cost[i + 1][j + 1] + _W + _dissimilarity(x, y)
            ):
                ops.append(("mod", a0 + i, b0 + j))
                i, j = i + 1, j + 1
                continue
        if i < rows and cost[i][j] == cost[i + 1][j] + _W:
            ops.append(("del", a0 + i, -1))
            i += 1
        else:
            ops.append(("ins", -1, b0 + j))
            j += 1
    return ops


def _align_greedy(a, b, a0, a1, b0, b1) -> list[tuple[str, int, int]]:
    """Fallback for very long middles: pair equal items in order, then same shapes."""
    import difflib

    left = [x.sig for x in a[a0:a1]]
    right = [y.sig for y in b[b0:b1]]
    ops: list[tuple[str, int, int]] = []
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops += [("eq", a0 + i1 + k, b0 + j1 + k) for k in range(i2 - i1)]
            continue
        i, j = i1, j1
        while i < i2 and j < j2 and a[a0 + i].shape == b[b0 + j].shape:
            ops.append(("mod", a0 + i, b0 + j))
            i, j = i + 1, j + 1
        ops += [("del", a0 + k, -1) for k in range(i, i2)]
        ops += [("ins", -1, b0 + k) for k in range(j, j2)]
    return ops


def _diff_items(a, b, path_a: Path, path_b: Path, out: list[Edit]) -> None:
    for op, i, j in _align(a, b):
        if op == "eq":
            continue
        if op == "del":
            x = a[i]
            out.append(Edit("delete", path_a, i, x.raw, None, x.start, None, x.label))
        elif op == "ins":
            y = b[j]
            out.append(Edit("insert", path_b, j, None, y.raw, None, y.start, y.label))
        else:
            x, y = a[i], b[j]
            if x.items is None:
                out.append(Edit("atom", path_a, i, x.raw, y.raw, x.start, y.start, x.label))
            else:
                _diff_items(x.items, y.items, path_a + ((i, x.head),), path_b + ((j, y.head),), out)


def diff(before: bytes, after: bytes, *, knows_bar: bool | None = None) -> list[Edit]:
    """The structural edits that turn ``before`` into ``after``, in document order.

    Each list's children are aligned with the fewest edits at that level, two items of the same
    shape (two atoms, or two lists with the same head) being one item changed, not one deleted and
    another inserted; among equally few edits the most similar pairing wins (atoms of one kind,
    lists sharing the most children), then pairing the next two items over deleting or inserting
    one; past _DP_LIMIT cells a middle is aligned greedily, which can take more edits. Separators
    are not items: whitespace alone changed is no edit (assert_* judges check bytes). ``knows_bar``
    defaults to what ``before`` declares. Raises dsn_lexer.LexError when either side does not lex.
    """
    knows_bar = _knows_bar(before, knows_bar)
    intern = _Interner()
    a, _ = _build(before, knows_bar, intern)
    b, _ = _build(after, knows_bar, intern)
    out: list[Edit] = []
    _diff_items(a, b, (), (), out)
    return out


# ---------------------------------------------------------------------------
# byte-level checks
# ---------------------------------------------------------------------------


def _common_prefix(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    i = 0
    while i + 4096 <= n and a[i : i + 4096] == b[i : i + 4096]:
        i += 4096
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _common_suffix(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    i = 0
    while i + 4096 <= n and a[len(a) - i - 4096 : len(a) - i] == b[len(b) - i - 4096 : len(b) - i]:
        i += 4096
    while i < n and a[len(a) - 1 - i] == b[len(b) - 1 - i]:
        i += 1
    return i


def single_span(before: bytes, after: bytes) -> tuple[int, int] | None:
    """Where one inserted span could sit: (lo, hi) such that for every k in lo..hi,
    ``after == before[:k] + after[k:k + d] + before[k:]`` with d the size difference.

    None when ``after`` is not longer, or no single span explains the difference. The
    prefix and suffix are matched independently, so a run of repeated bytes widens the
    range rather than hiding the span.
    """
    if len(after) <= len(before):
        return None
    lo = len(before) - _common_suffix(before, after)
    hi = _common_prefix(before, after)
    return (lo, hi) if lo <= hi else None


def boundaries(data: bytes, *, knows_bar: bool | None = None) -> set[int]:
    """Offsets where a token starts or ends, plus 0 and the end of ``data``."""
    out = {0, len(data)}
    for tok in L.iter_tokens(data, knows_bar=_knows_bar(data, knows_bar)):
        out.add(tok.start)
        out.add(tok.end)
    return out


def on_boundary(data: bytes, k: int, *, knows_bar: bool | None = None) -> bool:
    """Whether offset ``k`` of ``data`` is a token boundary (KiCad's lexer, not the CST's)."""
    return k in boundaries(data, knows_bar=knows_bar)


def _gaps(before: bytes, after: bytes, b_toks, a_toks, spans: list[tuple[int, int]]):
    """Check that ``after`` is ``before`` plus the items at ``spans`` (offsets in after).

    Yields, per gap between two of before's tokens that received items, the offset in
    before where the inserted bytes go and how many separate chunks they form there.
    Raises AssertionError when any other byte differs.
    """
    spans = sorted(spans)
    kept = []
    s = 0
    for tok in a_toks:
        while s < len(spans) and spans[s][1] <= tok.start:
            s += 1
        if s < len(spans) and spans[s][0] <= tok.start < spans[s][1]:
            continue
        kept.append(tok)
    if len(kept) != len(b_toks) or any(
        before[x.start : x.end] != after[y.start : y.end] for x, y in zip(b_toks, kept)
    ):
        raise AssertionError("the tokens outside the inserted items are not before's tokens")
    s = 0
    for g in range(len(b_toks) + 1):
        b_lo = b_toks[g - 1].end if g else 0
        b_hi = b_toks[g].start if g < len(b_toks) else len(before)
        a_lo = kept[g - 1].end if g else 0
        a_hi = kept[g].start if g < len(kept) else len(after)
        inside = []
        while s < len(spans) and spans[s][0] < a_hi:
            if spans[s][0] >= a_lo:
                inside.append(spans[s])
            s += 1
        sep = before[b_lo:b_hi]
        if not inside:
            if after[a_lo:a_hi] != sep:
                raise AssertionError(
                    f"separator bytes changed at before offset {b_lo}:"
                    f" {sep!r} -> {after[a_lo:a_hi]!r}"
                )
            continue
        cuts = [a_lo] + [x for span in inside for x in span] + [a_hi]
        runs = [after[cuts[2 * k] : cuts[2 * k + 1]] for k in range(len(inside) + 1)]
        keep = [k for k, run in enumerate(runs) if run == sep]
        if not keep:
            raise AssertionError(
                f"the bytes around the items inserted at before offset {b_lo} do not leave"
                f" before's separator {sep!r} in place"
            )
        last = len(inside)
        if 0 in keep:
            yield b_hi, 1
        elif last in keep:
            yield b_lo, 1
        else:
            yield b_lo, 2


def _assert_insertion(
    small: bytes,
    big: bytes,
    *,
    heads,
    contiguous: bool,
    knows_bar: bool,
    verb: str,
    small_name: str,
    big_name: str,
) -> list[Edit]:
    if small == big:
        raise AssertionError(f"no-op: nothing was {verb}; before and after are identical")
    intern = _Interner()
    if verb == "inserted":
        s_tree, s_toks = _lex_premise(small, knows_bar, intern, small_name)
        b_tree, b_toks = _lex_result(big, knows_bar, intern, big_name)
    else:
        b_tree, b_toks = _lex_premise(big, knows_bar, intern, big_name)
        s_tree, s_toks = _lex_result(small, knows_bar, intern, small_name)
    edits: list[Edit] = []
    _diff_items(s_tree, b_tree, (), (), edits)
    if not edits:
        raise AssertionError(
            f"nothing was {verb}: the tokens are identical, the separators are not"
        )
    wrong = [e for e in edits if e.op != "insert"]
    if wrong:
        shown = "; ".join(e.describe() for e in wrong[:3])
        raise AssertionError(f"not a pure {verb[:-1]}ion of whole items: {shown}")
    labels = Counter(e.label for e in edits)
    if heads is not None and labels != Counter(heads):
        raise AssertionError(
            f"{verb} items labelled {sorted(labels.elements(), key=str)},"
            f" expected {sorted(heads, key=str)}"
        )
    # Among byte-identical siblings the structure cannot say which one is new, so
    # every choice inside such a run is tried against the bytes.
    runs: dict[tuple[Path, int, int], int] = {}
    for e in edits:
        siblings = _children_at(b_tree, e.path)
        sig = siblings[e.index].sig
        lo = hi = e.index
        while lo > 0 and siblings[lo - 1].sig == sig:
            lo -= 1
        while hi + 1 < len(siblings) and siblings[hi + 1].sig == sig:
            hi += 1
        runs[(e.path, lo, hi)] = runs.get((e.path, lo, hi), 0) + 1
    options = [
        [(path, chosen) for chosen in combinations(range(lo, hi + 1), count)]
        for (path, lo, hi), count in runs.items()
    ]
    first_failure: AssertionError | None = None
    for choice in islice(product(*options), 1024):
        picked = [
            (path, index, _children_at(b_tree, path)[index])
            for path, chosen in choice
            for index in chosen
        ]
        try:
            _check_placement(
                small, big, s_toks, b_toks, picked, contiguous, verb, small_name, big_name
            )
        except AssertionError as exc:
            first_failure = first_failure or exc
            continue
        op = "insert" if verb == "inserted" else "delete"
        out = []
        for path, index, item in sorted(picked, key=lambda p: p[2].start):
            if op == "insert":
                out.append(Edit(op, path, index, None, item.raw, None, item.start, item.label))
            else:
                out.append(Edit(op, path, index, item.raw, None, item.start, None, item.label))
        return out
    assert first_failure is not None
    raise first_failure


def _children_at(tree: list[Item], path: Path) -> list[Item]:
    items = tree
    for index, _ in path:
        items = items[index].items
    return items


def _check_placement(small, big, s_toks, b_toks, picked, contiguous, verb, small_name, big_name):
    """Raise AssertionError unless ``big`` is ``small`` plus exactly the picked items."""
    spans = [(item.start, item.end) for _, _, item in picked]
    places = list(_gaps(small, big, s_toks, b_toks, spans))
    if not contiguous:
        return
    if len(places) != 1 or places[0][1] != 1:
        raise AssertionError(
            f"the {verb} items are not one contiguous span: {len(places)} places,"
            f" {sum(c for _, c in places)} chunks"
        )
    feasible = single_span(small, big)
    cut = places[0][0]
    if feasible is None or not feasible[0] <= cut <= feasible[1]:
        raise AssertionError(f"no single span of {big_name} explains the difference")
    if cut not in {0, len(small)} | {x for t in s_toks for x in (t.start, t.end)}:
        raise AssertionError(f"the span is not on a token boundary of {small_name}")


def assert_inserted(
    before: bytes,
    after: bytes,
    *,
    heads=None,
    contiguous: bool = True,
    knows_bar: bool | None = None,
) -> list[Edit]:
    """Assert ``after`` is ``before`` with whole items inserted; return the insertions.

    Fails on a no-op, on any change that is not an insertion of whole items, on bytes
    that moved elsewhere, and, with ``contiguous``, on more than one inserted span.
    ``heads`` (any iterable of labels) must equal the inserted items' labels as a
    multiset. Raises JudgeError when ``before`` itself does not lex.
    """
    return _assert_insertion(
        before,
        after,
        heads=heads,
        contiguous=contiguous,
        knows_bar=_knows_bar(before, knows_bar),
        verb="inserted",
        small_name="before",
        big_name="after",
    )


def assert_removed(
    before: bytes,
    after: bytes,
    *,
    heads=None,
    contiguous: bool = True,
    knows_bar: bool | None = None,
) -> list[Edit]:
    """Assert ``after`` is ``before`` with whole items removed; return the removals.

    The mirror of assert_inserted: the returned edits are deletions located in
    ``before``.
    """
    return _assert_insertion(
        after,
        before,
        heads=heads,
        contiguous=contiguous,
        knows_bar=_knows_bar(before, knows_bar),
        verb="removed",
        small_name="after",
        big_name="before",
    )


def atom_changes(before: bytes, after: bytes, *, knows_bar: bool | None = None) -> list[AtomChange]:
    """The atoms an edit changed, in order; fails if anything else changed.

    Anything else includes inserted or deleted items, a list whose head changed (which
    reads as one list deleted and another inserted), and separator bytes. Tests assert
    the exact list returned.
    """
    knows_bar = _knows_bar(before, knows_bar)
    intern = _Interner()
    a, _ = _lex_premise(before, knows_bar, intern, "before")
    b, _ = _lex_result(after, knows_bar, intern, "after")
    edits: list[Edit] = []
    _diff_items(a, b, (), (), edits)
    wrong = [e for e in edits if e.op != "atom"]
    if wrong:
        shown = "; ".join(e.describe() for e in wrong[:3])
        raise AssertionError(f"not only atom changes: {shown}")
    changes = []
    rebuilt = bytearray()
    cursor = 0
    for e in edits:
        old, new, at = e.old or b"", e.new or b"", e.old_at or 0  # set on every atom edit
        rebuilt += before[cursor:at] + new
        cursor = at + len(old)
        changes.append(AtomChange(e.path, e.index, old, new))
    rebuilt += before[cursor:]
    if bytes(rebuilt) != after:
        raise AssertionError("bytes outside the changed atoms changed too")
    return changes


# ---------------------------------------------------------------------------
# locating items
# ---------------------------------------------------------------------------


def _tree(data: bytes, knows_bar: bool | None) -> list[Item]:
    return _build(data, _knows_bar(data, knows_bar), _Interner())[0]


def walk(data: bytes, *, knows_bar: bool | None = None) -> Iterator[tuple[Path, Item]]:
    """Every item of ``data`` with its location, in document order (lists before their
    contents)."""
    pending: list[tuple[Path, list[Item], int]] = [((), _tree(data, knows_bar), 0)]
    while pending:
        path, items, i = pending.pop()
        if i >= len(items):
            continue
        item = items[i]
        pending.append((path, items, i + 1))
        location = path + ((i, item.label),)
        yield location, item
        if item.items is not None:
            pending.append((location, item.items, 0))


def item_at(data: bytes, path: Path, *, knows_bar: bool | None = None) -> Item:
    """The item at ``path`` (whose steps must match both index and label)."""
    items = _tree(data, knows_bar)
    item = None
    for depth, (index, label) in enumerate(path):
        if items is None or not 0 <= index < len(items) or items[index].label != label:
            raise LookupError(f"no item at step {depth} of {path!r}")
        item = items[index]
        items = item.items
    if item is None:
        raise LookupError("the empty path names no item")
    return item


def find_paths(
    data: bytes, predicate: Callable[[Path, Item], bool], *, knows_bar: bool | None = None
) -> list[Path]:
    """Locations of the items ``predicate(location, item)`` accepts, in document order."""
    return [loc for loc, item in walk(data, knows_bar=knows_bar) if predicate(loc, item)]


def assert_confined(
    before: bytes, after: bytes, within: Path, *, knows_bar: bool | None = None
) -> list[Edit]:
    """Assert every change lies inside the item at path ``within``; return the edits.

    Fails on a no-op, on any edit located outside ``within``, and on any byte outside
    that item that differs (separators included).
    """
    if before == after:
        raise AssertionError("no-op: nothing changed")
    knows_bar = _knows_bar(before, knows_bar)
    intern = _Interner()
    a, _ = _lex_premise(before, knows_bar, intern, "before")
    b, _ = _lex_result(after, knows_bar, intern, "after")
    edits: list[Edit] = []
    _diff_items(a, b, (), (), edits)
    outside = [e for e in edits if e.location[: len(within)] != tuple(within)]
    if outside:
        shown = "; ".join(e.describe() for e in outside[:3])
        raise AssertionError(f"changes outside {within!r}: {shown}")
    region_b = item_at(before, within, knows_bar=knows_bar)
    try:
        region_a = item_at(after, within, knows_bar=knows_bar)
    except LookupError as exc:
        raise AssertionError(f"{within!r} no longer names an item in after") from exc
    if (
        before[: region_b.start] != after[: region_a.start]
        or before[region_b.end :] != after[region_a.end :]
    ):
        raise AssertionError(f"bytes outside {within!r} changed")
    return edits


# ---------------------------------------------------------------------------
# comparable views of the CST and of KiCad's lexer
# ---------------------------------------------------------------------------


def cst_view(doc) -> list[tuple]:
    """The CST as a flat event list: ("(",), (")",) and ("A", quoted, value, raw).

    ``value`` is the atom's text as bytes (``text.encode("utf-8", "surrogateescape")``),
    ``raw`` its bytes in the file. Reads ``children``, ``kind``, ``raw`` and ``text``.
    """
    out: list[tuple] = []
    pending: list = [iter(doc.children)]
    while pending:
        child = next(pending[-1], None)
        if child is None:
            pending.pop()
            if pending:
                out.append((")",))
            continue
        if child.kind == "atom":
            raw = child.raw
            out.append(("A", raw[:1] == b'"', child.text.encode("utf-8", "surrogateescape"), raw))
        else:
            out.append(("(",))
            pending.append(iter(child.children))
    return out


_MERGEABLE = frozenset({L.SYMBOL, L.NUMBER, L.BAR})


def lexer_view(data: bytes, *, knows_bar: bool | None = None) -> list[tuple]:
    """KiCad's lexer's reading of ``data`` as the event list cst_view produces.

    Tokens of kind SYMBOL, NUMBER or BAR that touch with no separator between them are
    merged into one bare atom whose value is their bytes (decision D1); a STRING is a
    quoted atom whose value is KiCad's decoding. Raises dsn_lexer.LexError.
    """
    out: list[tuple] = []
    run_start = run_end = -1
    for tok in L.iter_tokens(data, knows_bar=_knows_bar(data, knows_bar)):
        if tok.kind in _MERGEABLE:
            if tok.start == run_end:
                run_end = tok.end
                continue
            if run_end >= 0:
                raw = data[run_start:run_end]
                out.append(("A", False, raw, raw))
            run_start, run_end = tok.start, tok.end
            continue
        if run_end >= 0:
            raw = data[run_start:run_end]
            out.append(("A", False, raw, raw))
            run_start = run_end = -1
        if tok.kind == L.STRING:
            out.append(("A", True, tok.value, data[tok.start : tok.end]))
        else:
            out.append((tok.kind,))
    if run_end >= 0:
        raw = data[run_start:run_end]
        out.append(("A", False, raw, raw))
    return out
