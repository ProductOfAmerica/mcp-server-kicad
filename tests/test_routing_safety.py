"""wire_pins_to_net judged by KiCad: the probe cases of the routing pressure test.

Each case comes from the measurement harness behind docs/adr-routing-safety.md and keeps its
case ID. A call is held to the write checks of routing_checks.call_wptn (one write that only
adds wires and labels, byte identity on a refusal or a no-op, no junction, overlap or crossing)
and, where kicad-cli is installed, to the netlist judge of netlist_oracle: no net merges,
splits or is renamed, and the requested pad lands on the requested net. Wherever a fixture's
netlist is exported anyway, the model's two views are also checked against it
(netlist_oracle.model_disagreements), before and after the call.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import netlist_nodes, requires_cli
from netlist_oracle import judge, model_disagreements, nets
from routing_checks import assert_only_added, call_wptn
from routing_fixtures import (
    ORIENTED,
    bus,
    fresh,
    label,
    place,
    power,
    project_file,
    rename_ref,
    set_lib_name,
    set_unit,
    sheet,
    stale_reference,
    stub,
    wire,
)

from mcp_server_kicad import _connectivity, _cst, project, schematic


def pins(*specs):
    return [{"reference": r, "pin": p} for r, p in specs]


def agrees(path, netlist=None) -> list:
    """Assert the model's views agree with kicad-cli on *path*; returns the netlist used."""
    netlist = nets(path) if netlist is None else netlist
    found = model_disagreements(path, netlist)
    assert found in (None, ([], [])), f"model and kicad-cli disagree: {found}"
    return netlist


def wired(path, specs, n, judge_on=None, oracle=True, **kw):
    """call_wptn plus, with *oracle*, the netlist judge of a write, on *judge_on* (the root of a
    hierarchy) when given. Returns (status, msg).

    A refusal or a no-op leaves the file byte-identical (call_wptn asserts it), so only a write
    has a change to judge; judging an unchanged file measures the judge, which maps a pad listed
    in two nets (a shared reference, say) to the first of them. A no-op is still held to its
    claim that kicad-cli has the pad on N already.
    """
    on = judge_on or path
    if oracle:
        agrees(path)
    before = nets(on) if oracle else None
    status, msg = call_wptn(path, pins(*specs), n, **kw)
    if before is not None and status != "REFUSED":
        agrees(path)
        verdict = judge(before, nets(on), [set(specs)], n, wrote=status == "OK")
        if status == "OK":
            assert not verdict.wrong, (msg, verdict.problems())
        assert verdict.delivered, msg
    return status, msg


#: A test so marked runs twice: "model" makes its checks without kicad-cli, and "kicad" makes
#: them with kicad-cli's netlist judging too, and skips where kicad-cli is not installed, so a
#: KiCad-free run reports the netlist half as skipped instead of passing without it.
ORACLE = pytest.mark.parametrize(
    "oracle", [pytest.param(False, id="model"), pytest.param(True, id="kicad", marks=requires_cli)]
)


# ---------------------------------------------------------------------------------------------
# PI-01, PI-12: every pin of a resistor, a diode and a transistor in all 12 orientations
# ---------------------------------------------------------------------------------------------

_ROW = {"R": ("R", 50.8), "D": ("D", 101.6), "Q_NPN_BCE": ("Q", 152.4)}


def _xy(node, which: int) -> tuple[float, float]:
    xy = node.find("pts").find_all("xy")[which]
    return round(float(xy.atoms[1].text), 4), round(float(xy.atoms[2].text), 4)


@ORACLE
def test_twelve_orientations_wire_the_pin_kicad_draws(tmp_path, oracle):
    """PI-01/PI-12. Every pin gets a stub that starts on the pin KiCad draws and points away
    from the body. The repo used to mirror before rotating, which put the stub on the other pin
    of R and D, and on empty space for Q, at rotation 90 or 270 with a mirror; and it pointed the
    stub into the body at rotation 90 or 270 without one.
    """
    p = fresh(tmp_path)
    calls = []
    for symbol, table in ORIENTED.items():
        prefix, y = _ROW[symbol]
        for i, ((rot, mir), ends) in enumerate(table.items()):
            ref, x = f"{prefix}{i + 1}", 25.4 + 20.32 * i
            place(p, symbol, ref, x, y, rot=rot, mirror=mir)
            calls += [(ref, str(k + 1), x + dx, y + dy, d) for k, (dx, dy, d) in enumerate(ends)]
    before = agrees(p) if oracle else None
    wrong = []
    for ref, num, ex, ey, d in calls:
        old = open(p, "rb").read()
        status, msg = call_wptn(p, pins((ref, num)), f"N_{ref}_{num}")
        added = assert_only_added(old, open(p, "rb").read())
        stubs = [n for n in added if n.head == "wire"]
        if status != "OK" or len(stubs) != 1:
            wrong.append((ref, num, status, msg))
            continue
        (sx, sy), (tx, ty) = _xy(stubs[0], 0), _xy(stubs[0], 1)
        got = ((tx > sx) - (tx < sx), (ty > sy) - (ty < sy))
        if (sx, sy) != (round(ex, 4), round(ey, 4)) or got != d:
            wrong.append((ref, num, (sx, sy), got, (round(ex, 4), round(ey, 4)), d))
    assert wrong == [], wrong
    if before is not None:
        after = agrees(p)
        on = {node: (name, frozenset(nodes)) for _c, name, _k, nodes in after for node in nodes}
        for ref, num, *_ in calls:
            name, nodes = on[(ref, num)]
            assert (name.lstrip("/"), nodes) == (f"N_{ref}_{num}", {(ref, num)}), (ref, num)


# ---------------------------------------------------------------------------------------------
# S1-S7: shorts the old fallbacks wrote, now decided by the touch rule
# ---------------------------------------------------------------------------------------------


@requires_cli
def test_s1_every_stub_end_holds_another_nets_label(tmp_path):
    """S1. Today's code warned "no safe direction found" and wrote the stub anyway."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 152.4, 101.6)
    stub(p, 152.4, 97.79, 0, -2.54, "OTHER")
    x, y = 101.6, 97.79
    for ex, ey in ((x, y - 2.54), (x + 2.54, y), (x - 2.54, y), (x, y + 2.54)):
        label(p, "OTHER", ex, ey)
    status, msg = wired(p, [("R1", "1")], "N1")
    assert status == "OK" and "on the pin end" in msg


@requires_cli
def test_s2_a_global_label_at_the_stub_end(tmp_path):
    """S2. The old collision check looked at local labels only."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 152.4, 101.6)
    wire(p, 152.4, 97.79, 152.4, 95.25)
    label(p, "GNET", 152.4, 95.25, kind="global_label")
    label(p, "GNET", 101.6, 95.25, kind="global_label")
    status, _ = wired(p, [("R1", "1")], "N2")
    assert status == "OK"


@requires_cli
def test_s3_a_power_symbol_at_the_stub_end(tmp_path):
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 152.4, 101.6)
    wire(p, 152.4, 97.79, 152.4, 95.25)
    power(p, "GND", "#PWR01", 152.4, 95.25)
    power(p, "GND", "#PWR02", 101.6, 95.25)
    status, _ = wired(p, [("R1", "1")], "N3")
    assert status == "OK"


@requires_cli
def test_s4_another_nets_wire_through_the_stub_end(tmp_path):
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 88.9, 99.06)
    wire(p, 88.9, 95.25, 114.3, 95.25)
    status, _ = wired(p, [("R1", "1")], "N4")
    assert status == "OK"


@requires_cli
def test_s5_another_parts_pin_at_the_stub_end(tmp_path):
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R3", 101.6, 91.44)
    status, _ = wired(p, [("R1", "1")], "N5")
    assert status == "OK"


@requires_cli
def test_s6_the_fallback_direction_lands_on_the_neighbouring_pin(tmp_path):
    """S6. With left blocked, the old fallback ran J1:2's stub up onto J1:1, 2.54 mm away."""
    p = fresh(tmp_path)
    place(p, "Conn_01x04", "J1", 101.6, 101.6)
    stub(p, 96.52, 99.06, -2.54, 0, "P1")
    label(p, "BLK", 93.98, 101.6)
    label(p, "BLK", 99.06, 101.6)
    status, _ = wired(p, [("J1", "2")], "N6")
    assert status == "OK"


@requires_cli
def test_s7_a_label_on_the_fallback_path(tmp_path):
    """S7. R1 at rotation 90 points pin 1 left; the old code thought right, fell back to up,
    and checked that path along the wrong axis, so it ran the stub through OTHER7."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6, rot=90)
    place(p, "R", "R2", 152.4, 101.6)
    stub(p, 152.4, 97.79, 0, -2.54, "OTHER7")
    label(p, "BLK", 95.25, 101.6)
    label(p, "BLK", 100.33, 101.6)
    label(p, "OTHER7", 97.79, 100.33)
    status, _ = wired(p, [("R1", "1")], "N7")
    assert status == "OK"


def test_two_pins_of_one_call_do_not_stack_their_stubs(tmp_path):
    """H-C15: what a call adds joins the model before the next pin is wired. R1 and R2 sit on
    top of each other, so both pin 1 stubs would run up the same 2.54 mm."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 101.6, 101.6)
    status, msg = call_wptn(p, pins(("R1", "1"), ("R2", "1")), "N")
    assert status == "OK", msg


# ---------------------------------------------------------------------------------------------
# Crossing ban (crossban_probe Q0-Q5)
# ---------------------------------------------------------------------------------------------

X = (101.6, 96.52)


def _crossing_fixture(tmp_path):
    """R1:1 at (101.6, 97.79) would stub up through net A's wire W along y = 96.52."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 76.2, 101.6)
    place(p, "R", "R3", 139.7, 101.6)
    stub(p, 139.7, 97.79, 0, -2.54, "N")
    wire(p, 76.2, 97.79, 76.2, 96.52)
    wire(p, 76.2, 96.52, 127, 96.52)
    label(p, "A", 127, 96.52)
    return p


@requires_cli
@pytest.mark.parametrize(
    "follow_up",
    [
        pytest.param(lambda p: schematic.add_label("A", *X, schematic_path=p), id="add_label"),
        pytest.param(lambda p: schematic.add_label("B", *X, schematic_path=p), id="label_B"),
        pytest.param(
            lambda p: schematic.add_global_label("A", *X, schematic_path=p), id="global_label"
        ),
        pytest.param(
            lambda p: schematic.add_junctions([{"x": X[0], "y": X[1]}], schematic_path=p),
            id="add_junctions",
        ),
        pytest.param(
            lambda p: schematic.add_wires(
                [{"x1": X[0], "y1": X[1], "x2": 114.3, "y2": 96.52}], schematic_path=p
            ),
            id="add_wires",
        ),
    ],
)
def test_a_later_tool_at_the_crossing_merges_nothing(tmp_path, follow_up):
    """Q1-Q5. A stub crossing net A's wire joins nothing when written, but a label, junction or
    auto-junctioned wire placed on the crossing by a later call merges /A and /N. With the
    crossing ban the stub is replaced by a label on the pin, so there is no crossing to land on.
    (add_junctions and add_wires truncate W in kicad-cli 9 whatever this tool did, which renames
    R2's net; the assertion is about merging A and N, not about those tools.)
    """
    p = _crossing_fixture(tmp_path)
    status, msg = wired(p, [("R1", "1")], "N")
    assert status == "OK" and "on the pin end" in msg
    before = nets(p)
    follow_up(p)
    v = judge(before, nets(p), [], None)
    assert v.bad_merges == [] and v.named_merge == [], v.problems()


# ---------------------------------------------------------------------------------------------
# Every other pin read uses the same transform (PI-01 for the read and flag tools)
# ---------------------------------------------------------------------------------------------


def _orientation_sheet(tmp_path):
    """All 36 placements of the sweep on one sheet; returns (path, {(ref, pin): (x, y)})."""
    p = fresh(tmp_path)
    want = {}
    for symbol, table in ORIENTED.items():
        prefix, y = _ROW[symbol]
        for i, ((rot, mir), ends) in enumerate(table.items()):
            ref, x = f"{prefix}{i + 1}", 25.4 + 20.32 * i
            place(p, symbol, ref, x, y, rot=rot, mirror=mir)
            for k, (dx, dy, _d) in enumerate(ends):
                want[(ref, str(k + 1))] = (round(x + dx, 2), round(y + dy, 2))
    return p, want


def _reported(path, refs) -> dict:
    """{(ref, pin): (x, y)} as get_pin_positions prints them."""
    got = {}
    for ref in refs:
        for line in schematic.get_pin_positions(ref, schematic_path=path).splitlines()[1:]:
            num = line.split()[1]
            x, y = line.rsplit("(", 1)[1].rstrip(")").split(", ")
            got[(ref, num)] = (float(x), float(y))
    return got


def test_get_pin_positions_reports_where_kicad_draws(tmp_path):
    """get_pin_positions shared the old transform, so it reported the reflected point for every
    rotation 90 or 270 with a mirror, and a caller drawing to it wired the other pin."""
    p, want = _orientation_sheet(tmp_path)
    assert _reported(p, {r for r, _ in want}) == want


@requires_cli
def test_a_label_at_the_reported_point_lands_on_that_pin(tmp_path):
    """The manager's re-check of the transform claim (mgr_verify.py claim 1), every orientation:
    a label dropped where get_pin_positions says pin 1 is must put pin 1 on its net."""
    p, want = _orientation_sheet(tmp_path)
    refs = {r for r, _ in want}
    for (ref, num), (x, y) in _reported(p, refs).items():
        if num == "1":
            schematic.add_label(f"P_{ref}", x, y, schematic_path=p)
    on = {name.lstrip("/"): sorted(nodes) for _c, name, _k, nodes in nets(p)}
    assert {ref: on.get(f"P_{ref}") for ref in refs if on.get(f"P_{ref}") != [(ref, "1")]} == {}


@requires_cli
def test_no_connect_pin_flags_the_pin_kicad_draws(tmp_path):
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6, rot=90, mirror="x")
    assert schematic.no_connect_pin("R1", "1", schematic_path=p).endswith("at (97.79, 101.6)")
    pintypes = {node: t for v in netlist_nodes(p).values() for node, t in v.items()}
    assert pintypes[("R1", "1")].endswith("+no_connect")
    assert not pintypes[("R1", "2")].endswith("+no_connect")


@requires_cli
def test_connect_pins_joins_the_pins_kicad_draws(tmp_path):
    """connect_pins itself is unchanged here, but its pin lookup shared the transform: at
    rotation 90 with mirror y it used to route to R1's other pin."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6, rot=90, mirror="y")
    place(p, "R", "R9", 152.4, 152.4)
    before = nets(p)
    schematic.connect_pins("R1", "1", "R9", "1", schematic_path=p)
    v = judge(before, nets(p), [{("R1", "1"), ("R9", "1")}], None)
    assert v.delivered and not v.wrong, v.problems()


def _renamed_entry(tmp_path) -> str:
    """R1 drawn from an embedded entry R_1, named by its lib_name, whose pins sit 5.08 mm from
    the origin where the stock R entry beside it has them at 3.81 mm: the shape KiCad saves
    when one placed copy of a symbol differs from its library."""
    from routing_fixtures import _edit, set_lib_name

    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R9", 152.4, 152.4)

    def add_r_1(root) -> None:
        libs = root.find("lib_symbols")
        r = next(s for s in libs.find_all("symbol") if s.atoms[1].text == "R")
        entry = r.copy()
        entry.atoms[1].set_text("R_1")
        for sub in entry.find_all("symbol"):
            sub.atoms[1].set_text("R_1" + sub.atoms[1].text[1:])  # R_1_1 -> R_1_1_1
            for pin in sub.find_all("pin"):
                at = pin.find("at")
                at.atoms[2].set_text("5.08" if float(at.atoms[2].text) > 0 else "-5.08")
        libs.insert_after(r, entry)

    _edit(p, add_r_1)
    set_lib_name(p, "R1", "R_1")
    return p


def test_the_read_tools_draw_a_part_from_the_entry_kicad_does(tmp_path):
    """KiCad draws a placed symbol from the lib_symbols entry its lib_name names. The read
    tools matched on lib_id alone, so they read R1's pins from the stock R entry, 1.27 mm off
    the R_1 pins that wire_pins_to_net wires."""
    p = _renamed_entry(tmp_path)
    m = _connectivity.Model(_cst.parse(Path(p).read_bytes()).lists[0])
    model = {
        (it.ref, it.num): (round(it.x / 1e4, 2), round(it.y / 1e4, 2))
        for it in m.items
        if it.kind == "pin" and it.ref == "R1"
    }
    assert model == {("R1", "1"): (101.6, 96.52), ("R1", "2"): (101.6, 106.68)}
    assert _reported(p, {"R1"}) == model


@requires_cli
def test_the_read_tools_draw_a_part_named_twice_from_the_last_entry(tmp_path):
    """Two lib_symbols entries named "R", pins swapped in the second: KiCad 9.0.8 and 10.0.6
    keep the last (SCH_SCREEN::AddLibSymbol), so R1's pin 1 is where the first entry draws pin
    2. The read tools took the first entry. A label where get_pin_positions puts R1's pin 1
    must put R1:1 on that label's net."""
    from routing_fixtures import duplicate_r_swapped

    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    duplicate_r_swapped(p)
    (x, y) = _reported(p, {"R1"})[("R1", "1")]
    assert (x, y) == (101.6, 105.41)
    schematic.add_label("P1", x, y, schematic_path=p)
    on = {name.lstrip("/"): sorted(nodes) for _c, name, _k, nodes in nets(p)}
    assert on.get("P1") == [("R1", "1")], on


@requires_cli
def test_connect_pins_reaches_a_pin_of_a_renamed_entry(tmp_path):
    p = _renamed_entry(tmp_path)
    before = nets(p)
    schematic.connect_pins("R1", "1", "R9", "1", schematic_path=p)
    v = judge(before, nets(p), [{("R1", "1"), ("R9", "1")}], None)
    assert v.delivered and not v.wrong, v.problems()


def test_get_net_connections_names_the_pin_kicad_draws(tmp_path):
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6, rot=270, mirror="x")
    status, _ = call_wptn(p, pins(("R1", "1")), "GN")
    assert status == "OK"
    found = schematic.get_net_connections("GN", schematic_path=p)
    assert [(c["reference"], c["pin"]) for c in found.connections] == [("R1", "1")]


# ---------------------------------------------------------------------------------------------
# Named nets and no-ops (design 4.4, decisions 1 and 2)
# ---------------------------------------------------------------------------------------------


@requires_cli
def test_s8_a_pin_already_on_a_named_net(tmp_path):
    """S8. The old code put NET_B on the pin beside NET_A's stub, joining the two."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    stub(p, 101.6, 97.79, 0, -2.54, "NET_A")
    status, msg = wired(p, [("R1", "1")], "NET_B")
    assert status == "REFUSED" and msg.startswith("[names]") and "'NET_A'" in msg


@requires_cli
def test_s9_coincident_pins_to_two_names(tmp_path):
    """S9. R1:2 and R2:1 share a point, so they are one net; the second name is refused."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 101.6, 109.22)  # R2:1 at (101.6, 105.41), on R1:2
    stub(p, 101.6, 105.41, 2.54, 0, "NET_A")
    status, msg = wired(p, [("R2", "1")], "NET_B")
    assert status == "REFUSED" and msg.startswith("[names]")


def test_dec23_a_power_symbol_pin_to_its_own_name_is_a_no_op(tmp_path):
    """DEC-23. A VCC power symbol's pin is on VCC by its Value: nothing to write."""
    p = fresh(tmp_path)
    power(p, "VCC", "#PWR02", 76.2, 38.1)
    status, msg = call_wptn(p, pins(("#PWR02", "1")), "VCC")
    assert status == "NOOP", msg
    assert "already on 'VCC' via power symbol #PWR02 (Value 'VCC')" in msg


@requires_cli
def test_a_slash_escaped_name_is_the_net_kicad_reads(tmp_path):
    """KiCad stores a typed 'A/B' as 'A{slash}B' and reads that and a raw 'A/B' as one net (the
    routing review's e4_slash). R1:1 is on it already; R2:1, wired to 'A/B', joins it."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 152.4, 101.6)
    stub(p, 101.6, 97.79, 0, -2.54, "A{slash}B")
    assert wired(p, [("R1", "1")], "A/B")[0] == "NOOP"
    assert wired(p, [("R2", "1")], "A/B")[0] == "OK"


def test_a_pin_already_on_the_net_is_a_no_op(tmp_path):
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    stub(p, 101.6, 97.79, 0, -2.54, "N")
    status, msg = call_wptn(p, pins(("R1", "1")), "N")
    assert status == "NOOP" and "already on 'N' via label 'N' at (101.6, 95.25)" in msg


@requires_cli
def test_dec15_an_alternate_sets_a_pins_type_and_never_its_name(tmp_path):
    """DEC-15 (d04_power_pins_as_targets f to i). KiCad takes a pin's type from the alternate
    placed on it and names a hidden power_in pin's net by the library pin's own name, never the
    alternate's (sch_pin.cpp IsGlobalPower and GetDefaultNetName, 9.0.8 and 10.0.6). U3:2 is
    hidden power_in 'VCC' with a passive alternate placed, so it names nothing; U4:2 is hidden
    passive 'VCC' with the power_in alternate 'PWRALT' placed, so it sits on VCC. The model used
    to count both names as possible and neither as certain, and refused all three calls."""
    from routing_fixtures import custom_lib, place_custom, select_alternate

    p = fresh(tmp_path)
    for name, ref, x, typ, alt in (
        ("HALT", "U3", 101.6, "power_in", ("ALTP", "passive")),
        ("HALT2", "U4", 152.4, "passive", ("PWRALT", "power_in")),
    ):
        lib = custom_lib(
            tmp_path,
            name,
            [("1", "A", "passive", -5.08, 0, 0, False), ("2", "VCC", typ, 5.08, 0, 180, True)],
            alternates={"2": [alt]},
        )
        place_custom(p, lib, name, ref, x, 101.6)
        select_alternate(p, ref, "2", alt[0])
    assert wired(p, [("U4", "2")], "VCC")[0] == "NOOP"
    status, msg = wired(p, [("U4", "2")], "PWRALT")
    assert status == "REFUSED" and msg.startswith("[names]") and "'VCC'" in msg, msg
    assert wired(p, [("U3", "2")], "SIG")[0] == "OK"


# ---------------------------------------------------------------------------------------------
# Pad identity and pin resolution (design 4.5, 4.8)
# ---------------------------------------------------------------------------------------------


@requires_cli
def test_every_copy_of_a_unit0_pad_is_wired(tmp_path):
    """UP-01. DualGate's pin 5 (GND) is common to both units, so each placed unit draws a copy.
    The old lookup wired the first copy only, and KiCad then listed pad 5 in two nets."""
    from conftest import make_dual_unit_sch

    p = make_dual_unit_sch(tmp_path)
    status, msg = wired(p, [("U1", "5")], "NGND")
    assert status == "OK" and msg.count("copy at") == 2, msg


def test_a_pad_with_one_copy_on_n_gets_only_the_other(tmp_path):
    from conftest import make_dual_unit_sch

    p = make_dual_unit_sch(tmp_path)
    stub(p, 100, 107.62, 0, 2.54, "NGND")  # unit 1's copy of pad 5, already on NGND
    old = open(p, "rb").read()
    status, msg = call_wptn(p, pins(("U1", "5")), "NGND")
    assert status == "OK", msg
    wires = [n for n in assert_only_added(old, open(p, "rb").read()) if n.head == "wire"]
    assert len(wires) == 1 and _xy(wires[0], 0) == (150.0, 107.62)
    assert "copy at (100, 107.62): already on 'NGND'" in msg


def test_a_pad_on_n_in_every_copy_is_a_no_op(tmp_path):
    from conftest import make_dual_unit_sch

    p = make_dual_unit_sch(tmp_path)
    stub(p, 100, 107.62, 0, 2.54, "NGND")
    stub(p, 150, 107.62, 0, 2.54, "NGND")
    status, msg = call_wptn(p, pins(("U1", "5")), "NGND")
    assert status == "NOOP", msg


def test_an_exact_pad_number_wins_over_a_pin_name(tmp_path):
    """PI-14. Pin 1 is named "2" and pin 2 is named "1"; "2" means pad 2. The old lookup took
    the first pin whose name or number matched, in file order, which was pad 1."""
    from routing_fixtures import custom_lib, place_custom

    p = fresh(tmp_path)
    lib = custom_lib(
        tmp_path,
        "SWAP",
        [("1", "2", "passive", -5.08, 0, 0, False), ("2", "1", "passive", 5.08, 0, 180, False)],
    )
    place_custom(p, lib, "SWAP", "U1", 101.6, 101.6)
    old = open(p, "rb").read()
    status, msg = call_wptn(p, pins(("U1", "2")), "N")
    assert status == "OK", msg
    wires = [n for n in assert_only_added(old, open(p, "rb").read()) if n.head == "wire"]
    assert _xy(wires[0], 0) == (106.68, 101.6)  # pad 2, on the right


def _stack(tmp_path, apart: bool):
    from routing_fixtures import custom_lib, place_custom

    p = fresh(tmp_path)
    second = (5.08, 0, 180) if apart else (-5.08, 0, 0)
    lib = custom_lib(
        tmp_path,
        "STACK",
        [
            ("3", "COM", "passive", -5.08, 0, 0, False),
            ("5", "COM", "passive", *second, True),
            ("1", "IN", "passive", 0, 5.08, 270, False),
        ],
    )
    place_custom(p, lib, "STACK", "U1", 101.6, 101.6)
    return p


@ORACLE
def test_a_name_on_stacked_pads_is_one_target(tmp_path, oracle):
    """Pads 3 and 5 are both "COM", drawn at one point: one stub wires both."""
    p = _stack(tmp_path, apart=False)
    before = nets(p) if oracle else None
    status, msg = call_wptn(p, pins(("U1", "COM")), "N")
    assert status == "OK" and "(pad 3)" in msg and "(pad 5)" in msg, msg
    if before is not None:
        v = judge(before, nets(p), [{("U1", "3"), ("U1", "5")}], "N")
        assert v.delivered and not v.wrong, v.problems()


def test_a_name_on_pads_at_two_points_is_refused(tmp_path):
    """PI-14/LW-04. "COM" names pads 3 and 5 at different points: which was meant is unknown,
    so the call refuses and lists them. The old lookup wired the first one only."""
    p = _stack(tmp_path, apart=True)
    status, msg = call_wptn(p, pins(("U1", "COM")), "N")
    assert status == "REFUSED" and msg.startswith("[resolve]"), msg
    assert "pad 3 at (96.52, 101.6)" in msg and "pad 5 at (106.68, 101.6)" in msg


@ORACLE
def test_a_pad_drawn_under_two_names_is_found_by_either(tmp_path, oracle):
    """The routing pressure test's X1c (x_duplicate_numbers): pad 1 is drawn twice in one unit,
    named A and A2, as KiCad's own 74278 draws pad 6 as Y4 and ~{P1}. Asked for by its second
    name, the pad was refused as not drawn here, because resolution kept only the first name
    of each pad number. Both copies of pad 1 are wired."""
    from routing_fixtures import custom_lib, place_custom

    p = fresh(tmp_path)
    lib = custom_lib(
        tmp_path,
        "DUP",
        [
            ("1", "A", "passive", -5.08, 0, 0, False),
            ("1", "A2", "passive", -5.08, -2.54, 0, False),
            ("2", "B", "passive", 5.08, 0, 180, False),
        ],
    )
    place_custom(p, lib, "DUP", "U1", 101.6, 101.6)
    before = nets(p) if oracle else None
    status, msg = call_wptn(p, pins(("U1", "A2")), "N")
    assert status == "OK", msg
    assert "(96.52, 101.6)" in msg and "(96.52, 104.14)" in msg, msg
    if before is not None:
        v = judge(before, nets(p), [{("U1", "1")}], "N")
        assert v.delivered and not v.wrong, v.problems()


# ---------------------------------------------------------------------------------------------
# Outright refusals (design 4.3): nets this tool cannot judge
# ---------------------------------------------------------------------------------------------


def _on(path) -> dict:
    """{node: (printed name, nodes)} in the kicad-cli netlist of *path*, after checking the
    model against it."""
    return {node: (name, nodes) for _c, name, _k, nodes in agrees(path) for node in nodes}


def _hierarchy(tmp_path, name: str, child: str = "c") -> tuple[str, str]:
    """A root sheet with a project file beside it and an empty child sheet."""
    root = fresh(tmp_path, name)
    kid = str(tmp_path / name / f"{child}.kicad_sch")
    project.create_schematic(schematic_path=kid)
    project_file(tmp_path / name, name)
    return root, kid


def _hier06(tmp_path, geom: str) -> str:
    """HIER-06 (h04_sheetpin_on_wire_interior): R1:1's wire passes over sheet pin PWR without
    ending on it, and the child ties PWR to +5V. R2:1 is on N1 and R3:1 on GND."""
    root, child = _hierarchy(tmp_path, "h")
    place(child, "R", "R31", 50.8, 50.8)  # R31:1 at (50.8, 46.99)
    wire(child, 50.8, 46.99, 50.8, 41.91)
    wire(child, 50.8, 41.91, 50.8, 38.1)
    label(child, "PWR", 50.8, 41.91, kind="hierarchical_label")
    power(child, "+5V", "#PWR01", 50.8, 38.1)
    pin = sheet(root, child, "C", 152.4, 25.4, pins=[("PWR", "input", "left", 2)])
    assert pin == {"PWR": (152.4, 30.48)}
    place(root, "R", "R1", 127, 40.64)  # R1:1 at (127, 36.83)
    if geom == "edge":  # along the sheet's left edge, over the pin
        wire(root, 127, 36.83, 152.4, 36.83)
        wire(root, 152.4, 36.83, 152.4, 27.94)
    else:  # through the pin into the sheet body
        wire(root, 127, 36.83, 139.7, 36.83)
        wire(root, 139.7, 36.83, 139.7, 30.48)
        wire(root, 139.7, 30.48, 165.1, 30.48)
    place(root, "R", "R2", 76.2, 50.8)
    stub(root, 76.2, 46.99, 0, -5.08, "N1")
    place(root, "R", "R3", 101.6, 50.8)
    wire(root, 101.6, 46.99, 101.6, 41.91)
    power(root, "GND", "#PWR02", 101.6, 41.91)
    return root


@ORACLE
@pytest.mark.parametrize("net", ["GND", "N1"])
@pytest.mark.parametrize("geom", ["edge", "into"])
def test_hier06_a_net_reaching_a_sheet_pin_is_refused(tmp_path, geom, net, oracle):
    """HIER-06. kicad-cli attaches a sheet pin to a wire that merely passes over it, so R1:1 is
    on the child's +5V. Wiring it to GND shorted +5V into GND, and to N1 joined N1 to +5V. The
    net is named across the hierarchy, which this tool does not follow, so it refuses."""
    root = _hier06(tmp_path, geom)
    if oracle:
        assert _on(root)[("R1", "1")][1] >= {("R1", "1"), ("R31", "1")}  # the premise
    status, msg = call_wptn(root, pins(("R1", "1")), net)
    assert status == "REFUSED" and msg.startswith("[sheet_pin] "), msg
    assert "sheet pin 'PWR' of sheet 'C' at (152.4, 30.48)" in msg


def _pi17(tmp_path) -> str:
    """PI-17 (b_m_orphan): D3's lib_name names no lib_symbols entry, and its origin is where
    R1:1's 2.54 mm stub would end."""
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "D", "D3", 101.6, 95.25)
    set_lib_name(p, "D3", "D_9")
    return p


@ORACLE
@pytest.mark.no_kicad_validation
def test_pi17_an_unresolved_symbol_refuses_the_whole_call(tmp_path, oracle):
    """PI-17. D3's lib_name names no lib_symbols entry, so KiCad stacks both its pins at its
    origin, which is where R1:1's stub ends; the old code knew nothing of D3's pins and joined
    R1:1 to them. Pins nobody can place cannot be judged, so the whole call refuses.

    The unresolved symbol is the subject, and kicad-cli 9.0.8's ERC, which the output oracle
    runs, crashes on it (exit 0xC0000005, measured); its netlist export loads it, so the
    premise below is still checked by KiCad.
    """
    p = _pi17(tmp_path)
    if oracle:
        assert _on(p)[("D3", "1")][1] == {("D3", "1"), ("D3", "2")}  # the premise
    status, msg = call_wptn(p, pins(("R1", "1")), "N")
    assert status == "REFUSED" and msg.startswith("[derived] "), msg
    assert "'D_9'" in msg


def _units_two_sheets(tmp_path) -> tuple[str, str]:
    """HIER-14 (h15_units_across_sheets): one 4011 split across two sheets, unit 1 on the root
    and unit 2 on the child, so each sheet draws its own copy of the unit-0 pad 14 (Vdd). Both
    sheets have a +5V power symbol on a resistor."""
    root, child = _hierarchy(tmp_path, "h")
    place(child, "4011", "U1", 101.6, 101.6, value="4011")
    set_unit(child, "U1", 2)
    place(child, "R", "R31", 50.8, 50.8)
    wire(child, 50.8, 46.99, 50.8, 41.91)
    power(child, "+5V", "#PWR01", 50.8, 41.91)
    sheet(root, child, "C", 203.2, 25.4)
    place(root, "4011", "U1", 101.6, 101.6, value="4011")
    place(root, "R", "R9", 50.8, 50.8)
    wire(root, 50.8, 46.99, 50.8, 41.91)
    power(root, "+5V", "#PWR02", 50.8, 41.91)
    return root, child


@ORACLE
@pytest.mark.parametrize(
    ("where", "net"),
    [("root", "+5V"), ("child", "+5V"), ("root", "VDD")],
    ids=["root_existing_name", "child_existing_name", "root_new_name"],
)
def test_hier14_a_pad_with_a_copy_on_another_sheet_is_refused(tmp_path, where, net, oracle):
    """HIER-14, and UP-05's shape (UP-05 used 74xx_IEEE:7400, whose pads 14 and 7 are unit-0
    pins as the 4011's are). A sheet sees only its own copy of pad 14, so the old code wired
    that copy and kicad-cli then listed the pad in two nets, the wired one and the other copy's.
    The pad has a copy this sheet cannot see, so the call refuses."""
    root, child = _units_two_sheets(tmp_path)
    if oracle:
        assert sum(("U1", "14") in nodes for *_x, nodes in agrees(root)) == 2  # the premise
    status, msg = call_wptn(root if where == "root" else child, pins(("U1", "14")), net)
    assert status == "REFUSED" and msg.startswith("[unit0_unplaced] "), msg


def _stub_on(path: str, ref: str, num: str, text: str) -> None:
    """A raw 2.54 mm stub with a label, outward from pin *num* of *ref*."""
    tree = _cst.parse(open(path, "rb").read())
    (c, *_more) = _connectivity.Model(tree.lists[0]).pads[(ref, num)]
    assert c.out is not None
    stub(path, c.x / 1e4, c.y / 1e4, c.out[0] * 2.54, c.out[1] * 2.54, text)


def _dec14(tmp_path) -> str:
    """DEC-14 (d11_pad_copies_across_sheets): RN1, an R_Network03_Split, unit 1 on the root and
    unit 2 on the child; its pad 1 is common to every unit. The child's copy of pad 1 is on X
    with R12:1, and the root's R5:1 is on N."""
    root, kid = _hierarchy(tmp_path, "hp", "kid")
    place(kid, "R_Network03_Split", "RN1", 101.6, 101.6, value="RN")
    set_unit(kid, "RN1", 2)
    place(kid, "R", "R12", 152.4, 101.6)
    _stub_on(kid, "RN1", "1", "X")
    _stub_on(kid, "R12", "1", "X")
    sheet(root, kid, "kid", 152.4, 50.8)
    place(root, "R_Network03_Split", "RN1", 101.6, 101.6, value="RN")
    place(root, "R", "R5", 177.8, 101.6)
    _stub_on(root, "R5", "1", "N")
    return root


def test_dec14_a_common_pad_split_across_sheets_is_refused(tmp_path):
    """DEC-14. Wiring the root's copy of RN1:1 to N put the pad in two nets, N and the child's
    X. The pad has a copy this sheet cannot see, so the call refuses."""
    root = _dec14(tmp_path)
    status, msg = call_wptn(root, pins(("RN1", "1")), "N")
    assert status == "REFUSED" and msg.startswith("[unit0_unplaced] "), msg


def _bus_p5(tmp_path) -> str:
    """BUS-P5 (s03d): R36:1 is both a bus end and a wire end, the bus carries a plain label
    SIG, and the wire runs to R38:1."""
    p = fresh(tmp_path)
    place(p, "R", "R36", 101.6, 101.6)
    place(p, "R", "R37", 127, 101.6)
    place(p, "R", "R38", 88.9, 101.6)
    bus(p, 101.6, 97.79, 127, 97.79)
    label(p, "SIG", 114.3, 97.79)
    wire(p, 101.6, 97.79, 88.9, 97.79)
    return p


@ORACLE
def test_bus_p5_a_net_reaching_a_bus_is_refused(tmp_path, oracle):
    """BUS-P5. kicad-cli puts R36:1, R37:1 and R38:1 on /SIG; main wired R38:1 to NEWNET and
    renamed /SIG to /NEWNET. Bus members are not modelled, so a net that reaches a bus is
    refused as [bus], before any name it carries is weighed."""
    p = _bus_p5(tmp_path)
    if oracle:
        assert _on(p)[("R38", "1")][0] == "/SIG"  # the premise
    status, msg = call_wptn(p, pins(("R38", "1")), "NEWNET")
    assert status == "REFUSED" and msg.startswith("[bus] "), msg


# ---------------------------------------------------------------------------------------------
# auto_place_decoupling_cap inherits these refusals after it has placed the cap
# ---------------------------------------------------------------------------------------------


def _cap(path, power_net="VCC", ground_net="GND"):
    """C5 = Device:C at (101.6, 101.6): pin 1 at (101.6, 97.79), pin 2 at (101.6, 105.41)."""
    from routing_fixtures import LIB

    return schematic.auto_place_decoupling_cap(
        "Device:C",
        "C5",
        "100nF",
        101.6,
        101.6,
        power_net,
        ground_net,
        symbol_lib_path=LIB,
        schematic_path=path,
    )


def _counts(path) -> dict:
    root = _cst.parse(open(path, "rb").read()).lists[0]
    placed = [
        s
        for s in root.find_all("symbol")
        if any(q.atoms[2].text == "C5" for q in s.find_all("property") if len(q.atoms) > 2)
    ]
    return {
        "C5": len(placed),
        "wire": len(root.find_all("wire")),
        "label": len(root.find_all("label")),
    }


def test_a_refused_pin_1_says_the_cap_is_on_the_sheet(tmp_path):
    """The cap is written before either pin is wired, so a wiring refusal used to arrive as
    "wire_pins_to_net refused the whole call; nothing was written" with the cap on disk."""
    from mcp.server.mcpserver.exceptions import ToolError

    p = fresh(tmp_path)
    wire(p, 95, 97.79, 110, 97.79)  # through C5:1's end: no stub, no label on the pin
    with pytest.raises(ToolError) as e:
        _cap(p)
    msg = str(e.value)
    head = msg.splitlines()[0]
    assert head.startswith("[touch] auto_place_decoupling_cap placed C5, but wiring pin 1"), msg
    assert "nothing was written" not in msg
    assert "remove_component('C5')" in msg and "C5:1:" in msg
    assert _counts(p) == {"C5": 1, "wire": 1, "label": 0}
    schematic.remove_component("C5", schematic_path=p)
    assert _counts(p) == {"C5": 0, "wire": 1, "label": 0}


def test_a_refused_pin_2_says_what_pin_1_got(tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    p = fresh(tmp_path)
    wire(p, 95, 105.41, 110, 105.41)  # through C5:2's end
    with pytest.raises(ToolError) as e:
        _cap(p)
    msg = str(e.value)
    assert msg.startswith(
        "[touch] auto_place_decoupling_cap placed C5 and wired pin 1 to 'VCC', but wiring pin 2"
    ), msg
    assert "nothing was written" not in msg
    undo = (
        "remove_component('C5'), remove_wire(101.6, 97.79, 101.6, 95.25),"
        " remove_label('VCC', 101.6, 95.25)"
    )
    assert undo in msg
    assert _counts(p) == {"C5": 1, "wire": 2, "label": 1}
    schematic.remove_component("C5", schematic_path=p)
    schematic.remove_wire(101.6, 97.79, 101.6, 95.25, schematic_path=p)
    schematic.remove_label("VCC", 101.6, 95.25, schematic_path=p)
    assert _counts(p) == {"C5": 0, "wire": 1, "label": 0}


def test_the_cap_refusal_speaks_in_its_own_parameters_and_says_what_stays(tmp_path):
    """The routing review's repro_undo: the cap's refusal forwarded wire_pins_to_net's remedies,
    "pass another direction" and "pass that name as label_text", which this tool has no
    parameters for, and its undo calls left the copied lib_symbols entry behind (357 bytes
    became 2,385) without saying so."""
    from mcp.server.mcpserver.exceptions import ToolError

    p = fresh(tmp_path)
    wire(p, 95, 105.41, 110, 105.41)  # through C5:2's end: [touch]
    with pytest.raises(ToolError) as e:
        _cap(p)
    msg = str(e.value)
    assert "direction" not in msg, msg
    assert "any library symbol it copied into lib_symbols stays" in msg, msg

    q = fresh(tmp_path, "q")
    label(q, "OTHER", 101.6, 105.41)  # on C5:2's end: [names]
    with pytest.raises(ToolError) as e:
        _cap(q)
    msg = str(e.value)
    assert "pass that name as ground_net" in msg and "label_text" not in msg, msg


def test_the_cap_passes_on_its_wirings_notes(tmp_path):
    """The result ended at "pin 1->VCC | pin 2->GND" and dropped what the wiring reported, so
    it never said that nothing on the sheet carried VCC or GND: on a sub-sheet the cap then sits
    on local nets, not on the rails the root's power symbols name (found by review)."""
    out = _cap(fresh(tmp_path))
    assert "Warning: nothing on this sheet carried 'VCC'" in out
    assert "Warning: nothing on this sheet carried 'GND'" in out


@pytest.mark.parametrize("bad", [{"power_net": " VCC"}, {"ground_net": "Net-(C5-Pad2)"}])
def test_a_bad_net_name_refuses_before_the_cap_is_placed(tmp_path, bad):
    """Validation needs nothing from the sheet, so it runs before the first write."""
    from mcp.server.mcpserver.exceptions import ToolError

    p = fresh(tmp_path)
    before = open(p, "rb").read()
    with pytest.raises(ToolError, match=rf"^\[validation\] {next(iter(bad))} "):
        _cap(p, **bad)
    assert open(p, "rb").read() == before


# ---------------------------------------------------------------------------------------------
# Duplicate references (design 4.6, H-C1)
# ---------------------------------------------------------------------------------------------


def _pi15(tmp_path, symbol: str, dup: str) -> str:
    """PI-15 (m_edges 13, v10_dupref): two parts given one reference, the first's pin 1 tied to
    R7:2 and the second's to R8:2."""
    p = fresh(tmp_path)
    a, b = (f"{symbol}1", f"{symbol}2")
    place(p, symbol, a, 101.6, 101.6)
    place(p, symbol, b, 127, 101.6)
    place(p, "R", "R7", 93.98, 97.79, rot=90)  # R7:2 at (97.79, 97.79)
    wire(p, 97.79, 97.79, 101.6, 97.79)
    place(p, "R", "R8", 119.38, 97.79, rot=90)  # R8:2 at (123.19, 97.79)
    wire(p, 123.19, 97.79, 127, 97.79)
    rename_ref(p, a, dup)
    rename_ref(p, b, dup)
    return p


@ORACLE
@pytest.mark.parametrize(("symbol", "dup"), [("R", "R1"), ("R", "R?"), ("C", "C1"), ("C", "C?")])
def test_pi15_two_parts_sharing_a_reference_are_refused(tmp_path, symbol, dup, oracle):
    """PI-15. KiCad's netlist knows a part by its reference, so the two pin 1s are one pad:
    wiring it put both on NDUP and merged R7's net with R8's."""
    p = _pi15(tmp_path, symbol, dup)
    status, msg = wired(p, [(dup, "1")], "NDUP", oracle=oracle)
    assert status == "REFUSED" and msg.startswith("[dup_ref] "), msg


def _mixed_parts(tmp_path) -> str:
    """dupref mixed_parts_test: a 74LS00 placed as U1 unit 1 and a 74LS04 retagged U1 unit 2,
    two different parts that look like two units of one. Both draw a pad 3."""
    p = fresh(tmp_path)
    place(p, "74LS00", "U1", 101.6, 101.6)
    place(p, "74LS04", "U2", 152.4, 101.6)
    set_unit(p, "U2", 2)
    rename_ref(p, "U2", "U1")
    return p


@ORACLE
@pytest.mark.parametrize("pad", ["1", "3"])
def test_two_different_parts_sharing_a_reference_are_refused(tmp_path, pad, oracle):
    """Distinct units are not enough: the definitions must agree on which unit draws each pad.
    Pad 3 is the 74LS00's 1Y output and the 74LS04's 2A input; the old code wired both copies,
    joining an output to an input."""
    p = _mixed_parts(tmp_path)
    status, msg = wired(p, [("U1", pad)], "N", oracle=oracle)
    assert status == "REFUSED" and msg.startswith("[dup_ref] "), msg
    assert "disagree on pad 2 ('74LS00': units 1 as input; '74LS04': units 1 as output)" in msg


@ORACLE
def test_a_reference_shared_with_another_sheet_is_refused(tmp_path, oracle):
    """Two resistors annotated R1 on two sheets are one pad 1 to KiCad: wiring the root's copy
    left that pad in two nets, N and the child's CHILD_NET."""
    root, child = _hierarchy(tmp_path, "h")
    place(child, "R", "R1", 101.6, 101.6)
    stub(child, 101.6, 97.79, 0, -2.54, "CHILD_NET")
    sheet(root, child, "C", 152.4, 25.4)
    place(root, "R", "R1", 101.6, 101.6)
    status, msg = wired(root, [("R1", "1")], "N", oracle=oracle)
    assert status == "REFUSED" and msg.startswith("[dup_ref] "), msg
    assert "on c.kicad_sch" in msg


@ORACLE
def test_units_of_one_part_on_two_sheets_are_accepted(tmp_path, oracle):
    """Guard against over-refusal (there was no reference check before): U1's gates 1 and 2 sit
    on two sheets as one part should, and a pin only its own gate draws is wired on either."""
    root, child = _units_two_sheets(tmp_path)
    status, msg = wired(root, [("U1", "1")], "A_IN", oracle=oracle)
    assert status == "OK", msg
    status, msg = wired(child, [("U1", "5")], "B_IN", judge_on=root, oracle=oracle)
    assert status == "OK", msg


def _reused(tmp_path, second_ref: str) -> tuple[str, str]:
    """One child sheet used twice from the root, holding R1, whose instance under the second
    sheet block is annotated *second_ref*. R2 on the root ties its pin 1 to nothing."""
    root, child = _hierarchy(tmp_path, "h")
    place(child, "R", "R1", 101.6, 101.6)
    sheet(root, child, "C1", 152.4, 25.4)
    sheet(root, child, "C2", 152.4, 76.2, again={"R1": second_ref})
    place(root, "R", "R2", 50.8, 101.6)
    return root, child


@ORACLE
def test_a_reused_sheet_whose_instances_share_a_reference_is_refused(tmp_path, oracle):
    """Divergence 8 of the design record (the prototype accepted this). Both instances of the
    child carry R1, so KiCad's netlist has one R1 whose pin 1 sits on two nets, /C1/N and
    /C2/N, once the child is wired: the reference names two parts."""
    root, child = _reused(tmp_path, "R1")
    status, msg = wired(child, [("R1", "1")], "N", judge_on=root, oracle=oracle)
    assert status == "REFUSED" and msg.startswith("[dup_ref] "), msg


@ORACLE
def test_a_reused_sheet_annotated_per_instance_is_accepted(tmp_path, oracle):
    """The instances carry R1 and R101, two parts with their own references, which is how KiCad
    annotates a reused sheet. Wiring the sheet wires every instance, R1:1 onto /C1/N and R101:1
    onto /C2/N, and the result says so."""
    root, child = _reused(tmp_path, "R101")
    before = nets(root) if oracle else None
    status, msg = call_wptn(child, pins(("R1", "1")), "N")
    assert status == "OK", msg
    assert "used 2 times in its project, so this wiring is in every instance: R1:1, R101:1" in msg
    if before is not None:
        v = judge(before, nets(root), [{("R1", "1")}, {("R101", "1")}], "N")
        assert v.delivered and not v.wrong, v.problems()


# ---------------------------------------------------------------------------------------------
# Net classes (H-C2 to H-C5)
# ---------------------------------------------------------------------------------------------

_BOX = [(71.12, 60.96), (81.28, 60.96), (81.28, 71.12), (71.12, 71.12)]
_STRIP = [(71.12, 70.485), (81.28, 70.485), (81.28, 71.755), (71.12, 71.755)]


def _inert05(tmp_path, v: str) -> str:
    """INERT-05 (v05_rule_area). R1:1 at (76.2, 72.39) points up into a rule area; R2:1 carries
    SIG; the project defines class HV. RA1 and RA4: a box from y 60.96 to 71.12 with an HV
    directive on its top edge. RA2: a strip from y 70.485 to 71.755 across the stub's path, its
    directive on the left edge. RA3: the box with no directive."""
    from routing_fixtures import netclass_flag, rule_area

    p = fresh(tmp_path)
    place(p, "R", "R1", 76.2, 76.2)
    place(p, "R", "R2", 96.52, 76.2)
    wire(p, 96.52, 72.39, 96.52, 69.85)
    label(p, "SIG", 96.52, 69.85)
    project_file(Path(p).parent, Path(p).stem, classes=("HV",))
    if v in ("RA1", "RA4"):
        rule_area(p, _BOX)
        netclass_flag(p, "HV", 78.74, 60.96)
    elif v == "RA2":
        rule_area(p, _STRIP)
        netclass_flag(p, "HV", 71.12, 71.12)
    else:
        rule_area(p, _BOX)
    return p


@ORACLE
@pytest.mark.parametrize("v", ["RA1", "RA2"])
def test_inert05_a_stub_into_a_classed_rule_area_falls_back_to_the_pin(tmp_path, v, oracle):
    """INERT-05. The 2.54 mm stub up from R1:1 enters an area whose directive assigns HV, which
    KiCad then gives to every net the area holds: the old stub moved R1:1 and R2:1 (SIG's net)
    into HV. The label goes on the pin end instead, outside the area."""
    p = _inert05(tmp_path, v)
    status, msg = wired(p, [("R1", "1")], "SIG", oracle=oracle)
    assert status == "OK" and "on the pin end" in msg, msg


@ORACLE
@pytest.mark.parametrize(("v", "direction"), [("RA3", "auto"), ("RA4", "left")])
def test_inert05_a_stub_clear_of_any_class_is_kept(tmp_path, v, direction, oracle):
    """Guards against over-refusal: an area with no directive assigns nothing (RA3), and a
    stub left stays clear of the box (RA4)."""
    p = _inert05(tmp_path, v)
    status, msg = wired(p, [("R1", "1")], "SIG", direction=direction, oracle=oracle)
    assert status == "OK" and "stub" in msg and "on the pin end" not in msg, msg


# ---------------------------------------------------------------------------------------------
# A part is known by the reference KiCad reads, not by a stale Reference property
# ---------------------------------------------------------------------------------------------


def _stale(tmp_path) -> str:
    """A project's root holding R1, R2 and R3, with R2:1 wired to R3:1. R1 and R2 both carry a
    first instance entry from another project naming them R1, and R1 as their Reference
    property; KiCad reads them as R1 and R2 at the live path."""
    p = fresh(tmp_path, "h")
    project_file(Path(p).parent, "h")
    place(p, "R", "R1", 101.6, 101.6)
    place(p, "R", "R2", 152.4, 101.6)
    place(p, "R", "R3", 203.2, 101.6)
    wire(p, 152.4, 97.79, 152.4, 92.71)
    wire(p, 152.4, 92.71, 203.2, 92.71)
    wire(p, 203.2, 92.71, 203.2, 97.79)
    stale_reference(p, "R1", "R1")
    stale_reference(p, "R2", "R1")
    return p


@ORACLE
def test_a_stale_reference_property_does_not_make_two_parts_one_pad(tmp_path, oracle):
    """Matching parts by the Reference property made KiCad's R1 and R2 one pad "R1", so wiring
    R1:1 also wired R2:1 and merged R2's net into N. Only R1:1 is wired now."""
    p = _stale(tmp_path)
    old = open(p, "rb").read()
    status, msg = wired(p, [("R1", "1")], "N", oracle=oracle)
    assert status == "OK" and "copy at" not in msg, msg
    added = assert_only_added(old, open(p, "rb").read())
    assert [_xy(n, 0) for n in added if n.head == "wire"] == [(101.6, 97.79)]


@ORACLE
def test_a_part_is_found_by_the_reference_kicad_reads(tmp_path, oracle):
    """KiCad's R2 has the Reference property R1; asking for R2 wired nothing ("not found")."""
    p = _stale(tmp_path)
    status, msg = wired(p, [("R2", "1")], "M", oracle=oracle)
    assert status == "OK", msg
