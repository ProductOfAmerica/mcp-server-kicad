"""Tests for high-level routing tools: wire_pin_to_label, connect_pins, no_connect_pin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import (
    _default_effects,
    _gen_uuid,
    build_r_symbol,
    build_test_part_symbol,
    new_schematic,
    place_r1,
    reparse,
)
from kiutils.items.common import Effects, Font, Position, Property
from kiutils.items.schitems import Connection, SchematicSymbol

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
        result = schematic.wire_pin_to_label(
            reference="U1",
            pin_name="IN",
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
        schematic.wire_pin_to_label(
            reference="U1",
            pin_name="IN",
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
        schematic.wire_pin_to_label(
            reference="U1",
            pin_name="IN",
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
        schematic.wire_pin_to_label(
            reference="U1",
            pin_name="OUT",
            label_text="AUTO_OUT",
            direction="auto",
            schematic_path=path,
        )
        sch = reparse(path)
        label = next(lbl for lbl in sch.labels if lbl.text == "AUTO_OUT")
        # OUT pin outward is right -> label rotation 0
        assert label.position.angle == 0

    def test_auto_direction_rotated_90(self, tmp_path):
        """TestPart at 90deg: IN pin outward should be up (-Y)."""
        path = _make_test_part_sch(tmp_path, rotation=90)
        schematic.wire_pin_to_label(
            reference="U1",
            pin_name="IN",
            label_text="ROT90",
            direction="auto",
            schematic_path=path,
        )
        sch = reparse(path)
        label = next(lbl for lbl in sch.labels if lbl.text == "ROT90")
        assert label.position.angle == 90  # up-pointing label
        # Wire should go up: end_y < start_y
        wire = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"][0]
        assert wire.points[1].Y < wire.points[0].Y

    def test_auto_direction_mirror_x(self, tmp_path):
        """TestPart IN pin with mirror=x: outward should still be left."""
        path = _make_test_part_sch(tmp_path, mirror="x")
        schematic.wire_pin_to_label(
            reference="U1",
            pin_name="IN",
            label_text="MIR_X",
            direction="auto",
            schematic_path=path,
        )
        sch = reparse(path)
        label = next(lbl for lbl in sch.labels if lbl.text == "MIR_X")
        assert label.position.angle == 180  # still left for IN pin with mirror-x

    def test_pin_by_number(self, scratch_sch):
        """R1 pins have name '~', should match by number."""
        result = schematic.wire_pin_to_label(
            reference="R1",
            pin_name="1",
            label_text="R1_PIN1",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        assert "R1_PIN1" in result
        sch = reparse(str(scratch_sch))
        assert any(lbl.text == "R1_PIN1" for lbl in sch.labels)

    def test_custom_stub_length(self, tmp_path):
        path = _make_test_part_sch(tmp_path)
        schematic.wire_pin_to_label(
            reference="U1",
            pin_name="IN",
            label_text="STUB",
            stub_length=5.08,
            direction="left",
            schematic_path=path,
        )
        sch = reparse(path)
        wire = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"][0]
        dx = abs(wire.points[1].X - wire.points[0].X)
        assert abs(dx - 5.08) < 0.02

    def test_bad_reference(self, scratch_sch):
        with pytest.raises(ValueError, match="not found"):
            schematic.wire_pin_to_label(
                reference="X99",
                pin_name="1",
                label_text="BAD",
                schematic_path=str(scratch_sch),
            )

    def test_bad_pin(self, scratch_sch):
        with pytest.raises(ValueError, match="not found"):
            schematic.wire_pin_to_label(
                reference="R1",
                pin_name="NONEXIST",
                label_text="BAD",
                schematic_path=str(scratch_sch),
            )

    def test_warns_on_conflicting_label(self, scratch_sch):
        """Warn when a different net label already exists at the endpoint."""
        # Wire R1 pin 1 to "NET_A"
        schematic.wire_pin_to_label(
            reference="R1",
            pin_name="1",
            label_text="NET_A",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        # Wire R1 pin 1 again to "NET_B" (different net, same pin = same endpoint)
        result = schematic.wire_pin_to_label(
            reference="R1",
            pin_name="1",
            label_text="NET_B",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        # Should contain a warning about conflicting label
        assert "warning" in result.lower() or "conflict" in result.lower()


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


# ===========================================================================
# TestGetNetConnections
# ===========================================================================


class TestGetNetConnections:
    def test_finds_wired_pin(self, scratch_sch):
        """Wire a pin to a label, then query that net."""
        schematic.wire_pin_to_label(
            reference="R1",
            pin_name="1",
            label_text="NET_X",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        result = schematic.get_net_connections(
            label_text="NET_X",
            schematic_path=str(scratch_sch),
        )
        data = json.loads(result)
        refs = [c["reference"] for c in data["connections"]]
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
        )
        schematic.wire_pin_to_label(
            reference="R1",
            pin_name="1",
            label_text="SHARED",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        schematic.wire_pin_to_label(
            reference="R2",
            pin_name="1",
            label_text="SHARED",
            direction="up",
            schematic_path=str(scratch_sch),
        )
        result = schematic.get_net_connections(
            label_text="SHARED",
            schematic_path=str(scratch_sch),
        )
        data = json.loads(result)
        refs = [c["reference"] for c in data["connections"]]
        assert "R1" in refs
        assert "R2" in refs

    def test_no_connections(self, scratch_sch):
        result = schematic.get_net_connections(
            label_text="NONEXISTENT",
            schematic_path=str(scratch_sch),
        )
        data = json.loads(result)
        assert data["connections"] == []


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
        result = schematic.wire_pins_to_net(
            pins=[{"reference": "R999", "pin": "1"}],
            label_text="VCC",
            schematic_path=str(scratch_sch),
        )
        assert "error" in result.lower() or "not found" in result.lower()


# ===========================================================================
# TestAddPowerRail
# ===========================================================================


class TestAddPowerRail:
    def test_places_symbol_and_wires_pins(self, scratch_sch, scratch_power_lib):
        result = schematic.add_power_rail(
            lib_id="power:VCC",
            reference="#PWR01",
            pins=[
                {"reference": "R1", "pin": "1"},
            ],
            x=100,
            y=50,
            symbol_lib_path=str(scratch_power_lib),
            schematic_path=str(scratch_sch),
        )
        assert "#PWR01" in result
        assert "1 pin" in result

        sch = reparse(str(scratch_sch))
        # Power symbol should be placed
        refs = [
            next((p.value for p in s.properties if p.key == "Reference"), "")
            for s in sch.schematicSymbols
        ]
        assert "#PWR01" in refs
        # VCC label should exist (from wiring R1 pin 1)
        vcc_labels = [lbl for lbl in sch.labels if lbl.text == "VCC"]
        assert len(vcc_labels) >= 1

    def test_empty_pins_just_places_symbol(self, scratch_sch, scratch_power_lib):
        result = schematic.add_power_rail(
            lib_id="power:GND",
            reference="#PWR02",
            pins=[],
            x=100,
            y=200,
            symbol_lib_path=str(scratch_power_lib),
            schematic_path=str(scratch_sch),
        )
        assert "#PWR02" in result
        assert "0 pin" in result


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
        )
        assert "C2" in result
        sch = reparse(str(scratch_sch))
        label_texts = {lbl.text for lbl in sch.labels}
        assert "+3V3" in label_texts
        assert "PGND" in label_texts
