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
of each pin after its repair; and the demo sheets KiCad ships.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import NamedTuple

import geom_gen as G
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
from routing_fixtures import ORIENTED, fresh, junction, label, no_connect, place, splice, wire

from mcp_server_kicad import project, schematic

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
    removes a flag by its position. A wire end names its wire, one of whose ends is the point.
    A stub's net carries one local label, so its repair is the wire_pins_to_net call that puts
    the pin on that name, the call that wrote the stub, made again. A route's net carries only
    the Net-(REF-PIN) label connect_pins wrote, a name wire_pins_to_net refuses, so it has a
    note and no repair, and the note names the pin its wires run to: the anchor of the call."""
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
            assert issue["repair"] == want, issue
        else:
            assert "repair" not in issue, issue
            assert f"{call.anchor} pin 1" in issue["note"], issue


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


def _act_on_report(p, issues) -> None:
    """What the report asks for on the 0.20.1 sheet: the repair an issue names; for a flag, the
    user deleting it in KiCad's editor and calling the no_connect_pin its note names; for a
    route, whose note names the pin its wires run to, the user wiring the pin to it as the
    0.20.1 call meant (connect_pins, as this package writes it now)."""
    by_key = {_expected(c): c for c in _STRAYED}
    for issue in issues:
        if "repair" in issue:
            getattr(schematic, issue["repair"]["tool"])(**issue["repair"]["arguments"])
        elif issue["type"] == "no_connect_off_pin":
            _delete_flag(p, S.iu_point((issue["x"], issue["y"])))
            schematic.no_connect_pin(issue["reference"], issue["pin"], schematic_path=p)
        else:
            call = by_key[_key(issue)]
            schematic.connect_pins(call.ref, call.pin, call.anchor, "1", schematic_path=p)


def test_acting_on_the_report_clears_it(tmp_path):
    """Made as asked (_act_on_report), each issue of the 0.20.1 sheet clears: a stub's once the
    call joined its pin to the stub's net, a route's once the pin is wired to the pin its wires
    run to, a flag's once the user deleted it and called the no_connect_pin its note names. The
    re-run reports nothing of either kind."""
    p = _copy_fixture(tmp_path)
    issues = _new_issues(p)
    assert len(issues) == len(_STRAYED)
    _act_on_report(p, issues)
    assert _new_issues(p) == []


@requires_cli
def test_after_acting_on_the_report_kicad_reads_each_pin_as_0_20_1_meant_it(tmp_path):
    """kicad-cli's netlist after acting on the report of the 0.20.1 sheet: each flagged
    transistor pin is a no-connect pin (+no_connect), each stubbed one is on its stub's net,
    each routed one shares its anchor's net, and against the netlist before, nothing else
    merged, split or was renamed."""
    p = _copy_fixture(tmp_path)
    before = nets(p)
    issues = _new_issues(p)
    assert len(issues) == len(_STRAYED)
    _act_on_report(p, issues)
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


def _named_route(tmp_path, anchor: str) -> tuple[str, _Call]:
    """The 0.20.1 sheet with Q3:3 on M and the anchor of its route, RA3:1, on a name: N, a
    local label, or G, a global one. connect_pins wrote its Net-(Q3-3) label only because the
    net had no name, so a route to a named net holds none, and the label goes first."""
    p = _copy_fixture(tmp_path)
    call = next(c for c in _STRAYED if c.tool == "connect_pins")
    schematic.remove_label(
        f"Net-({call.ref}-{call.pin})", call.old[0] / 1e4, call.old[1] / 1e4, schematic_path=p
    )
    schematic.wire_pins_to_net([{"reference": call.ref, "pin": call.pin}], "M", schematic_path=p)
    if anchor == "N":
        pins = [{"reference": call.anchor, "pin": "1"}]
        schematic.wire_pins_to_net(pins, anchor, schematic_path=p)
    else:
        x, y, _out = ORIENTED["R"][(0, "")][0]
        schematic.add_global_label(
            anchor,
            round(call.origin[0] - 7.62 + x, 4),
            round(88.9 + y, 4),
            rotation=0,
            schematic_path=p,
        )
    return p, call


@requires_cli
@pytest.mark.parametrize("anchor", ["N", "G"])
def test_no_wire_end_repair_joins_two_named_nets(tmp_path, anchor):
    """Q3:3 is on M and the 0.20.1 route from its old point ends at RA3:1, on N (a local
    label) or on G (a global one). Whatever the report offers for that wire, kicad-cli's
    netlist after it joins no two named nets: putting a pin on a net never means joining M
    with N or with G. The report offers no repair here, and its note says why: for N the
    refusal of the wire_pins_to_net call that would put the pin on N, for G, which that
    call cannot name, the pin the wires run to."""
    p, call = _named_route(tmp_path, anchor)
    before = nets(p)
    (issue,) = [
        i for i in _new_issues(p) if i["type"] == "wire_end_off_pin" and i["reference"] == call.ref
    ]
    if "repair" in issue:
        getattr(schematic, issue["repair"]["tool"])(**issue["repair"]["arguments"])
    verdict = judge(before, nets(p), [{(call.ref, call.pin), (call.anchor, "1")}])
    assert verdict.named_merge == [], (issue, verdict.problems())
    assert "repair" not in issue, issue
    if anchor == "N":
        assert "[names]" in issue["note"], issue
    else:
        assert f"{call.anchor} pin 1" in issue["note"], issue


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
# Items written with no (at)
# ---------------------------------------------------------------------------------------------

_EFFECTS = "(effects (font (size 1.27 1.27)))"
#: Items KiCad's parser accepts without an (at), as a file would hold them.
_NO_AT = {
    "junction": '(junction (diameter 0) (color 0 0 0 0) (uuid "x"))',
    "no_connect": '(no_connect (uuid "x"))',
    "label": f'(label "X" {_EFFECTS} (uuid "x"))',
    "global_label": f'(global_label "G" (shape input) {_EFFECTS} (uuid "x"))',
    "hierarchical_label": f'(hierarchical_label "H" (shape input) {_EFFECTS} (uuid "x"))',
    "bus_entry": '(bus_entry (size 2.54 2.54) (stroke (width 0) (type default)) (uuid "x"))',
}


@pytest.mark.parametrize("kind", _NO_AT)
def test_an_item_with_no_at_does_not_stop_the_check(tmp_path, kind):
    """R1 at 90 with a mirror and one item written with no (at): the check answers and finds
    nothing, as it does without the item. The connectivity model once raised AttributeError
    on it. The autouse oracle loads each sheet in kicad-cli."""
    p = fresh(tmp_path, "noat")
    place(p, "R", "R1", 50.8, 50.8, rot=90, mirror="x")
    splice(p, _NO_AT[kind])
    result = project.validate_hierarchy(schematic_path=p)
    assert (result.status, result.issues) == ("ok", [])


def test_the_cut_scan_survives_an_item_with_no_at(tmp_path):
    """A junction strictly inside a wire makes the cut scan build the model, which once
    raised on a label with no (at) anywhere on the sheet: the junction is reported, as it is
    without the label."""
    p = fresh(tmp_path, "cut")
    place(p, "R", "R1", 50.8, 50.8)
    wire(p, 25.4, 76.2, 76.2, 76.2)
    junction(p, 50.8, 76.2)
    splice(p, _NO_AT["label"])
    result = project.validate_hierarchy(schematic_path=p)
    assert [i["type"] for i in result.issues] == ["junction_inside_wire"]


@requires_cli
@pytest.mark.parametrize("kind", ["label", "global_label", "hierarchical_label", "no_connect"])
def test_kicad_reads_a_label_or_flag_with_no_at_at_the_origin(tmp_path, kind):
    """kicad-cli's netlist of R1 with pin 1's end at the origin: a label written with no (at)
    names pin 1's net, and a no-connect written with no (at) flags pin 1."""
    p = fresh(tmp_path, "origin")
    place(p, "R", "R1", 0, 3.81)
    splice(p, _NO_AT[kind])
    nodes = netlist_nodes(p)
    if kind == "no_connect":
        assert nodes[next(n for n in nodes if ("R1", "1") in nodes[n])][("R1", "1")].endswith(
            "+no_connect"
        )
    else:
        name = {"label": "X", "global_label": "G", "hierarchical_label": "H"}[kind]
        assert ("R1", "1") in nodes[name], nodes


@requires_cli
def test_kicad_reads_a_junction_with_no_at_at_the_origin(tmp_path):
    """A wire from (-5.08, 0) to (5.08, 0) labelled LH at its left end, R3:1 on its right
    end, and a wire from the origin down to R4:1, so that R4:1 reaches LH only through a
    junction at the origin. kicad-cli's netlist joins them with a junction written with no
    (at), as with one at (0 0), and not without one."""

    def tee(name, junction_text):
        p = fresh(tmp_path, name)
        place(p, "R", "R3", 8.89, 0, rot=90)
        place(p, "R", "R4", 0, 8.89)
        wire(p, -5.08, 0, 5.08, 0)
        wire(p, 0, 0, 0, 5.08)
        label(p, "LH", -5.08, 0)
        if junction_text:
            splice(p, junction_text)
        return netlist_nodes(p)["LH"]

    plain = '(junction {} (diameter 0) (color 0 0 0 0) (uuid "x"))'
    assert ("R4", "1") not in tee("none", None)
    assert ("R4", "1") in tee("origin", plain.format("(at 0 0)"))
    assert ("R4", "1") in tee("noat", plain.format(""))


def _erc_points(path) -> set[tuple[int, int]]:
    """Where kicad-cli's ERC puts its violations, in internal units (its JSON gives them in
    mm divided by 100)."""
    return {
        (round(item["pos"]["x"] * 1e6), round(item["pos"]["y"] * 1e6))
        for sheet in run_erc(path)["sheets"]
        for violation in sheet["violations"]
        for item in violation["items"]
    }


@requires_cli
def test_kicad_reads_a_bus_entry_with_no_at_at_the_origin(tmp_path):
    """kicad-cli's ERC marks a lone bus entry where the entry is: one written with no (at)
    is marked where one at (0 0) is, and not where one at (12.7 12.7) is."""

    def marked(name, at):
        p = fresh(tmp_path, name)
        splice(p, _NO_AT["bus_entry"].replace("(size", f"{at}(size", 1))
        return _erc_points(p)

    origin = marked("origin", "(at 0 0) ")
    assert origin and origin == marked("noat", "")
    assert origin != marked("elsewhere", "(at 12.7 12.7) ")


def test_the_check_reads_a_flag_with_no_at_at_the_origin(tmp_path):
    """Q1 at (5.08, 2.54), 90 with a mirror x: the point 0.20.1 gave its pin 3 is the origin
    (OLD), where KiCad draws no pin. A no-connect written with no (at) is there, so the check
    reports it, at (0, 0)."""
    p = fresh(tmp_path, "origin_flag")
    place(p, "Q_NPN_BCE", "Q1", 5.08, 2.54, rot=90, mirror="x")
    splice(p, _NO_AT["no_connect"])
    (issue,) = _new_issues(p)
    x, y, _out = ORIENTED["Q_NPN_BCE"][(90, "x")][2]
    assert _key(issue) == ("no_connect_off_pin", "Q1", "3", (0, 0), _at((5.08, 2.54), (x, y)))


@pytest.mark.parametrize(
    "kind", ["junction", "label", "global_label", "hierarchical_label", "bus_entry"]
)
def test_a_wire_end_at_the_origin_meets_an_item_with_no_at(tmp_path, kind):
    """A stub from Q1:3's old point, the origin, is reported; once an item written with no
    (at) is there the wire end meets it, as it meets an item written at (0 0), and is not."""
    p = fresh(tmp_path, "origin_wire")
    place(p, "Q_NPN_BCE", "Q1", 5.08, 2.54, rot=90, mirror="x")
    wire(p, 0, 0, 2.54, 0)
    label(p, "C", 2.54, 0)
    assert [i["type"] for i in _new_issues(p)] == ["wire_end_off_pin"]
    splice(p, _NO_AT[kind])
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
    lexer port and the transform port read them. Skipped where the install ships no demo
    sheets."""
    demos = K.shipped(subdirs=("demos",), suffixes=(".kicad_sch",), names=(), cap=K.PER_PUSH_CAP)
    if not demos:
        pytest.skip(K.NO_INSTALL)
    import test_ladder_r2_pins as PINS

    mirrored = {T.parse_symbol_transform([("at", str(r)), ("mirror", m)]) for r, m in _MIRRORED}
    count = 0
    for _name, path in demos:
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


def test_the_demo_premise_skips_where_the_install_ships_no_demos(monkeypatch):
    """An install with templates and no demo sheets (the macOS cask) fails the premise on
    'assert 0 > 0' unless it skips; no such install is at hand here, so K.shipped answers
    nothing for the demos."""
    shipped = K.shipped

    def without_demos(subdirs=("demos", "template"), **kwargs):
        return [] if subdirs == ("demos",) else shipped(subdirs=subdirs, **kwargs)

    monkeypatch.setattr(K, "shipped", without_demos)
    with pytest.raises(pytest.skip.Exception):
        test_kicads_demos_hold_parts_the_check_reads()


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
