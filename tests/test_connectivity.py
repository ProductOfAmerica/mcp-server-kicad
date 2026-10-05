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


def _entry(path, x, y):
    """A bus entry from (x, y) up and to the right, 2.54 mm each way: its middle is 1.27 mm in."""
    from routing_fixtures import bus_entry

    bus_entry(path, x, y, 2.54, -2.54)


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
        # What the possible view joins, the touch rule must avoid: a bus entry's body at the stub
        # end or through the pin end, and a graphic line's end on the stub.
        pytest.param(lambda p: _entry(p, 100.33, 96.52), "label", id="entry_body_at_end"),
        # (a body through the pin end is refused earlier, as [bus_entry]: a guard)
        pytest.param(lambda p: _entry(p, 100.33, 99.06), "refused", id="entry_body_at_pin"),
        pytest.param(
            lambda p: polyline(p, 101.6, 96.52, 110, 96.52), "label", id="gline_end_on_stub"
        ),
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


def test_a_foreign_pin_end_near_the_pin_end_refuses(tmp_path):
    """Guard: the routing review's mutant X8, rule 1 letting a pin within 0.05 mm of the pin end
    through as if it met it exactly, passed every test. R2's pin 2 end sits 0.03 mm from
    R1:1's: the stub runs over it and a label on the pin would be within the margin, so the
    call refuses. KiCad joins exact points only, so no netlist test can see this."""
    p = _r1(tmp_path)
    place(p, "R", "R2", 101.6, 93.98)
    data = Path(p).read_bytes()  # the symbol and its hidden fields at its origin move together
    assert b"(at 101.6 93.98 0)" in data
    Path(p).write_bytes(data.replace(b"(at 101.6 93.98 0)", b"(at 101.6 93.95 0)"))
    plan = _plan(p, [("R1", "1")])
    assert plan.codes == ["touch"], _first_line(plan)


def test_an_explicit_direction_is_honoured(tmp_path):
    plan = _plan(_r1(tmp_path), [("R1", "1")], direction="left")
    assert plan.wires == [((1016000, 977900), (990600, 977900))]
    assert plan.labels == [((990600, 977900), 180)]


def test_items_a_call_adds_join_the_model_before_its_next_pin(tmp_path):
    """H-C15. R1 and R2 sit on top of each other, so R2:1 shares R1:1's point. Once R1:1's stub
    and label are in the model, R2:1 is already on N: no second stub, no second label."""
    p = _r1(tmp_path)
    place(p, "R", "R2", 101.6, 101.6)
    plan = _plan(p, [("R1", "1"), ("R2", "1")])
    assert len(plan.wires) == 1 and len(plan.labels) == 1
    assert plan.lines[1].startswith("R2:1: already on 'N' via label 'N' at (101.6, 95.25)")


def test_a_refusal_names_every_blocked_pin(tmp_path):
    p = _r1(tmp_path)
    place(p, "R", "R2", 152.4, 101.6)
    wire(p, 95, 97.79, 110, 97.79)
    wire(p, 145, 97.79, 160, 97.79)
    text = _plan(p, [("R1", "1"), ("R2", "1")]).refusal()
    assert text.startswith("[touch] wire_pins_to_net refused the whole call; nothing was written.")
    assert "R1:1" in text and "R2:1" in text


# ---------------------------------------------------------------------------------------------
# Validation (design 4.8), before any file is read
# ---------------------------------------------------------------------------------------------

_BAD_ARGS = [
    pytest.param({"label_text": ""}, id="empty"),
    pytest.param({"label_text": " VCC"}, id="leading_space"),
    pytest.param({"label_text": "VCC "}, id="trailing_space"),
    pytest.param({"label_text": "/VCC"}, id="sheet_path_slash"),
    pytest.param({"label_text": "${NET}"}, id="text_variable"),
    pytest.param({"label_text": "Net-(R1-Pad1)"}, id="auto_name"),
    pytest.param({"label_text": "unconnected-(R1-Pad1)"}, id="auto_unconnected"),
    pytest.param({"label_text": "D[0..7]"}, id="bus_range"),
    pytest.param({"label_text": "{A B}"}, id="bus_group"),
    pytest.param({"direction": "sideways"}, id="direction"),
    pytest.param({"stub_length": 0}, id="stub_zero"),
    pytest.param({"stub_length": -2.54}, id="stub_negative"),
    pytest.param({"stub_length": 2.5401}, id="stub_off_grid"),
    pytest.param({"stub_length": 4e-05}, id="stub_rounds_to_zero"),
    pytest.param({"stub_length": float("nan")}, id="stub_nan"),
    pytest.param({"stub_length": True}, id="stub_bool"),
    pytest.param({"stub_length": "2.54"}, id="stub_text"),
    pytest.param({"stub_length": 1e305}, id="stub_overflow"),
    pytest.param({"stub_length": 1270}, id="stub_absurd"),
    pytest.param({"stub_length": 10**400}, id="stub_huge_int"),
]


@pytest.mark.parametrize("bad", _BAD_ARGS)
@pytest.mark.parametrize("with_pins", [True, False], ids=["pins", "no_pins"])
def test_bad_arguments_refuse_before_reading(tmp_path, bad, with_pins):
    from mcp.server.mcpserver.exceptions import ToolError

    from mcp_server_kicad import schematic
    from mcp_server_kicad.models import PinRefSpec

    p = _r1(tmp_path)
    before = Path(p).read_bytes()
    kw = {"label_text": "N", **bad}
    pins: list[PinRefSpec] = [{"reference": "R1", "pin": "1"}] if with_pins else []
    with pytest.raises(ToolError, match=r"^\[validation\] "):
        schematic.wire_pins_to_net(pins, schematic_path=p, **kw)
    assert Path(p).read_bytes() == before


@pytest.mark.parametrize("name", ["~{RESET}", "V_{CC}", "A^{2}", "+3V3", "SDA"])
def test_formatting_markup_is_not_bus_syntax(tmp_path, name):
    assert not _plan(_r1(tmp_path), [("R1", "1")], net=name).refused


def test_a_malformed_pin_entry_refuses(tmp_path):
    plan = C.plan_wire_pins(_root(_r1(tmp_path)), [{"reference": "R1"}], "N", "auto", 2.54)
    assert plan.codes == ["validation"]


def test_the_same_pad_twice_is_wired_once(tmp_path):
    plan = _plan(_r1(tmp_path), [("R1", "1"), ("R1", "1")])
    assert len(plan.wires) == 1 and len(plan.labels) == 1
    assert "R1:1: same pad as R1:1; counted once." in plan.lines


# ---------------------------------------------------------------------------------------------
# Names, by KiCad's rule (design 4.2), and the two views
# ---------------------------------------------------------------------------------------------


def _named(tmp_path, *, net="N"):
    """R1 at (101.6, 101.6) with a raw wire from pin 1 left to (88.9, 97.79)."""
    p = _r1(tmp_path)
    wire(p, 101.6, 97.79, 88.9, 97.79)
    return p


def _first_line(plan) -> str:
    return plan.refusal() if plan.refused else plan.lines[0]


@pytest.mark.parametrize("stored", ["A{slash}B", "A/B"])
@pytest.mark.parametrize("asked", ["A/B", "A{slash}B"])
def test_a_slash_in_a_net_name_is_one_net_however_it_is_spelled(tmp_path, stored, asked):
    """KiCad names a label's net EscapeString(UnescapeString(text), CTX_NETNAME) (9.0.8 and
    10.0.6 connection_graph.cpp and string_utils.cpp), so 'A/B' and 'A{slash}B' are one net,
    the one KiCad shows as A/B. The model compared the raw text: a pin on 'A{slash}B' was
    refused [names] for 'A/B', and 'A{slash}B' itself was refused as bus syntax (the routing
    review's e4_slash, and e4c_pic on KiCad's pic_programmer demo)."""
    p = _named(tmp_path)
    label(p, stored, 88.9, 97.79)
    plan = _plan(p, [("R1", "1")], net=asked)
    assert not plan.refused and not plan.labels, _first_line(plan)


def test_a_names_refusal_gives_the_name_as_kicad_shows_it(tmp_path):
    """The [names] remedy said to pass 'A{slash}B', which validation then refused; the name
    that works is the one KiCad shows."""
    p = _named(tmp_path)
    label(p, "A{slash}B", 88.9, 97.79)
    text = _plan(p, [("R1", "1")], net="X").refusal()
    assert "carries 'A/B' via label 'A/B'" in text and "{slash}" not in text, text


def test_a_name_spelled_another_way_is_not_a_new_net(tmp_path):
    """The routing review's e4_slash case b: R2:1 wired to 'A/B' beside a label stored
    'A{slash}B' joins that net, and the result said nothing on the sheet carried 'A/B'."""
    p = _named(tmp_path)
    label(p, "A{slash}B", 88.9, 97.79)
    place(p, "R", "R2", 152.4, 101.6)
    plan = _plan(p, [("R2", "1")], net="A/B")
    assert not plan.refused
    assert "'A/B' joins on this sheet: label 'A/B' at (88.9, 97.79)." in plan.success()


def test_a_power_symbol_names_its_net_by_its_value(tmp_path):
    from routing_fixtures import power

    p = _named(tmp_path)
    power(p, "GND", "#PWR01", 88.9, 97.79, rot=270)  # its pin end is its origin
    refused = _plan(p, [("R1", "1")], net="VCC")
    assert refused.codes == ["names"] and "'GND'" in refused.refusal()
    noop = _plan(p, [("R1", "1")], net="GND")
    assert not noop.refused and not noop.wires and not noop.labels
    assert "already on 'GND' via power symbol #PWR01" in noop.lines[0]


def test_pwr_flag_names_nothing(tmp_path):
    from routing_fixtures import power

    p = _named(tmp_path)
    power(p, "PWR_FLAG", "#FLG01", 88.9, 97.79)
    assert not _plan(p, [("R1", "1")], net="VCC").refused


def _4011(tmp_path):
    """U1 = 4011 with all four gates placed, so its unit-0 power pins are drawn four times."""
    from routing_fixtures import place_units

    p = fresh(tmp_path)
    place_units(
        p, "4011", "U1", [(1, 50.8, 101.6), (2, 101.6, 101.6), (3, 152.4, 101.6), (4, 203.2, 101.6)]
    )
    return p


def test_a_visible_power_in_pin_names_nothing(tmp_path):
    """4011 pin 14 (Vdd) is power_in and visible: KiCad names no net after it."""
    p = _4011(tmp_path)
    assert not _plan(p, [("U1", "14")], net="RAIL").refused


def _edit_lib_pin(path, symbol: str, number: str, hide=False, alternate=None, alt_type="") -> None:
    """Edit lib pin *number* of *symbol* in this file's lib_symbols: hide it, and give it the
    alternate *alternate* of type *alt_type*."""
    data = Path(path).read_bytes()
    tree = _cst.parse(data)
    root = tree.lists[0]
    lib = next(s for s in root.find("lib_symbols").find_all("symbol") if s.atoms[1].text == symbol)
    for sub in lib.find_all("symbol"):
        for pin in sub.find_all("pin"):
            if pin.find("number").atoms[1].text == number:
                if hide:
                    pin.insert_after(pin.find("length"), _cst.parse(b"(hide yes)").lists[0], b" ")
                if alternate:
                    alt = f'(alternate "{alternate}" {alt_type} line)'.encode()
                    pin.append_child(_cst.parse(alt).lists[0], b" ")
    Path(path).write_bytes(_cst.serialize(tree))


def test_a_hidden_power_in_pin_names_its_net(tmp_path):
    p = _4011(tmp_path)
    _edit_lib_pin(p, "4011", "14", hide=True)
    assert _plan(p, [("U1", "14")], net="RAIL").codes == ["names"]
    noop = _plan(p, [("U1", "14")], net="Vdd")
    assert not noop.refused and not noop.labels


@pytest.mark.parametrize(
    ("alternate", "alt_type", "select", "named"),
    [
        pytest.param("VBAT", "power_in", "VBAT", True, id="power_in"),
        pytest.param("ALTP", "passive", "ALTP", False, id="passive"),
        # SetAlt drops an alternate the library pin does not define, or one spelled like the
        # pin's own name, so the pin keeps its own type.
        pytest.param("ALTP", "passive", "NOPE", True, id="undefined"),
        pytest.param("Vdd", "passive", "Vdd", True, id="named_like_the_pin"),
    ],
)
def test_an_alternate_sets_the_type_and_never_the_name(
    tmp_path, alternate, alt_type, select, named
):
    """DEC-15. KiCad takes a pin's type from the alternate placed on it, when SetAlt accepts it,
    and names a hidden power_in pin's net by the library pin's own name, never the alternate's
    (sch_pin.cpp IsGlobalPower and GetDefaultNetName, 9.0.8 and 10.0.6). The model used to
    count both names as possible and neither as certain, so it refused what KiCad reads
    plainly."""
    from routing_fixtures import select_alternate

    p = _4011(tmp_path)
    _edit_lib_pin(p, "4011", "14", hide=True, alternate=alternate, alt_type=alt_type)
    select_alternate(p, "U1", "14", select)
    on_vdd = _plan(p, [("U1", "14")], net="Vdd")
    on_alt = _plan(p, [("U1", "14")], net="VBAT")
    if named:
        assert not on_vdd.refused and not on_vdd.labels, _first_line(on_vdd)
        assert on_alt.codes == ["names"] and "'Vdd'" in on_alt.refusal()
    else:
        assert on_vdd.labels and on_alt.labels


def test_a_power_symbol_pin_an_alternate_takes_off_power_in_names_nothing(tmp_path):
    """IsGlobalPower needs the type power_in, which an alternate sets, so with a passive
    alternate placed not even a power symbol's Value names the net."""
    from routing_fixtures import power, select_alternate

    p = _named(tmp_path)
    power(p, "GND", "#PWR01", 88.9, 97.79, rot=270)  # its pin end is its origin
    _edit_lib_pin(p, "GND", "1", alternate="ALTP", alt_type="passive")
    select_alternate(p, "#PWR01", "1", "ALTP")
    assert not _plan(p, [("R1", "1")], net="VCC").refused


def test_the_margin_reaches_a_near_miss_label(tmp_path):
    """GM-06. A label 0.03 mm past the wire end joins nothing in KiCad, but the possible view's
    0.05 mm margin reaches it, so another name refuses."""
    p = _named(tmp_path)
    label(p, "X", 88.87, 97.79)
    assert _plan(p, [("R1", "1")]).codes == ["names"]


def test_a_join_only_the_possible_view_sees_is_wired_explicitly(tmp_path):
    p = _named(tmp_path)
    label(p, "N", 88.87, 97.79)
    plan = _plan(p, [("R1", "1")])
    assert not plan.refused and plan.labels
    assert "possibly reached 'N' already" in plan.lines[0]


def test_a_pin_on_n_through_this_calls_label_is_not_counted_as_wired(tmp_path):
    """The routing review's e8_misc: R2:1 joins R1:1 by a wire, so R1:1's new label puts it on N
    with nothing written for it, and the head counted it among the pins wired."""
    p = _r1(tmp_path)
    place(p, "R", "R2", 127, 101.6)
    wire(p, 101.6, 97.79, 127, 97.79)
    plan = _plan(p, [("R1", "1"), ("R2", "1")])
    assert plan.wired == 1 and plan.success().startswith("Wired 1 pins to 'N'.")
    assert "R2:1: already on 'N' via label 'N' at (101.6, 95.25), added by this call" in plan.lines


def test_a_names_refusal_on_a_connect_pins_label_leads_with_remove_label(tmp_path):
    """The review's repro_names_auto: the remedy said to pass the name the net carries, and a
    connect_pins label's Net-(...) name is one validation refuses, so the route that works,
    remove_label and call again, comes first, and alone when no other name is on the net."""
    p = _named(tmp_path)
    label(p, "Net-(R1-1)", 88.9, 97.79)
    text = _plan(p, [("R1", "1")], net="SIG").refusal()
    assert "Remedy: 'Net-(R1-1)' looks like the label connect_pins writes" in text, text
    assert "remove_label('Net-(R1-1)', 88.9, 97.79)" in text
    assert "pass that name" not in text


def test_a_refusal_marks_the_pins_it_did_not_refuse_as_not_written(tmp_path):
    """The review's repro_names_auto: a refusal listed R2:1's stub as if drawn."""
    p = _named(tmp_path)
    label(p, "OTHER", 88.9, 97.79)
    place(p, "R", "R2", 152.4, 101.6)
    text = _plan(p, [("R1", "1"), ("R2", "1")]).refusal()
    line = next(x for x in text.splitlines() if x.startswith("- R2:1:"))
    assert line.endswith("(planned only; not written)"), line


def test_net_mates_are_reported(tmp_path):
    p = _named(tmp_path)
    place(p, "R", "R2", 88.9, 101.6)  # R2:1 at (88.9, 97.79), the wire's far end
    plan = _plan(p, [("R1", "1")])
    assert "Already connected to a wired pin, so now also on it: R2:1" in plan.success()


def test_a_new_local_net_is_a_warning(tmp_path):
    plan = _plan(_r1(tmp_path), [("R1", "1")], net="GND")
    assert "Warning: nothing on this sheet carried 'GND'" in plan.success()


def test_joining_a_name_on_this_sheet_says_so(tmp_path):
    from routing_fixtures import power

    p = _r1(tmp_path)
    power(p, "GND", "#PWR01", 152.4, 101.6)
    text = _plan(p, [("R1", "1")], net="GND").success()
    assert "'GND' joins on this sheet: power symbol #PWR01" in text
    assert "Warning" not in text


# ---------------------------------------------------------------------------------------------
# Outright refusals (design 4.3) and the order of the checks (H-C11, H-C12)
# ---------------------------------------------------------------------------------------------


def _nc_part(tmp_path):
    """U1 with pin 1 of no-connect type at (96.52, 101.6) and an ordinary pin 2."""
    from routing_fixtures import custom_lib, place_custom

    p = fresh(tmp_path)
    lib = custom_lib(
        tmp_path,
        "NCP",
        [("1", "NC", "no_connect", -5.08, 0, 0, False), ("2", "A", "passive", 5.08, 0, 180, False)],
    )
    place_custom(p, lib, "NCP", "U1", 101.6, 101.6)
    return p


def test_a_no_connect_type_pin_is_refused(tmp_path):
    p = _nc_part(tmp_path)
    assert _plan(p, [("U1", "1")]).codes == ["nc_type"]
    assert not _plan(p, [("U1", "2")]).refused


def test_a_no_connect_flag_on_the_pin_is_refused(tmp_path):
    from routing_fixtures import no_connect

    p = _r1(tmp_path)
    no_connect(p, 101.6, 97.79)
    plan = _plan(p, [("R1", "1")])
    assert plan.codes == ["nc_flag"] and "remove_no_connect" in plan.refusal()


def test_the_type_is_reported_before_a_flag_on_it(tmp_path):
    from routing_fixtures import no_connect

    p = _nc_part(tmp_path)
    no_connect(p, 96.52, 101.6)
    assert _plan(p, [("U1", "1")]).codes == ["nc_type"]


def test_a_flagged_pin_already_on_n_is_a_no_op(tmp_path):
    """Guard on the order (H-C11): the no-op is decided first, so a pin already on N writes
    nothing whatever else is on it. Before the flag check existed this passed trivially."""
    from routing_fixtures import no_connect, stub

    p = _r1(tmp_path)
    stub(p, 101.6, 97.79, 0, -2.54, "N")
    no_connect(p, 101.6, 97.79)
    plan = _plan(p, [("R1", "1")])
    assert not plan.refused and not plan.wires and not plan.labels


def _bus_net(tmp_path, name=None):
    """R1:1 wired up to a bus entry whose other end is on an unlabelled bus."""
    from routing_fixtures import bus, bus_entry

    p = _r1(tmp_path)
    wire(p, 101.6, 97.79, 101.6, 93.98)
    bus_entry(p, 101.6, 93.98, 2.54, -2.54)  # far end (104.14, 91.44), the bus's start
    bus(p, 104.14, 91.44, 127, 91.44)
    if name:
        label(p, name, 101.6, 95.25)  # on the wire
    return p


def test_every_unhandled_item_is_reported_in_a_fixed_order(tmp_path):
    """H-C12: bus, then bus entry, though the net reaches the entry first."""
    plan = _plan(_bus_net(tmp_path), [("R1", "1")])
    assert plan.codes == ["bus", "bus_entry"]
    text = plan.refusal()
    assert text.startswith("[bus] [bus_entry] ")
    assert "otherwise stop and report" in text and "add_label" in text


def test_an_unhandled_item_is_reported_before_a_name(tmp_path):
    assert _plan(_bus_net(tmp_path, name="OTHER"), [("R1", "1")]).codes == ["bus", "bus_entry"]


def test_the_flag_is_reported_before_an_unhandled_item(tmp_path):
    from routing_fixtures import no_connect

    p = _bus_net(tmp_path)
    no_connect(p, 101.6, 97.79)
    assert _plan(p, [("R1", "1")]).codes == ["nc_flag"]


def test_a_name_is_reported_before_a_blocked_stub(tmp_path):
    """Guard on the order: names are decided before geometry, and the name is the refusal the
    caller can act on. R1:1 sits on a wire carrying OTHER that also blocks every candidate."""
    p = _r1(tmp_path)
    wire(p, 95, 97.79, 110, 97.79)
    label(p, "OTHER", 95, 97.79)
    assert _plan(p, [("R1", "1")]).codes == ["names"]


def test_a_text_variable_on_the_net_is_refused(tmp_path):
    p = _named(tmp_path)
    label(p, "${RAIL}", 88.9, 97.79)
    assert _plan(p, [("R1", "1")]).codes == ["text_var"]


def test_a_global_labels_intersheet_field_is_not_a_text_variable(tmp_path):
    """Guard (the check is new, so this passed before it): KiCad 9 gives every global label an
    Intersheetrefs field holding ${INTERSHEET_REFS}, and counting it would refuse every net
    with a global label (H-C8). Here R1:1 reaches G only through a junction on a wire's
    interior, which the narrow view does not trust, so the call is not a no-op and the field is
    what decides."""
    from routing_fixtures import splice

    p = _named(tmp_path)  # R1:1 wired left to (88.9, 97.79)
    junction(p, 95.25, 97.79)
    wire(p, 95.25, 97.79, 95.25, 92.71)
    splice(
        p,
        '(global_label "G" (shape input) (at 95.25 92.71 90) (fields_autoplaced yes)'
        ' (effects (font (size 1.27 1.27)) (justify left)) (uuid "x")'
        ' (property "Intersheetrefs" "${INTERSHEET_REFS}" (at 95.25 92.71 0)'
        " (effects (font (size 1.27 1.27)) (justify left) (hide yes))))",
    )
    plan = _plan(p, [("R1", "1")], net="G")
    assert not plan.refused and plan.labels, plan.lines
    assert "possibly reached 'G' already" in plan.lines[0]


def _with_jumpers(tmp_path, form: bytes, numbers):
    """U1 (JMP) with its first pin on R1:1's end, and the jumper declaration *form* added to
    its library entry. kicad-cli 9.0.8 cannot load a schematic with jumpers ("Failed to load
    schematic", measured), so they go into the parsed tree only, never into the file."""
    from routing_fixtures import custom_lib, place_custom

    p = _r1(tmp_path)
    lib = custom_lib(
        tmp_path,
        "JMP",
        [
            (numbers[0], "A", "passive", -5.08, 0, 0, False),
            (numbers[1], "B", "passive", 5.08, 0, 180, False),
        ],
    )
    place_custom(p, lib, "JMP", "U1", 106.68, 97.79)  # its first pin at (101.6, 97.79)
    root = _root(p)
    entry = next(s for s in root.find("lib_symbols").find_all("symbol") if s.atoms[1].text == "JMP")
    entry.append_child(_cst.parse(form).lists[0], b" ")
    return root


@pytest.mark.parametrize(
    ("form", "numbers"),
    [
        pytest.param(b'(jumper_pin_groups ("1" "2"))', ("1", "2"), id="groups"),
        pytest.param(b"(duplicate_pin_numbers_are_jumpers yes)", ("1", "1"), id="duplicates"),
    ],
)
def test_a_net_reaching_a_symbol_with_jumper_pins_is_refused(tmp_path, form, numbers):
    """H-C9. KiCad 10 joins jumpered pins inside the symbol, which nothing here models."""
    root = _with_jumpers(tmp_path, form, numbers)
    for ref, num in (("R1", "1"), ("U1", numbers[1])):
        plan = C.plan_wire_pins(root, [{"reference": ref, "pin": num}], "N", "auto", 2.54)
        assert plan.codes == ["jumper"], (ref, num, plan.lines)


def _two_units(tmp_path):
    """U1 = 74LS04 placed once with two instance entries, unit 1 and unit 2: the shape a sheet
    used twice with a different unit per instance has. R1:1 sits on U1 pin 1's end."""
    from routing_fixtures import add_instance

    p = fresh(tmp_path)
    place(p, "74LS04", "U1", 152.4, 101.6)  # unit 1: pin 1 at (144.78, 101.6)
    add_instance(p, "U1", "/00000000-0000-0000-0000-00000000000a/0000000b", 2)
    place(p, "R", "R1", 144.78, 105.41)  # R1:1 at (144.78, 101.6)
    return p


def test_instance_entries_that_disagree_on_the_unit_refuse_the_pin(tmp_path):
    p = _two_units(tmp_path)
    assert _plan(p, [("U1", "1")]).codes == ["units_disagree"]
    assert _plan(p, [("U1", "no such pin")]).codes == ["units_disagree"]  # before the lookup


def test_a_net_reaching_a_symbol_whose_units_disagree_is_refused(tmp_path):
    """H-C16: its pins enter the model for every listed unit, none of them certain."""
    assert _plan(_two_units(tmp_path), [("R1", "1")]).codes == ["units_disagree"]


def test_a_unit0_pad_of_a_part_not_fully_placed_here_is_refused(tmp_path):
    """The KiCad-free twin of HIER-14: one 4011 gate here, so pad 14 has copies elsewhere."""
    p = fresh(tmp_path)
    place(p, "4011", "U1", 101.6, 101.6)
    assert _plan(p, [("U1", "14")]).codes == ["unit0_unplaced"]
    assert not _plan(p, [("U1", "1")]).refused  # only unit 1 draws pad 1


def test_a_common_pad_refusal_claims_no_copy_it_cannot_know(tmp_path):
    """The routing review's e9_unit0: with only unit 1 of the 4011 placed anywhere, pad 14 has
    one copy, here, and kicad-cli lists one U1:14 node; the refusal said the pad has copies this
    sheet cannot see. It now says what is known: any other unit placed elsewhere draws one."""
    p = fresh(tmp_path)
    place(p, "4011", "U1", 101.6, 101.6)
    text = _plan(p, [("U1", "14")]).refusal()
    assert "has copies this sheet cannot see" not in text, text
    assert "any such unit placed on another sheet draws a copy of the pad there" in text, text


def test_a_pad_also_drawn_by_a_unit_not_placed_here_is_refused(tmp_path):
    """Generalised from the unit-0 rule (design 4.5; an inference, not a measured case): pad 3
    is drawn by both units and only unit 1 is placed here, so its other copy is out of sight.
    The same holds for a pin whose net reaches that pad."""
    from routing_fixtures import custom_lib, place_custom

    p = fresh(tmp_path)
    lib = custom_lib(
        tmp_path,
        "DUO",
        [
            ("1", "A", "passive", -5.08, 2.54, 0, False, 1),
            ("3", "S", "passive", -5.08, -2.54, 0, False, 1),
            ("2", "B", "passive", -5.08, 2.54, 0, False, 2),
            ("3", "S", "passive", -5.08, -2.54, 0, False, 2),
        ],
    )
    place_custom(p, lib, "DUO", "U1", 101.6, 101.6)  # pad 3 at (96.52, 104.14)
    place(p, "R", "R1", 96.52, 107.95)  # R1:1 on pad 3
    assert _plan(p, [("U1", "3")]).codes == ["unit0_unplaced"]
    assert _plan(p, [("R1", "1")]).codes == ["unit0_unplaced"]
    assert not _plan(p, [("U1", "1")]).refused


def _unresolved(tmp_path):
    """R1, and D3 off R1's net with a lib_name that names no lib_symbols entry. The lib_name is
    set in the parsed tree only: kicad-cli 9.0.8's ERC crashes on such a file (exit 0xC0000005,
    measured), so the output oracle could not check it on disk."""
    from routing_fixtures import _placed

    p = _r1(tmp_path)
    place(p, "D", "D3", 152.4, 101.6)
    root = _root(p)
    _placed(root, "D3")[0].find("lib_name").atoms[1].set_text("D_9")
    return root


def test_an_unresolved_symbol_anywhere_refuses_the_whole_call(tmp_path):
    """PI-17 without its geometry: D3 is nowhere near R1:1 and the call still refuses, alone,
    before any pin is looked up."""
    pins = [{"reference": "R1", "pin": "1"}, {"reference": "NOPE", "pin": "1"}]
    plan = C.plan_wire_pins(_unresolved(tmp_path), pins, "N", "auto", 2.54)
    assert plan.codes == ["derived"]
    assert "D3 uses library symbol 'D_9'" in plan.refusal()


def test_a_derived_library_entry_refuses_the_whole_call(tmp_path):
    p = _r1(tmp_path)
    place(p, "D", "D3", 152.4, 101.6)
    root = _root(p)  # KiCad never writes (extends) into a schematic, so add it in memory only
    entry = next(s for s in root.find("lib_symbols").find_all("symbol") if s.atoms[1].text == "D")
    entry.append_child(_cst.parse(b'(extends "D0")').lists[0], b" ")
    plan = C.plan_wire_pins(root, [{"reference": "R1", "pin": "1"}], "N", "auto", 2.54)
    assert plan.codes == ["derived"] and "is derived" in plan.refusal()


def test_a_library_symbol_named_twice_refuses_the_whole_call(tmp_path):
    """The routing review's e3b_duplib_swap: the model drew R1 from the first of two entries
    named "R" and KiCad from the last, so "Wired R1:1" put R1's pad 2 on N in kicad-cli 9.0.8.
    Which entry the file meant is unknown, so the call refuses."""
    from routing_fixtures import duplicate_r_swapped

    p = _r1(tmp_path)
    duplicate_r_swapped(p)
    plan = _plan(p, [("R1", "1")])
    assert plan.codes == ["derived"]
    assert "holds 2 entries named 'R'" in plan.refusal()


@pytest.mark.parametrize(("lib_name", "entry"), [("A{slash}B", "A/B"), ("A/B", "A{slash}B")])
def test_a_slash_escape_in_a_library_name_reads_as_kicad_reads_it(tmp_path, lib_name, entry):
    """The routing review's e7_libslash: KiCad's parser reads {slash} as '/' in lib_symbols
    names, unit names, lib_name and lib_id (9.0.8 and 10.0.6 sch_io_kicad_sexpr_parser.cpp), so
    these name one symbol. The model compared the raw text and refused the whole call
    [derived]."""
    p = _r1(tmp_path)
    data = Path(p).read_bytes()
    for old in (b'(symbol "R"', b'(symbol "R_0_1"', b'(symbol "R_1_1"'):
        assert data.count(old) == 1, old
        data = data.replace(old, old.replace(b'"R', b'"' + entry.encode()))
    data = data.replace(b'(lib_name "R")', b'(lib_name "%s")' % lib_name.encode(), 1)
    Path(p).write_bytes(data)
    plan = _plan(p, [("R1", "1")])
    assert not plan.refused, plan.refusal()


def test_an_unloadable_symbol_is_reported_before_an_unresolved_one(tmp_path):
    """Guard on the order of the whole-call checks (the [derived] check is new, so this passed
    before it)."""
    from routing_fixtures import _placed

    root = _unresolved(tmp_path)  # the bad angle goes into memory only, like the lib_name
    _placed(root, "R1")[0].find("at").atoms[3].set_text("45")
    plan = C.plan_wire_pins(root, [{"reference": "R1", "pin": "1"}], "N", "auto", 2.54)
    assert plan.codes == ["unloadable"]


# ---------------------------------------------------------------------------------------------
# Duplicate references across the hierarchy (H-C1)
# ---------------------------------------------------------------------------------------------


def _two_sheets(tmp_path, child_ref="R2"):
    """Root h.kicad_sch with R1 and a sheet for c.kicad_sch, which holds one resistor."""
    from routing_fixtures import project_file, sheet

    from mcp_server_kicad import project

    root = fresh(tmp_path, "h")
    child = str(tmp_path / "h" / "c.kicad_sch")
    project.create_schematic(schematic_path=child)
    project_file(tmp_path / "h", "h")
    place(child, "R", child_ref, 101.6, 101.6)
    sheet(root, child, "C", 152.4, 25.4)
    place(root, "R", "R1", 101.6, 101.6)
    return root, child


def _plan_in(path, specs, net="N"):
    pins = [{"reference": r, "pin": p} for r, p in specs]
    return C.plan_wire_pins(_root(path), pins, net, "auto", 2.54, path)


def test_the_hierarchy_is_read_for_the_reference_check(tmp_path):
    root, _child = _two_sheets(tmp_path, child_ref="R1")
    plan = _plan_in(root, [("R1", "1")])
    assert plan.codes == ["dup_ref"] and "on c.kicad_sch" in plan.refusal()
    assert not _plan(root, [("R1", "1")]).refused  # without the path, only this sheet is seen


def test_an_edited_sheet_is_never_served_stale(tmp_path):
    """Guard on the facts cache (new with the check): it is keyed by content, so renaming the
    child's R2 to R1 between two calls is seen by the second."""
    from routing_fixtures import rename_ref

    root, child = _two_sheets(tmp_path)
    assert not _plan_in(root, [("R1", "1")]).refused
    rename_ref(child, "R2", "R1")
    assert _plan_in(root, [("R1", "1")]).codes == ["dup_ref"]


def test_a_missing_sheet_file_is_skipped(tmp_path):
    """Guard: KiCad loads a missing sheet file empty, so it holds no reference."""
    root, child = _two_sheets(tmp_path, child_ref="R1")
    Path(child).unlink()
    assert not _plan_in(root, [("R1", "1")]).refused


@pytest.mark.no_kicad_validation
def test_a_sheet_that_cannot_be_read_refuses(tmp_path):
    """A child that can carry R1, cut off before its last parenthesis: what it holds is
    unknown, so R1's uniqueness is too. The truncated sheet is the subject, which is why the
    output oracle is off. (A sheet that cannot carry the reference is never parsed for it.)"""
    root, child = _two_sheets(tmp_path, child_ref="R1")
    data = Path(child).read_bytes()
    Path(child).write_bytes(data[: data.rindex(b")")])
    plan = _plan_in(root, [("R1", "1")])
    assert plan.codes == ["dup_ref"] and "c.kicad_sch of this hierarchy could not be read" in (
        plan.refusal()
    )


def _two_definitions(tmp_path, third) -> str:
    """U1 placed twice: unit 1 from library symbol PARTA and unit 2 from PARTB. The two agree on
    pads 1 and 2 and differ on pad 3 as *third* (type, unit) says for PARTB."""
    from routing_fixtures import custom_lib, place_custom, rename_ref, set_unit

    p = fresh(tmp_path)
    common = [
        ("1", "A", "passive", -5.08, 0, 0, False, 1),
        ("2", "B", "passive", -5.08, 0, 0, False, 2),
    ]
    lib_a = custom_lib(tmp_path, "PARTA", [*common, ("3", "C", "passive", 5.08, 0, 180, False, 1)])
    kind, unit = third
    lib_b = custom_lib(tmp_path, "PARTB", [*common, ("3", "C", kind, 5.08, 0, 180, False, unit)])
    place_custom(p, lib_a, "PARTA", "U1", 101.6, 101.6)
    place_custom(p, lib_b, "PARTB", "U9", 152.4, 101.6)
    set_unit(p, "U9", 2)
    rename_ref(p, "U9", "U1")
    return p


@pytest.mark.parametrize("third", [("input", 1), ("passive", 2)], ids=["type", "unit"])
def test_two_definitions_sharing_a_reference_differ_in_one_half_of_a_pad(tmp_path, third):
    """Guard: the routing review's mutants M5e and M5f, a pad map holding only the units that
    draw each pad or only its types, still refused the 74LS00 and 74LS04 fixture, which differ
    in both, and only the message changed. Here the two definitions differ in pad 3's type
    alone, or in which unit draws it alone, and each is still two parts."""
    p = _two_definitions(tmp_path, third)
    plan = _plan(p, [("U1", "1")])
    assert plan.codes == ["dup_ref"], _first_line(plan)


def test_the_units_of_one_part_on_one_sheet_are_one_part(tmp_path):
    """Guard: the 4011's four gates all carry U1, distinct units of one definition."""
    assert not _plan(_4011(tmp_path), [("U1", "14")], net="RAIL").refused


def test_the_reference_check_comes_after_units_disagree_and_before_the_lookup(tmp_path):
    from routing_fixtures import add_instance, rename_ref

    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 152.4, 101.6)
    rename_ref(p, "R2", "R1")
    assert _plan(p, [("R1", "no such pin")]).codes == ["dup_ref"]
    add_instance(p, "R1", "/00000000-0000-0000-0000-00000000000a/0000000b", 2)
    assert _plan(p, [("R1", "1")]).codes == ["units_disagree"]


# ---------------------------------------------------------------------------------------------
# Live instance paths (design 4.6, 4.1): references and units as KiCad reads them
# ---------------------------------------------------------------------------------------------

DEAD = "/00000000-0000-0000-0000-00000000000a/0000000b"


def _with_dead_entry(tmp_path, sub: str = ""):
    """Root h.kicad_sch (with h.kicad_pro) holding a sheet for c.kicad_sch, in *sub* when
    given. The child's U1, a 74LS04, is unit 1 at its live path and carries a leftover entry
    for unit 2 on a path no sheet of this project has."""
    from routing_fixtures import add_instance, project_file, sheet

    from mcp_server_kicad import project

    root = fresh(tmp_path, "h")
    d = tmp_path / "h" / sub if sub else tmp_path / "h"
    d.mkdir(parents=True, exist_ok=True)
    child = str(d / "c.kicad_sch")
    project.create_schematic(schematic_path=child)
    project_file(tmp_path / "h", "h")
    place(child, "74LS04", "U1", 101.6, 101.6)
    sheet(root, child, "C", 152.4, 25.4, file=f"{sub}/c.kicad_sch" if sub else None)
    add_instance(child, "U1", DEAD, 2)
    return root, child


def test_a_dead_entry_does_not_count(tmp_path):
    """KiCad draws the unit at the live path (sch_symbol.cpp GetUnitSelection, 9.0.8); every
    entry counted, the leftover unit 2 made U1 look like a reused sheet's disagreeing units."""
    _root, child = _with_dead_entry(tmp_path)
    assert not _plan_in(child, [("U1", "1")]).refused
    assert _plan(child, [("U1", "1")]).codes == ["units_disagree"]  # no path: every entry


def test_a_pin_on_a_unit_not_drawn_here_names_the_unit_kicad_draws(tmp_path):
    """The routing pressure test's PI-06 (unit_instances/4a): U1's live entry is unit 2 under a
    top-level (unit 1). KiCad draws unit 2, so pin 1 is refused, and the refusal named the
    top-level unit as the one placed here and advised switching the body style."""
    from routing_fixtures import _edit, _placed

    _root, child = _with_dead_entry(tmp_path)

    def live_unit_2(root) -> None:
        (u1,) = _placed(root, "U1")
        for path_node in u1.find("instances").find("project").find_all("path"):
            if path_node.atoms[1].text != DEAD:
                path_node.find("unit").atoms[1].set_text("2")

    _edit(child, live_unit_2)
    plan = _plan_in(child, [("U1", "1")])
    assert plan.codes == ["resolve"]
    text = plan.refusal()
    assert "(U1 here: unit 2)" in text and "Place that unit" in text, text


def test_a_sheet_file_in_a_symlink_loop_counts_as_missing(tmp_path):
    """Resolving a symlink loop raises (RuntimeError before Python 3.13, OSError after), and the
    walk resolved sheet paths outside its error handling, so the call died with a raw error.
    No reader can open such a file, so it is a missing sheet, which KiCad loads empty."""
    import os

    root, _child = _two_sheets(tmp_path)
    tree = _root(root)
    for q in tree.find("sheet").find_all("property"):
        if q.atoms[1].text == "Sheetfile":
            q.atoms[2].set_text("loop.kicad_sch")
    Path(root).write_bytes(_cst.serialize(tree))
    d = Path(root).parent
    try:
        os.symlink("loop2.kicad_sch", d / "loop.kicad_sch")
        os.symlink("loop.kicad_sch", d / "loop2.kicad_sch")
    except OSError as e:
        pytest.skip(f"cannot create symlinks here: {e}")
    try:
        assert not _plan_in(root, [("R1", "1")]).refused
    finally:  # the autouse kicad-cli check reads every .kicad_sch here, and cannot read these
        (d / "loop.kicad_sch").unlink()
        (d / "loop2.kicad_sch").unlink()


def test_the_project_is_found_from_a_sheet_in_a_subdirectory(tmp_path):
    """The project file sits a directory above the sheet (RoyalBlue54L-Feather's sch/ layout),
    so its live path is found only by looking up."""
    _root, child = _with_dead_entry(tmp_path, sub="sch")
    assert not _plan_in(child, [("U1", "1")]).refused


def test_an_unreached_sheet_counts_every_entry(tmp_path):
    """Guard (this was every case before live paths): a sheet the project's hierarchy never
    reaches has no live path, so every entry counts, as the prototype counted them."""
    root, child = _with_dead_entry(tmp_path)
    tree = _cst.parse(Path(root).read_bytes())
    tree.lists[0].remove_child(tree.lists[0].find("sheet"))
    Path(root).write_bytes(_cst.serialize(tree))
    assert _plan_in(child, [("U1", "1")]).codes == ["units_disagree"]


def test_a_kicad6_root_table_counts_as_references(tmp_path):
    """Below version 20221002 KiCad takes references from the root's (symbol_instances) table
    (eeschema_helpers.cpp, 9.0.8). Here the table gives the child's R5 the reference R1, the
    root's own R1's. The old version and the table go into the parsed root only."""
    root, child = _two_sheets(tmp_path, child_ref="R5")
    r5 = next(s for s in _root(child).find_all("symbol") if "R5" in _cst.serialize(s).decode())
    sheet_uuid = _root(root).find("sheet").find("uuid").atoms[1].text
    tree = _root(root)
    tree.find("version").atoms[1].set_text("20211123")
    table = (
        f'(symbol_instances (path "/{sheet_uuid}/{r5.find("uuid").atoms[1].text}"'
        ' (reference "R1") (unit 1) (value "R5") (footprint "")))'
    )
    tree.append_child(_cst.parse(table.encode()).lists[0], b"\n")
    pins = [{"reference": "R1", "pin": "1"}]
    plan = C.plan_wire_pins(tree, pins, "N", "auto", 2.54, root)
    assert plan.codes == ["dup_ref"], plan.lines


def test_a_sheet_that_contains_itself_ends_the_walk(tmp_path):
    """Guard: KiCad refuses a recursive hierarchy, and the walk must stop rather than follow it.
    The loop goes into the parsed root only."""
    root, _child = _two_sheets(tmp_path)
    tree = _root(root)
    for q in tree.find("sheet").find_all("property"):
        if q.atoms[1].text == "Sheetfile":
            q.atoms[2].set_text("h.kicad_sch")
    pins = [{"reference": "R1", "pin": "1"}]
    assert not C.plan_wire_pins(tree, pins, "N", "auto", 2.54, root).refused


# ---------------------------------------------------------------------------------------------
# Net classes (H-C2 to H-C5): carriers, the two views of a class, the check
# ---------------------------------------------------------------------------------------------


def _classed(tmp_path, *, area=None, flag=None, sig_flag=False):
    """R1 at (101.6, 101.6), R2 at (127, 101.6) with SIG on R2:1, optional rule area and HV
    directive, and optionally an HV directive on SIG's net too (certainly, at the label)."""
    from routing_fixtures import netclass_flag, rule_area

    p = _r1(tmp_path)
    place(p, "R", "R2", 127, 101.6)
    wire(p, 127, 97.79, 127, 92.71)
    label(p, "SIG", 127, 92.71)
    if area:
        rule_area(p, area)
    if flag:
        netclass_flag(p, "HV", *flag)
    if sig_flag:
        netclass_flag(p, "HV", 127, 92.71)
    return p


#: A box around R1:1's end (101.6, 97.79) and its stub path, directive on the top edge.
_AROUND = [(96.52, 91.44), (106.68, 91.44), (106.68, 99.06), (96.52, 99.06)]
#: A box above the pin end that the 2.54 mm stub (to y 95.25) runs into.
_ABOVE = [(96.52, 88.9), (106.68, 88.9), (106.68, 96.52), (96.52, 96.52)]


def test_a_stub_into_a_classed_area_is_replaced_by_a_label_on_the_pin(tmp_path):
    p = _classed(tmp_path, area=_ABOVE, flag=(101.6, 88.9))
    plan = _plan(p, [("R1", "1")], net="SIG")
    assert not plan.refused and not plan.wires and plan.labels, plan.lines
    assert "net classes ['HV'] are touched" in plan.lines[0]


def test_a_pin_inside_a_classed_area_joining_an_unclassed_net_is_refused(tmp_path):
    """R1:1 certainly has HV and SIG certainly has none, so joining them changes one side's
    class, whichever geometry does it."""
    p = _classed(tmp_path, area=_AROUND, flag=(101.6, 91.44))
    plan = _plan(p, [("R1", "1")], net="SIG")
    assert set(plan.codes) == {"netclass"}, plan.lines
    assert plan.refusal().startswith("[netclass] ")


def test_joining_two_nets_of_one_certain_class_is_allowed(tmp_path):
    p = _classed(tmp_path, area=_AROUND, flag=(101.6, 91.44), sig_flag=True)
    assert not _plan(p, [("R1", "1")], net="SIG").refused


def test_a_class_only_the_named_net_carries_does_not_block(tmp_path):
    """Guard (the check is new): KiCad gives the pin's net N's class, which is the requested
    join, so a class on N's side alone is no reason to refuse."""
    p = _classed(tmp_path, sig_flag=True)
    plan = _plan(p, [("R1", "1")], net="SIG")
    assert not plan.refused and plan.wires


def test_a_directive_inside_an_area_but_off_its_outline_assigns_nothing(tmp_path):
    """Guard: KiCad attaches a directive to a rule area only within 5 IU of the outline
    (sch_rule_area.cpp, 9.0.8), so an area with its directive in the middle carries no class."""
    p = _classed(tmp_path, area=_ABOVE, flag=(101.6, 91.44))
    plan = _plan(p, [("R1", "1")], net="SIG")
    assert not plan.refused and plan.wires, plan.lines


def test_a_directive_on_the_pins_net_is_a_carrier(tmp_path):
    from routing_fixtures import netclass_flag

    p = _classed(tmp_path)
    wire(p, 101.6, 97.79, 88.9, 97.79)
    netclass_flag(p, "HV", 88.9, 97.79)
    assert set(_plan(p, [("R1", "1")], net="SIG").codes) == {"netclass"}


def test_a_netclass_field_on_any_label_is_a_carrier(tmp_path):
    """H-C2: a Netclass field counts on every label kind, not only on a directive. Label A sits
    0.03 mm past R1:1's wire end, so only the possible view reaches it: R1:1 might be HV already
    and A certainly is, which H-C5 refuses. That is the check's conservative edge, as
    specified."""
    from routing_fixtures import splice

    p = _classed(tmp_path)
    wire(p, 101.6, 97.79, 88.9, 97.79)
    splice(
        p,
        '(label "A" (at 88.87 97.79 0) (effects (font (size 1.27 1.27))) (uuid "x")'
        ' (property "Netclass" "HV" (at 88.87 97.79 0) (effects (font (size 1.27 1.27)))))',
    )
    assert set(_plan(p, [("R1", "1")], net="A").codes) == {"netclass"}


# ---------------------------------------------------------------------------------------------
# The byte pre-filter: which sheets a reference check must parse
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        b'(reference "R1")',
        b"(reference R1)",
        b'(reference"R1")',
        b'( reference\t"R1" )',
        b'("reference" "R1")',
        b'\r\n\t\t\t\t(reference "R1")\r\n',
        b'(property "Reference" "R1" (at 0 0 0))',
        b"(property Reference R1(at 0 0 0))",
        b'(property\r\n"Reference"\r\n"R1"',
    ],
)
def test_the_reference_pattern_matches_every_spelling(text):
    """Quoted, bare, re-spaced and glued: every way _cst.TOKEN reads (reference R1) or
    (property Reference R1)."""
    pattern = C.refs_pattern({"R1"})
    assert pattern is not None and pattern.search(text)


@pytest.mark.parametrize(
    "text",
    [
        b'(reference "R10")',
        b'(reference "XR1")',
        b"(reference R1A)",
        b'(name "R1")',
        b'(property "Value" "R1")',
        b'(reference_x "R1")',
    ],
)
def test_the_reference_pattern_matches_only_that_reference(text):
    pattern = C.refs_pattern({"R1"})
    assert pattern is not None and not pattern.search(text)


@pytest.mark.parametrize("ref", ['R"1', "R\\1", "R\t1", "", "R\n1"])
def test_a_reference_the_file_would_escape_reads_every_sheet(ref):
    assert C.refs_pattern({ref, "R2"}) is None


def test_the_structure_pattern_finds_sheets_and_tables_only():
    for text in (b"(sheet (at 1 2)", b"(sheet\r\n\t(at", b'("sheet"(at', b"(symbol_instances"):
        assert C._STRUCTURE.search(text), text
    for text in (b"(sheet_instances (path", b"(sheetfile x)", b'(property "Sheetname" "x")'):
        assert not C._STRUCTURE.search(text), text


def test_a_sheet_without_the_reference_is_not_parsed(tmp_path):
    """The child holds R2 and no sheet block, so R1's check leaves it unparsed; once its symbol
    carries R1 it is read, and the duplicate is found."""
    from routing_fixtures import rename_ref

    root, child = _two_sheets(tmp_path)
    m = C.Model(_root(root), root)
    (other,) = m.hier.others
    assert other.facts is None and other.data is not None
    m.check_unique("R1")
    assert other.facts is None
    rename_ref(child, "R2", "R1")
    m = C.Model(_root(root), root)
    with pytest.raises(C.Refusal):
        m.check_unique("R1")
    assert m.hier.others[0].facts is not None


def test_without_a_project_every_reference_a_part_could_carry_counts(tmp_path):
    """No project file, so the live path is unknown. R2 carries a first entry from elsewhere
    naming it R1, and R1 as its property, so it may be R1 as well as R2: both references are
    refused as shared ([dup_ref]), the conservative rule, where matching by the property alone
    had answered "R2" with not found."""
    from routing_fixtures import stale_reference

    p = _r1(tmp_path)
    place(p, "R", "R2", 152.4, 101.6)
    stale_reference(p, "R2", "R1")
    assert _plan(p, [("R2", "1")]).codes == ["dup_ref"]
    assert _plan(p, [("R1", "1")]).codes == ["dup_ref"]


def test_a_sheet_pin_of_the_same_name_is_not_said_to_join(tmp_path):
    """The note said KiCad joins a label to a same-named sheet pin "when something on this
    sheet already carries" the name; kicad-cli 9.0.8 joined it neither without nor with such a
    label (found by review). It now only warns that the name does not reliably reach the port."""
    from routing_fixtures import project_file, sheet

    from mcp_server_kicad import project

    p = _r1(tmp_path)
    child = str(Path(p).parent / "c.kicad_sch")
    project.create_schematic(schematic_path=child)
    project_file(Path(p).parent, Path(p).stem)
    sheet(p, child, "C", 152.4, 25.4, pins=[("N", "input", "left", 2)])
    text = _plan(p, [("R1", "1")]).success()
    assert "sheet pin 'N' of sheet 'C'" in text
    assert "does not reliably connect to it" in text and "already carries" not in text
