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
    make_demorgan_sch,
    make_dual_unit_sch,
    netlist_nodes,
    new_schematic,
    place_r1,
    requires_cli,
)
from kiutils.items.common import Effects, Font, Position, Property
from kiutils.items.schitems import Connection, LocalLabel, SchematicSymbol
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


class TestMultiUnitSymbols:
    """A placed symbol draws its own unit and body style plus what is common.

    Fixture (make_dual_unit_sch): U1 unit 1 at (100, 100), unit 2 at (150, 100);
    both gates put their input at the same body-local coordinate, so a unit
    mix-up lands pin 3 exactly on top of pin 1 rather than somewhere obviously
    wrong.  Shared power pins 5 and 6 live in unit 0.
    """

    def test_net_connections_excludes_a_sibling_units_pin(self, tmp_path: Path) -> None:
        """A label on gate 1's input must not also report gate 2's input."""
        result = schematic.get_net_connections("GATE1_IN", make_dual_unit_sch(tmp_path))
        assert isinstance(result, NetConnectionsResult)
        pins = [(c["reference"], c["pin"]) for c in result.connections]
        assert pins == [("U1", "1")]

    def test_pin_positions_use_each_unit_own_origin(self, tmp_path: Path) -> None:
        result = schematic.get_pin_positions("U1", make_dual_unit_sch(tmp_path))
        pin_lines = [ln for ln in result.splitlines() if ln.strip().startswith("Pin ")]
        pin1 = [ln for ln in pin_lines if ln.strip().startswith("Pin 1 ")]
        pin3 = [ln for ln in pin_lines if ln.strip().startswith("Pin 3 ")]
        assert len(pin1) == 1 and "94.92" in pin1[0]
        assert len(pin3) == 1 and "144.92" in pin3[0]

    def test_pin_positions_report_every_placed_unit(self, tmp_path: Path) -> None:
        result = schematic.get_pin_positions("U1", make_dual_unit_sch(tmp_path))
        heads = [ln for ln in result.splitlines() if ln.startswith("U1 ")]
        assert len(heads) == 2
        assert "unit=1" in heads[0]
        assert "unit=2" in heads[1]

    def test_unit_zero_pins_are_drawn_on_every_unit(self, tmp_path: Path) -> None:
        """Unit 0 holds what all units share, so VCC belongs to both instances."""
        result = schematic.get_pin_positions("U1", make_dual_unit_sch(tmp_path))
        vcc = [ln for ln in result.splitlines() if ln.strip().startswith("Pin 6 ")]
        assert len(vcc) == 2
        assert any("(100" in ln for ln in vcc)
        assert any("(150" in ln for ln in vcc)

    def test_single_unit_output_is_unchanged(self, scratch_sch: Path) -> None:
        """One placed instance means no unit suffix: single-unit parts see no change."""
        result = schematic.get_pin_positions("R1", str(scratch_sch))
        assert "unit=" not in result.splitlines()[0]

    def test_a_placed_symbol_without_a_unit_token_is_unit_one(self, tmp_path: Path) -> None:
        """KiCad reads a symbol with no (unit N) as unit 1 (SCH_SYMBOL::Init); so do we."""
        path = Path(make_dual_unit_sch(tmp_path))
        # The instance data repeats (unit 1), so remove the placed node's own token.
        tree = _cst.parse(path.read_bytes())
        gate1 = next(s for s in tree.lists[0].find_all("symbol") if schematic._sym_unit_cst(s) == 1)
        gate1.remove_child(gate1.find("unit"))
        path.write_bytes(_cst.serialize(tree))
        result = schematic.get_pin_positions("U1", str(path))
        pin_lines = [ln.strip() for ln in result.splitlines() if ln.strip().startswith("Pin ")]
        assert [ln for ln in pin_lines if ln.startswith("Pin 1 ")] == ["Pin 1 (1A): (94.92, 100.0)"]
        assert [ln for ln in pin_lines if ln.startswith("Pin 3 ")] == [
            "Pin 3 (2A): (144.92, 100.0)"
        ]

    @requires_cli
    def test_net_connections_match_kicad_netlist(self, tmp_path: Path) -> None:
        path = make_dual_unit_sch(tmp_path)
        result = schematic.get_net_connections("GATE1_IN", path)
        assert isinstance(result, NetConnectionsResult)
        ours = {(c["reference"], c["pin"]) for c in result.connections}
        assert ours == set(netlist_nodes(path)["GATE1_IN"]) == {("U1", "1")}


class TestBodyStyles:
    """Body style is the other half of KiCad's rule: ``_1_1`` and ``_1_2`` both
    say unit 1, and a placed symbol draws only the style it names (1 when it
    names none), plus style 0, which is common to both.  Stock KiCad 9.0.8
    ships 44 symbols with a De Morgan alternate, 31 of them in 74xx (derived
    symbols included; counted 2026-10-01).
    """

    def test_a_demorgan_part_reports_each_pin_once(self, tmp_path: Path) -> None:
        result = schematic.get_pin_positions("U1", make_demorgan_sch(tmp_path))
        pin_lines = [ln.strip() for ln in result.splitlines() if ln.strip().startswith("Pin ")]
        numbers = sorted(ln.split()[1] for ln in pin_lines)
        assert numbers == ["1", "14", "2", "3", "7"]

    @pytest.mark.parametrize("body_style", [1, 2])
    def test_the_placed_style_decides_where_a_pin_is(self, tmp_path: Path, body_style: int) -> None:
        """The two styles draw pin 1 at different places; the placed one is reported."""
        result = schematic.get_pin_positions("U1", make_demorgan_sch(tmp_path, body_style))
        pin1 = [ln.strip() for ln in result.splitlines() if ln.strip().startswith("Pin 1 ")]
        expected = {1: "Pin 1 (A): (92.38, 97.46)", 2: "Pin 1 (A): (89.84, 94.92)"}
        assert pin1 == [expected[body_style]]

    @pytest.mark.parametrize("body_style", [1, 2])
    def test_a_label_on_the_undrawn_style_connects_nothing(
        self, tmp_path: Path, body_style: int
    ) -> None:
        """NAND_A sits on the drawn style's pin 1, PHANTOM on the other style's."""
        path = make_demorgan_sch(tmp_path, body_style)
        drawn = schematic.get_net_connections("NAND_A", path)
        phantom = schematic.get_net_connections("PHANTOM", path)
        assert isinstance(drawn, NetConnectionsResult)
        assert isinstance(phantom, NetConnectionsResult)
        assert [(c["reference"], c["pin"]) for c in drawn.connections] == [("U1", "1")]
        assert phantom.connections == []

    @pytest.mark.parametrize("body_style", [1, 2])
    def test_style_zero_power_pins_are_drawn_in_either_style(
        self, tmp_path: Path, body_style: int
    ) -> None:
        """``_2_0`` is the power unit in style 0, drawn whichever style is placed."""
        result = schematic.get_pin_positions("U1", make_demorgan_sch(tmp_path, body_style))
        pin_lines = [ln.strip() for ln in result.splitlines() if ln.strip().startswith("Pin ")]
        assert "Pin 7 (GND): (150.0, 107.62)" in pin_lines
        assert "Pin 14 (VCC): (150.0, 92.38)" in pin_lines

    @requires_cli
    @pytest.mark.parametrize("body_style", [1, 2])
    def test_demorgan_connections_match_kicad_netlist(
        self, tmp_path: Path, body_style: int
    ) -> None:
        path = make_demorgan_sch(tmp_path, body_style)
        nets = netlist_nodes(path)
        assert set(nets["NAND_A"]) == {("U1", "1")}
        assert set(nets.get("PHANTOM", {})) == set()
        drawn = schematic.get_net_connections("NAND_A", path)
        assert isinstance(drawn, NetConnectionsResult)
        assert {(c["reference"], c["pin"]) for c in drawn.connections} == set(nets["NAND_A"])

    def test_placed_symbol_tokens(self) -> None:
        """KiCad 10 writes (body_style N) always; KiCad 9 writes (convert N) for De Morgan only."""
        bare = _cst.parse(b'(symbol (lib_id "Device:R") (at 0 0 0))').lists[0]
        assert schematic._sym_unit_cst(bare) == 1
        assert schematic._sym_body_style_cst(bare) == 1
        k10 = _cst.parse(b'(symbol (lib_id "x") (at 0 0 0) (unit 2) (body_style 2))').lists[0]
        assert (schematic._sym_unit_cst(k10), schematic._sym_body_style_cst(k10)) == (2, 2)
        k9 = _cst.parse(b'(symbol (lib_id "x") (at 0 0 0) (unit 1) (convert 2))').lists[0]
        assert (schematic._sym_unit_cst(k9), schematic._sym_body_style_cst(k9)) == (1, 2)

    def test_lib_unit_style_reads_the_trailing_fields(self) -> None:
        """Symbol names contain underscores, so unit and style are the last two fields."""
        node = _cst.parse(b'(symbol "SN74LVC2G17_2_1")').lists[0]
        assert schematic._lib_unit_style(node) == (2, 1)
        node = _cst.parse(b'(symbol "74LS00_5_0")').lists[0]
        assert schematic._lib_unit_style(node) == (5, 0)
        node = _cst.parse(b'(symbol "NotAUnitName")').lists[0]
        assert schematic._lib_unit_style(node) is None

    def test_instance_units_is_kicad_rule(self) -> None:
        """LIB_SYMBOL::GetPins: unit in (this, 0) and style in (this, 0), both at once.

        0 means common on that axis only: ``X_0_1`` is shared by every unit but
        belongs to body style 1, so a style-2 placement does not draw it, while
        ``X_0_0`` is drawn by every placement.
        """
        lib = _cst.parse(
            b'(symbol "X" (symbol "X_0_0") (symbol "X_0_1") (symbol "X_1_1")'
            b' (symbol "X_1_2") (symbol "X_2_0") (symbol "X_2_1"))'
        ).lists[0]

        def drawn(unit: int, style: int) -> list[str]:
            return [s.atoms[1].text for s in schematic._instance_units(lib, unit, style)]

        assert drawn(1, 1) == ["X_0_0", "X_0_1", "X_1_1"]
        assert drawn(1, 2) == ["X_0_0", "X_1_2"]
        assert drawn(2, 1) == ["X_0_0", "X_0_1", "X_2_0", "X_2_1"]
        assert drawn(2, 2) == ["X_0_0", "X_2_0"]

    def test_refusal_names_the_body_style_when_that_is_what_differs(self) -> None:
        """A pin only the unplaced style draws: the remedy is the style, not a unit.

        Unit 0 is drawn by every placed unit, so a unit-0 pin missing from the
        sheet is missing for its body style as well.
        """
        root = _cst.parse(
            b'(kicad_sch (lib_symbols (symbol "L:Odd"'
            b' (symbol "Odd_1_1" (pin passive line (at -5.08 0 0) (length 2.54)'
            b' (name "A") (number "1")))'
            b' (symbol "Odd_1_2" (pin passive line (at -5.08 0 0) (length 2.54)'
            b' (name "A") (number "1")) (pin passive line (at 5.08 0 180) (length 2.54)'
            b' (name "X") (number "9")))'
            b' (symbol "Odd_0_2" (pin passive line (at 0 5.08 270) (length 2.54)'
            b' (name "C") (number "7")))))'
            b' (symbol (lib_id "L:Odd") (at 100 100 0) (unit 1) (property "Reference" "U9")))'
        ).lists[0]
        with pytest.raises(ValueError, match=r"on unit 1 body style 2, which this sheet does not"):
            schematic._get_pin_pos_cst(root, "U9", "9")
        with pytest.raises(ValueError, match=r"on body style 2 \(common to all units\).*Switch"):
            schematic._get_pin_pos_cst(root, "U9", "7")
