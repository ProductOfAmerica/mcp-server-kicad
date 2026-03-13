"""Tests for mutating (write) tools in schematic.py."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import assert_erc_clean, build_r_symbol, new_schematic, reparse
from kiutils.items.schitems import Connection

from mcp_server_kicad import schematic

HAS_KICAD_CLI = shutil.which("kicad-cli") is not None
HAS_KICAD_LIBS = any(
    (p / "Device.kicad_sym").exists()
    for p in [
        __import__("pathlib").Path("/usr/share/kicad/symbols"),
        __import__("pathlib").Path("/usr/local/share/kicad/symbols"),
    ]
)

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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            x=200,
            y=150,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            x=200,
            y=150,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        sch = reparse(str(scratch_sch))
        r9 = _find_symbol(sch, "R9")
        assert r9 is not None
        assert r9.position.X == 101.6
        assert r9.position.Y == 101.6

    @pytest.mark.skipif(not HAS_KICAD_LIBS, reason="KiCad system libraries not installed")
    def test_auto_embeds_lib_symbol(self, empty_sch):
        """place_component auto-embeds lib_symbol from system library."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="10K",
            x=100,
            y=100,
            schematic_path=str(empty_sch),
            project_path=str(empty_sch.with_suffix(".kicad_pro")),
        )
        assert "R1" in result
        sch = reparse(str(empty_sch))
        # lib_symbol should be embedded — wire_pin_to_label should work
        lib_names = [ls.entryName for ls in sch.libSymbols]
        assert "R" in lib_names

    @pytest.mark.skipif(not HAS_KICAD_LIBS, reason="KiCad system libraries not installed")
    def test_auto_embed_then_wire(self, empty_sch):
        """place_component + wire_pin_to_label works without add_lib_symbol."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="10K",
            x=100,
            y=100,
            schematic_path=str(empty_sch),
            project_path=str(empty_sch.with_suffix(".kicad_pro")),
        )
        # This should NOT raise "Lib symbol not found"
        result = schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
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
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        sch_after = reparse(str(scratch_sch))
        assert len(sch_after.libSymbols) == lib_count_before

    @pytest.mark.skipif(not HAS_KICAD_LIBS, reason="KiCad system libraries not installed")
    def test_missing_symbol_suggests_alternatives(self, empty_sch):
        """When symbol not found, error lists available symbols from lib."""
        result = schematic.place_component(
            lib_id="Device:Q_PMOS_GSD",
            reference="Q1",
            value="test",
            x=100,
            y=100,
            schematic_path=str(empty_sch),
            project_path=str(empty_sch.with_suffix(".kicad_pro")),
        )
        # Should mention the symbol wasn't found and list alternatives
        assert "not found" in result.lower() or "Q_PMOS_GSD" in result
        # Should suggest similar symbols if the library was found
        if "Device" in result:
            assert "Q_PMOS" in result or "available" in result.lower()

    def test_fuzzy_match_no_substring_noise(self, tmp_path: Path):
        """Short name 'D' must not suggest unrelated 'Q_PMOS_GSD'.

        Regression: old substring matching matched 'D' against 'Q_PMOS_GSD'
        because 'd' appeared inside 'gsd'.
        """
        from kiutils.symbol import Symbol, SymbolLib

        lib = SymbolLib(version="20231120", generator="kicad_symbol_editor")
        for name in ["Q_PMOS_GSD", "D_Schottky", "LED", "D_TVS"]:
            s = Symbol()
            s.entryName = name
            lib.symbols.append(s)
        lib_path = tmp_path / "TestLib.kicad_sym"
        lib.filePath = str(lib_path)
        lib.to_file()

        sch_path = tmp_path / "test.kicad_sch"
        from conftest import new_schematic

        sch = new_schematic()
        sch.filePath = str(sch_path)
        sch.to_file()

        result = schematic.place_component(
            lib_id="TestLib:D",
            reference="D1",
            value="test",
            x=100,
            y=100,
            schematic_path=str(sch_path),
            symbol_lib_path=str(lib_path),
        )
        assert "not found" in result.lower()
        # Old substring matching would include Q_PMOS_GSD; difflib should not
        assert "Q_PMOS_GSD" not in result

    def test_no_matches_suggests_cross_library_search(self, tmp_path: Path):
        """When no close matches found, suggest list_lib_symbols."""
        from kiutils.symbol import Symbol, SymbolLib

        lib = SymbolLib(version="20231120", generator="kicad_symbol_editor")
        for name in ["Q_PMOS_GSD", "Q_NMOS_GSD"]:
            s = Symbol()
            s.entryName = name
            lib.symbols.append(s)
        lib_path = tmp_path / "TestLib.kicad_sym"
        lib.filePath = str(lib_path)
        lib.to_file()

        sch_path = tmp_path / "test.kicad_sch"
        sch = new_schematic()
        sch.filePath = str(sch_path)
        sch.to_file()

        result = schematic.place_component(
            lib_id="TestLib:TOTALLY_UNRELATED",
            reference="U1",
            value="test",
            x=100,
            y=100,
            schematic_path=str(sch_path),
            symbol_lib_path=str(lib_path),
        )
        assert "not found" in result.lower()
        assert "list_lib_symbols" in result

    def test_sub_sheet_instances_use_root_project(self, tmp_path: Path):
        """Components in sub-sheets must use root project name and full hierarchy path."""
        from kiutils.items.common import Effects, Font, Position, Property
        from kiutils.items.schitems import HierarchicalSheet

        root_sch = new_schematic()
        root_sch.libSymbols.append(build_r_symbol())
        root_path = tmp_path / "myproject.kicad_sch"
        root_sch.filePath = str(root_path)

        sheet = HierarchicalSheet()
        sheet.uuid = "aaaaaaaa-1111-2222-3333-444444444444"
        sheet.position = Position(X=25.4, Y=25.4)
        sheet.width = 25.4
        sheet.height = 10.16
        sheet.sheetName = Property(
            key="Sheetname",
            value="Sub",
            id=0,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=25.4, Y=24.13, angle=0),
        )
        sheet.fileName = Property(
            key="Sheetfile",
            value="sub.kicad_sch",
            id=1,
            effects=Effects(font=Font(height=1.27, width=1.27)),
            position=Position(X=25.4, Y=36.83, angle=0),
        )
        root_sch.sheets.append(sheet)
        root_sch.to_file()

        child_sch = new_schematic()
        child_sch.libSymbols.append(build_r_symbol())
        child_path = tmp_path / "sub.kicad_sch"
        child_sch.filePath = str(child_path)
        child_sch.to_file()

        pro_path = str(tmp_path / "myproject.kicad_pro")
        schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="10K",
            x=100,
            y=100,
            schematic_path=str(child_path),
            project_path=pro_path,
        )

        child_sch = reparse(str(child_path))
        r1 = _find_symbol(child_sch, "R1")
        assert r1 is not None
        inst = r1.instances[0]
        assert inst.name == "myproject"
        assert inst.paths[0].sheetInstancePath == f"/{root_sch.uuid}/{sheet.uuid}"


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

        schematic.add_wires(
            [{"x1": 100, "y1": 100, "x2": 200, "y2": 100}],
            schematic_path=str(scratch_sch),
        )

        sch_after = reparse(str(scratch_sch))
        count_after = _count_wires(sch_after)
        assert count_after == count_before + 1

    @pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
    def test_wire_erc(self, scratch_sch):
        schematic.add_wires(
            [{"x1": 100, "y1": 100, "x2": 200, "y2": 100}],
            schematic_path=str(scratch_sch),
        )
        assert_erc_clean(scratch_sch)

    def test_zero_length_wire(self, scratch_sch):
        # A zero-length wire should not crash
        schematic.add_wires(
            [{"x1": 50, "y1": 50, "x2": 50, "y2": 50}],
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

    def test_add_wires_creates_junctions_on_existing_wire(self, scratch_sch):
        """add_wires should auto-add junction when new wire T-connects to existing."""
        from kiutils.schematic import Schematic

        # scratch_sch has a wire from (50,50) to (80,50)
        # Add a new wire whose ENDPOINT lands on the interior of the existing wire.
        # _auto_junctions checks if new wire endpoints land on existing wire interiors.
        # Wire from (65,30) to (65,50) — endpoint (65,50) is on the interior of (50,50)-(80,50)
        result = schematic.add_wires(
            wires=[{"x1": 65, "y1": 30, "x2": 65, "y2": 50}],
            schematic_path=str(scratch_sch),
        )
        assert "Added 1 wires" in result

        sch = Schematic.from_file(str(scratch_sch))
        # Should have a junction at (65, 50) where the new wire endpoint meets existing wire
        junctions = [(j.position.X, j.position.Y) for j in sch.junctions]
        assert (65, 50) in junctions


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
        schematic.add_junctions(
            [{"x": 50.8, "y": 50.8}],
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
# TestRemoveLabel
# ===========================================================================


class TestRemoveLabel:
    def test_remove_by_text(self, scratch_sch):
        """Remove a label by its text."""
        result = schematic.remove_label(
            text="TEST_NET",
            schematic_path=str(scratch_sch),
        )
        assert "Removed" in result
        sch = reparse(str(scratch_sch))
        assert not any(lbl.text == "TEST_NET" for lbl in sch.labels)

    def test_remove_by_text_and_position(self, scratch_sch):
        """Remove only the label at a specific position."""
        # add_label preserves exact coordinates (no grid snapping),
        # so remove_label must match the exact position passed to add_label.
        schematic.add_label(
            text="TEST_NET",
            x=200,
            y=200,
            schematic_path=str(scratch_sch),
        )
        result = schematic.remove_label(
            text="TEST_NET",
            x=200,
            y=200,
            schematic_path=str(scratch_sch),
        )
        assert "Removed 1" in result
        sch = reparse(str(scratch_sch))
        # Original label at (50, 50) should still exist
        remaining = [lbl for lbl in sch.labels if lbl.text == "TEST_NET"]
        assert len(remaining) == 1

    def test_remove_missing(self, scratch_sch):
        result = schematic.remove_label(
            text="NONEXISTENT",
            schematic_path=str(scratch_sch),
        )
        assert "not found" in result.lower() or "0" in result

    def test_remove_off_grid_label_by_exact_position(self, scratch_sch):
        """Label at non-grid position (50, 50) is removable by its exact coords.

        Regression: old code snapped filter coords to the 1.27mm grid,
        so (50, 50) became (49.53, 49.53) which didn't match the stored
        position, making the label impossible to remove by position.
        """
        result = schematic.remove_label(
            text="TEST_NET",
            x=50,
            y=50,
            schematic_path=str(scratch_sch),
        )
        assert "Removed 1" in result
        sch = reparse(str(scratch_sch))
        assert not any(lbl.text == "TEST_NET" for lbl in sch.labels)

    def test_no_over_deletion_near_same_grid_point(self, scratch_sch):
        """Two labels >0.1mm apart but near the same grid point: only the
        targeted one is removed.

        Regression: old code snapped filter coords to the grid, so both
        labels (within 0.1mm of the snap point) matched and were deleted.
        """
        from kiutils.items.common import Position
        from kiutils.items.schitems import LocalLabel

        sch = reparse(str(scratch_sch))
        # Place two labels at off-grid positions that both snap to 49.53
        # but are 0.16mm apart (> 0.1mm tolerance).
        sch.labels.append(
            LocalLabel(
                text="SNAP_A",
                position=Position(X=49.45, Y=100.33, angle=0),
            )
        )
        sch.labels.append(
            LocalLabel(
                text="SNAP_A",
                position=Position(X=49.61, Y=100.33, angle=0),
            )
        )
        sch.to_file()

        result = schematic.remove_label(
            text="SNAP_A",
            x=49.45,
            y=100.33,
            schematic_path=str(scratch_sch),
        )
        assert "Removed 1" in result
        sch = reparse(str(scratch_sch))
        remaining = [lbl for lbl in sch.labels if lbl.text == "SNAP_A"]
        assert len(remaining) == 1
        assert abs(remaining[0].position.X - 49.61) < 0.01


# ===========================================================================
# TestRemoveWire
# ===========================================================================


class TestRemoveWire:
    def test_remove_by_endpoints(self, scratch_sch):
        """Remove a wire by its start/end coordinates."""
        result = schematic.remove_wire(
            x1=50,
            y1=50,
            x2=80,
            y2=50,
            schematic_path=str(scratch_sch),
        )
        assert "Removed" in result
        sch = reparse(str(scratch_sch))
        wires = [g for g in sch.graphicalItems if isinstance(g, Connection) and g.type == "wire"]
        matching = [
            w
            for w in wires
            if (
                abs(w.points[0].X - 50) < 0.1
                and abs(w.points[0].Y - 50) < 0.1
                and abs(w.points[1].X - 80) < 0.1
                and abs(w.points[1].Y - 50) < 0.1
            )
        ]
        assert len(matching) == 0

    def test_remove_missing(self, scratch_sch):
        result = schematic.remove_wire(
            x1=999,
            y1=999,
            x2=999,
            y2=998,
            schematic_path=str(scratch_sch),
        )
        assert "not found" in result.lower() or "0" in result


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
            project_path=str(empty_sch.with_suffix(".kicad_pro")),
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
            project_path=str(empty_sch.with_suffix(".kicad_pro")),
        )
        sch = reparse(str(empty_sch))
        refs = [
            next((p.value for p in s.properties if p.key == "Reference"), "")
            for s in sch.schematicSymbols
        ]
        assert "#PWR01" in refs
        assert any(r.startswith("#FLG") for r in refs)


class TestSetComponentFootprint:
    def test_sets_footprint(self, scratch_sch):
        result = schematic.set_component_property(
            reference="R1",
            key="Footprint",
            value="Resistor_SMD:R_0402_1005Metric",
            schematic_path=str(scratch_sch),
        )
        assert "R1" in result
        sch = reparse(str(scratch_sch))
        r1 = _find_symbol(sch, "R1")
        fp = next(p.value for p in r1.properties if p.key == "Footprint")
        assert fp == "Resistor_SMD:R_0402_1005Metric"

    def test_missing_component(self, scratch_sch):
        result = schematic.set_component_property(
            reference="R999",
            key="Footprint",
            value="test",
            schematic_path=str(scratch_sch),
        )
        assert "not found" in result.lower()


# ===========================================================================
# TestSetComponentProperty
# ===========================================================================


class TestSetComponentProperty:
    def test_updates_existing_property(self, scratch_sch):
        """Update the Value property (which already exists)."""
        result = schematic.set_component_property(
            reference="R1",
            key="Value",
            value="4.7K",
            schematic_path=str(scratch_sch),
        )
        assert "R1" in result
        sch = reparse(str(scratch_sch))
        r1 = _find_symbol(sch, "R1")
        val = next(p.value for p in r1.properties if p.key == "Value")
        assert val == "4.7K"

    def test_creates_new_property(self, scratch_sch):
        """Create a property that doesn't exist yet."""
        result = schematic.set_component_property(
            reference="R1",
            key="MPN",
            value="RC0402FR-0710KL",
            schematic_path=str(scratch_sch),
        )
        assert "R1" in result
        sch = reparse(str(scratch_sch))
        r1 = _find_symbol(sch, "R1")
        mpn = next((p.value for p in r1.properties if p.key == "MPN"), None)
        assert mpn == "RC0402FR-0710KL"

    def test_missing_component(self, scratch_sch):
        result = schematic.set_component_property(
            reference="R999",
            key="Value",
            value="test",
            schematic_path=str(scratch_sch),
        )
        assert "not found" in result.lower()


# ===========================================================================
# TestRemoveJunction
# ===========================================================================


class TestRemoveJunction:
    def test_remove_existing(self, scratch_sch):
        # First add a junction
        schematic.add_junctions([{"x": 50.8, "y": 50.8}], schematic_path=str(scratch_sch))
        sch = reparse(str(scratch_sch))
        assert any(j.position.X == 50.8 and j.position.Y == 50.8 for j in sch.junctions)

        result = schematic.remove_junction(x=50.8, y=50.8, schematic_path=str(scratch_sch))
        assert "Removed" in result
        sch = reparse(str(scratch_sch))
        assert not any(
            abs(j.position.X - 50.8) < 0.1 and abs(j.position.Y - 50.8) < 0.1 for j in sch.junctions
        )

    def test_remove_missing(self, scratch_sch):
        result = schematic.remove_junction(x=999, y=999, schematic_path=str(scratch_sch))
        assert "not found" in result.lower()


# ===========================================================================
# TestPageBoundary (Bug 1)
# ===========================================================================


class TestPageBoundary:
    def test_valid_coordinates_pass(self, scratch_sch):
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R20",
            value="1K",
            x=150,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result

    def test_out_of_bounds_x(self, scratch_sch):
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R21",
            value="1K",
            x=350,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Error" in result
        assert "outside" in result

    def test_out_of_bounds_y(self, scratch_sch):
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R22",
            value="1K",
            x=100,
            y=250,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Error" in result
        assert "outside" in result

    def test_edge_coordinates_accepted(self, scratch_sch):
        """Coordinates exactly at page edges should be accepted."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R23",
            value="1K",
            x=297,
            y=210,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result

    def test_add_label_out_of_bounds(self, scratch_sch):
        result = schematic.add_label(text="OOB", x=350, y=100, schematic_path=str(scratch_sch))
        assert "Error" in result
        assert "outside" in result

    def test_add_wires_out_of_bounds(self, scratch_sch):
        result = schematic.add_wires(
            wires=[{"x1": 350, "y1": 100, "x2": 360, "y2": 100}],
            schematic_path=str(scratch_sch),
        )
        assert "Error" in result
        assert "outside" in result

    def test_add_junctions_out_of_bounds(self, scratch_sch):
        result = schematic.add_junctions(
            points=[{"x": 350, "y": 100}], schematic_path=str(scratch_sch)
        )
        assert "Error" in result
        assert "outside" in result

    def test_move_component_out_of_bounds(self, scratch_sch):
        result = schematic.move_component(
            reference="R1", x=350, y=100, schematic_path=str(scratch_sch)
        )
        assert "Error" in result
        assert "outside" in result

    def test_add_global_label_out_of_bounds(self, scratch_sch):
        result = schematic.add_global_label(
            text="OOB", x=350, y=100, schematic_path=str(scratch_sch)
        )
        assert "Error" in result
        assert "outside" in result

    def test_add_text_out_of_bounds(self, scratch_sch):
        result = schematic.add_text(text="OOB", x=350, y=100, schematic_path=str(scratch_sch))
        assert "Error" in result
        assert "outside" in result


# ===========================================================================
# TestReferenceValidation (Bug 4)
# ===========================================================================


class TestReferenceValidation:
    def test_valid_ref_passes(self, scratch_sch):
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R10",
            value="1K",
            x=130,
            y=130,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result

    def test_letter_suffix_ref_accepted(self, scratch_sch):
        """KiCad accepts letter suffixes after the number (e.g. C5B, R1A)."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="C5B",
            value="1K",
            x=130,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result

    def test_empty_ref_rejected(self, scratch_sch):
        result = schematic.place_component(
            lib_id="Device:R",
            reference="",
            value="1K",
            x=100,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Error" in result

    def test_digits_only_rejected(self, scratch_sch):
        result = schematic.place_component(
            lib_id="Device:R",
            reference="123",
            value="1K",
            x=100,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Error" in result

    def test_power_ref_accepted(self, scratch_sch):
        """#FLG01 and #PWR05 should be accepted."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="#FLG01",
            value="1K",
            x=130,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result

    def test_lowercase_ref_rejected(self, scratch_sch):
        """Lowercase references like 'r1' should still be rejected."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="r1",
            value="1K",
            x=100,
            y=100,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Error" in result


# ===========================================================================
# TestAddHierarchicalLabel (Task 8)
# ===========================================================================


class TestAddHierarchicalLabel:
    def test_adds_label(self, empty_sch):
        from kiutils.schematic import Schematic

        result = schematic.add_hierarchical_label(
            text="VIN",
            shape="input",
            x=25.4,
            y=30.0,
            schematic_path=str(empty_sch),
        )
        assert "VIN" in result

        sch = Schematic.from_file(str(empty_sch))
        assert len(sch.hierarchicalLabels) == 1
        hl = sch.hierarchicalLabels[0]
        assert hl.text == "VIN"
        assert hl.shape == "input"
        assert hl.position.X == 25.4

    def test_invalid_shape_returns_error(self, empty_sch):
        result = schematic.add_hierarchical_label(
            text="BAD",
            shape="invalid",
            x=10,
            y=10,
            schematic_path=str(empty_sch),
        )
        assert "Error" in result or "invalid" in result.lower()
