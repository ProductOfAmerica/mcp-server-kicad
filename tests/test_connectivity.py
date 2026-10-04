"""Unit tests of the routing model's rules, one rule at a time, with no KiCad install.

The netlist-checked counterparts are in test_routing_safety.py; these pin each rule of
mcp_server_kicad._connectivity on its own, so a KiCad-free CI leg still exercises all of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from routing_fixtures import ORIENTED, fresh, junction, label, place, polyline, wire

from mcp_server_kicad import _connectivity as C
from mcp_server_kicad import _cst


def _root(path):
    return _cst.parse(Path(path).read_bytes()).lists[0]


def _plan(path, specs, net="N", direction="auto", stub_length=2.54):
    pins = [{"reference": r, "pin": p} for r, p in specs]
    return C.plan_wire_pins(_root(path), pins, net, direction, stub_length)


# ---------------------------------------------------------------------------------------------
# The transform
# ---------------------------------------------------------------------------------------------


def test_every_orientation_lands_where_kicad_draws_it(tmp_path):
    """Rotate, then mirror: the KiCad-true pin ends and outward directions of the pressure
    test's 12-orientation sweep, for a resistor, a diode and a transistor."""
    p = fresh(tmp_path)
    want = {}
    for row, (symbol, table) in enumerate(ORIENTED.items()):
        y = 50.8 * (row + 1)
        for i, ((rot, mir), ends) in enumerate(table.items()):
            ref, x = f"{symbol[0]}{i + 1}", 25.4 + 20.32 * i
            place(p, symbol, ref, x, y, rot=rot, mirror=mir)
            for k, (dx, dy, d) in enumerate(ends):
                want[(ref, str(k + 1))] = (C.kiround((x + dx) * 1e4), C.kiround((y + dy) * 1e4), d)
    m = C.Model(_root(p))
    got = {(it.ref, it.num): (it.x, it.y, it.out) for it in m.items if it.kind == "pin"}
    assert got == want


def _symbol(at: bytes, extra: bytes = b"") -> object:
    return _cst.parse(b'(symbol (lib_id "x") ' + extra + at + b")").lists[0]


def test_a_mirror_written_before_at_is_ignored():
    """KiCad's parser resets the transform at (at ...), so an earlier (mirror) is lost."""
    before = _symbol(b"(at 0 0 90)", b"(mirror y) ")
    after = _cst.parse(b'(symbol (lib_id "x") (at 0 0 90) (mirror y))').lists[0]
    assert C.symbol_transform(before, "U1")[1] == C._ROT[90]
    assert C.symbol_transform(after, "U1")[1] == C._compose(C._ROT[90], C._MIR["y"])


def test_an_angle_kicad_truncates_is_read_the_same_way():
    """KiCad casts the parsed double to int, so 90.5 is 90 and 359 is unloadable."""
    assert C.symbol_transform(_symbol(b"(at 1 2 90.5)"), "U1") == ((10000, 20000), C._ROT[90])
    with pytest.raises(C.Refusal) as e:
        C.symbol_transform(_symbol(b"(at 1 2 359)"), "U1")
    assert e.value.codes == ("unloadable",)


@pytest.mark.no_kicad_validation
def test_an_unloadable_angle_refuses_the_whole_call(tmp_path):
    """The file is unloadable on purpose (that is the subject), so kicad-cli cannot check it."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    data = Path(p).read_bytes().replace(b"(at 101.6 101.6 0)", b"(at 101.6 101.6 45)", 1)
    Path(p).write_bytes(data)
    plan = _plan(p, [("R1", "1")])
    assert plan.codes == ["unloadable"]
    assert "angle 45" in plan.refusal()


def test_ints_format_exactly():
    assert [C.mm(v) for v in (977900, 1016000, -12700, 0, 1, 25400)] == [
        "97.79",
        "101.6",
        "-1.27",
        "0",
        "0.0001",
        "2.54",
    ]
    assert C.iu("97.79") == 977900 and C.iu("-0.00005") == -1


# ---------------------------------------------------------------------------------------------
# The touch rule (R1 at (101.6, 101.6): pin 1 at (101.6, 97.79), outward up, 1x stub end at
# (101.6, 95.25))
# ---------------------------------------------------------------------------------------------


def _r1(tmp_path):
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    return p


def _outcome(plan) -> str:
    if plan.refused:
        return "refused"
    return "label" if not plan.wires else "stub"


def test_a_clear_pin_gets_the_outward_stub(tmp_path):
    plan = _plan(_r1(tmp_path), [("R1", "1")])
    assert plan.wires == [((1016000, 977900), (1016000, 952500))]
    assert plan.labels == [((1016000, 952500), 90)]


@pytest.mark.parametrize(
    ("obstacle", "expect"),
    [
        # Rule 2: anything at or within 0.05 mm of the new stub end.
        pytest.param(lambda p: label(p, "X", 101.6, 95.25), "label", id="label_at_end"),
        pytest.param(lambda p: label(p, "X", 101.63, 95.25), "label", id="label_near_end"),
        pytest.param(lambda p: wire(p, 95, 95.25, 110, 95.25), "label", id="wire_through_end"),
        # Rule 3: anything on the stub's interior, collinear overlaps, crossings.
        pytest.param(lambda p: junction(p, 101.6, 96.52), "label", id="junction_on_path"),
        pytest.param(lambda p: label(p, "X", 101.6, 96.52), "label", id="label_on_path"),
        pytest.param(lambda p: wire(p, 101.6, 96.52, 101.6, 90), "label", id="overlap"),
        pytest.param(lambda p: wire(p, 95, 96.52, 110, 96.52), "label", id="crossing_wire"),
        pytest.param(lambda p: polyline(p, 95, 96.52, 110, 96.52), "label", id="crossing_line"),
        # Rule 1 for both candidates: something at or through the pin end itself.
        pytest.param(lambda p: wire(p, 95, 97.79, 110, 97.79), "refused", id="wire_through_pin"),
        pytest.param(lambda p: label(p, "X", 101.63, 97.79), "refused", id="label_near_pin"),
        # Exactly at the pin end: a wire end and another pin end are the pin's own connections.
        pytest.param(lambda p: wire(p, 101.6, 97.79, 110, 97.79), "stub", id="wire_ends_at_pin"),
    ],
)
def test_the_touch_rule(tmp_path, obstacle, expect):
    p = _r1(tmp_path)
    obstacle(p)
    assert _outcome(_plan(p, [("R1", "1")])) == expect


def test_a_junction_at_the_pin_end_keeps_the_stub_but_not_a_label_on_it(tmp_path):
    p = _r1(tmp_path)
    junction(p, 101.6, 97.79)
    assert _outcome(_plan(p, [("R1", "1")])) == "stub"
    wire(p, 101.6, 96.52, 101.6, 90)  # now the stub overlaps: only the label is left, and the
    plan = _plan(p, [("R1", "1")])  # junction at P blocks that too
    assert plan.codes == ["touch"]
    assert "junction at (101.6, 97.79)" in plan.refusal()


def test_another_pin_end_at_the_same_point_is_allowed(tmp_path):
    p = _r1(tmp_path)
    place(p, "R", "R2", 101.6, 93.98)  # R2:2 at (101.6, 97.79), on R1:1; R2:1 at 90.17
    # The 2.54 mm stub ends at (101.6, 95.25), inside R2's body, touching no other item.
    assert _outcome(_plan(p, [("R1", "1")])) == "stub"


def test_an_explicit_direction_is_honoured(tmp_path):
    plan = _plan(_r1(tmp_path), [("R1", "1")], direction="left")
    assert plan.wires == [((1016000, 977900), (990600, 977900))]
    assert plan.labels == [((990600, 977900), 180)]


def test_items_a_call_adds_block_its_next_pin(tmp_path):
    """H-C15: the second stub would run up the first one's 2.54 mm."""
    p = _r1(tmp_path)
    place(p, "R", "R2", 101.6, 101.6)
    plan = _plan(p, [("R1", "1"), ("R2", "1")])
    assert len(plan.wires) == 1 and len(plan.labels) == 2
    assert "R2:1: label 'N' on the pin end (101.6, 97.79)" in plan.lines[1]


def test_a_refusal_names_every_blocked_pin(tmp_path):
    p = _r1(tmp_path)
    place(p, "R", "R2", 152.4, 101.6)
    wire(p, 95, 97.79, 110, 97.79)
    wire(p, 145, 97.79, 160, 97.79)
    text = _plan(p, [("R1", "1"), ("R2", "1")]).refusal()
    assert text.startswith("[touch] wire_pins_to_net refused the whole call; nothing was written.")
    assert "R1:1" in text and "R2:1" in text
