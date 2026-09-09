"""Tests for the read-only tools in schematic.py.

Covers: list_schematic_components, list_schematic_labels, list_schematic_wires,
list_schematic_global_labels, get_schematic_summary, get_symbol_pins, get_pin_positions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import (
    _default_effects,
    _default_stroke,
    _gen_uuid,
    build_r_symbol,
    new_schematic,
    place_r1,
)
from kiutils.items.common import Effects, Font, Position, Property
from kiutils.items.schitems import Connection, LocalLabel, SchematicSymbol
from kiutils.symbol import Symbol, SymbolPin
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, schematic
from mcp_server_kicad.models import (
    NetConnectionsResult,
    SchematicSummary,
)

# ---------------------------------------------------------------------------
# Helper: build a schematic with R1 at a given rotation/mirror
# ---------------------------------------------------------------------------


def _make_rotated_sch(tmp_path: Path, rotation: float = 0, mirror: str = "") -> str:
    """Create a schematic with Device:R placed as R1 at (100,100) with rotation/mirror."""
    sch = new_schematic()
    sch.libSymbols.append(build_r_symbol())

    sym = SchematicSymbol()
    sym.libId = "Device:R"
    sym.libName = "R"
    sym.position = Position(X=100, Y=100, angle=rotation)
    sym.uuid = _gen_uuid()
    sym.unit = 1
    sym.inBom = True
    sym.onBoard = True
    if mirror:
        sym.mirror = mirror

    sym.properties = [
        Property(
            key="Reference",
            value="R1",
            id=0,
            effects=_default_effects(),
            position=Position(X=100, Y=96.19, angle=0),
        ),
        Property(
            key="Value",
            value="10K",
            id=1,
            effects=_default_effects(),
            position=Position(X=100, Y=103.81, angle=0),
        ),
        Property(
            key="Footprint",
            value="",
            id=2,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=100, Y=100, angle=0),
        ),
        Property(
            key="Datasheet",
            value="~",
            id=3,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=100, Y=100, angle=0),
        ),
    ]

    sym.pins = {"1": _gen_uuid(), "2": _gen_uuid()}
    sch.schematicSymbols.append(sym)

    path = str(tmp_path / f"rot{int(rotation)}_mir{mirror or 'none'}.kicad_sch")
    sch.filePath = path
    sch.to_file()
    return path


# ---------------------------------------------------------------------------
# Tests: list_schematic_* (split tools, consolidated)
# ---------------------------------------------------------------------------


class TestListSchematicItems:
    def test_list_components(self, scratch_sch):
        result = schematic.list_schematic_components(str(scratch_sch))
        assert isinstance(result, list)

    def test_list_components_has_data(self, scratch_sch):
        result = schematic.list_schematic_components(str(scratch_sch))
        assert len(result) > 0

    def test_list_labels(self, scratch_sch):
        result = schematic.list_schematic_labels(str(scratch_sch))
        assert isinstance(result, list)

    def test_list_wires(self, scratch_sch):
        result = schematic.list_schematic_wires(str(scratch_sch))
        assert isinstance(result, list)

    def test_list_global_labels(self, scratch_sch):
        result = schematic.list_schematic_global_labels(str(scratch_sch))
        assert isinstance(result, list)

    def test_empty_components(self, empty_sch):
        result = schematic.list_schematic_components(str(empty_sch))
        assert result == []

    def test_empty_labels(self, empty_sch):
        result = schematic.list_schematic_labels(str(empty_sch))
        assert result == []

    def test_empty_wires(self, empty_sch):
        result = schematic.list_schematic_wires(str(empty_sch))
        assert result == []


# ---------------------------------------------------------------------------
# Tests: get_symbol_pins
# ---------------------------------------------------------------------------


class TestGetSymbolPins:
    def test_known_symbol(self, scratch_sch: Path) -> None:
        result = schematic.get_symbol_pins("R", str(scratch_sch))
        # Should contain pin 1 and pin 2 info
        assert "Pin 1" in result or "pin 1" in result.lower()
        assert "Pin 2" in result or "pin 2" in result.lower()
        assert "passive" in result

    def test_unknown_symbol(self, scratch_sch: Path) -> None:
        with pytest.raises(ToolError, match="not found"):
            schematic.get_symbol_pins("NonExistent", str(scratch_sch))


# ---------------------------------------------------------------------------
# Tests: get_pin_positions
# ---------------------------------------------------------------------------


class TestGetPinPositions:
    def test_rotation_0(self, scratch_sch: Path) -> None:
        """Default rotation: Pin 1 at (100, 96.19), Pin 2 at (100, 103.81)."""
        result = schematic.get_pin_positions("R1", str(scratch_sch))
        lines = result.strip().split("\n")
        pin_lines = [ln for ln in lines if ln.strip().startswith("Pin")]
        pin1_line = [ln for ln in pin_lines if "Pin 1" in ln][0]
        pin2_line = [ln for ln in pin_lines if "Pin 2" in ln][0]
        assert "96.19" in pin1_line
        assert "103.81" in pin2_line

    def test_rotation_90(self, tmp_path: Path) -> None:
        """90 deg CW: Pin 1 at (96.19, 100), Pin 2 at (103.81, 100)."""
        path = _make_rotated_sch(tmp_path, rotation=90)
        result = schematic.get_pin_positions("R1", path)
        lines = result.strip().split("\n")
        pin_lines = [ln for ln in lines if ln.strip().startswith("Pin")]
        pin1_line = [ln for ln in pin_lines if "Pin 1" in ln][0]
        pin2_line = [ln for ln in pin_lines if "Pin 2" in ln][0]
        assert "96.19" in pin1_line
        assert "103.81" in pin2_line

    def test_rotation_180(self, tmp_path: Path) -> None:
        """180 deg: Pin 1 at (100, 103.81), Pin 2 at (100, 96.19)."""
        path = _make_rotated_sch(tmp_path, rotation=180)
        result = schematic.get_pin_positions("R1", path)
        lines = result.strip().split("\n")
        pin_lines = [ln for ln in lines if ln.strip().startswith("Pin")]
        pin1_line = [ln for ln in pin_lines if "Pin 1" in ln][0]
        pin2_line = [ln for ln in pin_lines if "Pin 2" in ln][0]
        assert "103.81" in pin1_line
        assert "96.19" in pin2_line

    def test_rotation_270(self, tmp_path: Path) -> None:
        """270 deg CW: Pin 1 at (103.81, 100), Pin 2 at (96.19, 100)."""
        path = _make_rotated_sch(tmp_path, rotation=270)
        result = schematic.get_pin_positions("R1", path)
        lines = result.strip().split("\n")
        pin_lines = [ln for ln in lines if ln.strip().startswith("Pin")]
        pin1_line = [ln for ln in pin_lines if "Pin 1" in ln][0]
        pin2_line = [ln for ln in pin_lines if "Pin 2" in ln][0]
        assert "103.81" in pin1_line
        assert "96.19" in pin2_line

    def test_mirror_x(self, tmp_path: Path) -> None:
        """Mirror x negates py in schematic coords (after Y-negate, rot=0).
        Pin 1: (0,3.81) -> negate Y -> (0,-3.81) -> mirror x -> (0,3.81) -> (100, 103.81).
        Pin 2: (0,-3.81) -> negate Y -> (0,3.81) -> mirror x -> (0,-3.81) -> (100, 96.19).
        """
        path = _make_rotated_sch(tmp_path, rotation=0, mirror="x")
        result = schematic.get_pin_positions("R1", path)
        assert "96.19" in result
        assert "103.81" in result
        # With mirror x, pin 1 and pin 2 swap positions vs rotation_0
        # Pin 1 should be at y=103.81, Pin 2 at y=96.19
        lines = result.strip().split("\n")
        pin_lines = [ln for ln in lines if ln.strip().startswith("Pin")]
        pin1_line = [ln for ln in pin_lines if "Pin 1" in ln][0]
        pin2_line = [ln for ln in pin_lines if "Pin 2" in ln][0]
        assert "103.81" in pin1_line
        assert "96.19" in pin2_line

    def test_mirror_y(self, tmp_path: Path) -> None:
        """Mirror y negates px (which is 0 for a vertical resistor, no visible change).
        Pin positions same as corrected rotation_0.
        Pin 1: (100, 96.19), Pin 2: (100, 103.81).
        """
        path = _make_rotated_sch(tmp_path, rotation=0, mirror="y")
        result = schematic.get_pin_positions("R1", path)
        assert "103.81" in result
        assert "96.19" in result
        # Same positions as rotation_0 since px=0 for both pins
        lines = result.strip().split("\n")
        pin_lines = [ln for ln in lines if ln.strip().startswith("Pin")]
        pin1_line = [ln for ln in pin_lines if "Pin 1" in ln][0]
        pin2_line = [ln for ln in pin_lines if "Pin 2" in ln][0]
        assert "96.19" in pin1_line
        assert "103.81" in pin2_line

    def test_unknown_reference(self, scratch_sch: Path) -> None:
        with pytest.raises(ToolError, match="not found"):
            schematic.get_pin_positions("X99", str(scratch_sch))


# ---------------------------------------------------------------------------
# Tests: get_schematic_summary
# ---------------------------------------------------------------------------


class TestGetSchematicSummary:
    def test_returns_page_and_counts(self, scratch_sch: Path) -> None:
        result = schematic.get_schematic_summary(str(scratch_sch))
        assert isinstance(result, SchematicSummary)
        assert result.page_size == "A4"
        assert result.page_width_mm == 297
        assert result.page_height_mm == 210
        assert result.components >= 0
        assert result.labels >= 0
        assert result.wires >= 0

    def test_empty_schematic(self, empty_sch: Path) -> None:
        result = schematic.get_schematic_summary(str(empty_sch))
        assert isinstance(result, SchematicSummary)
        assert result.page_size == "A4"
        assert result.components == 0
        assert result.labels == 0
        assert result.wires == 0


# ---------------------------------------------------------------------------
# Tests: get_net_connections multi-hop BFS
# ---------------------------------------------------------------------------


class TestGetNetConnectionsMultiHop:
    def test_traces_through_multiple_wire_segments(self, tmp_path: Path):
        """get_net_connections should follow multi-hop wire chains to reach a pin."""
        # Build schematic with 3-hop chain: label -> wire -> wire -> wire -> R1 pin
        # R1 at (100, 100): pin 1 at (100, 96.19), pin 2 at (100, 103.81)
        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        sch.schematicSymbols.append(place_r1(100, 100))

        # Label at (10, 96.19) — same Y as pin 1
        sch.labels.append(
            LocalLabel(
                text="MULTI_HOP",
                position=Position(X=10, Y=96.19, angle=0),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )
        # Wire 1: (10, 96.19) -> (40, 96.19)
        sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[Position(X=10, Y=96.19), Position(X=40, Y=96.19)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )
        # Wire 2: (40, 96.19) -> (70, 96.19)
        sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[Position(X=40, Y=96.19), Position(X=70, Y=96.19)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )
        # Wire 3: (70, 96.19) -> (100, 96.19) — reaches R1 pin 1
        sch.graphicalItems.append(
            Connection(
                type="wire",
                points=[Position(X=70, Y=96.19), Position(X=100, Y=96.19)],
                stroke=_default_stroke(),
                uuid=_gen_uuid(),
            )
        )

        path = tmp_path / "multihop.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = schematic.get_net_connections(
            label_text="MULTI_HOP",
            schematic_path=str(path),
        )
        assert isinstance(result, NetConnectionsResult)
        assert result.label_count == 1
        # With BFS, all 3 hops are traversed and R1 pin 1 at (100, 96.19) is found.
        # Old single-hop would only reach (40, 96.19) and miss the pin.
        conn_refs = {c["reference"] for c in result.connections}
        assert "R1" in conn_refs, f"BFS should reach R1 via 3 hops, got: {result.connections}"


# ---------------------------------------------------------------------------
# Tests: list_schematic_* expanded (5 new item types)
# ---------------------------------------------------------------------------


class TestListSchematicItemsExpanded:
    def test_hierarchical_labels(self, tmp_path: Path):
        from kiutils.items.schitems import HierarchicalLabel

        sch = new_schematic()
        sch.hierarchicalLabels.append(
            HierarchicalLabel(
                text="VIN",
                shape="input",
                position=Position(X=25.4, Y=30.0, angle=0),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )
        path = tmp_path / "hlabels.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = schematic.list_schematic_hierarchical_labels(schematic_path=str(path))
        assert len(result) == 1
        assert result[0].text == "VIN"
        assert result[0].shape == "input"
        assert result[0].x == 25.4

    def test_sheets(self, tmp_path: Path):
        from mcp_server_kicad import project

        parent = tmp_path / "root.kicad_sch"
        child = tmp_path / "child.kicad_sch"
        project.create_schematic(schematic_path=str(parent))
        project.create_schematic(schematic_path=str(child))
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name="Power",
            sheet_file=str(child),
            pins=[{"name": "VIN", "direction": "input"}],
        )

        result = schematic.list_schematic_sheets(schematic_path=str(parent))
        assert len(result) == 1
        assert result[0].sheet_name == "Power"
        assert result[0].file_name == "child.kicad_sch"
        assert result[0].pin_count == 1
        assert result[0].uuid

    def test_junctions(self, tmp_path: Path):
        from kiutils.items.common import ColorRGBA
        from kiutils.items.schitems import Junction

        sch = new_schematic()
        sch.junctions.append(
            Junction(
                position=Position(X=50, Y=50),
                diameter=0,
                color=ColorRGBA(R=0, G=0, B=0, A=0),
                uuid=_gen_uuid(),
            )
        )
        path = tmp_path / "junctions.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = schematic.list_schematic_junctions(schematic_path=str(path))
        assert len(result) == 1
        assert result[0].x == 50
        assert result[0].y == 50

    def test_no_connects(self, tmp_path: Path):
        from kiutils.items.schitems import NoConnect

        sch = new_schematic()
        sch.noConnects.append(
            NoConnect(
                position=Position(X=75, Y=80),
                uuid=_gen_uuid(),
            )
        )
        path = tmp_path / "noconn.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = schematic.list_schematic_no_connects(schematic_path=str(path))
        assert len(result) == 1
        assert result[0].x == 75
        assert result[0].y == 80

    def test_bus_entries(self, tmp_path: Path):
        from kiutils.items.schitems import BusEntry

        sch = new_schematic()
        sch.busEntries.append(
            BusEntry(
                position=Position(X=40, Y=60),
                size=Position(X=2.54, Y=2.54),
                uuid=_gen_uuid(),
            )
        )
        path = tmp_path / "bus.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = schematic.list_schematic_bus_entries(schematic_path=str(path))
        assert len(result) == 1
        assert result[0].x == 40
        assert result[0].size_x == 2.54

    def test_summary_includes_new_counts(self, tmp_path: Path):
        from kiutils.items.common import ColorRGBA
        from kiutils.items.schitems import HierarchicalLabel, Junction

        sch = new_schematic()
        sch.hierarchicalLabels.append(
            HierarchicalLabel(
                text="A",
                shape="input",
                position=Position(X=10, Y=10, angle=0),
                effects=_default_effects(),
                uuid=_gen_uuid(),
            )
        )
        sch.junctions.append(
            Junction(
                position=Position(X=20, Y=20),
                diameter=0,
                color=ColorRGBA(R=0, G=0, B=0, A=0),
                uuid=_gen_uuid(),
            )
        )
        path = tmp_path / "summary.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        result = schematic.get_schematic_summary(schematic_path=str(path))
        assert isinstance(result, SchematicSummary)
        assert result.hierarchical_labels == 1
        assert result.junctions == 1


# ---------------------------------------------------------------------------
# Tests: floating-point precision in _transform_pin_pos
# ---------------------------------------------------------------------------


class TestPinPositionPrecision:
    def test_no_floating_point_artifacts(self, tmp_path: Path):
        """_transform_pin_pos must return cleanly rounded coordinates, no IEEE 754 artifacts.

        Without rounding:
          132.08 + (-3.81) * cos(0) = 128.27 (happens to be clean)
          but 132.08 + 0 * sin(0) = 132.08 (clean)
          48.26 - 0 * sin(0) + (-3.81) * cos(0) = 44.449999... (artifact!)

        The artifact comes from float arithmetic on non-power-of-2 values.
        """
        from mcp_server_kicad.schematic import _transform_pin_pos

        # Resistor pin 1 at lib pos (0, 3.81), angle 270
        # Placed at (132.08, 48.26), rotation 0, no mirror
        x, y, _ = _transform_pin_pos(
            0,
            3.81,
            270,
            132.08,
            48.26,
            0,
            None,
        )
        # x and y should be cleanly representable to 4 decimal places
        assert x == round(x, 4), f"x={x!r} has FP artifact (expected {round(x, 4)})"
        assert y == round(y, 4), f"y={y!r} has FP artifact (expected {round(y, 4)})"

        # Resistor pin 2 at lib pos (0, -3.81), angle 90
        x2, y2, _ = _transform_pin_pos(
            0,
            -3.81,
            90,
            132.08,
            48.26,
            0,
            None,
        )
        assert x2 == round(x2, 4), f"x={x2!r} has FP artifact (expected {round(x2, 4)})"
        assert y2 == round(y2, 4), f"y={y2!r} has FP artifact (expected {round(y2, 4)})"

    def test_90_deg_rotation_no_artifacts(self, tmp_path: Path):
        """90-degree rotation should also produce clean coordinates."""
        from mcp_server_kicad.schematic import _transform_pin_pos

        # 48.26 + 5.08 * sin(90) = 53.339999999999996 without rounding
        x, y, _ = _transform_pin_pos(
            0,
            3.81,
            270,
            132.08,
            48.26,
            90,
            None,
        )
        assert x == round(x, 4), f"x={x!r} has FP artifact"
        assert y == round(y, 4), f"y={y!r} has FP artifact"


# ---------------------------------------------------------------------------
# Tests: multi-unit symbols
# ---------------------------------------------------------------------------


def _build_dual_gate_symbol() -> Symbol:
    """Build a 'DualGate' symbol: two gate units plus shared unit-0 power pins.

    Unit 0: pin 5 "GND" at (0, -7.62) and pin 6 "VCC" at (0, 7.62), which KiCad
    draws on every unit.  Unit 1: pin 1 "1A" at (-5.08, 0), pin 2 "1Y" at
    (5.08, 0).  Unit 2: pin 3 "2A" and pin 4 "2Y" at the same body-local
    coordinates as unit 1's pair, which is exactly what makes a unit mix-up
    visible: the sibling gate's input lands on top of this gate's input.
    """
    sym = Symbol()
    sym.entryName = "DualGate"
    sym.pinNamesOffset = 0
    sym.inBom = True
    sym.onBoard = True

    def _unit(unit_id: int, pins: list[tuple[str, str, float, float, float]]) -> Symbol:
        unit = Symbol()
        unit.entryName = "DualGate"
        unit.unitId = unit_id
        unit.styleId = 1
        unit.pins = [
            SymbolPin(
                electricalType="passive",
                position=Position(X=px, Y=py, angle=angle),
                length=2.54,
                name=name,
                number=number,
            )
            for number, name, px, py, angle in pins
        ]
        return unit

    sym.units = [
        _unit(0, [("5", "GND", 0, -7.62, 90), ("6", "VCC", 0, 7.62, 270)]),
        _unit(1, [("1", "1A", -5.08, 0, 0), ("2", "1Y", 5.08, 0, 180)]),
        _unit(2, [("3", "2A", -5.08, 0, 0), ("4", "2Y", 5.08, 0, 180)]),
    ]
    return sym


def _place_dual_gate(x: float, y: float, unit: int, numbers: tuple[str, ...]) -> SchematicSymbol:
    """Place one unit of U1 at (x, y)."""
    sym = SchematicSymbol()
    sym.libId = "Device:DualGate"
    sym.libName = "DualGate"
    sym.position = Position(X=x, Y=y, angle=0)
    sym.uuid = _gen_uuid()
    sym.unit = unit
    sym.inBom = True
    sym.onBoard = True
    sym.properties = [
        Property(
            key="Reference",
            value="U1",
            id=0,
            effects=_default_effects(),
            position=Position(X=x, Y=y - 10.16, angle=0),
        ),
        Property(
            key="Value",
            value="DualGate",
            id=1,
            effects=_default_effects(),
            position=Position(X=x, Y=y + 10.16, angle=0),
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
    sym.pins = {number: _gen_uuid() for number in numbers}
    return sym


def _make_dual_unit_sch(tmp_path: Path) -> str:
    """Both gates of U1 placed, with a net label on gate 1's input alone.

    Unit 1 sits at (100, 100), so pin 1 is at (94.92, 100); unit 2 sits at
    (150, 100), so pin 3 is at (144.92, 100).  The label is on pin 1.
    """
    sch = new_schematic()
    sch.libSymbols.append(_build_dual_gate_symbol())
    sch.schematicSymbols.append(_place_dual_gate(100, 100, unit=1, numbers=("1", "2", "5", "6")))
    sch.schematicSymbols.append(_place_dual_gate(150, 100, unit=2, numbers=("3", "4", "5", "6")))
    sch.labels.append(
        LocalLabel(
            text="GATE1_IN",
            position=Position(X=94.92, Y=100, angle=0),
            effects=_default_effects(),
            uuid=_gen_uuid(),
        )
    )
    path = tmp_path / "dual_unit.kicad_sch"
    sch.filePath = str(path)
    sch.to_file()
    return str(path)


class TestMultiUnitSymbols:
    def test_net_connections_excludes_a_sibling_units_pin(self, tmp_path: Path) -> None:
        """A label on gate 1's input must not also report gate 2's input.

        Both gates define their input at the same body-local coordinate, so
        scanning every unit against one placed instance puts pin 3 on top of
        pin 1 and the net gains a pin that is not on it.
        """
        result = schematic.get_net_connections("GATE1_IN", _make_dual_unit_sch(tmp_path))
        assert isinstance(result, NetConnectionsResult)
        pins = {(c["reference"], c["pin"]) for c in result.connections}
        assert ("U1", "1") in pins
        assert ("U1", "3") not in pins

    def test_pin_positions_use_each_unit_own_origin(self, tmp_path: Path) -> None:
        result = schematic.get_pin_positions("U1", _make_dual_unit_sch(tmp_path))
        pin_lines = [ln for ln in result.splitlines() if ln.strip().startswith("Pin ")]
        pin1 = [ln for ln in pin_lines if ln.strip().startswith("Pin 1 ")]
        pin3 = [ln for ln in pin_lines if ln.strip().startswith("Pin 3 ")]
        assert len(pin1) == 1 and "94.92" in pin1[0]
        assert len(pin3) == 1 and "144.92" in pin3[0]

    def test_pin_positions_report_every_placed_unit(self, tmp_path: Path) -> None:
        result = schematic.get_pin_positions("U1", _make_dual_unit_sch(tmp_path))
        heads = [ln for ln in result.splitlines() if ln.startswith("U1 ")]
        assert len(heads) == 2
        assert "unit=1" in heads[0]
        assert "unit=2" in heads[1]

    def test_unit_zero_pins_are_drawn_on_every_unit(self, tmp_path: Path) -> None:
        """Unit 0 holds what all units share, so VCC belongs to both instances."""
        result = schematic.get_pin_positions("U1", _make_dual_unit_sch(tmp_path))
        vcc = [ln for ln in result.splitlines() if ln.strip().startswith("Pin 6 ")]
        assert len(vcc) == 2
        assert any("(100" in ln for ln in vcc)
        assert any("(150" in ln for ln in vcc)

    def test_single_unit_output_is_unchanged(self, scratch_sch: Path) -> None:
        """One placed instance means no unit suffix: single-unit parts see no change."""
        result = schematic.get_pin_positions("R1", str(scratch_sch))
        assert "unit=" not in result.splitlines()[0]

    def test_lib_unit_id_reads_the_trailing_fields(self) -> None:
        """Symbol names contain underscores, so the unit is the second-last field."""
        node = _cst.parse(b'(symbol "SN74LVC2G17_2_1")').lists[0]
        assert schematic._lib_unit_id(node) == 2
        node = _cst.parse(b'(symbol "NotAUnitName")').lists[0]
        assert schematic._lib_unit_id(node) is None
