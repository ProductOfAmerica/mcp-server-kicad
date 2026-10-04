"""wire_pins_to_net judged by KiCad: the probe cases of the routing pressure test.

Each case comes from the measurement harness behind docs/adr-routing-safety.md and keeps its
case ID. A call is held to the write checks of routing_checks.call_wptn (one write that only
adds wires and labels, byte identity on a refusal or a no-op, no junction, overlap or crossing)
and, where kicad-cli is installed, to the netlist judge of netlist_oracle: no net merges,
splits or is renamed, and the requested pad lands on the requested net.
"""

from __future__ import annotations

import pytest
from conftest import netlist_nodes, requires_cli
from netlist_oracle import judge, nets
from routing_checks import assert_only_added, call_wptn
from routing_fixtures import ORIENTED, fresh, label, place, power, stub, wire

from mcp_server_kicad import schematic


def pins(*specs):
    return [{"reference": r, "pin": p} for r, p in specs]


def wired(path, specs, n, **kw):
    """call_wptn plus the netlist judge when kicad-cli is present. Returns (status, msg)."""
    before = nets(path) if _cli() else None
    status, msg = call_wptn(path, pins(*specs), n, **kw)
    if before is not None:
        verdict = judge(before, nets(path), [set(specs)], n, wrote=status == "OK")
        assert not verdict.wrong, (msg, verdict.problems())
        if status != "REFUSED":
            assert verdict.delivered, msg
    return status, msg


def _cli() -> bool:
    from conftest import HAS_KICAD_CLI

    return HAS_KICAD_CLI


# ---------------------------------------------------------------------------------------------
# PI-01, PI-12: every pin of a resistor, a diode and a transistor in all 12 orientations
# ---------------------------------------------------------------------------------------------

_ROW = {"R": ("R", 50.8), "D": ("D", 101.6), "Q_NPN_BCE": ("Q", 152.4)}


def _xy(node, which: int) -> tuple[float, float]:
    xy = node.find("pts").find_all("xy")[which]
    return round(float(xy.atoms[1].text), 4), round(float(xy.atoms[2].text), 4)


def test_twelve_orientations_wire_the_pin_kicad_draws(tmp_path):
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
    before = nets(p) if _cli() else None
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
        after = nets(p)
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


def test_a_pin_already_on_the_net_is_a_no_op(tmp_path):
    p = fresh(tmp_path)
    place(p, "R", "R1", 101.6, 101.6)
    stub(p, 101.6, 97.79, 0, -2.54, "N")
    status, msg = call_wptn(p, pins(("R1", "1")), "N")
    assert status == "NOOP" and "already on 'N' via label 'N' at (101.6, 95.25)" in msg
