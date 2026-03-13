"""Tests that connectivity tools preserve exact coordinates instead of snapping.

The _snap_grid function rounds to the nearest 1.27mm grid point, which
destroys coordinate precision when tools receive exact pin positions
(e.g. from get_pin_positions).  These tests verify that add_label,
add_wires, add_global_label, add_junctions, no_connect_pin, and
wire_pins_to_net all preserve the caller's coordinates faithfully.
"""

from __future__ import annotations

import pytest
from conftest import reparse

from mcp_server_kicad import schematic

# R1 at (100, 100) with Device:R symbol:
#   Pin 1 at (0, 3.81) angle 270 → absolute (100, 96.19)
#   Pin 2 at (0, -3.81) angle 90 → absolute (100, 103.81)
# 96.19 / 1.27 = 75.74 — NOT on the 1.27mm grid.
# _snap_grid(96.19) = 96.52, a 0.33mm error.


class TestAddLabelPreservesCoordinates:
    def test_off_grid_coordinates_preserved(self, scratch_sch):
        """add_label must NOT snap off-grid coordinates to the 1.27mm grid."""
        schematic.add_label(text="OFF_GRID", x=96.19, y=100.5, schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        label = next(lbl for lbl in sch.labels if lbl.text == "OFF_GRID")
        assert label.position.X == pytest.approx(96.19)
        assert label.position.Y == pytest.approx(100.5)

    def test_on_grid_coordinates_unchanged(self, scratch_sch):
        """On-grid coordinates should remain unchanged (sanity check)."""
        # 76.2 = 60 * 1.27, 66.04 = 52 * 1.27 — both on the 1.27mm grid
        schematic.add_label(text="ON_GRID", x=76.2, y=66.04, schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        label = next(lbl for lbl in sch.labels if lbl.text == "ON_GRID")
        assert label.position.X == pytest.approx(76.2)
        assert label.position.Y == pytest.approx(66.04)


class TestAddWiresPreservesCoordinates:
    def test_off_grid_coordinates_preserved(self, scratch_sch):
        """add_wires must NOT snap off-grid coordinates."""
        from kiutils.items.schitems import Connection

        schematic.add_wires(
            wires=[{"x1": 96.19, "y1": 100.5, "x2": 103.81, "y2": 100.5}],
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        wires = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"]
        # Last wire is the one we just added
        new_wire = wires[-1]
        assert new_wire.points[0].X == pytest.approx(96.19)
        assert new_wire.points[0].Y == pytest.approx(100.5)
        assert new_wire.points[1].X == pytest.approx(103.81)
        assert new_wire.points[1].Y == pytest.approx(100.5)


class TestAddGlobalLabelPreservesCoordinates:
    def test_off_grid_coordinates_preserved(self, scratch_sch):
        """add_global_label must NOT snap off-grid coordinates."""
        schematic.add_global_label(
            text="GL_TEST", x=96.19, y=100.5, schematic_path=str(scratch_sch)
        )
        sch = reparse(str(scratch_sch))
        gl = next(gl for gl in sch.globalLabels if gl.text == "GL_TEST")
        assert gl.position.X == pytest.approx(96.19)
        assert gl.position.Y == pytest.approx(100.5)


class TestAddJunctionsPreservesCoordinates:
    def test_off_grid_coordinates_preserved(self, scratch_sch):
        """add_junctions must NOT snap off-grid coordinates."""
        schematic.add_junctions(points=[{"x": 96.19, "y": 100.5}], schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        junc = sch.junctions[-1]
        assert junc.position.X == pytest.approx(96.19)
        assert junc.position.Y == pytest.approx(100.5)


class TestNoConnectPinExactPosition:
    def test_no_connect_at_exact_pin_position(self, scratch_sch):
        """no_connect_pin must place flag at exact pin position, not snapped.

        R1 pin 1 is at (100, 96.19). _snap_grid(96.19) = 96.52.
        The no-connect flag must be at 96.19, not 96.52.
        """
        schematic.no_connect_pin(reference="R1", pin_name="1", schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        nc = sch.noConnects[0]
        assert nc.position.X == pytest.approx(100.0)
        assert nc.position.Y == pytest.approx(96.19)


class TestWirePinsToNetExactStub:
    def test_stub_exact_length(self, scratch_sch):
        """wire_pins_to_net stub must be exact length, not snapped.

        R1 pin 1 at (100, 96.19), going up by 2.54mm.
        Endpoint should be (100, 93.65), NOT _snap_grid(93.65) = 93.98.
        """
        from kiutils.items.schitems import Connection

        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="STUB_TEST",
            direction="up",
            stub_length=2.54,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        wires = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"]
        # Find the stub wire near R1 pin 1 (x ≈ 100)
        stub_wire = [w for w in wires if any(abs(p.X - 100) < 0.01 for p in w.points)][-1]
        # Wire should span exactly 2.54mm vertically
        dy = abs(stub_wire.points[0].Y - stub_wire.points[1].Y)
        assert dy == pytest.approx(2.54)

    def test_label_at_exact_stub_endpoint(self, scratch_sch):
        """Label must be at the exact wire stub endpoint."""

        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="LABEL_POS",
            direction="up",
            stub_length=2.54,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        label = next(lbl for lbl in sch.labels if lbl.text == "LABEL_POS")
        # Label Y should be at pin_y - stub_length = 96.19 - 2.54 = 93.65
        assert label.position.Y == pytest.approx(93.65)
