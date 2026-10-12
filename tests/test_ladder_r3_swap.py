"""Rung 3, package 3 (the swap rows) and step 7's swap tests: a footprint swapped by the
board update, judged by KiCad.

Code under test: pcb._apply_netlist_cst, the board update behind update_pcb_from_schematic,
when a symbol's footprint id no longer matches its board footprint's (the swap), and its
removal of stale footprints; driven with netlists edit_gen.netlist_text builds in
kicad-cli's form (rung 3's D11), with no schematic and no kicad-cli.

KiCad's rule (kicad_ref.exchange, which test_ladder_r3_judges.py checks against pcbnew
replaying ExchangeFootprint): a locked footprint is never swapped, the updater reports it
and still updates its value, fields and pads; any other footprint is replaced by the
library footprint placed at its position, flipped onto its side and set to its
orientation, so it keeps position, angle and side, its pads and texts turned and flipped
with it; the new footprint carries the old footprint's uuid, its Reference's and Value's,
each other field's by name, each fp_text's by getMatchingTextItem and each pad's by
number, so a group that listed the old footprint lists the new one. Stale removal keeps a
locked footprint. A footprint is locked when KiCad's parser reads it so: a bare
``locked``, (locked), (locked yes) or (locked true), and not (locked no) or (locked
false).

Judges, none of them the code under test:

- kicad_ref.exchange (exchange, carried_ids, is_locked) over kicad_ref.footprint's
  reading of every file through the lexer port; comparisons follow footprint_judge's
  rules D1 to D3 and leave out the mandatory fields a file does not write;
- the domain rule that a swap to the same geometry under another name keeps every pad
  where it was;
- pcbnew replaying the exchange (pcbnew_fp_ops.exchange_job), per footprint;
- the invariant: a swap the tool refuses (a construct it does not port) keeps the old
  footprint and says so; a swap changes nothing outside the swapped footprint but pad
  nets (the pad pass rewrites those, bug 22, step 10's).

Rung 3's D4 kept each test to one cause while its bug was open: bug 7's tests compare
footprint geometry and pad positions, never pad angles, with library pad angles 0; bug
2's swap tests stand on the front, or on the back at plus or minus 90, where bug 7's
angle agreed; bug 16's text test swaps on the front and compares texts only; bug 15's
zone test is a front swap comparing zones only. Generated tests build their boards in temporary
directories (D17); the hand-picked ones write into tmp_path, where the autouse oracle
loads every board and footprint with kicad-cli.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import edit_gen as E
import edit_judge as J
import footprint_judge as FJ
import geom_gen as G
import pcbnew_fp_ops as OPS
import pcbnew_judge as PJ
import pytest
import sexpr_gen
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from kicad_ref import board_item as B
from kicad_ref import exchange as X
from kicad_ref import footprint as F

from mcp_server_kicad import pcb

MM = 1_000_000

_VERSIONS = st.sampled_from(("k9", "k10"))
_HALF = G.half_degree_angles()
_AT = (50 * MM, 40 * MM)
_NEIGHBOUR = (90 * MM, 40 * MM)
#: The nickname of the library a swap takes its new footprint from; the board's own
#: footprints come from "Ladder" (edit_gen.board_footprint_text), so a footprint of one
#: name in both is a swap all the same.
_SWAP = "Swap"


def _major(version: str) -> int:
    """The KiCad that reads a board of ``version``."""
    return 10 if version == "k10" else 9


def _forget(path: Path) -> None:
    """Drop the board pcb._open_pcb_cst cached for ``path``."""
    pcb._BOARD_CACHE.pop(str(Path(path).resolve()), None)


def _board(path: Path, items=(), version: str = "k9", *, copper: int = 2) -> Path:
    path.write_text(
        E.layered_board_text(items, version=version, copper=copper),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _update(board: Path, netlist: str, libraries, *, delete_stale: bool = False) -> dict:
    """pcb._apply_netlist_cst with ``netlist`` written beside ``board``, the .pretty
    directories ``libraries`` to resolve footprints in."""
    path = board.parent / "netlist.xml"
    path.write_text(netlist, encoding="utf-8")
    try:
        return pcb._apply_netlist_cst(
            str(path), str(board), [str(p) for p in libraries], delete_stale
        )
    finally:
        _forget(board)


def _swap_netlist(ref: str, new: E.SynFootprint, value: str = "V", fields=()) -> str:
    """A netlist naming ``ref``'s footprint ``Swap:<new.name>``."""
    return E.netlist_text([E.NetComponent(ref, value, f"{_SWAP}:{new.name}", fields=fields)])


def _pads_at_zero(fp: E.SynFootprint) -> E.SynFootprint:
    """``fp`` with every pad at library angle 0 (bug 2 kept out, D4)."""
    return fp._replace(pads=tuple(p._replace(angle=0.0) for p in fp.pads))


def _kicads_swap(before: bytes, ref: str, library: Path, version: str) -> F.Footprint:
    """KiCad's exchange of footprint ``ref`` of the board ``before`` for the library file
    ``library`` (kicad_ref.exchange), read back as KiCad writes it, in the comparison
    form."""
    major = _major(version)
    copper = FJ.copper_count(before)
    old = FJ.read(before, ref, major=major)
    new = F.read_footprint(FJ.root(library.read_bytes()), major=major, copper_count=copper)
    return F.written(F.canonical(X.exchange(old, new, FJ.layer_table(before), major=major)))


def _swapped(
    old: E.SynFootprint,
    new: E.SynFootprint,
    theta: float,
    side: str,
    version: str,
    work: Path,
) -> tuple[bytes, bytes, Path, dict]:
    """(before, after, the new footprint's library file, the update's summary): a board
    holding ``old`` as KiCad places it at ``theta`` on ``side`` as U1, and U2 beside it,
    updated by a netlist that names ``new`` for U1 and leaves U2 out."""
    items = [
        E.board_footprint_text(old, "U1", _AT, theta, side=side, version=version),
        E.board_footprint_text(E.footprint_with_pad_at(0.0), "U2", _NEIGHBOUR, 30.0),
    ]
    board = _board(work / "start.kicad_pcb", items, version)
    pretty = E.write_library(work, new, library=_SWAP)
    before = board.read_bytes()
    summary = _update(board, _swap_netlist("U1", new), [pretty])
    return before, board.read_bytes(), pretty / f"{new.name}.kicad_mod", summary


def _pad_places(fp: F.Footprint, major: int) -> list[str]:
    """The footprint's position, angle and side, and every pad's number and board
    position (BOARD_ITEM::SetFPRelativePosition's arithmetic, kicad_ref.board_item)."""
    places = [f"footprint at {fp.position} angle {fp.orientation!r} on {fp.layer}"]
    for p in fp.pads:
        board = B.fp_relative_to_board(fp.position, fp.orientation, p.local, major=major)
        places.append(f"pad {p.number!r} at {board}")
    places[1:] = sorted(places[1:])
    return places


def _place_problems(got: F.Footprint, want: F.Footprint, major: int) -> list[str]:
    mine, theirs = _pad_places(got, major), _pad_places(want, major)
    return [f"written {m} where KiCad has {t}" for m, t in zip(mine, theirs) if m != t] + (
        [f"{len(mine)} places written where KiCad has {len(theirs)}"]
        if len(mine) != len(theirs)
        else []
    )


# ---------------------------------------------------------------------------
# geometry: position, angle and side kept
# ---------------------------------------------------------------------------


def _self_swap_problems(fp: E.SynFootprint, theta: float, side: str, version: str) -> list[str]:
    """A swap of ``fp`` (library pad angles 0) for the same footprint under another name:
    every pad's board position and angle and the footprint's position, angle and side
    read as they did before (the domain rule; positions through kicad_ref.board_item)."""
    fp = _pads_at_zero(fp)
    with tempfile.TemporaryDirectory() as work:
        before, after, _, _ = _swapped(fp, fp, theta, side, version, Path(work))
    major = _major(version)
    old, new = FJ.read(before, "U1", major=major), FJ.read(after, "U1", major=major)
    problems = _place_problems(new, old, major)
    angles, found = FJ.pair(new.pads, old.pads, lambda p: p.number, lambda p: p.local, "pad")
    problems += found
    problems += [
        f"pad {a.number!r}: angle {a.angle!r} where it stood at {b.angle!r}"
        for a, b in angles
        if a.angle != b.angle
    ]
    return problems


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(0.0), theta=90.0, version="k9")
@given(fp=E.synthetic_footprints(), theta=_HALF, version=_VERSIONS)
def test_self_swap_on_the_front_keeps_every_pad_in_place(fp, theta, version):
    """A front footprint KiCad holds at any angle that is a multiple of half a degree (so
    the tool's arithmetic is exact, bug 61 kept out) swapped for the same geometry under
    another library name: the footprint stays where it was, at its angle, on its side,
    and every pad reads at the board position and angle it had."""
    problems = _self_swap_problems(fp, theta, "F.Cu", version)
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(0.0), theta=180.0, version="k9")
@example(fp=E.footprint_with_pad_at(0.0), theta=0.0, version="k10")
@example(fp=E.footprint_with_pad_at(0.0), theta=150.0, version="k9")
@given(fp=E.synthetic_footprints(), theta=_HALF, version=_VERSIONS)
def test_self_swap_on_the_back_keeps_every_pad_in_place(fp, theta, version):
    """The same on the back: the footprint KiCad placed on the back at theta stays at
    theta and every pad where it was. The tool set the old angle and then flipped, which
    leaves the footprint at 180 minus theta, so only plus and minus 90 came back right."""
    problems = _self_swap_problems(fp, theta, "B.Cu", version)
    assert not problems, "\n".join(problems)


#: A footprint of other geometry than footprint_with_pad_at's: three pads, none on the x
#: axis, so a footprint at the wrong angle or side puts each elsewhere.
_OTHER = E.SynFootprint(
    "Other",
    (
        E.SynPad("1", (-2 * MM, MM), 0.0),
        E.SynPad("2", (2 * MM, -MM), 0.0),
        E.SynPad("3", (MM // 2, 2 * MM), 0.0),
    ),
    E.footprint_with_pad_at(0.0).texts,
)


def _geometry_problems(old, new, theta, side, version) -> list[str]:
    """A swap of ``old`` for ``new`` (library pad angles 0): the footprint's position,
    angle and side and every pad's board position are kicad_ref.exchange's."""
    with tempfile.TemporaryDirectory() as work:
        before, after, library, _ = _swapped(
            _pads_at_zero(old), _pads_at_zero(new), theta, side, version, Path(work)
        )
        want = _kicads_swap(before, "U1", library, version)
    major = _major(version)
    return _place_problems(FJ.read(after, "U1", major=major), want, major)


@pytest.mark.parametrize(
    "side",
    [
        pytest.param("F.Cu", id="front"),
        pytest.param("B.Cu", id="back"),
    ],
)
@settings(max_examples=sexpr_gen.budget(4))
@example(old=E.footprint_with_pad_at(0.0), new=_OTHER, theta=180.0, version="k9")
@given(
    old=E.synthetic_footprints(),
    new=E.synthetic_footprints(),
    theta=_HALF,
    version=_VERSIONS,
)
def test_swap_matches_kicads_exchange_geometry(side, old, new, theta, version):
    """A footprint KiCad holds at any angle that is a multiple of half a degree swapped for
    a footprint of other geometry: the new footprint sits where KiCad's exchange puts it,
    at the old position, angle and side, and every pad's board position is the
    exchange's (kicad_ref.exchange)."""
    problems = _geometry_problems(old, new, theta, side, version)
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# pads and texts turned with the footprint
# ---------------------------------------------------------------------------


def _pad_angle_problems(got: F.Footprint, want: F.Footprint) -> list[str]:
    pairs, problems = FJ.pair(
        got.pads,
        want.pads,
        lambda p: p.number,
        lambda p: p.local,
        "pad",
        lambda a, b: (abs(a.angle - b.angle), a.layers != b.layers),
    )
    return problems + [
        f"pad {a.number!r} at {a.local}: angle {a.angle!r} where KiCad has {b.angle!r}"
        for a, b in pairs
        if a.angle != b.angle
    ]


@pytest.mark.parametrize(
    ("side", "theta"),
    [
        pytest.param("F.Cu", None, id="front"),
        pytest.param("B.Cu", 90.0, id="back at 90"),
        pytest.param("B.Cu", -90.0, id="back at -90"),
    ],
)
@settings(max_examples=sexpr_gen.budget(4))
@example(old=E.footprint_with_pad_at(0.0), new=E.footprint_with_pad_at(90.0), turn=90.0)
@given(old=E.synthetic_footprints(), new=E.synthetic_footprints(), turn=_HALF)
def test_swap_composes_library_pad_angles(side, theta, old, new, turn):
    """A new footprint whose library pads stand at angles of their own, swapped in on the
    front at any angle that is a multiple of half a degree, or on the back at plus or
    minus 90 (where bug 7's angle agrees with KiCad's): every pad's absolute angle is the
    exchange's, its library angle turned with the footprint (and flipped with it on the
    back). The tool wrote the footprint's angle on every pad."""
    angle = turn if theta is None else theta
    new = new._replace(pads=(*new.pads, E.SynPad("90", (0, 3 * MM), 90.0)))
    with tempfile.TemporaryDirectory() as work:
        before, after, library, _ = _swapped(old, new, angle, side, "k9", Path(work))
        want = _kicads_swap(before, "U1", library, "k9")
    problems = _pad_angle_problems(FJ.read(after, "U1", major=9), want)
    assert not problems, "\n".join(problems)


def _text_problems(old: E.SynFootprint, new: E.SynFootprint, theta: float) -> list[str]:
    """A front swap of ``old`` at ``theta`` for ``new``: where the new footprint's fields
    and fp_text items differ from KiCad's exchange's (angle, keep-upright, justification,
    layer and position, in the comparison form, which holds KiCad 9's SetOrientation and
    KiCad 10's KeepUpright as one, D2), the mandatory fields its file leaves out aside."""
    with tempfile.TemporaryDirectory() as work:
        before, after, library, _ = _swapped(old, new, theta, "F.Cu", "k9", Path(work))
        want = _kicads_swap(before, "U1", library, "k9")
    got = FJ.read(after, "U1", major=9)
    return FJ.differences(got._replace(pads=()), want._replace(pads=()), written_fields_only=True)


@settings(max_examples=sexpr_gen.budget(4))
@example(old=E.footprint_with_pad_at(0.0), new=E.footprint_with_pad_at(0.0), theta=90.0)
@given(
    old=E.synthetic_footprints(),
    new=E.synthetic_footprints(),
    theta=G.angles().filter(lambda a: a % 360 != 0),
)
def test_swap_turns_texts_as_kicad_does(old, new, theta):
    """A front footprint at an angle that is not a whole turn swapped for another: every
    field and fp_text of the new footprint reads as KiCad's exchange leaves it, turned
    with the footprint (_text_problems). The tool left every text at its library angle."""
    problems = _text_problems(old, new, theta)
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@given(
    old=E.synthetic_footprints(),
    new=E.synthetic_footprints(),
    theta=st.sampled_from((0.0, 360.0, -360.0, 720.0)),
)
def test_swap_at_a_whole_turn_keeps_library_texts(old, new, theta):
    """The passing sibling (D5): at a whole turn KiCad's exchange leaves every text as the
    library has it, and so does the swap."""
    problems = _text_problems(old, new, theta)
    assert not problems, "\n".join(problems)


@settings(max_examples=sexpr_gen.budget(4))
@example(old=E.footprint_with_pad_at(0.0), new=E.footprint_with_pad_at(0.0), theta=90.0)
@given(
    old=E.synthetic_footprints(),
    new=E.synthetic_footprints(pad_details=True, primitives=True),
    theta=st.sampled_from((90.0, -90.0)),
)
def test_back_swap_at_90_flips_pads_as_kicad_does(old, new, theta):
    """A back footprint at plus or minus 90 (where bug 7's angle agrees with KiCad's)
    swapped for one whose pads, at library angle 0, are chamfered, trapezoids, offset or
    custom: every pad's angle, position, layers, chamfered corners, trapezoid delta, shape
    offset and primitives are KiCad's exchange's, PAD::Flip's on the new footprint (the
    flip slice's transform, reached by the swap)."""
    new = _pads_at_zero(new)
    with tempfile.TemporaryDirectory() as work:
        before, after, library, _ = _swapped(old, new, theta, "B.Cu", "k9", Path(work))
        want = _kicads_swap(before, "U1", library, "k9")
    got = FJ.read(after, "U1", major=9)
    problems = FJ.differences(
        got._replace(texts=()), want._replace(texts=()), details=True, copper_count=2
    )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("theta", [0.0, 90.0, 30.0])
@pytest.mark.parametrize("version", ["k9", "k10"])
def test_front_swap_writes_footprint_zones_in_board_coordinates(tmp_path, theta, version):
    """A front footprint at 0, 90 and 30 degrees swapped for edit_gen.ZONED, which owns
    zones on F.Cu, on every copper layer with a hole, on F.Cu and Dwgs.User and on F&B.Cu:
    every zone point reads in board coordinates where KiCad's exchange puts it (moved to
    the footprint and turned about it with the integer RotatePoint), each zone on its
    layers. The tool left the library's points at the origin."""
    old = E.footprint_with_pad_at(0.0)
    before, after, library, _ = _swapped(old, E.ZONED, theta, "F.Cu", version, tmp_path)
    want = _kicads_swap(before, "U1", library, version)
    got = FJ.read(after, "U1", major=_major(version))
    if len(got.zones) != len(want.zones):
        raise AssertionError(f"{len(got.zones)} zones written where KiCad has {len(want.zones)}")
    problems = FJ.differences(
        got._replace(pads=(), texts=()),
        want._replace(pads=(), texts=()),
        copper_count=FJ.copper_count(after),
    )
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# uuids and groups
# ---------------------------------------------------------------------------

#: A group in the layout KiCad 9.0.8's writer gives one (its multichannel_mixer demo),
#: "{members}" the quoted uuids it lists.
_GROUP = (
    '\t(group ""\n\t\t(uuid "00000000-0000-4000-8000-00000000c1c1")\n\t\t(members {members})\n\t)\n'
)


def _group_members(data: bytes) -> list[frozenset[str]]:
    """The uuids each top-level group of a board lists."""
    return [
        frozenset(t.value.decode() for t in FJ.child(item, b"members").items[1:])
        for item in F.children(FJ.root(data), b"group")
    ]


def _uuid_problems(before: bytes, after: bytes, library: Path, version: str) -> list[str]:
    """Where U1's uuids after a swap differ from the ones KiCad's exchange carries over
    (kicad_ref.exchange.carried_ids, footprint_judge.uuid_problems), and where the board's
    group no longer lists what it did or lists a uuid no footprint holds."""
    major = _major(version)
    copper = FJ.copper_count(before)
    old_group = FJ.footprint_group(before, "U1")
    old = FJ.read(before, "U1", major=major)
    new_group = FJ.root(library.read_bytes())
    port = X.exchange(
        old,
        F.read_footprint(new_group, major=major, copper_count=copper),
        FJ.layer_table(before),
        major=major,
    )
    old_ids = X.read_ids(old_group)
    problems = FJ.uuid_problems(
        FJ.read(after, "U1", major=major),
        X.read_ids(FJ.footprint_group(after, "U1")),
        port,
        X.carried_ids(old_group, old, new_group, port, copper),
        {u for u in (old_ids.footprint, *old_ids.pads, *old_ids.texts) if u},
    )
    if _group_members(after) != _group_members(before):
        problems.append(
            f"the group lists {_group_members(after)}, it listed {_group_members(before)}"
        )
    held = {X.read_ids(fp).footprint for fp in F.children(FJ.root(after), b"footprint")}
    for members in _group_members(after):
        if not members <= held:
            problems.append(f"the group lists {sorted(members - held)}, which no footprint holds")
    return problems


@settings(max_examples=sexpr_gen.budget(4))
@example(old=E.footprint_with_pad_at(0.0), new=E.footprint_with_pad_at(90.0), theta=30.0)
@given(old=E.synthetic_footprints(), new=E.synthetic_footprints(), theta=_HALF)
def test_swap_keeps_uuids_and_group_membership(old, new, theta):
    """A front footprint at any angle, listed in a group with its neighbour, swapped for
    another: the new footprint holds the old one's uuid, its Reference's and Value's, each
    other field's of the same name, each fp_text's getMatchingTextItem picks and each
    pad's of the same number (KiCad 9.0.8's rules, kicad_ref.exchange), and no other item
    takes an old uuid; the group lists what it listed, every uuid still a footprint's. The
    tool gave the footprint a new uuid and regenerated every other, so the group lost
    its member."""
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        u1 = E.board_footprint_text(old, "U1", _AT, theta)
        u2 = E.board_footprint_text(E.footprint_with_pad_at(0.0), "U2", _NEIGHBOUR, 30.0)
        ids = " ".join(f'"{X.read_ids(FJ.root(text.encode())).footprint}"' for text in (u1, u2))
        board = _board(work / "start.kicad_pcb", [u1, u2, _GROUP.format(members=ids)])
        pretty = E.write_library(work, new, library=_SWAP)
        before = board.read_bytes()
        _update(board, _swap_netlist("U1", new), [pretty])
        problems = _uuid_problems(
            before, board.read_bytes(), pretty / f"{new.name}.kicad_mod", "k9"
        )
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# locks
# ---------------------------------------------------------------------------

_LOCKED = [s for s, locked in E.LOCKS.items() if locked]
_UNLOCKED = [s for s, locked in E.LOCKS.items() if not locked]


def _locked_board(work: Path, spelling: str | None, *, theta: float = 30.0) -> Path:
    """U1 (footprint_with_pad_at(45), at theta, with the lock ``spelling`` or none) and U2,
    unlocked, beside it."""
    u1 = E.board_footprint_text(E.footprint_with_pad_at(45.0), "U1", _AT, theta)
    if spelling is not None:
        u1 = E.with_lock(u1, spelling)
    u2 = E.board_footprint_text(E.footprint_with_pad_at(0.0), "U2", _NEIGHBOUR, 0.0)
    return _board(work / "locks.kicad_pcb", [u1, u2])


def _fpid(data: bytes, ref: str) -> str:
    return FJ.footprint_group(data, ref).items[1].value.decode()


@pytest.mark.parametrize("spelling", _LOCKED)
def test_swap_leaves_a_locked_footprint_untouched(tmp_path, spelling):
    """A footprint KiCad reads as locked (kicad_ref.exchange.is_locked: a bare locked,
    (locked), (locked yes), (locked true)) whose symbol names another footprint: it keeps
    its footprint id, its uuid and every item as KiCad reads it, since the updater never
    replaces a locked footprint (pad net tokens may move, bug 22's subject). The tool
    replaced it."""
    board = _locked_board(tmp_path, spelling)
    before = board.read_bytes()
    if not X.is_locked(FJ.footprint_group(before, "U1")):
        raise RuntimeError(f"the premise fails: KiCad reads {spelling} as unlocked")
    pretty = E.write_library(tmp_path, _OTHER, library=_SWAP)
    _update(board, _swap_netlist("U1", _OTHER), [pretty])
    after = board.read_bytes()
    assert _fpid(after, "U1") == _fpid(before, "U1")
    assert (
        X.read_ids(FJ.footprint_group(after, "U1")).footprint
        == X.read_ids(FJ.footprint_group(before, "U1")).footprint
    )
    problems = FJ.differences(
        FJ.read(after, "U1"), FJ.read(before, "U1"), shapes=True, details=True
    )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("spelling", _LOCKED)
def test_locked_swap_is_reported_and_the_rest_still_updates(tmp_path, spelling):
    """N11. The same footprint, its symbol naming another footprint, a new value and a
    field the footprint lacks: the update lists it in skipped with the reason
    footprint_locked and reports incomplete, does not count it as swapped, and still
    writes the symbol's value and field onto the old footprint, as KiCad's updater does
    (BOARD_NETLIST_UPDATER, board_netlist_updater.cpp:353-363 and 1366-1383 at 9.0.8)."""
    board = _locked_board(tmp_path, spelling)
    before = board.read_bytes()
    pretty = E.write_library(tmp_path, _OTHER, library=_SWAP)
    summary = _update(
        board, _swap_netlist("U1", _OTHER, value="V2", fields=(("MPN", "X9"),)), [pretty]
    )
    after = board.read_bytes()
    assert {"ref": "U1", "reason": "footprint_locked"} in summary["skipped"], summary
    assert summary["status"] == "incomplete", summary
    assert "U1" not in summary["fpid_changed"], summary
    assert _fpid(after, "U1") == _fpid(before, "U1")
    fields = {
        item.items[1].value.decode(): item.items[2].value.decode()
        for item in F.children(FJ.footprint_group(after, "U1"), b"property")
    }
    assert (fields.get("Value"), fields.get("MPN")) == ("V2", "X9"), fields


@pytest.mark.parametrize("spelling", [*_UNLOCKED, None])
def test_an_unlocked_footprint_is_swapped(tmp_path, spelling):
    """The passing sibling: a footprint KiCad reads as unlocked ((locked no), (locked
    false) or no lock at all) is swapped, its footprint id the new one and its lock off,
    and the update counts it."""
    board = _locked_board(tmp_path, spelling)
    pretty = E.write_library(tmp_path, _OTHER, library=_SWAP)
    summary = _update(board, _swap_netlist("U1", _OTHER), [pretty])
    after = board.read_bytes()
    assert summary["fpid_changed"] == ["U1"], summary
    assert _fpid(after, "U1") == f"{_SWAP}:{_OTHER.name}"
    assert not X.is_locked(FJ.footprint_group(after, "U1"))


def _stale_removed(work: Path, spelling: str | None) -> bool:
    """Whether update with delete_stale removes U1, which the netlist leaves out, when it
    carries the lock ``spelling`` (U2 stays in the netlist)."""
    board = _locked_board(work, spelling, theta=0.0)
    netlist = E.netlist_text([E.NetComponent("U2", "V", "Ladder:Syn")])
    summary = _update(board, netlist, [], delete_stale=True)
    if summary["stale_footprints"] != ["U1"]:
        raise RuntimeError(f"U1 was not found stale: {summary}")
    removed = "U1" in summary["stale_removed"]
    on_board = [FJ.reference_of(fp) for fp in F.children(FJ.root(board.read_bytes()), b"footprint")]
    if removed == ("U1" in on_board):
        raise RuntimeError(f"the summary and the board disagree: {summary}, {on_board}")
    return removed


@pytest.mark.parametrize(
    "spelling",
    _LOCKED,
)
def test_stale_removal_keeps_a_locked_footprint(tmp_path, spelling):
    """delete_stale keeps a stale footprint KiCad reads as locked, as KiCad's updater
    does ("Could not remove unused footprint ... (footprint is locked)",
    board_netlist_updater.cpp:1438-1454 at 9.0.8). The tool removed one locked by the bare
    atom KiCad 6 and 7 wrote."""
    assert not _stale_removed(tmp_path, spelling)


@pytest.mark.parametrize(
    "spelling",
    [*_UNLOCKED, None],
)
def test_stale_removal_removes_an_unlocked_footprint(tmp_path, spelling):
    """delete_stale removes a stale footprint KiCad reads as unlocked: (locked no),
    (locked false), or no lock at all. The tool kept one with (locked no) or (locked
    false), taking any (locked) list for a lock."""
    assert _stale_removed(tmp_path, spelling)


# ---------------------------------------------------------------------------
# constructs the swap does not port (N4, the swap rows)
# ---------------------------------------------------------------------------

#: The constructs every edit refuses, swapped in on the front; the rest only a flip
#: refuses, swapped in on the back.
_SWAP_CONSTRUCTS = [
    (construct, "F.Cu" if construct in E.MOVED_CONSTRUCTS else "B.Cu") for construct in E.CONSTRUCTS
]


def _swap_construct_params():
    for construct, side in _SWAP_CONSTRUCTS:
        marks = []
        if construct == "future_item":
            marks.append(pytest.mark.kicad_placeholder("Constructs.pretty/Host.kicad_mod"))
        yield pytest.param(construct, side, id=f"{construct}-{side}", marks=marks)


@pytest.mark.parametrize(("construct", "side"), list(_swap_construct_params()))
def test_unported_constructs_in_a_swap_are_refused(tmp_path, construct, side):
    """N4's swap rows: a symbol whose new footprint carries a construct the tool does not
    port (one every edit refuses, swapped in on the front: a dimension, image, table,
    barcode or a child no KiCad knows; one only a flip refuses, swapped in on the back: a
    text box, a footprint stackup, a pad's own padstack, KiCad 10's back drills or post
    machining): the update keeps the old footprint as KiCad reads it, with its footprint
    id and uuid, lists it in skipped with a reason naming the construct, and reports
    incomplete. The tool swapped the footprint in and moved or flipped it around the
    construct."""
    version = E.construct_version(construct)
    old = E.board_footprint_text(E.footprint_with_pad_at(0.0), "U1", _AT, 30.0, side=side)
    board = _board(tmp_path / "board.kicad_pcb", [old], version, copper=4)
    pretty = E.construct_library(tmp_path, construct)
    before = board.read_bytes()
    netlist = E.netlist_text([E.NetComponent("U1", "V", f"{pretty.stem}:Host")])
    summary = _update(board, netlist, [pretty])
    after = board.read_bytes()
    reasons = [s["reason"] for s in summary["skipped"] if s["ref"] == "U1"]
    assert any(construct in reason for reason in reasons), summary
    assert summary["status"] == "incomplete", summary
    assert _fpid(after, "U1") == _fpid(before, "U1")
    major = _major(version)
    problems = FJ.differences(
        FJ.read(after, "U1", major=major), FJ.read(before, "U1", major=major), shapes=True
    )
    assert not problems, "\n".join(problems)


#: A footprint with a text on In1.Cu, which KiCad 9 keeps there when it flips the
#: footprint on a board of four copper layers and KiCad 10 moves to In2.Cu.
_INNER_TEXT = E.footprint_with_pad_at(0.0)._replace(
    name="Inner",
    texts=(
        *E.footprint_with_pad_at(0.0).texts,
        E.SynText("fp_text", "user", "IN1", (0, 3 * MM), 0.0, True, 0, 0, False, "In1.Cu"),
    ),
)


@pytest.mark.parametrize("version", ["k9", "k10"])
def test_inner_copper_swap_on_the_back_is_refused(tmp_path, version):
    """N4's inner-copper row: on a board of four copper layers, a back footprint whose
    symbol names a footprint with a text on In1.Cu, which KiCad 9 and 10 flip apart: the
    update keeps the old footprint, with its footprint id, and lists it in skipped with a
    reason naming In1.Cu. The tool swapped the footprint in and flipped it."""
    old = E.board_footprint_text(E.footprint_with_pad_at(0.0), "U1", _AT, 30.0, side="B.Cu")
    board = _board(tmp_path / "board.kicad_pcb", [old], version, copper=4)
    pretty = E.write_library(tmp_path, _INNER_TEXT, library=_SWAP)
    before = board.read_bytes()
    summary = _update(board, _swap_netlist("U1", _INNER_TEXT), [pretty])
    after = board.read_bytes()
    reasons = [s["reason"] for s in summary["skipped"] if s["ref"] == "U1"]
    assert any("In1.Cu" in reason for reason in reasons), summary
    assert _fpid(after, "U1") == _fpid(before, "U1")


# ---------------------------------------------------------------------------
# byte preservation (N15)
# ---------------------------------------------------------------------------

_NETS = ("N1", "N2")
_LINK = ("00000000-0000-4000-8000-0000000000a3", "a.kicad_sch")


def _guard_board(work: Path, old: E.SynFootprint, theta: float, side: str, version: str) -> Path:
    """U1 (to be swapped), U2 (left out of the netlist, so stale), U3 (in the netlist under
    its own footprint id, linked to its symbol as the update links it), a track on N1 and
    a group listing U1 and U2, on a board whose KiCad 9 net table declares the netlist's
    nets."""
    u1 = E.board_footprint_text(old, "U1", _AT, theta, side=side, version=version)
    u2 = E.board_footprint_text(E.footprint_with_pad_at(0.0), "U2", _NEIGHBOUR, 30.0)
    u3 = E.board_footprint_text(
        E.footprint_with_pad_at(0.0),
        "U3",
        (50 * MM, 80 * MM),
        0.0,
        path=f"/{_LINK[0]}",
        sheetname="/",
        sheetfile=_LINK[1],
    )
    net = "(net 1)" if version == "k9" else '(net "N1")'
    track = (
        f"\t(segment\n\t\t(start 10 10)\n\t\t(end 20 10)\n\t\t(width 0.25)\n"
        f'\t\t(layer "F.Cu")\n\t\t{net}\n'
        f'\t\t(uuid "00000000-0000-4000-8000-0000000000a4")\n\t)\n'
    )
    ids = " ".join(f'"{X.read_ids(FJ.root(t.encode())).footprint}"' for t in (u1, u2))
    text = E.layered_board_text([u1, u2, u3, track, _GROUP.format(members=ids)], version=version)
    if version == "k9":
        rows = "".join(f'\t(net {k} "{name}")\n' for k, name in enumerate(_NETS, start=1))
        text = text.replace('\t(net 0 "")\n', '\t(net 0 "")\n' + rows, 1)
    path = work / "guard.kicad_pcb"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _without_nets(data: bytes) -> bytes:
    """``data`` with every (net ...) list and the whitespace before it removed."""
    out, cursor = bytearray(), 0
    for location, item in J.walk(data):
        if (
            item.items is not None
            and item.head == "net"
            and location[-2:-1]
            and location[-2][1] == "pad"
        ):
            start = item.start
            while start > 0 and data[start - 1 : start] in (b" ", b"\t", b"\n", b"\r"):
                start -= 1
            out += data[cursor:start]
            cursor = item.end
    return bytes(out + data[cursor:])


def _span(data: bytes, ref: str) -> tuple[int, int]:
    group = FJ.footprint_group(data, ref)
    return group.start, group.end


@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.footprint_with_pad_at(45.0), theta=180.0, side="B.Cu", version="k9")
@given(
    fp=E.synthetic_footprints(),
    theta=G.angles(),
    side=st.sampled_from(("F.Cu", "B.Cu")),
    version=_VERSIONS,
)
def test_swap_changes_only_the_swapped_footprint(fp, theta, side, version):
    """N15, a guard that holds before and after the fix: a swap changes the swapped
    footprint and, of everything else on the board, only the (net ...) of the pads of the
    other footprint the netlist names (the pad pass rebinds those, bug 22). The stale
    footprint, the track, the group and the net table keep every byte, and the other
    footprint keeps every byte but its pads' nets."""
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        board = _guard_board(work, fp, theta, side, version)
        pretty = E.write_library(work, E.footprint_with_pad_at(90.0), library=_SWAP)
        before = board.read_bytes()
        netlist = E.netlist_text(
            [
                E.NetComponent("U1", "V", f"{_SWAP}:Syn"),
                E.NetComponent("U3", "V", "Ladder:Syn", _LINK[0], sheetfile=_LINK[1]),
            ],
            [("N1", [("U1", "1"), ("U3", "1")]), ("N2", [("U1", "2"), ("U3", "2")])],
        )
        summary = _update(board, netlist, [pretty])
        after = board.read_bytes()
    if summary["fpid_changed"] != ["U1"]:
        raise RuntimeError(f"the premise fails: U1 was not swapped: {summary}")
    b1, a1 = _span(before, "U1"), _span(after, "U1")
    b3, a3 = _span(before, "U3"), _span(after, "U3")
    if not b1[1] < b3[0]:
        raise RuntimeError("the guard board no longer holds U1 before U3")
    outside_before = before[: b1[0]] + before[b1[1] : b3[0]] + before[b3[1] :]
    outside_after = after[: a1[0]] + after[a1[1] : a3[0]] + after[a3[1] :]
    assert outside_after == outside_before, "a byte outside U1 and U3 changed"
    u3_before, u3_after = before[b3[0] : b3[1]], after[a3[0] : a3[1]]
    assert _without_nets(u3_after) == _without_nets(u3_before), "U3 changed beyond its pad nets"


# ---------------------------------------------------------------------------
# against pcbnew
# ---------------------------------------------------------------------------

#: Stock footprints a symbol's footprint changes between whose library pads stand at 0,
#: none with two pads of one number (KiCad 10 matches those by similarity): SMD, a
#: through-hole pair, and a non-plated hole without a number to a plated pad.
PCBNEW_PAIRS = (
    ("R_0603_1608Metric", "R_0805_2012Metric"),
    ("SOT-23", "SOT-23-3"),
    ("D_SOD-123", "D_SOD-323"),
    ("LED_0603_1608Metric", "LED_0805_2012Metric"),
    ("PinHeader_1x02_P2.54mm_Vertical", "PinSocket_1x02_P2.54mm_Vertical"),
    ("MountingHole_3.2mm_M3", "MountingHole_3.2mm_M3_Pad"),
)
_PCBNEW_ANGLES = (0.0, 90.0, 180.0, 150.0)
_SIDES = ("F.Cu", "B.Cu")


def _slot(index: int) -> tuple[int, int]:
    return (25 * MM * (1 + index % 8), 25 * MM * (1 + index // 8))


def _tolerance(angle: float) -> int:
    return 0 if angle % 90 == 0 else FJ.ROTATION_TOLERANCE_NM


def _turn(angle: float, side: str):
    return [("rotate", angle)] if side == "F.Cu" else [("flip", None), ("rotate_to", angle)]


def _ports_every_construct(path: Path) -> bool:
    """Whether a footprint file holds none of the constructs the swap refuses
    (edit_gen.FOOTPRINT_CONSTRUCTS and PAD_CONSTRUCTS, N4's subject)."""
    group = FJ.root(path.read_bytes())
    children = {name.encode() for name in E.FOOTPRINT_CONSTRUCTS}
    tokens = {name.encode() for name in E.PAD_CONSTRUCTS}
    if any(F.head(item) in children for item in group.items):
        return False
    return not any(F.head(item) in tokens for pad in F.pad_groups(group) for item in pad.items)


def _swap_runs(pairs, cases, work: Path) -> list[tuple[str, str, float, str, bytes, bytes]]:
    """``pairs`` holds (old, new) footprint files. For each (pair index, angle, side):
    pcbnew places the pair's old footprint at the
    angle on that side and saves the board; the tool swaps every footprint for the pair's
    new one on a copy (a netlist naming the new footprint ids), and pcbnew replays the
    exchange on another (pcbnew_fp_ops.exchange_job). Returns (reference, what, angle,
    side, the tool's board, pcbnew's board) per footprint."""
    stock = list(pairs)
    empty = _board(work / "empty.kicad_pcb")
    groups: dict[tuple[float, str], list] = {}
    for index, (k, angle, side) in enumerate(cases):
        groups.setdefault((angle, side), []).append((f"U{index + 1}", k))
    starts, jobs = [], []
    for g, ((angle, side), items) in enumerate(groups.items()):
        start = work / f"start{g}.kicad_pcb"
        starts.append(start)
        place = [
            OPS.place_item(stock[k][0].parent, stock[k][0].stem, ref, _slot(i), _turn(angle, side))
            for i, (ref, k) in enumerate(items)
        ]
        jobs.append(OPS.place_job(empty, place, start))
    OPS.run(jobs)
    jobs, outs, tools = [], [], []
    for g, ((angle, side), items) in enumerate(groups.items()):
        out = work / f"kicad{g}.kicad_pcb"
        outs.append(out)
        jobs.append(
            OPS.exchange_job(
                starts[g],
                [OPS.exchange_item(ref, stock[k][1].parent, stock[k][1].stem) for ref, k in items],
                out,
            )
        )
        tool = work / f"tool{g}.kicad_pcb"
        shutil.copyfile(starts[g], tool)
        netlist = E.netlist_text(
            [
                E.NetComponent(ref, "V", f"{stock[k][1].parent.stem}:{stock[k][1].stem}")
                for ref, k in items
            ]
        )
        _update(tool, netlist, sorted({stock[k][1].parent for _, k in items}))
        tools.append(tool)
    OPS.run(jobs)
    runs = []
    for g, ((angle, side), items) in enumerate(groups.items()):
        mine, theirs = tools[g].read_bytes(), outs[g].read_bytes()
        for ref, k in items:
            what = f"{stock[k][0].stem} to {stock[k][1].stem} on {side} at {angle}"
            runs.append((ref, what, angle, side, mine, theirs))
    return runs


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    cases = [
        (k, angle, side)
        for side in _SIDES
        for angle in _PCBNEW_ANGLES
        for k in range(len(PCBNEW_PAIRS))
    ]
    pairs = [(E.stock_footprint(a), E.stock_footprint(b)) for a, b in PCBNEW_PAIRS]
    return _swap_runs(pairs, cases, tmp_path_factory.mktemp("swap"))


def _pcbnew_problems(runs, side: str, part: str) -> list[str]:
    """Where the tool's swapped footprints differ from pcbnew's replay, per ``part``: the
    footprint's geometry and every pad's angle and position ("pads"), the geometry and
    pad positions only ("geometry"), or the texts ("texts", at angles that are not 0)."""
    problems = []
    major = PJ.major()
    for ref, what, angle, run_side, mine, theirs in runs:
        if run_side != side or (part == "texts" and angle == 0):
            continue
        got, want = FJ.read(mine, ref, major=major), FJ.read(theirs, ref, major=major)
        if part == "texts":
            found = FJ.differences(
                got._replace(pads=()),
                want._replace(pads=()),
                tolerance_nm=_tolerance(angle),
                written_fields_only=True,
            )
        else:
            found = FJ.differences(
                got._replace(texts=()),
                want._replace(texts=()),
                tolerance_nm=_tolerance(angle),
                angles=part == "pads",
                copper_count=FJ.copper_count(mine),
            )
        problems += [f"{what}: {p}" for p in found]
    return problems


@pytest.mark.parametrize(
    ("side", "part"),
    [
        pytest.param("F.Cu", "pads", id="front pads"),
        pytest.param("B.Cu", "geometry", id="back geometry"),
        pytest.param("F.Cu", "texts", id="front texts"),
    ],
)
def test_swap_matches_pcbnew_exchange(runs, side, part):
    """One item per part: PCBNEW_PAIRS, the old footprint as pcbnew placed it at 0, 90,
    180 and 150 degrees, swapped by the update and by pcbnew's replay of
    ExchangeFootprint: on the front every pad's angle and position and the footprint's
    position, angle and side read as pcbnew's ("front pads"); on the back the footprint's
    geometry and every pad's position ("back geometry"); on the front at 90, 180 and 150
    every text in the comparison form ("front texts"). Positions within two nanometres
    off the quarter turns."""
    problems = _pcbnew_problems(runs, side, part)
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def test_pcbnew_pairs_exercise_what_the_swap_tests_need():
    """The premise of the pcbnew tests above: every pair's library pads stand at 0 and
    carry no two pads of one number, as KiCad reads them. Raises RuntimeError, never
    AssertionError, when the install no longer does."""
    for pair in PCBNEW_PAIRS:
        for name in pair:
            fp = FJ.read_library(E.stock_footprint(name).read_bytes())
            numbers = [p.number for p in fp.pads if p.number]
            if any(p.angle != 0 for p in fp.pads) or len(numbers) != len(set(numbers)):
                raise RuntimeError(f"the installed {name} no longer fits PCBNEW_PAIRS")


@pytest.mark.nightly
@settings(max_examples=sexpr_gen.budget(25))
@example(picks=[(0, 1, 180.0)])
@given(
    st.lists(
        st.tuples(st.integers(0, 10**9), st.integers(0, 10**9), G.angles()),
        min_size=1,
        max_size=6,
    )
)
def test_nightly_swap_matches_pcbnew(picks):
    """Sampled stock footprints without a zone, the old one as pcbnew placed it on the
    back at a generated angle, swapped for another by the update and by pcbnew's replay:
    the footprint's geometry and every pad's position read as pcbnew's. The explicit
    example swaps two stock footprints at 180, which the tool left at 0. A new footprint
    carrying a construct the swap refuses is N4's subject, not this test's."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    stock = E.stock_footprints_for_turns()
    if not stock:
        pytest.skip("no stock footprint libraries")
    pairs = [(stock[a % len(stock)], stock[b % len(stock)]) for a, b, _ in picks]
    assume(all(_ports_every_construct(new) for _, new in pairs))
    cases = [(k, angle, "B.Cu") for k, (_, _, angle) in enumerate(picks)]
    with tempfile.TemporaryDirectory() as work:
        found = _swap_runs(pairs, cases, Path(work))
    problems = _pcbnew_problems(found, "B.Cu", "geometry")
    assert not problems, "\n".join(problems[:40])
