"""Tests for high-level routing tools: wire_pins_to_net, connect_pins, no_connect_pin."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import (
    HAS_KICAD_CLI,
    _default_effects,
    _gen_uuid,
    build_r_symbol,
    build_test_part_symbol,
    new_schematic,
    place_r1,
    reparse,
)
from conftest import (
    make_power_sch as _make_power_sch,
)
from kiutils.items.common import Effects, Font, Position, Property, Stroke
from kiutils.items.schitems import Connection, SchematicSymbol
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import schematic

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_part_sch(tmp_path: Path, x=100, y=100, rotation=0, mirror="") -> str:
    """Create schematic with TestPart placed as U1."""
    sch = new_schematic()
    sch.libSymbols.append(build_test_part_symbol())

    sym = SchematicSymbol()
    sym.libId = "TestPart"
    sym.libName = "TestPart"
    sym.position = Position(X=x, Y=y, angle=rotation)
    sym.uuid = _gen_uuid()
    sym.unit = 1
    sym.inBom = True
    sym.onBoard = True
    if mirror:
        sym.mirror = mirror
    sym.properties = [
        Property(
            key="Reference",
            value="U1",
            id=0,
            effects=_default_effects(),
            position=Position(X=x, Y=y - 3.81, angle=0),
        ),
        Property(
            key="Value",
            value="TestPart",
            id=1,
            effects=_default_effects(),
            position=Position(X=x, Y=y + 3.81, angle=0),
        ),
        Property(
            key="Footprint",
            value="",
            id=2,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
        Property(
            key="Datasheet",
            value="~",
            id=3,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=x, Y=y, angle=0),
        ),
    ]
    sym.pins = {"1": _gen_uuid(), "2": _gen_uuid()}
    sch.schematicSymbols.append(sym)

    path = str(tmp_path / "testpart.kicad_sch")
    sch.filePath = path
    sch.to_file()
    return path


def _count_wires(sch) -> int:
    return len([g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"])


# ===========================================================================
# TestWirePinToLabel
# ===========================================================================


class TestWirePinToLabel:
    def test_explicit_direction_right(self, tmp_path):
        path = _make_test_part_sch(tmp_path)
        result = schematic.wire_pins_to_net(
            pins=[{"reference": "U1", "pin": "IN"}],
            label_text="VIN",
            direction="right",
            schematic_path=path,
        )
        assert "VIN" in result
        sch = reparse(path)
        assert _count_wires(sch) == 1
        assert any(lbl.text == "VIN" for lbl in sch.labels)

    def test_explicit_direction_left(self, tmp_path):
        path = _make_test_part_sch(tmp_path)
        schematic.wire_pins_to_net(
            pins=[{"reference": "U1", "pin": "IN"}],
            label_text="NET_A",
            direction="left",
            schematic_path=path,
        )
        sch = reparse(path)
        label = next(lbl for lbl in sch.labels if lbl.text == "NET_A")
        assert label.position.angle == 180  # left-pointing label

    def test_auto_direction_in_pin(self, tmp_path):
        """TestPart IN pin at (-5.08,0) angle 0: outward is left."""
        path = _make_test_part_sch(tmp_path)
        schematic.wire_pins_to_net(
            pins=[{"reference": "U1", "pin": "IN"}],
            label_text="AUTO_IN",
            direction="auto",
            schematic_path=path,
        )
        sch = reparse(path)
        label = next(lbl for lbl in sch.labels if lbl.text == "AUTO_IN")
        # IN pin outward is left -> label rotation 180
        assert label.position.angle == 180
        # Wire should go left: end_x < start_x
        wire = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"][0]
        assert wire.points[1].X < wire.points[0].X

    def test_auto_direction_out_pin(self, tmp_path):
        """TestPart OUT pin at (5.08,0) angle 180: outward is right."""
        path = _make_test_part_sch(tmp_path)
        schematic.wire_pins_to_net(
            pins=[{"reference": "U1", "pin": "OUT"}],
            label_text="AUTO_OUT",
            direction="auto",
            schematic_path=path,
        )
        sch = reparse(path)
        label = next(lbl for lbl in sch.labels if lbl.text == "AUTO_OUT")
        # OUT pin outward is right -> label rotation 0
        assert label.position.angle == 0

    def test_auto_direction_rotated_90(self, tmp_path):
        """TestPart at 90 deg: KiCad turns IN, which points left at 0 deg, to point down.

        IN sits at (100, 105.08), below the body. This test used to assert "up", which is the
        direction into the body: the old outward angle was wrong at 90 and 270 deg.
        """
        path = _make_test_part_sch(tmp_path, rotation=90)
        schematic.wire_pins_to_net(
            pins=[{"reference": "U1", "pin": "IN"}],
            label_text="ROT90",
            direction="auto",
            schematic_path=path,
        )
        sch = reparse(path)
        label = next(lbl for lbl in sch.labels if lbl.text == "ROT90")
        assert label.position.angle == 270  # down-pointing label
        wire = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"][0]
        assert (wire.points[0].X, wire.points[0].Y) == (100, 105.08)
        assert wire.points[1].Y > wire.points[0].Y

    def test_auto_direction_mirror_x(self, tmp_path):
        """TestPart IN pin with mirror=x: outward should still be left."""
        path = _make_test_part_sch(tmp_path, mirror="x")
        schematic.wire_pins_to_net(
            pins=[{"reference": "U1", "pin": "IN"}],
            label_text="MIR_X",
            direction="auto",
            schematic_path=path,
        )
        sch = reparse(path)
        label = next(lbl for lbl in sch.labels if lbl.text == "MIR_X")
        assert label.position.angle == 180  # still left for IN pin with mirror-x

    def test_pin_by_number(self, scratch_sch):
        """R1 pins have name '~', should match by number."""
        result = schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="R1_PIN1",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        assert "R1_PIN1" in result
        sch = reparse(str(scratch_sch))
        assert any(lbl.text == "R1_PIN1" for lbl in sch.labels)

    def test_custom_stub_length(self, tmp_path):
        path = _make_test_part_sch(tmp_path)
        schematic.wire_pins_to_net(
            pins=[{"reference": "U1", "pin": "IN"}],
            label_text="STUB",
            stub_length=5.08,
            direction="left",
            schematic_path=path,
        )
        sch = reparse(path)
        wire = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"][0]
        # The stub runs exactly stub_length from the pin end: nothing is snapped.
        dx = abs(wire.points[1].X - wire.points[0].X)
        assert dx == pytest.approx(5.08)

    def test_bad_reference(self, scratch_sch):
        with pytest.raises(ToolError, match="not found"):
            schematic.wire_pins_to_net(
                pins=[{"reference": "X99", "pin": "1"}],
                label_text="BAD",
                schematic_path=str(scratch_sch),
            )

    def test_bad_pin(self, scratch_sch):
        with pytest.raises(ToolError, match="not found"):
            schematic.wire_pins_to_net(
                pins=[{"reference": "R1", "pin": "NONEXIST"}],
                label_text="BAD",
                schematic_path=str(scratch_sch),
            )

    def test_a_pin_already_on_another_named_net_is_refused(self, scratch_sch):
        """R1:1 is on NET_A; wiring it to NET_B as well would join the two nets.

        This test used to assert the opposite: that the second call "auto-redirected" its stub
        and succeeded, with the two labels at different positions. Both labels were on R1:1's
        net, so NET_A and NET_B became one net. The call now refuses and writes nothing.
        """
        path = str(scratch_sch)
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="NET_A",
            direction="up",
            schematic_path=path,
        )
        before = scratch_sch.read_bytes()
        with pytest.raises(ToolError, match=r"(?s)^\[names\] .*'NET_A'"):
            schematic.wire_pins_to_net(
                pins=[{"reference": "R1", "pin": "1"}],
                label_text="NET_B",
                direction="up",
                schematic_path=path,
            )
        assert scratch_sch.read_bytes() == before


def _make_two_parts_sch(tmp_path: Path) -> str:
    """Create schematic with R1 at (100,100) and TestPart U1 at (200,100)."""
    sch = new_schematic()
    sch.libSymbols.append(build_r_symbol())
    sch.libSymbols.append(build_test_part_symbol())

    sch.schematicSymbols.append(place_r1(100, 100))

    sym = SchematicSymbol()
    sym.libId = "TestPart"
    sym.libName = "TestPart"
    sym.position = Position(X=200, Y=100, angle=0)
    sym.uuid = _gen_uuid()
    sym.unit = 1
    sym.inBom = True
    sym.onBoard = True
    sym.properties = [
        Property(
            key="Reference",
            value="U1",
            id=0,
            effects=_default_effects(),
            position=Position(X=200, Y=96.19, angle=0),
        ),
        Property(
            key="Value",
            value="TestPart",
            id=1,
            effects=_default_effects(),
            position=Position(X=200, Y=103.81, angle=0),
        ),
        Property(
            key="Footprint",
            value="",
            id=2,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=200, Y=100, angle=0),
        ),
        Property(
            key="Datasheet",
            value="~",
            id=3,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=200, Y=100, angle=0),
        ),
    ]
    sym.pins = {"1": _gen_uuid(), "2": _gen_uuid()}
    sch.schematicSymbols.append(sym)

    path = str(tmp_path / "two_parts.kicad_sch")
    sch.filePath = path
    sch.to_file()
    return path


# ===========================================================================
# TestConnectPins
# ===========================================================================


class TestConnectPins:
    def test_l_shaped_route(self, tmp_path):
        """R1 pin 1 and U1 IN at different X/Y: L-shaped route."""
        path = _make_two_parts_sch(tmp_path)
        result = schematic.connect_pins(
            ref1="R1",
            pin1="1",
            ref2="U1",
            pin2="IN",
            schematic_path=path,
        )
        assert "2 wire segments" in result
        sch = reparse(path)
        assert _count_wires(sch) == 2

    def test_axis_aligned_route(self, scratch_sch):
        """R1 pin 1 and pin 2 share X: single wire."""
        sch_before = reparse(str(scratch_sch))
        wires_before = _count_wires(sch_before)

        result = schematic.connect_pins(
            ref1="R1",
            pin1="1",
            ref2="R1",
            pin2="2",
            schematic_path=str(scratch_sch),
        )
        assert "1 wire segment" in result

        sch_after = reparse(str(scratch_sch))
        assert _count_wires(sch_after) == wires_before + 1

    def test_bad_reference(self, scratch_sch):
        with pytest.raises(ValueError, match="not found"):
            schematic.connect_pins(
                ref1="X99",
                pin1="1",
                ref2="R1",
                pin2="1",
                schematic_path=str(scratch_sch),
            )

    def test_bad_pin(self, scratch_sch):
        with pytest.raises(ValueError, match="not found"):
            schematic.connect_pins(
                ref1="R1",
                pin1="NOPE",
                ref2="R1",
                pin2="1",
                schematic_path=str(scratch_sch),
            )


# ===========================================================================
# TestNoConnectPin
# ===========================================================================


class TestNoConnectPin:
    def test_basic(self, scratch_sch):
        result = schematic.no_connect_pin(
            reference="R1",
            pin_name="1",
            schematic_path=str(scratch_sch),
        )
        assert "No-connect" in result
        assert "R1" in result
        sch = reparse(str(scratch_sch))
        assert len(sch.noConnects) == 1

    def test_bad_reference(self, scratch_sch):
        with pytest.raises(ValueError, match="not found"):
            schematic.no_connect_pin(
                reference="X99",
                pin_name="1",
                schematic_path=str(scratch_sch),
            )

    def test_bad_pin(self, scratch_sch):
        with pytest.raises(ValueError, match="not found"):
            schematic.no_connect_pin(
                reference="R1",
                pin_name="NONEXIST",
                schematic_path=str(scratch_sch),
            )

    def test_idempotent(self, scratch_sch):
        """Second call for the same pin is a no-op, not a duplicate (issue #4)."""
        schematic.no_connect_pin(reference="R1", pin_name="1", schematic_path=str(scratch_sch))
        result = schematic.no_connect_pin(
            reference="R1", pin_name="1", schematic_path=str(scratch_sch)
        )
        assert "already present" in result
        sch = reparse(str(scratch_sch))
        assert len(sch.noConnects) == 1


# ===========================================================================
# TestRemoveNoConnect
# ===========================================================================


class TestRemoveNoConnect:
    def test_remove(self, scratch_sch):
        schematic.no_connect_pin(reference="R1", pin_name="1", schematic_path=str(scratch_sch))
        result = schematic.remove_no_connect(
            reference="R1", pin_name="1", schematic_path=str(scratch_sch)
        )
        assert "Removed 1" in result
        assert len(reparse(str(scratch_sch)).noConnects) == 0

    def test_remove_stacked_duplicates(self, scratch_sch):
        """Schematics written before the idempotency fix have stacked
        no-connects on one pin; a single call clears all of them."""
        from kiutils.items.schitems import NoConnect

        schematic.no_connect_pin(reference="R1", pin_name="1", schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        dup = NoConnect(position=sch.noConnects[0].position, uuid=_gen_uuid())
        sch.noConnects.append(dup)
        sch.to_file()

        result = schematic.remove_no_connect(
            reference="R1", pin_name="1", schematic_path=str(scratch_sch)
        )
        assert "Removed 2" in result
        assert len(reparse(str(scratch_sch)).noConnects) == 0

    def test_remove_missing(self, scratch_sch):
        with pytest.raises(ToolError, match="No no-connect"):
            schematic.remove_no_connect(
                reference="R1", pin_name="1", schematic_path=str(scratch_sch)
            )


# ===========================================================================
# TestGetNetConnections
# ===========================================================================


class TestGetNetConnections:
    def test_finds_wired_pin(self, scratch_sch):
        """Wire a pin to a label, then query that net."""
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="NET_X",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        result = schematic.get_net_connections(
            label_text="NET_X",
            schematic_path=str(scratch_sch),
        )
        refs = [c["reference"] for c in result.connections]
        assert "R1" in refs

    def test_finds_multiple_pins(self, scratch_sch):
        """Multiple pins wired to the same net all appear."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="4.7K",
            x=200,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="SHARED",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        schematic.wire_pins_to_net(
            pins=[{"reference": "R2", "pin": "1"}],
            label_text="SHARED",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        result = schematic.get_net_connections(
            label_text="SHARED",
            schematic_path=str(scratch_sch),
        )
        refs = [c["reference"] for c in result.connections]
        assert "R1" in refs
        assert "R2" in refs

    def test_no_connections(self, scratch_sch):
        result = schematic.get_net_connections(
            label_text="NONEXISTENT",
            schematic_path=str(scratch_sch),
        )
        assert result.connections == []


# ===========================================================================
# TestWirePinsToNet
# ===========================================================================


class TestWirePinsToNet:
    def test_wires_multiple_pins(self, scratch_sch):
        """Batch wire 2 pins to the same net."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="4.7K",
            x=200,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        result = schematic.wire_pins_to_net(
            pins=[
                {"reference": "R1", "pin": "1"},
                {"reference": "R2", "pin": "1"},
            ],
            label_text="VCC",
            schematic_path=str(scratch_sch),
        )
        assert "2 pins" in result
        sch = reparse(str(scratch_sch))
        vcc_labels = [lbl for lbl in sch.labels if lbl.text == "VCC"]
        assert len(vcc_labels) == 2

    def test_empty_list(self, scratch_sch):
        result = schematic.wire_pins_to_net(
            pins=[],
            label_text="VCC",
            schematic_path=str(scratch_sch),
        )
        assert "0 pins" in result

    def test_bad_reference(self, scratch_sch):
        with pytest.raises(ToolError, match="not found"):
            schematic.wire_pins_to_net(
                pins=[{"reference": "R999", "pin": "1"}],
                label_text="VCC",
                schematic_path=str(scratch_sch),
            )


# ===========================================================================
# TestConnectPinsNoSnap (Bug 2)
# ===========================================================================


class TestConnectPinsNoSnap:
    def test_wire_reaches_non_grid_pin(self, tmp_path):
        """Wire endpoints must land exactly on pin positions, not snapped."""
        path = _make_test_part_sch(tmp_path)
        # TestPart U1 at (100,100): pin IN at (94.92, 100), pin OUT at (105.08, 100)
        # These pin positions are NOT on the 1.27mm grid.
        result = schematic.connect_pins(
            ref1="U1",
            pin1="IN",
            ref2="U1",
            pin2="OUT",
            schematic_path=path,
        )
        assert "Connected" in result
        sch = reparse(path)
        wires = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"]
        # Collect all wire endpoints
        endpoints = set()
        for w in wires:
            for pt in w.points:
                endpoints.add((round(pt.X, 4), round(pt.Y, 4)))
        # Exact pin positions must appear (not snapped versions)
        assert (94.92, 100.0) in endpoints
        assert (105.08, 100.0) in endpoints


# ===========================================================================
# TestStubCollision (Bug 3)
# ===========================================================================


class TestStubCollision:
    def test_coincident_pins_cannot_go_to_different_nets(self, tmp_path):
        """R1:2 and R2:1 share the point (100, 103.81), so KiCad joins them.

        This test used to assert that wiring them to NET_A and then NET_B "avoided the
        collision" because the two labels landed at different positions. They were one net,
        so that merged NET_A and NET_B. The second call now refuses and writes nothing.
        """
        # R1 at (100, 100) and R2 at (100, 107.62): R1:2 and R2:1 coincide
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        sch.schematicSymbols.append(place_r1(100, 100))

        # Place R2 close to R1
        from conftest import _default_effects as _de
        from conftest import _gen_uuid as _gu

        r2 = SchematicSymbol()
        r2.libId = "Device:R"
        r2.libName = "R"
        r2.position = Position(X=100, Y=107.62, angle=0)
        r2.uuid = _gu()
        r2.unit = 1
        r2.inBom = True
        r2.onBoard = True
        r2.properties = [
            Property(
                key="Reference",
                value="R2",
                id=0,
                effects=_de(),
                position=Position(X=100, Y=103.81, angle=0),
            ),
            Property(
                key="Value",
                value="10K",
                id=1,
                effects=_de(),
                position=Position(X=100, Y=111.43, angle=0),
            ),
            Property(
                key="Footprint",
                value="",
                id=2,
                effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                position=Position(X=100, Y=107.62, angle=0),
            ),
            Property(
                key="Datasheet",
                value="~",
                id=3,
                effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                position=Position(X=100, Y=107.62, angle=0),
            ),
        ]
        r2.pins = {"1": _gu(), "2": _gu()}
        sch.schematicSymbols.append(r2)

        path = str(tmp_path / "adjacent.kicad_sch")
        sch.filePath = path
        sch.to_file()

        # Wire R1 pin 2 to NET_A (down)
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "2"}],
            label_text="NET_A",
            direction="down",
            schematic_path=path,
        )
        before = Path(path).read_bytes()
        with pytest.raises(ToolError, match=r"(?s)^\[names\] .*'NET_A'"):
            schematic.wire_pins_to_net(
                pins=[{"reference": "R2", "pin": "1"}],
                label_text="NET_B",
                direction="up",
                schematic_path=path,
            )
        assert Path(path).read_bytes() == before


# ===========================================================================
# TestAutoPlaceDecouplingCap
# ===========================================================================


class TestAutoPlaceDecouplingCap:
    def test_places_and_wires_cap(self, scratch_sch):
        result = schematic.auto_place_decoupling_cap(
            lib_id="Device:R",
            reference="C1",
            value="100nF",
            x=150,
            y=100,
            power_net="VCC",
            ground_net="GND",
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "C1" in result
        assert "VCC" in result
        assert "GND" in result

        sch = reparse(str(scratch_sch))
        # Cap should be placed
        c1 = None
        for sym in sch.schematicSymbols:
            if any(p.key == "Reference" and p.value == "C1" for p in sym.properties):
                c1 = sym
                break
        assert c1 is not None

        # Should have VCC and GND labels
        label_texts = {lbl.text for lbl in sch.labels}
        assert "VCC" in label_texts
        assert "GND" in label_texts

    def test_custom_nets(self, scratch_sch):
        """Works with non-standard net names."""
        result = schematic.auto_place_decoupling_cap(
            lib_id="Device:R",
            reference="C2",
            value="10uF",
            x=200,
            y=100,
            power_net="+3V3",
            ground_net="PGND",
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "C2" in result
        sch = reparse(str(scratch_sch))
        label_texts = {lbl.text for lbl in sch.labels}
        assert "+3V3" in label_texts
        assert "PGND" in label_texts


# ===========================================================================
# TestAutoJunctions
# ===========================================================================


class TestAutoJunctions:
    """Tests for automatic junction insertion at T-intersections."""

    def test_t_junction_connect_pins(self, tmp_path):
        """Reproduce the D3 bug: two connect_pins calls where the second
        wire's corner lands mid-segment on the first wire."""
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())

        # Place R1 at (100, 80) and R2 at (100, 120) — vertically aligned
        r1 = place_r1(100, 80)
        sch.schematicSymbols.append(r1)

        r2 = place_r1(100, 120)
        for prop in r2.properties:
            if prop.key == "Reference":
                prop.value = "R2"
        sch.schematicSymbols.append(r2)

        # Place R3 at (120, 100) — offset to the right
        r3 = place_r1(120, 100)
        for prop in r3.properties:
            if prop.key == "Reference":
                prop.value = "R3"
        sch.schematicSymbols.append(r3)

        path = tmp_path / "tjunc.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        sch_path = str(path)

        # R1 pin 2 is at (100, 83.81), R2 pin 1 is at (100, 116.19)
        # connect_pins creates a straight vertical wire (same X)
        schematic.connect_pins("R1", "2", "R2", "1", schematic_path=sch_path)

        # Verify no junctions yet (straight wire, no T)
        sch_after1 = reparse(sch_path)
        assert len(sch_after1.junctions) == 0

        # R3 pin 1 is at (120, 96.19). Connect R3:1 to R2:1 (100, 116.19)
        # L-shape: (120, 96.19) -> corner (100, 96.19) -> (100, 116.19)
        # The vertical segment from (100, 96.19) to (100, 116.19) overlaps
        # the existing wire from (100, 83.81) to (100, 116.19).
        # Corner point (100, 96.19) lands on interior of existing wire.
        schematic.connect_pins("R3", "1", "R2", "1", schematic_path=sch_path)

        # A junction should have been auto-created at (100, 96.19)
        sch_after2 = reparse(sch_path)
        assert len(sch_after2.junctions) >= 1
        junc_positions = [(j.position.X, j.position.Y) for j in sch_after2.junctions]
        assert any(abs(x - 100) < 0.02 and abs(y - 96.19) < 0.02 for x, y in junc_positions), (
            f"Expected junction near (100, 96.19), got {junc_positions}"
        )

    def test_no_junction_at_wire_endpoint(self, tmp_path):
        """No junction added when new wire meets existing wire at its endpoint."""
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())

        r1 = place_r1(100, 100)
        sch.schematicSymbols.append(r1)

        r2 = place_r1(100, 130)
        for prop in r2.properties:
            if prop.key == "Reference":
                prop.value = "R2"
        sch.schematicSymbols.append(r2)

        path = tmp_path / "no_junc.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        sch_path = str(path)

        # R1 pin 2 at (100, 103.81), R2 pin 1 at (100, 126.19)
        # Straight vertical wire — endpoints meet at pin positions
        schematic.connect_pins("R1", "2", "R2", "1", schematic_path=sch_path)

        sch_after = reparse(sch_path)
        assert len(sch_after.junctions) == 0

    def test_no_duplicate_junctions(self, tmp_path):
        """If a junction already exists at a T-point, don't add another."""
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())

        r1 = place_r1(100, 80)
        sch.schematicSymbols.append(r1)

        r2 = place_r1(100, 120)
        for prop in r2.properties:
            if prop.key == "Reference":
                prop.value = "R2"
        sch.schematicSymbols.append(r2)

        r3 = place_r1(120, 100)
        for prop in r3.properties:
            if prop.key == "Reference":
                prop.value = "R3"
        sch.schematicSymbols.append(r3)

        path = tmp_path / "dup_junc.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        sch_path = str(path)

        # Create the T-junction scenario
        schematic.connect_pins("R1", "2", "R2", "1", schematic_path=sch_path)
        schematic.connect_pins("R3", "1", "R2", "1", schematic_path=sch_path)

        sch_after = reparse(sch_path)
        junc_count_1 = len(sch_after.junctions)
        assert junc_count_1 >= 1

        # Connect again with a fourth resistor that hits the same point
        r4 = place_r1(80, 100)
        for prop in r4.properties:
            if prop.key == "Reference":
                prop.value = "R4"
        sch_after.schematicSymbols.append(r4)
        sch_after.to_file()

        schematic.connect_pins("R4", "1", "R2", "1", schematic_path=sch_path)

        sch_after2 = reparse(sch_path)
        # Count junctions near (100, 96.19)
        junc_positions = [(j.position.X, j.position.Y) for j in sch_after2.junctions]
        near_count = sum(
            1 for x, y in junc_positions if abs(x - 100) < 0.02 and abs(y - 96.19) < 0.02
        )
        assert near_count == 1, f"Expected 1 junction near (100, 96.19), got {near_count}"

    def test_wire_pins_to_net_refuses_a_pin_end_on_a_wire_interior(self, tmp_path):
        """R1:1 sits on the interior of an unlabelled wire, which in KiCad does not connect.

        This test used to assert that wire_pins_to_net joins them with a junction. A junction on
        an unsplit wire is read by kicad-cli 9, the reader behind netlist export, ERC and PCB
        update, as cutting the wire (docs/adr-routing-safety.md). Without one, neither the stub
        nor a label on the pin end can be placed without touching the wire, so the call refuses
        and writes nothing.
        """
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())

        # R1 at (100, 100): pin 1 at (100, 96.19), pin 2 at (100, 103.81)
        r1 = place_r1(100, 100)
        sch.schematicSymbols.append(r1)

        # Pre-existing horizontal wire crossing through R1's vertical axis
        sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[Position(X=90, Y=96.19), Position(X=110, Y=96.19)],
                stroke=Stroke(width=0, type="default"),
                uuid=_gen_uuid(),
            )
        )

        path = tmp_path / "wptn_junc.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        sch_path = str(path)

        before = path.read_bytes()
        with pytest.raises(ToolError, match=r"\[touch\]"):
            schematic.wire_pins_to_net(
                pins=[{"reference": "R1", "pin": "1"}],
                label_text="VCC",
                schematic_path=sch_path,
            )
        assert path.read_bytes() == before


# ===========================================================================
# TestNoAutoPwrFlag
# ===========================================================================


class TestNoAutoPwrFlag:
    def test_power_in_net_gets_no_pwr_flag(self, tmp_path):
        """wire_pins_to_net places no PWR_FLAG, even on a net with only power_in pins.

        Whether a net needs a flag depends on every driver on the net, which
        one call cannot see; deciding it per call put a second flag on nets
        that already had a driver. add_power_symbol places one on purpose.
        """
        path = _make_power_sch(tmp_path)
        schematic.wire_pins_to_net(
            pins=[{"reference": "#PWR01", "pin": "1"}],
            label_text="VCC",
            schematic_path=path,
        )
        sch = reparse(path)
        pwr_flags = [
            sym
            for sym in sch.schematicSymbols
            if any(p.key == "Value" and p.value == "PWR_FLAG" for p in sym.properties)
        ]
        assert pwr_flags == []
        assert not any(ls.entryName.endswith("PWR_FLAG") for ls in sch.libSymbols)


# ===========================================================================
# TestSystemLibSymbolRoundtrip
# ===========================================================================


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
class TestSystemLibSymbolRoundtrip:
    def test_pwr_flag_raw_tokens_preserved(self, tmp_path):
        """System library tokens like exclude_from_sim survive the verbatim copy."""
        path = _make_power_sch(tmp_path)
        schematic.place_component(
            lib_id="power:PWR_FLAG",
            reference="#FLG01",
            value="PWR_FLAG",
            x=110,
            y=100,
            schematic_path=path,
        )
        raw_text = Path(path).read_text()
        # These tokens are dropped by kiutils but preserved by _save_sch
        assert "exclude_from_sim" in raw_text, "exclude_from_sim was dropped"
        assert "pin_numbers" in raw_text, "pin_numbers was dropped"

    def test_system_lib_erc_no_mismatch(self, tmp_path):
        """ERC should report zero PWR_FLAG 'symbol doesn't match' violations after the copy."""
        from conftest import run_erc

        path = _make_power_sch(tmp_path)
        schematic.place_component(
            lib_id="power:PWR_FLAG",
            reference="#FLG01",
            value="PWR_FLAG",
            x=110,
            y=100,
            schematic_path=path,
        )
        report = run_erc(path)
        violations = []
        for sheet in report.get("sheets", []):
            violations.extend(sheet.get("violations", []))
        # Only check for PWR_FLAG mismatch — the VCC symbol is synthetic (from
        # conftest) and may legitimately differ from the system library copy.
        pwr_flag_mismatch = [
            v
            for v in violations
            if "match" in v.get("description", "").lower()
            and "PWR_FLAG" in v.get("description", "")
        ]
        assert pwr_flag_mismatch == [], f"PWR_FLAG symbol mismatch violations: {pwr_flag_mismatch}"


# ===========================================================================
# TestCustomLibNotAffected
# ===========================================================================
