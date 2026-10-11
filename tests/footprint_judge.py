"""Judging one footprint against another: as KiCad reads it, and token for token.

Every footprint is read from the file's bytes with the lexer port and
kicad_ref.footprint, never with the CST under test. The comparisons follow the test
ladder's rules for edits:

- D1, KiCad's reading: two footprints are compared as KiCad reads their tokens (angles
  through Normalize180, Normalize and the text loader's arithmetic), so a spelling KiCad
  reads the same way is no difference; spelling is judged on its own (``tokens``).
- D2, the comparison form (kicad_ref.footprint.canonical): a keep-upright text as
  KeepUpright leaves it, any other text at its angle normalized.
- D3, positions: footprint-relative positions are exact on quarter turns; elsewhere KiCad
  re-derives the ones it writes from board positions it rounded through RotatePoint, which
  can move each coordinate by up to two nanometres over a load, a turn and a save
  (BOARD_ITEM::GetFPRelativePosition, pcbnew/board_item.cpp:334-345 at 9.0.8, 399-410 at
  10.0.7), so a comparison with pcbnew's output is given ``tolerance_nm=2`` there.

Pads are paired by number and texts by key (a field's name, an fp_text's text) and layer;
within a number or a key, by the nearest footprint-relative position, ties broken by the
nearest angle, because pcbnew's writer sorts pads and drawings (FOOTPRINT::cmp_drawings and
cmp_pads, pcbnew/footprint.cpp:3774-3894 at 9.0.8, 4346-4497 at 10.0.7) where a library
file keeps its own order, and some stock footprints stack pads of one number at one place.
"""

from __future__ import annotations

import math
from collections import defaultdict

from kicad_ref import dsn_lexer as L
from kicad_ref import footprint as F
from kicad_ref import pad as PAD
from kicad_ref import pcb_text as T
from kicad_ref.trigo import normalize

#: Within this many nanometres of each other, two footprint-relative positions are the
#: same after a turn off the quarter turns (module docstring, D3).
ROTATION_TOLERANCE_NM = 2


def root(data: bytes):
    """The top-level list of a board or a .kicad_mod, read by the lexer port."""
    return L.tree(data, knows_bar=L.knows_bar_of(data))[0]


def reference_of(group) -> str | None:
    """A footprint's Reference, from its (property "Reference" ...) or a legacy
    (fp_text reference ...)."""
    for item in group.items:
        name = F.head(item)
        if name == b"property" and len(item.items) > 2 and item.items[1].value == b"Reference":
            return item.items[2].value.decode()
        if name == b"fp_text" and len(item.items) > 2 and item.items[1].value == b"reference":
            return item.items[2].value.decode()
    return None


def child(group, name: bytes):
    """The first child list of ``group`` headed ``name``; RuntimeError when there is none
    (a premise of the caller's reading, never the defect judged)."""
    found = F.child(group, name)
    if found is None:
        raise RuntimeError(f"no ({name.decode()} ...) in the list at byte {group.start}")
    return found


def footprint_group(data: bytes, reference: str):
    """The one (footprint ...) of a board whose Reference is ``reference``; RuntimeError
    when there is not exactly one (a premise, never the defect judged)."""
    found = [fp for fp in F.children(root(data), b"footprint") if reference_of(fp) == reference]
    if len(found) != 1:
        raise RuntimeError(f"{len(found)} footprints {reference!r} on the board, not one")
    return found[0]


def read(data: bytes, reference: str, *, major: int = 10) -> F.Footprint:
    """Footprint ``reference`` of the board ``data`` as KiCad ``major`` reads it."""
    return F.read_footprint(footprint_group(data, reference), major=major)


def read_library(data: bytes, *, major: int = 10) -> F.Footprint:
    """The footprint of a .kicad_mod as KiCad reads it (orientation 0, at the origin)."""
    return F.read_footprint(root(data), major=major)


def _distance(a, b) -> float:
    if a is None or b is None:
        return 0.0 if a is b else math.inf
    return math.dist(a, b)


def _angle_gap(a: float, b: float) -> float:
    gap = abs(normalize(a) - normalize(b))
    return min(gap, 360.0 - gap)


def pair(got, want, key, local, what: str, tiebreak=None) -> tuple[list[tuple], list[str]]:
    """(pairs, problems): the items of ``got`` and ``want`` paired by ``key`` and, within a
    key, by the nearest ``local`` position, ties broken by the smallest ``tiebreak( item,
    other )`` (several of KiCad's stock footprints stack pads of one number at one place
    at different angles); a key holding a different number of items on each side is a
    problem."""
    by_got, by_want = defaultdict(list), defaultdict(list)
    for item in got:
        by_got[key(item)].append(item)
    for item in want:
        by_want[key(item)].append(item)
    pairs, problems = [], []
    for k in sorted(set(by_got) | set(by_want), key=repr):
        mine, theirs = by_got[k], list(by_want[k])
        if len(mine) != len(theirs):
            problems.append(f"{what} {k!r}: {len(mine)} written where KiCad has {len(theirs)}")
            continue
        for item in sorted(mine, key=lambda i: repr(local(i))):
            best = min(
                theirs,
                key=lambda other: (
                    _distance(local(item), local(other)),
                    tiebreak(item, other) if tiebreak else 0,
                ),
            )
            theirs.remove(best)
            pairs.append((item, best))
    return pairs, problems


def _pad_tiebreak(a: F.Pad, b: F.Pad) -> float:
    return _angle_gap(a.angle, b.angle)


def _text_tiebreak(a: F.Text, b: F.Text) -> float:
    return _angle_gap(a.style.angle, b.style.angle) + (0 if a.style == b.style else 1)


def _near(a, b, tolerance_nm: int) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a[0] - b[0]) <= tolerance_nm and abs(a[1] - b[1]) <= tolerance_nm


def differences(
    got: F.Footprint,
    want: F.Footprint,
    *,
    tolerance_nm: int = 0,
    position: bool = True,
    shapes: bool = False,
) -> list[str]:
    """Where ``got`` differs from ``want`` in the comparison form: the footprint's
    orientation and layer (and position, unless ``position`` is False), every pad's
    absolute angle and footprint-relative position, every text's style (angle,
    keep-upright, justification, mirror), layer and position, and with ``shapes`` every
    graphic's points. Positions within ``tolerance_nm``. Empty when they agree."""
    got, want = F.canonical(got), F.canonical(want)
    problems = []
    if position and got.position != want.position:
        problems.append(f"footprint position {got.position} where KiCad has {want.position}")
    if got.orientation != want.orientation:
        problems.append(f"orientation {got.orientation!r} where KiCad has {want.orientation!r}")
    if got.layer != want.layer:
        problems.append(f"layer {got.layer} where KiCad has {want.layer}")
    pads, found = pair(
        got.pads, want.pads, lambda p: p.number, lambda p: p.local, "pad", _pad_tiebreak
    )
    problems += found
    for mine, theirs in pads:
        if mine.angle != theirs.angle:
            problems.append(
                f"pad {mine.number!r} at {mine.local}: angle {mine.angle!r}"
                f" where KiCad has {theirs.angle!r}"
            )
        if not _near(mine.local, theirs.local, tolerance_nm):
            problems.append(f"pad {mine.number!r}: at {mine.local} where KiCad has {theirs.local}")
    texts, found = pair(
        got.texts,
        want.texts,
        lambda t: (t.key, t.layer),
        lambda t: t.local,
        "text",
        _text_tiebreak,
    )
    problems += found
    for mine, theirs in texts:
        if mine.style != theirs.style:
            problems.append(f"text {mine.key!r}: {mine.style} where KiCad has {theirs.style}")
        if mine.layer != theirs.layer:
            problems.append(f"text {mine.key!r}: layer {mine.layer} where KiCad has {theirs.layer}")
        if not _near(mine.local, theirs.local, tolerance_nm):
            problems.append(f"text {mine.key!r}: at {mine.local} where KiCad has {theirs.local}")
    if shapes:
        drawn, found = pair(
            got.shapes,
            want.shapes,
            lambda s: (s.kind, s.layer),
            lambda s: s.points[0] if s.points else None,
            "graphic",
        )
        problems += found
        for mine, theirs in drawn:
            if len(mine.points) != len(theirs.points) or not all(
                _near(a, b, tolerance_nm) for a, b in zip(mine.points, theirs.points)
            ):
                problems.append(
                    f"{mine.kind} on {mine.layer}: {mine.points} against {theirs.points}"
                )
    return problems


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------


def _angle_atom(at) -> str | None:
    if at is None or len(at.items) < 4 or at.items[3].kind != L.NUMBER:
        return None
    return at.items[3].value.decode()


def footprint_angle_token(group) -> str | None:
    """The footprint's own (at) angle atom as written, None when it has none."""
    return _angle_atom(F.child(group, b"at"))


def tokens(group, *, major: int = 10) -> dict:
    """What a footprint's orientation edit writes, as written: the footprint's angle atom,
    and per pad and per text (in read_footprint's order, alongside its reading) the (at)
    angle atom, and for a text the (unlocked ...) word or bare atom and the justify words.
    Returns {"footprint": atom, "pads": [(Pad, atom)], "texts": [(Text, (atom, unlocked,
    justify))]}."""
    fp = F.read_footprint(group, major=major)
    pads = [(pad, _angle_atom(F.child(g, b"at"))) for pad, g in zip(fp.pads, F.pad_groups(group))]
    texts = []
    for text, g in zip(fp.texts, F.text_groups(group)):
        at = F.child(g, b"at")
        unlocked = []
        if at is not None:
            unlocked = [w.value.decode() for w in at.items[3:] if w.kind == L.SYMBOL]
        for node in F.children(g, b"unlocked"):
            unlocked.append(node.items[1].value.decode())
        effects = F.child(g, b"effects")
        justify = []
        for node in F.children(effects, b"justify") if effects is not None else []:
            justify += [w.value.decode() for w in node.items[1:]]
        texts.append((text, (_angle_atom(at), tuple(unlocked), tuple(justify))))
    return {"footprint": footprint_angle_token(group), "pads": pads, "texts": texts}


def expected_tokens(fp: F.Footprint) -> dict:
    """The tokens KiCad's writer spells for the footprint ``fp`` holds, in the shape
    ``tokens`` returns: the footprint's angle atom (fp_angle_token), each pad's
    (kicad_ref.pad.angle_token), and each text's angle atom (always written), ("yes",)
    for a text that is not keep-upright and the justify words (kicad_ref.pcb_text)."""
    return {
        "footprint": F.fp_angle_token(fp.orientation),
        "pads": [(p, PAD.angle_token(p.angle)) for p in fp.pads],
        "texts": [
            (
                t,
                (
                    T.angle_token(t.style),
                    () if t.style.keep_upright else ("yes",),
                    tuple(T.justify_words(t.style)),
                ),
            )
            for t in fp.texts
        ],
    }


#: The fields a footprint holds whether or not its file writes them (kicad_ref.footprint
#: MANDATORY_FIELDS): a file that leaves one out is read with its default, so in a
#: comparison of written tokens one side may lack it; ``differences`` compares its reading.
MANDATORY_KEYS = frozenset(("property", name) for name, _ in F.MANDATORY_FIELDS)


def token_problems(
    got: dict, want: dict, *, tolerance_nm: int = 0, footprint: bool = True
) -> list[str]:
    """Where two ``tokens`` results differ: the footprint's angle atom (unless
    ``footprint`` is False), each pad's angle atom, each text's angle atom, unlocked word
    and justify words, all compared as strings; positions as KiCad reads them, within
    ``tolerance_nm``. A mandatory field ``got`` does not write is left to ``differences``.
    Empty when they agree."""
    written = {t.key for t, _ in got["texts"]}
    want = {
        **want,
        "texts": [
            entry
            for entry in want["texts"]
            if entry[0].key in written or entry[0].key not in MANDATORY_KEYS
        ],
    }
    problems = []
    if footprint and got["footprint"] != want["footprint"]:
        problems.append(
            f"footprint angle {got['footprint']!r} where KiCad writes {want['footprint']!r}"
        )
    pads, found = pair(
        got["pads"],
        want["pads"],
        lambda p: p[0].number,
        lambda p: p[0].local,
        "pad",
        lambda a, b: _pad_tiebreak(a[0], b[0]),
    )
    problems += found
    for (mine, mine_atom), (theirs, their_atom) in pads:
        if mine_atom != their_atom:
            problems.append(
                f"pad {mine.number!r} at {mine.local}: angle {mine_atom!r}"
                f" where KiCad writes {their_atom!r}"
            )
        if not _near(mine.local, theirs.local, tolerance_nm):
            problems.append(f"pad {mine.number!r}: at {mine.local} where KiCad has {theirs.local}")
    texts, found = pair(
        got["texts"],
        want["texts"],
        lambda t: (t[0].key, t[0].layer),
        lambda t: t[0].local,
        "text",
        lambda a, b: _text_tiebreak(a[0], b[0]),
    )
    problems += found
    for (mine, mine_tokens), (theirs, their_tokens) in texts:
        if mine_tokens != their_tokens:
            problems.append(
                f"text {mine.key!r}: (angle, unlocked, justify) {mine_tokens!r}"
                f" where KiCad writes {their_tokens!r}"
            )
        if not _near(mine.local, theirs.local, tolerance_nm):
            problems.append(f"text {mine.key!r}: at {mine.local} where KiCad has {theirs.local}")
    return problems


def token_differences(
    got_group, want_group, *, tolerance_nm: int = 0, footprint: bool = True
) -> list[str]:
    """token_problems of two footprints as written."""
    return token_problems(
        tokens(got_group), tokens(want_group), tolerance_nm=tolerance_nm, footprint=footprint
    )
