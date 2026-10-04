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
    pytest.param({"stub_length": float("nan")}, id="stub_nan"),
    pytest.param({"stub_length": True}, id="stub_bool"),
    pytest.param({"stub_length": "2.54"}, id="stub_text"),
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


def _hide_lib_pin(path, number: str, alternate: str | None = None) -> None:
    """Hide lib pin *number* of the 4011 in this file's lib_symbols, optionally adding an
    alternate of type power_in and selecting it on U1."""
    data = Path(path).read_bytes()
    tree = _cst.parse(data)
    root = tree.lists[0]
    lib = next(s for s in root.find("lib_symbols").find_all("symbol") if s.atoms[1].text == "4011")
    for sub in lib.find_all("symbol"):
        for pin in sub.find_all("pin"):
            if pin.find("number").atoms[1].text == number:
                pin.insert_after(pin.find("length"), _cst.parse(b"(hide yes)").lists[0], b" ")
                if alternate:
                    alt = f'(alternate "{alternate}" power_in line)'.encode()
                    pin.append_child(_cst.parse(alt).lists[0], b" ")
    if alternate:
        for u1 in root.find_all("symbol"):
            if not any(
                q.atoms[2].text == "U1" for q in u1.find_all("property") if len(q.atoms) > 2
            ):
                continue
            placed = next(q for q in u1.find_all("pin") if q.atoms[1].text == number)
            placed.append_child(_cst.parse(f'(alternate "{alternate}")'.encode()).lists[0], b" ")
    Path(path).write_bytes(_cst.serialize(tree))


def test_a_hidden_power_in_pin_names_its_net(tmp_path):
    p = _4011(tmp_path)
    _hide_lib_pin(p, "14")
    assert _plan(p, [("U1", "14")], net="RAIL").codes == ["names"]
    noop = _plan(p, [("U1", "14")], net="Vdd")
    assert not noop.refused and not noop.labels


def test_an_alternate_makes_the_name_possible_only(tmp_path):
    """With a placed alternate it is not established which name KiCad uses, so the narrow view
    takes neither (no no-op) and the possible view takes both (Vdd and VBAT conflict)."""
    p = _4011(tmp_path)
    _hide_lib_pin(p, "14", alternate="VBAT")
    plan = _plan(p, [("U1", "14")], net="Vdd")
    assert plan.codes == ["names"] and "'VBAT'" in plan.refusal()


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


def test_an_unloadable_symbol_is_reported_before_an_unresolved_one(tmp_path):
    """Guard on the order of the whole-call checks (the [derived] check is new, so this passed
    before it)."""
    from routing_fixtures import _placed

    root = _unresolved(tmp_path)  # the bad angle goes into memory only, like the lib_name
    _placed(root, "R1")[0].find("at").atoms[3].set_text("45")
    plan = C.plan_wire_pins(root, [{"reference": "R1", "pin": "1"}], "N", "auto", 2.54)
    assert plan.codes == ["unloadable"]
