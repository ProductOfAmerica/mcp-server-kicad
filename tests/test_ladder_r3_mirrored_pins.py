"""Rung 3: validate_hierarchy lists what releases before 0.21.0 left at mirrored pin points.

Releases up to 0.20.1 put a pin of a placed symbol where mirroring first and rotating second
puts it. KiCad rotates first: its parser sets the rotation from (at) and composes a later
(mirror) onto it (kicad_ref.transform, from sch_io_kicad_sexpr_parser.cpp:3071-3098 and
SCH_SYMBOL::SetOrientation, sch_symbol.cpp:1766-1916 at 9.0.8). So at rotation 90 or 270 with
a mirror their point is the pin's end reflected through the symbol's origin, and
no_connect_pin, wire_pins_to_net and connect_pins wrote there.

Measured 2026-10-16 with mcp-server-kicad 0.20.1 from PyPI, whose sources are the v0.20.1
tag's: R, D and Q_NPN_BCE (routing_fixtures) in all 12 states, every pin, each of those three
tools once per pin, 252 calls. Every call wrote at the point its get_pin_positions reported
for the pin: the flag, the stub's first end, the route's first end and the Net-(REF-PIN)
label it put there. OLD below is that point. kicad-cli 9.0.8 read each write at 90 or 270
with a mirror as missing its pin: on R and D it flagged or wired the other pin, on the
transistor nothing.

tests/fixtures/mirrored_0_20_1.kicad_sch is a sheet 0.20.1 wrote, from the library in
tests/fixtures/routing.kicad_sym (_FIXTURE says with which calls).

Judges, none of them the code under test: OLD, the measured points; routing_fixtures.ORIENTED,
KiCad's pin ends (measured, and checked against the transform port by test_ladder_r2_judges);
kicad-cli 9's netlist of a copy stripped to its symbols, with a label at each point in
question, which names the pins KiCad draws there (_pins_kicad_draws_at); kicad-cli's reading
of each pin after its repair; for a repair that joins two pins, kicad-cli's verdict on what the
connect_pins call the report used to name does (_HAZARDS) and geom_judge's on whether a route
runs over another pin end; for the repairs of one report made in its order, kicad-cli's netlist
after them, and for the copy of the sheet the report checks them on, the tools' own writes on
the file (the report run again between real repairs; the wires and junctions the calls leave on
the file against those on the copy, read with KiCad's lexer) and their own refusals; and the demo
sheets KiCad ships.
"""

from __future__ import annotations

import os
import random
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import NamedTuple

import geom_gen as G
import geom_judge as GJ
import kicad_corpus as K
import pytest
import sch_judge as S
import sexpr_gen
from conftest import _nightly_skip_reason, netlist_nodes, requires_cli, run_erc
from hypothesis import example, given, settings
from hypothesis import strategies as st
from kicad_ref import dsn_lexer as L
from kicad_ref import transform as T
from netlist_oracle import bare_name, judge, nets
from routing_fixtures import (
    ORIENTED,
    bus,
    fresh,
    label,
    netclass_flag,
    no_connect,
    place,
    project_file,
    rename_ref,
    set_lib_name,
    sheet,
    stub,
    wire,
)

from mcp_server_kicad import _connectivity as C
from mcp_server_kicad import _cst, project, schematic

FIXTURE = Path(__file__).parent / "fixtures" / "mirrored_0_20_1.kicad_sch"
NEW = ("no_connect_off_pin", "wire_end_off_pin")

#: Where mcp-server-kicad 0.20.1 put each pin, relative to the symbol's origin in mm, per
#: (rotation, mirror), pins in number order: the point its get_pin_positions reported and its
#: no_connect_pin, wire_pins_to_net and connect_pins wrote at (measured, module docstring).
OLD = {
    "R": {
        (0, ""): [(0, -3.81), (0, 3.81)],
        (90, ""): [(-3.81, 0), (3.81, 0)],
        (180, ""): [(0, 3.81), (0, -3.81)],
        (270, ""): [(3.81, 0), (-3.81, 0)],
        (0, "x"): [(0, 3.81), (0, -3.81)],
        (90, "x"): [(3.81, 0), (-3.81, 0)],
        (180, "x"): [(0, -3.81), (0, 3.81)],
        (270, "x"): [(-3.81, 0), (3.81, 0)],
        (0, "y"): [(0, -3.81), (0, 3.81)],
        (90, "y"): [(-3.81, 0), (3.81, 0)],
        (180, "y"): [(0, 3.81), (0, -3.81)],
        (270, "y"): [(3.81, 0), (-3.81, 0)],
    },
    "D": {
        (0, ""): [(-3.81, 0), (3.81, 0)],
        (90, ""): [(0, 3.81), (0, -3.81)],
        (180, ""): [(3.81, 0), (-3.81, 0)],
        (270, ""): [(0, -3.81), (0, 3.81)],
        (0, "x"): [(-3.81, 0), (3.81, 0)],
        (90, "x"): [(0, 3.81), (0, -3.81)],
        (180, "x"): [(3.81, 0), (-3.81, 0)],
        (270, "x"): [(0, -3.81), (0, 3.81)],
        (0, "y"): [(3.81, 0), (-3.81, 0)],
        (90, "y"): [(0, -3.81), (0, 3.81)],
        (180, "y"): [(-3.81, 0), (3.81, 0)],
        (270, "y"): [(0, 3.81), (0, -3.81)],
    },
    "Q_NPN_BCE": {
        (0, ""): [(-5.08, 0), (2.54, -5.08), (2.54, 5.08)],
        (90, ""): [(0, 5.08), (-5.08, -2.54), (5.08, -2.54)],
        (180, ""): [(5.08, 0), (-2.54, 5.08), (-2.54, -5.08)],
        (270, ""): [(0, -5.08), (5.08, 2.54), (-5.08, 2.54)],
        (0, "x"): [(-5.08, 0), (2.54, 5.08), (2.54, -5.08)],
        (90, "x"): [(0, 5.08), (5.08, -2.54), (-5.08, -2.54)],
        (180, "x"): [(5.08, 0), (-2.54, -5.08), (-2.54, 5.08)],
        (270, "x"): [(0, -5.08), (-5.08, 2.54), (5.08, 2.54)],
        (0, "y"): [(5.08, 0), (-2.54, -5.08), (-2.54, 5.08)],
        (90, "y"): [(0, -5.08), (-5.08, 2.54), (5.08, 2.54)],
        (180, "y"): [(-5.08, 0), (2.54, 5.08), (2.54, -5.08)],
        (270, "y"): [(0, 5.08), (5.08, -2.54), (-5.08, -2.54)],
    },
}
_PREFIX = {"R": "R", "D": "D", "Q_NPN_BCE": "Q"}
_MIRRORED = ((90, "x"), (90, "y"), (270, "x"), (270, "y"))
_TOOLS = ("no_connect_pin", "wire_pins_to_net", "connect_pins")


def _at(origin, offset) -> tuple[int, int]:
    """origin plus an offset, both in mm, in internal units as KiCad reads them."""
    return S.iu_point((origin[0] + offset[0], origin[1] + offset[1]))


def _new_issues(path) -> list[dict]:
    issues = project.validate_hierarchy(schematic_path=str(path)).issues
    return [i for i in issues if i["type"] in NEW]


def _key(issue) -> tuple:
    """(type, reference, pin, the point, KiCad's pin end), points in internal units."""
    return (
        issue["type"],
        issue["reference"],
        issue["pin"],
        S.iu_point((issue["x"], issue["y"])),
        S.iu_point((issue["pin_x"], issue["pin_y"])),
    )


# ---------------------------------------------------------------------------------------------
# The judges
# ---------------------------------------------------------------------------------------------


def test_0_20_1_reflected_every_pin_at_90_and_270_with_a_mirror():
    """The measurement against KiCad's pin ends: at rotation 90 or 270 with a mirror every
    point 0.20.1 used is KiCad's end reflected through the symbol's origin, and in the other
    eight states it is KiCad's end. That reflection, and nothing else, is what the ADR
    inferred; here it is measured on every pin of R, D and Q_NPN_BCE."""
    for symbol, states in OLD.items():
        for state, offsets in states.items():
            kicad = [(x, y) for x, y, _out in ORIENTED[symbol][state]]
            flip = -1 if state in _MIRRORED else 1
            assert offsets == [(flip * x, flip * y) for x, y in kicad], (symbol, state)


_PROBED = (
    b"wire",
    b"bus",
    b"bus_entry",
    b"junction",
    b"no_connect",
    b"label",
    b"global_label",
    b"hierarchical_label",
)


def _pins_kicad_draws_at(path, points) -> dict:
    """{point: the (reference, pin) KiCad draws there} for points in internal units.

    kicad-cli exports the netlist of a copy of the sheet holding only its symbols and one
    label per point: KiCad joins a label to exactly the pins whose end is its point, so the
    label's net lists them. The copy is cut with the lexer port's byte offsets, not the CST."""
    data = Path(path).read_bytes()
    (root,) = L.tree(data, knows_bar=L.knows_bar_of(data))
    pieces, last = [], 0
    for item in root.items:
        if item.kind == L.GROUP and item.items and item.items[0].value in _PROBED:
            pieces.append(data[last : item.start])
            last = item.end
    pieces.append(data[last : root.end - 1])
    names = {}
    for k, q in enumerate(sorted(set(points))):
        names[f"PROBE_{k}"] = q
        pieces.append(
            f'\n\t(label "PROBE_{k}" (at {G.sch_mm(q[0])} {G.sch_mm(q[1])} 0)'
            f' (effects (font (size 1.27 1.27))) (uuid "{uuid.uuid4()}"))\n'.encode()
        )
    pieces.append(data[root.end - 1 :])
    found = {q: frozenset() for q in names.values()}
    with tempfile.TemporaryDirectory() as work:
        probe = Path(work) / "probe.kicad_sch"
        probe.write_bytes(b"".join(pieces))
        for _code, name, _cls, nodes in nets(probe):
            if bare_name(name) in names:
                found[names[bare_name(name)]] = nodes
    return found


# ---------------------------------------------------------------------------------------------
# The sheet 0.20.1 wrote
# ---------------------------------------------------------------------------------------------


class _Call(NamedTuple):
    """One 0.20.1 call that wrote tests/fixtures/mirrored_0_20_1.kicad_sch: the tool, the
    symbol it was asked about (its library name, reference, state and origin) and the pin; a
    connect_pins call ran to pin 1 of an unrotated resistor ``anchor``."""

    tool: str
    symbol: str
    ref: str
    state: tuple[int, str]
    origin: tuple[float, float]
    pin: str
    anchor: str | None = None

    @property
    def old(self) -> tuple[int, int]:
        return _at(self.origin, OLD[self.symbol][self.state][int(self.pin) - 1])

    @property
    def end(self) -> tuple[int, int]:
        """KiCad's end of the pin."""
        x, y, _out = ORIENTED[self.symbol][self.state][int(self.pin) - 1]
        return _at(self.origin, (x, y))


def _fixture_calls() -> list[_Call]:
    """The calls in the order they were made, once 0.20.1's place_component had placed every
    symbol from tests/fixtures/routing.kicad_sym:

    - Q1 to Q12, Q_NPN_BCE at y 50.8 and x 25.4 + 20.32 (n - 1), in the four states at 90
      and 270 with a mirror, three each: no_connect_pin, wire_pins_to_net (direction auto,
      net N_<ref>_<pin>) and connect_pins to RA<n>:1 (R at x - 7.62, y 88.9), on pin (state
      index + tool index) % 3 + 1;
    - the two-pin case at y 127: R1 to R3 and D1 to D3 at x 25.4 + 30.48 i, the anchors RB<i>
      at x - 7.62, y 152.4;
    - unaffected states at y 177.8: Q20 to Q23, the anchor RC3 at x - 7.62, y 203.2."""
    calls = []
    for s, state in enumerate(_MIRRORED):
        for t, tool in enumerate(_TOOLS):
            n = 3 * s + t + 1
            x = 25.4 + 20.32 * (n - 1)
            anchor = f"RA{n}" if tool == "connect_pins" else None
            calls.append(
                _Call(tool, "Q_NPN_BCE", f"Q{n}", state, (x, 50.8), str((s + t) % 3 + 1), anchor)
            )
    two = [
        ("R", "R1", (90, "x"), "no_connect_pin", "1"),
        ("R", "R2", (270, "y"), "wire_pins_to_net", "1"),
        ("R", "R3", (90, "y"), "connect_pins", "2"),
        ("D", "D1", (270, "x"), "no_connect_pin", "2"),
        ("D", "D2", (90, "y"), "wire_pins_to_net", "1"),
        ("D", "D3", (270, "y"), "connect_pins", "1"),
    ]
    for i, (symbol, ref, state, tool, pin) in enumerate(two):
        anchor = f"RB{i + 1}" if tool == "connect_pins" else None
        calls.append(_Call(tool, symbol, ref, state, (25.4 + 30.48 * i, 127), pin, anchor))
    plain = [
        ("Q20", (90, ""), "no_connect_pin", "1"),
        ("Q21", (270, ""), "wire_pins_to_net", "2"),
        ("Q22", (0, "x"), "connect_pins", "3"),
        ("Q23", (180, "y"), "no_connect_pin", "2"),
    ]
    for i, (ref, state, tool, pin) in enumerate(plain):
        anchor = f"RC{i + 1}" if tool == "connect_pins" else None
        calls.append(_Call(tool, "Q_NPN_BCE", ref, state, (25.4 + 30.48 * i, 177.8), pin, anchor))
    return calls


_FIXTURE = _fixture_calls()
#: The calls whose point no pin of KiCad's occupies: the transistors in the mirrored states.
_STRAYED = [c for c in _FIXTURE if c.symbol == "Q_NPN_BCE" and c.state in _MIRRORED]


#: The y of the anchor resistors the connect_pins calls ran to, per anchor prefix, each at the
#: x of its part less 7.62 (_fixture_calls).
_ANCHOR_Y = {"RA": 88.9, "RB": 152.4, "RC": 203.2}


def _fixture_pin_ends() -> dict[tuple[str, str], tuple[int, int]]:
    """{(reference, pin): KiCad's end of it} for every part the calls placed, from ORIENTED."""
    ends = {}
    for c in _FIXTURE:
        for k, (x, y, _out) in enumerate(ORIENTED[c.symbol][c.state]):
            ends[(c.ref, str(k + 1))] = _at(c.origin, (x, y))
        if c.anchor:
            origin = (c.origin[0] - 7.62, _ANCHOR_Y[c.anchor[:2]])
            for k, (x, y, _out) in enumerate(ORIENTED["R"][(0, "")]):
                ends[(c.anchor, str(k + 1))] = _at(origin, (x, y))
    return ends


def _route_is_clear(first, second, ends) -> bool:
    """Whether connect_pins(first, second) writes a route that runs over no other pin end:
    straight when the pins share an x or a y, else horizontal from the first pin's end to the
    second's x and then vertical (its documented route). *first* and *second* are (reference,
    pin); *ends* is _fixture_pin_ends."""
    a, b = ends[first], ends[second]
    turns = [a, b] if a[0] == b[0] or a[1] == b[1] else [a, (b[0], a[1]), b]
    others = [q for node, q in ends.items() if node not in (first, second)]
    return not any(GJ.on_segment(q, s, e) for q in others for s, e in zip(turns, turns[1:]))


def _copy_fixture(tmp_path) -> str:
    folder = tmp_path / "old"
    folder.mkdir()
    path = folder / "old.kicad_sch"
    shutil.copyfile(FIXTURE, path)
    return str(path)


def _expected(call: _Call) -> tuple:
    kind = "no_connect_off_pin" if call.tool == "no_connect_pin" else "wire_end_off_pin"
    return kind, call.ref, call.pin, call.old, call.end


def test_validate_hierarchy_reports_what_0_20_1_left(tmp_path):
    """The sheet 0.20.1 wrote: each of its twelve writes on a transistor at 90 or 270 with a
    mirror is reported, a flag as no_connect_off_pin and a stub's or a route's first end as
    wire_end_off_pin, naming the symbol, the pin, the point (OLD) and KiCad's end of the pin
    (ORIENTED). Its writes on R and D, which landed on the other pin's end, and its writes in
    the unaffected states, which landed on the pin, are not."""
    p = _copy_fixture(tmp_path)
    issues = _new_issues(p)
    assert sorted(map(_key, issues)) == sorted(map(_expected, _STRAYED))
    assert {i["file"] for i in issues} == {p}


def test_the_report_names_each_wire_and_how_to_join_its_pin(tmp_path):
    """Per issue on the 0.20.1 sheet: a flag carries a note and no repair, since no tool
    removes a flag by its position; a wire end names its wire, one of whose ends is the
    point, and as its repair the call that joins the pin to what the wire reaches, which for
    0.20.1's own writes is the call that wrote it, made again: wire_pins_to_net with the
    stub's net name, connect_pins to the route's other pin."""
    p = _copy_fixture(tmp_path)
    wires = {(line.start, line.end) for line in S.lines(Path(p).read_bytes())}
    by_key = {_key(i): i for i in _new_issues(p)}
    for call in _STRAYED:
        issue = by_key[_expected(call)]
        if call.tool == "no_connect_pin":
            assert "repair" not in issue, issue
            assert "KiCad's editor" in issue["note"] and "no_connect_pin" in issue["note"], issue
            continue
        w = issue["wire"]
        ends = (S.iu_point((w["x1"], w["y1"])), S.iu_point((w["x2"], w["y2"])))
        assert ends in wires and call.old in ends, (call, w)
        if call.tool == "wire_pins_to_net":
            want = {
                "tool": "wire_pins_to_net",
                "arguments": {
                    "pins": [{"reference": call.ref, "pin": call.pin}],
                    "label_text": f"N_{call.ref}_{call.pin}",
                    "schematic_path": p,
                },
            }
        else:
            # Either pin may come first: the route runs horizontally from the first, and the
            # report names the order whose route crosses no other pin end. From three of the
            # transistors' pins it is the anchor's first, since the part's other pin lies on the
            # horizontal leg from the pin (Q3:2 on the leg from Q3:3, say).
            ends = _fixture_pin_ends()
            pin, anchor = (call.ref, call.pin), (call.anchor, "1")
            first, second = (pin, anchor) if _route_is_clear(pin, anchor, ends) else (anchor, pin)
            assert _route_is_clear(first, second, ends), call  # premise: one order is clear
            want = {
                "tool": "connect_pins",
                "arguments": {
                    "ref1": first[0],
                    "pin1": first[1],
                    "ref2": second[0],
                    "pin2": second[1],
                    "schematic_path": p,
                },
            }
        assert issue["repair"] == want, issue


@requires_cli
def test_kicad_draws_no_pin_where_the_report_points(tmp_path):
    """kicad-cli judges the points of the 0.20.1 sheet: at each reported point it draws no
    pin, at each reported pin end it draws that pin, and at the points of the two-pin case,
    which are not reported, it draws the other pin of the part."""
    p = _copy_fixture(tmp_path)
    issues = _new_issues(p)
    assert len(issues) == len(_STRAYED)
    two = [c for c in _FIXTURE if c.symbol in ("R", "D")]
    points = [_key(i)[3] for i in issues] + [_key(i)[4] for i in issues] + [c.old for c in two]
    drawn = _pins_kicad_draws_at(p, points)
    for issue in issues:
        _kind, ref, pin, at, end = _key(issue)
        assert drawn[at] == frozenset(), (issue, drawn[at])
        assert drawn[end] == {(ref, pin)}, (issue, drawn[end])
    for call in two:
        other = {"1": "2", "2": "1"}[call.pin]
        assert drawn[call.old] == {(call.ref, other)}, (call, drawn[call.old])


def _delete_flag(path, at) -> None:
    """Delete the no-connect flag at ``at`` (internal units), as the user does in KiCad's
    editor: its list goes from the file, every other byte stays."""
    data = Path(path).read_bytes()
    (root,) = L.tree(data, knows_bar=L.knows_bar_of(data))
    spans = []
    for item in root.items:
        if item.kind != L.GROUP or not item.items or item.items[0].value != b"no_connect":
            continue
        (where,) = [g for g in item.items[1:] if g.kind == L.GROUP and g.items[0].value == b"at"]
        if S.iu_point((float(where.items[1].value), float(where.items[2].value))) == at:
            spans.append((item.start, item.end))
    if len(spans) != 1:
        raise RuntimeError(f"premise: {len(spans)} flags at {at}")
    (start, end) = spans[0]
    Path(path).write_bytes(data[:start] + data[end:])


def test_the_repairs_clear_the_report(tmp_path):
    """Made as named, each repair clears its issue: a wire end once the call joined its pin to
    the wire's net, a flag once the user deleted it in KiCad's editor and called the
    no_connect_pin its note names. The re-run reports nothing of either kind."""
    p = _copy_fixture(tmp_path)
    issues = _new_issues(p)
    assert len(issues) == len(_STRAYED)
    for issue in issues:
        if "repair" in issue:
            getattr(schematic, issue["repair"]["tool"])(**issue["repair"]["arguments"])
        else:
            _delete_flag(p, S.iu_point((issue["x"], issue["y"])))
            schematic.no_connect_pin(issue["reference"], issue["pin"], schematic_path=p)
    assert _new_issues(p) == []


@requires_cli
def test_after_the_repairs_kicad_reads_each_pin_as_0_20_1_meant_it(tmp_path):
    """kicad-cli's netlist after the repairs of the 0.20.1 sheet: each flagged transistor
    pin is a no-connect pin (+no_connect), each stubbed one is on its stub's net, each routed
    one shares its anchor's net, and against the netlist before, nothing else merged, split
    or was renamed."""
    p = _copy_fixture(tmp_path)
    before = nets(p)
    issues = _new_issues(p)
    assert len(issues) == len(_STRAYED)
    for issue in issues:
        if "repair" in issue:
            getattr(schematic, issue["repair"]["tool"])(**issue["repair"]["arguments"])
        else:
            _delete_flag(p, S.iu_point((issue["x"], issue["y"])))
            schematic.no_connect_pin(issue["reference"], issue["pin"], schematic_path=p)
    after = nets(p)
    groups = [{(c.ref, c.pin), (c.anchor, "1")} if c.anchor else {(c.ref, c.pin)} for c in _STRAYED]
    verdict = judge(before, after, groups)
    assert not verdict.wrong, verdict.problems()
    on = {node: (name, nodes) for _c, name, _k, nodes in after for node in nodes}
    types = {node: t for net in netlist_nodes(p).values() for node, t in net.items()}
    for call in _STRAYED:
        node = (call.ref, call.pin)
        if call.tool == "no_connect_pin":
            assert types[node].endswith("+no_connect"), (call, types[node])
        elif call.tool == "wire_pins_to_net":
            assert bare_name(on[node][0]) == f"N_{call.ref}_{call.pin}", (call, on[node])
        else:
            assert on[node][1] == {node, (call.anchor, "1")}, (call, on[node])


def test_validate_hierarchy_does_not_write_the_sheet_it_reports_on(tmp_path):
    """The check only reads: the 0.20.1 sheet is byte-identical after it, and no file
    appears beside it."""
    p = _copy_fixture(tmp_path)
    before = {f: f.read_bytes() for f in Path(p).parent.iterdir()}
    assert len(_new_issues(p)) == len(_STRAYED)
    assert {f: f.read_bytes() for f in Path(p).parent.iterdir()} == before


def test_a_sub_sheet_is_reported_with_its_own_file(tmp_path):
    """The 0.20.1 sheet used as a sub-sheet of a project's root: its issues are reported as
    on its own file, the root's reporting none."""
    d = tmp_path / "proj"
    project.create_project(directory=str(d), name="proj")
    child = d / "old.kicad_sch"
    shutil.copyfile(FIXTURE, child)
    project.add_hierarchical_sheet(str(d / "proj.kicad_sch"), "Old", str(child), [])
    issues = _new_issues(d / "proj.kicad_sch")
    assert sorted(map(_key, issues)) == sorted(map(_expected, _STRAYED))
    assert {i["file"] for i in issues} == {str(child)}


# ---------------------------------------------------------------------------------------------
# Every state, generated placements
# ---------------------------------------------------------------------------------------------

_SYMBOLS = tuple(OLD)


def _flags_everywhere(tmp_path) -> tuple[str, dict]:
    """R, D and Q_NPN_BCE in all 12 states (36 parts, placed by place_component), a
    no-connect flag at every point 0.20.1 used for a pin of each. Returns the path and
    {(reference, pin): (old point, KiCad's end)}."""
    p = fresh(tmp_path, "sweep")
    points = {}
    for row, symbol in enumerate(_SYMBOLS):
        for i, state in enumerate(G.STATES):
            ref = f"{_PREFIX[symbol]}{i + 1}"
            origin = (25.4 + 22.86 * i, 50.8 + 50.8 * row)
            place(p, symbol, ref, *origin, rot=state[0], mirror=state[1])
            for k, offset in enumerate(OLD[symbol][state]):
                old = _at(origin, offset)
                x, y, _out = ORIENTED[symbol][state][k]
                points[(ref, str(k + 1))] = (old, _at(origin, (x, y)))
    for old, _end in points.values():
        no_connect(p, old[0] / 1e4, old[1] / 1e4)
    return p, points


def test_a_flag_at_every_old_point_is_reported_where_no_pin_is_drawn(tmp_path):
    """Every pin of R, D and Q_NPN_BCE in every state, a flag at its old point: exactly the
    transistor's twelve at 90 or 270 with a mirror are reported. In the other states the
    flag is on its own pin; on R and D at 90 or 270 with a mirror it is on the other pin
    (ORIENTED's ends say so, kicad-cli's in the next test)."""
    p, points = _flags_everywhere(tmp_path)
    ends = {end for _old, end in points.values()}
    want = sorted(
        ("no_connect_off_pin", ref, pin, old, end)
        for (ref, pin), (old, end) in points.items()
        if old != end and old not in ends
    )
    assert len(want) == 12 and {ref[0] for _k, ref, *_ in want} == {"Q"}
    assert sorted(map(_key, _new_issues(p))) == want


@requires_cli
def test_kicad_draws_a_pin_at_every_old_point_not_reported(tmp_path):
    """kicad-cli judges the sweep: at every old point reported it draws no pin, at every
    other old point it draws a pin, the part's own in the eight unaffected states and the
    other pin of R and D at 90 or 270 with a mirror."""
    p, points = _flags_everywhere(tmp_path)
    reported = {_key(i)[3] for i in _new_issues(p)}
    drawn = _pins_kicad_draws_at(p, [old for old, _end in points.values()])
    for (ref, pin), (old, end) in points.items():
        if old in reported:
            assert drawn[old] == frozenset(), (ref, pin, drawn[old])
        elif old == end:
            assert drawn[old] == {(ref, pin)}, (ref, pin, drawn[old])
        else:
            assert drawn[old] == {(ref, {"1": "2", "2": "1"}[pin])}, (ref, pin, drawn[old])


#: What a generated case writes at one pin: nothing; at its old point a flag, a stub (a wire
#: out along the pin's direction, as 0.20.1 drew it, with a label at its far end), or both;
#: or a flag or a stub at KiCad's end of the pin.
_ACTIONS = ("none", "flag", "stub", "flag+stub", "flag@end", "stub@end")


@st.composite
def _cases(draw):
    """1 to 4 parts, each (symbol, state, an action per pin)."""
    parts = []
    for _ in range(draw(st.integers(1, 4))):
        symbol = draw(st.sampled_from(_SYMBOLS))
        state = draw(G.symbol_states())
        actions = tuple(draw(st.sampled_from(_ACTIONS)) for _ in OLD[symbol][state])
        parts.append((symbol, state, actions))
    return parts


def _build(path, parts) -> tuple[set, list]:
    """Place the parts 50.8 mm apart and write each action. Returns the issues expected,
    by OLD and ORIENTED alone: a flag or a stub at an old point that is no pin end of the
    part, and the old points left out for lying on one, with the part's reference."""
    want, skipped = set(), []
    for i, (symbol, state, actions) in enumerate(parts):
        ref, origin = f"{_PREFIX[symbol]}{i + 1}", (38.1 + 50.8 * i, 101.6)
        place(path, symbol, ref, *origin, rot=state[0], mirror=state[1])
        pins = ORIENTED[symbol][state]
        ends = {_at(origin, (x, y)) for x, y, _out in pins}
        for k, action in enumerate(actions):
            old, (x, y, out) = _at(origin, OLD[symbol][state][k]), pins[k]
            end = _at(origin, (x, y))
            at = end if action.endswith("@end") else old
            mm = (at[0] / 1e4, at[1] / 1e4)
            if "flag" in action:
                no_connect(path, *mm)
            if "stub" in action:
                far = (mm[0] + 2.54 * out[0], mm[1] + 2.54 * out[1])
                wire(path, *mm, *far)
                label(path, f"S{i}_{k}", *far)
            if action == "none" or action.endswith("@end") or old == end:
                continue
            if old in ends:
                skipped.append((ref, old))
                continue
            if "flag" in action:
                want.add(("no_connect_off_pin", ref, str(k + 1), old, end))
            if "stub" in action:
                want.add(("wire_end_off_pin", ref, str(k + 1), old, end))
    return want, skipped


_EVERY_MIRRORED = [("Q_NPN_BCE", s, ("flag", "stub", "flag+stub")) for s in _MIRRORED]


@settings(max_examples=sexpr_gen.budget(8))
@example(parts=_EVERY_MIRRORED)
@example(parts=[("R", (90, "x"), ("stub", "flag")), ("D", (270, "y"), ("flag+stub", "stub@end"))])
@given(parts=_cases())
def test_generated_damage_is_reported_where_no_pin_is_drawn(parts):
    """Generated parts in any of the 12 states with flags and stubs at their old points or
    at their pins: the report is exactly the flags and stub ends at old points that are no
    pin end of the part, each a flag with a note and a stub end with its wire and the
    wire_pins_to_net call that puts the pin on the stub's net."""
    with tempfile.TemporaryDirectory() as work:
        p = fresh(Path(work), "gen")
        want, _skipped = _build(p, parts)
        issues = _new_issues(p)
    assert {_key(i) for i in issues} == want
    for issue in issues:
        if issue["type"] == "no_connect_off_pin":
            assert "note" in issue and "repair" not in issue, issue
        else:
            assert issue["repair"]["tool"] == "wire_pins_to_net", issue


@pytest.mark.nightly
@requires_cli
@settings(max_examples=sexpr_gen.budget(25))
@given(parts=_cases())
def test_nightly_kicad_draws_no_pin_at_generated_reports(parts):
    """The generated cases judged by kicad-cli: no pin at a reported point, a pin of the
    part at every old point left out (test_kicad_draws_a_pin_at_every_old_point_not_reported
    is its sibling on every push)."""
    with tempfile.TemporaryDirectory() as work:
        p = fresh(Path(work), "gen")
        _want, skipped = _build(p, parts)
        reported = [_key(i)[3] for i in _new_issues(p)]
        drawn = _pins_kicad_draws_at(p, reported + [old for _ref, old in skipped])
    assert all(drawn[q] == frozenset() for q in reported), drawn
    assert all(any(r == ref for r, _pin in drawn[old]) for ref, old in skipped), drawn


# ---------------------------------------------------------------------------------------------
# What is not reported
# ---------------------------------------------------------------------------------------------

_Q_AT = (101.6, 101.6)


def _q_sheet(tmp_path, name: str) -> tuple[str, tuple[float, float], tuple[int, int]]:
    """Q1, a Q_NPN_BCE at 90 with mirror x. Returns the path, pin 1's old point in mm and
    KiCad's end of pin 1 in internal units."""
    p = fresh(tmp_path, name)
    place(p, "Q_NPN_BCE", "Q1", *_Q_AT, rot=90, mirror="x")
    dx, dy = OLD["Q_NPN_BCE"][(90, "x")][0]
    x, y, _out = ORIENTED["Q_NPN_BCE"][(90, "x")][0]
    return p, (_Q_AT[0] + dx, _Q_AT[1] + dy), _at(_Q_AT, (x, y))


def _stub(p, at: tuple[float, float]) -> None:
    """A wire from ``at`` 2.54 mm right, labelled C at its far end: its end at ``at``
    dangles, so it is reported."""
    wire(p, *at, at[0] + 2.54, at[1])
    label(p, "C", at[0] + 2.54, at[1])


def _corner(p, at: tuple[float, float]) -> None:
    wire(p, *at, at[0], at[1] + 2.54)


def _tee(p, at: tuple[float, float]) -> None:
    wire(p, at[0] - 2.54, at[1], *at)
    wire(p, *at, at[0], at[1] + 2.54)
    schematic.add_junctions([{"x": at[0], "y": at[1]}], schematic_path=p)


def _named(p, at: tuple[float, float]) -> None:
    label(p, "OTHER", *at)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_corner, id="a_corner"),
        pytest.param(_tee, id="a_tee_as_kicad_saves_it"),
        pytest.param(_named, id="a_label_of_another_name"),
    ],
)
def test_a_wire_end_that_meets_something_else_there_is_not_reported(tmp_path, build):
    """A stub whose end dangles at an old point no pin occupies is reported; once that end
    meets something else there it is not: a second wire's end (a corner), KiCad's T (three
    wire ends and a junction), a label. By KiCad's rule none of those ends dangles then
    (SCH_LINE::UpdateDanglingState, sch_line.cpp:596-640 at 9.0.8: another item's end point
    there). KiCad's own files agree: in its 9.0.8 demos, of the 6,002 points where a pin's
    end reflected through its symbol's origin finds no pin, whatever the symbol's state, 27
    hold wire ends, 77 in all, every one meeting another wire's end, a junction or a label
    and none dangling (measured 2026-10-16)."""
    p, at, _end = _q_sheet(tmp_path, "met")
    _stub(p, at)
    assert [i["type"] for i in _new_issues(p)] == ["wire_end_off_pin"]
    build(p, at)
    assert _new_issues(p) == []


@pytest.mark.parametrize(
    "text, reported",
    [
        pytest.param("Net-(Q1-1)", True, id="by_number"),
        pytest.param("Net-(Q1-B)", True, id="by_name"),
        pytest.param("Net-(Q1-2)", False, id="another_pin"),
    ],
)
def test_only_the_label_0_20_1_wrote_there_does_not_count(tmp_path, text, reported):
    """0.20.1's connect_pins put a label Net-(<ref1>-<pin1>) at the route's first end, the old
    point, with the pin as the call named it, number or name (Q_NPN_BCE's pin 1 is B). A
    wire end there under that label for this very pin is reported; under the same label for
    another pin it is a label like any other, and is not."""
    p, at, _end = _q_sheet(tmp_path, "own")
    wire(p, *at, at[0] + 2.54, at[1])
    label(p, text, *at)
    issues = _new_issues(p)
    assert [i["type"] for i in issues] == (["wire_end_off_pin"] if reported else [])
    for issue in issues:  # an auto name wire_pins_to_net refuses, and no pin: no repair
        assert "repair" not in issue and "remove_wire(" in issue["note"], issue


def test_a_flag_on_a_hierarchical_label_is_not_reported(tmp_path):
    """A flag at Q1:1's old point is reported; with a hierarchical label at the same point it
    is not, since KiCad accepts a no-connect that shares its point with a hierarchical label
    or a sheet pin (ercCheckNoConnects, connection_graph.cpp:3735-3750 at 9.0.8)."""
    p, at, _end = _q_sheet(tmp_path, "port")
    no_connect(p, *at)
    assert [i["type"] for i in _new_issues(p)] == ["no_connect_off_pin"]
    label(p, "PORT", *at, kind="hierarchical_label")
    assert _new_issues(p) == []


@pytest.mark.parametrize(
    "oracle", [pytest.param(False, id="model"), pytest.param(True, id="kicad", marks=requires_cli)]
)
def test_a_flag_wired_to_its_pin_is_not_reported(tmp_path, oracle):
    """A flag at Q1:1's old point is reported; once a wire runs from the pin KiCad draws to
    the flag it is not: the flag then marks that pin, the one pin on its net, which KiCad's
    ERC accepts (ercCheckNoConnects, connection_graph.cpp:3762-3798 at 9.0.8) and kicad-cli
    reads as a no-connect pin. Pin 1's end and its old point lie on one vertical line."""
    p, at, end = _q_sheet(tmp_path, "wired")
    no_connect(p, *at)
    assert [i["type"] for i in _new_issues(p)] == ["no_connect_off_pin"]
    wire(p, end[0] / 1e4, end[1] / 1e4, *at)
    assert _new_issues(p) == []
    if oracle:
        types = {node: t for net in netlist_nodes(p).values() for node, t in net.items()}
        assert types[("Q1", "1")].endswith("+no_connect"), types


def test_a_flag_note_says_whether_the_pin_is_flagged_already(tmp_path):
    """A flag at Q1:1's old point: its note names the no_connect_pin call that flags the pin
    KiCad draws, and once a flag sits there (no_connect_pin's), says so instead. Either way the
    flag at the old point is still reported: only KiCad's editor can delete it."""
    p, at, end = _q_sheet(tmp_path, "flags")
    no_connect(p, *at)
    (issue,) = _new_issues(p)
    assert issue["note"].endswith("then no_connect_pin('Q1', '1') flags the pin."), issue
    schematic.no_connect_pin("Q1", "1", schematic_path=p)
    (issue,) = _new_issues(p)
    assert _key(issue)[4] == end and issue["note"].endswith("the pin is flagged already.")


def test_a_repair_the_tool_would_refuse_is_a_note_with_its_refusal(tmp_path):
    """A stub labelled N at Q1:1's old point, with Q1:1 already on M at its end:
    wire_pins_to_net refuses to put the pin on N as well, since its net carries M ([names]),
    so the issue names no repair and its note gives that refusal. The tool itself confirms
    it: called as the note describes, it refuses and writes nothing."""
    from mcp.server.mcpserver.exceptions import ToolError

    p, at, _end = _q_sheet(tmp_path, "refused")
    wire(p, *at, at[0], at[1] - 2.54)
    label(p, "N", at[0], at[1] - 2.54)
    schematic.wire_pins_to_net([{"reference": "Q1", "pin": "1"}], "M", schematic_path=p)
    (issue,) = _new_issues(p)
    assert "repair" not in issue and "[names]" in issue["note"], issue
    before = Path(p).read_bytes()
    with pytest.raises(ToolError, match=r"\[names\]"):
        schematic.wire_pins_to_net([{"reference": "Q1", "pin": "1"}], "N", schematic_path=p)
    assert Path(p).read_bytes() == before


# ---------------------------------------------------------------------------------------------
# A repair is made only where the call it names is safe
# ---------------------------------------------------------------------------------------------

Pt = tuple[float, float]
_ROUTE_Q: Pt = (101.6, 76.2)
_ROUTE_R: Pt = (50.8, 96.52)


def _routed(tmp_path, name: str, r_at: Pt = _ROUTE_R) -> tuple[str, Pt, Pt, Pt]:
    """Q1 (Q_NPN_BCE at 90 with mirror x) at _ROUTE_Q, R1 at *r_at* and the route 0.20.1's
    connect_pins(Q1, 1, R1, 1) wrote: from the point it gave Q1:1, horizontal first, to R1:1
    (one wire when they share an x). Returns the path and, in mm, Q1:1's old point, KiCad's end
    of Q1:1 and R1:1's end."""
    p = fresh(tmp_path, name)
    place(p, "Q_NPN_BCE", "Q1", *_ROUTE_Q, rot=90, mirror="x")
    place(p, "R", "R1", *r_at)
    dx, dy = OLD["Q_NPN_BCE"][(90, "x")][0]
    ex, ey, _out = ORIENTED["Q_NPN_BCE"][(90, "x")][0]
    fx, fy, _out = ORIENTED["R"][(0, "")][0]
    old = (_ROUTE_Q[0] + dx, _ROUTE_Q[1] + dy)
    end = (_ROUTE_Q[0] + ex, _ROUTE_Q[1] + ey)
    far = (r_at[0] + fx, r_at[1] + fy)
    if old[0] != far[0]:
        wire(p, *old, far[0], old[1])
    wire(p, far[0], old[1], *far)
    return p, old, end, far


def _local_name(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """R1:1 on N by a local label, Q1:1 on M: joining the pins would join two named nets."""
    stub(p, *far, -2.54, 0, "N")
    stub(p, *end, 0, -2.54, "M")


def _global_name(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """The same with R1:1 on G by a global label, which wire_pins_to_net cannot put a pin on."""
    wire(p, *far, far[0] - 2.54, far[1])
    label(p, "G", far[0] - 2.54, far[1], rot=180, kind="global_label")
    stub(p, *end, 0, -2.54, "M")


def _flagged(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """Q1:1 carries a no-connect flag at KiCad's end of the pin."""
    no_connect(p, *end)


def _corner_pin(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """Pin 1 of R2 sits where the route turns with Q1:1 first, at (R1:1's x, Q1:1's y), and pin 1
    of R3 where it turns with R1:1 first, at (Q1:1's x, R1:1's y): neither call has a clear
    route."""
    place(p, "R", "R2", far[0], end[1] + 3.81)
    place(p, "R", "R3", end[0], far[1] + 3.81)


def _classed(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """Q1:1 is on net class HV by a directive at the end of its stub; R1:1 is on none."""
    project_file(Path(p).parent, Path(p).stem, classes=("HV",))
    wire(p, *end, end[0], end[1] - 2.54)
    netclass_flag(p, "HV", end[0], end[1] - 2.54)


def _on_a_bus(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """A bus ends on a stub of Q1:1, which wire_pins_to_net and connect_pins cannot judge."""
    wire(p, *end, end[0], end[1] - 2.54)
    bus(p, end[0], end[1] - 2.54, end[0] + 5.08, end[1] - 2.54)


def _unloadable(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """R2, elsewhere on the sheet, names a library symbol the file does not hold: KiCad stacks
    its pins at its origin, so the geometry of the sheet is not known."""
    place(p, "R", "R2", 152.4, 76.2)
    set_lib_name(p, "R2", "Gone")


def _shared_reference(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """R2, elsewhere, answers to R1 too: KiCad's netlist joins the pads of both."""
    place(p, "R", "R2", 152.4, 76.2)
    rename_ref(p, "R2", "R1")


def _shared_with_a_sheet(p: str, old: Pt, end: Pt, far: Pt) -> None:
    """A child sheet holds a resistor annotated R1 too, which KiCad's netlist joins to this one."""
    project_file(Path(p).parent, Path(p).stem)
    child = str(Path(p).parent / "c.kicad_sch")
    project.create_schematic(schematic_path=child)
    place(child, "R", "R1", 101.6, 101.6)
    sheet(p, child, "C", 152.4, 25.4)


#: hazard: (builder, the code the note carries, the kicad-cli reading of connect_pins' call
#: as the verdict's problem it shows, or None where the check is for what is not judged)
_HAZARDS = {
    "local_name": (_local_name, "names", "named_merge"),
    "global_name": (_global_name, "names", "named_merge"),
    "no_connect_flag": (_flagged, "nc_flag", "no_connect_connected"),
    "pin_at_the_corner": (_corner_pin, "touch", "bad_merges"),
    "net_class": (_classed, "netclass", "class_changes"),
    "bus": (_on_a_bus, "bus", None),
    "unloadable_symbol": (_unloadable, "derived", None),
    "shared_reference": (_shared_reference, "dup_ref", None),
    "reference_on_a_sheet": (_shared_with_a_sheet, "dup_ref", None),
}


#: The sheet with a symbol that names no library entry is one kicad-cli's ERC crashes on
#: (exit 0xC0000005, measured; test_routing_safety's PI-17), so the output oracle leaves it out.
_UNLOADABLE = pytest.mark.kicad_placeholder("unloadable_symbol/unloadable_symbol.kicad_sch")


@pytest.mark.parametrize(
    "hazard",
    [pytest.param(h, marks=_UNLOADABLE if h == "unloadable_symbol" else ()) for h in _HAZARDS],
)
def test_a_route_that_joins_what_it_should_not_is_a_note_not_a_repair(tmp_path, hazard):
    """Q1:1's route to R1:1, with one thing present that makes the join unsafe (_HAZARDS):
    connect_pins checks none of it, so the report may not name that call. The issue carries
    no repair and a note that gives the reason, as wire_pins_to_net's refusal is given."""
    build, code, _problem = _HAZARDS[hazard]
    p, *where = _routed(tmp_path, hazard)
    build(p, *where)
    (issue,) = _new_issues(p)
    assert issue["type"] == "wire_end_off_pin"
    assert "repair" not in issue, issue
    # One refusal for each call tried: both orders where the route is what is refused, else the
    # first call's alone, since the second would be refused for the same reason.
    assert issue["note"].count(f"[{code}]") == (2 if code in ("touch", "netclass") else 1), issue


@requires_cli
@pytest.mark.parametrize("hazard", [h for h, (_b, _c, problem) in _HAZARDS.items() if problem])
def test_kicad_reads_the_call_the_report_used_to_name_as_harm(tmp_path, hazard):
    """The judge of the test above, for the hazards kicad-cli can show: the connect_pins call
    the report used to name, made on a copy, merges two named nets (a local name and a global
    one), connects a pin its no-connect flag says is open (ERC's no_connect_connected), joins
    the pin of another part, or changes a net class."""
    build, _code, problem = _HAZARDS[hazard]
    p, *where = _routed(tmp_path, hazard)
    build(p, *where)
    before = nets(p)
    scratch = Path(p).with_name("applied.kicad_sch")
    shutil.copyfile(p, scratch)
    schematic.connect_pins("Q1", "1", "R1", "1", schematic_path=str(scratch))
    if problem == "no_connect_connected":
        found = {v["type"] for s in run_erc(scratch)["sheets"] for v in s["violations"]}
        assert problem in found, found
    else:
        verdict = judge(before, nets(scratch), [{("Q1", "1"), ("R1", "1")}])
        assert getattr(verdict, problem), verdict.problems()


@pytest.mark.parametrize(
    "oracle", [pytest.param(False, id="model"), pytest.param(True, id="kicad", marks=requires_cli)]
)
def test_a_blocked_route_is_tried_with_the_other_pin_first(tmp_path, oracle):
    """Pin 1 of R2 sits where connect_pins(Q1, 1, R1, 1) turns, so that call would join R2:1
    too. The call with R1:1 first turns elsewhere, and is the repair; made, kicad-cli puts
    Q1:1 and R1:1 on one net and joins nothing else."""
    p, old, end, far = _routed(tmp_path, "other")
    place(p, "R", "R2", far[0], end[1] + 3.81)
    (issue,) = _new_issues(p)
    assert issue["repair"] == {
        "tool": "connect_pins",
        "arguments": {"ref1": "R1", "pin1": "1", "ref2": "Q1", "pin2": "1", "schematic_path": p},
    }, issue
    if oracle:
        before = nets(p)
        schematic.connect_pins(**issue["repair"]["arguments"])
        verdict = judge(before, nets(p), [{("Q1", "1"), ("R1", "1")}])
        assert verdict.delivered and not verdict.wrong, verdict.problems()


#: R1 straight below Q1: the route 0.20.1 wrote and the one the repair would draw are both one
#: vertical wire, and the two calls (either pin first) draw the same one.
_BELOW = (_ROUTE_Q[0], 96.52)


@pytest.mark.parametrize(
    "oracle", [pytest.param(False, id="model"), pytest.param(True, id="kicad", marks=requires_cli)]
)
def test_a_straight_route_has_one_call_the_parts_pin_first(tmp_path, oracle):
    """Q1:1 and R1:1 share an x: connect_pins draws one wire, so there is one call, the part's
    pin first. Made, kicad-cli puts the two pins on one net and joins nothing else."""
    p, old, end, far = _routed(tmp_path, "straight", _BELOW)
    assert old[0] == far[0] == end[0]
    (issue,) = _new_issues(p)
    assert issue["repair"] == {
        "tool": "connect_pins",
        "arguments": {"ref1": "Q1", "pin1": "1", "ref2": "R1", "pin2": "1", "schematic_path": p},
    }, issue
    if oracle:
        before = nets(p)
        schematic.connect_pins(**issue["repair"]["arguments"])
        verdict = judge(before, nets(p), [{("Q1", "1"), ("R1", "1")}])
        assert verdict.delivered and not verdict.wrong, verdict.problems()


def test_a_straight_route_across_another_wire_is_one_refusal(tmp_path):
    """A wire of no net crosses the straight route between the old point and Q1:1's end, which
    ADR-R2's crossing ban refuses. Both calls draw that one wire, so the note gives the
    refusal once."""
    p, old, end, far = _routed(tmp_path, "crossed", _BELOW)
    wire(p, end[0] - 2.54, end[1] + 5.08, end[0] + 2.54, end[1] + 5.08)
    (issue,) = _new_issues(p)
    assert "repair" not in issue and issue["note"].count("[touch]") == 1, issue


def test_the_refusal_of_wire_pins_to_net_is_the_note_not_a_second_try(tmp_path):
    """R1:1 is on N by a local label and Q1:1 on M. wire_pins_to_net would put the pin on N and
    refuses ([names]), and the note says that; it does not go on to the other call. The tool
    confirms the refusal and writes nothing."""
    from mcp.server.mcpserver.exceptions import ToolError

    p, old, end, far = _routed(tmp_path, "second")
    _local_name(p, old, end, far)
    (issue,) = _new_issues(p)
    assert "repair" not in issue and "wire_pins_to_net would put the pin on 'N'" in issue["note"]
    before = Path(p).read_bytes()
    with pytest.raises(ToolError, match=r"\[names\]"):
        schematic.wire_pins_to_net([{"reference": "Q1", "pin": "1"}], "N", schematic_path=p)
    assert Path(p).read_bytes() == before


@pytest.mark.parametrize(
    "oracle", [pytest.param(False, id="model"), pytest.param(True, id="kicad", marks=requires_cli)]
)
def test_a_route_to_a_net_named_by_a_global_label_is_still_repaired(tmp_path, oracle):
    """Guards against over-refusal: R1:1 is on G by a global label, wire_pins_to_net cannot
    name that net, and Q1:1 is on none. The repair is connect_pins to R1:1, and made it puts
    Q1:1 on G with nothing else changed in kicad-cli's netlist."""
    p, old, end, far = _routed(tmp_path, "global")
    wire(p, *far, far[0] - 2.54, far[1])
    label(p, "G", far[0] - 2.54, far[1], rot=180, kind="global_label")
    (issue,) = _new_issues(p)
    assert issue["repair"] == {
        "tool": "connect_pins",
        "arguments": {"ref1": "Q1", "pin1": "1", "ref2": "R1", "pin2": "1", "schematic_path": p},
    }, issue
    if oracle:
        before = nets(p)
        schematic.connect_pins(**issue["repair"]["arguments"])
        verdict = judge(before, nets(p), [{("Q1", "1"), ("R1", "1")}])
        assert verdict.delivered and not verdict.wrong, verdict.problems()
        assert any(
            bare_name(name) == "G" and nodes == {("Q1", "1"), ("R1", "1")}
            for _c, name, _k, nodes in nets(p)
        )


def test_a_route_to_a_net_with_one_local_name_is_repaired_by_that_name(tmp_path):
    """Guards against over-refusal: R1:1 is on N by a local label and Q1:1 on none: the repair
    is wire_pins_to_net with N, which plans clean, and not connect_pins."""
    p, old, end, far = _routed(tmp_path, "local")
    stub(p, *far, -2.54, 0, "N")
    (issue,) = _new_issues(p)
    assert issue["repair"] == {
        "tool": "wire_pins_to_net",
        "arguments": {
            "pins": [{"reference": "Q1", "pin": "1"}],
            "label_text": "N",
            "schematic_path": p,
        },
    }, issue


def test_a_pin_connect_pins_would_take_for_another_is_a_note(tmp_path):
    """connect_pins takes the first pin, in file order, whose name or number is the one given;
    the model takes the exact pad number (PI-14). U1 draws the pin named 1 (number 2) before the
    pin numbered 1, so connect_pins(Q1, 1, U1, 1) would wire the pin numbered 2, not the one the
    route reaches. The report does not name that call."""
    from routing_fixtures import custom_lib, place_custom

    p = fresh(tmp_path, "taken")
    place(p, "Q_NPN_BCE", "Q1", *_ROUTE_Q, rot=90, mirror="x")
    pins = [("2", "1", "passive", 5.08, 0, 180, False), ("1", "2", "passive", -5.08, 0, 0, False)]
    place_custom(p, custom_lib(tmp_path, "SWAP", pins), "SWAP", "U1", 50.8, 96.52)
    wire(
        p, 101.6, 81.28, 45.72, 81.28
    )  # Q1:1's old point to U1's pin numbered 1, at (45.72, 96.52)
    wire(p, 45.72, 81.28, 45.72, 96.52)
    (issue,) = _new_issues(p)
    assert "repair" not in issue and "connect_pins would not find Q1:1 and U1:1" in issue["note"]


# ---------------------------------------------------------------------------------------------
# The repairs of one report are made in its order
# ---------------------------------------------------------------------------------------------


def _stray_route(
    p: str, q: str, q_at: Pt, state: tuple[int, str], r: str, r_at: Pt, r_rot: int = 0
) -> tuple[Pt, Pt, Pt]:
    """Q_NPN_BCE *q* in *state* at *q_at*, R *r* (rotated *r_rot*) at *r_at*, and the route
    0.20.1's connect_pins(q, 1, r, 1) wrote: from the point it gave Q:1, horizontal first, to
    R:1. Returns, in mm, that old point, KiCad's end of Q:1 and R:1's end."""
    place(p, "Q_NPN_BCE", q, *q_at, rot=state[0], mirror=state[1])
    place(p, "R", r, *r_at, rot=r_rot)
    dx, dy = OLD["Q_NPN_BCE"][state][0]
    ex, ey, _out = ORIENTED["Q_NPN_BCE"][state][0]
    fx, fy, _out = ORIENTED["R"][(r_rot, "")][0]
    old = (q_at[0] + dx, q_at[1] + dy)
    end = (q_at[0] + ex, q_at[1] + ey)
    far = (r_at[0] + fx, r_at[1] + fy)
    if old[0] != far[0]:
        wire(p, *old, far[0], old[1])
    wire(p, far[0], old[1], *far)
    return old, end, far


class _Routes(NamedTuple):
    """The path of _two_routes' sheet and its points in mm: for Q1 and Q2, the old point of pin 1,
    KiCad's end of it, and the end of the pin of R1 or R2 its route runs to."""

    path: str
    old1: Pt
    end1: Pt
    far1: Pt
    old2: Pt
    end2: Pt
    far2: Pt


def _two_routes(tmp_path, name: str, labelled: bool = False) -> _Routes:
    """Two routes 0.20.1 wrote, each to a pin on a global label of its own. Q1 (Q_NPN_BCE at 90
    with mirror x) at (101.6, 101.6) to R1:1, on GA; Q2 (at 90 with mirror y) at (20.32, 91.44)
    to R2:1, on GB, R2 rotated 180. Q1's issue comes first. connect_pins(Q1, 1, R1, 1) turns at
    (40.64, 96.52) and runs along y 96.52 from x 101.6, and connect_pins(Q2, 1, R2, 1) turns at
    (60.96, 96.52), on that leg. *labelled* adds the Net-(REF-PIN) label 0.20.1's connect_pins
    put at each old point."""
    p = fresh(tmp_path, name)
    old1, end1, far1 = _stray_route(p, "Q1", (101.6, 101.6), (90, "x"), "R1", (40.64, 120.65))
    old2, end2, far2 = _stray_route(p, "Q2", (20.32, 91.44), (90, "y"), "R2", (60.96, 67.31), 180)
    if labelled:
        label(p, "Net-(Q1-1)", *old1)
        label(p, "Net-(Q2-1)", *old2)
    wire(p, *far1, far1[0] - 2.54, far1[1])
    label(p, "GA", far1[0] - 2.54, far1[1], rot=180, kind="global_label")
    wire(p, *far2, far2[0] + 2.54, far2[1])
    label(p, "GB", far2[0] + 2.54, far2[1], rot=0, kind="global_label")
    return _Routes(p, old1, end1, far1, old2, end2, far2)


_PAIRS = [{("Q1", "1"), ("R1", "1")}, {("Q2", "1"), ("R2", "1")}]


def _make(issue: dict) -> None:
    """Make the repair an issue names, as given."""
    getattr(schematic, issue["repair"]["tool"])(**issue["repair"]["arguments"])


def _call(issue: dict) -> tuple:
    """The call a repair names, without the path: (tool, its other arguments in order)."""
    args = {k: v for k, v in issue["repair"]["arguments"].items() if k != "schematic_path"}
    return issue["repair"]["tool"], tuple(args.values())


@requires_cli
def test_the_repairs_of_a_report_made_in_its_order_join_nothing_else(tmp_path):
    """Two stray routes whose repairs meet. Each call is clear on the sheet alone, but the
    first draws a leg along y 96.52 and the call named for Q2:1 turned at (60.96, 96.52), on
    that leg, where connect_pins writes a junction: made in the report's order, kicad-cli 9.0.8
    read GA and GB as one net and validate_hierarchy then returned ok (measured 2026-10-17).
    The report checks each repair on the sheet as the ones before it leave it, so made in its
    order they deliver both joins and nothing else."""
    p = _two_routes(tmp_path, "order").path
    before = nets(p)
    issues = _new_issues(p)
    assert [i["type"] for i in issues] == ["wire_end_off_pin"] * 2, issues
    assert all("repair" in i for i in issues), issues
    for issue in issues:
        _make(issue)
    verdict = judge(before, nets(p), _PAIRS)
    assert verdict.delivered and not verdict.wrong, verdict.problems()
    assert _new_issues(p) == []


@requires_cli
def test_a_route_the_first_repair_blocks_is_left_for_the_user(tmp_path):
    """The same routes with a Net-(REF-PIN) label at each old point, where 0.20.1's connect_pins
    wrote one when the net had no label. Q2:1's label lies on the vertical leg of the call with
    R2:1 first, so once the first repair is made neither call for Q2:1 is clear. The report
    names the first repair and a note for the second; made, the sheet joins Q1:1 to R1:1 and
    nothing else, where the call the report used to name for Q2:1 joined GA and GB."""
    p = _two_routes(tmp_path, "held", labelled=True).path
    before = nets(p)
    first, second = _new_issues(p)
    assert "repair" in first and "repair" not in second, (first, second)
    _make(first)
    verdict = judge(before, nets(p), _PAIRS[:1])
    assert verdict.delivered and not verdict.wrong, verdict.problems()


def test_the_second_repair_is_the_call_that_is_clear_once_the_first_is_made(tmp_path):
    """The call with Q2:1 first is clear on the sheet as it stands and refused once the first
    repair is made, since its route turns on that repair's leg. The report names the other
    call, with R2:1 first, which turns at (20.32, 71.12) and is clear after the first."""
    r = _two_routes(tmp_path, "second")
    p = r.path
    corner = (r.far2[0], r.old2[1])
    run = [(r.old2, corner), (corner, r.far2)]  # Q2:1's stray wires, which the call runs over
    through = frozenset((S.iu_point(a), S.iu_point(b)) for a, b in run)
    first, second = _new_issues(p)
    assert _call(first) == ("connect_pins", ("Q1", "1", "R1", "1"))
    assert _call(second) == ("connect_pins", ("R2", "1", "Q2", "1"))

    def q2_first() -> C.Refusal | None:
        root = _cst.parse(Path(p).read_bytes()).lists[0]
        model = C.Model(root, p)
        at1, at2 = S.iu_point(r.end2), S.iu_point(r.far2)
        return C.check_connect_pins(model, "Q2", "1", at1, "R2", "1", at2, through)

    assert q2_first() is None
    _make(first)
    refused = q2_first()
    assert refused is not None and refused.codes == ("touch",), refused
    assert "(101.6, 96.52)" in refused.text, refused.text  # the first repair's leg


def test_a_repair_only_the_earlier_ones_refuse_is_a_note_that_says_so(tmp_path):
    """R3's pin 1 sits at (20.32, 71.12), where the call with R2:1 first would turn, so once
    the first repair is made neither call for Q2:1 is clear; alone the call with Q2:1 first is.
    The note says that it is the repairs above that refuse, and to run the report again. Run
    again after the first repair, the note is the plain refusal."""
    r = _two_routes(tmp_path, "later")
    p = r.path
    place(p, "R", "R3", r.end2[0], r.far2[1] + 3.81)
    first, second = _new_issues(p)
    assert "repair" in first and "repair" not in second, (first, second)
    assert second["note"].count("[touch]") == 2, second["note"]
    assert "the repairs above it in this report" in second["note"], second["note"]
    _make(first)
    (again,) = _new_issues(p)
    assert "repair" not in again and again["note"].count("[touch]") == 2, again
    assert "the repairs above it in this report" not in again["note"], again["note"]


def test_the_repairs_of_a_sub_sheet_are_checked_in_order_too(tmp_path):
    """The routes of _two_routes on a sub-sheet of a project: the issues are on its own file, the
    second repair is the call with R2:1 first as on a root, and both repairs, which name the
    sub-sheet's file, clear the report from the root."""
    d = tmp_path / "proj"
    project.create_project(directory=str(d), name="proj")
    child = d / "sub.kicad_sch"
    shutil.copyfile(_two_routes(tmp_path, "sub").path, child)
    project.add_hierarchical_sheet(str(d / "proj.kicad_sch"), "Sub", str(child), [])
    first, second = _new_issues(d / "proj.kicad_sch")
    assert {first["file"], second["file"]} == {str(child)}
    assert _call(first) == ("connect_pins", ("Q1", "1", "R1", "1"))
    assert _call(second) == ("connect_pins", ("R2", "1", "Q2", "1"))
    assert first["repair"]["arguments"]["schematic_path"] == str(child)
    for issue in (first, second):
        _make(issue)
    assert _new_issues(d / "proj.kicad_sch") == []


def _groups_apart(netlist, groups) -> list:
    """The nets of a netlist that hold nodes of two of the groups."""
    return [name for _c, name, _k, nodes, *_ in netlist if sum(bool(nodes & g) for g in groups) > 1]


def _stub_on_a_corner(tmp_path, name: str) -> tuple[str, set, set]:
    """Q3 (Q_NPN_BCE at 90 with mirror x) with the stub 0.20.1's wire_pins_to_net(Q3, 1, N) left
    from the old point toward the origin, labelled N, and Q2's route to R2:1 on GB as in
    _two_routes. Q3 comes first and puts the end of its pin 1, outward up, 1.27 mm below the
    turn of connect_pins(Q2, 1, R2, 1) at (60.96, 96.52): the repair for Q3:1 is wire_pins_to_net
    with N, whose new stub runs up from that end through the turn. Returns the path and the
    joins asked for."""
    ex, ey, _out = ORIENTED["Q_NPN_BCE"][(90, "x")][0]
    dx, dy = OLD["Q_NPN_BCE"][(90, "x")][0]
    origin3 = (60.96 - ex, 96.52 + 1.27 - ey)
    old3 = (origin3[0] + dx, origin3[1] + dy)
    p = fresh(tmp_path, name)
    place(p, "Q_NPN_BCE", "Q3", *origin3, rot=90, mirror="x")
    stub(p, *old3, 0, -2.54, "N")
    _old2, _end2, far2 = _stray_route(p, "Q2", (20.32, 91.44), (90, "y"), "R2", (60.96, 67.31), 180)
    wire(p, *far2, far2[0] + 2.54, far2[1])
    label(p, "GB", far2[0] + 2.54, far2[1], rot=0, kind="global_label")
    return p, {("Q3", "1")}, {("Q2", "1"), ("R2", "1")}


@requires_cli
def test_a_stub_the_first_repair_writes_is_seen_by_the_next(tmp_path):
    """The first repair is wire_pins_to_net for Q3:1, N: its stub runs up from the pin's end
    (60.96, 97.79) to (60.96, 95.25). The call named for Q2:1 turned at (60.96, 96.52), inside
    that stub, where connect_pins writes a junction and splits the stub: made in the report's
    order, kicad-cli 9.0.8 read Q3:1 on the net of Q2:1 and R2:1 (measured 2026-10-17). The
    report names the call that does not turn there, and made in order the joins stay apart."""
    p, only3, pair2 = _stub_on_a_corner(tmp_path, "mixed")
    before = nets(p)
    first, second = _new_issues(p)
    assert first["repair"]["tool"] == "wire_pins_to_net", first
    assert _call(second) == ("connect_pins", ("R2", "1", "Q2", "1")), second
    for issue in (first, second):
        _make(issue)
    after = nets(p)
    verdict = judge(before, after, [only3, pair2])
    assert verdict.delivered and not verdict.wrong, verdict.problems()
    assert _groups_apart(after, [only3, pair2]) == []
    assert _new_issues(p) == []


def _one_net_two_routes(tmp_path, name: str, first_named: bool) -> str:
    """Q1 and Q2 (Q_NPN_BCE at 90 with mirror x) at (50.8, 101.6) and (101.6, 101.6), their pins 1
    wired together at the ends KiCad draws, so one net, X. Each has the route 0.20.1 wrote from its
    old point: Q1:1's to R1:1 at (38.1, 127), Q2:1's to R2:1 at (114.3, 127). R2:1 is on the
    global label ZZ and, with *first_named*, R1:1 on YY. Alone, each repair joins X to a net
    with one name at most; both made, X joins the two."""
    p = fresh(tmp_path, name)
    _old1, end1, far1 = _stray_route(p, "Q1", (50.8, 101.6), (90, "x"), "R1", (38.1, 127))
    _old2, end2, far2 = _stray_route(p, "Q2", (101.6, 101.6), (90, "x"), "R2", (114.3, 127))
    wire(p, *end1, *end2)
    if first_named:
        wire(p, *far1, far1[0] - 2.54, far1[1])
        label(p, "YY", far1[0] - 2.54, far1[1], rot=180, kind="global_label")
    wire(p, *far2, far2[0] + 2.54, far2[1])
    label(p, "ZZ", far2[0] + 2.54, far2[1], rot=0, kind="global_label")
    return p


@requires_cli
def test_a_name_the_first_repair_puts_on_the_net_counts_for_the_next(tmp_path):
    """Q1:1 and Q2:1 are one net, and the routes 0.20.1 wrote from them lead to R1:1 on YY and
    R2:1 on ZZ. Each repair joins that net to one name, and made together they join YY and ZZ:
    kicad-cli 9.0.8 read one net holding both names (measured 2026-10-17). The report names the
    first repair, and a note for the second that gives the [names] refusal it meets once the
    first is made."""
    p = _one_net_two_routes(tmp_path, "names", first_named=True)
    before = nets(p)
    first, second = _new_issues(p)
    assert _call(first) == ("connect_pins", ("Q1", "1", "R1", "1")), first
    assert "repair" not in second and second["note"].count("[names]") == 1, second
    assert "the repairs above it in this report" in second["note"], second["note"]
    _make(first)
    verdict = judge(before, nets(p), [{("Q1", "1"), ("R1", "1"), ("Q2", "1")}])
    assert not verdict.wrong, verdict.problems()


def test_the_label_the_first_repair_writes_counts_as_a_name_for_the_next(tmp_path):
    """The same net with no name on R1:1: connect_pins writes Net-(Q1-1), as 0.20.1's did, and
    the report counts it a name, as it does when the second call is checked on the real sheet,
    so the second repair, which would join X to ZZ, is refused ([names]) once the first is made.
    kicad-cli reads no harm from that label alone (measured 2026-10-17): what is judged is that
    the report and the real sheet agree."""
    p = _one_net_two_routes(tmp_path, "label", first_named=False)
    first, second = _new_issues(p)
    assert "repair" in first and "repair" not in second, (first, second)
    assert second["note"].count("[names]") == 1 and "Net-(Q1-1)" in second["note"], second
    _make(first)
    (again,) = _new_issues(p)
    assert "repair" not in again and "Net-(Q1-1)" in again["note"], again
    assert "the repairs above it in this report" not in again["note"], again["note"]


def _route_and_stub_on_one_net(tmp_path, name: str) -> str:
    """Q1 and Q2 (Q_NPN_BCE at 90 with mirror x) at (50.8, 101.6) and (101.6, 101.6), their pins 1
    wired together at the ends KiCad draws, so one net, X. Q1:1 has the route 0.20.1 wrote to
    R1:1 at (38.1, 127), on the global label M; Q2:1 has the stub its wire_pins_to_net(Q2, 1, N)
    left, up from the old point (101.6, 106.68) and labelled N. Alone, the repair for Q2:1 puts X
    on N; once the first repair has put X on M, it cannot."""
    p = fresh(tmp_path, name)
    _old1, end1, far1 = _stray_route(p, "Q1", (50.8, 101.6), (90, "x"), "R1", (38.1, 127))
    place(p, "Q_NPN_BCE", "Q2", 101.6, 101.6, rot=90, mirror="x")
    dx, dy = OLD["Q_NPN_BCE"][(90, "x")][0]
    ex, ey, _out = ORIENTED["Q_NPN_BCE"][(90, "x")][0]
    wire(p, *end1, 101.6 + ex, 101.6 + ey)
    wire(p, *far1, far1[0] - 2.54, far1[1])
    label(p, "M", far1[0] - 2.54, far1[1], rot=180, kind="global_label")
    stub(p, 101.6 + dx, 101.6 + dy, 0, -2.54, "N")
    return p


def test_a_wire_pins_to_net_the_first_repair_makes_impossible_is_a_note(tmp_path):
    """The first repair puts X on M, so the call that would put Q2:1 on N (its stub's label) is
    one the tool itself refuses ([names]) once the first is made, and writes nothing. The report
    does not name it, and its note says so; run again after the first repair, the note is the
    plain refusal."""
    from mcp.server.mcpserver.exceptions import ToolError

    p = _route_and_stub_on_one_net(tmp_path, "later_net")
    first, second = _new_issues(p)
    assert _call(first) == ("connect_pins", ("Q1", "1", "R1", "1")), first
    assert "repair" not in second, second
    assert second["note"].count("[names]") == 1, second["note"]
    assert "the repairs above it in this report" in second["note"], second["note"]
    _make(first)
    before = Path(p).read_bytes()
    with pytest.raises(ToolError, match=r"\[names\]"):
        schematic.wire_pins_to_net([{"reference": "Q2", "pin": "1"}], "N", schematic_path=p)
    assert Path(p).read_bytes() == before
    (again,) = _new_issues(p)
    assert "repair" not in again and again["note"].count("[names]") == 1, again
    assert "the repairs above it in this report" not in again["note"], again["note"]


def _repairs_of(path) -> list[dict]:
    """The wire-end issues of the report on *path* that carry a repair."""
    return [i for i in _new_issues(path) if i["type"] == "wire_end_off_pin" and "repair" in i]


def _report_matches_real_repairs(path) -> int:
    """Make the repairs of the report on *path* one at a time with the report run again between,
    each as the first report named it. The issue of the pin the next repair is for, in the report
    run again, is then the very one the first report gave, or is gone because the repairs made
    have joined its wire to its pin (a repair the ones above it make unnecessary is still named,
    and harmless); and at the end none is left. A tool that refuses one of them raises. Returns
    how many the first report named."""
    named = _repairs_of(path)
    for issue in named:
        now = {
            (i["reference"], i["pin"]): i
            for i in _new_issues(path)
            if i["type"] == "wire_end_off_pin"
        }
        again = now.get((issue["reference"], issue["pin"]))
        if again is None:
            continue
        assert again == issue, (again, issue)
        _make(issue)
    assert _repairs_of(path) == []
    return len(named)


def _fixture_sheet(tmp_path) -> str:
    return _copy_fixture(tmp_path)


def _first_layout(tmp_path) -> str:
    return _two_routes(tmp_path, "rerun").path


def _labelled_layout(tmp_path) -> str:
    return _two_routes(tmp_path, "rerun", True).path


def _blocked_layout(tmp_path) -> str:
    r = _two_routes(tmp_path, "rerun")
    place(r.path, "R", "R3", r.end2[0], r.far2[1] + 3.81)
    return r.path


def _mixed_layout(tmp_path) -> str:
    return _stub_on_a_corner(tmp_path, "rerun")[0]


def _named_layout(tmp_path) -> str:
    return _one_net_two_routes(tmp_path, "rerun", first_named=True)


def _label_layout(tmp_path) -> str:
    return _one_net_two_routes(tmp_path, "rerun", first_named=False)


def _stub_layout(tmp_path) -> str:
    return _route_and_stub_on_one_net(tmp_path, "rerun")


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_fixture_sheet, id="the_sheet_0_20_1_wrote"),
        pytest.param(_first_layout, id="routes_that_meet"),
        pytest.param(_labelled_layout, id="routes_that_meet_with_labels"),
        pytest.param(_blocked_layout, id="one_repair_refused_after_the_first"),
        pytest.param(_mixed_layout, id="a_stub_then_a_route"),
        pytest.param(_named_layout, id="a_name_joined_by_the_first"),
        pytest.param(_label_layout, id="a_label_written_by_the_first"),
        pytest.param(_stub_layout, id="a_stub_call_refused_after_the_first"),
    ],
)
def test_running_the_report_again_after_each_repair_names_the_next_one_it_did(tmp_path, build):
    """The report checks each repair on a copy of the sheet with the ones before it made, in
    memory. Made for real instead, one at a time with the report run again between, the first
    repair left is the very one the first report named next, so the copy is the sheet the
    tools leave."""
    assert _report_matches_real_repairs(build(tmp_path)), "premise: the layout has a repair"


def _drawn(data: bytes) -> tuple:
    """A sheet's wires and buses, junctions and Net-(...) labels, as KiCad's lexer reads the
    first two and the bytes show the third."""
    wires = sorted((line.kind, line.start, line.end) for line in S.lines(data))
    return wires, sorted(S.junctions(data)), data.count(b'(label "Net-(')


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_fixture_sheet, id="the_sheet_0_20_1_wrote"),
        pytest.param(_first_layout, id="routes_that_meet"),
        pytest.param(_named_layout, id="a_name_joined_by_the_first"),
    ],
)
def test_the_copy_the_report_checks_on_is_the_sheet_the_tools_leave(tmp_path, build):
    """The connect_pins repairs of a report, made on the copy it holds in memory, leave the wires,
    junctions and Net-(...) labels (the splits a junction makes included) that the same calls
    leave on the file. The wires and junctions are read with KiCad's lexer, not the CST."""
    p = build(tmp_path)
    calls = [i for i in _new_issues(p) if "repair" in i and i["repair"]["tool"] == "connect_pins"]
    assert calls, "premise: the layout has a connect_pins repair"
    repairs = schematic._Repairs(_cst.parse(Path(p).read_bytes()).lists[0], p)
    for issue in calls:
        repairs.make(issue["repair"])
    in_memory = _drawn(_cst.serialize(repairs.root))
    for issue in calls:
        _make(issue)
    assert in_memory == _drawn(Path(p).read_bytes())
    assert in_memory[1], "premise: the calls wrote a junction"


# ---------------------------------------------------------------------------------------------
# Generated layouts, where the repairs of a report meet
# ---------------------------------------------------------------------------------------------


class _Seeded:
    """random.Random(seed) cut down to random(), which Python promises to keep, so that a seed
    names the same layout on every interpreter of the CI matrix (choice and randrange are not
    promised)."""

    def __init__(self, seed: int):
        self._random = random.Random(seed).random

    def below(self, n: int) -> int:
        return int(self._random() * n)

    def pick(self, options):
        return options[self.below(len(options))]

    def chance(self, p: float) -> bool:
        return self._random() < p


def _dense_layout(path: str, seed: int) -> None:
    """The layout *seed* names: two to four Q_NPN_BCE at 90 or 270 with a mirror, on a coarse
    grid so that routes meet, each with the stray 0.20.1 left at the old point of one of its
    pins. Either its connect_pins route to pin 1 of an R (rotated any way), whose net a global
    label, a local label or nothing names, or its wire_pins_to_net stub with a local label. Some
    pairs of the transistors' pins are wired together at the ends KiCad draws, so nets are
    shared. Changing this changes what a seed names."""
    rnd = _Seeded(seed)
    parts, taken = [], []
    for i in range(rnd.pick([2, 3, 3, 4])):
        for _ in range(60):  # a place clear of the parts before it, else the last try
            q_at = (round(25.4 * (1 + rnd.below(5)), 4), round(50.8 + 25.4 * rnd.below(3), 4))
            r_at = (round(25.4 + 12.7 * rnd.below(9), 4), round(38.1 + 12.7 * rnd.below(8), 4))
            if all(abs(q_at[0] - t[0]) > 10 or abs(q_at[1] - t[1]) > 15 for t in taken) and all(
                abs(r_at[0] - t[0]) > 7 or abs(r_at[1] - t[1]) > 10 for t in taken
            ):
                break
        taken += [q_at, r_at]
        state, pin, r_rot = rnd.pick(_MIRRORED), rnd.below(3), rnd.pick([0, 90, 180, 270])
        stray = rnd.pick(["route", "route", "route", "stub"])
        net = rnd.pick(["global", "global", "local", "none"])
        parts.append((i + 1, q_at, r_at, state, pin, r_rot, stray, net))
    for n, q_at, r_at, state, _pin, r_rot, _stray, _net in parts:
        place(path, "Q_NPN_BCE", f"Q{n}", *q_at, rot=state[0], mirror=state[1])
        place(path, "R", f"R{n}", *r_at, rot=r_rot)
    ends = []
    for n, q_at, r_at, state, pin, r_rot, stray, net in parts:
        ex, ey, _out = ORIENTED["Q_NPN_BCE"][state][pin]
        ends.append((round(q_at[0] + ex, 4), round(q_at[1] + ey, 4)))
        dx, dy = OLD["Q_NPN_BCE"][state][pin]
        old = (round(q_at[0] + dx, 4), round(q_at[1] + dy, 4))
        fx, fy, (ox, oy) = ORIENTED["R"][(r_rot, "")][0]
        far = (round(r_at[0] + fx, 4), round(r_at[1] + fy, 4))
        if stray == "stub":  # toward the symbol's origin, one axis, as 0.20.1 drew it
            sx = 2.54 * ((q_at[0] > old[0]) - (q_at[0] < old[0]))
            sy = 0 if sx else 2.54 * ((q_at[1] > old[1]) - (q_at[1] < old[1]))
            stub(path, *old, sx, sy, f"S{n}")
            continue
        if old[0] != far[0] and old[1] != far[1]:
            wire(path, *old, far[0], old[1])
            wire(path, far[0], old[1], *far)
        else:
            wire(path, *old, *far)
        if net != "none":
            wire(path, *far, far[0] + 2.54 * ox, far[1] + 2.54 * oy)
            kind = "global_label" if net == "global" else "label"
            label(path, f"{net[0].upper()}Q{n}", far[0] + 2.54 * ox, far[1] + 2.54 * oy, kind=kind)
    for a in range(len(ends)):
        for b in range(a + 1, len(ends)):
            if rnd.chance(0.3) and (ends[a][0] == ends[b][0] or ends[a][1] == ends[b][1]):
                wire(path, *ends[a], *ends[b])


def _joins_of(issues: list[dict]) -> list[set]:
    """The pins each repair asks to join: the two of a connect_pins, the one of a stub's."""
    out = []
    for issue in issues:
        args = issue["repair"]["arguments"]
        if issue["repair"]["tool"] == "connect_pins":
            out.append({(args["ref1"], args["pin1"]), (args["ref2"], args["pin2"])})
        else:
            out.append({(pin["reference"], pin["pin"]) for pin in args["pins"]})
    return out


def _named_nets_joined(path) -> list:
    """Make the repairs of the report on *path*, in its order, and return what kicad-cli reads as
    two nets that carried a name each joined into one."""
    before = nets(path)
    issues = _repairs_of(path)
    for issue in issues:
        _make(issue)
    return judge(before, nets(path), _joins_of(issues)).named_merge


#: Seeds whose layouts the report named wrongly before the repairs were checked in order, as the
#: commit before measured them (2026-10-17): 126, 241, 52 and 75 joined two named nets when made in
#: the report's order (52 and 75 also needed the names rule for two nets one possible component
#: holds, test_connectivity.py), 19 and 265 named a call the tool then refused ([names]), and 111
#: named a repair that another made unnecessary.
_PINNED = (126, 241, 52, 75, 19, 265, 111)


@pytest.mark.parametrize("seed", _PINNED)
def test_pinned_layouts_report_the_repairs_the_tools_then_leave(tmp_path, seed):
    """Each pinned layout holds two stray wire ends or more, and the report names the same calls
    the report run again between real repairs does, none of which a tool refuses."""
    p = fresh(tmp_path, "pinned")
    _dense_layout(p, seed)
    assert len([i for i in _new_issues(p) if i["type"] == "wire_end_off_pin"]) >= 2
    _report_matches_real_repairs(p)


@requires_cli
@pytest.mark.parametrize("seed", [126, 241, 52, 75])
def test_pinned_layouts_repaired_in_order_join_no_two_named_nets(tmp_path, seed):
    """The four pinned layouts that joined two named nets, judged by kicad-cli: the repairs made
    in the report's order join no two named nets now."""
    p = fresh(tmp_path, "pinned")
    _dense_layout(p, seed)
    assert _named_nets_joined(p) == []


@settings(max_examples=sexpr_gen.budget(25))
@given(seed=st.integers(0, 10**6))
def test_generated_layouts_report_the_repairs_the_tools_then_leave(seed):
    """Any layout of _dense_layout: the report names the same calls the report run again between
    real repairs does, and no tool refuses one."""
    with tempfile.TemporaryDirectory() as work:
        p = fresh(Path(work), "gen")
        _dense_layout(p, seed)
        _report_matches_real_repairs(p)


@pytest.mark.nightly
@requires_cli
@settings(max_examples=sexpr_gen.budget(25))
@given(seed=st.integers(0, 10**6))
def test_nightly_generated_layouts_repaired_in_order_join_no_two_named_nets(seed):
    """The generated layouts judged by kicad-cli (the pinned ones above are its sibling on every
    push)."""
    with tempfile.TemporaryDirectory() as work:
        p = fresh(Path(work), "gen")
        _dense_layout(p, seed)
        assert _named_nets_joined(p) == []


def test_a_wire_end_on_the_pins_net_is_not_reported(tmp_path):
    """A stub at Q1:1's old point labelled N is reported until Q1:1 itself is on N, here
    through the stub wire_pins_to_net writes at its end: the stray stub then joins nothing
    the pin lacks, and is not."""
    p, at, _end = _q_sheet(tmp_path, "joined")
    wire(p, *at, at[0], at[1] - 2.54)
    label(p, "N", at[0], at[1] - 2.54)
    assert [i["type"] for i in _new_issues(p)] == ["wire_end_off_pin"]
    schematic.wire_pins_to_net([{"reference": "Q1", "pin": "1"}], "N", schematic_path=p)
    assert _new_issues(p) == []


def test_edits_by_the_fixed_tools_are_not_reported(tmp_path):
    """R, D and Q_NPN_BCE in every state, each pin flagged by no_connect_pin, stubbed by
    wire_pins_to_net or routed by connect_pins as this package now writes them, at the pin
    KiCad draws (test_routing_safety judges that with kicad-cli): nothing is reported."""
    p = fresh(tmp_path, "fixed")
    for row, symbol in enumerate(_SYMBOLS):
        for i, state in enumerate(G.STATES):
            ref, origin = f"{_PREFIX[symbol]}{i + 1}", (25.4 + 22.86 * i, 50.8 + 50.8 * row)
            place(p, symbol, ref, *origin, rot=state[0], mirror=state[1])
            schematic.no_connect_pin(ref, "1", schematic_path=p)
            schematic.wire_pins_to_net(
                [{"reference": ref, "pin": "2"}], f"N{ref}", schematic_path=p
            )
            if symbol == "Q_NPN_BCE":
                anchor = f"RX{i + 1}"
                place(p, "R", anchor, origin[0] - 7.62, origin[1] + 25.4)
                schematic.connect_pins(ref, "3", anchor, "1", schematic_path=p)
    assert _new_issues(p) == []


# ---------------------------------------------------------------------------------------------
# KiCad's own sheets
# ---------------------------------------------------------------------------------------------


def _demo_params(cap):
    found = K.shipped(subdirs=("demos",), suffixes=(".kicad_sch",), names=(), cap=cap)
    return [pytest.param(path, id=name) for name, path in found] or [
        pytest.param(None, id="no-install", marks=pytest.mark.skip(reason=K.NO_INSTALL))
    ]


def _nightly_demo_params():
    if _nightly_skip_reason(os.environ.get("HYPOTHESIS_PROFILE")) is not None:
        return [pytest.param(None, id="nightly-only")]
    return _demo_params(None)


def test_kicads_demos_hold_parts_the_check_reads():
    """The premise of the two tests below, so that they cannot pass for want of a case: the
    demo sheets they read on every push place symbols at 90 or 270 with a mirror, as the
    lexer port and the transform port read them."""
    if not K.share_dirs():
        pytest.skip(K.NO_INSTALL)
    import test_ladder_r2_pins as PINS

    mirrored = {T.parse_symbol_transform([("at", str(r)), ("mirror", m)]) for r, m in _MIRRORED}
    count = 0
    for _name, path in K.shipped(
        subdirs=("demos",), suffixes=(".kicad_sch",), names=(), cap=K.PER_PUSH_CAP
    ):
        try:
            root = K.lexed(path)[0]
        except L.LexError:
            continue
        for symbol in PINS.placed_symbols(root):
            try:
                count += PINS.symbol_placement(symbol)[1] in mirrored
            except LookupError:
                continue
    assert count > 0


@pytest.mark.parametrize("path", _demo_params(K.PER_PUSH_CAP))
def test_kicads_demo_sheets_report_nothing(path):
    """KiCad's demos (files up to kicad_corpus.PER_PUSH_CAP), each run as a root, report no
    flag and no wire end: KiCad saved them. Over all 93 demo sheets of 9.0.8, 160 pins sit on
    symbols at 90 or 270 with a mirror, 28 of them with an old point no pin occupies
    (measured 2026-10-16)."""
    assert _new_issues(path) == []


@pytest.mark.nightly
@pytest.mark.parametrize("path", _nightly_demo_params())
def test_nightly_kicads_demo_sheets_report_nothing(path):
    """The same over every demo schematic the install holds."""
    assert _new_issues(path) == []
