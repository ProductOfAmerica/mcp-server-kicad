"""Tests for mutating (write) tools in schematic.py."""

from __future__ import annotations

import shutil

import pytest
from conftest import assert_erc_clean, reparse
from kiutils.items.schitems import Connection

from mcp_server_kicad import schematic

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_refs(sch) -> list[str | None]:
    """Extract reference designators from schematic symbols."""
    return [
        next((p.value for p in s.properties if p.key == "Reference"), None)
        for s in sch.schematicSymbols
    ]


def _find_symbol(sch, reference: str):
    """Find a SchematicSymbol by reference designator."""
    for s in sch.schematicSymbols:
        ref = next((p.value for p in s.properties if p.key == "Reference"), None)
        if ref == reference:
            return s
    return None


def _count_wires(sch) -> int:
    """Count wires in graphicalItems."""
    return len([g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"])


# ===========================================================================
# TestPlaceComponent
# ===========================================================================


class TestPlaceComponent:
    def test_basic_placement(self, scratch_sch):
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="4.7K",
            x=150,
            y=150,
            schematic_path=str(scratch_sch),
        )
        assert "R2" in result

        sch = reparse(str(scratch_sch))
        refs = _get_refs(sch)
        assert "R2" in refs

        r2 = _find_symbol(sch, "R2")
        assert r2 is not None
        assert r2.libId == "Device:R"
        val = next((p.value for p in r2.properties if p.key == "Value"), None)
        assert val == "4.7K"

    @pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
    def test_placement_erc(self, scratch_sch):
        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="4.7K",
            x=150,
            y=150,
            schematic_path=str(scratch_sch),
        )
        assert_erc_clean(scratch_sch)

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_rotation(self, scratch_sch, rotation):
        schematic.place_component(
            lib_id="Device:R",
            reference="R3",
            value="1K",
            x=200,
            y=200,
            rotation=rotation,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        r3 = _find_symbol(sch, "R3")
        assert r3 is not None
        assert r3.position.angle == rotation

    def test_mirror_x(self, scratch_sch):
        schematic.place_component(
            lib_id="Device:R",
            reference="R4",
            value="2.2K",
            x=200,
            y=200,
            mirror="x",
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        r4 = _find_symbol(sch, "R4")
        assert r4 is not None
        assert r4.mirror == "x"

    def test_mirror_y(self, scratch_sch):
        schematic.place_component(
            lib_id="Device:R",
            reference="R5",
            value="3.3K",
            x=200,
            y=200,
            mirror="y",
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        r5 = _find_symbol(sch, "R5")
        assert r5 is not None
        assert r5.mirror == "y"

    def test_custom_lib(self, scratch_sch, scratch_sym_lib):
        schematic.place_component(
            lib_id="test:TestPart",
            reference="U1",
            value="TestPart",
            x=250,
            y=250,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))

        # TestPart should be in libSymbols
        lib_names = [ls.entryName for ls in sch.libSymbols]
        assert "TestPart" in lib_names

        # U1 should be placed
        refs = _get_refs(sch)
        assert "U1" in refs

    def test_pin_uuids_assigned(self, scratch_sch):
        schematic.place_component(
            lib_id="Device:R",
            reference="R6",
            value="100",
            x=300,
            y=300,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        r6 = _find_symbol(sch, "R6")
        assert r6 is not None
        assert isinstance(r6.pins, dict)
        assert len(r6.pins) == 2
        assert "1" in r6.pins
        assert "2" in r6.pins


# ===========================================================================
# TestRemoveComponent
# ===========================================================================


class TestRemoveComponent:
    def test_remove_existing(self, scratch_sch):
        # Verify R1 exists first
        sch = reparse(str(scratch_sch))
        assert "R1" in _get_refs(sch)

        result = schematic.remove_component(
            reference="R1",
            schematic_path=str(scratch_sch),
        )
        assert "Removed" in result

        sch = reparse(str(scratch_sch))
        assert "R1" not in _get_refs(sch)

    def test_remove_missing(self, scratch_sch):
        result = schematic.remove_component(
            reference="R999",
            schematic_path=str(scratch_sch),
        )
        assert "not found" in result.lower()


# ===========================================================================
# TestAddWire
# ===========================================================================


class TestAddWire:
    def test_basic_wire(self, scratch_sch):
        sch_before = reparse(str(scratch_sch))
        count_before = _count_wires(sch_before)

        schematic.add_wire(
            x1=100,
            y1=100,
            x2=200,
            y2=100,
            schematic_path=str(scratch_sch),
        )

        sch_after = reparse(str(scratch_sch))
        count_after = _count_wires(sch_after)
        assert count_after == count_before + 1

    @pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
    def test_wire_erc(self, scratch_sch):
        schematic.add_wire(
            x1=100,
            y1=100,
            x2=200,
            y2=100,
            schematic_path=str(scratch_sch),
        )
        assert_erc_clean(scratch_sch)

    def test_zero_length_wire(self, scratch_sch):
        # A zero-length wire should not crash
        schematic.add_wire(
            x1=50,
            y1=50,
            x2=50,
            y2=50,
            schematic_path=str(scratch_sch),
        )
        # Should be able to reparse without error
        sch = reparse(str(scratch_sch))
        assert sch is not None


# ===========================================================================
# TestAddWires
# ===========================================================================


class TestAddWires:
    def test_batch(self, scratch_sch):
        sch_before = reparse(str(scratch_sch))
        count_before = _count_wires(sch_before)

        wires = [
            {"x1": 10, "y1": 10, "x2": 20, "y2": 10},
            {"x1": 20, "y1": 10, "x2": 30, "y2": 10},
            {"x1": 30, "y1": 10, "x2": 40, "y2": 10},
        ]
        schematic.add_wires(
            wires=wires,
            schematic_path=str(scratch_sch),
        )

        sch_after = reparse(str(scratch_sch))
        count_after = _count_wires(sch_after)
        assert count_after == count_before + 3

    def test_empty_list(self, scratch_sch):
        result = schematic.add_wires(
            wires=[],
            schematic_path=str(scratch_sch),
        )
        assert "0" in result

        # Reparse should work fine
        sch = reparse(str(scratch_sch))
        assert sch is not None


# ===========================================================================
# TestAddLabel
# ===========================================================================


class TestAddLabel:
    def test_basic_label(self, scratch_sch):
        schematic.add_label(
            text="MY_NET",
            x=60,
            y=60,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        label_texts = [lbl.text for lbl in sch.labels]
        assert "MY_NET" in label_texts

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_rotation(self, scratch_sch, rotation):
        schematic.add_label(
            text="ROT_LABEL",
            x=70,
            y=70,
            rotation=rotation,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        # Find the label we just added
        rot_label = next(
            (lbl for lbl in sch.labels if lbl.text == "ROT_LABEL"),
            None,
        )
        assert rot_label is not None
        assert rot_label.position.angle == rotation


# ===========================================================================
# TestAddJunction
# ===========================================================================


class TestAddJunction:
    def test_basic_junction(self, scratch_sch):
        schematic.add_junction(
            x=50,
            y=50,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        # Find a junction at (50, 50)
        found = any(j.position.X == 50 and j.position.Y == 50 for j in sch.junctions)
        assert found


# ===========================================================================
# TestAddJunctions
# ===========================================================================


class TestAddJunctions:
    def test_batch(self, scratch_sch):
        sch_before = reparse(str(scratch_sch))
        junctions_before = len(sch_before.junctions)

        points = [
            {"x": 10, "y": 10},
            {"x": 20, "y": 20},
            {"x": 30, "y": 30},
        ]
        schematic.add_junctions(
            points=points,
            schematic_path=str(scratch_sch),
        )

        sch_after = reparse(str(scratch_sch))
        junctions_after = len(sch_after.junctions)
        assert junctions_after == junctions_before + 3

    def test_empty_list(self, scratch_sch):
        result = schematic.add_junctions(
            points=[],
            schematic_path=str(scratch_sch),
        )
        assert "0" in result
