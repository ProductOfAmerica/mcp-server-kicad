"""Rung 3: library footprints in KiCad 7's form through the board update and placement
(bug 91), judged by KiCad.

Code under test: pcb._apply_netlist_cst, the board update behind update_pcb_from_schematic
(its swap, its add, the value it writes on a footprint already on the board, its stale
report and removal), and pcb.place_footprint, given library footprints written before
format 20230620 ("PCB Fields", pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.h:147 at
9.0.8, 153 at 10.0.7), whose Reference and Value are (fp_text reference ...) and (fp_text
value ...) (edit_gen.legacy_library_text), and board footprints holding those two in that
form (edit_gen.board_footprint_text with legacy_fields).

KiCad's rules:

- its parser reads a legacy (fp_text reference|value ...) as the Reference or the Value
  field at either major and any format version (kicad_ref.footprint.reference_and_value,
  checked against pcbnew in test_ladder_r3_judges.py);
- the updater gives every footprint the netlist names the component's reference and
  value, a swapped or an added one included (updateFootprintParameters,
  pcbnew/netlist_reader/board_netlist_updater.cpp:400-439 at 9.0.8, called at 1380 and
  1403; 427-474 at 10.0.7, called at 2310); it matches a footprint to its component by
  the reference it reads from the footprint (1363 at 9.0.8, 2136 at 10.0.7) and takes a
  footprint for unused, and removes it when asked, when no component has that reference
  (1433-1436 at 9.0.8, 2348-2362 at 10.0.7);
- place_footprint names the reference and the value the placed footprint reads with.

Judges, none of them the code under test:

- pcbnew replaying the exchange and the placement with the symbols' values
  (pcbnew_fp_ops.exchange_job and place_job), on KiCad 9 boards, and on KiCad 10 boards
  where pcbnew is 10;
- the rules above, every board read through the lexer port with
  kicad_ref.footprint.reference_and_value, on boards of both majors whatever KiCad is
  installed;
- edit_judge for bytes: a value the update writes on a footprint already on the board
  changes that footprint's Value text atom and no other byte (atom_changes); and two
  placements, additions or swaps of one library footprint that differ only in the
  reference and value asked for differ only in the Reference's and the Value's own text
  atoms, the legacy fp_text the library writes, besides the fresh uuid a placed or added
  footprint takes.

Bug 91's tests are strict xfails: the tool writes a reference and a value only into a
(property "Reference"|"Value" ...), which a footprint in KiCad 7's form does not have, so
it keeps REF** and its library's value and reads as no symbol's footprint.
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
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import footprint as F

from mcp_server_kicad import pcb

MM = 1_000_000
_VERSIONS = ("k9", "k10")
_AT = (50 * MM, 40 * MM)
_SECOND = (90 * MM, 40 * MM)
#: The board's own footprints come from "Ladder" (edit_gen.board_footprint_text); the
#: legacy library footprint from "Leg".
_OLD = E.footprint_with_pad_at(0.0)
_OLD_ID = f"Ladder:{_OLD.name}"
_LEG = "Leg"
_LEGACY_ID = f"{_LEG}:{E.LEGACY.name}"
#: A symbol's uuid and sheet file, linked on a footprint as the update links it
#: (test_ladder_r3_swap's guard board).
_LINK = ("00000000-0000-4000-8000-0000000000a3", "a.kicad_sch")


def _forget(path: Path) -> None:
    """Drop the board pcb._open_pcb_cst cached for ``path``."""
    pcb._BOARD_CACHE.pop(str(Path(path).resolve()), None)


def _board(path: Path, items=(), version: str = "k9") -> Path:
    path.write_text(E.layered_board_text(items, version=version), encoding="utf-8", newline="\n")
    return path


def _netlist(components) -> str:
    """A netlist naming each (reference, value, footprint id) of ``components``."""
    return E.netlist_text([E.NetComponent(ref, value, fpid) for ref, value, fpid in components])


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


def _place(board: Path, ref: str, value: str, pretty: Path, *, layer: str = "F.Cu") -> None:
    """place_footprint of edit_gen.LEGACY from ``pretty`` at _AT and 30 degrees."""
    try:
        pcb.place_footprint(
            ref,
            value,
            _AT[0] / MM,
            _AT[1] / MM,
            rotation=30.0,
            layer=layer,
            library=str(pretty),
            footprint=E.LEGACY.name,
            pcb_path=str(board),
        )
    finally:
        _forget(board)


def _designators(data: bytes) -> list[tuple[str, str]]:
    """Every footprint of the board ``data`` as (Reference, Value) as KiCad reads them
    (kicad_ref.footprint.reference_and_value), sorted."""
    return sorted(F.reference_and_value(fp) for fp in F.children(FJ.root(data), b"footprint"))


def _pcbnew_for(version: str) -> None:
    """Skip without a pcbnew, and for a KiCad 10 board below pcbnew 10, which cannot load
    one."""
    reason = PJ.skip_reason()
    if reason:
        pytest.skip(reason)
    if version == "k10" and PJ.major() < 10:
        pytest.skip(f"pcbnew {PJ.major()} cannot load a KiCad 10 board")


# ---------------------------------------------------------------------------
# against pcbnew
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 91: the swap writes the reference and value into no legacy fp_text, so the"
    " footprint keeps REF** and its library value; fixed by step 7a",
)
@pytest.mark.parametrize("version", _VERSIONS)
def test_swap_to_a_legacy_footprint_matches_pcbnew(tmp_path, version):
    """U1 on the front at 30 and U2 on the back at 90, whose symbols now name
    edit_gen.LEGACY in KiCad 7's form, swapped by the update and by pcbnew's replay of the
    exchange with the symbols' values (pcbnew_fp_ops.exchange_job), on a board of each
    major pcbnew loads: every footprint reads with the Reference and the Value pcbnew's
    exchange leaves it with, U1 V1 and U2 V2, as KiCad reads them. The tool left both at
    REF** and the library's value, Legacy."""
    _pcbnew_for(version)
    items = [
        E.board_footprint_text(_OLD, "U1", _AT, 30.0, version=version),
        E.board_footprint_text(_OLD, "U2", _SECOND, 90.0, side="B.Cu", version=version),
    ]
    start = _board(tmp_path / "start.kicad_pcb", items, version)
    pretty = E.write_legacy_library(tmp_path, E.LEGACY, library=_LEG)
    kicad = tmp_path / "kicad.kicad_pcb"
    swaps = [
        OPS.exchange_item(r, pretty, E.LEGACY.name, value=v)
        for r, v in (("U1", "V1"), ("U2", "V2"))
    ]
    OPS.run([OPS.exchange_job(start, swaps, kicad)])
    tool = tmp_path / "tool.kicad_pcb"
    shutil.copyfile(start, tool)
    _update(tool, _netlist([("U1", "V1", _LEGACY_ID), ("U2", "V2", _LEGACY_ID)]), [pretty])
    assert _designators(tool.read_bytes()) == _designators(kicad.read_bytes())


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 91: the add writes the reference and value into no legacy fp_text, so the"
    " footprint keeps REF** and its library value; fixed by step 7a",
)
@pytest.mark.parametrize("version", _VERSIONS)
def test_add_of_a_legacy_footprint_matches_pcbnew(tmp_path, version):
    """A symbol U3 new to an empty board, its footprint edit_gen.LEGACY in KiCad 7's form,
    added by the update and placed by pcbnew with the symbol's reference and value
    (FootprintLoad, SetReference, SetValue: pcbnew_fp_ops.place_job, what the updater's
    addNewFootprint and updateFootprintParameters do), on a board of each major pcbnew
    loads: the footprint reads U3 and V3 as KiCad reads them, as pcbnew's does. The tool
    left REF** and the library's value."""
    _pcbnew_for(version)
    empty = _board(tmp_path / "empty.kicad_pcb", (), version)
    pretty = E.write_legacy_library(tmp_path, E.LEGACY, library=_LEG)
    kicad = tmp_path / "kicad.kicad_pcb"
    placed = OPS.place_item(pretty, E.LEGACY.name, "U3", _AT, value="V3")
    OPS.run([OPS.place_job(empty, [placed], kicad)])
    tool = tmp_path / "tool.kicad_pcb"
    shutil.copyfile(empty, tool)
    _update(tool, _netlist([("U3", "V3", _LEGACY_ID)]), [pretty])
    assert _designators(tool.read_bytes()) == _designators(kicad.read_bytes())


# ---------------------------------------------------------------------------
# the updater's rules, on boards of both majors
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 91: the swapped and the added legacy footprint keep REF** and their library"
    " value; fixed by step 7a",
)
@settings(max_examples=sexpr_gen.budget(4))
@example(fp=E.LEGACY, theta=30.0, side="F.Cu", version="k9")
@example(fp=E.LEGACY, theta=90.0, side="B.Cu", version="k10")
@given(
    fp=E.legacy_footprints(),
    theta=G.half_degree_angles(),
    side=st.sampled_from(("F.Cu", "B.Cu")),
    version=st.sampled_from(_VERSIONS),
)
def test_update_gives_legacy_footprints_their_symbols_designators(fp, theta, side, version):
    """A generated library footprint in KiCad 7's form (edit_gen.legacy_footprints), the
    footprint of two symbols: U1, whose board footprint stands at theta on either side and
    is swapped, and U2, new to the board and added, on a board of either major: every
    footprint then reads with its symbol's reference and value as KiCad reads them, U1 V1
    and U2 V2 (updateFootprintParameters). The tool left both at REF** and the library's
    value."""
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        u1 = E.board_footprint_text(_OLD, "U1", _AT, theta, side=side, version=version)
        board = _board(work / "start.kicad_pcb", [u1], version)
        pretty = E.write_legacy_library(work, fp, library=_LEG)
        fpid = f"{_LEG}:{fp.name}"
        _update(board, _netlist([("U1", "V1", fpid), ("U2", "V2", fpid)]), [pretty])
        got = _designators(board.read_bytes())
    assert got == [("U1", "V1"), ("U2", "V2")]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 91: the swapped and the added legacy footprint read as REF**, so a second"
    " update adds both again and finds every footprint stale; fixed by step 7a",
)
@pytest.mark.parametrize("version", _VERSIONS)
def test_a_second_identical_update_adds_nothing_and_finds_nothing_stale(tmp_path, version):
    """The update run twice with one netlist, which swaps U1 to edit_gen.LEGACY in KiCad
    7's form and adds U2 on it: the second run adds nothing, swaps nothing and finds
    nothing stale, since KiCad matches every footprint to its symbol by the reference it
    reads, and the board holds U1 V1 and U2 V2 and nothing else. The tool's second run
    added U1 and U2 again and reported four footprints REF** stale."""
    u1 = E.board_footprint_text(_OLD, "U1", _AT, 30.0, version=version)
    board = _board(tmp_path / "board.kicad_pcb", [u1], version)
    pretty = E.write_legacy_library(tmp_path, E.LEGACY, library=_LEG)
    netlist = _netlist([("U1", "V1", _LEGACY_ID), ("U2", "V2", _LEGACY_ID)])
    _update(board, netlist, [pretty])
    again = _update(board, netlist, [pretty])
    found = (again["added"], again["fpid_changed"], again["stale_footprints"])
    assert found == ([], [], []), again
    assert _designators(board.read_bytes()) == [("U1", "V1"), ("U2", "V2")]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 91: delete_stale removes the swapped and the added legacy footprint, which"
    " read as REF**; fixed by step 7a",
)
@pytest.mark.parametrize("version", _VERSIONS)
def test_delete_stale_removes_only_the_footprint_no_symbol_names(tmp_path, version):
    """U1, whose symbol now names edit_gen.LEGACY in KiCad 7's form, and U9, which no
    symbol names, updated with delete_stale by a netlist that also adds U2 on LEGACY: the
    update removes U9 and nothing else, and the board holds U1 V1 and U2 V2. The tool
    removed the swapped U1 and the added U2 as well, leaving no footprint at all."""
    items = [
        E.board_footprint_text(_OLD, "U1", _AT, 30.0, version=version),
        E.board_footprint_text(_OLD, "U9", _SECOND, 0.0, version=version),
    ]
    board = _board(tmp_path / "board.kicad_pcb", items, version)
    pretty = E.write_legacy_library(tmp_path, E.LEGACY, library=_LEG)
    netlist = _netlist([("U1", "V1", _LEGACY_ID), ("U2", "V2", _LEGACY_ID)])
    summary = _update(board, netlist, [pretty], delete_stale=True)
    assert summary["stale_removed"] == ["U9"], summary
    assert _designators(board.read_bytes()) == [("U1", "V1"), ("U2", "V2")]


# ---------------------------------------------------------------------------
# a footprint already on the board
# ---------------------------------------------------------------------------


def _is_value_text(item) -> bool:
    """Whether a lexed item is a footprint's Value: (property "Value" ...) or a legacy
    (fp_text value ...)."""
    if item.items is None or len(item.items) < 3:
        return False
    return (item.head, item.items[1].label) in (("property", "Value"), ("fp_text", "value"))


@pytest.mark.parametrize("version", _VERSIONS)
@pytest.mark.parametrize(
    "form",
    [
        pytest.param("property", id="property"),
        pytest.param(
            "fp_text",
            id="fp_text",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="bug 91: the update writes a new value into no legacy (fp_text value"
                " ...); fixed by step 7a",
            ),
        ),
    ],
)
def test_a_new_value_changes_only_the_footprints_value_text(tmp_path, form, version):
    """A footprint already on the board, linked to its symbol as the update links it,
    whose symbol's value changed and whose footprint id did not, on a board of either
    major: its Value reads the new value as KiCad reads it, the update reports it in
    value_updated, and the board changes in that one text atom and in no other byte
    (edit_judge.atom_changes). That atom is the Value's (property ...) for a footprint in
    KiCad 9's form (the passing row) and its legacy (fp_text value ...) for one whose
    Reference and Value are legacy fp_text, which KiCad reads as the same field. The tool
    wrote no value into the legacy fp_text and changed nothing."""
    u1 = E.board_footprint_text(
        _OLD,
        "U1",
        _AT,
        30.0,
        version=version,
        path=f"/{_LINK[0]}",
        sheetname="/",
        sheetfile=_LINK[1],
        legacy_fields=form == "fp_text",
    )
    board = _board(tmp_path / "board.kicad_pcb", [u1], version)
    before = board.read_bytes()
    paths = J.find_paths(before, lambda _, item: _is_value_text(item))
    if len(paths) != 1:
        raise RuntimeError(f"the board holds {len(paths)} Value texts, not one")
    netlist = E.netlist_text([E.NetComponent("U1", "NEWV", _OLD_ID, _LINK[0], sheetfile=_LINK[1])])
    summary = _update(board, netlist, [])
    after = board.read_bytes()
    assert _designators(after) == [("U1", "NEWV")]
    assert summary["value_updated"] == ["U1"], summary
    assert J.atom_changes(before, after) == [J.AtomChange(paths[0], 2, b'"V"', b'"NEWV"')]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 91: place_footprint writes the reference and value into no legacy fp_text,"
    " so the footprint keeps REF** and its library value; fixed by step 7a",
)
@pytest.mark.parametrize("version", _VERSIONS)
@pytest.mark.parametrize("side", ["F.Cu", "B.Cu"])
def test_place_footprint_gives_a_legacy_footprint_its_reference_and_value(tmp_path, side, version):
    """place_footprint of edit_gen.LEGACY, in KiCad 7's form, as U9 with the value V9 at
    30 degrees on either side of a board of either major: the board gains that one
    footprint and nothing else (edit_judge.assert_inserted), and it reads U9 and V9 as
    KiCad reads them. The tool left REF** and the library's value."""
    board = _board(tmp_path / "board.kicad_pcb", (), version)
    pretty = E.write_legacy_library(tmp_path, E.LEGACY, library=_LEG)
    before = board.read_bytes()
    _place(board, "U9", "V9", pretty, layer=side)
    after = board.read_bytes()
    J.assert_inserted(before, after, heads=["footprint"])
    assert _designators(after) == [("U9", "V9")]


# ---------------------------------------------------------------------------
# bytes: the reference and value land in the footprint's own texts
# ---------------------------------------------------------------------------


def _runs(work: Path, operation: str, version: str) -> tuple[bytes, bytes]:
    """The boards ``operation`` leaves when it gives edit_gen.LEGACY, in KiCad 7's form,
    the reference and value U1 V1 and when it gives U7 V7, from two boards alike but for
    that: place_footprint on an empty board; the update adding the footprint to an empty
    board; the update swapping the board's U1 (U7), its footprint text written once, the
    two copies differing only in its Reference's text."""
    pretty = E.write_legacy_library(work, E.LEGACY, library=_LEG)
    items: list[str] = []
    old = ""
    if operation == "swap":
        old = E.board_footprint_text(_OLD, "U1", _AT, 30.0, version=version)
        if old.count('(property "Reference" "U1"') != 1:
            raise RuntimeError("the board footprint does not write its Reference once")
    out = []
    for ref, value in (("U1", "V1"), ("U7", "V7")):
        if operation == "swap":
            items = [old.replace('(property "Reference" "U1"', f'(property "Reference" "{ref}"')]
        board = _board(work / f"{operation}_{ref}.kicad_pcb", items, version)
        if operation == "place":
            _place(board, ref, value, pretty)
        else:
            _update(board, _netlist([(ref, value, _LEGACY_ID)]), [pretty])
        out.append(board.read_bytes())
    return out[0], out[1]


def _legacy_text(kind: str):
    """A predicate for edit_judge.find_paths: a footprint's legacy (fp_text ``kind`` ...)."""

    def found(_, item) -> bool:
        return item.items is not None and item.head == "fp_text" and item.items[1].label == kind

    return found


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="bug 91: the reference and value land in no legacy fp_text of the footprint;"
    " fixed by step 7a",
)
@pytest.mark.parametrize("version", _VERSIONS)
@pytest.mark.parametrize("operation", ["place", "add", "swap"])
def test_reference_and_value_change_only_the_legacy_texts(tmp_path, operation, version):
    """edit_gen.LEGACY, in KiCad 7's form, placed by place_footprint, added by the update
    or swapped in by it, once as U1 V1 and once as U7 V7, from boards alike but for that,
    on a board of either major: each board holds the library's one (fp_text reference ...)
    and one (fp_text value ...), and the two boards differ in their text atoms, U1 against
    U7 and V1 against V7, and nowhere else (edit_judge.atom_changes) but the uuid a placed
    or added footprint takes afresh: the reference and value land in the footprint's own
    texts. The tool wrote neither, so the boards were alike but for that uuid."""
    first, second = _runs(tmp_path, operation, version)
    paths = {}
    for kind in ("reference", "value"):
        found = J.find_paths(first, _legacy_text(kind))
        assert len(found) == 1, f"{len(found)} (fp_text {kind} ...) written, the library has 1"
        paths[kind] = found[0]
    changes = [c for c in J.atom_changes(first, second) if c.path[-1][1] not in ("uuid", "tstamp")]
    assert changes == [
        J.AtomChange(paths["reference"], 2, b'"U1"', b'"U7"'),
        J.AtomChange(paths["value"], 2, b'"V1"', b'"V7"'),
    ]
