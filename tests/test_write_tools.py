"""Tests for mutating (write) tools in schematic.py."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import (
    assert_kicad_parseable,
    build_power_symbol,
    build_r_symbol,
    new_schematic,
    reparse,
)
from kiutils.items.schitems import Connection
from mcp.server.fastmcp.exceptions import ToolError

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
    def test_placement_parseable(self, scratch_sch):
        """Verify kicad-cli can parse the schematic after placing a component.

        scratch_sch has unconnected pins so it won't pass ERC; we only
        check that the output file is structurally valid.
        """
        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="4.7K",
            x=150,
            y=150,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert_kicad_parseable(scratch_sch)

    def test_placement_with_rotation(self, scratch_sch):
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="1K",
            x=150,
            y=150,
            rotation=90,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "R2" in result

        sch = reparse(str(scratch_sch))
        r2 = _find_symbol(sch, "R2")
        assert r2 is not None
        assert r2.position.angle == 90

    def test_placement_with_custom_lib(self, scratch_sch, scratch_sym_lib):
        """Place using a custom symbol library file."""
        result = schematic.place_component(
            lib_id="Test:R",
            reference="R2",
            value="2.2K",
            x=150,
            y=150,
            symbol_lib_path=str(scratch_sym_lib),
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "R2" in result

    def test_grid_snap(self, scratch_sch):
        """Off-grid placement snaps to nearest 1.27 mm grid point."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="1K",
            x=150,
            y=150,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "R2" in result

        sch = reparse(str(scratch_sch))
        r2 = _find_symbol(sch, "R2")
        # 150 / 1.27 = 118.11 -> 118 * 1.27 = 149.86
        assert r2.position.X == 149.86
        assert r2.position.Y == 149.86

    def test_lib_symbol_reuse(self, scratch_sch):
        """Placing two Device:R components should not duplicate lib_symbol."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="1K",
            x=150,
            y=150,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        sch = reparse(str(scratch_sch))
        r_libs = [ls for ls in sch.libSymbols if ls.entryName == "R"]
        assert len(r_libs) == 1

    def test_instances_block(self, scratch_sch):
        """Placed component should have an instances block for annotation."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="1K",
            x=150,
            y=150,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "R2" in result

        sch = reparse(str(scratch_sch))
        r2 = _find_symbol(sch, "R2")
        assert r2 is not None
        assert len(r2.instances) >= 1
        inst = r2.instances[0]
        assert len(inst.paths) >= 1
        assert inst.paths[0].reference == "R2"
        assert inst.paths[0].unit == 1

    def test_pin_uuids_assigned(self, scratch_sch):
        """Placed component should have pin UUIDs."""
        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="1K",
            x=150,
            y=150,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        sch = reparse(str(scratch_sch))
        r2 = _find_symbol(sch, "R2")
        assert isinstance(r2.pins, dict)
        assert len(r2.pins) == 2
        assert "1" in r2.pins
        assert "2" in r2.pins

    @pytest.mark.skipif(not HAS_KICAD_LIBS, reason="KiCad system libraries not installed")
    def test_missing_symbol_suggests_alternatives(self, empty_sch):
        """When symbol not found, error lists available symbols from lib."""
        with pytest.raises(ToolError, match="not found") as exc_info:
            schematic.place_component(
                lib_id="Device:Q_PMOS_GSD",
                reference="Q1",
                value="test",
                x=100,
                y=100,
                schematic_path=str(empty_sch),
                project_path=str(empty_sch.with_suffix(".kicad_pro")),
            )
        msg = str(exc_info.value)
        # Should suggest similar symbols if the library was found
        assert "Q_PMOS" in msg or "Similar" in msg

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
        sch = new_schematic()
        sch.filePath = str(sch_path)
        sch.to_file()

        with pytest.raises(ToolError, match="not found") as exc_info:
            schematic.place_component(
                lib_id="TestLib:D",
                reference="D1",
                value="test",
                x=100,
                y=100,
                schematic_path=str(sch_path),
                symbol_lib_path=str(lib_path),
            )
        msg = str(exc_info.value)
        # Old substring matching would include Q_PMOS_GSD; difflib should not
        assert "Q_PMOS_GSD" not in msg

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

        with pytest.raises(ToolError, match="not found") as exc_info:
            schematic.place_component(
                lib_id="TestLib:TOTALLY_UNRELATED",
                reference="U1",
                value="test",
                x=100,
                y=100,
                schematic_path=str(sch_path),
                symbol_lib_path=str(lib_path),
            )
        msg = str(exc_info.value)
        assert "list_lib_symbols" in msg

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
        # lib_symbol should be embedded
        lib_names = [ls.entryName for ls in sch.libSymbols]
        assert "R" in lib_names

    @pytest.mark.skipif(not HAS_KICAD_LIBS, reason="KiCad system libraries not installed")
    def test_auto_embed_then_wire(self, empty_sch):
        """place_component + wire_pins_to_net works without add_lib_symbol."""
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
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_component(
                reference="R999",
                schematic_path=str(scratch_sch),
            )


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
    def test_wire_parseable(self, scratch_sch):
        """Verify kicad-cli can parse the schematic after adding a wire.

        scratch_sch has unconnected pins so it won't pass ERC; we only
        check that the output file is structurally valid.
        """
        schematic.add_wires(
            [{"x1": 100, "y1": 100, "x2": 200, "y2": 100}],
            schematic_path=str(scratch_sch),
        )
        assert_kicad_parseable(scratch_sch)

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
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_label(
                text="NONEXISTENT",
                schematic_path=str(scratch_sch),
            )

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
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_wire(
                x1=999,
                y1=999,
                x2=999,
                y2=998,
                schematic_path=str(scratch_sch),
            )


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
        with pytest.raises(ToolError, match="not found"):
            schematic.set_component_property(
                reference="R999",
                key="Footprint",
                value="test",
                schematic_path=str(scratch_sch),
            )


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
        with pytest.raises(ToolError, match="not found"):
            schematic.set_component_property(
                reference="R999",
                key="Value",
                value="test",
                schematic_path=str(scratch_sch),
            )

    @pytest.mark.no_kicad_validation
    def test_set_reference_updates_per_symbol_instances(self, tmp_path):
        """Changing Reference via set_component_property must update sym.instances."""
        from conftest import _gen_uuid, new_schematic
        from kiutils.items.common import Effects, Font, Position, Property
        from kiutils.items.schitems import (
            SchematicSymbol,
            SymbolProjectInstance,
            SymbolProjectPath,
        )

        sch = new_schematic()
        sch.libSymbols.append(build_r_symbol())
        sym = SchematicSymbol()
        sym.libId = "Device:R"
        sym.position = Position(X=100, Y=100)
        sym.uuid = _gen_uuid()
        sym.unit = 1
        sym.inBom = True
        sym.onBoard = True
        sym.properties = [
            Property(
                key="Reference",
                value="R1",
                id=0,
                effects=Effects(font=Font(height=1.27, width=1.27)),
                position=Position(X=100, Y=97),
            ),
            Property(
                key="Value",
                value="10K",
                id=1,
                effects=Effects(font=Font(height=1.27, width=1.27)),
                position=Position(X=100, Y=103),
            ),
            Property(
                key="Footprint",
                value="",
                id=2,
                effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                position=Position(X=100, Y=100),
            ),
        ]
        sym.pins = {"1": _gen_uuid(), "2": _gen_uuid()}
        # Set up initial per-symbol instances block (as KiCad 9 does)
        sym.instances = [
            SymbolProjectInstance(
                name="test_proj",
                paths=[
                    SymbolProjectPath(
                        sheetInstancePath=f"/{sch.uuid}",
                        reference="R1",
                        unit=1,
                    ),
                ],
            ),
        ]
        sch.schematicSymbols.append(sym)
        path = tmp_path / "test_ref.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()

        schematic.set_component_property(
            reference="R1",
            key="Reference",
            value="R42",
            schematic_path=str(path),
        )

        sch2 = reparse(str(path))
        sym2 = sch2.schematicSymbols[0]
        # Property should be updated
        ref_val = next(p.value for p in sym2.properties if p.key == "Reference")
        assert ref_val == "R42"
        # Per-symbol instances must also be updated
        assert len(sym2.instances) > 0, "instances block missing"
        for inst in sym2.instances:
            for path_entry in inst.paths:
                assert path_entry.reference == "R42", (
                    f"Per-symbol instance reference {path_entry.reference!r} "
                    f"doesn't match new reference 'R42'"
                )


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
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_junction(x=999, y=999, schematic_path=str(scratch_sch))


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
        with pytest.raises(ToolError, match="outside"):
            schematic.place_component(
                lib_id="Device:R",
                reference="R21",
                value="1K",
                x=350,
                y=100,
                schematic_path=str(scratch_sch),
                project_path=str(scratch_sch.with_suffix(".kicad_pro")),
            )

    def test_out_of_bounds_y(self, scratch_sch):
        with pytest.raises(ToolError, match="outside"):
            schematic.place_component(
                lib_id="Device:R",
                reference="R22",
                value="1K",
                x=100,
                y=250,
                schematic_path=str(scratch_sch),
                project_path=str(scratch_sch.with_suffix(".kicad_pro")),
            )

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
        with pytest.raises(ToolError, match="outside"):
            schematic.add_label(text="OOB", x=350, y=100, schematic_path=str(scratch_sch))

    def test_add_wires_out_of_bounds(self, scratch_sch):
        with pytest.raises(ToolError, match="outside"):
            schematic.add_wires(
                wires=[{"x1": 350, "y1": 100, "x2": 360, "y2": 100}],
                schematic_path=str(scratch_sch),
            )

    def test_add_junctions_out_of_bounds(self, scratch_sch):
        with pytest.raises(ToolError, match="outside"):
            schematic.add_junctions(points=[{"x": 350, "y": 100}], schematic_path=str(scratch_sch))

    def test_move_component_out_of_bounds(self, scratch_sch):
        with pytest.raises(ToolError, match="outside"):
            schematic.move_component(reference="R1", x=350, y=100, schematic_path=str(scratch_sch))

    def test_add_global_label_out_of_bounds(self, scratch_sch):
        with pytest.raises(ToolError, match="outside"):
            schematic.add_global_label(text="OOB", x=350, y=100, schematic_path=str(scratch_sch))

    def test_add_text_out_of_bounds(self, scratch_sch):
        with pytest.raises(ToolError, match="outside"):
            schematic.add_text(text="OOB", x=350, y=100, schematic_path=str(scratch_sch))


# ===========================================================================
# TestRemoveText
# ===========================================================================


class TestRemoveText:
    def test_remove_by_content(self, scratch_sch):
        schematic.add_text(text="HELLO", x=50, y=50, schematic_path=str(scratch_sch))
        result = schematic.remove_text(text="HELLO", schematic_path=str(scratch_sch))
        assert "Removed 1" in result
        sch = reparse(str(scratch_sch))
        assert not any(t.text == "HELLO" for t in sch.texts)

    def test_remove_by_content_and_position(self, scratch_sch):
        schematic.add_text(text="A", x=50, y=50, schematic_path=str(scratch_sch))
        schematic.add_text(text="A", x=80, y=80, schematic_path=str(scratch_sch))
        result = schematic.remove_text(text="A", x=50, y=50, schematic_path=str(scratch_sch))
        assert "Removed 1" in result
        sch = reparse(str(scratch_sch))
        a_texts = [t for t in sch.texts if t.text == "A"]
        assert len(a_texts) == 1
        assert abs(a_texts[0].position.X - 80) < 0.1

    def test_remove_missing(self, scratch_sch):
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_text(text="NONEXISTENT", schematic_path=str(scratch_sch))


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
        with pytest.raises(ToolError):
            schematic.place_component(
                lib_id="Device:R",
                reference="",
                value="1K",
                x=100,
                y=100,
                schematic_path=str(scratch_sch),
                project_path=str(scratch_sch.with_suffix(".kicad_pro")),
            )

    def test_digits_only_rejected(self, scratch_sch):
        with pytest.raises(ToolError):
            schematic.place_component(
                lib_id="Device:R",
                reference="123",
                value="1K",
                x=100,
                y=100,
                schematic_path=str(scratch_sch),
                project_path=str(scratch_sch.with_suffix(".kicad_pro")),
            )

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
        with pytest.raises(ToolError):
            schematic.place_component(
                lib_id="Device:R",
                reference="r1",
                value="1K",
                x=100,
                y=100,
                schematic_path=str(scratch_sch),
                project_path=str(scratch_sch.with_suffix(".kicad_pro")),
            )


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
        with pytest.raises(ToolError):
            schematic.add_hierarchical_label(
                text="BAD",
                shape="invalid",
                x=10,
                y=10,
                schematic_path=str(empty_sch),
            )


# ===========================================================================
# TestRemoveHierarchicalLabel (Task 9)
# ===========================================================================


class TestRemoveHierarchicalLabel:
    def test_removes_by_name(self, empty_sch):
        from kiutils.schematic import Schematic

        schematic.add_hierarchical_label(
            text="VIN",
            shape="input",
            x=25,
            y=30,
            schematic_path=str(empty_sch),
        )
        result = schematic.remove_hierarchical_label(
            text="VIN",
            schematic_path=str(empty_sch),
        )
        assert "Removed" in result

        sch = Schematic.from_file(str(empty_sch))
        assert len(sch.hierarchicalLabels) == 0

    def test_not_found_returns_error(self, empty_sch):
        with pytest.raises(ToolError, match="not found"):
            schematic.remove_hierarchical_label(
                text="NONEXISTENT",
                schematic_path=str(empty_sch),
            )


# ===========================================================================
# TestModifyHierarchicalLabel (Task 10)
# ===========================================================================


class TestModifyHierarchicalLabel:
    def test_rename_label(self, empty_sch):
        from kiutils.schematic import Schematic

        schematic.add_hierarchical_label(
            text="VIN",
            shape="input",
            x=25,
            y=30,
            schematic_path=str(empty_sch),
        )
        result = schematic.modify_hierarchical_label(
            text="VIN",
            new_text="VIN_PROT",
            schematic_path=str(empty_sch),
        )
        assert "VIN_PROT" in result

        sch = Schematic.from_file(str(empty_sch))
        assert sch.hierarchicalLabels[0].text == "VIN_PROT"

    def test_change_shape(self, empty_sch):
        from kiutils.schematic import Schematic

        schematic.add_hierarchical_label(
            text="SIG",
            shape="input",
            x=25,
            y=30,
            schematic_path=str(empty_sch),
        )
        result = schematic.modify_hierarchical_label(
            text="SIG",
            new_shape="output",
            schematic_path=str(empty_sch),
        )
        assert "output" in result

        sch = Schematic.from_file(str(empty_sch))
        assert sch.hierarchicalLabels[0].shape == "output"


class TestConnectPinsNetLabel:
    def test_creates_net_label_when_no_existing(self, scratch_sch):
        """connect_pins auto-adds net label when neither pin has one."""
        from kiutils.schematic import Schematic

        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="10K",
            x=100,
            y=130,
            schematic_path=str(scratch_sch),
        )
        schematic.connect_pins(
            ref1="R1",
            pin1="2",
            ref2="R2",
            pin2="1",
            schematic_path=str(scratch_sch),
        )
        sch = Schematic.from_file(str(scratch_sch))
        net_labels = [lbl.text for lbl in sch.labels if lbl.text.startswith("Net-(")]
        assert "Net-(R1-2)" in net_labels

    def test_skips_label_when_pin_already_labeled(self, scratch_sch):
        """connect_pins skips auto-label when pin already has a net label."""
        from kiutils.schematic import Schematic

        # Wire R1:1 to a net label first
        schematic.wire_pins_to_net(
            pins=[{"reference": "R1", "pin": "1"}],
            label_text="MY_NET",
            schematic_path=str(scratch_sch),
        )
        # Place R2 and connect to R1:1 (which now has MY_NET at its pin pos)
        schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="10K",
            x=100,
            y=70,
            schematic_path=str(scratch_sch),
        )
        schematic.connect_pins(
            ref1="R1",
            pin1="1",
            ref2="R2",
            pin2="2",
            schematic_path=str(scratch_sch),
        )
        sch = Schematic.from_file(str(scratch_sch))
        auto_labels = [lbl.text for lbl in sch.labels if lbl.text.startswith("Net-(")]
        assert len(auto_labels) == 0, f"Should skip auto-label, got: {auto_labels}"


# ---------------------------------------------------------------------------
# wire_pins_to_net  –  auto_pwr_flag opt-out
# ---------------------------------------------------------------------------


class TestWirePinsToNetAutoPwrFlag:
    @pytest.mark.no_kicad_validation
    def test_auto_pwr_flag_false_skips_pwr_flag(self, tmp_path):
        """wire_pins_to_net with auto_pwr_flag=False should not place PWR_FLAG."""
        import uuid

        from kiutils.items.common import Effects, Font, Position, Property
        from kiutils.items.schitems import SchematicSymbol

        sch = new_schematic()
        # Add VCC lib symbol (power_in type)
        sch.libSymbols.append(build_power_symbol("VCC", "power_in"))

        # Place a VCC symbol instance
        vcc = SchematicSymbol()
        vcc.libId = "power:VCC"
        vcc.libName = "VCC"
        vcc.position = Position(X=100, Y=100, angle=0)
        vcc.uuid = str(uuid.uuid4())
        vcc.unit = 1
        vcc.inBom = False
        vcc.onBoard = True
        vcc.properties = [
            Property(
                key="Reference",
                value="#PWR01",
                id=0,
                effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                position=Position(X=100, Y=96.19, angle=0),
            ),
            Property(
                key="Value",
                value="VCC",
                id=1,
                effects=Effects(font=Font(height=1.27, width=1.27)),
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
        vcc.pins = {"1": str(uuid.uuid4())}
        sch.schematicSymbols.append(vcc)

        sch_path = tmp_path / "pwr_test.kicad_sch"
        sch.filePath = str(sch_path)
        sch.to_file()

        schematic.wire_pins_to_net(
            pins=[{"reference": "#PWR01", "pin": "1"}],
            label_text="VCC_NET",
            auto_pwr_flag=False,
            schematic_path=str(sch_path),
        )

        # Reload and check no PWR_FLAG was placed
        sch2 = reparse(str(sch_path))
        pwr_flags = [
            s
            for s in sch2.schematicSymbols
            if any(p.key == "Value" and p.value == "PWR_FLAG" for p in s.properties)
        ]
        assert len(pwr_flags) == 0, "PWR_FLAG should not be placed when auto_pwr_flag=False"

    @pytest.mark.no_kicad_validation
    def test_auto_pwr_flag_true_places_pwr_flag(self, tmp_path):
        """wire_pins_to_net with auto_pwr_flag=True (default) should place PWR_FLAG for power_in."""
        import uuid

        from kiutils.items.common import Effects, Font, Position, Property
        from kiutils.items.schitems import SchematicSymbol

        sch = new_schematic()
        sch.libSymbols.append(build_power_symbol("VCC", "power_in"))

        vcc = SchematicSymbol()
        vcc.libId = "power:VCC"
        vcc.libName = "VCC"
        vcc.position = Position(X=100, Y=100, angle=0)
        vcc.uuid = str(uuid.uuid4())
        vcc.unit = 1
        vcc.inBom = False
        vcc.onBoard = True
        vcc.properties = [
            Property(
                key="Reference",
                value="#PWR01",
                id=0,
                effects=Effects(font=Font(height=1.27, width=1.27), hide=True),
                position=Position(X=100, Y=96.19, angle=0),
            ),
            Property(
                key="Value",
                value="VCC",
                id=1,
                effects=Effects(font=Font(height=1.27, width=1.27)),
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
        vcc.pins = {"1": str(uuid.uuid4())}
        sch.schematicSymbols.append(vcc)

        sch_path = tmp_path / "pwr_test2.kicad_sch"
        sch.filePath = str(sch_path)
        sch.to_file()

        schematic.wire_pins_to_net(
            pins=[{"reference": "#PWR01", "pin": "1"}],
            label_text="VCC_NET2",
            schematic_path=str(sch_path),
        )

        sch2 = reparse(str(sch_path))
        pwr_flags = [
            s
            for s in sch2.schematicSymbols
            if any(p.key == "Value" and p.value == "PWR_FLAG" for p in s.properties)
        ]
        assert len(pwr_flags) == 1, "PWR_FLAG should be placed when auto_pwr_flag=True (default)"
