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

validate_hierarchy finds the junctions earlier versions of this package left inside wires and
names the add_junctions or remove_junction call that leaves each as KiCad's editor holds it.
"""

from __future__ import annotations

import ast
import math
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import edit_judge as J
import kicad_corpus as K
import pytest
import sch_judge as S
from conftest import (
    HAS_KICAD_CLI,
    _cli_report,
    _kicad_cli_major,
    _nightly_skip_reason,
    requires_cli,
)
from kicad_ref import dsn_lexer as L
from kicad_ref import sch_line
from mcp.server.mcpserver.exceptions import ToolError
from routing_fixtures import (
    bus,
    bus_entry,
    fresh,
    junction,
    place,
    project_file,
    set_unit,
    sheet,
    splice,
    wire,
)
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

from mcp_server_kicad import _connectivity, _cst, project, schematic
from mcp_server_kicad._shared import _run_cli

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
        pytest.param(_empty, (50.8, 50.8), id="empty_space"),
        pytest.param(_straight, P, id="straight_wire_interior"),
        pytest.param(_corner, P, id="l_corner"),
        pytest.param(_wire_end_at_a_pin, A, id="wire_end_at_a_pin"),
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


def test_remove_junction_keeps_a_wire_merged_into_one_of_no_length(tmp_path):
    """KiCad's T at P with a wire of no length at P first in the file. DeleteJunction pairs
    every line ending at P, and one of no length is parallel to each; MergeOverlap returns
    the first line with the merged ends (sch_line.cpp:559-570 at 9.0.8), so the stem's extent
    survives, carried by the first line: the stem is still drawn from P to R3:1, the bar's
    halves become one wire, and kicad-cli reads R1:2 with R2:1 and R3:1 alone."""
    p = layout(tmp_path, "zero_first")
    wire(p, *P, *P)
    wire(p, *C, *P)
    wire(p, *A, *P)
    wire(p, *P, *B)
    junction(p, *P)
    before = read(p)
    schematic.remove_junction(*P, schematic_path=p)
    after = read(p)
    merges = S.assert_merge_edit(before, after, heads=["wire", "wire", "junction"])
    assert sorted({m.new.start, m.new.end} == {S.iu_point(P), S.iu_point(C)} for m in merges) == [
        False,
        True,
    ]
    if HAS_KICAD_CLI:
        assert joins(p, {R1_2, R2_1, R3_1}) == {frozenset({R1_2, R2_1}), frozenset({R3_1})}


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


# ---------------------------------------------------------------------------------------------
# The junction rule against KiCad's own files
# ---------------------------------------------------------------------------------------------

#: KiCad 9.0.8's schematic format (SEXPR_SCHEMATIC_FILE_VERSION), the domain of the port of
#: KiCad 9's AnalyzePoint: KiCad 10 refines the rule (see _connectivity.junction_info).
_K9_SCH_VERSION_MAX = 20250114


def _k9_schematics(cap):
    out = []
    for name, path in K.shipped(subdirs=("demos",), suffixes=(".kicad_sch",), names=(), cap=cap):
        version = L.file_version(path.read_bytes()[:256])
        if version is not None and version <= _K9_SCH_VERSION_MAX:
            out.append((name, path))
    return out


def _k9_params(files):
    return [pytest.param(path, id=name) for name, path in files] or [
        pytest.param(None, id="no-install", marks=pytest.mark.skip(reason=K.NO_INSTALL))
    ]


def _nightly_k9_schematics():
    if _nightly_skip_reason(os.environ.get("HYPOTHESIS_PROFILE")) is not None:
        return [pytest.param(None, id="nightly-only")]
    return _k9_params(_k9_schematics(None))


def _junction_rule_holds_on(path: Path) -> None:
    """On a sheet KiCad saved, the port places a junction exactly where KiCad did: every
    junction in the file is one the rule calls explicit, and no wire or bus end and no pin
    lacks one the rule calls needed.

    The second half leaves out the pins of a symbol whose instance entries disagree on its
    unit (``Sym.amb``). KiCad draws one unit per reading (each sheet instance, or the file
    loaded alone at its own root path) and the file holds the junctions of the reading it
    was last edited in, while the model draws every unit at once. KiCad 10's
    jetson-agx-thor-baseboard usb_debug_pd.kicad_sch is the case: a stale entry at the
    file's own root path draws unit 1 of a TPS65988 where the project draws unit 2, so two
    of unit 1's pin ends sit inside wires in a reading the file was not edited in."""
    data = path.read_bytes()
    try:
        m = _connectivity.Model(_cst.parse(data).lists[0])
    except _connectivity.Refusal as exc:
        raise RuntimeError(f"premise: the model refuses KiCad's own {path.name}: {exc}") from exc
    dots = {(it.x, it.y) for it in m.items if it.kind == "junction"}
    if len([it for it in m.items if it.kind == "junction"]) != data.count(b"(junction"):
        raise RuntimeError(f"premise: the model missed junctions of {path.name}")
    ends = {
        xy for it in m.items if it.kind in ("wire", "bus") for xy in ((it.x, it.y), (it.x2, it.y2))
    }
    ends |= {(it.x, it.y) for it in m.items if it.kind == "pin"}
    unread = {
        (it.x, it.y) for it in m.items if it.kind == "pin" and it.sym is not None and it.sym.amb
    }
    wrong = [
        ("not explicit", p)
        for p in sorted(dots)
        if not _connectivity.junction_info(m, p, break_crossings=False).explicit
    ]
    wrong += [
        ("needed", p)
        for p in sorted(ends - unread)
        if _connectivity.junction_info(m, p, break_crossings=False).needed
    ]
    assert wrong == [], wrong[:10]


@pytest.mark.parametrize("path", _k9_params(_k9_schematics(K.PER_PUSH_CAP)))
def test_junction_rule_agrees_with_kicads_own_files(path):
    """KiCad's demos at KiCad 9's format (files up to kicad_corpus.PER_PUSH_CAP): the port
    of AnalyzePoint (junction_helpers.cpp:26-142 at 9.0.8) finds every junction KiCad saved
    explicit, and finds a junction needed at no wire or bus end and no pin end, so it adds
    junctions exactly where KiCad's own tools did."""
    _junction_rule_holds_on(path)


@pytest.mark.nightly
@pytest.mark.parametrize("path", _nightly_k9_schematics())
def test_nightly_junction_rule_agrees_with_kicads_own_files(path):
    """The same over every demo schematic at KiCad 9's format the install holds."""
    _junction_rule_holds_on(path)


# ---------------------------------------------------------------------------------------------
# validate_hierarchy: the junctions earlier versions left inside wires
# ---------------------------------------------------------------------------------------------

P2 = (111.76, 101.6)  # on the bar A-B, right of P
E = (111.76, 110.49)  # R5:1
R5_1 = ("R5", "1")
#: The pin each part layout() places brings, and where its end is.
_PINS = {"R1": (R1_2, A), "R2": (R2_1, B), "R3": (R3_1, C), "R4": (R4_2, D), "R5": (R5_1, E)}


class _Damage(NamedTuple):
    """A sheet as an earlier version of this package wrote it, which the tools now refuse to
    write: the parts layout() places, raw wires ``lines`` drawn whole, each (start, end), and
    junctions at ``dots``, each strictly inside a wire. ``kept`` holds the dots KiCad 9's
    editor keeps when it loads the sheet (_editor_lines says which)."""

    parts: tuple[str, ...]
    lines: tuple
    dots: tuple
    kept: tuple

    @property
    def pins(self) -> set:
        return {_PINS[ref][0] for ref in self.parts}


_T = _Damage(("R1", "R2", "R3"), ((A, B), (C, P)), (P,), (P,))
_DAMAGED = [
    pytest.param(_T, id="t"),
    pytest.param(_T._replace(lines=((B, A), (C, P))), id="t_bar_drawn_from_its_far_end"),
    pytest.param(_Damage(("R1", "R2", "R3", "R4"), ((A, B), (C, D)), (P,), (P,)), id="crossing"),
    pytest.param(
        _Damage(("R1", "R2", "R3", "R5"), ((A, B), (C, P), (E, P2)), (P, P2), (P, P2)),
        id="two_junctions_on_one_wire",
    ),
    pytest.param(_Damage(("R1", "R2"), ((A, B),), (P,), ()), id="stray_junction"),
]


def _sheet(tmp_path, name: str, case: _Damage, lines, dots) -> str:
    """layout() with the case's parts, then raw wires and junctions exactly as given."""
    p = layout(tmp_path, name, parts=case.parts)
    for a, b in lines:
        wire(p, *a, *b)
    for q in dots:
        junction(p, *q)
    return p


def _inside_mm(a, b, q) -> bool:
    return sch_line.strictly_inside(S.iu_point(a), S.iu_point(b), S.iu_point(q))


def _editor_lines(case: _Damage) -> list:
    """The case's wires as KiCad 9's editor holds them once it has loaded the sheet.

    Loading runs SCHEMATIC::CleanUp on every sheet (files-io.cpp:539, schematic.cpp:1307-1331
    at 9.0.8). It splits every wire at every junction inside it, the schematic holder adding
    each second half (1147, which calls BreakSegmentsOnJunctions at 1090-1113); it deletes
    each junction where IsExplicitJunction fails, here where fewer than three directions meet
    (1149-1158); and it merges collinear pieces that meet where no junction is left
    (1212-1303). So each wire stays split at the junctions kept and whole across the rest."""
    out = []
    for a, b in case.lines:
        cuts = sorted((q for q in case.kept if _inside_mm(a, b, q)), key=lambda q: math.dist(a, q))
        stops = [a, *cuts, b]
        out += list(zip(stops, stops[1:]))
    return out


def _junction_issues(path) -> list[dict]:
    result = project.validate_hierarchy(schematic_path=str(path))
    return [i for i in result.issues if i["type"] == "junction_inside_wire"]


def _ends(stretch) -> tuple:
    """A reported stretch's two ends, in internal units as KiCad reads the numbers."""
    return S.iu_point((stretch["x1"], stretch["y1"])), S.iu_point((stretch["x2"], stretch["y2"]))


def _repair_point(repair):
    """The point a repair call names, as KiCad reads the numbers."""
    args = repair["arguments"]
    (spot,) = args["points"] if repair["tool"] == "add_junctions" else [args]
    return S.iu_point((spot["x"], spot["y"]))


def _repair(issue) -> None:
    """Make the repair call the issue names, as a caller would."""
    tool = issue["repair"]["tool"]
    assert tool in ("add_junctions", "remove_junction"), tool
    getattr(schematic, tool)(**issue["repair"]["arguments"])


def _shape(data: bytes):
    """A sheet's wires and buses (kind, start, end) and junctions, as KiCad reads them."""
    return sorted((ln.kind, ln.start, ln.end) for ln in S.lines(data)), sorted(S.junctions(data))


@pytest.mark.parametrize("case", _DAMAGED)
def test_validate_hierarchy_reports_a_junction_inside_a_wire(tmp_path, case):
    """A sheet an earlier version wrote, junctions inside wires that sch_judge finds:
    validate_hierarchy names each such point and every wire it sits inside.

    Per wire, kicad-cli 9 keeps the stretch from the wire's first point to the cut nearest it,
    junctions and bus-entry ends cutting alike, and drops the rest: BreakSegment shortens the
    wire and has no schematic holder to add the other half to (schematic.cpp:1055-1113 at
    9.0.8). kicad-cli 9 is the judge: it reads the sheet exactly as it reads one holding only
    the stretches reported kept, and puts the pin at each dropped end on a net of its own.

    The editor's reading and the repair follow KiCad's source (_editor_lines), which the port
    of AnalyzePoint agrees with: the editor keeps a junction where three or more directions
    meet, and add_junctions there splits the wires as it does; it deletes the others, and so
    does remove_junction. Made as named, the repair calls leave the sheet as KiCad 9's editor
    holds it, and kicad-cli 9 then joins the pins as it joins that form."""
    p = _sheet(tmp_path, "damaged", case, case.lines, case.dots)
    data = read(p)
    inside = S.junctions_on_interiors(data)
    issues = _junction_issues(p)
    assert sorted(S.iu_point((i["x"], i["y"])) for i in issues) == sorted({q for q, _ in inside})
    model = _connectivity.Model(_cst.parse(data).lists[0])
    kept: dict = {}
    for issue in issues:
        q = S.iu_point((issue["x"], issue["y"]))
        assert issue["file"] == p
        assert sorted((ln["kind"], *_ends(ln)) for ln in issue["lines"]) == sorted(
            (ln.kind, ln.start, ln.end) for at, ln in inside if at == q
        )
        for ln in issue["lines"]:
            start, end = _ends(ln)
            (first, cut), (cut_again, last) = _ends(ln["kept"]), _ends(ln["dropped"])
            assert (first, cut_again, last) == (start, cut, end), ln
            assert sch_line.strictly_inside(start, end, cut), ln
            assert kept.setdefault((start, end), cut) == cut, "two readings of one wire"
        keeps = q in {S.iu_point(k) for k in case.kept}
        if _connectivity.junction_info(model, q, break_crossings=True).explicit != keeps:
            raise RuntimeError(f"premise: the AnalyzePoint port and KiCad's source part at {q}")
        assert issue["editor"] == ("keeps_junction" if keeps else "deletes_junction")
        assert issue["repair"]["tool"] == ("add_junctions" if keeps else "remove_junction")
        assert issue["repair"]["arguments"]["schematic_path"] == p
        assert _repair_point(issue["repair"]) == q

    editor = _sheet(tmp_path, "editor", case, _editor_lines(case), case.kept)
    if S.junctions_on_interiors(read(editor)):
        raise RuntimeError("premise: the editor's form holds a junction inside a wire")
    reading = None
    if cli9():
        reading = joins(p, case.pins)
        stretches = []
        for a, b in case.lines:
            cut = kept.get((S.iu_point(a), S.iu_point(b)))
            stretches.append((a, b) if cut is None else (a, (cut[0] / 10_000, cut[1] / 10_000)))
        assert reading == joins(_sheet(tmp_path, "kept", case, stretches, ()), case.pins)
        at = {S.iu_point(where): pin for pin, where in _PINS.values()}
        for issue in issues:
            for ln in issue["lines"]:
                assert frozenset({at[_ends(ln["dropped"])[1]]}) in reading, (ln, reading)
        if reading == joins(editor, case.pins):
            raise RuntimeError("premise: kicad-cli 9 reads the damaged sheet as the editor does")

    for issue in issues:
        _repair(issue)
    assert _junction_issues(p) == []
    assert _shape(read(p)) == _shape(read(editor))
    if reading is not None:
        assert joins(p, case.pins) == joins(editor, case.pins)


def _three_levels(tmp_path):
    """A project whose root holds sheet Child; Child's file holds sheets Grand and Gone.
    In Grand's file: a hierarchical label ORPHAN that no pin of Grand matches, a part R7
    sharing its reference with one on the root, and a T as an earlier version wrote it (the
    bar whole under the junction). Returns the four files: root, child, grand, gone."""
    d = tmp_path / "proj"
    project.create_project(directory=str(d), name="proj")
    root, child, grand, gone = (d / f"{n}.kicad_sch" for n in ("proj", "child", "grand", "gone"))
    for f in (child, grand, gone):
        project.create_schematic(schematic_path=str(f))
    project.add_hierarchical_sheet(
        str(root), "Child", str(child), [{"name": "A", "direction": "input"}]
    )
    project.add_hierarchical_sheet(
        str(child), "Grand", str(grand), [{"name": "A", "direction": "input"}]
    )
    project.add_hierarchical_sheet(str(child), "Gone", str(gone), [], x=101.6)
    schematic.add_hierarchical_label("ORPHAN", "output", 50.8, 50.8, schematic_path=str(grand))
    place(str(root), "R", "R7", 152.4, 152.4)
    place(str(grand), "R", "R7", 152.4, 152.4)
    wire(str(grand), *A, *B)
    wire(str(grand), *C, *P)
    junction(str(grand), *P)
    return root, child, grand, gone


def test_validate_hierarchy_checks_every_sheet_however_deep(tmp_path):
    """Bug 77: validate_hierarchy stopped at the root's own sheets. Each issue of the
    grandchild in _three_levels, and the missing file of the sheet beside it, is reported as
    the same issue one level up is: the orphaned label, the reference the root shares, the
    junction inside a wire, the missing file."""
    root, _child, grand, gone = _three_levels(tmp_path)
    gone.unlink()
    try:
        issues = project.validate_hierarchy(schematic_path=str(root)).issues
    finally:  # the kicad-cli oracle refuses a hierarchy with a sheet file missing
        project.create_schematic(schematic_path=str(gone))
    assert {"type": "orphaned_label", "sheet_name": "Grand", "label": "ORPHAN"} in issues
    assert {"type": "missing_file", "sheet_name": "Gone", "file_name": "gone.kicad_sch"} in issues
    shared = {"type": "duplicate_ref", "reference": "R7", "sheets": [root.name, grand.name]}
    assert shared in issues
    (dot,) = [i for i in issues if i["type"] == "junction_inside_wire"]
    assert (dot["file"], S.iu_point((dot["x"], dot["y"]))) == (str(grand), S.iu_point(P))


def test_validate_hierarchy_changes_no_file(tmp_path):
    """validate_hierarchy only reads: over _three_levels with a stray junction on the root as
    well, two sheets it builds the connectivity model of, no byte of any file under the
    project changes and no file appears or goes."""
    root, *_ = _three_levels(tmp_path)
    wire(str(root), 60.96, 101.6, 91.44, 101.6)
    junction(str(root), 76.2, 101.6)

    def files() -> dict:
        return {f: f.read_bytes() for f in sorted(tmp_path.rglob("*")) if f.is_file()}

    before = files()
    issues = project.validate_hierarchy(schematic_path=str(root)).issues
    assert files() == before
    assert len([i for i in issues if i["type"] == "junction_inside_wire"]) == 2


def test_validate_hierarchy_finds_a_sheet_file_beside_the_sheet_naming_it():
    """KiCad opens a sheet's file beside the file holding the sheet: its loader makes a
    relative name absolute against the directory of the file being loaded
    (SCH_IO_KICAD_SEXPR::loadHierarchy, sch_io_kicad_sexpr.cpp:189-198 at 9.0.8, 201-211 at
    10.0.7), and a file it cannot open is a missing sheet (243-258). sub/child.kicad_sch holds
    sheet Near, whose grand.kicad_sch is in sub/, and sheet Far, whose far.kicad_sch exists
    only beside the root: Near's file is checked, its orphaned label reported, and Far's
    file is missing. Written outside the test's directory, since the kicad-cli oracle refuses
    a hierarchy with a sheet file missing."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        d = Path(work)
        project.create_project(directory=str(d), name="proj")
        (d / "sub").mkdir()
        root, child, grand, far = (
            d / "proj.kicad_sch",
            d / "sub" / "child.kicad_sch",
            d / "sub" / "grand.kicad_sch",
            d / "far.kicad_sch",
        )
        for f in (child, grand, far):
            project.create_schematic(schematic_path=str(f))
        project.add_hierarchical_sheet(str(root), "Child", str(child), [])
        project.add_hierarchical_sheet(str(child), "Near", str(grand), [])
        project.add_hierarchical_sheet(str(child), "Far", str(far), [], x=101.6)
        root_bytes = read(root)
        if root_bytes.count(b'"child.kicad_sch"') != 1:
            raise RuntimeError("premise: the root names its sheet file once")
        root.write_bytes(root_bytes.replace(b'"child.kicad_sch"', b'"sub/child.kicad_sch"'))
        schematic.add_hierarchical_label(
            "NEAR_ONLY", "input", 50.8, 50.8, schematic_path=str(grand)
        )
        issues = project.validate_hierarchy(schematic_path=str(root)).issues
    assert issues == [
        {"type": "orphaned_label", "sheet_name": "Near", "label": "NEAR_ONLY"},
        {"type": "missing_file", "sheet_name": "Far", "file_name": "far.kicad_sch"},
    ]


def test_validate_hierarchy_skips_a_sheet_that_contains_itself():
    """Child's own sheet Back names the root's file, so the root contains itself: KiCad's
    loader refuses that sheet ("already appears as a direct ancestor",
    sch_io_kicad_sexpr.cpp:202-222 at 9.0.8). The walk skips it and refuses nothing: the root's
    hierarchical label, which only Back's edge could orphan, goes unreported, and the call
    ends. Written outside the test's directory, since the kicad-cli oracle refuses a sheet
    that is its own ancestor."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        d = Path(work)
        project.create_project(directory=str(d), name="proj")
        root, child = d / "proj.kicad_sch", d / "child.kicad_sch"
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            str(root), "Child", str(child), [{"name": "A", "direction": "input"}]
        )
        project.add_hierarchical_sheet(str(child), "Back", str(root), [], x=101.6)
        schematic.add_hierarchical_label("STRAY", "input", 152.4, 152.4, schematic_path=str(root))
        result = project.validate_hierarchy(schematic_path=str(root))
    assert (result.status, result.issues) == ("ok", [])


def test_validate_hierarchy_gives_no_kicad_9_reading_of_a_kicad_10_file():
    """The T an earlier version wrote, in a file stamped with KiCad 10.0.7's format. The
    junction is reported with its repair, since it is not KiCad's form in either major, but
    without the stretches kicad-cli 9 keeps and drops or the KiCad 9 editor's reading: KiCad 9
    loads no file stamped past 20250114 (SEXPR_SCHEMATIC_FILE_VERSION, sch_file_versions.h:117
    at 9.0.8), and KiCad 10 reads it otherwise, splitting nothing on load (CleanUp,
    schematic.cpp:1537-1585 at 10.0.7) and joining wires through a junction in its graph
    (connection_graph.cpp:1418-1429). The repair call clears it. No kicad-cli is involved;
    written outside the test's directory, since the local kicad-cli 9 cannot load it."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        p = _sheet(Path(work), "k10", _T, _T.lines, _T.dots)
        _as_kicad10(p)
        before = read(p)
        (issue,) = _junction_issues(p)
        unchanged = read(p) == before
        _repair(issue)
        after = read(p)
        left = _junction_issues(p)
    assert unchanged
    assert "editor" not in issue and issue["repair"]["tool"] == "add_junctions"
    assert [sorted(ln) for ln in issue["lines"]] == [["kind", "x1", "x2", "y1", "y2"]]
    assert left == [] and S.junctions_on_interiors(after, major=10) == []


@pytest.mark.kicad_placeholder("t45/t45.kicad_sch")
def test_validate_hierarchy_on_a_sheet_kicad_cannot_load(tmp_path):
    """The T an earlier version wrote, on a sheet whose R2 is placed at 45 degrees, which
    KiCad's parser refuses (it takes 0, 90, 180 or 270 only), so neither reader can load the
    sheet. The junction is still reported, with the wires it sits inside, and in place of a
    reading or a repair the reason the sheet cannot be judged; the call refuses nothing. The
    unloadable sheet is the input, so the kicad-cli oracle leaves it out."""
    p = _sheet(tmp_path, "t45", _T, _T.lines, _T.dots)
    data = read(p)
    if data.count(b"(at 127 101.6 90)") != 1:
        raise RuntimeError("premise: R2 is placed once at (127, 101.6) turned 90 degrees")
    Path(p).write_bytes(data.replace(b"(at 127 101.6 90)", b"(at 127 101.6 45)"))
    (issue,) = _junction_issues(p)
    assert S.iu_point((issue["x"], issue["y"])) == S.iu_point(P)
    assert "repair" not in issue and "editor" not in issue
    assert "angle 45" in issue["note"], issue
    assert [sorted(ln) for ln in issue["lines"]] == [["kind", "x1", "x2", "y1", "y2"]]


# ---------------------------------------------------------------------------------------------
# validate_hierarchy: bus-entry ends inside wires, references per sheet instance, edge cases
# ---------------------------------------------------------------------------------------------

Q = (90.17, 101.6)  # on the bar A-B, between A and P


class _Entries(NamedTuple):
    """A sheet as an earlier version wrote it, with bus entries: the parts, raw wires, the
    junctions, and bus entries ((x, y), (dx, dy)), each with a bus ending at its position and
    its far end on a wire. ``ends`` are the entry ends lying inside a wire."""

    parts: tuple[str, ...]
    lines: tuple
    dots: tuple
    entries: tuple
    ends: tuple

    @property
    def pins(self) -> set:
        return {_PINS[ref][0] for ref in self.parts}


_ENTRY_T = _Entries(
    ("R1", "R2", "R3"), ((A, B), (C, P)), (P,), (((87.63, 99.06), (2.54, 2.54)),), (Q,)
)
_ENTRY_THROUGH = _Entries(("R1", "R2"), ((A, B),), (), (((99.06, 99.06), (2.54, 2.54)),), (P,))
_ENTRY_CASES = [
    pytest.param(_ENTRY_THROUGH, id="wire_through_an_entry_end"),
    pytest.param(_ENTRY_T, id="t_and_an_entry_end_nearer_a"),
]


def _entry_sheet(tmp_path, name: str, case: _Entries, lines, dots) -> str:
    p = layout(tmp_path, name, parts=case.parts)
    for a, b in lines:
        wire(p, *a, *b)
    for q in dots:
        junction(p, *q)
    for (x, y), (dx, dy) in case.entries:
        bus(p, x, y - 10.16, x, y)
        bus_entry(p, x, y, dx, dy)
    return p


def _issues_of(path, kind: str) -> list[dict]:
    result = project.validate_hierarchy(schematic_path=str(path))
    return [i for i in result.issues if i["type"] == kind]


def _spot(issue) -> tuple:
    return S.iu_point((issue["x"], issue["y"]))


@pytest.mark.parametrize("case", _ENTRY_CASES)
def test_validate_hierarchy_reports_a_bus_entry_end_inside_a_wire(tmp_path, case):
    """kicad-cli 9 cuts a line at both ends of every bus entry inside it as it cuts one at a
    junction (BreakSegmentsOnJunctions, schematic.cpp:1096-1101 at 9.0.8), and earlier
    versions drew wires through bus-entry ends without splitting them. validate_hierarchy
    reports each such end as it reports a junction: the wire, the stretch kicad-cli 9 keeps
    and the one it drops (judged by kicad-cli 9, which reads the sheet as one holding only the
    stretches kept), the editor splitting the wire there, and add_junctions at the end, which
    KiCad allows there (three wire directions and the entry's one bus direction,
    IsExplicitJunctionAllowed, sch_screen.cpp:498-504 at 9.0.8; the port agrees). Made as
    named, the repairs leave no cut point inside a line and the re-run clean, and kicad-cli 9
    joins the pins as it joins the editor's form, every line split at every cut point."""
    p = _entry_sheet(tmp_path, "damaged", case, case.lines, case.dots)
    data = read(p)
    dots = _issues_of(p, "junction_inside_wire")
    ends = _issues_of(p, "bus_entry_inside_wire")
    assert sorted(map(_spot, dots)) == sorted(map(S.iu_point, case.dots))
    assert sorted(map(_spot, ends)) == sorted(map(S.iu_point, case.ends))
    model = _connectivity.Model(_cst.parse(data).lists[0])
    kept: dict = {}
    for issue in dots + ends:
        for ln in issue["lines"]:
            start, end = _ends(ln)
            (first, cut), (cut_again, last) = _ends(ln["kept"]), _ends(ln["dropped"])
            assert (first, cut_again, last) == (start, cut, end), ln
            assert kept.setdefault((start, end), cut) == cut, "two readings of one wire"
    for issue in ends:
        q = _spot(issue)
        assert issue["file"] == p
        assert [(ln["kind"], *_ends(ln)) for ln in issue["lines"]] == [
            ("wire", S.iu_point(a), S.iu_point(b))
            for a, b in case.lines
            if sch_line.strictly_inside(S.iu_point(a), S.iu_point(b), q)
        ]
        if not _connectivity.junction_info(model, q, break_crossings=True).explicit:
            raise RuntimeError(f"premise: KiCad allows no junction at the entry end {q}")
        assert issue["editor"] == "splits_wire"
        assert issue["repair"]["tool"] == "add_junctions"
        assert _repair_point(issue["repair"]) == q

    cuts = (*case.dots, *case.ends)
    split = []
    for a, b in case.lines:
        inner = sorted((q for q in cuts if _inside_mm(a, b, q)), key=lambda q: math.dist(a, q))
        stops = [a, *inner, b]
        split += list(zip(stops, stops[1:]))
    editor = _entry_sheet(tmp_path, "editor", case, split, case.dots)
    reading = None
    if cli9():
        reading = joins(p, case.pins)
        stretches = []
        for a, b in case.lines:
            cut = kept.get((S.iu_point(a), S.iu_point(b)))
            stretches.append((a, b) if cut is None else (a, (cut[0] / 10_000, cut[1] / 10_000)))
        assert reading == joins(_entry_sheet(tmp_path, "kept", case, stretches, ()), case.pins)
        if reading == joins(editor, case.pins):
            raise RuntimeError("premise: kicad-cli 9 reads the damaged sheet as the editor does")

    for issue in dots + ends:
        _repair(issue)
    assert _issues_of(p, "junction_inside_wire") == _issues_of(p, "bus_entry_inside_wire") == []
    assert not [
        (ln.start, ln.end, q)
        for ln in S.lines(read(p))
        for q in map(S.iu_point, cuts)
        if sch_line.strictly_inside(ln.start, ln.end, q)
    ]
    if reading is not None:
        assert joins(p, case.pins) == joins(editor, case.pins) == one_net(*case.pins)


def test_validate_hierarchy_names_no_repair_for_an_entry_end_no_junction_may_take():
    """A bus drawn through the bus end of a bus entry: kicad-cli 9 cuts the bus there, and
    KiCad allows no junction there (a bus entry meeting more than one bus direction,
    IsExplicitJunctionAllowed, sch_screen.cpp:498-504 at 9.0.8), so no single call repairs
    it: the issue carries a note saying so in place of a repair. Structural only; written
    outside the test's directory, as a sheet holding nothing but a bus."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        p = fresh(Path(work), "busy")
        bus(p, *A, *B)
        bus_entry(p, *P, 2.54, -2.54)
        (issue,) = _issues_of(p, "bus_entry_inside_wire")
    assert [ln["kind"] for ln in issue["lines"]] == ["bus"]
    assert "repair" not in issue and issue["note"], issue


def test_validate_hierarchy_reports_no_entry_end_on_a_kicad_10_file():
    """In a KiCad 10 file a wire through a bus-entry end is read alike by every reader that
    can load it: CleanUp breaks no line on load (schematic.cpp:1537-1728 at 10.0.7; its only
    BreakSegmentsOnJunctions, sch_line_wire_bus_tool.cpp:1459, is never called), and the
    graph joins items at their own connection points (connection_graph.cpp:1361-1392), a
    wire's being its ends, and a line inside it only under a label or a junction
    (1393-1429); a bus entry end inside a wire joins nothing but a bus there (1443-1462).
    The wire stays whole, so there is no cut to report. Written outside the test's
    directory, since the local kicad-cli 9 cannot load it."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        case = _ENTRY_THROUGH
        p = _entry_sheet(Path(work), "k10", case, case.lines, case.dots)
        _as_kicad10(p)
        issues = project.validate_hierarchy(schematic_path=p).issues
    assert issues == []


def _component_refs(root) -> tuple[Counter, bool]:
    """kicad-cli 9's netlist export: how many components it lists under each reference, over
    every sheet instance (it lists the units of a multi-unit part once), and whether it warns
    that the schematic has annotation errors (SCH_REFERENCE_LIST::CheckAnnotation,
    eeschema_jobs_handler.cpp:409-426 at 9.0.8)."""
    result, xml = _cli_report(
        ["sch", "export", "netlist", "--format", "kicadxml"], Path(root), "refs.xml"
    )
    if xml is None:
        raise RuntimeError(f"premise: kicad-cli exports no netlist: {result.stderr.strip()}")
    warned = "annotation errors" in result.stdout + result.stderr
    return Counter(comp.get("ref") for comp in ET.fromstring(xml).iter("comp")), warned


def _reused_shared(tmp_path) -> Path:
    """add_hierarchical_sheet twice on one child holding R1, as the tools build it: R1 has no
    instance entry for either sheet path, so both instances read R1."""
    d = tmp_path / "shared"
    project.create_project(directory=str(d), name="shared")
    root, child, pro = d / "shared.kicad_sch", d / "child.kicad_sch", d / "shared.kicad_pro"
    project.create_schematic(schematic_path=str(child))
    schematic.place_component(
        lib_id="Device:R",
        reference="R1",
        value="R1",
        x=50.8,
        y=50.8,
        symbol_lib_path=str(Path(__file__).parent / "fixtures" / "routing.kicad_sym"),
        schematic_path=str(child),
        project_path=str(pro),
    )
    project.add_hierarchical_sheet(str(root), "S1", str(child), [], project_path=str(pro))
    project.add_hierarchical_sheet(str(root), "S2", str(child), [], x=76.2, project_path=str(pro))
    return root


def _reused_apart(tmp_path) -> Path:
    """One child used twice, its parts annotated R1 and R2 in one instance and R101 and R102
    in the other, as KiCad annotates a reused sheet."""
    root = Path(fresh(tmp_path, "apart"))
    project_file(root.parent, "apart")
    child = root.parent / "child.kicad_sch"
    project.create_schematic(schematic_path=str(child))
    for ref, x in (("R1", 50.8), ("R2", 76.2)):
        place(str(child), "R", ref, x, 50.8)
    sheet(str(root), str(child), "S1", 25.4, 25.4)
    sheet(str(root), str(child), "S2", 76.2, 25.4, again={"R1": "R101", "R2": "R102"})
    return root


def _units(tmp_path, child_unit: int) -> Path:
    """RN1, an R_Network03_Split (three units), placed on the child as unit *child_unit* and on
    the root as unit 1: two units of one part, or one unit placed twice."""
    root = Path(fresh(tmp_path, f"units{child_unit}"))
    project_file(root.parent, root.stem)
    child = root.parent / "child.kicad_sch"
    project.create_schematic(schematic_path=str(child))
    place(str(child), "R_Network03_Split", "RN1", 101.6, 101.6, value="RN")
    set_unit(str(child), "RN1", child_unit)
    sheet(str(root), str(child), "S1", 25.4, 25.4)
    place(str(root), "R_Network03_Split", "RN1", 101.6, 101.6, value="RN")
    return root


@requires_cli
@pytest.mark.parametrize(
    "build",
    [_reused_shared, _reused_apart, lambda t: _units(t, 2), lambda t: _units(t, 1)],
    ids=["shared", "apart", "two_units_of_one_part", "one_unit_twice"],
)
def test_validate_hierarchy_reads_references_per_sheet_instance(tmp_path, build):
    """A reused sheet's symbol has one reference per instance: the instance entry for that
    sheet path, else its first entry, else its Reference property (SymRecord.at, after
    sch_symbol.cpp GetRef at 9.0.8). kicad-cli 9 judges: validate_hierarchy reports a
    duplicate wherever its netlist lists two components under one reference, and reports one
    exactly when it warns of annotation errors. Both instances of the shared child read R1;
    the child annotated apart has none; two units of one part share a reference, and one
    unit placed twice is a duplicate."""
    root = build(tmp_path)
    counts, warned = _component_refs(root)
    issues = project.validate_hierarchy(schematic_path=str(root)).issues
    dups = {i["reference"] for i in issues if i["type"] == "duplicate_ref"}
    assert dups >= {ref for ref, n in counts.items() if n > 1}
    assert bool(dups) == warned, (dups, warned)


def test_validate_hierarchy_notes_a_sheet_it_cannot_read():
    """A grandchild cut off half way, which no reader can parse: the sheet gets a note naming
    its file in place of its checks, and the rest of the hierarchy is still checked (the
    junction in the sheet after it). Written outside the test's directory, since the
    kicad-cli oracle refuses a hierarchy holding a sheet it cannot load."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        d = Path(work)
        project.create_project(directory=str(d), name="proj")
        root, child, cut, good = (d / f"{n}.kicad_sch" for n in ("proj", "child", "cut", "good"))
        for f in (child, cut, good):
            project.create_schematic(schematic_path=str(f))
        project.add_hierarchical_sheet(str(root), "Child", str(child), [])
        project.add_hierarchical_sheet(str(child), "Cut", str(cut), [])
        project.add_hierarchical_sheet(str(child), "Good", str(good), [], x=101.6)
        wire(str(good), *A, *B)
        junction(str(good), *P)
        data = read(cut)
        cut.write_bytes(data[: len(data) // 2])
        issues = project.validate_hierarchy(schematic_path=str(root)).issues
    (noted,) = [i for i in issues if i["type"] == "unreadable_file"]
    assert (noted["sheet_name"], noted["file_name"]) == ("Cut", "cut.kicad_sch")
    assert str(cut) in noted["note"], noted
    assert [i["file"] for i in issues if i["type"] == "junction_inside_wire"] == [str(good)]


def test_validate_hierarchy_bounds_the_instances_it_reads_references_at():
    """Thirteen files, each using the next twice: 8191 sheet instances, past the 4096 the walk
    reads references at, so reuse inside reuse cannot make the call run without end. The call
    returns, and one note says the references were not all checked. Structural only; written
    outside the test's directory, since the kicad-cli oracle would load every instance."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
        d = Path(work)
        project.create_project(directory=str(d), name="deep")
        files = [d / "deep.kicad_sch"] + [d / f"f{k}.kicad_sch" for k in range(1, 13)]
        for f in files[1:]:
            project.create_schematic(schematic_path=str(f))
        for parent, child in zip(files, files[1:]):
            for name, x in (("A", 25.4), ("B", 76.2)):
                project.add_hierarchical_sheet(str(parent), name, str(child), [], x=x)
        issues = project.validate_hierarchy(schematic_path=str(files[0])).issues
    assert [i["type"] for i in issues] == ["references_unchecked"]


def test_the_repair_named_for_a_junction_off_the_page_works(tmp_path):
    """The T an earlier version wrote, on a sheet whose page now ends short of it. KiCad reads
    items off the page as any other, and add_junctions at a junction already there places
    nothing, it only splits, so the page border does not stop it: the repair named runs and
    the re-run is clean."""
    p = _sheet(tmp_path, "offpage", _T, _T.lines, _T.dots)
    schematic.set_page_size("User", width=60, height=60, schematic_path=p)
    (issue,) = _junction_issues(p)
    _repair(issue)
    assert _junction_issues(p) == []
    if cli9():
        assert joins(p, _T.pins) == one_net(*_T.pins)
