"""Tests for the 5 read-only tools in schematic.py.

Covers: list_components, list_labels, list_wires, get_symbol_pins, get_pin_positions.
"""

from __future__ import annotations

from pathlib import Path

from mcp_server_kicad import schematic
from conftest import (
    _gen_uuid,
    _default_effects,
    build_r_symbol,
    new_schematic,
    place_r1,
)

from kiutils.items.common import Effects, Font, Position, Property
from kiutils.items.schitems import SchematicSymbol


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
            key="Reference", value="R1", id=0,
            effects=_default_effects(),
            position=Position(X=100, Y=96.19, angle=0),
        ),
        Property(
            key="Value", value="10K", id=1,
            effects=_default_effects(),
            position=Position(X=100, Y=103.81, angle=0),
        ),
        Property(
            key="Footprint", value="", id=2,
            effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
            position=Position(X=100, Y=100, angle=0),
        ),
        Property(
            key="Datasheet", value="~", id=3,
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
# Tests: list_components
# ---------------------------------------------------------------------------

class TestListComponents:
    def test_returns_preplaced_r1(self, scratch_sch: Path) -> None:
        result = schematic.list_components(str(scratch_sch))
        assert "R1" in result
        assert "10K" in result
        assert "Device:R" in result

    def test_empty_schematic(self, empty_sch: Path) -> None:
        result = schematic.list_components(str(empty_sch))
        assert result == "No components found."


# ---------------------------------------------------------------------------
# Tests: list_labels
# ---------------------------------------------------------------------------

class TestListLabels:
    def test_returns_preplaced_label(self, scratch_sch: Path) -> None:
        result = schematic.list_labels(str(scratch_sch))
        assert "TEST_NET" in result

    def test_empty_schematic(self, empty_sch: Path) -> None:
        result = schematic.list_labels(str(empty_sch))
        assert result == "No labels found."


# ---------------------------------------------------------------------------
# Tests: list_wires
# ---------------------------------------------------------------------------

class TestListWires:
    def test_returns_preplaced_wire(self, scratch_sch: Path) -> None:
        result = schematic.list_wires(str(scratch_sch))
        # Wire goes from (50, 50) to (80, 50)
        assert "50" in result
        assert "80" in result

    def test_empty_schematic(self, empty_sch: Path) -> None:
        result = schematic.list_wires(str(empty_sch))
        assert result == "No wires found."


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
        result = schematic.get_symbol_pins("NonExistent", str(scratch_sch))
        assert "not found" in result


# ---------------------------------------------------------------------------
# Tests: get_pin_positions
# ---------------------------------------------------------------------------

class TestGetPinPositions:
    def test_rotation_0(self, scratch_sch: Path) -> None:
        """Default rotation: Pin 1 at (100, 103.81), Pin 2 at (100, 96.19)."""
        result = schematic.get_pin_positions("R1", str(scratch_sch))
        assert "100" in result
        assert "103.81" in result
        assert "96.19" in result

    def test_rotation_90(self, tmp_path: Path) -> None:
        """90 deg CCW: Pin 1 (0,3.81) -> (-3.81, 0) + (100,100) = (96.19, 100).
        Pin 2 (0,-3.81) -> (3.81, 0) + (100,100) = (103.81, 100).
        """
        path = _make_rotated_sch(tmp_path, rotation=90)
        result = schematic.get_pin_positions("R1", path)
        assert "96.19" in result
        assert "103.81" in result

    def test_rotation_180(self, tmp_path: Path) -> None:
        """180 deg: Pin 1 (0,3.81) -> (0, -3.81) + (100,100) = (100, 96.19).
        Pin 2 (0,-3.81) -> (0, 3.81) + (100,100) = (100, 103.81).
        """
        path = _make_rotated_sch(tmp_path, rotation=180)
        result = schematic.get_pin_positions("R1", path)
        assert "96.19" in result
        assert "103.81" in result

    def test_rotation_270(self, tmp_path: Path) -> None:
        """270 deg CCW: Pin 1 (0,3.81) -> (3.81, 0) + (100,100) = (103.81, 100).
        Pin 2 (0,-3.81) -> (-3.81, 0) + (100,100) = (96.19, 100).
        """
        path = _make_rotated_sch(tmp_path, rotation=270)
        result = schematic.get_pin_positions("R1", path)
        assert "103.81" in result
        assert "96.19" in result

    def test_mirror_x(self, tmp_path: Path) -> None:
        """Mirror x negates py before rotation (rot=0).
        Pin 1: (0, -3.81) + (100,100) = (100, 96.19).
        Pin 2: (0, 3.81) + (100,100) = (100, 103.81).
        """
        path = _make_rotated_sch(tmp_path, rotation=0, mirror="x")
        result = schematic.get_pin_positions("R1", path)
        assert "96.19" in result
        assert "103.81" in result
        # With mirror x, pin 1 and pin 2 swap positions vs rotation_0
        # Pin 1 should be at y=96.19, Pin 2 at y=103.81
        lines = result.strip().split("\n")
        pin_lines = [l for l in lines if l.strip().startswith("Pin")]
        pin1_line = [l for l in pin_lines if "Pin 1" in l][0]
        pin2_line = [l for l in pin_lines if "Pin 2" in l][0]
        assert "96.19" in pin1_line
        assert "103.81" in pin2_line

    def test_mirror_y(self, tmp_path: Path) -> None:
        """Mirror y negates px (which is 0 for a vertical resistor, no visible change).
        Pin positions same as rotation_0.
        Pin 1: (100, 103.81), Pin 2: (100, 96.19).
        """
        path = _make_rotated_sch(tmp_path, rotation=0, mirror="y")
        result = schematic.get_pin_positions("R1", path)
        assert "103.81" in result
        assert "96.19" in result
        # Same positions as rotation_0 since px=0 for both pins
        lines = result.strip().split("\n")
        pin_lines = [l for l in lines if l.strip().startswith("Pin")]
        pin1_line = [l for l in pin_lines if "Pin 1" in l][0]
        pin2_line = [l for l in pin_lines if "Pin 2" in l][0]
        assert "103.81" in pin1_line
        assert "96.19" in pin2_line

    def test_unknown_reference(self, scratch_sch: Path) -> None:
        result = schematic.get_pin_positions("X99", str(scratch_sch))
        assert "not found" in result
