"""Edge-case tests for KiCad MCP tools: duplicates, bad paths, odd rotations, extremes."""

from pathlib import Path

import pytest
from conftest import reparse

from mcp_server_kicad import schematic


class TestDuplicateReference:
    def test_duplicate_ref_allowed(self, scratch_sch: Path) -> None:
        """Placing a second component with the same reference (R1) should succeed.

        KiCad flags duplicate references via ERC, not at placement time.
        """
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="4.7K",
            x=200,
            y=200,
            schematic_path=str(scratch_sch),
        )
        assert "Placed" in result

        sch = reparse(scratch_sch)
        r1_syms = [
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R1" for p in s.properties)
        ]
        assert len(r1_syms) == 2


class TestInvalidRotation:
    def test_non_standard_rotation_45(self, scratch_sch: Path) -> None:
        """A 45-degree rotation is non-standard but should not crash."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="1K",
            x=150,
            y=150,
            rotation=45,
            schematic_path=str(scratch_sch),
        )
        assert "Placed" in result

        sch = reparse(scratch_sch)
        r2 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R2" for p in s.properties)
        )
        assert r2.position.angle == 45

    def test_negative_rotation(self, scratch_sch: Path) -> None:
        """A negative rotation should not crash."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R3",
            value="2.2K",
            x=160,
            y=160,
            rotation=-90,
            schematic_path=str(scratch_sch),
        )
        assert "Placed" in result

        sch = reparse(scratch_sch)
        r3 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R3" for p in s.properties)
        )
        assert r3.position.angle == -90


class TestBadPaths:
    def test_nonexistent_schematic(self) -> None:
        """list_components on a nonexistent file should raise an Exception."""
        with pytest.raises(Exception):
            schematic.list_components("/nonexistent/path.kicad_sch")

    def test_nonexistent_sym_lib(self, scratch_sch: Path) -> None:
        """add_lib_symbol with a nonexistent library path should raise an Exception."""
        with pytest.raises(Exception):
            schematic.add_lib_symbol("/nonexistent/lib.kicad_sym", "X", str(scratch_sch))


class TestLargeCoordinates:
    def test_extreme_position(self, scratch_sch: Path) -> None:
        """Placing a component at extreme coordinates should round-trip correctly.

        Coordinates are snapped to the 1.27mm grid.
        99999.8 == 78740*1.27, already on grid.
        """
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R99",
            value="100K",
            x=99999.8,
            y=99999.8,
            schematic_path=str(scratch_sch),
        )
        assert "Placed" in result

        sch = reparse(scratch_sch)
        r99 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R99" for p in s.properties)
        )
        assert r99.position.X == 99999.8
        assert r99.position.Y == 99999.8
