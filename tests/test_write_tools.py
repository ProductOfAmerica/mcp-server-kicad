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

    def test_instances_block_created(self, scratch_sch):
        """place_component must create an instances block with the schematic UUID."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R7",
            value="47K",
            x=127,
            y=127,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        r7 = _find_symbol(sch, "R7")
        assert r7 is not None
        assert len(r7.instances) == 1
        inst = r7.instances[0]
        assert len(inst.paths) == 1
        assert inst.paths[0].reference == "R7"
        assert inst.paths[0].unit == 1
        assert inst.paths[0].sheetInstancePath == f"/{sch.uuid}"

    def test_grid_snapping(self, scratch_sch):
        """Off-grid coordinates are snapped to nearest 1.27mm multiple."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R8",
            value="1M",
            x=100,  # 100 / 1.27 = 78.74 -> 79 -> 100.33
            y=200,  # 200 / 1.27 = 157.48 -> 157 -> 199.39
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        r8 = _find_symbol(sch, "R8")
        assert r8 is not None
        assert r8.position.X == 100.33
        assert r8.position.Y == 199.39

    def test_on_grid_unchanged(self, scratch_sch):
        """Coordinates already on the 1.27mm grid are not modified."""
        # 101.6 == 80*1.27, exactly on grid
        schematic.place_component(
            lib_id="Device:R",
            reference="R9",
            value="330",
            x=101.6,
            y=101.6,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        r9 = _find_symbol(sch, "R9")
        assert r9 is not None
        assert r9.position.X == 101.6
        assert r9.position.Y == 101.6

    def test_auto_embeds_lib_symbol(self, empty_sch):
        """place_component auto-embeds lib_symbol from system library."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="10K",
            x=100,
            y=100,
            schematic_path=str(empty_sch),
        )
        assert "R1" in result
        sch = reparse(str(empty_sch))
        # lib_symbol should be embedded — wire_pin_to_label should work
        lib_names = [ls.entryName for ls in sch.libSymbols]
        assert "R" in lib_names

    def test_auto_embed_then_wire(self, empty_sch):
        """place_component + wire_pin_to_label works without add_lib_symbol."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="10K",
            x=100,
            y=100,
            schematic_path=str(empty_sch),
        )
        # This should NOT raise "Lib symbol not found"
        result = schematic.wire_pin_to_label(
            reference="R1",
            pin_name="1",
            label_text="VCC",
            direction="up",
            schematic_path=str(empty_sch),
        )
        assert "VCC" in result

    def test_auto_embed_skips_if_already_present(self, scratch_sch):
        """Don't duplicate lib_symbol if it's already in the schematic."""
        sch_before = reparse(str(scratch_sch))
        lib_count_before = len(sch_before.libSymbols)

        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="4.7K",
            x=150,
            y=150,
            schematic_path=str(scratch_sch),
        )
        sch_after = reparse(str(scratch_sch))
        assert len(sch_after.libSymbols) == lib_count_before


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
            x=50.8,
            y=50.8,
            schematic_path=str(scratch_sch),
        )
        sch = reparse(str(scratch_sch))
        # 50.8 == 40*1.27, already on grid
        found = any(j.position.X == 50.8 and j.position.Y == 50.8 for j in sch.junctions)
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


# ===========================================================================
# TestAddPowerSymbol
# ===========================================================================


class TestAddPowerSymbol:
    def test_pwr_flag_no_duplicate(self, empty_sch, scratch_power_lib):
        """Placing PWR_FLAG should NOT auto-create a second PWR_FLAG."""
        schematic.add_power_symbol(
            lib_id="power:PWR_FLAG",
            reference="#FLG01",
            x=100,
            y=100,
            symbol_lib_path=str(scratch_power_lib),
            schematic_path=str(empty_sch),
        )
        sch = reparse(str(empty_sch))
        flg_refs = [
            next((p.value for p in s.properties if p.key == "Reference"), "")
            for s in sch.schematicSymbols
            if any(p.value == "PWR_FLAG" for p in s.properties if p.key == "Value")
        ]
        # Should be exactly 1 PWR_FLAG, not 2
        assert len(flg_refs) == 1
        assert flg_refs[0] == "#FLG01"

    def test_vcc_gets_auto_pwr_flag(self, empty_sch, scratch_power_lib):
        """Placing VCC should auto-create a PWR_FLAG."""
        schematic.add_power_symbol(
            lib_id="power:VCC",
            reference="#PWR01",
            x=100,
            y=100,
            symbol_lib_path=str(scratch_power_lib),
            schematic_path=str(empty_sch),
        )
        sch = reparse(str(empty_sch))
        refs = [
            next((p.value for p in s.properties if p.key == "Reference"), "")
            for s in sch.schematicSymbols
        ]
        assert "#PWR01" in refs
        assert any(r.startswith("#FLG") for r in refs)
