"""Rung 3: junctions are added and removed as KiCad's editor adds and removes them.

Placing a junction by hand (tools/sch_drawing_tools.cpp:1465-1497 at 9.0.8, 1705-1730 at
10.0.7): nothing happens where a junction already is; otherwise it is refused, with
"Junction location contains no joinable wires and/or pins.", unless
SCH_SCREEN::IsExplicitJunctionAllowed holds (sch_screen.cpp:498-504 at 9.0.8, 554-560 at
10.0.7), and once added every wire through it is split. That test is
JUNCTION_HELPERS::AnalyzePoint (junction_helpers.cpp:26-142 at 9.0.8) with crossings
broken: three or more directions of wires, pins and sheet pins meet at the point. So
KiCad refuses a junction in empty space, inside a straight wire, at an L corner and where
one wire ends at a pin, and allows one at a crossing or a T.

Deleting one, SCH_EDIT_FRAME::DeleteJunction (bus-wire-junction.cpp:115-185 at 9.0.8,
117-187 at 10.0.7, identical): the dot goes, and of the wires and buses that end at its
position each parallel pair is made one (SCH_LINE::MergeOverlap) or, when the two are
identical, one goes. So a T loses its join and a crossing becomes a plain crossing.

The layout and its pin ends are test_ladder_r3_wires'; edits are judged by KiCad's lexer
(sch_judge, edit_judge) and joins by kicad-cli's netlist.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
from pathlib import Path

import edit_judge as J
import pytest
import sch_judge as S
from conftest import HAS_KICAD_CLI, _kicad_cli_major, requires_cli
from mcp.server.mcpserver.exceptions import ToolError
from routing_fixtures import junction, place, splice, wire
from test_ladder_r3_wires import (
    R1_2,
    R2_1,
    R3_1,
    R4_2,
    A,
    B,
    C,
    D,
    P,
    cli9,
    joins,
    layout,
    one_net,
    read,
)

from mcp_server_kicad import schematic
from mcp_server_kicad._shared import _run_cli

_BUG5 = (
    "bug 5: add_junctions writes a junction where KiCad's editor writes none, on a wire"
    " it does not split included; fixed by step 8"
)
_BUG94 = (
    "bug 94: add_junctions writes a junction where KiCad's editor refuses one and its"
    " CleanUp deletes it (nothing, an L corner, a wire end at a pin); fixed by step 8"
)
_BUG93 = (
    "bug 93: remove_junction removes only the dot, so the wires KiCad's delete merges stay"
    " joined; fixed by step 8"
)


def _xfail(reason: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


FIXTURE = Path(__file__).parent / "fixtures" / "kicad_native.kicad_sch"


def _refusal(path, call) -> str:
    """Run ``call``; the ToolError's text when it refused and left ``path`` byte-identical.
    Raises AssertionError when it wrote, or refused with the file changed."""
    before = read(path)
    try:
        call()
    except ToolError as exc:
        assert read(path) == before, "refused, but the file changed"
        return str(exc)
    raise AssertionError("accepted: the call raised no ToolError")


def _junctions_at(path, *points) -> None:
    schematic.add_junctions([{"x": x, "y": y} for x, y in points], schematic_path=path)


def _kicads_t(tmp_path, name: str) -> str:
    """The T as KiCad's editor saves it: the bar split at P, the stem C-P, a junction at P."""
    p = layout(tmp_path, name)
    wire(p, *A, *P)
    wire(p, *P, *B)
    wire(p, *C, *P)
    junction(p, *P)
    return p


# ---------------------------------------------------------------------------------------------
# add_junctions: where KiCad refuses one
# ---------------------------------------------------------------------------------------------


def _empty(p) -> None:
    pass


def _straight(p) -> None:
    wire(p, *A, *B)


def _corner(p) -> None:
    wire(p, *A, *P)
    wire(p, *P, *C)


def _wire_end_at_a_pin(p) -> None:
    wire(p, *A, *P)


@pytest.mark.parametrize(
    "build, point",
    [
        pytest.param(_empty, (50.8, 50.8), id="empty_space", marks=_xfail(_BUG94)),
        pytest.param(_straight, P, id="straight_wire_interior", marks=_xfail(_BUG5)),
        pytest.param(_corner, P, id="l_corner", marks=_xfail(_BUG94)),
        pytest.param(_wire_end_at_a_pin, A, id="wire_end_at_a_pin", marks=_xfail(_BUG94)),
    ],
)
def test_add_junctions_refuses_where_kicad_refuses(tmp_path, build, point):
    """Where fewer than three directions meet (nothing; a wire's two; an L's two; a wire end
    and a pin) KiCad's editor refuses a junction and its load-time CleanUp deletes one: the
    call refuses, names the point and writes nothing."""
    p = layout(tmp_path, "refuse")
    build(p)
    message = _refusal(p, lambda: _junctions_at(p, point))
    assert f"({point[0]}, {point[1]})" in message, message


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG94)
def test_add_junctions_refuses_the_whole_call_for_one_bad_point(tmp_path):
    """A list holding a T point (three wire ends meet at P) and a point in empty space: the
    whole call is refused, the refused point named, and nothing is written."""
    p = layout(tmp_path, "mixed")
    wire(p, *A, *P)
    wire(p, *P, *B)
    wire(p, *C, *P)
    message = _refusal(p, lambda: _junctions_at(p, P, (50.8, 50.8)))
    assert "(50.8, 50.8)" in message, message


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_a_junction_kicad_cli_9_would_cut_a_wire_at_is_refused(tmp_path):
    """A junction inside the straight wire A-B is one kicad-cli 9 cuts the wire at, leaving
    R1:2 and R2:1 apart (checked on a copy as the premise); add_junctions refuses it."""
    if not cli9():
        pytest.skip("kicad-cli 10 joins a wire through a junction, so the cut is kicad-cli 9's")
    p = layout(tmp_path, "d2", parts=("R1", "R2"))
    wire(p, *A, *B)
    dotted = Path(tmp_path) / "d2copy" / "d2copy.kicad_sch"
    dotted.parent.mkdir()
    shutil.copyfile(p, dotted)
    junction(str(dotted), *P)
    if joins(dotted, {R1_2, R2_1}) != {frozenset({R1_2}), frozenset({R2_1})}:
        raise RuntimeError("premise: kicad-cli 9 does not cut a wire at a junction inside it")
    _refusal(p, lambda: _junctions_at(p, P))


# ---------------------------------------------------------------------------------------------
# add_junctions: where KiCad allows one
# ---------------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_add_junctions_on_a_crossing_splits_both_wires(tmp_path):
    """A junction on the crossing of A-B and C-D (four directions): both wires are split at
    P as BreakAt splits them, the junction inserted, nothing else changed; no junction lies
    inside a wire, and kicad-cli 9 reads R1:2, R2:1, R3:1 and R4:2 on one net (it read
    R1:2 with R3:1 only, each wire cut at P)."""
    p = layout(tmp_path, "x1", parts=("R1", "R2", "R3", "R4"))
    wire(p, *A, *B)
    wire(p, *C, *D)
    before = read(p)
    _junctions_at(p, P)
    after = read(p)
    splits = S.assert_split_edit(before, after, heads=["wire", "wire", "junction"])
    assert sorted(s.point for s in splits) == [S.iu_point(P)] * 2
    assert S.junctions_on_interiors(after) == []
    if cli9():
        pins = {R1_2, R2_1, R3_1, R4_2}
        assert joins(p, pins) == one_net(*pins)


def test_remove_junction_undoes_add_junctions_on_a_crossing(tmp_path):
    """add_junctions on a crossing then remove_junction there gives back the file byte for
    byte: whatever the junction split, the delete merges back (the inverse law)."""
    p = layout(tmp_path, "inverse", parts=("R1", "R2", "R3", "R4"))
    wire(p, *A, *B)
    wire(p, *C, *D)
    before = read(p)
    _junctions_at(p, P)
    schematic.remove_junction(*P, schematic_path=p)
    assert read(p) == before


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_add_junctions_splits_the_wire_under_a_junction_already_there(tmp_path):
    """The T as earlier versions wrote it: the bar A-B whole, the stem C-P and a junction at
    P inside the bar. add_junctions at P adds no second junction and splits the bar at P,
    which is what KiCad's editor holds after loading the file; kicad-cli 9 then reads the
    three pins on one net, where it read R2:1 cut off."""
    pins = {R1_2, R2_1, R3_1}
    p = layout(tmp_path, "damaged")
    wire(p, *A, *B)
    wire(p, *C, *P)
    junction(p, *P)
    if cli9() and joins(p, pins) != {frozenset({R1_2, R3_1}), frozenset({R2_1})}:
        raise RuntimeError("premise: kicad-cli 9 does not cut the bar at the junction")
    before = read(p)
    _junctions_at(p, P)
    after = read(p)
    (split,) = S.assert_split_edit(before, after, heads=["wire"])
    assert (split.old.start, split.old.end, split.point) == (
        S.iu_point(A),
        S.iu_point(B),
        S.iu_point(P),
    )
    if cli9():
        assert joins(p, pins) == one_net(*pins)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_add_junctions_where_kicad_has_one_writes_nothing(tmp_path):
    """At the junction of KiCad's own T nothing runs through the point and a junction is
    there already: KiCad's editor does nothing, and the file stays byte-identical."""
    p = _kicads_t(tmp_path, "kicad_t")
    before = read(p)
    _junctions_at(p, P)
    assert read(p) == before


# ---------------------------------------------------------------------------------------------
# remove_junction: KiCad's DeleteJunction
# ---------------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG93)
def test_remove_junction_disconnects_a_t(tmp_path):
    """Deleting the junction of KiCad's own T: the dot goes and the bar's halves, parallel
    and meeting at P, become one wire (the first keeps its uuid and start and takes the far
    end), so the stem ends inside the bar and joins nothing: kicad-cli reads R1:2 with R2:1
    and R3:1 alone."""
    p = _kicads_t(tmp_path, "rj")
    before = read(p)
    schematic.remove_junction(*P, schematic_path=p)
    after = read(p)
    (merge,) = S.assert_merge_edit(before, after, heads=["wire", "junction"])
    assert (merge.new.start, merge.new.end) == (S.iu_point(A), S.iu_point(B))
    if HAS_KICAD_CLI:
        assert joins(p, {R1_2, R2_1, R3_1}) == {frozenset({R1_2, R2_1}), frozenset({R3_1})}


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG93)
def test_remove_junction_turns_a_split_crossing_back_into_a_crossing(tmp_path):
    """A crossing as KiCad saves it with a junction (four halves meeting at P): deleting the
    junction merges each wire's halves, and kicad-cli reads the two wires as two nets."""
    p = layout(tmp_path, "rjx", parts=("R1", "R2", "R3", "R4"))
    for a, b in ((A, P), (P, B), (C, P), (P, D)):
        wire(p, *a, *b)
    junction(p, *P)
    before = read(p)
    schematic.remove_junction(*P, schematic_path=p)
    merges = S.assert_merge_edit(before, read(p), heads=["wire", "wire", "junction"])
    assert len(merges) == 2
    if HAS_KICAD_CLI:
        assert joins(p, {R1_2, R2_1, R3_1, R4_2}) == {
            frozenset({R1_2, R2_1}),
            frozenset({R3_1, R4_2}),
        }


G = (111.76, 91.44)  # R8:1, R8 at (111.76, 95.25): up and right of P at 45 degrees


def test_remove_junction_keeps_three_wires_of_different_directions_joined(tmp_path):
    """Three wires ending at P, none parallel to another (left, down, up and to the right):
    DeleteJunction merges only parallel pairs, so it removes the dot alone, and kicad-cli
    still reads the three pins on one net through the ends they share."""
    pins = {R1_2, R3_1, ("R8", "1")}
    p = layout(tmp_path, "three", parts=("R1", "R3"))
    place(p, "R", "R8", 111.76, 95.25)
    for a, b in ((A, P), (P, C), (P, G)):
        wire(p, *a, *b)
    junction(p, *P)
    before = read(p)
    schematic.remove_junction(*P, schematic_path=p)
    J.assert_removed(before, read(p), heads=["junction"])
    if HAS_KICAD_CLI:
        assert joins(p, pins) == one_net(*pins)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG93)
def test_remove_junction_drops_one_of_two_identical_wires(tmp_path):
    """Two identical wires A-P meet a junction at P: DeleteJunction removes one of two
    identical lines, so the dot and one wire go, and the other wire stays as it was. Which
    one goes follows KiCad's spatial index, not the file, so either may."""
    p = layout(tmp_path, "twins", parts=("R1",))
    wire(p, *A, *P)
    wire(p, *A, *P)
    junction(p, *P)
    before = read(p)
    twins = {line.uuid for line in S.lines(before)}
    schematic.remove_junction(*P, schematic_path=p)
    after = read(p)
    J.assert_removed(before, after, heads=["junction", "wire"], contiguous=False)
    (kept,) = S.lines(after)
    assert kept.uuid in twins


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG93)
def test_remove_junction_merges_nothing_where_a_bus_entry_ends(tmp_path):
    """The bar's halves meet at P where a bus entry also ends. Merging them would leave the
    entry's end inside one wire, which kicad-cli 9 cuts (it breaks every wire at every
    bus-entry end, SCHEMATIC::BreakSegmentsOnJunctions, schematic.cpp:1090-1113 at 9.0.8)
    and which KiCad 9's editor splits again on its next cleanup. So the dot alone goes and
    the result says the merge was skipped for the bus entry."""
    p = layout(tmp_path, "entry", parts=("R1", "R2"))
    wire(p, *A, *P)
    wire(p, *P, *B)
    stroke = "(stroke (width 0) (type default))"
    splice(p, f'(bus (pts (xy 104.14 88.9) (xy 104.14 100.33)) {stroke} (uuid "x"))')
    splice(p, f'(bus_entry (at 104.14 99.06) (size -2.54 2.54) {stroke} (uuid "x"))')
    junction(p, *P)
    before = read(p)
    result = schematic.remove_junction(*P, schematic_path=p)
    J.assert_removed(before, read(p), heads=["junction"])
    assert "bus entry" in result, result


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG93)
def test_remove_junction_removes_a_second_junction_at_the_point_too(tmp_path):
    """KiCad's T with two junctions at exactly P. KiCad's load-time CleanUp drops a second
    junction at one position (schematic.cpp:1163-1174 at 9.0.8), so its editor shows one and
    deleting it merges the bar: both dots go, the bar's halves become one wire, and
    kicad-cli reads R3:1 alone."""
    p = _kicads_t(tmp_path, "twice")
    junction(p, *P)
    before = read(p)
    schematic.remove_junction(*P, schematic_path=p)
    after = read(p)
    (merge,) = S.assert_merge_edit(before, after, heads=["wire", "junction", "junction"])
    assert (merge.new.start, merge.new.end) == (S.iu_point(A), S.iu_point(B))
    if HAS_KICAD_CLI:
        assert joins(p, {R1_2, R2_1, R3_1}) == {frozenset({R1_2, R2_1}), frozenset({R3_1})}


def test_remove_junction_on_a_straight_wire_leaves_it_whole(tmp_path):
    """A junction inside the straight wire A-B (what an earlier add_junctions wrote) ends no
    wire, so deleting it merges nothing: the dot alone goes, and kicad-cli reads R1:2 and
    R2:1 on one net again."""
    p = layout(tmp_path, "stray", parts=("R1", "R2"))
    wire(p, *A, *B)
    junction(p, *P)
    before = read(p)
    schematic.remove_junction(*P, schematic_path=p)
    J.assert_removed(before, read(p), heads=["junction"])
    if HAS_KICAD_CLI:
        assert joins(p, {R1_2, R2_1}) == one_net(R1_2, R2_1)


# ---------------------------------------------------------------------------------------------
# KiCad 10 groups: what a split and a merge do to them
# ---------------------------------------------------------------------------------------------

_K10_VERSION = b"(version 20260306)"


def _as_kicad10(path) -> None:
    """Stamp a KiCad 9 schematic with KiCad 10.0.7's format version: every node these tests
    touch is written alike by both (wire, junction, bus entry), and groups exist only from
    KiCad 10 on (the 9.0.8 parser has no group token)."""
    data = read(path)
    if data.count(b"(version 20250114)") != 1:
        raise RuntimeError("the schematic does not carry KiCad 9's version stamp once")
    Path(path).write_bytes(data.replace(b"(version 20250114)", _K10_VERSION))


def _group(path, name: str, members) -> bytes:
    """Splice a group in saveGroup's form; returns its uuid."""
    import uuid

    gid = str(uuid.uuid4())
    listed = " ".join(f'"{m.decode()}"' for m in members)
    splice(str(path), f'(group "{name}" (uuid "{gid}") (members {listed}))', keep_uuid=True)
    return gid.encode()


#: KiCad 10.0.7's rule for a grouped line that is split or merged, from its source: the
#: SCH_LINE copy constructor sets the copy's group to none ("Don't apply groups to cloned
#: lines", sch_line.cpp:83-103 at 10.0.7), so SCH_ITEM::Duplicate, which BreakAt calls with
#: addToParentGroup, finds no group on the copy to add it to (sch_item.cpp:149-177), and
#: SCH_LINE::MergeOverlap's merged line is such a copy too (sch_line.cpp:457-575); a commit puts
#: a new item only in the group the user has entered in the editor (sch_commit.cpp:265-268)
#: and takes a removed item out of its group (sch_commit.cpp:323-324). So a split leaves every
#: group as it was, the new half in none, and a merge takes both lines it joined out of their
#: groups, the merged line in none; saveGroup writes no group left empty
#: (sch_io_kicad_sexpr.cpp:1635-1637).


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_a_split_leaves_the_wires_group_as_kicad_10_does():
    """On a KiCad 10 file a stem drawn onto a grouped wire splits it as BreakAt does, and the
    group is left as it was: the wire stays its only member and the new half joins none
    (the rule above). KiCad 9's kicad-cli cannot load a KiCad 10 file, so the file is
    written outside the test's directory; the twin below has kicad-cli 10 load it."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        p = Path(work) / "k10.kicad_sch"
        shutil.copyfile(FIXTURE, p)
        _as_kicad10(p)
        (bar,) = S.lines(read(p))
        gid = _group(p, "g", [bar.uuid])
        before = read(p)
        schematic.add_wires([{"x1": 65, "y1": 80, "x2": 65, "y2": 100}], schematic_path=str(p))
        after = read(p)
    (split,) = S.assert_split_edit(before, after, heads=["wire", "wire", "junction"])
    assert split.point == S.iu_point((65, 100))
    assert S.groups(after) == {gid: [bar.uuid]}


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG93)
def test_a_merge_takes_both_wires_out_of_their_groups_as_kicad_10_does():
    """On a KiCad 10 file, deleting the junction of a T whose halves are grouped: the merge
    takes both halves out of every group (the merged wire, which keeps the first half's uuid,
    is in none), so a group holding them and the stem keeps the stem alone and a group
    holding only the second half is removed (the rule above). Written outside the test's
    directory, as above."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        p = _kicads_t(Path(work), "k10t")
        _as_kicad10(p)
        first, second, stem = S.lines(read(p))
        both = _group(p, "both", [first.uuid, second.uuid, stem.uuid])
        _group(p, "alone", [second.uuid])
        before = read(p)
        schematic.remove_junction(*P, schematic_path=p)
        after = read(p)
    assert S.groups(after) == {both: [stem.uuid]}, S.groups(after)
    lines_after = S.lines(after)
    assert [(ln.uuid, ln.start, ln.end) for ln in lines_after if ln.uuid == first.uuid] == [
        (first.uuid, S.iu_point(A), S.iu_point(B))
    ]
    assert second.uuid not in {ln.uuid for ln in lines_after}
    assert S.junctions(after) == [] and len(before) > len(after)


@requires_cli
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_a_split_leaves_group_membership_on_real_kicad10(tmp_path):
    """The split case on a file kicad-cli 10 minted, with a group in saveGroup's form around
    the fixture's wire: the bar is split, the group is left as it was, and the autouse oracle
    has kicad-cli 10 load the file with its group."""
    if _kicad_cli_major() < 10:
        pytest.skip("needs kicad-cli 10+ to mint a current-format schematic")
    p = tmp_path / "k10.kicad_sch"
    shutil.copyfile(FIXTURE, p)
    _run_cli(["sch", "upgrade", "--force", str(p)])
    if b"(version 2026" not in read(p)[:80]:
        raise RuntimeError("kicad-cli 10 did not mint a KiCad 10 schematic")
    (bar,) = S.lines(read(p), major=10)
    gid = _group(p, "", [bar.uuid])
    before = read(p)
    schematic.add_wires([{"x1": 65, "y1": 80, "x2": 65, "y2": 100}], schematic_path=str(p))
    after = read(p)
    (split,) = S.assert_split_edit(before, after, heads=["wire", "wire", "junction"])
    assert split.point == S.iu_point((65, 100))
    assert S.groups(after) == {gid: [bar.uuid]}


# ---------------------------------------------------------------------------------------------
# The guard: every junction is written by the one helper that splits
# ---------------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_BUG5)
def test_every_junction_is_written_through_the_join_helper():
    """schematic._JUNCTION_TPL, the only junction template, is used by schematic._join_points
    alone, the helper that splits every wire through a junction it writes, so no tool can
    write a junction without the split. Functional tests cannot see a new direct splice;
    this reads the package's source."""
    package = Path(schematic.__file__).parent
    offenders = []
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        owners = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    owners.setdefault(id(node), fn.name)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Name, ast.Attribute)):
                continue
            if (node.id if isinstance(node, ast.Name) else node.attr) != "_JUNCTION_TPL":
                continue
            owner = owners.get(id(node))
            if owner is None and isinstance(node.ctx, ast.Store):
                continue  # the template's own definition
            if owner != "_join_points":
                offenders.append(f"{module.name}:{node.lineno} in {owner or 'module scope'}")
    assert offenders == [], offenders
